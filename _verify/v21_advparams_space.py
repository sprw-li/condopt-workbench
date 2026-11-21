"""量化：`.condopt-advanced-params` **收起**时是否仍占版面（无 :not([open]) 加固）。

对照实验：同一页面下
  (a) 原样（收起）
  (b) 临时给 body 加 display:none（模拟「已加固」）
比较 `.stream` 的 scrollHeight 与后续兄弟（plan-build 等）的 y 坐标差。

运行：py -3 _verify/v21_advparams_space.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_profile as bp  # §11.5 临时资源卫生（profile 不残留 + 动态端口）
from common import Report, Server, fresh, log  # noqa: E402

import v21_geom_states as G  # noqa: E402

PROBE = r"""
(() => {
  const stream = document.querySelector('.stream');
  const det = document.querySelector('.condopt-advanced-params');
  const body = document.querySelector('.condopt-advanced-params-body');
  const build = document.querySelector('.condopt-plan-build');
  if (!stream || !det || !body || !build) return JSON.stringify({error:'missing nodes'});
  function snap(tag){
    return {tag,
            det_open: det.open,
            det_h: Math.round(det.getBoundingClientRect().height),
            body_h: Math.round(body.getBoundingClientRect().height),
            body_display: getComputedStyle(body).display,
            body_hidden: body.hidden,
            build_y_abs: Math.round(build.getBoundingClientRect().top + stream.scrollTop),
            stream_scrollHeight: stream.scrollHeight,
            stream_clientHeight: stream.clientHeight,
            section_h: Math.round(document.querySelector('.condopt-kickoff-plan').getBoundingClientRect().height)};
  }
  det.open = false;
  const asIs = snap('as-is (closed)');
  const prev = body.style.display;
  body.style.display = 'none';
  const hardened = snap('body display:none');
  body.style.display = prev;
  return JSON.stringify({asIs, hardened});
})()
"""


def main() -> int:
    report = Report("高级参数收起态是否仍占版面（量化）")
    runs = fresh("advspace")
    port = bp.free_port()          # §11.5：动态端口，避免固定端口竞态
    proc = G.start_edge(port, bp.make_profile("advspace"))
    try:
        with Server(runs) as server:
            cdp = G.CDP(G.new_target(port))
            cdp.call("Page.enable")
            cdp.call("Runtime.enable")
            cdp.call("Page.navigate", {"url": f"{server.base}/"})
            cdp.pump(4.0)
            for _ in range(40):
                if cdp.js("!!document.querySelector('.condopt-chat-log')"):
                    break
                cdp.pump(0.5)
            for _ in range(10):
                if cdp.js("document.querySelectorAll('.condopt-opt-card').length > 0"):
                    break
                cdp.js("(function(){var c=document.querySelectorAll('.condopt-chip');if(c.length)c[0].click();})()")
                cdp.pump(1.2)
            cdp.js("(function(){var b=document.querySelector('.condopt-opt-go');if(b)b.click();})()")
            cdp.pump(2.5)

            data = json.loads(cdp.js(PROBE))
            if data.get("error"):
                report.check("探针可用", False, str(data["error"]))
                return report.finish("v21_advparams_space.log")
            a, h = data["asIs"], data["hardened"]
            for d in (a, h):
                log(f"{d['tag']:<20}  det_h={d['det_h']:>5}  body_h={d['body_h']:>5}  body_display={d['body_display']:<6} "
                    f"build_y={d['build_y_abs']:>5}  section_h={d['section_h']:>5}  scrollHeight={d['stream_scrollHeight']}")

            waste = h["stream_scrollHeight"] - a["stream_scrollHeight"]
            push = a["build_y_abs"] - h["build_y_abs"]
            log(f"\n收起态比「已加固」多出：scrollHeight +{waste}px，build 块被下推 {push}px，"
                f"body 仍占 {a['body_h']}px（display={a['body_display']}）")

            report.check(
                "诊断 1（**修复后应成立**）：`.condopt-advanced-params` 收起时 body 不参与布局"
                "（display:none、高度 0）",
                a["body_display"] == "none" and a["body_h"] == 0,
                f"body_h={a['body_h']} display={a['body_display']}（修复前是 h=678 display=flex 的幻影矩形）",
            )
            report.check(
                "诊断 2：收起态与「强行 display:none」对照组的布局完全一致（无版面泄漏、无下推）",
                push == 0 and waste == 0 and a["body_h"] == 0,
                f"下推 {push}px、scrollHeight 差 {waste}px（{a['stream_scrollHeight']} vs "
                f"{h['stream_scrollHeight']}）；收起态 body_h={a['body_h']}",
            )
            report.check(
                "诊断 3：两个抽屉的收起态加固现在**对称**（都有 :not([open]) > body{display:none}）",
                a["body_display"] == "none",
                f".condopt-advanced-params:not([open]) > .condopt-advanced-params-body 已存在"
                f"（app.css 实测生效：body display={a['body_display']}）",
            )
            cdp.close()
    finally:
        bp.kill_tree(proc)   # §11.5：父还活着时树杀（先 terminate 会让子进程脱管）
    return report.finish("v21_advparams_space.log")


if __name__ == "__main__":
    raise SystemExit(bp.run_guarded(main, "advspace"))
