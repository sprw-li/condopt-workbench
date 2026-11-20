"""§10 对话式开工独立验收（API 侧）：chat / plan / init params。

对应 task-16 的必测 4–12。全部真实 HTTP + 真实子进程服务；router 结果由我自己复算，
不采信响应里的说法。

运行：py -3 _verify/v21_kickoff.py
"""

from __future__ import annotations

import json
import shutil
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    REPO, Report, Server, action, fresh, get, http, js, log,
)

sys.path.insert(0, str(REPO))
from condopt import prepare as condopt_prepare  # noqa: E402
from condopt import registry as condopt_registry  # noqa: E402
from condopt import router as condopt_router  # noqa: E402

CHAT = "/api/kickoff/chat"
PLAN = "/api/kickoff/plan"


def chat(base, content, state=None, free_text=None):
    body = {"messages": [{"role": "user", "content": content}]}
    if state is not None:
        body["state"] = state
    if free_text is not None:
        body["free_text"] = free_text
    status, _, text = http(base, "POST", CHAT, body=body)
    return status, js(text)


def plan(base, state, algorithm="auto"):
    status, _, text = http(base, "POST", PLAN, body={"state": state, "algorithm": algorithm})
    return status, js(text)


def drive_to_options(base, report, label="主流程"):
    """用服务端自己返回的 quick_replies[].value 驱动对话，直到 step==options。"""
    responses = []
    status, first = chat(base, "开始", state=None)
    responses.append(first)
    report.check(
        f"4.{label} 首轮**不带 state** → 200 且 step=='objective'",
        status == 200 and first.get("ok") is True and (first.get("state") or {}).get("step") == "objective",
        f"status={status} step={(first.get('state') or {}).get('step')!r} "
        f"quick_replies={len((first.get('reply') or {}).get('quick_replies') or [])}",
    )
    state = first["state"]
    chip_answers = 0
    for _ in range(8):
        if state.get("step") == "options":
            break
        chips = (responses[-1].get("reply") or {}).get("quick_replies") or []
        if not chips:
            break
        status, resp = chat(base, chips[0]["value"], state=state)
        responses.append(resp)
        chip_answers += 1
        if status != 200:
            break
        state = resp["state"]
    return state, responses, chip_answers


def main() -> int:
    report = Report("§10 对话式开工（API 侧）")
    runs = fresh("kickoff")
    with Server(runs) as server:
        base = server.base

        # ---------------- 4. state 契约 ----------------
        log("\n### 4 chat 的 state 契约")
        status, _, text = http(base, "POST", CHAT, body={"messages": [{"role": "user", "content": "开始"}], "state": {}})
        parsed = js(text)
        report.check(
            "4.1 state:{} → 422 E_VALIDATION 且 message 可读（点明 state.step 非法）",
            status == 422 and parsed.get("error") == "E_VALIDATION" and "state" in str(parsed.get("message")),
            f"status={status} error={parsed.get('error')} message={str(parsed.get('message'))[:90]!r} "
            f"hint={str(parsed.get('hint'))[:60]!r}",
        )
        status, _, text = http(base, "POST", CHAT, body={"messages": [{"role": "user", "content": "开始"}], "state": None})
        report.check(
            "4.2 state:null 等价于省略 → 200",
            status == 200 and js(text).get("ok") is True,
            f"status={status} step={(js(text).get('state') or {}).get('step')!r}",
        )

        # ---------------- 5. 五轮收敛 + 我自己复算 router ----------------
        log("\n### 5 五轮内收敛到选项 + router 复算")
        state, responses, chip_answers = drive_to_options(base, report)
        options = responses[-1].get("options")
        report.check(
            "5.1 五轮内收敛：step=='options' 且 2 <= len(options) <= 4",
            state.get("step") == "options" and options is not None and 2 <= len(options) <= 4,
            f"chip 答案轮数={chip_answers}（≤5 才合规） step={state.get('step')!r} "
            f"options={len(options) if options else None} keys={[o.get('key') for o in (options or [])]}",
        )
        report.check(
            "5.2 每轮 reply.text 非空、quick_replies ≥2（最后一轮除外）",
            all((r.get("reply") or {}).get("text") for r in responses)
            and all(len((r.get("reply") or {}).get("quick_replies") or []) >= 2 for r in responses[:-1]),
            f"各轮 quick_replies 数={[len((r.get('reply') or {}).get('quick_replies') or []) for r in responses]}",
        )
        recommended = [o for o in (options or []) if o.get("recommended") is True]
        report.check(
            "5.3 recommended 唯一",
            len(recommended) == 1,
            f"recommended 个数={len(recommended)} → {[o.get('key') for o in recommended]}",
        )

        # 我自己复算：用 plan 返回的 answers 走 condopt 的 parse_answers + router.route
        status, planned = plan(base, state, "auto")
        my_answers = planned.get("answers") or {}
        my_space, my_objective, my_context = condopt_prepare.parse_answers(my_answers)
        my_route = _to_jsonable_recommendation(condopt_router.route(my_context))
        my_pick = str(my_route.get("algorithm"))
        report.check(
            "5.4 **我自己调 condopt.router.route() 复算**，结果 == 响应里的 recommended",
            status == 200 and recommended and recommended[0].get("key") == my_pick
            and (responses[-1].get("candidates") or {}).get("recommended") == my_pick,
            f"我复算={my_pick}（rationale {len(my_route.get('rationale') or [])} 条） "
            f"响应 recommended={recommended[0].get('key') if recommended else None} "
            f"candidates.recommended={(responses[-1].get('candidates') or {}).get('recommended')!r} "
            f"plan.algorithm={planned.get('algorithm')!r}",
        )

        # ---------------- 5b. 芯片 label（真实点击回传的文本）也能被识别 ----------------
        status, l0 = chat(base, "开始", state=None)
        st_l = l0["state"]
        label = l0["reply"]["quick_replies"][0]["label"]
        status, l1 = chat(base, label, state=st_l)
        st_l2 = l1.get("state") or {}
        report.check(
            "5.5 用芯片 label（按钮点击回传文本，走 _match_chip）也能推进到下一步且目标已填",
            status == 200 and st_l2.get("step") == "budget"
            and str((st_l2.get("answers") or {}).get("objective", {}).get("name") or "").strip(),
            f"发送 label={label!r} → step={st_l2.get('step')!r} "
            f"objective={(st_l2.get('answers') or {}).get('objective')}",
        )

        # ---------------- 6. 信息不足：停住 + 说明缺什么 + options null ----------------
        log("\n### 6 信息不足（变量为空）")
        status, r0 = chat(base, "开始", state=None)                              # → objective
        st = r0["state"]
        status, r1 = chat(base, r0["reply"]["quick_replies"][0]["value"], state=st)   # 答目标 → budget
        st = r1["state"]
        status, r2 = chat(base, r1["reply"]["quick_replies"][0]["value"], state=st)   # 答预算 → variables
        st = r2["state"]
        status, r3 = chat(base, {"variables": []}, state=st)                      # 变量故意留空 → noise
        st = r3["state"]
        report.check(
            "6.0 变量留空后仍前进到 noise（服务端不在这一步就拦）",
            st.get("step") == "noise",
            f"step={st.get('step')!r} answers.variables={st['answers'].get('variables')}",
        )
        status, r4 = chat(base, r3["reply"]["quick_replies"][0]["value"], state=st)   # 答噪声 → constraints
        st = r4["state"]
        report.check(
            "6.0b 答噪声后走到 constraints（此时仍缺 variables）",
            st.get("step") == "constraints",
            f"step={st.get('step')!r} missing={st.get('missing')}",
        )
        status, body5 = chat(base, r4["reply"]["quick_replies"][0]["value"], state=st)  # 答约束 → 应停住
        report.check(
            "6.1 信息不足 → 停在**缺的那一步**（variables），且 options 为 null（不凑答案）",
            status == 200 and body5.get("options") is None and (body5.get("state") or {}).get("step") == "variables",
            f"status={status} step={(body5.get('state') or {}).get('step')!r} options={body5.get('options')!r} "
            f"ready={body5.get('ready')!r}",
        )
        report.check(
            "6.2 reply.text 明确说明缺什么",
            bool(str((body5.get("reply") or {}).get("text")) ) and
            any(word in str((body5.get("reply") or {}).get("text")) for word in ("缺", "还差", "变量")),
            f"reply.text={str((body5.get('reply') or {}).get('text'))[:120]!r}",
        )
        report.check(
            "6.3 state.missing 里列着 variables",
            "variables" in (body5.get("state") or {}).get("missing", []),
            f"missing={(body5.get('state') or {}).get('missing')}",
        )

        # ---------------- 7. free_text 乱输：不许瞎猜 ----------------
        log("\n### 7 free_text 乱输")
        status, r = chat(base, "今天天气不错，随便聊聊", state=None, free_text=True)
        st7 = r.get("state") or {}
        report.check(
            "7.1 free_text 下乱输 → 停在 objective、options=null、明确说没解析出来、不猜参数值",
            status == 200 and st7.get("step") == "objective" and r.get("options") is None
            and not str((st7.get("answers") or {}).get("objective", {}).get("name") or "").strip()
            and any(word in str((r.get("reply") or {}).get("text")) for word in ("没解析", "没看懂", "不敢")),
            f"status={status} step={st7.get('step')!r} objective.name="
            f"{(st7.get('answers') or {}).get('objective', {}).get('name')!r} "
            f"reply={str((r.get('reply') or {}).get('text'))[:100]!r}",
        )

        # ---------------- 8. plan 的三个一致性 ----------------
        log("\n### 8 plan：params/why/display_source")
        algo = planned.get("algorithm")
        described = {item["name"]: item for item in condopt_registry.describe_all()}
        spec_params = described.get(algo, {}).get("params") or []
        got_params = planned.get("params") or []
        param_diffs = []
        if len(spec_params) != len(got_params):
            param_diffs.append(f"数量不一致 registry={len(spec_params)} plan={len(got_params)}")
        for want, got in zip(spec_params, got_params):
            for key in ("name", "type", "default", "choices"):
                if key in want or key in got:
                    if want.get(key) != got.get(key):
                        param_diffs.append(f"{want.get('name')}.{key}: registry={want.get(key)!r} plan={got.get(key)!r}")
        report.check(
            f"8.1 plan.params 与 registry.describe_all()[{algo}].params 逐字段一致（name/type/default/choices）",
            not param_diffs and bool(got_params),
            f"算法={algo} 参数数={len(got_params)} 差异={param_diffs or '无'}",
        )
        report.check(
            "8.2 plan.why == router.route().rationale（我自己复算的那份）",
            [str(x) for x in (planned.get("why") or [])] == [str(x) for x in (my_route.get("rationale") or [])],
            f"plan.why={[str(x)[:40] for x in (planned.get('why') or [])][:3]} "
            f"我复算 rationale={[str(x)[:40] for x in (my_route.get('rationale') or [])][:3]}",
        )
        registry_entries = [p for p in got_params if p.get("display_source") == "registry"]
        hand_entries = [p for p in got_params if p.get("display_source") == "workbench"]
        fallback_bad = []
        for entry in registry_entries:
            want = described.get(algo, {})
            desc = next((p.get("description") for p in spec_params if p.get("name") == entry.get("name")), None)
            if entry.get("label") != entry.get("name") or entry.get("plain") != (desc or "（registry 未提供说明）"):
                fallback_bad.append(entry.get("name"))
        report.check(
            "8.3 display_source=='registry' 的条目确实是回落（label==name 且 plain==registry description）",
            not fallback_bad,
            f"registry 回落 {len(registry_entries)} 个 / workbench 人工 {len(hand_entries)} 个；"
            f"不符={fallback_bad or '无'}；示例={[(p.get('name'), p.get('display_source')) for p in got_params[:4]]}",
        )
        report.check(
            "8.4 advanced 默认折叠字段存在 + params_source/display_source_note 显式标注来源",
            all("advanced" in p for p in got_params) and bool(planned.get("params_source"))
            and bool(planned.get("display_source_note")),
            f"advanced 字段数={sum(1 for p in got_params if p.get('advanced') is True)} "
            f"params_source={str(planned.get('params_source'))[:50]!r}",
        )

        # ---------------- 10/11/12. init params ----------------
        log("\n### 10–12 init params")
        answers = planned.get("answers") or {}
        full = {p["name"]: p.get("default") for p in spec_params}
        status, _, text = action(base, "init", payload={
            "id": "params_full", "answers": answers, "algorithm": algo, "params": full,
            "budget": answers.get("budget"), "batch": answers.get("batch"),
        })
        created = js(text)
        stored = json.loads((runs / "params_full" / "config.json").read_text("utf-8")).get("params")
        report.check(
            "10.1 完整平表 → init 200，且 config.json 里的 params **逐字等于**传入值",
            status == 200 and created.get("ok") is True and stored == full,
            f"status={status} 传入={json.dumps(full, ensure_ascii=False)} "
            f"落地={json.dumps(stored, ensure_ascii=False)}",
        )

        partial = {k: full[k] for k in list(full)[:1]} if full else {}
        status, _, text = action(base, "init", payload={
            "id": "params_partial", "answers": answers, "algorithm": algo, "params": partial,
            "budget": answers.get("budget"), "batch": answers.get("batch"),
        })
        stored_partial = json.loads((runs / "params_partial" / "config.json").read_text("utf-8")).get("params")
        expected_partial = condopt_registry.validate_params(algo, partial)
        report.check(
            "11.1 部分平表 → 传入的键值原样落地，且整体 == registry.validate_params(algorithm, 传入)",
            status == 200
            and all(stored_partial.get(k) == v for k, v in partial.items())
            and stored_partial == json.loads(json.dumps(expected_partial, ensure_ascii=False)),
            f"传入={partial} 落地={json.dumps(stored_partial, ensure_ascii=False)[:160]} "
            f"validate_params 期望={json.dumps(expected_partial, ensure_ascii=False)[:160]}",
        )

        first_key = list(full)[0] if full else "kernel"
        bad_cases = [
            ("未知键", {"no_such_param": 1}, "no_such_param"),
            ("非法值", {first_key: "__definitely_invalid__"}, first_key),
            ("嵌套结构", {first_key: {"nested": 1}}, first_key),
            ("params 非对象", "not-an-object", "params"),
        ]
        for label, bad_params, needle in bad_cases:
            run_id = f"bad_{abs(hash(label)) % 10000}"
            if (runs / run_id).exists():
                shutil.rmtree(runs / run_id)
            status, _, text = action(base, "init", payload={
                "id": run_id, "answers": answers, "algorithm": algo, "params": bad_params,
            })
            parsed = js(text)
            report.check(
                f"12.1 init params {label} → 422 E_VALIDATION 且 message 点名字段（{needle}）",
                status == 422 and parsed.get("error") == "E_VALIDATION" and needle in str(parsed.get("message")),
                f"status={status} error={parsed.get('error')} message={str(parsed.get('message'))[:110]!r}",
            )
            report.check(
                f"12.2 init params {label} → **不留残留 run 目录**",
                not (runs / run_id).exists(),
                f"{run_id} 存在={(runs / run_id).exists()}",
            )

        run_id = "bad_auto_params"
        if (runs / run_id).exists():
            shutil.rmtree(runs / run_id)
        status, _, text = action(base, "init", payload={
            "id": run_id, "answers": answers, "algorithm": "auto", "params": {first_key: "x"},
        })
        parsed = js(text)
        report.check(
            "12.3 algorithm='auto' + 非空 params → 422 且 message 说明 auto 下不能指定参数",
            status == 422 and parsed.get("error") == "E_VALIDATION" and "auto" in str(parsed.get("message")),
            f"status={status} message={str(parsed.get('message'))[:120]!r}",
        )
        report.check(
            "12.4 auto+params 也不留残留目录",
            not (runs / run_id).exists(),
            f"{run_id} 存在={(runs/run_id).exists()}",
        )

        # ---------------- 13. verifier 自选对抗 ----------------
        log("\n### 13 额外对抗（verifier 自选）")
        # 13.1 全算法扫一遍 plan：params 逐字段一致 + display_source 合法 + 回落内容正确
        algo_report = []
        fallback_entries = 0
        bad_fallback = []
        bad_impact = []
        mismatch = []
        for algo_name in condopt_registry.names():
            st_status, st_planned = plan(base, state, algo_name)
            if st_status != 200:
                mismatch.append(f"{algo_name}: plan {st_status}")
                continue
            spec = {item["name"]: item for item in condopt_registry.describe_all()}.get(algo_name, {}).get("params") or []
            got = st_planned.get("params") or []
            for want, entry in zip(spec, got):
                for key in ("name", "type", "default", "choices"):
                    if want.get(key) != entry.get(key):
                        mismatch.append(f"{algo_name}.{want.get('name')}.{key}")
                if entry.get("display_source") not in ("workbench", "registry"):
                    mismatch.append(f"{algo_name}.{want.get('name')}.display_source")
                if entry.get("display_source") == "registry":
                    fallback_entries += 1
                    if entry.get("label") != entry.get("name") or entry.get("plain") != (
                        want.get("description") or "（registry 未提供说明）"
                    ):
                        bad_fallback.append(f"{algo_name}.{entry.get('name')}")
                if entry.get("impact") not in ("high", "medium", "low"):
                    bad_impact.append(f"{algo_name}.{entry.get('name')}={entry.get('impact')!r}")
            algo_report.append(f"{algo_name}({len(got)}p)")
        report.check(
            f"13.1 全 {len(condopt_registry.names())} 个算法的 plan.params 逐字段一致且 display_source 合法",
            not mismatch,
            f"算法={algo_report[:6]}… 不一致={mismatch[:6] or '无'}",
        )
        report.check(
            "13.2 display_source=='registry' 的回落条目内容正确（label==name、plain==registry description）",
            not bad_fallback,
            f"全算法共 {fallback_entries} 个回落条目；不符={bad_fallback[:5] or '无'}"
            + ("" if fallback_entries else "；注意：本次扫描未触发任何回落（PARAM_DISPLAY 全覆盖），"
                                          "该分支内容本身未被实测"),
        )
        report.check(
            "13.3 impact 只取 high/medium/low",
            not bad_impact,
            f"越界={bad_impact[:5] or '无'}",
        )

        # 13.2 plan 显式选非推荐算法 → 给出对照 warning，且 algorithm 字段确实是所选
        other = next((o["key"] for o in options if o.get("key") != recommended[0]["key"]), None)
        if other:
            status, alt = plan(base, state, other)
            warns = " ".join(str(w) for w in (alt.get("warnings") or []))
            report.check(
                f"13.4 plan 显式选非推荐算法（{other}）→ 200、algorithm==所选、且有对照 warning",
                status == 200 and alt.get("algorithm") == other and "首选" in warns,
                f"status={status} algorithm={alt.get('algorithm')!r} warnings={warns[:120]!r}",
            )

        # 13.3 plan 的入参校验
        status, _, text = http(base, "POST", PLAN, body={"state": state, "algorithm": "no_such_algo"})
        report.check(
            "13.5 plan 未知算法 → 422 E_VALIDATION",
            status == 422 and js(text).get("error") == "E_VALIDATION",
            f"status={status} message={str(js(text).get('message'))[:70]!r}",
        )
        status, _, text = http(base, "POST", PLAN, body={"algorithm": "auto"})
        report.check(
            "13.6 plan 缺 state → 422 且提示需要 state",
            status == 422 and "state" in str(js(text).get("message")),
            f"status={status} message={str(js(text).get('message'))[:70]!r}",
        )
        status, _, text = http(base, "POST", CHAT, body={"messages": [], "oops": 1})
        report.check(
            "13.7 chat 未知 payload 字段 → 422 点名字段",
            status == 422 and "oops" in str(js(text).get("message")),
            f"status={status} message={str(js(text).get('message'))[:70]!r}",
        )
        status, empty_msgs = chat(base, "", state=None)
        report.check(
            "13.8 chat 空回答（content 为空串）→ 200 且仍停在同一问题，不前进、不崩",
            status == 200 and (empty_msgs.get("state") or {}).get("step") == "objective"
            and empty_msgs.get("options") is None,
            f"status={status} step={(empty_msgs.get('state') or {}).get('step')!r}",
        )
        status, again = chat(base, "随便再说一句", state=state)
        report.check(
            "13.9 已在 options 状态下再喂消息 → 保持 options（状态不倒流）",
            status == 200 and (again.get("state") or {}).get("step") == "options"
            and again.get("options") == options,
            f"status={status} step={(again.get('state') or {}).get('step')!r} "
            f"options 一致={again.get('options') == options}",
        )

        # 13.10 被篡改的 state：变量只有名字、没有范围/kind → 必须受控失败，不许产出「凑出来的」选项
        # 关键：要送一个**能解析的 constraints 答案**，否则只会在 constraints 步「没看懂再问一次」，
        # 那样这条检查就是空的（我第一版就踩了这个：200/options=null 是因为答案没被识别）。
        for label, variables in (
            ("只有名字", [{"name": "T"}]),
            ("有 kind 无范围", [{"name": "T", "kind": "continuous"}]),
        ):
            tampered = json.loads(json.dumps(state, ensure_ascii=False))
            tampered["answers"]["variables"] = variables
            tampered["answers"].pop("constraint_items", None)
            tampered["step"] = "constraints"
            status, _, text = http(base, "POST", CHAT, body={
                "messages": [{"role": "user", "content": {"constraints": []}}], "state": tampered,
            })
            parsed_t = js(text)
            produced = parsed_t.get("options")
            controlled = status == 422 or (status == 200 and produced is None)
            report.check(
                f"13.10 变量「{label}」的篡改 state → 受控失败（4xx 或 options:null），不产出凑出来的选项",
                controlled,
                f"status={status} error={parsed_t.get('error')} options={'有' if produced else 'null'} "
                f"step={(parsed_t.get('state') or {}).get('step')!r} "
                f"message={str(parsed_t.get('message'))[:100]!r}",
            )
            if status == 200 and produced is None:
                report.check(
                    f"13.10b 变量「{label}」→ 停在原步且 reply 说明缺什么（不是静默失败）",
                    (parsed_t.get("state") or {}).get("step") in ("variables", "constraints")
                    and bool(str((parsed_t.get("reply") or {}).get("text"))),
                    f"step={(parsed_t.get('state') or {}).get('step')!r} "
                    f"missing={(parsed_t.get('state') or {}).get('missing')} "
                    f"reply={str((parsed_t.get('reply') or {}).get('text'))[:90]!r}",
                )
        # ---------------- 12.5 兼容：不传 params 行为不变 ----------------
        status, _, text = action(base, "init", payload={
            "id": "no_params", "answers": answers, "algorithm": algo,
            "budget": answers.get("budget"), "batch": answers.get("batch"),
        })
        report.check(
            "12.5 不传 params → 仍 200（旧行为兼容）",
            status == 200 and (runs / "no_params" / "config.json").exists(),
            f"status={status} config 存在={(runs / 'no_params' / 'config.json').exists()}",
        )

    # ---------------- 9. 离线（自写 monkeypatch + 阳性对照） ----------------
    log("\n### 9 离线：屏蔽 socket 后 chat→plan→init 仍跑通（含阳性对照）")
    offline_runs = fresh("kickoff_offline")
    sys.path.insert(0, str(REPO))
    import bridge as bridge_module  # noqa: E402

    real_connect = socket.socket.connect
    real_create = socket.create_connection
    real_connect_ex = socket.socket.connect_ex

    def _boom(*args, **kwargs):
        raise OSError("网络被 verifier 屏蔽（离线验收）")

    control = ""
    try:
        socket.socket.connect = _boom
        socket.create_connection = _boom
        socket.socket.connect_ex = _boom
        try:
            socket.create_connection(("127.0.0.1", 9), timeout=1)
            control = "未拦住（补丁无效）"
        except OSError as exc:
            control = f"已拦住: {exc}"
        report.check(
            "9.1 阳性对照：补丁确实能拦住一次真实连接（否则本项测试无意义）",
            control.startswith("已拦住"),
            control,
        )

        b = bridge_module.Bridge(offline_runs)
        b.import_llm = False
        state = None
        chat_ok = True
        detail = []
        for index in range(6):
            body = {"messages": [{"role": "user", "content": "开始"}]} if index == 0 else {
                "messages": [{"role": "user", "content": (resp.get("reply") or {}).get("quick_replies")[0]["value"]}],
                "state": state,
            }
            resp = b.kickoff_chat(body)
            state = resp["state"]
            detail.append(state.get("step"))
            if state.get("step") == "options":
                break
        chat_ok = state.get("step") == "options"
        planned_off = b.kickoff_plan({"state": state, "algorithm": "auto"})
        init_off = b.dispatch("init", None, {
            "id": "offline_run", "answers": planned_off["answers"],
            "algorithm": planned_off["algorithm"], "budget": planned_off["answers"].get("budget"),
            "batch": planned_off["answers"].get("batch"),
        })
        report.check(
            "9.2 屏蔽 socket 后 chat→plan→init 全部跑通（纯规则，无外网依赖）",
            chat_ok and planned_off.get("algorithm") and (offline_runs / "offline_run" / "config.json").exists(),
            f"对话步进={detail} plan.algorithm={planned_off.get('algorithm')!r} "
            f"init 建库={(offline_runs / 'offline_run' / 'config.json').exists()}",
        )
    finally:
        socket.socket.connect = real_connect
        socket.create_connection = real_create
        socket.socket.connect_ex = real_connect_ex
        if not control.startswith("已拦住"):
            report.check("9.0 补丁已恢复", True, "已恢复 socket 原方法")

    return report.finish("v21_kickoff.log")


def _to_jsonable_recommendation(recommendation):
    data = recommendation.to_dict() if hasattr(recommendation, "to_dict") else recommendation
    return json.loads(json.dumps(data, ensure_ascii=False, default=str))


if __name__ == "__main__":
    raise SystemExit(main())
