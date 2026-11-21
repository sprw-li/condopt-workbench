"""诊断：v21_geom_states 报出的「可疑重叠」到底是**真遮挡**还是**滚动容器裁切造成的假象**。

方法（不改 geom_probe 的判定逻辑，只在本脚本里做更强的可见性分析）：
1. 对每一对可疑元素，取两者 rect 的交集（候选重叠区）；
2. 把两个元素各自用**所有裁切祖先**（overflow != visible）的 rect 逐层求交，得到「实际可见区域」；
3. 若两个可见区域的交集为空 → 这段重叠根本看不见 → 判为探针假阳性；
4. 再在重叠中心做 `document.elementFromPoint` 命中测试，看最上层是谁；
5. 最后截图供人眼确认。

运行：py -3 _verify/v21_geom_diag.py
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

#: 在页面里做「裁切感知」的重叠分析：输入两对选择器，输出详细证据
DIAG_JS = r"""
(() => {
  const pairs = %s;
  function rectOf(el) { const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; }
  function clipChain(el) {
    const out = [];
    let p = el.parentElement;
    while (p) {
      const st = getComputedStyle(p);
      if (st.overflowX !== 'visible' || st.overflowY !== 'visible') {
        out.push({tag: p.tagName.toLowerCase(), cls: p.className && String(p.className).slice(0,60), rect: rectOf(p),
                  ox: st.overflowX, oy: st.overflowY});
      }
      p = p.parentElement;
    }
    return out;
  }
  function intersect(a, b) {
    const x0 = Math.max(a.x, b.x), y0 = Math.max(a.y, b.y);
    const x1 = Math.min(a.x + a.w, b.x + b.w), y1 = Math.min(a.y + a.h, b.y + b.h);
    if (x1 <= x0 || y1 <= y0) return null;
    return {x:x0, y:y0, w:x1-x0, h:y1-y0};
  }
  function visibleRect(el) {
    let r = rectOf(el);
    for (const c of clipChain(el)) { r = intersect(r, c.rect); if (!r) return null; }
    return r;
  }
  const results = [];
  for (const [sa, sb] of pairs) {
    const ea = document.querySelector(sa), eb = document.querySelector(sb);
    if (!ea || !eb) { results.push({pair:[sa,sb], missing:true}); continue; }
    const ra = rectOf(ea), rb = rectOf(eb);
    const raw = intersect(ra, rb);
    const va = visibleRect(ea), vb = visibleRect(eb);
    const visInter = (va && vb) ? intersect(va, vb) : null;
    let hit = null, hitChain = [];
    if (raw) {
      const cx = raw.x + raw.w/2, cy = raw.y + raw.h/2;
      let el = document.elementFromPoint(cx, cy);
      const chain = [];
      while (el && chain.length < 5) { chain.push(el.tagName.toLowerCase() + (el.className ? '.' + String(el.className).split(' ')[0] : '')); el = el.parentElement; }
      hit = chain[0] || null; hitChain = chain;
    }
    results.push({pair:[sa,sb], raw_overlap_area: raw ? Math.round(raw.w*raw.h) : 0,
                  a: ra, b: rb,
                  a_pos: getComputedStyle(ea).position, b_pos: getComputedStyle(eb).position,
                  a_clip: clipChain(ea), b_clip: clipChain(eb),
                  a_visible: va, b_visible: vb,
                  visible_overlap_area: visInter ? Math.round(visInter.w*visInter.h) : 0,
                  hit_test_topmost: hit, hit_chain: hitChain});
  }
  return JSON.stringify(results);
})()
"""

PAIRS = [
    [".condopt-kickoff-head", ".condopt-bubble"],
    [".condopt-plan-build", ".condopt-param"],
    [".condopt-param", ".condopt-advanced-params"],
]


def main() -> int:
    report = Report("几何重叠真伪诊断（裁切感知 + 命中测试）")
    runs = fresh("geomdiag")
    profile = bp.make_profile("geomdiag")
    port = bp.free_port()
    proc = G.start_edge(port, profile)
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

            # 走到 S3 参数页（与 v21_geom_states 同样的路径）
            for _ in range(10):
                if cdp.js("document.querySelectorAll('.condopt-opt-card').length > 0"):
                    break
                cdp.js("(function(){var c=document.querySelectorAll('.condopt-chip');if(c.length)c[0].click();})()")
                cdp.pump(1.2)
            cdp.js("(function(){var b=document.querySelector('.condopt-opt-go');if(b)b.click();})()")
            cdp.pump(2.5)
            log(f"已到参数页：参数项={cdp.js('document.querySelectorAll(\".condopt-param\").length')}")

            shot = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
            path = SHOTS / "kickoff_param_page.png"
            path.write_bytes(base64.b64decode(shot["data"]))
            log(f"截图：{path}")

            data = json.loads(cdp.js(DIAG_JS % json.dumps(PAIRS, ensure_ascii=False)))
            for item in data:
                if item.get("missing"):
                    log(f"\n=== {item['pair']} → 元素不存在（该状态没有）")
                    continue
                log(f"\n=== {item['pair']}")
                log(f"    raw 重叠面积={item['raw_overlap_area']}  可见重叠面积={item['visible_overlap_area']}")
                log(f"    a rect={item['a']} pos={item['a_pos']} 裁切祖先={[(c['tag'], c['cls'], c['oy']) for c in item['a_clip']]}")
                log(f"    b rect={item['b']} pos={item['b_pos']} 裁切祖先={[(c['tag'], c['cls'], c['oy']) for c in item['b_clip']]}")
                log(f"    a 可见区={item['a_visible']}  b 可见区={item['b_visible']}")
                log(f"    命中测试最上层={item['hit_test_topmost']} 链={item['hit_chain']}")
                real = item["visible_overlap_area"] > 0
                report.check(
                    f"诊断 {item['pair']} → {'**真遮挡**（可见重叠 %d px²）' % item['visible_overlap_area'] if real else '假阳性（裁切后可见重叠=0）'}",
                    True,
                    f"raw={item['raw_overlap_area']} visible={item['visible_overlap_area']} "
                    f"hit={item['hit_test_topmost']}",
                )
            cdp.close()
    finally:
        bp.kill_tree(proc)   # §11.5：父还活着时树杀（先 terminate 会让子进程脱管）
    return report.finish("v21_geom_diag.log")


if __name__ == "__main__":
    raise SystemExit(bp.run_guarded(main, "geomdiag"))
