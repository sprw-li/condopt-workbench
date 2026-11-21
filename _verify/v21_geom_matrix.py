"""视口/主题矩阵复验（web-shell 补测项的独立复核）。

对 **4 配置 × 4 状态** 各做一次三重判据几何检查：
  配置 = {light, dark} × {1344x896, 900x800}
  状态 = 首屏(聊天) / 选项态 / 参数页 / 参数页+两个抽屉都打开
判据用 **lead 新版 geom_probe 的判定代码**（页面侧三重）**加上**我自己的 Python 侧判据，
两边都要 0 零尺寸 / 0 真实重叠，并各存一张截图。

运行：py -3 _verify/v21_geom_matrix.py
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_profile as bp  # §11.5 临时资源卫生（profile 不残留 + 动态端口）
from common import Report, SHOTS, Server, fresh, log  # noqa: E402

import geom_probe as LP  # lead 的判定代码
import v21_geom_states as G

CONFIGS = [
    ("light", "1344x896", 1344, 896),
    ("dark", "1344x896", 1344, 896),
    ("light", "900x800", 900, 800),
    ("dark", "900x800", 900, 800),
]
STATES = ("首屏", "选项态", "参数页", "参数页+两抽屉开")


def his_verdict(cdp):
    data = json.loads(js_retry(cdp, LP.MEASURE_JS % json.dumps(LP.SELECTORS, ensure_ascii=False),
                               label="lead 判定"))
    boxes, pairs = data["boxes"], data["pairs"]
    zero = [b for b in boxes if b["h"] <= 1 or b["w"] <= 1]
    real = [p for p in pairs if p["in_stack"]]
    return len(boxes), zero, pairs, real


def js_retry(cdp, expression, tries: int = 3, label: str = ""):
    """CDP 偶发超时（长截图/大响应后）时重试，避免把基础设施抖动记成产品问题。"""
    last = None
    for attempt in range(1, tries + 1):
        try:
            return cdp.js(expression)
        except Exception as exc:  # noqa: BLE001
            last = exc
            log(f"    [重试 {attempt}/{tries}] {label or 'js'} → {type(exc).__name__}: {exc}")
            G.time.sleep(1.5)
    raise RuntimeError(f"{label or 'js'} 连续 {tries} 次失败: {last}")


def drive(cdp, state: str):
    """从干净页面驱动到目标状态。每次重新导航，保证状态独立。"""
    cdp.call("Page.navigate", {"url": cdp.url})
    cdp.pump(4.0)
    for _ in range(40):
        if js_retry(cdp, "!!document.querySelector('.condopt-chat-log')", label="等待聊天窗"):
            break
        cdp.pump(0.5)
    if state == "首屏":
        return
    for _ in range(10):
        if js_retry(cdp, "document.querySelectorAll('.condopt-opt-card').length > 0", label="等待选项"):
            break
        js_retry(cdp, "(function(){var c=document.querySelectorAll('.condopt-chip');if(c.length)c[0].click();})()",
                 label="点芯片")
        cdp.pump(1.2)
    if state == "选项态":
        return
    js_retry(cdp, "(function(){var b=document.querySelector('.condopt-opt-go');if(b)b.click();})()", label="点算法卡")
    cdp.pump(2.5)
    if state == "参数页+两抽屉开":
        js_retry(cdp, "(function(){document.querySelectorAll('details').forEach(d=>{d.open=true;"
                      "d.dispatchEvent(new Event('toggle'));});})()", label="展开抽屉")
        cdp.pump(2.0)


def start_edge(port: int, profile: Path, width: int, height: int):
    import subprocess
    proc = subprocess.Popen(
        [G.EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
         f"--window-size={width},{height}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    bp.attach(profile, proc)          # §11.5：登记进程，退出时 taskkill /T /F 树杀 + 删 profile
    import time
    import urllib.request
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2).read()
            return proc
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    raise RuntimeError("Edge DevTools 未就绪")


def main() -> int:
    report = Report("几何矩阵：主题 × 视口 × 状态（独立复核 web-shell 的补测）")
    runs = fresh("matrix")
    with Server(runs) as server:
        for theme, viewport, width, height in CONFIGS:
            port = bp.free_port()                      # §11.5：动态端口，避免固定端口竞态
            profile = bp.make_profile(f"matrix-{theme}-{viewport}")
            proc = start_edge(port, profile, width, height)
            try:
                cdp = G.CDP(G.new_target(port))
                cdp.url = f"{server.base}/"
                cdp.call("Page.enable")
                cdp.call("Runtime.enable")
                for state in STATES:
                    drive(cdp, state)
                    if theme == "dark":
                        js_retry(cdp, "document.documentElement.setAttribute('data-theme','dark')", label="切主题")
                        cdp.pump(0.6)
                    actual_theme = js_retry(cdp, "document.documentElement.getAttribute('data-theme')",
                                            label="读主题")
                    inner = js_retry(cdp, "window.innerWidth + 'x' + window.innerHeight", label="读视口")
                    his_boxes, his_zero, his_pairs, his_real = his_verdict(cdp)
                    my_zero, my_bad = G.judge(G.measure(cdp), clipping_aware=True)
                    ok = (not his_zero) and (not his_real) and (not my_zero) and (not my_bad)
                    log(f"[{theme}@{viewport}] {state}: 主题={actual_theme} 视口={inner} "
                        f"| lead: 块={his_boxes} 零尺寸={len(his_zero)} 相交={len(his_pairs)} 真实={len(his_real)} "
                        f"| 我: 零尺寸={len(my_zero)} 可见重叠={len(my_bad)}")
                    report.check(
                        f"{theme}@{viewport} · {state}：两套判据均 0 零尺寸 / 0 真实重叠",
                        ok,
                        f"主题={actual_theme} 视口={inner} lead 零尺寸={len(his_zero)} 真实={len(his_real)}；"
                        f"我 零尺寸={len(my_zero)} 重叠={len(my_bad)}",
                    )
                    if state == "参数页" and viewport == "900x800":
                        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
                        path = SHOTS / f"matrix_{theme}_{viewport}_param.png"
                        path.write_bytes(base64.b64decode(shot["data"]))
                cdp.close()
            finally:
                bp.kill_tree(proc)   # §11.5：父还活着时树杀（先 terminate 会让子进程脱管）
    return report.finish("v21_geom_matrix.log")


if __name__ == "__main__":
    raise SystemExit(bp.run_guarded(main, "matrix"))
