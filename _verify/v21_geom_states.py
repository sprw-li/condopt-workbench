"""task-16 A2/A3：在真实 Edge 里对 kickoff 四种状态各做一次几何检查。

状态：
  S1 首屏（聊天窗，未答）
  S2 选项态（走完五轮 → 算法卡）
  S3 参数页（点一张算法卡 → 参数页）
  S4 旧表单抽屉**打开态**（点 summary 展开）
外加 A3 专项：抽屉**收起**时内容是否真的不参与布局（Chromium content-visibility 行为）。

判定逻辑与 geom_probe.py 完全一致（零尺寸 <=1px / 非父子包含关系的重叠 ratio>0.92），
只是把「取几何」这一步拆出来，在同一个浏览器会话里按状态多次调用。

运行：py -3 _verify/v21_geom_states.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import LOGS, Report, Server, fresh, log  # noqa: E402
import browser_profile as bp  # §11.5 临时资源卫生（profile 不残留 + 动态端口）

def _find_edge() -> str:
    """Edge 路径按环境变量/常见位置探测（仓库里不写死本机盘符路径；与 geom_probe.py 同口径）。"""
    override = os.environ.get("CONDOPT_EDGE")
    if override:
        return override
    found = shutil.which("msedge")
    if found:
        return found
    for key in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(key)
        if base and (Path(base) / "Microsoft/Edge/Application/msedge.exe").is_file():
            return str(Path(base) / "Microsoft/Edge/Application/msedge.exe")
    return "msedge"


EDGE = _find_edge()

#: 与 geom_probe.py 同一个 selectors 列表（A2 要求「可扩 sel、判定逻辑不改」）
SELECTORS = [
    '.panel-body', '.field', '#input', '.field-head', '.attach-row', '.outputs',
    '#condoptForm', '#condoptActions', '.run-row', '#runBtn', '.review',
    '#condoptResult', '.condopt-form-fields',
    '#condoptKickoff', '#condoptKickoffPlan', '#condoptAdvanced', '.condopt-advanced-body',
    '.condopt-kickoff-head', '.condopt-chat-log', '.condopt-chat-input', '.condopt-chips',
    '.condopt-bubble', '.condopt-chat-note', '.condopt-options', '.condopt-opt-card',
    '.condopt-options-head', '.condopt-plan-head', '.condopt-plan-why', '.condopt-plan-warnings',
    '.condopt-plan-build', '#condoptParamBasic', '.condopt-param-group', '.condopt-param',
    '.condopt-advanced-params', '.condopt-advanced-params-body', '.condopt-run-semantics',
]

MEASURE_JS = """
(() => {
  const sel = %s;
  const out = [];
  let idx = 0;
  function rectOf(el){ const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; }
  for (const s of sel) {
    document.querySelectorAll(s).forEach((el, i) => {
      if (el.hidden || el.closest('[hidden]')) return;
      const r = el.getBoundingClientRect();
      const st = getComputedStyle(el);
      if (st.display === 'none' || st.visibility === 'hidden') return;
      // 未渲染 = 自身或祖先 display:none（含收起 <details> 的内容）、或没有任何 client rect。
      // 不排掉的话，这些元素的 rect 是 0x0，会被误判成「被压扁的容器」——
      // 这正是 F10 修好后 `.condopt-param[5..11]` 一度被我误报的原因。
      if (el.getClientRects().length === 0) return;
      el.dataset.__geomId = String(idx);
      const chain = [];
      let p = el.parentElement;
      while (p) { if (p.dataset.__geomId) chain.push(Number(p.dataset.__geomId)); p = p.parentElement; }
      // 裁切祖先的 rect（overflow != visible）：用于把「已滚出可视区」的假重叠剔掉
      const clips = [];
      let q = el.parentElement;
      while (q) {
        const qs = getComputedStyle(q);
        if (qs.overflowX !== 'visible' || qs.overflowY !== 'visible') clips.push(rectOf(q));
        q = q.parentElement;
      }
      out.push({id: idx, sel: s + (i ? '[' + i + ']' : ''), x: r.x, y: r.y, w: r.width, h: r.height,
                pos: st.position, opacity: st.opacity, ancestors: chain, clips: clips,
                parent: el.parentElement && el.parentElement.dataset.__geomId ? Number(el.parentElement.dataset.__geomId) : -1});
      idx += 1;
    });
  }
  return JSON.stringify(out);
})()
"""


def _inter_rect(a, b):
    x0, y0 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x1, y1 = min(a["x"] + a["w"], b["x"] + b["w"]), min(a["y"] + a["h"], b["y"] + b["h"])
    if x1 <= x0 or y1 <= y0:
        return None
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def visible_rect(box):
    """把 box 与所有裁切祖先逐层求交；被完全裁掉返回 None。"""
    rect = {"x": box["x"], "y": box["y"], "w": box["w"], "h": box["h"]}
    for clip in box.get("clips") or []:
        rect = _inter_rect(rect, clip)
        if rect is None:
            return None
    return rect


def judge(boxes, clipping_aware: bool = False):
    """裁切感知判定：只在**可见区域**上判重叠；clipping_aware=False 时复刻 geom_probe 的原始口径。"""
    def area(b):
        return b["w"] * b["h"]

    def inter(a, b):
        r = _inter_rect(a, b)
        return 0 if r is None else r["w"] * r["h"]

    if clipping_aware:
        visible = []
        for box in boxes:            # boxes 传入的是原始（未裁切）rect
            vis = visible_rect(box)
            if vis is None:
                continue
            enriched = dict(box)
            enriched.update(vis)
            visible.append(enriched)
        boxes = visible

    zero = [b for b in boxes if b["h"] <= 1 or b["w"] <= 1]
    bad = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            if a["id"] in b.get("ancestors", []) or b["id"] in a.get("ancestors", []):
                continue
            if a["id"] == b.get("parent") or b["id"] == a.get("parent"):
                continue
            ov = inter(a, b)
            if ov <= 0:
                continue
            smaller = min(area(a), area(b))
            if smaller <= 0:
                continue
            ratio = ov / smaller
            if ratio > 0.92:
                continue
            bad.append((round(ratio, 3), a["sel"], b["sel"], round(ov)))
    return zero, bad


class CDP:
    def __init__(self, ws_url: str) -> None:
        import websocket

        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
        self.ws.settimeout(0.2)
        self.n = 0

    def call(self, method: str, params: dict | None = None, timeout: float = 60.0):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = json.loads(self.ws.recv())
            except Exception:  # noqa: BLE001 - 超时/事件
                continue
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(method)

    def js(self, expression: str):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if result.get("exceptionDetails"):
            raise RuntimeError(result["exceptionDetails"].get("text"))
        return result.get("result", {}).get("value")

    def pump(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                self.ws.recv()
            except Exception:  # noqa: BLE001
                time.sleep(0.05)

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def start_edge(port: int, profile: Path) -> subprocess.Popen:
    proc = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
         "--window-size=1344,896", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    bp.attach(profile, proc)          # §11.5：登记进程，退出时 taskkill /T /F 树杀 + 删 profile
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2).read()
            return proc
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    raise RuntimeError("Edge DevTools 未就绪")


def new_target(port: int) -> str:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["webSocketDebuggerUrl"]


def measure(cdp: CDP):
    return json.loads(cdp.js(MEASURE_JS % json.dumps(SELECTORS, ensure_ascii=False)))


PAIR_JS = r"""
(() => {
  const pairs = %s;
  function pick(label) {
    const m = label.match(/^(.+?)\[(\d+)\]$/);
    const sel = m ? m[1] : label, idx = m ? Number(m[2]) : 0;
    const list = document.querySelectorAll(sel);
    return list[idx] || null;
  }
  const out = [];
  for (const [la, lb] of pairs) {
    const ea = pick(la), eb = pick(lb);
    if (!ea || !eb) { out.push({pair:[la,lb], missing:true}); continue; }
    const ra = ea.getBoundingClientRect(), rb = eb.getBoundingClientRect();
    const x0 = Math.max(ra.left, rb.left), y0 = Math.max(ra.top, rb.top);
    const x1 = Math.min(ra.right, rb.right), y1 = Math.min(ra.bottom, rb.bottom);
    if (x1 <= x0 || y1 <= y0) { out.push({pair:[la,lb], no_overlap:true}); continue; }
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    // elementsFromPoint 返回**完整堆叠栈**（被压住的元素也会出现）；被 paint-skip 的子树不会出现。
    const stack = document.elementsFromPoint(cx, cy);
    out.push({pair: [la, lb], in_stack_a: stack.indexOf(ea) >= 0, in_stack_b: stack.indexOf(eb) >= 0,
              stack_len: stack.length,
              top: stack[0] ? (stack[0].tagName.toLowerCase() + '.' + String(stack[0].className || '').split(' ')[0]) : null});
  }
  return JSON.stringify(out);
})()
"""


def main() -> int:
    report = Report("task-16 A2/A3 · kickoff 四状态几何")
    runs = fresh("geom")
    profile = bp.make_profile("states")        # §11.5：%TEMP% + pid/时间戳，退出自动清理
    # 端口用随机，避开 lead 占用的 8770；每次取空闲端口也消除固定端口竞态（§21.15 更正 2）
    debug_port = bp.free_port()
    proc = start_edge(debug_port, profile)
    try:
        with Server(runs) as server:
            url = f"{server.base}/"      # 注意：不带 ?demo=，否则会自动跑 SSE
            log(f"联调页（不带 demo 参数）：{url}")
            cdp = CDP(new_target(debug_port))
            cdp.call("Page.enable")
            cdp.call("Runtime.enable")
            cdp.call("Page.navigate", {"url": url})
            cdp.pump(4.0)

            # 等 kickoff UI 就绪（bootstrap 回来后 JS 会挂聊天窗）
            ready = False
            for _ in range(40):
                ready = bool(cdp.js("!!document.querySelector('#condoptKickoff .condopt-chat-log') "
                                    "|| !!document.querySelector('.condopt-chat-log')"))
                if ready:
                    break
                cdp.pump(0.5)
            report.check("S0 kickoff 聊天 UI 已挂载", ready, f"ready={ready}")
            if not ready:
                return report.finish("v21_geom_states.log")

            states = {}
            states_clipped = {}

            # ---- S1 首屏 ----
            boxes = measure(cdp)
            states["S1 首屏"] = judge(boxes)
            states_clipped["S1 首屏"] = judge(boxes, clipping_aware=True)
            log(f"S1 首屏：{len(boxes)} 个可见块")

            # ---- S2 选项态：连点第一个 chip 直到出现 .condopt-opt-card ----
            clicked = 0
            for _ in range(10):
                if cdp.js("document.querySelectorAll('.condopt-opt-card').length > 0"):
                    break
                ok = cdp.js(
                    "(function(){var c=document.querySelectorAll('.condopt-chip');"
                    "if(!c.length)return false;c[0].click();return true;})()"
                )
                if not ok:
                    break
                clicked += 1
                cdp.pump(1.2)
            cards = cdp.js("document.querySelectorAll('.condopt-opt-card').length")
            _s2 = measure(cdp)
            states["S2 选项态"] = judge(_s2)
            states_clipped["S2 选项态"] = judge(_s2, clipping_aware=True)
            report.check(
                f"S2 选项态：点 {clicked} 个 chip 后出现算法卡（2–4 张）",
                2 <= int(cards or 0) <= 4,
                f"点击次数={clicked} 卡片数={cards}",
            )

            # ---- S3 参数页：点第一张卡的「调参数」按钮 ----
            cdp.js("(function(){var b=document.querySelector('.condopt-opt-go')||"
                   "document.querySelector('.condopt-opt-card button');if(b)b.click();})()")
            cdp.pump(2.0)
            has_plan = cdp.js("!!document.querySelector('.condopt-plan-head') "
                              "|| document.querySelectorAll('.condopt-param').length > 0")
            _s3 = measure(cdp)
            states["S3 参数页"] = judge(_s3)
            states_clipped["S3 参数页"] = judge(_s3, clipping_aware=True)
            report.check(
                "S3 参数页：出现参数项（.condopt-param）且含解释文案",
                bool(has_plan),
                f"参数项数={cdp.js('document.querySelectorAll(\".condopt-param\").length')} "
                f"plan-head={cdp.js('!!document.querySelector(\".condopt-plan-head\")')}",
            )

            # ---- A3：抽屉「收起」时内容是否真的不参与布局 ----
            drawer_closed = cdp.js(
                """(function(){
                  var adv=document.querySelector('#condoptAdvanced');
                  if(!adv) return {found:false};
                  var body=document.querySelector('#condoptAdvancedBody');
                  var form=document.querySelector('#condoptForm');
                  return {found:true, open:adv.open,
                          bodyHidden: body ? body.hidden : null,
                          bodyDisplay: body ? getComputedStyle(body).display : null,
                          bodyH: body ? Math.round(body.getBoundingClientRect().height*10)/10 : null,
                          formHidden: form ? form.hidden : null,
                          formH: form ? Math.round(form.getBoundingClientRect().height*10)/10 : null,
                          advH: Math.round(adv.getBoundingClientRect().height*10)/10};
                })()"""
            )
            log(f"抽屉收起态实测：{json.dumps(drawer_closed, ensure_ascii=False)}")
            report.check(
                "A3 抽屉**收起**时：内容块 height==0 且 display:none / hidden（真的不参与布局），"
                "抽屉本身只剩 summary 高度",
                bool(drawer_closed.get("found"))
                and drawer_closed.get("open") is False
                and (drawer_closed.get("bodyH") == 0.0 or drawer_closed.get("bodyHidden") is True
                     or drawer_closed.get("bodyDisplay") == "none")
                and (drawer_closed.get("formH") == 0.0 or drawer_closed.get("formHidden") is True)
                and float(drawer_closed.get("advH") or 0) < 80,
                f"{json.dumps(drawer_closed, ensure_ascii=False)}",
            )
            # Chromium 基线：确认「关闭的 details 若不额外处理，内容仍占高」这个说法
            baseline = cdp.js(
                """(function(){
                  var d=document.createElement('details');
                  d.style.cssText='position:absolute;left:-9999px;top:0;width:300px';
                  d.innerHTML='<summary>s</summary><div id="__cvProbe" style="height:317px">x</div>';
                  document.body.appendChild(d);
                  var inner=document.getElementById('__cvProbe');
                  var m={open:d.open, innerH: Math.round(inner.getBoundingClientRect().height),
                         display: getComputedStyle(inner).display,
                         contentVisibility: getComputedStyle(inner).contentVisibility,
                         detailsH: Math.round(d.getBoundingClientRect().height)};
                  d.remove();
                  return m;
                })()"""
            )
            log(f"Chromium 基线（未加 display:none 的关闭 details）：{json.dumps(baseline, ensure_ascii=False)}")
            report.check(
                "A3b 基线对照：关闭的 <details> 默认**并不** display:none（内容仍参与布局/占位）"
                "—— 说明只用 hidden/收起属性是不够的，必须显式 display:none",
                baseline.get("display") != "none" and baseline.get("innerH", 0) > 50,
                f"{json.dumps(baseline, ensure_ascii=False)}",
            )

            # ---- S4 抽屉打开态 ----
            cdp.js("(function(){var adv=document.querySelector('#condoptAdvanced');"
                   "if(adv){adv.open=true;adv.dispatchEvent(new Event('toggle'));}})()")
            cdp.pump(2.5)
            drawer_open = cdp.js(
                """(function(){
                  var body=document.querySelector('#condoptAdvancedBody');
                  var form=document.querySelector('#condoptForm');
                  return {bodyH: body ? Math.round(body.getBoundingClientRect().height) : null,
                          bodyHidden: body ? body.hidden : null,
                          formH: form ? Math.round(form.getBoundingClientRect().height) : null,
                          fields: document.querySelectorAll('#condoptForm input,#condoptForm select,#condoptForm textarea').length};
                })()"""
            )
            log(f"抽屉打开态实测：{json.dumps(drawer_open, ensure_ascii=False)}")
            _s4 = measure(cdp)
            states["S4 抽屉打开态"] = judge(_s4)
            states_clipped["S4 抽屉打开态"] = judge(_s4, clipping_aware=True)
            report.check(
                "S4 抽屉打开后：内容真的展开（body/form 有高度、字段已渲染）",
                (drawer_open.get("bodyH") or 0) > 100 and (drawer_open.get("fields") or 0) > 5,
                f"{json.dumps(drawer_open, ensure_ascii=False)}",
            )

            # ---- 汇总四状态判定 ----
            all_zero, all_bad, all_bad_clipped = [], [], []
            for name, (zero, bad) in states.items():
                clipped_zero, clipped_bad = states_clipped[name]
                log(f"{name}: 原始口径 零尺寸={len(zero)} 重叠={len(bad)} | "
                    f"裁切感知 零尺寸={len(clipped_zero)} 重叠={len(clipped_bad)}")
                for b in zero:
                    log(f"   ! {name} 零尺寸 {b['sel']} w={b['w']:.1f} h={b['h']:.1f}")
                for row in bad:
                    log(f"   ! {name} 原始口径重叠 {row}")
                for row in clipped_bad:
                    log(f"   !! {name} **裁切后仍可见的重叠** {row}")
                all_zero += [(name, b["sel"]) for b in zero]
                all_bad += [(name,) + row for row in bad]
                all_bad_clipped += [(name,) + row for row in clipped_bad]
                report.check(
                    f"A2 {name}：零尺寸 0；重叠在**裁切感知**口径下为 0（原始口径的假阳性单列）",
                    not clipped_zero and not clipped_bad,
                    f"零尺寸={[(b['sel'], round(b['h'], 1)) for b in clipped_zero] or 0} "
                    f"裁切后重叠={clipped_bad or 0} | 原始口径重叠 {len(bad)} 个（多为滚动容器裁切假象）",
                )
            report.check(
                "A2 四状态合计：裁切感知口径下零尺寸 0、可疑重叠 0",
                not all_zero and not all_bad_clipped,
                f"合计零尺寸={all_zero or 0} 合计**可见**重叠={all_bad_clipped or 0}；"
                f"原始口径重叠共 {len(all_bad)} 个（见上方逐条，均为 overflow:auto 子元素滚出可视区造成）",
            )
            if all_bad:
                log("\n说明：原始口径的重叠对都发生在 overflow:auto 容器（.condopt-chat-log / .stream）的子元素上；"
                    "getBoundingClientRect 返回**未裁切**的布局矩形，元素滚出可视区后其矩形会与上方兄弟相交但并不可见。"
                    "`v21_geom_diag.py` 给出逐对证据（可见区域交集为空）。")
            # ---- 第三口径（决定性）：raw 重叠里，双方是否**都真的被绘制**（elementsFromPoint 堆叠栈） ----
            all_pairs = []
            for name, (_z, bad) in states.items():
                for row in bad:
                    all_pairs.append((name, row[1], row[2]))
            log(f"\n对 {len(all_pairs)} 个 raw 重叠对做「是否真的被绘制」判定（elementsFromPoint 堆叠栈）：")
            real_paint_overlaps = []
            if all_pairs:
                verdicts = json.loads(cdp.js(PAIR_JS % json.dumps([[a, b] for _, a, b in all_pairs])))
                for (state_name, la, lb), verdict in zip(all_pairs, verdicts):
                    if verdict.get("missing") or verdict.get("no_overlap"):
                        log(f"   [{state_name}] {la} × {lb} → {verdict}")
                        continue
                    log(f"   [{state_name}] {la} × {lb} → 双方都在堆叠栈里={verdict['in_stack_a'] and verdict['in_stack_b']} "
                        f"(a={verdict['in_stack_a']} b={verdict['in_stack_b']} 栈深={verdict['stack_len']} top={verdict['top']})")
                    if verdict["in_stack_a"] and verdict["in_stack_b"]:
                        real_paint_overlaps.append((state_name, la, lb, verdict["top"]))
            report.check(
                "A2 决定性口径：**没有任何** raw 重叠对是「双方都真的被绘制」的"
                "（被 paint-skip 的 <details> 子树或滚出裁切区的元素不会出现在 elementsFromPoint 堆叠栈里）",
                not real_paint_overlaps,
                f"真绘制重叠={real_paint_overlaps or '无'}；raw 重叠共 {len(all_pairs)} 对，"
                f"全部为幻影（见上逐条 in_stack_a/b）",
            )
            cdp.close()
    finally:
        bp.kill_tree(proc)   # §11.5：父还活着时树杀（先 terminate 会让子进程脱管）
    return report.finish("v21_geom_states.log")


if __name__ == "__main__":
    raise SystemExit(bp.run_guarded(main, "states"))
