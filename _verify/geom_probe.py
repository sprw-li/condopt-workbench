"""量真实 DOM 几何，判定「元素是否真的互相压住」。

## 为什么需要三重判定（2026-11-21 修）

第一版只看 `getBoundingClientRect()` 两两求交，结果**既误报又漏报**（verifier 在 task-16 §21.9 证明）：

* **误报**：`getBoundingClientRect()` 返回的是**未裁切**矩形。元素在 `overflow:auto` 的父容器里
  滚出可视区后矩形照旧，于是「压住别人」——其实用户根本看不到它。
* **误报（第二类）**：收起的 `<details>` 子树在 Chromium 里只是 `content-visibility:hidden`，
  矩形仍在，但**不绘制**。
* **漏报**：只按「包含关系」排除父容器后，某些真实嵌套又会被漏掉。

所以现在的判定是三条**缺一不可**：

1. **裁切感知**：把每个矩形与它所有可滚动祖先的 client rect 求交，得到**有效可见矩形**；
   有效矩形为空 = 滚出可视区 → 不是重叠。
2. **绘制感知**：跳过 `hidden` / `display:none` / **闭合的 `<details>` 子树**（这些不绘制）。
3. **命中测试**：对**有效**重叠，在重叠中心调 `document.elementsFromPoint()`，
   要求两个元素里至少有一个真的出现在堆叠栈里（证明这个位置真的画着它）。
   另外做一次**布局增量**对照：把较小的那个临时 `display:none`，看页面总高变化是否为 0
   —— 为 0 说明它不参与布局（多为幻影），只作参考信息，不单独定案。

用法：
    py -3 _verify/geom_probe.py [--url URL] [--scenario 01] [--no-human-note]
退出码：0 = 零尺寸 0 且真实重叠 0；2 = 有可疑项。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def _find_edge() -> str:
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

#: 要纳入判定的关键块（可按需追加；**判定逻辑不要改**）
SELECTORS = [
    ".panel-body", ".field", "#input", ".field-head", ".attach-row", ".outputs",
    "#condoptForm", "#condoptActions", ".run-row", "#runBtn", ".review",
    "#condoptResult", ".condopt-form-fields",
    "#condoptKickoff", "#condoptKickoffPlan", "#condoptAdvanced", ".condopt-advanced-body",
    ".condopt-kickoff-head", ".condopt-chat-log", ".condopt-chat-input", ".condopt-chips",
    ".condopt-bubble", ".condopt-chat-note", ".condopt-options", ".condopt-opt-card",
    ".condopt-options-head", ".condopt-plan-head", ".condopt-plan-why", ".condopt-plan-warnings",
    ".condopt-plan-build", "#condoptParamBasic", ".condopt-param-group", ".condopt-param",
    ".condopt-advanced-params", ".condopt-advanced-params-body", ".condopt-run-semantics",
]

MEASURE_JS = r"""
(() => {
  const SEL = %s;

  const visibleRect = (el) => {
    let r = el.getBoundingClientRect();
    let box = {x: r.x, y: r.y, w: r.width, h: r.height};
    let p = el.parentElement;
    while (p) {
      const st = getComputedStyle(p);
      if (/(auto|scroll|hidden)/.test(st.overflowY) || /(auto|scroll|hidden)/.test(st.overflowX)) {
        const pr = p.getBoundingClientRect();
        const nx = Math.max(box.x, pr.x), ny = Math.max(box.y, pr.y);
        const rx = Math.min(box.x + box.w, pr.x + pr.width), ry = Math.min(box.y + box.h, pr.y + pr.height);
        box = {x: nx, y: ny, w: Math.max(0, rx - nx), h: Math.max(0, ry - ny)};
      }
      p = p.parentElement;
    }
    return box;
  };

  const paintSkipped = (el) => {
    if (el.hidden) return true;
    let p = el;
    while (p) {
      const st = getComputedStyle(p);
      if (st.display === 'none' || st.visibility === 'hidden') return true;
      if (p.tagName === 'DETAILS' && !p.open) return true;   // 闭合抽屉：Chromium 只是 content-visibility:hidden
      p = p.parentElement;
    }
    return false;
  };

  const out = [];
  let idx = 0;
  for (const s of SEL) {
    document.querySelectorAll(s).forEach((el, i) => {
      if (paintSkipped(el)) return;
      const r = el.getBoundingClientRect();
      const vr = visibleRect(el);
      const st = getComputedStyle(el);
      el.dataset.__geomId = String(idx);
      const chain = [];
      let p = el.parentElement;
      while (p) { if (p.dataset.__geomId !== undefined) chain.push(Number(p.dataset.__geomId)); p = p.parentElement; }
      out.push({
        id: idx, sel: s + (i ? '[' + i + ']' : ''),
        x: r.x, y: r.y, w: r.width, h: r.height,
        vx: vr.x, vy: vr.y, vw: vr.w, vh: vr.h,          // 裁切后的有效可见矩形
        pos: st.position, opacity: st.opacity,
        parent: el.parentElement && el.parentElement.dataset.__geomId !== undefined
                ? Number(el.parentElement.dataset.__geomId) : -1,
        ancestors: chain,
      });
      idx += 1;
    });
  }

  const interArea = (a, b) => {
    const x = Math.max(0, Math.min(a.vx + a.vw, b.vx + b.vw) - Math.max(a.vx, b.vx));
    const y = Math.max(0, Math.min(a.vy + a.vh, b.vy + b.vh) - Math.max(a.vy, b.vy));
    return {area: x * y, cx: Math.max(a.vx, b.vx) + x / 2, cy: Math.max(a.vy, b.vy) + y / 2};
  };

  // 命中测试装置：临时给元素打名字，方便从堆叠栈里认出它
  const tag = (el, name) => { el.dataset.__hitName = name; return el; };
  const byId = {};
  for (const s of SEL) document.querySelectorAll(s).forEach((el) => {
    if (el.dataset.__geomId !== undefined) byId[el.dataset.__geomId] = el;
  });
  for (const id of Object.keys(byId)) tag(byId[id], 'g' + id);

  const pairs = [];
  const ids = Object.keys(byId).map(Number);
  for (let i = 0; i < ids.length; i++) {
    for (let j = i + 1; j < ids.length; j++) {
      const a = out.find(o => o.id === ids[i]), b = out.find(o => o.id === ids[j]);
      if (!a || !b) continue;
      if (a.ancestors.includes(b.id) || b.ancestors.includes(a.id)) continue;   // 正常嵌套
      const {area, cx, cy} = interArea(a, b);
      if (area <= 0) continue;
      const smaller = Math.min(a.vw * a.vh, b.vw * b.vh);
      if (smaller <= 0) continue;
      const ratio = area / smaller;
      if (ratio > 0.92) continue;
      // 命中测试：这个点上到底画着谁
      const stack = document.elementsFromPoint(cx, cy)
        .map(e => e.dataset && e.dataset.__hitName).filter(Boolean);
      const inStack = stack.includes('g' + a.id) || stack.includes('g' + b.id);
      // 布局增量：把小的临时隐藏，页面总高是否变化
      const ea = byId[a.id], eb = byId[b.id];
      const target = (a.vw * a.vh) <= (b.vw * b.vh) ? ea : eb;
      const before = document.documentElement.scrollHeight;
      const prev = target.style.display;
      target.style.display = 'none';
      const after = document.documentElement.scrollHeight;
      target.style.display = prev;
      pairs.push({ratio: Math.round(ratio * 1000) / 1000, a: a.sel, b: b.sel, area: Math.round(area),
                  cx: Math.round(cx), cy: Math.round(cy), in_stack: inStack,
                  layout_delta: after - before});
    }
  }
  return JSON.stringify({boxes: out, pairs: pairs, metrics: {
    innerW: window.innerWidth, innerH: window.innerHeight,
    scrollW: document.documentElement.scrollWidth,
    clientW: document.documentElement.clientWidth,
    mq1024: window.matchMedia('(max-width:1024px)').matches,   // 窄屏断点是否真的生效
    mq680: window.matchMedia('(max-width:680px)').matches,
    shellDisplay: (() => { const el = document.querySelector('.workbench');
                           return el ? getComputedStyle(el).display : null; })(),
  }});
})()
"""


def _free_port() -> int:
    """取一个空闲端口：固定端口 + 连跑多次 = 上一轮 Edge 还没放开端口，下一轮就起不来
    （实测表现为间歇性「拿不到 CDP 目标」、退出码 1）。"""
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_port_closed(port: int, timeout: float = 10.0) -> None:
    import socket

    deadline = time.time() + timeout
    while time.time() < deadline:
        probe = socket.socket()
        try:
            probe.bind(("127.0.0.1", port))   # 能绑上 = 已释放
            probe.close()
            return
        except OSError:
            probe.close()
            time.sleep(0.2)


def cdp_measure(url: str, scenario_prefix: str | None, all_open: bool = False,
                window_size: str = "1344,896", theme: str = "light") -> dict:
    port = _free_port()
    # 每次用**独立** profile：共用一个目录时，上一次 Edge 还没完全退出就会导致
    # 下一次启动失败（实测表现为「拿不到 CDP 目标」、退出码 1；矩阵里会看成随机失败）。
    profile = Path(tempfile.gettempdir()) / f"condopt-geom-{os.getpid()}-{int(time.time() * 1000) % 1000000}"
    proc = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
         f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
         f"--window-size={window_size}", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        ws_url = None
        for _ in range(60):
            time.sleep(0.5)
            try:
                data = json.loads(urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/list", timeout=3).read().decode())
            except Exception:
                continue
            pages = [t for t in data if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
            if pages:
                ws_url = pages[0]["webSocketDebuggerUrl"]
                break
        if not ws_url:
            raise SystemExit("拿不到 CDP 目标")
        import websocket

        ws = websocket.create_connection(ws_url, timeout=40)
        time.sleep(3.0)
        if theme == "dark":
            # 上游主题走 localStorage + html[data-theme]；先写存储再设属性，避免闪白后再切
            theme_js = ("(() => { try { localStorage.setItem('edukit-theme', 'dark'); } catch (e) {}"
                        " document.documentElement.dataset.theme = 'dark';"
                        " return document.documentElement.dataset.theme; })()")
            ws.send(json.dumps({"id": 9, "method": "Runtime.evaluate",
                                "params": {"expression": theme_js, "returnByValue": True}}))
            for _ in range(40):
                if json.loads(ws.recv()).get("id") == 9:
                    break
            time.sleep(0.6)
        if scenario_prefix:
            click_js = (
                "(() => { const it = [...document.querySelectorAll('button,a')]"
                f".find(e => e.textContent.includes({json.dumps(scenario_prefix)}));"
                " if (it) { it.click(); return 'clicked'; } return 'not-found'; })()"
            )
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                "params": {"expression": click_js, "returnByValue": True}}))
            for _ in range(40):
                if json.loads(ws.recv()).get("id") == 1:
                    break
            time.sleep(1.8)
        if all_open:
            # 把折叠容器全部展开再测：收起态会被 paintSkipped 跳过，展开态才验证得到它们
            open_js = ("(() => { document.querySelectorAll('details').forEach(d => d.open = true);"
                       " return document.querySelectorAll('details').length; })()")
            ws.send(json.dumps({"id": 3, "method": "Runtime.evaluate",
                                "params": {"expression": open_js, "returnByValue": True}}))
            for _ in range(40):
                if json.loads(ws.recv()).get("id") == 3:
                    break
            time.sleep(1.2)
        ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate",
                            "params": {"expression": MEASURE_JS % json.dumps(SELECTORS),
                                       "returnByValue": True}}))
        raw = None
        for _ in range(80):
            msg = json.loads(ws.recv())
            if msg.get("id") == 2:
                raw = msg["result"]["result"]["value"]
                break
        ws.close()
        if raw is None:
            raise SystemExit("测量脚本没有返回")
        return json.loads(raw)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)   # 独立 profile 用完即弃，避免占空间
        _wait_port_closed(port)                      # 等端口真的释放，下一轮才不会竞争


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8770/")
    parser.add_argument("--scenario", default="开工建议",
                        help="点这个侧栏项进入场景；传空字符串则不点")
    parser.add_argument("--shots-dir", default=str(Path(__file__).resolve().parent / "shots"))
    parser.add_argument("--all-open", action="store_true",
                        help="先把所有 <details> 展开再测（覆盖「抽屉展开」态）")
    parser.add_argument("--window-size", default="1344,896", help="形如 900,800")
    parser.add_argument("--theme", choices=("light", "dark"), default="light")
    args = parser.parse_args()

    data = cdp_measure(args.url, args.scenario or None, all_open=args.all_open,
                       window_size=args.window_size, theme=args.theme)
    boxes, pairs, metrics = data["boxes"], data["pairs"], data.get("metrics", {})

    state = "抽屉展开态" if args.all_open else "默认态"
    print(f"=== 几何判定（{state}，{args.theme} @{args.window_size}，"
          f"URL={args.url}，场景={args.scenario or '不切换'}）===")
    print(f"visible boxes（裁切+绘制过滤后）: {len(boxes)}")
    for b in sorted(boxes, key=lambda z: (z["y"], z["x"])):
        clip = "" if (b["vw"] >= b["w"] - 0.5 and b["vh"] >= b["h"] - 0.5) else \
               f"  裁切后={b['vw']:.0f}x{b['vh']:.0f}"
        print(f"  {b['sel']:<30} x={b['x']:7.1f} y={b['y']:7.1f} w={b['w']:7.1f} h={b['h']:7.1f}{clip}")

    zero = [b for b in boxes if b["h"] <= 1 or b["w"] <= 1]
    print(f"\n零尺寸（被压扁 → 子元素会溢出压住兄弟）: {len(zero)}")
    for b in zero:
        print(f"  ! {b['sel']:<30} w={b['w']:.1f} h={b['h']:.1f}")

    real = [p for p in pairs if p["in_stack"]]
    phantom = [p for p in pairs if not p["in_stack"]]
    print(f"\n原始相交对: {len(pairs)}   其中【真实】(命中测试可见): {len(real)}   【幻影】: {len(phantom)}")
    for p in real:
        print(f"  REAL   {p['ratio']:.2f}  {p['a']} x {p['b']}  area={p['area']}  "
              f"@({p['cx']},{p['cy']})  布局增量={p['layout_delta']}")
    for p in phantom:
        print(f"  phantom {p['ratio']:.2f}  {p['a']} x {p['b']}  area={p['area']}  "
              f"（命中栈里没有它；布局增量={p['layout_delta']}）")

    print("\n说明：判定三重缺一不可 —— ① 可滚动祖先裁切后的有效矩形 ② 跳过不绘制子树"
          "（含闭合 <details>）③ 重叠中心 elementsFromPoint 命中测试。")

    # 窄屏最常见的真缺陷不是重叠而是**横向溢出**；顺带记录断点是否真的命中
    overflow = (metrics.get("scrollW", 0) or 0) - (metrics.get("clientW", 0) or 0)
    print(f"\n视口/断点：inner={metrics.get('innerW')}x{metrics.get('innerH')}  "
          f"shell.display={metrics.get('shellDisplay')}  "
          f"mq1024={metrics.get('mq1024')}  mq680={metrics.get('mq680')}")
    print(f"横向溢出：scrollWidth={metrics.get('scrollW')} clientWidth={metrics.get('clientW')} "
          f"→ 溢出 {overflow}px {'（OK）' if overflow <= 1 else '（溢出 > 1px，视为缺陷）'}")

    print(f"截图建议：py -3 -m http.server 托管后用 Edge --screenshot 目视复核（shots 目录：{args.shots_dir}）")

    return 0 if (not real and not zero and overflow <= 1) else 2


if __name__ == "__main__":
    sys.exit(main())
