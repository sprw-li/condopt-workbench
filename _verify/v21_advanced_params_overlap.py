"""决定性判定：`.condopt-advanced-params`（高级参数 details）**打开**后是否与「建 study」块重叠。

三种情形的对照，用来区分「真遮挡」与「paint-skipping 造成的幻影矩形」：
  A) details 关闭（默认）→ 高级参数行是否被绘制？真实重叠被判定的区域里 elementFromPoint 命中谁？
  B) details 打开 → body 参与绘制后，build 块是否被推开？是否出现真重叠？
  C) 命中测试：build 按钮与最后一条高级参数的输入框，各自能否被点到。

运行：py -3 _verify/v21_advanced_params_overlap.py
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import Report, SHOTS, Server, fresh, log  # noqa: E402

import v21_geom_states as G  # noqa: E402

PROBE = r"""
(() => {
  const stream = document.querySelector('.stream');
  const det = document.querySelector('.condopt-advanced-params');
  const body = document.querySelector('.condopt-advanced-params-body');
  const build = document.querySelector('.condopt-plan-build');
  if (!stream || !det || !body || !build) return JSON.stringify({error:'missing nodes'});
  function rect(el){ const r=el.getBoundingClientRect(); return {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)}; }
  function ir(a,b){ const x0=Math.max(a.x,b.x),y0=Math.max(a.y,b.y),x1=Math.min(a.x+a.w,b.x+b.w),y1=Math.min(a.y+a.h,b.y+b.h);
    return (x1<=x0||y1<=y0)?null:{x:Math.round(x0),y:Math.round(y0),w:Math.round(x1-x0),h:Math.round(y1-y0)}; }
  function hit(pt){ if(!pt) return null; const e=document.elementFromPoint(pt.x+pt.w/2, pt.y+pt.h/2);
    return e ? e.tagName.toLowerCase()+'.'+String(e.className||'').split(' ')[0] : null; }
  const rows = Array.from(body.querySelectorAll('.condopt-param'));
  function snap(tag){
    return {tag, open: det.open,
            det_rect: rect(det), body_rect: rect(body), build_rect: rect(build),
            body_display: getComputedStyle(body).display,
            body_visibility: getComputedStyle(body).visibility,
            body_contentVisibility: getComputedStyle(body).contentVisibility,
            body_overflow: getComputedStyle(body).overflow,
            overlap_body_build: ir(rect(body), rect(build)),
            overlap_rows_build: rows.map((r,i)=>({i, r: ir(rect(r), rect(build))})).filter(o=>o.r),
            first_row_rect: rows.length ? rect(rows[0]) : null,
            last_row_rect: rows.length ? rect(rows[rows.length-1]) : null,
            first_row_topmost: rows.length ? hit(rect(rows[0])) : null,
            build_btn_topmost: (function(){ const b=build.querySelector('button'); return b?hit(rect(b)):null; })(),
            stream_scrollTop: Math.round(stream.scrollTop), stream_scrollHeight: stream.scrollHeight};
  }
  stream.scrollTop = stream.scrollHeight;
  const closed = snap('closed');
  det.open = true;
  det.dispatchEvent(new Event('toggle'));
  stream.scrollTop = stream.scrollHeight;
  const opened = snap('opened');
  return JSON.stringify({closed, opened});
})()
"""


def main() -> int:
    report = Report("高级参数 details 打开前后的重叠对照")
    runs = fresh("advparams")
    proc = G.start_edge(9454, Path.home() / ".condopt-advparams")
    try:
        with Server(runs) as server:
            cdp = G.CDP(G.new_target(9454))
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
                return report.finish("v21_advanced_params_overlap.log")

            for key in ("closed", "opened"):
                d = data[key]
                log(f"\n=== details {d['tag']} ===")
                log(f"    open={d['open']} body display={d['body_display']} visibility={d['body_visibility']} "
                    f"contentVisibility={d['body_contentVisibility']} overflow={d['body_overflow']}")
                log(f"    det={d['det_rect']} body={d['body_rect']} build={d['build_rect']}")
                log(f"    body × build 重叠={d['overlap_body_build']}")
                log(f"    参数行 × build 重叠={d['overlap_rows_build']}")
                log(f"    首行 topmost={d['first_row_topmost']}  build 按钮 topmost={d['build_btn_topmost']}")

            c = data["closed"]
            o = data["opened"]
            report.check(
                "A) details 关闭时 body 已被显式禁止参与布局（display:none / 高度 0）——"
                "即 task-18 的加固已生效，不再依赖 UA 的 paint-skip 行为",
                c["body_display"] == "none" and c["body_rect"]["h"] == 0,
                f"body display={c['body_display']} h={c['body_rect']['h']} contentVisibility={c['body_contentVisibility']}",
            )
            report.check(
                "B) details 打开后 build 块被推到参数表之后（不再与任何参数行重叠）",
                not o["overlap_rows_build"]
                and (o["build_rect"]["y"] >= o["body_rect"]["y"] + o["body_rect"]["h"] - 2),
                f"打开后 build.y={o['build_rect']['y']} body.y+h={o['body_rect']['y']+o['body_rect']['h']} "
                f"参数行×build 重叠={o['overlap_rows_build']}",
            )
            report.check(
                "C) 打开后「建 study」按钮仍可点到（命中它自己或其子元素）",
                "condopt-plan-build" in str(o["build_btn_topmost"]) or "btn" in str(o["build_btn_topmost"]),
                f"build 按钮 topmost={o['build_btn_topmost']}",
            )

            shot = cdp.call("Page.captureScreenshot", {"format": "png"})
            path = SHOTS / "param_page_advanced_open.png"
            path.write_bytes(base64.b64decode(shot["data"]))
            log(f"打开态截图：{path}")

            # 反证：若把 details 打开后再关闭，build 是否回到原位置（不留残余）
            back = cdp.js(
                """(function(){
                  var det=document.querySelector('.condopt-advanced-params');
                  var build=document.querySelector('.condopt-plan-build');
                  var body=document.querySelector('.condopt-advanced-params-body');
                  det.open=false; det.dispatchEvent(new Event('toggle'));
                  var r=build.getBoundingClientRect();
                  return JSON.stringify({y:Math.round(r.y), bodyDisplay:getComputedStyle(body).display});
                })()"""
            )
            back = json.loads(back)
            report.check(
                "D) 再关闭后 build 回到原位置（关闭态不留下版面残余）",
                abs(back["y"] - c["build_rect"]["y"]) <= 2,
                f"关闭→打开→关闭后 build.y={back['y']}（初始 {c['build_rect']['y']}）",
            )
            cdp.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
    return report.finish("v21_advanced_params_overlap.log")


if __name__ == "__main__":
    raise SystemExit(main())
