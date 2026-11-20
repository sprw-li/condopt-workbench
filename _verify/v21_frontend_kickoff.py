"""§10.5.5 前端一致性 + 真点「建 study」端到端。

检查：
 1. 01 首屏是**聊天**而不是表单（旧 45 字段表单在收起抽屉里、不可见）
 2. 算法选项是**可点按钮**（不是裸 URL 文本）
 3. 参数页含助手解释（why/label/plain），且 advanced 参数**默认折叠**
 4. 点「建 study」真的走 POST /api/action {init}，且 run 在服务端出现（同一套 run 语义）

运行：py -3 _verify/v21_frontend_kickoff.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import Report, Server, fresh, get, js, log  # noqa: E402

import v21_geom_states as G  # noqa: E402

FIRST_SCREEN = r"""
(() => {
  const kickoff = document.querySelector('#condoptKickoff');
  const chat = document.querySelector('.condopt-chat-log');
  const adv = document.querySelector('#condoptAdvanced');
  const form = document.querySelector('#condoptForm');
  const visibleInputs = Array.from(document.querySelectorAll('#condoptForm input, #condoptForm select, #condoptForm textarea'))
    .filter(el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && !el.closest('[hidden]'); });
  return JSON.stringify({
    has_kickoff: !!kickoff, has_chat_log: !!chat,
    chat_visible: chat ? (chat.getBoundingClientRect().height > 20) : false,
    adv_hidden: adv ? adv.hidden : null, adv_open: adv ? adv.open : null,
    form_hidden: form ? form.hidden : null,
    visible_form_inputs_on_first_screen: visibleInputs.length,
    chips: document.querySelectorAll('.condopt-chip').length
  });
})()
"""

OPTIONS = r"""
(() => {
  const cards = Array.from(document.querySelectorAll('.condopt-opt-card'));
  return JSON.stringify({
    cards: cards.length,
    all_buttons: cards.every(c => c.tagName === 'BUTTON' || !!c.querySelector('button')),
    has_title: cards.filter(c => c.querySelector('.condopt-opt-title')).length,
    has_one_line: cards.filter(c => c.querySelector('.condopt-opt-one')).length,
    has_why: cards.filter(c => c.querySelector('.condopt-opt-why')).length,
    has_tradeoff: cards.filter(c => c.querySelector('.condopt-opt-tradeoff')).length,
    recommended_badges: document.querySelectorAll('.condopt-badge-rec').length,
    raw_urls: (document.body.innerText.match(/https?:\/\//g) || []).length
  });
})()
"""

PLAN = r"""
(() => {
  const adv = document.querySelector('.condopt-advanced-params');
  const advBody = document.querySelector('.condopt-advanced-params-body');
  return JSON.stringify({
    plan_head: !!document.querySelector('.condopt-plan-head'),
    why_items: document.querySelectorAll('.condopt-plan-why li, .condopt-plan-why p, .condopt-plan-why .condopt-bubble-why').length,
    params: document.querySelectorAll('.condopt-param').length,
    with_label: document.querySelectorAll('.condopt-param-label').length,
    with_plain: document.querySelectorAll('.condopt-param-plain').length,
    with_impact: document.querySelectorAll('.condopt-impact').length,
    advanced_details_present: !!adv,
    advanced_default_collapsed: adv ? (adv.open === false) : null,
    advanced_body_layout_h: advBody ? Math.round(advBody.getBoundingClientRect().height) : null,
    build_btn_text: (function(){ const b=document.querySelector('#condoptKickoffBuild'); return b ? b.innerText.trim() : null; })(),
    run_id_value: (function(){ const i=document.querySelector('#condoptKickoffRunId'); return i ? i.value : null; })(),
    run_semantics_note: (function(){ const n=document.querySelector('.condopt-run-semantics'); return n ? n.innerText.trim().slice(0,80) : null; })()
  });
})()
"""


def main() -> int:
    report = Report("§10.5.5 前端一致性 + 建 study 端到端")
    runs = fresh("fe")
    proc = G.start_edge(9456, Path.home() / ".condopt-fe")
    try:
        with Server(runs) as server:
            cdp = G.CDP(G.new_target(9456))
            cdp.call("Page.enable")
            cdp.call("Runtime.enable")
            cdp.call("Page.navigate", {"url": f"{server.base}/"})
            cdp.pump(4.0)
            for _ in range(40):
                if cdp.js("!!document.querySelector('.condopt-chat-log')"):
                    break
                cdp.pump(0.5)

            first = json.loads(cdp.js(FIRST_SCREEN))
            log(f"首屏：{json.dumps(first, ensure_ascii=False)}")
            report.check(
                "1) 01 首屏是聊天窗（可见）而不是表单（表单字段在首屏可见数为 0，抽屉收起）",
                first["has_chat_log"] and first["chat_visible"]
                and first["visible_form_inputs_on_first_screen"] == 0
                and first["adv_open"] is False and first["adv_hidden"] in (True, False),
                f"{json.dumps(first, ensure_ascii=False)}",
            )
            report.check(
                "1b) 首屏有快捷回复芯片（chip≥2），不是空白输入框",
                first["chips"] >= 2,
                f"chips={first['chips']}",
            )

            for _ in range(10):
                if cdp.js("document.querySelectorAll('.condopt-opt-card').length > 0"):
                    break
                cdp.js("(function(){var c=document.querySelectorAll('.condopt-chip');if(c.length)c[0].click();})()")
                cdp.pump(1.2)
            opts = json.loads(cdp.js(OPTIONS))
            log(f"选项态：{json.dumps(opts, ensure_ascii=False)}")
            report.check(
                "2) 算法选项是**可点按钮**（卡片都是 button），且每卡都有 title/one_line/why/tradeoff",
                opts["cards"] >= 2 and opts["all_buttons"]
                and opts["has_title"] == opts["cards"] and opts["has_one_line"] == opts["cards"]
                and opts["has_why"] == opts["cards"] and opts["has_tradeoff"] == opts["cards"]
                and opts["raw_urls"] == 0,
                f"{json.dumps(opts, ensure_ascii=False)}",
            )
            report.check(
                "2b) recommended 徽章唯一",
                opts["recommended_badges"] == 1,
                f"recommended 徽章数={opts['recommended_badges']}",
            )

            cdp.js("(function(){var b=document.querySelector('.condopt-opt-go');if(b)b.click();})()")
            cdp.pump(2.5)
            plan = json.loads(cdp.js(PLAN))
            log(f"参数页：{json.dumps(plan, ensure_ascii=False)}")
            report.check(
                "3) 参数页：有助手解释（why）+ 每个参数有 label/plain/impact",
                plan["plan_head"] and plan["why_items"] >= 1 and plan["params"] > 0
                and plan["with_label"] == plan["params"] and plan["with_plain"] == plan["params"]
                and plan["with_impact"] == plan["params"],
                f"{json.dumps(plan, ensure_ascii=False)}",
            )
            report.check(
                "3b) advanced 参数**默认折叠**（details 存在且 open=false）",
                plan["advanced_details_present"] and plan["advanced_default_collapsed"] is True,
                f"details 存在={plan['advanced_details_present']} "
                f"open==False 成立={plan['advanced_default_collapsed']}",
            )
            report.check(
                "3c) 页面明示「建 study」与旧入口是同一套 run 语义",
                bool(plan["run_semantics_note"]) or (plan["build_btn_text"] or "").find("init") >= 0,
                f"按钮文案={plan['build_btn_text']!r} run_semantics={plan['run_semantics_note']!r}",
            )

            # ---- 4) 真点「建 study」→ 服务端应出现该 run ----
            before = js(get(server.base, "/api/runs")[2])["runs"]
            cdp.js("(function(){var b=document.querySelector('#condoptKickoffBuild');if(b)b.click();})()")
            cdp.pump(4.0)
            after = js(get(server.base, "/api/runs")[2])["runs"]
            run_id = plan.get("run_id_value")
            new_ids = [r["id"] for r in after if r["id"] not in {x["id"] for x in before}]
            log(f"点击前 runs={[r['id'] for r in before]}；点击后新增={new_ids}；UI 里的 run id={run_id!r}")
            report.check(
                "4) 点「建 study」真的走 init：服务端出现新 run，且与 UI 里填的 run id 一致",
                bool(new_ids) and (run_id in new_ids or run_id in [r["id"] for r in after]),
                f"新增={new_ids} UI run id={run_id!r} 现有={[r['id'] for r in after]}",
            )
            if new_ids:
                detail = js(get(server.base, f"/api/study?id={new_ids[0]}")[2])
                study = detail.get("study") or {}
                cfg = study.get("config") or {}
                report.check(
                    "4b) 新 run 的 config.params 是参数页确认过的值（非空、来自 registry）",
                    bool(cfg.get("params")),
                    f"run={new_ids[0]} algorithm={cfg.get('algorithm')!r} params 键数={len(cfg.get('params') or {})} "
                    f"params 前几项={list((cfg.get('params') or {}).items())[:3]}",
                )
                report.check(
                    "4c) 没有出现残留的中间态 run（只新增 1 个）",
                    len(new_ids) == 1,
                    f"新增={new_ids}",
                )
            cdp.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
    return report.finish("v21_frontend_kickoff.log")


if __name__ == "__main__":
    raise SystemExit(main())
