"""§11.4 独立验收驱动：旧 45 字段表单（「高级：直接改 answers JSON」抽屉）逐字段。

**验收方驱动**：控件数、逐字段改值后的 `answers`、类型保持，全部从真实 DOM 与真实页面
产出的 JSON（`#input` 文本域）读取；期望值来自后端 `/api/bootstrap` 的 `prepare_template`
与 condopt 自己的模板（不手抄字段表）。

判据（CONTRACT §11.4）：
  1. 抽屉里渲染出的控件数 == `prepare_template` 的叶片字段数；
  2. 逐字段改值 → `answers` 逐字段等于所改值，且**类型保持**（bool/int/float/list/str）；
  3. 未触碰字段不影响：产出与「只在 JSON 里改同一字段」的**键集一致**；
  4. 空值字段不得写成像 `"None"` 的字符串。

临时资源按 §11.5 交给 ``browser_profile``。

用法：``py -3 _verify/v22_oldform_fields.py``（退出码 0 = 全部 PASS）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

VERIFY = Path(__file__).resolve().parent
if str(VERIFY) not in sys.path:
    sys.path.insert(0, str(VERIFY))

import browser_profile  # noqa: E402  §11.5 卫生
from common import REPO, Report, Server, fresh, get, js, log  # noqa: E402

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import websocket  # noqa: E402

FIELDS_BOX = "#condoptFormFields"
ANSWERS_INPUT = "#input"
DRAWER = "#condoptAdvanced"


def find_edge() -> str:
    override = os.environ.get("CONDOPT_EDGE")
    if override:
        return override
    found = shutil.which("msedge")
    if found:
        return found
    for candidate in (
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft/Edge/Application/msedge.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return "msedge"


EDGE = find_edge()


class CDP:
    def __init__(self, ws_url: str) -> None:
        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
        self.ws.settimeout(0.2)
        self.next_id = 0
        self.events: list[dict] = []

    def call(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        self.next_id += 1
        mid = self.next_id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            data = json.loads(raw)
            if data.get("id") == mid:
                if "error" in data:
                    raise RuntimeError(f"{method}: {data['error']}")
                return data.get("result", {})
            self.events.append(data)
        raise TimeoutError(f"CDP {method} 超时")

    def pump(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:  # noqa: BLE001
                return
            self.events.append(json.loads(raw))

    def evaluate(self, expression: str, timeout: float = 60.0):
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            return {"__exception__": result["exceptionDetails"].get("text")}
        return result.get("result", {}).get("value")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def start_edge(port: int, profile: Path) -> subprocess.Popen:
    cmd = [
        EDGE, "--headless=new",
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--hide-scrollbars",
        "--window-size=1700,1200",
        "about:blank",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as resp:
                json.loads(resp.read())
                return proc
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    raise RuntimeError("Edge DevTools 端点未就绪（设 CONDOPT_EDGE 指到 msedge.exe）")


def new_target(port: int) -> str:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["webSocketDebuggerUrl"]


# --------------------------------------------------------------------------- #
# 期望值（来自后端模板，不手抄）
# --------------------------------------------------------------------------- #


def template_leaf_paths(template: dict) -> list[str]:
    """模板的**叶片字段**路径：与渲染器「每行只渲染该行实际拥有的键」的口径一致。"""
    paths: list[str] = []
    for key, value in template.items():
        if key == "objective" and isinstance(value, dict):
            paths.extend(f"objective.{inner}" for inner in value)
        elif key == "variables" and isinstance(value, list):
            for index, row in enumerate(value):
                paths.extend(f"variables.{index}.{inner}" for inner in (row or {}))
        else:
            paths.append(key)
    return paths


def get_path(model: dict, path: str):
    node = model
    for part in path.split("."):
        if isinstance(node, list):
            node = node[int(part)]
        elif isinstance(node, dict):
            node = node.get(part)
        else:
            return None
    return node


def walk_strings(node) -> list[str]:
    if isinstance(node, dict):
        out: list[str] = []
        for value in node.values():
            out.extend(walk_strings(value))
        return out
    if isinstance(node, list):
        out = []
        for value in node:
            out.extend(walk_strings(value))
        return out
    return [node] if isinstance(node, str) else []


SET_JS = """
(function(){{
  var all = document.querySelectorAll('{box} [data-path]');
  var c = all[{index}];
  if (!c) return {{ok:false, reason:'no-control'}};
  var kind = c.dataset.kind || '';
  {body}
  c.dispatchEvent(new Event('input', {{bubbles:true}}));
  c.dispatchEvent(new Event('change', {{bubbles:true}}));
  return {{ok:true, path:c.dataset.path, tag:c.tagName, type:c.type||'', kind:kind, value:c.value}};
}})()
"""


def set_control(cdp: CDP, index: int, body: str) -> dict:
    return cdp.evaluate(SET_JS.format(box=FIELDS_BOX, index=index, body=body))


def read_answers(cdp: CDP, timeout: float = 15.0) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        text = cdp.evaluate(f"document.querySelector('{ANSWERS_INPUT}') ? document.querySelector('{ANSWERS_INPUT}').value : null")
        if isinstance(text, str) and text.strip():
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        cdp.pump(0.3)
    return None


def main() -> int:
    report = Report("§11.4 旧表单逐字段独立验收（真实 DOM + 真实 answers JSON）")
    profile = browser_profile.make_profile("oldform")
    port = browser_profile.free_port()
    proc = start_edge(port, profile)
    browser_profile.attach(profile, proc)
    log(f"Edge headless pid={proc.pid} devtools=127.0.0.1:{port} profile={profile}")
    cdp: CDP | None = None
    try:
        cdp = CDP(new_target(port))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        with Server(fresh("v22_oldform"), port=None) as server:
            status, _headers, body = get(server.base, "/api/bootstrap")
            boot = js(body)
            template = boot.get("prepare_template") or {}
            expected_paths = template_leaf_paths(template)
            report.check("bootstrap 提供 prepare_template（期望字段数从后端取）",
                         status == 200 and bool(expected_paths),
                         f"status={status} 叶片字段={len(expected_paths)}"
                         "（口径 CONTRACT §11.4：标量算 1；objective 展开成叶子；variables 每行只数该行自己的键；"
                         "列表值本身算 1 个逗号分隔控件）")

            cdp.call("Page.navigate", {"url": server.base + "/"})
            deadline = time.time() + 60
            while time.time() < deadline:
                cdp.pump(0.4)
                if any(e.get("method") == "Page.loadEventFired" for e in cdp.events):
                    break
            # 打开「高级：直接改 answers JSON」抽屉（表单在抽屉里首次打开才渲染）
            opened = cdp.evaluate(
                "(function(){var d=document.querySelector('" + DRAWER + "');"
                "if(!d) return 'no-drawer';"
                "d.open=true; d.dispatchEvent(new Event('toggle')); return 'opened';})()"
            )
            cdp.pump(1.5)
            counts = cdp.evaluate(
                "({controls:document.querySelectorAll('" + FIELDS_BOX + " [data-path]').length,"
                "paths:Array.from(document.querySelectorAll('" + FIELDS_BOX + " [data-path]')).map(function(c){return c.dataset.path;}),"
                "formVisible:!!document.querySelector('#condoptForm') && !document.querySelector('#condoptForm').hidden})"
            )
            log(f"抽屉={opened}；控件数={counts.get('controls') if isinstance(counts, dict) else counts}")
            controls = counts.get("controls") if isinstance(counts, dict) else None
            report.check("抽屉里控件数 == prepare_template 叶片字段数",
                         controls == len(expected_paths),
                         f"控件 {controls} vs 期望 {len(expected_paths)}"
                         + (f"；DOM 路径={counts.get('paths')}" if controls != len(expected_paths) else ""))

            # ---- 一一对应：每行只许有自己的键（缺陷根因判据，光数总数可能被两个错误抵消）
            dom_paths = (counts.get("paths") if isinstance(counts, dict) else None) or []
            expected_by_row = {
                f"variables.{index}": set((row or {}))
                for index, row in enumerate(template.get("variables") or [])
            }
            got_by_row: dict[str, set] = {}
            for path in dom_paths:
                parts = path.split(".")
                if len(parts) == 3 and parts[0] == "variables":
                    got_by_row.setdefault(f"variables.{parts[1]}", set()).add(parts[2])
            row_problems = []
            for row_key, expected_keys in expected_by_row.items():
                actual_keys = got_by_row.get(row_key, set())
                if actual_keys != expected_keys:
                    row_problems.append(
                        f"{row_key}: 期望 {sorted(expected_keys)} 实得 {sorted(actual_keys)}"
                        f"（多={sorted(actual_keys - expected_keys)} 少={sorted(expected_keys - actual_keys)}）"
                    )
            extra_rows = sorted(set(got_by_row) - set(expected_by_row))
            if extra_rows:
                row_problems.append(f"多出模板没有的行: {extra_rows}")
            report.check("每行只渲染**该行自己的键**（不给 categorical/bool 行补 low/high/unit 幻影控件）",
                         not row_problems, "; ".join(row_problems[:4]) if row_problems else
                         f"{len(expected_by_row)} 行逐行键集一致（各 {[len(v) for v in expected_by_row.values()]}）")

            objective_paths = sorted(p for p in dom_paths if p.startswith("objective."))
            report.check("objective 的两片叶子都在（name/direction）",
                         objective_paths == ["objective.direction", "objective.name"], f"{objective_paths}")
            scalar_paths = sorted(p for p in dom_paths if "." not in p)
            expected_scalars = sorted(k for k in template if k not in ("objective", "variables"))
            report.check("顶层标量字段逐键一致（不含幻影键）",
                         scalar_paths == expected_scalars,
                         f"DOM={scalar_paths}；模板={expected_scalars}")

            answers = read_answers(cdp)
            report.check("能读出表单产出的 answers JSON（#input）", isinstance(answers, dict),
                         f"keys={sorted(answers)[:8] if isinstance(answers, dict) else answers}")
            if not isinstance(answers, dict):
                return report.finish("v22_oldform_fields.log")
            baseline_keys = set(answers)
            untouched_before = dict(answers)

            # ---- 逐字段改值：类型保持 --------------------------------------
            results = cdp.evaluate(
                "Array.from(document.querySelectorAll('" + FIELDS_BOX + " [data-path]')).map(function(c){"
                "return {path:c.dataset.path, tag:c.tagName, type:(c.type||''), kind:(c.dataset.kind||''),"
                "options:(c.tagName==='SELECT'?c.options.length:0)};})"
            )
            if not isinstance(results, list):
                report.check("能枚举控件元信息", False, f"{results}")
                return report.finish("v22_oldform_fields.log")

            type_ok = True
            value_ok = True
            detail: list[str] = []
            type_detail: list[str] = []
            for index, info in enumerate(results):
                path = info["path"]
                tag = info["tag"]
                ctype = info["type"]
                kind = info["kind"]
                # 期望值按控件类型定，**类型语义**（bool/int/float/list/str）在下面逐条断言
                if ctype == "checkbox":
                    kind_expected = "bool"
                    outcome = set_control(cdp, index, "c.checked = !c.checked;")
                elif ctype == "number":
                    kind_expected = "number"
                    outcome = set_control(cdp, index, "c.value = '42.5';")
                elif kind == "list":
                    kind_expected = "list"
                    outcome = set_control(cdp, index, "c.value = 'alpha, beta';")
                elif tag == "SELECT":
                    kind_expected = "str"
                    outcome = set_control(
                        cdp, index,
                        "if(c.options.length>1){c.selectedIndex=(c.selectedIndex+1)%c.options.length;}",
                    )
                elif tag == "TEXTAREA":
                    kind_expected = "str"
                    outcome = set_control(cdp, index, f"c.value = 'notes-{index}';")
                else:
                    kind_expected = "str"
                    outcome = set_control(cdp, index, f"c.value = 'txt-{index}';")

                if isinstance(outcome, dict) and outcome.get("__exception__"):
                    value_ok = False
                    detail.append(f"{path}: JS 异常 {outcome['__exception__']}")
                    continue
                cdp.pump(0.15)
                model = read_answers(cdp)
                if not isinstance(model, dict):
                    value_ok = False
                    detail.append(f"{path}: 读不到 answers")
                    continue
                got = get_path(model, path)

                # 「所改值」= 控件**实际停留在**的值（checkbox 读 checked，其余读 value）
                dom_state = cdp.evaluate(
                    "document.querySelectorAll('" + FIELDS_BOX + " [data-path]')[" + str(index) + "]"
                )
                if ctype == "checkbox":
                    expected = bool(outcome.get("value") == "on") if isinstance(outcome, dict) else True
                    dom_checked = cdp.evaluate(
                        "document.querySelectorAll('" + FIELDS_BOX + " [data-path]')[" + str(index) + "].checked"
                    )
                    expected = bool(dom_checked)
                elif ctype == "number":
                    expected = 42.5
                elif kind == "list":
                    expected = ["alpha", "beta"]
                else:
                    expected = outcome.get("value") if isinstance(outcome, dict) else None

                if got != expected:
                    value_ok = False
                    detail.append(f"{path}: 期望 {expected!r} 实得 {got!r}")

                # 类型保持：bool→bool、number→数值（且不是 bool）、list→list、其余→str
                if kind_expected == "bool" and not isinstance(got, bool):
                    type_ok = False
                    type_detail.append(f"{path}: 应为 bool，实得 {type(got).__name__} {got!r}")
                elif kind_expected == "number" and (isinstance(got, bool) or not isinstance(got, (int, float))):
                    type_ok = False
                    type_detail.append(f"{path}: 应为数值，实得 {type(got).__name__} {got!r}")
                elif kind_expected == "list" and not isinstance(got, list):
                    type_ok = False
                    type_detail.append(f"{path}: 应为 list，实得 {type(got).__name__} {got!r}")
                elif kind_expected == "str" and not isinstance(got, str):
                    type_ok = False
                    type_detail.append(f"{path}: 应为 str，实得 {type(got).__name__} {got!r}")

            report.check(f"逐字段改值 → answers 逐字段等于所改值（{len(results)} 个控件）", value_ok,
                         "; ".join(detail[:5]) if detail else "全部一致")
            report.check("类型保持（checkbox→bool、number→数值、list→list、text→str）", type_ok,
                         "; ".join(type_detail[:5]) if type_detail else "全部保持")

            after_all = read_answers(cdp)
            report.check("未触碰的字段不影响：键集与基线一致（只改了值，没增删键）",
                         isinstance(after_all, dict) and set(after_all) == baseline_keys,
                         f"基线 {sorted(baseline_keys)}；现 {sorted(after_all) if isinstance(after_all, dict) else after_all}")

            # ---- 「只在 JSON 里改同一字段」键集一致 -------------------------
            target_path = results[0]["path"]
            cdp.evaluate(
                "(function(){var el=document.querySelector('" + ANSWERS_INPUT + "');"
                "var obj=JSON.parse(el.value);"
                "return el.value;})()"
            )
            # 通过表单改一个字段
            form_model = read_answers(cdp)
            set_control(cdp, 0, "if(c.type==='checkbox'){c.checked=!c.checked;}else{c.value='json-cmp';}")
            cdp.pump(0.4)
            via_form = read_answers(cdp)
            # 再只用 JSON 文本域改同一个字段
            json_edit = cdp.evaluate(
                "(function(){var el=document.querySelector('" + ANSWERS_INPUT + "');"
                "var obj=JSON.parse(el.value);var parts=" + json.dumps(target_path.split(".")) + ";"
                "var node=obj;for(var i=0;i<parts.length-1;i++){node=node[parts[i]];}"
                "var last=parts[parts.length-1];"
                "node[last]=(typeof node[last]==='boolean')?!node[last]:'json-only';"
                "el.value=JSON.stringify(obj,null,2);"
                "el.dispatchEvent(new Event('input',{bubbles:true}));return el.value;})()"
            )
            cdp.pump(0.6)
            via_json = read_answers(cdp)
            report.check("「表单改」与「只在 JSON 里改同一字段」键集一致",
                         isinstance(via_form, dict) and isinstance(via_json, dict)
                         and set(via_form) == set(via_json),
                         f"form={sorted(via_form) if isinstance(via_form, dict) else via_form}；"
                         f"json={sorted(via_json) if isinstance(via_json, dict) else via_json}")

            # ---- 空值不得写成 "None" ---------------------------------------
            cdp.evaluate(
                "(function(){var box=document.querySelector('" + FIELDS_BOX + "');"
                "var targets=box.querySelectorAll('[data-path]');"
                "for(var i=0;i<targets.length;i++){var c=targets[i];"
                "if(c.type==='checkbox'||c.tagName==='SELECT') continue;"
                "c.value='';c.dispatchEvent(new Event('input',{bubbles:true}));}"
                "return true;})()"
            )
            cdp.pump(0.8)
            emptied = read_answers(cdp)
            strings = walk_strings(emptied) if isinstance(emptied, dict) else []
            report.check("清空所有文本/数值控件后，answers 里不出现字符串 \"None\"/\"null\"",
                         isinstance(emptied, dict)
                         and "None" not in strings and "null" not in strings
                         and "undefined" not in strings,
                         f"可疑字符串={[s for s in strings if s in ('None', 'null', 'undefined')][:5]}"
                         f"；样本={strings[:4]}")
            report.check("清空后仍未触碰的键集不变（空值被剪掉而不是写成噪声）",
                         isinstance(emptied, dict) and set(emptied) <= baseline_keys,
                         f"现键={sorted(emptied) if isinstance(emptied, dict) else emptied}")
    finally:
        if cdp is not None:
            cdp.close()
        browser_profile.kill_tree(proc)
        if not browser_profile.cleanup(profile):
            report.check("一次性 profile 清理干净（连续两次确认不存在）", False, f"残留 {profile}")
        else:
            report.check("一次性 profile 清理干净（连续两次确认不存在）", True, str(profile))
    return report.finish("v22_oldform_fields.log")


if __name__ == "__main__":
    raise SystemExit(browser_profile.guard_residue(main))
