"""确认 + 定级：参数页滚到底时 `.condopt-plan-build` 与最后两条 `.condopt-param` 重叠。

要回答三个问题：
1. 两者是否祖孙关系？（若是包含关系，重叠正常）
2. 重叠区里**谁在最上层**？参数行里的输入控件还能不能被点到（elementFromPoint）？
3. 成因线索：两元素祖先链的 rect / overflow / max-height。

运行：py -3 _verify/v21_param_overlap2.py
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_profile as bp  # §11.5 临时资源卫生（profile 不残留 + 动态端口）
from common import Report, SHOTS, Server, fresh, log  # noqa: E402

import v21_geom_states as G  # noqa: E402

PROBE = r"""
(() => {
  const stream = document.querySelector('.stream');
  const builds = Array.from(document.querySelectorAll('.condopt-plan-build'));
  const params = Array.from(document.querySelectorAll('.condopt-param'));
  const b = builds[builds.length-1];
  if (!stream || !b) return JSON.stringify({error:'missing .stream or .condopt-plan-build'});
  stream.scrollTop = stream.scrollHeight;
  function rect(el){ const r=el.getBoundingClientRect(); return {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)}; }
  function chain(el){ const a=[]; let p=el;
    while(p && a.length<8){ const s=getComputedStyle(p);
      a.push({tag:p.tagName.toLowerCase(), cls:String(p.className||'').slice(0,44), rect:rect(p),
              oy:s.overflowY, pos:s.position, maxH:s.maxHeight, h:s.height, mt:s.marginTop, mb:s.marginBottom});
      p=p.parentElement; } return a; }
  function ir(a,b){ const x0=Math.max(a.x,b.x),y0=Math.max(a.y,b.y),x1=Math.min(a.x+a.w,b.x+b.w),y1=Math.min(a.y+a.h,b.y+b.h);
    return (x1<=x0||y1<=y0)?null:{x:x0,y:y0,w:x1-x0,h:y1-y0}; }
  const target = params[params.length-1];
  const o = ir(rect(target), rect(b));
  const controls = Array.from(target.querySelectorAll('input,select,textarea,button'));
  const hits = controls.map(c => { const r=c.getBoundingClientRect();
    if (!r.width || !r.height) return {ctrl:c.tagName.toLowerCase()+'.'+String(c.className||'').split(' ')[0], skipped:true};
    const cx=r.x+r.width/2, cy=r.y+r.height/2;
    const top=document.elementFromPoint(cx,cy);
    return {ctrl:c.tagName.toLowerCase()+'.'+String(c.className||'').split(' ')[0],
            topmost: top ? (top.tagName.toLowerCase()+'.'+String(top.className||'').split(' ')[0]) : null,
            clickable: !!(top && (top===c || c.contains(top) || top.contains(c)))}; });
  let centerTop=null;
  if (o) { const t=document.elementFromPoint(o.x+o.w/2, o.y+o.h/2);
    centerTop = t ? t.tagName.toLowerCase()+'.'+String(t.className||'').split(' ')[0] : null; }
  return JSON.stringify({
    scrollTop: Math.round(stream.scrollTop), scrollHeight: stream.scrollHeight,
    build_is_ancestor_of_param: b.contains(target),
    param_is_ancestor_of_build: target.contains(b),
    param_nested_in_build: null,
    param_index: params.length-1,
    build_rect: rect(b), target_rect: rect(target), overlap: o,
    overlap_center_topmost: centerTop,
    controls_in_target: hits,
    build_chain: chain(b),
    target_chain: chain(target)
  });
})()
"""


def main() -> int:
    report = Report("确认与定级：参数页底部遮挡")
    SHOTS.mkdir(parents=True, exist_ok=True)   # 克隆里可能不存在（健壮性：缺目录不该算产品失败）
    runs = fresh("paramoverlap2")
    port = bp.free_port()          # §11.5：动态端口，避免固定端口竞态
    proc = G.start_edge(port, bp.make_profile("paramoverlap2"))
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
                return report.finish("v21_param_overlap2.log")

            shot = cdp.call("Page.captureScreenshot", {"format": "png"})
            path = SHOTS / "param_page_bottom_overlap.png"
            path.write_bytes(base64.b64decode(shot["data"]))
            log(f"底部截图：{path}")

            log(json.dumps({k: data[k] for k in (
                "scrollTop", "scrollHeight", "build_is_ancestor_of_param", "param_is_ancestor_of_build",
                "build_rect", "target_rect", "overlap", "overlap_center_topmost")}, ensure_ascii=False, indent=2))
            log("最后一条参数的控件命中测试：")
            for h in data["controls_in_target"]:
                log(f"   {h}")
            log("plan-build 祖先链：")
            for c in data["build_chain"]:
                log(f"   {c}")
            log("param 祖先链：")
            for c in data["target_chain"]:
                log(f"   {c}")

            report.check(
                "两者**不是**祖孙关系（包含关系不算缺陷）",
                data["build_is_ancestor_of_param"] is False and data["param_is_ancestor_of_build"] is False,
                f"build 包含 param={data['build_is_ancestor_of_param']} "
                f"param 包含 build={data['param_is_ancestor_of_build']}",
            )
            report.check(
                "**修复后应成立**：滚到底时最后一条参数与 build 块**不再重叠**"
                "（F10/task-18 给 `.condopt-advanced-params` 补了 `:not([open])>body{display:none}` 后，"
                "原先 41,566 px² 的幻影相交已消失）",
                not data.get("overlap"),
                f"overlap={data.get('overlap')} center_topmost={data.get('overlap_center_topmost')}"
                f"（修复前此断言期望「确实重叠」，是当时的定级口径）",
            )
            blocked = [h for h in data["controls_in_target"] if h.get("clickable") is False]
            report.check(
                "最后一条参数里的输入控件仍可点击（没有被 build 块挡住 → 影响定级）",
                not blocked,
                f"被挡控件={blocked or '无'}；全部命中={data['controls_in_target']}",
            )
            cdp.close()
    finally:
        bp.kill_tree(proc)   # §11.5：父还活着时树杀（先 terminate 会让子进程脱管）
    return report.finish("v21_param_overlap2.log")


if __name__ == "__main__":
    raise SystemExit(bp.run_guarded(main, "paramoverlap2"))
