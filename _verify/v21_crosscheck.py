"""两项交叉验证：

**A. 几何判据交叉对照**：把 lead 新版 `geom_probe.py` 的**判定代码**（`MEASURE_JS % SELECTORS`）
   注入到**我驱动的四种状态**里跑，与我自己判据的结论逐态比对。
   —— 这样验的是「他的判据 + 我的状态覆盖」，不是各跑各的。

**B. F10 修复的副作用**：web-shell 说参数抽屉用纯 CSS `display:none`（不用 `hidden` 属性），
   为的是收起态仍能被 `readParams()` 读到值。这条**必须我自己验**：
   展开抽屉 → 改一个高级参数 → **收起抽屉** → 点「建 study」 → 检查 config.params 里那笔改动还在。

运行：py -3 _verify/v21_crosscheck.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import Report, Server, fresh, get, js, log  # noqa: E402

import geom_probe as LP  # lead 的新版探针：复用它的 MEASURE_JS / SELECTORS
import v21_geom_states as G

STATES = ("S1 首屏", "S2 选项态", "S3 参数页", "S4 抽屉打开态")


def his_verdict(cdp):
    """用 lead 的 JS 测量，并按他的口径算结论（real = 命中栈里可见）。"""
    data = json.loads(cdp.js(LP.MEASURE_JS % json.dumps(LP.SELECTORS, ensure_ascii=False)))
    boxes, pairs = data["boxes"], data["pairs"]
    zero = [b for b in boxes if b["h"] <= 1 or b["w"] <= 1]
    real = [p for p in pairs if p["in_stack"]]
    phantom = [p for p in pairs if not p["in_stack"]]
    return boxes, pairs, zero, real, phantom


def set_advanced_param(cdp, name: str, value: str) -> bool:
    """展开抽屉 → 找到该参数的输入框并设值 → 收起抽屉。返回是否找到。"""
    return bool(cdp.js(
        """(function(){
             const det=document.querySelector('.condopt-advanced-params');
             det.open=true; det.dispatchEvent(new Event('toggle'));
             const rows=[...document.querySelectorAll('.condopt-advanced-params-body .condopt-param')];
             const row=rows.find(r => (r.textContent||'').includes(%s));
             if(!row) return false;
             const ctl=row.querySelector('input,select,textarea');
             if(!ctl) return false;
             const proto = ctl.tagName==='SELECT' ? HTMLSelectElement : HTMLInputElement;
             const setter = Object.getOwnPropertyDescriptor(proto.prototype,'value').set;
             setter.call(ctl, %s);
             ctl.dispatchEvent(new Event('input',{bubbles:true}));
             ctl.dispatchEvent(new Event('change',{bubbles:true}));
             det.open=false; det.dispatchEvent(new Event('toggle'));
             return true;
           })()""" % (json.dumps(name), json.dumps(value))
    ))


def main() -> int:
    report = Report("交叉对照：lead 判据 × 我的状态 + F10 副作用")
    runs = fresh("crosscheck")
    proc = G.start_edge(9457, Path.home() / ".condopt-crosscheck")
    try:
        with Server(runs) as server:
            cdp = G.CDP(G.new_target(9457))
            cdp.call("Page.enable")
            cdp.call("Runtime.enable")
            cdp.call("Page.navigate", {"url": f"{server.base}/"})
            cdp.pump(4.0)
            for _ in range(40):
                if cdp.js("!!document.querySelector('.condopt-chat-log')"):
                    break
                cdp.pump(0.5)

            mine = {}
            his = {}

            # --- S1 ---
            mine["S1 首屏"] = G.judge(G.measure(cdp), clipping_aware=True)
            his["S1 首屏"] = his_verdict(cdp)
            # --- S2 ---
            for _ in range(10):
                if cdp.js("document.querySelectorAll('.condopt-opt-card').length > 0"):
                    break
                cdp.js("(function(){var c=document.querySelectorAll('.condopt-chip');if(c.length)c[0].click();})()")
                cdp.pump(1.2)
            mine["S2 选项态"] = G.judge(G.measure(cdp), clipping_aware=True)
            his["S2 选项态"] = his_verdict(cdp)
            # --- S3 ---
            cdp.js("(function(){var b=document.querySelector('.condopt-opt-go');if(b)b.click();})()")
            cdp.pump(2.5)
            mine["S3 参数页"] = G.judge(G.measure(cdp), clipping_aware=True)
            his["S3 参数页"] = his_verdict(cdp)
            # --- S4 抽屉打开态 ---
            cdp.js("(function(){document.querySelectorAll('details').forEach(d=>{d.open=true;"
                   "d.dispatchEvent(new Event('toggle'));});})()")
            cdp.pump(2.0)
            mine["S4 抽屉打开态"] = G.judge(G.measure(cdp), clipping_aware=True)
            his["S4 抽屉打开态"] = his_verdict(cdp)

            log("\n=== A. 逐态对照（我的判据 vs lead 的判据，同一批页面状态）===")
            agree = True
            for name in STATES:
                my_zero, my_bad = mine[name]
                boxes, pairs, hz, real, phantom = his[name]
                ok = (not my_zero) and (not my_bad) and (not hz) and (not real)
                agree = agree and ok
                log(f"  {name}:")
                log(f"    我的判据：零尺寸={len(my_zero)} 可见重叠={len(my_bad)}")
                log(f"    lead 判据：可见块={len(boxes)} 零尺寸={len(hz)} 原始相交={len(pairs)} "
                    f"真实={len(real)} 幻影={len(phantom)}")
                if real:
                    for p in real:
                        log(f"      REAL {p['ratio']:.2f} {p['a']} × {p['b']} 布局增量={p['layout_delta']}")
                report.check(
                    f"A {name}：两套判据结论一致且都判无问题"
                    f"（我 0/0；lead 0 零尺寸 / 0 真实重叠）",
                    ok,
                    f"我 零尺寸={len(my_zero)} 重叠={len(my_bad)}；"
                    f"lead 零尺寸={len(hz)} 真实={len(real)} 幻影={len(phantom)}",
                )
            report.check(
                "A 合计：四种状态两套判据**逐态一致**（没有出现一方报问题、另一方漏掉）",
                agree,
                f"四态均一致={agree}",
            )

            # --- B. 收起的高级参数是否仍进入 init ---
            log("\n=== B. F10 副作用：收起态改的高级参数是否仍提交 ===")
            # 回到参数页（S4 把 details 全开了，先收起来）
            cdp.js("(function(){document.querySelectorAll('details').forEach(d=>{d.open=false;"
                   "d.dispatchEvent(new Event('toggle'));});})()")
            cdp.pump(1.2)
            before = js(get(server.base, "/api/runs")[2])["runs"]
            found = set_advanced_param(cdp, "n_candidates", "777")
            collapsed = cdp.js("document.querySelector('.condopt-advanced-params').open === false")
            cdp.pump(0.8)
            cdp.js("(function(){var b=document.querySelector('#condoptKickoffBuild');if(b)b.click();})()")
            cdp.pump(4.0)
            after = js(get(server.base, "/api/runs")[2])["runs"]
            new_ids = [r["id"] for r in after if r["id"] not in {x["id"] for x in before}]
            params = None
            if new_ids:
                detail = js(get(server.base, f"/api/study?id={new_ids[0]}")[2])
                params = ((detail.get("study") or {}).get("config") or {}).get("params")
            log(f"  找到并修改 n_candidates=777：{found}；抽屉收起={collapsed}；新建 run={new_ids}")
            log(f"  新 run 的 params.n_candidates = {(params or {}).get('n_candidates')!r}")
            report.check(
                "B1 能在高级参数抽屉里改 n_candidates 并收起抽屉",
                found and collapsed is True,
                f"found={found} collapsed={collapsed}",
            )
            report.check(
                "B2 **抽屉收起时改的值仍然进入 init**（收起不影响取值 —— web-shell 的关键论断）",
                bool(new_ids) and (params or {}).get("n_candidates") == 777,
                f"新 run={new_ids} params.n_candidates={(params or {}).get('n_candidates')!r}（期望 777）",
            )
            cdp.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
    return report.finish("v21_crosscheck.log")


if __name__ == "__main__":
    raise SystemExit(main())
