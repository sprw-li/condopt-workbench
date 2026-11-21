"""定向诊断：`.condopt-plan-build` 与 `.condopt-param[5]/[6]` 的重叠是真遮挡还是布局坐标假象。

判据（决定性）：
* 若 `.condopt-plan-build` 是 `static` 且两者在正常流里前后排列 → 真实重叠只可能来自
  「一方被滚动容器裁掉后其未裁切矩形与另一方相交」，属假阳性；
* 若它是 `sticky`/`fixed` 且在与参数行**同时可见**的位置相交 → 参数被按钮条遮住 = 真缺陷。

做法：在参数页对滚动容器取 3 个 scrollTop（顶/中/底），每个位置记录
两者 rect、position、以及**裁切后可见区域的交集**。

运行：py -3 _verify/v21_param_overlap.py
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
  const out = [];
  const stream = document.querySelector('.stream');
  if (!stream) return JSON.stringify({error: 'no .stream'});
  const st = getComputedStyle(stream);
  const buildEls = Array.from(document.querySelectorAll('.condopt-plan-build'));
  const paramEls = Array.from(document.querySelectorAll('.condopt-param'));
  function rect(el){ const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; }
  function clips(el){ const a=[]; let p=el.parentElement;
    while(p){ const s=getComputedStyle(p); if(s.overflowX!=='visible'||s.overflowY!=='visible') a.push(rect(p)); p=p.parentElement; } return a; }
  function ir(a,b){ const x0=Math.max(a.x,b.x),y0=Math.max(a.y,b.y),x1=Math.min(a.x+a.w,b.x+b.w),y1=Math.min(a.y+a.h,b.y+b.h);
    return (x1<=x0||y1<=y0)?null:{x:x0,y:y0,w:x1-x0,h:y1-y0}; }
  function vis(el){ let r=rect(el); for(const c of clips(el)){ r=ir(r,c); if(!r) return null; } return r; }
  const positions = [0, Math.round(stream.scrollHeight/2), stream.scrollHeight];
  for (const top of positions) {
    stream.scrollTop = top;
    const b = buildEls[buildEls.length-1];
    const rows = [];
    paramEls.forEach((el, i) => {
      if (!b) return;
      const raw = ir(rect(el), rect(b));
      const vv = (vis(el) && vis(b)) ? ir(vis(el), vis(b)) : null;
      if (raw) rows.push({i, raw: Math.round(raw.w*raw.h),
                          visible: vv ? Math.round(vv.w*vv.h) : 0,
                          param_visible: !!vis(el), build_visible: !!vis(b),
                          param_pos: getComputedStyle(el).position, build_pos: getComputedStyle(b).position});
    });
    out.push({scrollTop: Math.round(top), scrollHeight: stream.scrollHeight,
              clientHeight: stream.clientHeight, overflowY: st.overflowY,
              build_rect: b ? rect(b) : null, build_position: b ? getComputedStyle(b).position : null,
              build_in_dom: !!b, raw_overlaps: rows});
  }
  stream.scrollTop = 0;
  return JSON.stringify(out);
})()
"""


def main() -> int:
    report = Report("定向诊断：参数页 plan-build 遮挡参数行？")
    runs = fresh("paramoverlap")
    port = bp.free_port()          # §11.5：动态端口，避免固定端口竞态
    proc = G.start_edge(port, bp.make_profile("paramoverlap"))
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
            log(f"参数项={cdp.js('document.querySelectorAll(\".condopt-param\").length')} "
                f"plan-build 数={cdp.js('document.querySelectorAll(\".condopt-plan-build\").length')}")

            data = json.loads(cdp.js(PROBE))
            if isinstance(data, dict) and data.get("error"):
                report.check("拿到 .stream 容器", False, data["error"])
                return report.finish("v21_param_overlap.log")

            real_occlusions = []
            for block in data:
                log(f"\n=== scrollTop={block['scrollTop']}/{block['scrollHeight']} "
                    f"clientHeight={block['clientHeight']} overflowY={block['overflowY']}")
                log(f"    .condopt-plan-build rect={block['build_rect']} position={block['build_position']}")
                for row in block["raw_overlaps"]:
                    log(f"    param[{row['i']}] raw={row['raw']} 可见重叠={row['visible']} "
                        f"param可见={row['param_visible']} build可见={row['build_visible']} "
                        f"param_pos={row['param_pos']} build_pos={row['build_pos']}")
                    if row["visible"] > 0 and row["param_visible"] and row["build_visible"]:
                        real_occlusions.append((block["scrollTop"], row))

            report.check(
                "plan-build 是 static（正常流元素），不是 sticky/fixed 浮层",
                all(b.get("build_position") == "static" for b in data if b.get("build_in_dom")),
                f"各滚动位置的 position={[b.get('build_position') for b in data]}",
            )
            report.check(
                "**任何滚动位置**下都没有「两者同时可见且相交」的情形（即不存在真遮挡）",
                not real_occlusions,
                f"真遮挡={real_occlusions or '无'}；"
                f"raw 重叠出现在 {sum(len(b['raw_overlaps']) for b in data)} 个 (滚动位置, 参数行) 组合上，"
                f"但其可见重叠全部为 0（一侧已被 .stream 裁掉）",
            )
            cdp.close()
    finally:
        bp.kill_tree(proc)   # §11.5：父还活着时树杀（先 terminate 会让子进程脱管）
    return report.finish("v21_param_overlap.log")


if __name__ == "__main__":
    raise SystemExit(bp.run_guarded(main, "paramoverlap"))
