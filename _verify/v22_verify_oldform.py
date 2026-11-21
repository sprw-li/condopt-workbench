"""§11.4 旧表单逐字段 —— **verifier 自写**独立验收（不复用 api-backend 的 `v22_oldform_fields.py`）。

判据（CONTRACT §11.4，口径：**叶子控件 33**，不是标量递归的 35）：
  1. 抽屉里渲染的字段控件数 == 后端 `prepare_template` 按叶子口径算出的数量（我从 `/api/bootstrap` 独立复算）；
  2. **逐字段改值** → `answers` 里该路径**逐字段等于所改值**，且**类型保持**（bool/int/float/list/str）；
  3. 未触碰的字段：键集与基线一致（改一个字段不应凭空增删其它键）；
  4. 空值不得被写成字符串 `"None"` / `"null"` / `"undefined"`。

运行（在克隆里）：py -3 _verify/v22_verify_oldform.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_profile as bp  # noqa: E402
import v21_geom_states as G  # noqa: E402
from common import Report, Server, fresh, log  # noqa: E402


def leaf_paths(template: dict) -> list[str]:
    """从 `prepare_template` 独立复算**叶子**路径（不写死 33）。

    口径（我实测反推、与契约 §11.4 的 `5,5,5,3,4,2` 分解一致）：
      * 顶层标量（budget/batch/noise/smoothness/domain/prior/notes）= **7**；
      * `objective` 是 dict → 其键 `name`/`direction` = **2**；
      * `variables` 是**行列表** → 每行**全部键**都是可编辑叶子（连续行含 name/kind/low/high/unit = 5；
        分类行含 name/kind/choices = 3；… 合计 **24**）。
    合计 7 + 2 + 24 = **33**。
    """
    leaves: list[str] = []
    for key, spec in (template or {}).items():
        if key == "objective" and isinstance(spec, dict):
            leaves += [f"objective.{sub}" for sub in spec]
        elif key == "variables" and isinstance(spec, list):
            for index, row in enumerate(spec):
                if isinstance(row, dict):
                    leaves += [f"variables[{index}].{sub}" for sub in row]
        else:
            leaves.append(key)
    return leaves


def form_state(session) -> dict:
    """从 `#input` 的 answers JSON 读回当前表单产出。"""
    raw = session.js("(function(){const el=document.querySelector('#input');"
                     "return el? el.value : null;})()")
    try:
        return json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        return {"__unparsed__": raw}


def get_path(state: dict, path: str, row_index: int):
    """取值：`variables[i].key` / `objective.sub` / 顶层标量。"""
    if path.startswith("variables"):
        # DOM 的 data-path 既可能写成 `variables[0].low`，也可能写成 `variables.0.low` —— 两种都认
        match = re.match(r"variables(?:\[(\d+)\]|\.(\d+))\.(.+)$", path)
        if not match:
            return None
        index = int(match.group(1) if match.group(1) is not None else match.group(2))
        key = match.group(3)
        rows = state.get("variables")
        if isinstance(rows, list) and index < len(rows) and isinstance(rows[index], dict):
            return rows[index].get(key)
        return None
    if path.startswith("objective."):
        return (state.get("objective") or {}).get(path.split(".", 1)[1])
    return state.get(path)


def main() -> int:
    report = Report("§11.4 旧表单逐字段（verifier 自写驱动）")
    runs = fresh("oldform")
    port = bp.free_port()
    profile = bp.make_profile("v22oldform")
    proc = G.start_edge(port, profile)
    try:
        with Server(runs) as server:
            bootstrap = json.loads(__import__("common").get(server.base, "/api/bootstrap")[2])
            template = bootstrap.get("prepare_template") or {}
            expected_leaves = leaf_paths(template)
            session = G.CDP(G.new_target(port))
            session.call("Page.enable")
            session.call("Runtime.enable")
            session.call("Page.navigate", {"url": f"{server.base}/"})
            session.pump(4.0)
            for _ in range(40):
                if session.js("!!document.querySelector('.condopt-chat-log')"):
                    break
                session.pump(0.4)

            # 展开「高级：直接改 answers JSON」抽屉（字段首次打开才渲染）
            session.js("(function(){const d=document.querySelector('#condoptAdvanced');"
                       "if(d){d.open=true;d.dispatchEvent(new Event('toggle'));}return true;})()")
            for _ in range(30):
                if session.js("document.querySelectorAll('#condoptFormFields [data-path]').length > 0"):
                    break
                session.pump(0.4)

            dom_count = int(session.js("document.querySelectorAll('#condoptFormFields [data-path]').length") or 0)
            report.check(
                f"1) 抽屉渲染的叶子控件数 == 我从 prepare_template 独立复算的叶子数"
                f"（契约口径 33；标量递归口径会是 35）",
                dom_count == len(expected_leaves) and dom_count == 33,
                f"DOM={dom_count} 我复算={len(expected_leaves)} 契约=33；"
                f"复算明细前 8 项={expected_leaves[:8]}",
            )

            # 基线键集
            baseline = form_state(session)
            baseline_keys = sorted(baseline.keys())

            # ---- 2) 逐控件改值 → 读回 → 比对值 + 类型 ----
            controls = session.js(
                "(function(){return JSON.stringify("
                "Array.from(document.querySelectorAll('#condoptFormFields [data-path]')).map((el,i)=>({"
                "  i:i, path:el.getAttribute('data-path'), tag:el.tagName.toLowerCase(),"
                "  type:(el.getAttribute('type')||''), kind:(el.getAttribute('data-kind')||''),"
                "  options:(el.tagName==='SELECT'?Array.from(el.options).map(o=>o.value):null)})));})()"
            )
            controls = json.loads(controls)
            mismatches, type_failures = [], []
            for control in controls:
                path, tag, kind = control["path"], control["tag"], control["kind"]
                if tag == "select" and control["options"]:
                    options = control["options"]
                    target = options[1 % len(options)] if len(options) > 1 else options[0]
                    script = ("(function(){const el=document.querySelectorAll('#condoptFormFields [data-path]')"
                              f"[{control['i']}];el.value={json.dumps(target)};"
                              "el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));return el.value;})()")
                    expect, expect_type = target, str
                elif tag == "input" and control["type"] == "checkbox":
                    script = ("(function(){const el=document.querySelectorAll('#condoptFormFields [data-path]')"
                              f"[{control['i']}];el.checked=!el.checked;"
                              "el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));return el.checked;})()")
                    expect, expect_type = None, bool          # 值在下面读回后确定
                elif kind == "list" or control["type"] == "list":
                    script = ("(function(){const el=document.querySelectorAll('#condoptFormFields [data-path]')"
                              f"[{control['i']}];el.value='alpha, beta';"
                              "el.dispatchEvent(new Event('input',{bubbles:true}));"
                              "el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));return el.value;})()")
                    expect, expect_type = ["alpha", "beta"], list
                elif control["type"] == "number" or kind == "number":
                    script = ("(function(){const el=document.querySelectorAll('#condoptFormFields [data-path]')"
                              f"[{control['i']}];el.value='42.5';"
                              "el.dispatchEvent(new Event('input',{bubbles:true}));"
                              "el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));return el.value;})()")
                    expect, expect_type = 42.5, float
                else:
                    unique = f"verify-{control['i']}"
                    script = ("(function(){const el=document.querySelectorAll('#condoptFormFields [data-path]')"
                              f"[{control['i']}];el.value={json.dumps(unique)};"
                              "el.dispatchEvent(new Event('input',{bubbles:true}));"
                              "el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));return el.value;})()")
                    expect, expect_type = unique, str
                returned = session.js(script)
                if tag == "input" and control["type"] == "checkbox":
                    expect = bool(returned)
                session.pump(0.15)
                state = form_state(session)
                actual = get_path(state, path, control["i"])
                if actual != expect:
                    mismatches.append((path, expect, actual))
                if expect is not None and not isinstance(actual, expect_type):
                    type_failures.append((path, expect_type.__name__, type(actual).__name__))

            report.check(
                f"2) 逐字段改值（{len(controls)} 个控件）→ answers 里逐字段等于所改值",
                not mismatches,
                f"不一致 {len(mismatches)} 个：{mismatches[:4] or '无'}",
            )
            report.check(
                "2b) 类型保持（bool/int/float/list/str 不被串化）",
                not type_failures,
                f"类型不符 {len(type_failures)} 个：{type_failures[:4] or '无'}",
            )

            # ---- 3) 未触碰字段：键集一致 ----
            after_keys = sorted(form_state(session).keys())
            report.check(
                "3) 改字段不改变顶层键集（未触碰字段不受影响）",
                after_keys == baseline_keys,
                f"基线={baseline_keys} 改后={after_keys}",
            )

            # ---- 4) 空值不得写成字符串 ----
            session.js("(function(){document.querySelectorAll('#condoptFormFields [data-path]').forEach(el=>{"
                       "if(el.tagName==='SELECT'||el.type==='checkbox')return;"
                       "el.value='';el.dispatchEvent(new Event('input',{bubbles:true}));"
                       "el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));});return true;})()")
            session.pump(0.5)
            text = json.dumps(form_state(session), ensure_ascii=False)
            bad = [token for token in ('"None"', '"null"', '"undefined"') if token in text]
            report.check(
                "4) 清空所有文本/数值控件后，answers 里不出现 \"None\"/\"null\"/\"undefined\" 字符串",
                not bad,
                f"命中={bad or '无'}（键集 {sorted(form_state(session).keys())}）",
            )
            session.close()
    finally:
        bp.kill_tree(proc)
    return report.finish("v22_verify_oldform.log")


if __name__ == "__main__":
    raise SystemExit(bp.run_guarded(main, "v22oldform"))
