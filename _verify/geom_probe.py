"""用 CDP 量真实 DOM 几何，判断 01 场景是否存在「元素相互重叠」。

不靠眼看截图，直接取每个可见块的 getBoundingClientRect，两两求交面积，
排除合法的包含关系（父容器包住子元素）。输出重叠对与面积。
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


def _find_edge() -> str:
    """Edge 路径按环境变量/常见位置探测，仓库里不写死本机盘符路径（发布去本机化）。"""
    override = os.environ.get("CONDOPT_EDGE")
    if override:
        return override
    found = shutil.which("msedge")
    if found:
        return found
    for key in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(key)
        if not base:
            continue
        candidate = Path(base) / "Microsoft/Edge/Application/msedge.exe"
        if candidate.is_file():
            return str(candidate)
    return "msedge"


EDGE = _find_edge()

JS = r"""
(() => {
  const sel = [
    '.panel-body', '.field', '#input', '.field-head', '.attach-row', '.outputs',
    '#condoptForm', '#condoptActions', '.run-row', '#runBtn', '.review',
    '#condoptResult', '.condopt-form-fields',
    // §10 对话式开工新增的容器（web-shell 追加，判定逻辑未改）
    '#condoptKickoff', '#condoptKickoffPlan', '#condoptAdvanced', '.condopt-advanced-body'
  ];
  const out = [];
  let idx = 0;
  for (const s of sel) {
    document.querySelectorAll(s).forEach((el, i) => {
      if (el.hidden || el.closest('[hidden]')) return;   // 隐藏元素不参与几何判定
      const r = el.getBoundingClientRect();
      const st = getComputedStyle(el);
      el.dataset.__geomId = String(idx);
      out.push({id: idx, sel: s + (i ? '[' + i + ']' : ''), x: r.x, y: r.y, w: r.width, h: r.height,
                pos: st.position, vis: st.visibility, opacity: st.opacity, overflow: st.overflowY,
                parent: el.parentElement && el.parentElement.dataset.__geomId ? Number(el.parentElement.dataset.__geomId) : -1});
      // 记录祖先链，用于把「父容器包含子元素」排除掉
      const chain = [];
      let p = el.parentElement;
      while (p) {
        if (p.dataset.__geomId) chain.push(Number(p.dataset.__geomId));
        p = p.parentElement;
      }
      out[out.length - 1].ancestors = chain;
      idx += 1;
    });
  }
  return JSON.stringify(out);
})()
"""


def main() -> int:
    port = 9333
    proc = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
         f"--remote-debugging-port={port}", "--user-data-dir=" + str(Path.home() / ".condopt-geom"),
         "--window-size=1344,896", "http://127.0.0.1:8770/?demo=kickoff"],
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
            print("FAIL: 拿不到 CDP 目标")
            return 1
        import websocket

        ws = websocket.create_connection(ws_url, timeout=30)
        time.sleep(3.0)
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": JS, "returnByValue": True}}))
        raw = None
        for _ in range(50):
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                raw = msg["result"]["result"]["value"]
                break
        ws.close()
        boxes = json.loads(raw)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    def area(b):
        return b["w"] * b["h"]

    def inter(a, b):
        x = max(0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
        y = max(0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
        return x * y

    print(f"visible boxes: {len(boxes)}")
    for b in sorted(boxes, key=lambda z: (z["y"], z["x"])):
        print(f"  {b['sel']:<24} x={b['x']:7.1f} y={b['y']:7.1f} w={b['w']:7.1f} h={b['h']:7.1f} pos={b['pos']} op={b['opacity']}")

    bad = []
    zero_height = [b for b in boxes if b["h"] <= 1 or b["w"] <= 1]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            # 祖先/后代互相包住是正常嵌套，不算重叠缺陷
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
            bad.append((round(ratio, 3), a["sel"], b["sel"], ov))

    print(f"\nzero-size boxes (被压扁的容器): {len(zero_height)}")
    for b in zero_height:
        print(f"  ! {b['sel']:<24} w={b['w']:.1f} h={b['h']:.1f} (子元素会溢出并压住后继兄弟)")

    print(f"\nsuspicious overlaps (ratio<=0.92): {len(bad)}")
    for ratio, an, bn, ov in sorted(bad, reverse=True):
        print(f"  {ratio:5.2f}  {an}  x  {bn}   area={ov:.0f}")
    return 0 if (not bad and not zero_height) else 2


if __name__ == "__main__":
    sys.exit(main())
