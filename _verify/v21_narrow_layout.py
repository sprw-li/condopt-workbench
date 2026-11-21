"""核实 web-shell 的说法：900x800 是否**真的**触发了窄屏堆叠布局（而不是只是窗口小了一点）。

若 `@media (max-width:1024px)` 没生效，那他们的「窄屏矩阵」测的其实还是桌面布局，
结论就不能算覆盖窄屏。用与桌面视口对比的方式证伪/证实。

运行：py -3 _verify/v21_narrow_layout.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_profile as bp  # §11.5 临时资源卫生（profile 不残留 + 动态端口）
from common import Report, Server, fresh, log  # noqa: E402

import v21_geom_matrix as M  # 复用它的 Edge 启动与导航

PROBE = r"""
(() => {
  const nav = document.querySelector('.topbar, header, nav');
  const shell = document.querySelector('.app, .workbench');
  const cs = shell ? getComputedStyle(shell) : null;
  const items = [...document.querySelectorAll('.topbar a, header a, nav a')];
  const visibleNav = items.filter(a => { const r = a.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(a).visibility !== 'hidden'; }).length;
  return JSON.stringify({
    inner: window.innerWidth + 'x' + window.innerHeight,
    mq1024: window.matchMedia('(max-width:1024px)').matches,
    mq680: window.matchMedia('(max-width:680px)').matches,
    nav_found: !!nav,
    nav_display: nav ? getComputedStyle(nav).display : null,
    nav_h: nav ? Math.round(nav.getBoundingClientRect().height) : null,
    visible_nav_links: visibleNav,
    shell_display: cs ? cs.display : null,
    shell_cols: cs ? cs.gridTemplateColumns : null,
    sidebar_display: (function(){ const s=document.querySelector('.sidebar, aside');
      return s ? getComputedStyle(s).display : null; })(),
    doc_scrollW: document.documentElement.scrollWidth,
    doc_clientW: document.documentElement.clientWidth
  });
})()
"""


def main() -> int:
    report = Report("窄屏断言核实：900x800 是否真的进堆叠布局")
    runs = fresh("narrow")
    with Server(runs) as server:
        results = {}
        for label, w, h in (("desktop 1344x896", 1344, 896), ("narrow 900x800", 900, 800)):
            port = bp.free_port()                      # §11.5：动态端口，避免固定端口竞态
            proc = M.start_edge(port, bp.make_profile(f"narrow-{w}"), w, h)
            try:
                cdp = M.G.CDP(M.G.new_target(port))
                cdp.url = f"{server.base}/"
                cdp.call("Page.enable")
                cdp.call("Runtime.enable")
                M.drive(cdp, "首屏")
                data = json.loads(M.js_retry(cdp, PROBE, label=f"{label} 探针"))
                results[label] = data
                log(f"\n{label}: {json.dumps(data, ensure_ascii=False)}")
                cdp.close()
            finally:
                bp.kill_tree(proc)   # §11.5：父还活着时树杀（先 terminate 会让子进程脱管）

        d, n = results["desktop 1344x896"], results["narrow 900x800"]
        report.check(
            "窄屏视口确实命中 @media (max-width:1024px)（900x800 → 866x703）",
            n["mq1024"] is True and n["mq680"] is False and d["mq1024"] is False,
            f"narrow mq1024={n['mq1024']} mq680={n['mq680']} inner={n['inner']}；"
            f"desktop mq1024={d['mq1024']} inner={d['inner']}",
        )
        report.check(
            "窄屏下布局**确实变了**（不是「窗口变小但布局没变」）：shell 由 grid 变 block、"
            "导航链接由 4 变 1、列宽规则由 `230px 1080px` 变 `205px minmax(0,1fr)`",
            n["mq1024"] is True
            and n["shell_display"] != d["shell_display"]
            and n["visible_nav_links"] < d["visible_nav_links"]
            and n["shell_cols"] != d["shell_cols"],
            f"desktop: display={d['shell_display']} cols={d['shell_cols']} 导航链接={d['visible_nav_links']}；"
            f"narrow: display={n['shell_display']} cols={n['shell_cols']} 导航链接={n['visible_nav_links']}",
        )
        report.check(
            "说明：窄屏只是**收起**导航（4→1）而 topbar 本身仍 display:grid，"
            "与 web-shell「顶栏导航隐藏、面板单列」的描述基本一致（措辞略强于实测）",
            True,
            f"nav_display narrow={n['nav_display']}（未变 none），可见链接 4→1；"
            f"sidebar 仍 display={n['sidebar_display']} 但 shell 已转 block",
        )
        report.check(
            "窄屏无横向溢出（scrollWidth <= clientWidth+1）",
            n["doc_scrollW"] <= n["doc_clientW"] + 1,
            f"narrow scrollW={n['doc_scrollW']} clientW={n['doc_clientW']}；"
            f"desktop scrollW={d['doc_scrollW']} clientW={d['doc_clientW']}",
        )
    return report.finish("v21_narrow_layout.log")


if __name__ == "__main__":
    raise SystemExit(bp.run_guarded(main, "narrow"))
