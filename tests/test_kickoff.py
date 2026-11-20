"""对话式开工（CONTRACT §10）的验收测试。

独立文件（不动既有 66 项）。三类证据混用，避免「自家断言自家实现」：

* **端到端**：真起 HTTP 服务，``chat`` 五轮 → ``plan`` → 拿返回的 ``answers``/``algorithm``
  直接调 ``POST /api/action {init}``，再回到 runs 目录里核对 ``problem.json``/``config.json``；
* **独立复算**：`why`/选项打分不与实现比对，而是**重新调用 condopt 的 router** 算一遍；
* **离线证明**：把 ``Bridge._llm_chat`` 换成「被调用即失败」，整条链路仍必须跑通。
"""

from __future__ import annotations

import json
import re
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bridge as bridge_module  # noqa: E402
import server as server_module  # noqa: E402
from condopt import prepare as condopt_prepare  # noqa: E402
from condopt import registry as condopt_registry  # noqa: E402
from condopt.router import route as condopt_route  # noqa: E402

TIMEOUT = 120
#: §10.1 的五个轮次（芯片答案）。
CHIP_FLOW = (
    ("objective", "收率越大越好"),
    ("budget", "很贵 · 24 次"),
    ("variables", "温度 + 压力（用 20–150 / 1–10 的默认区间）"),
    ("noise", "噪声中 · 一般"),
    ("constraints", "纯度 ≥ 95"),
)
NO_CONSTRAINT_FLOW = (*CHIP_FLOW[:4], ("constraints", "没有硬约束"))


# --------------------------------------------------------------------------- #
# 夹具 / 小工具
# --------------------------------------------------------------------------- #


@pytest.fixture()
def runs_dir(tmp_path: Path) -> Path:
    return tmp_path / "runs"


@pytest.fixture()
def live_server(runs_dir: Path):
    httpd = server_module.create_server(runs_dir=runs_dir, host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield base
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def call(base: str, path: str, body: dict, method: str = "POST"):
    """返回 ``(status, payload_dict, raw_text)``；HTTP 错误也读 body。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            text = response.read().decode("utf-8", "replace")
            return response.status, json.loads(text), text
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")
        return exc.code, json.loads(text), text


def chat(base: str, state: dict | None, content, free_text: bool = False):
    body: dict = {"messages": [{"role": "user", "content": content}]}
    if state is not None:
        body["state"] = state
    if free_text:
        body["free_text"] = True
    status, payload, text = call(base, "/api/kickoff/chat", body)
    assert status == 200, f"{status} {text[:300]}"
    return payload


def run_flow(base: str, flow=CHIP_FLOW):
    """跑一遍五轮，返回 (最后一轮响应, 每轮响应列表)。"""
    state = None
    rounds = []
    for _step, label in flow:
        payload = chat(base, state, label)
        state = payload["state"]
        rounds.append(payload)
    return rounds[-1], rounds


def plan(base: str, state: dict, algorithm: str = "auto"):
    status, payload, text = call(
        base, "/api/kickoff/plan", {"state": state, "algorithm": algorithm}
    )
    assert status == 200, f"{status} {text[:300]}"
    return payload


# --------------------------------------------------------------------------- #
# 1. 五轮收敛（§10.5.1 / §10.5.2）
# --------------------------------------------------------------------------- #


def test_chat_converges_in_five_rounds(live_server: str):
    last, rounds = run_flow(live_server)
    assert [r["state"]["step"] for r in rounds] == ["budget", "variables", "noise", "constraints", "options"]
    # 前四轮只有问题、没有选项
    for index, payload in enumerate(rounds[:4]):
        assert payload["options"] is None, f"第 {index + 1} 轮不该给选项"
        assert payload["ready"] is False
        assert "为什么问这个" in payload["reply"]["text"]
        assert len(payload["reply"]["quick_replies"]) >= 2, f"第 {index + 1} 轮快捷回复 < 2"
    # 最后一轮 = 选项
    assert last["state"]["step"] == "options"
    assert last["ready"] is True
    assert isinstance(last["options"], list) and 2 <= len(last["options"]) <= 4
    for card in last["options"]:
        for key in ("key", "title", "one_line", "why", "tradeoff", "recommended", "risk"):
            assert key in card, f"选项缺字段 {key}: {card}"
        assert card["key"] in condopt_registry.names()
        assert card["risk"] in ("low", "medium", "high")


def test_every_round_reply_is_nonempty_chinese_prose(live_server: str):
    _last, rounds = run_flow(live_server)
    for payload in rounds:
        text = payload["reply"]["text"].strip()
        assert text and len(text) >= 20
        assert text.count("。") + text.count("\n") >= 1


# --------------------------------------------------------------------------- #
# 2. 选项 = router 的真实打分（独立复算）
# --------------------------------------------------------------------------- #


def test_recommended_is_exactly_the_router_pick(live_server: str):
    last, _rounds = run_flow(live_server)
    recommended = [card for card in last["options"] if card["recommended"]]
    assert len(recommended) == 1, f"recommended 必须唯一: {[c['recommended'] for c in last['options']]}"
    # 独立复算：把 plan 的 answers 重新喂给 condopt router
    plan_payload = plan(live_server, last["state"])
    space, objective, context = condopt_prepare.parse_answers(plan_payload["answers"])
    recomputed = condopt_route(context).to_dict()
    assert recommended[0]["key"] == recomputed["algorithm"]
    assert last["candidates"]["recommended"] == recomputed["algorithm"]


def test_option_scores_equal_router_scores(live_server: str):
    last, _rounds = run_flow(live_server)
    plan_payload = plan(live_server, last["state"])
    _space, _objective, context = condopt_prepare.parse_answers(plan_payload["answers"])
    scores = condopt_route(context).to_dict()["candidate_scores"]
    for card in last["options"]:
        assert card["score"] == pytest.approx(float(scores[card["key"]])), f"{card['key']} 分数与 router 不一致"


def test_options_are_ordered_by_score_with_pick_first(live_server: str):
    last, _rounds = run_flow(live_server)
    keys = [card["key"] for card in last["options"]]
    assert keys[0] == last["candidates"]["recommended"]
    scores = [float(card["score"]) for card in last["options"]]
    assert scores == sorted(scores, reverse=True), f"选项未按打分排序: {scores}"


# --------------------------------------------------------------------------- #
# 3. 解析不了就再问，绝不瞎猜（§10.2 / §10.5.3）
# --------------------------------------------------------------------------- #


def test_variables_without_range_stops_instead_of_guessing(live_server: str):
    state = None
    for _step, label in CHIP_FLOW[:2]:
        state = chat(live_server, state, label)["state"]
    payload = chat(live_server, state, "温度、压力", free_text=True)
    assert payload["state"]["step"] == "variables", "只报名字不该前进"
    assert payload["options"] is None
    assert payload["state"]["missing"] == ["variables"]
    assert payload["state"]["answers"]["variables"] == []
    assert "再问" in payload["reply"]["text"] or "换个说法" in payload["reply"]["text"]
    assert len(payload["reply"]["quick_replies"]) >= 2


def test_variables_free_text_with_explicit_ranges_is_parsed(live_server: str):
    state = None
    for _step, label in CHIP_FLOW[:2]:
        state = chat(live_server, state, label)["state"]
    payload = chat(live_server, state, "温度 20-150、溶剂[MeCN, EtOH]", free_text=True)
    assert payload["state"]["step"] == "noise"
    variables = payload["state"]["answers"]["variables"]
    assert [item["name"] for item in variables] == ["温度", "溶剂"]
    assert variables[0] == {
        "name": "温度", "kind": "continuous", "low": 20.0, "high": 150.0,
    }
    assert variables[1]["kind"] == "categorical" and variables[1]["choices"] == ["MeCN", "EtOH"]


def test_free_text_outside_free_text_flag_does_not_guess(live_server: str):
    """没带 free_text:true 时纯文本不解析，但**仍是再问一次**而不是 500。"""
    state = None
    for _step, label in CHIP_FLOW[:2]:
        state = chat(live_server, state, label)["state"]
    payload = chat(live_server, state, "温度 20-150")
    assert payload["state"]["step"] == "variables"
    assert payload["options"] is None
    assert payload["state"]["answers"]["variables"] == []


def test_objective_free_text_needs_a_direction(live_server: str):
    payload = chat(live_server, None, "收率", free_text=True)
    assert payload["state"]["step"] == "objective"
    assert payload["state"]["answers"]["objective"]["name"] == ""
    # 用**不是芯片标签**的说法，证明走的是规则解析而不是 chip 匹配
    good = chat(live_server, payload["state"], "能耗越低越好", free_text=True)
    assert good["state"]["step"] == "budget"
    assert good["state"]["answers"]["objective"] == {"name": "能耗", "direction": "min"}


def test_budget_free_text_parses_digits(live_server: str):
    first = chat(live_server, None, "收率越大越好")
    payload = chat(live_server, first["state"], "大概做 60 次实验", free_text=True)
    assert payload["state"]["step"] == "variables"
    assert payload["state"]["answers"]["budget"] == 60


def test_constraint_parsing_uses_spec_and_keeps_no_constraint_path_plain(live_server: str):
    with_constraint, _rounds = run_flow(live_server, CHIP_FLOW)
    plan_with = plan(live_server, with_constraint["state"])
    assert plan_with["answers"]["constraints"] == 1
    assert plan_with["answers"]["spec"]["constraints"]["purity"] == {"op": ">=", "value": 95.0}
    assert [goal["metric"] for goal in plan_with["answers"]["spec"]["goals"]] == ["yield"]
    assert any("spec" in warning for warning in plan_with["warnings"])

    plain, _rounds = run_flow(live_server, NO_CONSTRAINT_FLOW)
    plan_plain = plan(live_server, plain["state"])
    assert plan_plain["answers"]["constraints"] == 0
    assert "spec" not in plan_plain["answers"], "没有硬约束就不该走 spec 路径"


def test_defer_to_import_chip_stops_with_null_options(live_server: str):
    state = None
    for _step, label in CHIP_FLOW[:2]:
        state = chat(live_server, state, label)["state"]
    payload = chat(live_server, state, "我贴一份历史数据（走 06 导入）")
    assert payload["options"] is None
    assert payload["ready"] is False
    assert payload["state"]["step"] == "variables"
    assert "导入" in payload["reply"]["text"]


def test_plan_refuses_when_information_is_insufficient(live_server: str):
    status, payload, text = call(
        live_server,
        "/api/kickoff/plan",
        {"state": {"step": "variables", "answers": {"objective": {"name": "yield", "direction": "max"}, "variables": []}}},
    )
    assert status == 422, text[:200]
    assert payload["error"] == "E_VALIDATION"
    assert "可调条件" in payload["message"]


# --------------------------------------------------------------------------- #
# 4. plan.params 必须逐字段来自 registry（§10.3 / §10.5.4）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("algorithm", ["bayes", "constrained", "sobol", "tpe"])
def test_plan_params_match_registry_field_by_field(live_server: str, algorithm: str):
    last, _rounds = run_flow(live_server)
    payload = plan(live_server, last["state"], algorithm=algorithm)
    described = {item["name"]: item for item in condopt_registry.describe_all()}[algorithm]
    expected = described["params"]
    got = payload["params"]
    assert len(got) == len(expected), f"{algorithm} 参数个数不一致"
    for want, have in zip(expected, got):
        for key in ("name", "type", "default", "choices"):
            # 键的存在性也要一致：registry 只给 choice 参数带 choices，不许我们凭空加/删
            assert (key in have) == (key in want), f"{algorithm}.{want['name']} 的 {key} 有无不一致"
            assert have.get(key) == want.get(key), f"{algorithm}.{want['name']}.{key}: {have.get(key)!r} != {want.get(key)!r}"
        # registry 的其余字段也必须原样带过来（不许只挑几个字段伪造）
        for key, value in want.items():
            assert have.get(key) == value, f"{algorithm}.{want['name']} 的 {key} 被改动"
    # 展示层字段必须都是「加」上去的，不是替换掉 registry 的
    assert all("display_source" in item for item in got)


def test_plan_display_layer_is_marked_and_falls_back_to_registry(live_server: str):
    last, _rounds = run_flow(live_server)
    payload = plan(live_server, last["state"], algorithm="bayes")
    for item in payload["params"]:
        assert item["display_source"] in ("workbench", "registry")
        assert item["label"] and item["plain"]
        assert item["impact"] in ("high", "medium", "low")
        assert isinstance(item["advanced"], bool)
        if item["display_source"] == "workbench":
            copy = bridge_module._param_copy("bayes", item["name"])
            assert copy is not None, f"{item['name']} 标了 workbench 却查不到文案"
            assert item["label"] == copy["label"]
            assert item["plain"] == copy["plain"]
        else:
            assert item["plain"] == item["description"]
    # 至少要能看出两种来源都真的出现过
    assert "workbench" in payload["display_source_note"]
    assert "registry" in payload["display_source_note"]
    assert "registry" in payload["params_source"]


def test_plan_why_is_router_rationale_verbatim(live_server: str):
    last, _rounds = run_flow(live_server)
    payload = plan(live_server, last["state"])
    _space, _objective, context = condopt_prepare.parse_answers(payload["answers"])
    expected = [str(item) for item in condopt_route(context).to_dict()["rationale"]]
    assert payload["why"] == expected
    assert payload["why"], "why 不该为空"


def test_plan_auto_uses_router_pick_and_unknown_algorithm_is_422(live_server: str):
    last, _rounds = run_flow(live_server)
    auto = plan(live_server, last["state"], algorithm="auto")
    assert auto["algorithm"] == auto["recommended_algorithm"] == last["candidates"]["recommended"]
    status, payload, _text = call(
        live_server, "/api/kickoff/plan", {"state": last["state"], "algorithm": "nope"}
    )
    assert status == 422 and payload["error"] == "E_VALIDATION"


def test_plan_warns_when_user_overrides_the_router(live_server: str):
    last, _rounds = run_flow(live_server)
    pick = last["candidates"]["recommended"]
    other = next(card["key"] for card in last["options"] if card["key"] != pick)
    payload = plan(live_server, last["state"], algorithm=other)
    assert payload["algorithm"] == other
    assert any(pick in warning for warning in payload["warnings"]), payload["warnings"]


# --------------------------------------------------------------------------- #
# 5. 端到端：chat → plan → init（§10.4 / §10.5.5 的后端部分）
# --------------------------------------------------------------------------- #


def test_chat_plan_init_end_to_end(live_server: str, runs_dir: Path):
    last, _rounds = run_flow(live_server)
    plan_payload = plan(live_server, last["state"], algorithm="auto")
    status, payload, text = call(
        live_server,
        "/api/action",
        {
            "action": "init",
            "payload": {
                "id": "kickoff-run",
                "answers": plan_payload["answers"],
                "algorithm": plan_payload["algorithm"],
            },
        },
    )
    assert status == 200, text[:400]
    assert payload["ok"] is True

    run_dir = runs_dir / "kickoff-run"
    assert run_dir.is_dir()
    problem = json.loads((run_dir / "problem.json").read_text(encoding="utf-8"))
    config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    assert config["algorithm"] == plan_payload["algorithm"]
    assert [item["name"] for item in problem["space"]["variables"]] == ["温度", "压力"]
    assert problem["objective"]["constraint_limits"] == {"purity": {"op": ">=", "value": 95.0}}
    assert int(problem["budget"]) == 24
    # runs 目录是唯一真源：/api/study 能看到它
    with urllib.request.urlopen(live_server + "/api/study?id=kickoff-run", timeout=TIMEOUT) as response:
        assert response.status == 200
        snapshot = json.loads(response.read().decode("utf-8"))
    assert snapshot["ok"] is True and snapshot["study"]["id"] == "kickoff-run"


def test_same_run_semantics_as_other_entrypoints(live_server: str, runs_dir: Path):
    """对话入口与直接 init 建出的 study 在 runs 目录里是同一套语义。"""
    last, _rounds = run_flow(live_server, NO_CONSTRAINT_FLOW)
    plan_payload = plan(live_server, last["state"])
    for run_id in ("from-chat", "from-form"):
        answers = dict(plan_payload["answers"])
        if run_id == "from-form":
            answers = bridge_module.condopt_prepare.template()
        status, payload, text = call(
            live_server,
            "/api/action",
            {"action": "init", "payload": {"id": run_id, "answers": answers, "algorithm": plan_payload["algorithm"]}},
        )
        assert status == 200, text[:300]
    for run_id in ("from-chat", "from-form"):
        problem = json.loads((runs_dir / run_id / "problem.json").read_text(encoding="utf-8"))
        config = json.loads((runs_dir / run_id / "config.json").read_text(encoding="utf-8"))
        assert {"space", "objective", "budget"} <= set(problem), f"{run_id} 的 problem.json 形状不同"
        assert set(problem) == set(
            json.loads((runs_dir / "from-chat" / "problem.json").read_text(encoding="utf-8"))
        ), "两个入口写出的 problem.json 形状必须一致"
        assert config["algorithm"] == plan_payload["algorithm"]
        assert problem["objective"]["direction"] == "max"


# --------------------------------------------------------------------------- #
# 6. 离线：默认纯规则，一次模型调用都不许发生（§10.4 / §10.5.6）
# --------------------------------------------------------------------------- #


def test_whole_flow_never_calls_the_llm(live_server: str, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("对话式开工默认必须纯规则：不该调用模型")

    monkeypatch.setattr(bridge_module.Bridge, "_llm_chat", forbidden)
    monkeypatch.delenv("BOHRIUM_API_KEY", raising=False)
    last, rounds = run_flow(live_server)
    plan_payload = plan(live_server, last["state"])
    assert plan_payload["params"] and plan_payload["why"]
    assert len(rounds) == 5


def test_bootstrap_reports_offline_default(live_server: str, monkeypatch):
    monkeypatch.delenv("BOHRIUM_API_KEY", raising=False)
    with urllib.request.urlopen(live_server + "/api/bootstrap", timeout=TIMEOUT) as response:
        boot = json.loads(response.read().decode("utf-8"))
    assert boot["import_llm"]["enabled"] is False
    assert boot["import_llm"]["configured"] is False


# --------------------------------------------------------------------------- #
# 7. 错误模型：沿用既有 E_VALIDATION / E_BAD_REQUEST（§10.4）
# --------------------------------------------------------------------------- #


def test_kickoff_error_model(live_server: str):
    cases = [
        ("/api/kickoff/chat", {"messages": [], "zzz": 1}, 422, "E_VALIDATION"),
        ("/api/kickoff/chat", {"messages": "not-a-list"}, 422, "E_VALIDATION"),
        ("/api/kickoff/chat", {"messages": [], "state": {"step": "nope", "answers": {}}}, 422, "E_VALIDATION"),
        ("/api/kickoff/chat", {"messages": [], "state": 42}, 422, "E_VALIDATION"),
        ("/api/kickoff/plan", {"algorithm": "bayes"}, 422, "E_VALIDATION"),
        ("/api/kickoff/plan", {"state": {"answers": {}}, "bogus": True}, 422, "E_VALIDATION"),
        ("/api/kickoff/plan", {"state": {"answers": {}}, "algorithm": 12}, 422, "E_VALIDATION"),
    ]
    for path, body, status, error in cases:
        got_status, payload, text = call(live_server, path, body)
        assert got_status == status, f"{path} {body}: {got_status} {text[:200]}"
        assert payload["error"] == error, f"{path} {body}: {payload}"
        assert payload["message"], "错误必须带可读 message"


def test_kickoff_endpoints_reject_get(live_server: str):
    for path in ("/api/kickoff/chat", "/api/kickoff/plan"):
        req = urllib.request.Request(live_server + path, method="GET")
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(req, timeout=TIMEOUT)
        assert excinfo.value.code == 405


def test_unknown_scenario_import_hint_unchanged(live_server: str):
    """§10 不该动到既有入口的错误语义。"""
    status, payload, _text = call(live_server, "/api/run", {"scenario": "import"})
    assert status == 400 and payload["error"] == "E_BAD_REQUEST"


# --------------------------------------------------------------------------- #
# 8. 响应信封与契约形状一致（防止字段漂移把前端打崩）
# --------------------------------------------------------------------------- #


def test_response_envelope_matches_contract_shape(live_server: str):
    payload = chat(live_server, None, "收率越大越好")
    assert set(payload) >= {"ok", "reply", "state", "ready", "options", "candidates"}
    assert set(payload["reply"]) >= {"text", "quick_replies"}
    assert set(payload["state"]) >= {"step", "answers"}
    assert payload["ok"] is True
    for chip in payload["reply"]["quick_replies"]:
        assert set(chip) == {"label", "value"}, chip
        assert isinstance(chip["label"], str) and chip["label"]
        assert isinstance(chip["value"], dict) and chip["value"]
    last, _rounds = run_flow(live_server)
    assert set(last) >= {"ok", "reply", "state", "ready", "options", "candidates"}
    plan_payload = plan(live_server, last["state"])
    assert set(plan_payload) >= {"ok", "algorithm", "why", "params", "answers", "warnings"}
    for item in plan_payload["params"]:
        assert {"name", "type", "default", "label", "plain", "impact", "advanced"} <= set(item)


def test_llm_polish_switch_position_is_offline_safe(tmp_path: Path):
    """§10.4：``--import-llm`` 只是预留的润色开关；打开且**没有密钥**时也必须照常可用。"""
    bridge = bridge_module.Bridge(tmp_path / "runs", import_llm=True)
    assert bridge.import_llm is True
    state = None
    for _step, label in CHIP_FLOW:
        state = bridge.kickoff_chat({"messages": [{"role": "user", "content": label}], "state": state})["state"]
    plan_payload = bridge.kickoff_plan({"state": state, "algorithm": "auto"})
    assert plan_payload["algorithm"]
    # 占位实现必须是恒等函数（不许偷偷改 JSON 结构或路由结论）
    sample = {"text": "x", "quick_replies": []}
    assert bridge._kickoff_polish_reply(sample) == sample


# --------------------------------------------------------------------------- #
# 9. init 接受 params（task-15）：平表、422 点名、config.json 一致
# --------------------------------------------------------------------------- #


def init_call(base: str, run_id: str, algorithm: str, params=None, answers=None):
    payload = {"id": run_id, "answers": answers or bridge_module.condopt_prepare.template(),
               "algorithm": algorithm}
    if params is not None:
        payload["params"] = params
    return call(base, "/api/action", {"action": "init", "payload": payload})


def test_init_params_land_in_config_json(live_server: str, runs_dir: Path):
    """task-15 的核心断言：传入的 params 必须真的写进 runs/<id>/config.json。"""
    passed = {"kernel": "rbf", "alpha": 0.05}
    status, payload, text = init_call(live_server, "params-run", "bayes", passed)
    assert status == 200, text[:300]
    assert payload["ok"] is True

    config = json.loads((runs_dir / "params-run" / "config.json").read_text(encoding="utf-8"))
    stored = config["params"]
    # ① 传入的每个键值都原样落地（这是「参数页调完参真的生效」的判据）
    assert {key: stored[key] for key in passed} == passed, f"传入 {passed}，config.json 里是 {stored}"
    # ② 整体等于 condopt 自己的规范化结果（证明没被悄悄丢/改；condopt 会补默认值）
    assert stored == bridge_module.condopt_registry.validate_params("bayes", passed), stored
    # ③ 响应里也能看到算法确实是 bayes
    assert config["algorithm"] == "bayes"


def test_plan_params_roundtrip_through_init(live_server: str, runs_dir: Path):
    """参数页真实流程：plan 的 params → 改一个值 → init → config.json 反映改动。"""
    last, _rounds = run_flow(live_server)
    plan_payload = plan(live_server, last["state"], algorithm="bayes")
    defaults = {item["name"]: item["default"] for item in plan_payload["params"] if "default" in item}
    changed = dict(defaults)
    changed["kernel"] = next(choice for choice in plan_payload["params"][0]["choices"] if choice != defaults["kernel"])
    status, _payload, text = init_call(
        live_server, "params-page", "bayes", changed, answers=plan_payload["answers"]
    )
    assert status == 200, text[:300]
    stored = json.loads((runs_dir / "params-page" / "config.json").read_text(encoding="utf-8"))["params"]
    assert stored["kernel"] == changed["kernel"] != defaults["kernel"], stored
    assert {key: stored[key] for key in changed} == changed
    # 参数页给的是**完整**平表（plan 返回全部参数 + 默认值），这时 config.json 必须**逐字相等**
    assert stored == changed, f"完整平表必须原样落地\n传入: {changed}\n落地: {stored}"


def test_init_without_params_behaves_like_before(live_server: str, runs_dir: Path):
    status, _payload, text = init_call(live_server, "no-params", "bayes")
    assert status == 200, text[:300]
    stored = json.loads((runs_dir / "no-params" / "config.json").read_text(encoding="utf-8"))["params"]
    assert stored == bridge_module.condopt_registry.validate_params("bayes", None)
    # 空表等价于不传
    status, _payload, text = init_call(live_server, "empty-params", "bayes", {})
    assert status == 200, text[:300]
    empty = json.loads((runs_dir / "empty-params" / "config.json").read_text(encoding="utf-8"))["params"]
    assert empty == stored


def test_init_params_errors_are_422_and_name_the_field(live_server: str, runs_dir: Path):
    cases = [
        ("unknown-key", {"nope": 1}, "nope"),
        ("bad-value", {"kernel": "no-such-kernel"}, "kernel"),
        ("nested", {"bayes": {"kernel": "rbf"}}, "bayes"),
        ("auto-with-params", {"kernel": "rbf"}, "auto"),
    ]
    for run_id, params, needle in cases:
        algorithm = "auto" if run_id == "auto-with-params" else "bayes"
        status, payload, text = init_call(live_server, run_id, algorithm, params)
        assert status == 422, f"{run_id}: {status} {text[:200]}"
        assert payload["error"] == "E_VALIDATION"
        assert needle in payload["message"], f"{run_id} 没点名字段: {payload['message']}"
        assert payload["hint"]
        # 校验失败不许留下半成品 run
        assert not (runs_dir / run_id).exists(), f"{run_id} 校验失败却建了目录"
    status, payload, _text = init_call(live_server, "not-a-map", "bayes", "kernel=rbf")
    assert status == 422 and "平表" in payload["message"]


def test_init_auto_without_params_still_routes(live_server: str, runs_dir: Path):
    answers = bridge_module.condopt_prepare.template()
    status, _payload, text = init_call(live_server, "auto-ok", "auto", answers=answers)
    assert status == 200, text[:300]
    config = json.loads((runs_dir / "auto-ok" / "config.json").read_text(encoding="utf-8"))
    _space, _objective, context = bridge_module.condopt_prepare.parse_answers(answers)
    recommendation = bridge_module.condopt_route(context)
    assert config["algorithm"] == recommendation.algorithm, "auto 必须落成 router 选中的算法"
    assert config["params"] == recommendation.params, "auto 的参数必须来自 router（不许被清空）"


# --------------------------------------------------------------------------- #
# 10. 文案不许静默说错（task-17 / 验收 F9）
# --------------------------------------------------------------------------- #


def _param_specs() -> dict[str, dict[str, dict]]:
    """``{参数名: {算法: ParamSpec}}``（来自 registry，不手写）。"""
    specs: dict[str, dict[str, dict]] = {}
    for algorithm in condopt_registry.names():
        for param in condopt_registry.describe(algorithm)["params"]:
            specs.setdefault(param["name"], {})[algorithm] = param
    return specs


def _mentions(text: str, token: str) -> bool:
    """token 作为**独立词**出现在文案里（``logei`` 不算提到 ``ei``）。"""
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", text) is not None


def test_param_copy_index_resolution():
    """索引规则：先精确 ``(algorithm, name)``，再通用 ``name``。"""
    assert bridge_module._param_copy("bayes", "acquisition") is bridge_module.PARAM_DISPLAY[("bayes", "acquisition")]
    assert bridge_module._param_copy("mfbo", "acquisition") is bridge_module.PARAM_DISPLAY["acquisition"]
    assert bridge_module._param_copy("bayes", "kernel") is bridge_module.PARAM_DISPLAY["kernel"]
    assert bridge_module._param_copy("bayes", "no-such-param") is None
    # 通用 acquisition 文案不许列举 pi/ts（mfbo 等只有 ei/logei/ucb）
    generic = bridge_module.PARAM_DISPLAY["acquisition"]["plain"]
    assert not _mentions(generic, "pi") and not _mentions(generic, "ts")


def _same_value(claimed: str, actual: object) -> bool:
    """把文案里的字面量断言与 registry 的实际值比较（容忍写法差异）。

    ``1e-6`` vs ``1e-06``、``0.30`` vs ``0.3``、``true`` vs ``True`` 都算相等——
    这些是同一个值的不同写法，不是谎言；报的是**真的不一样**。
    """
    if str(claimed).lower() == str(actual).lower():
        return True
    try:
        return float(claimed) == float(actual)
    except (TypeError, ValueError):
        return False


def _param_copy_lies(display: dict | None = None) -> list[str]:
    """检查人工文案里「对算法库的可证伪断言」，返回问题清单（空 = 没有说错）。

    抽成函数是为了让**负对照**能直接调用它（篡改一份副本 → 必须报出问题），
    而不是只能靠改源码再跑 pytest。
    """
    specs = _param_specs()
    problems: list[str] = []
    for key, entry in (bridge_module.PARAM_DISPLAY if display is None else display).items():
        if isinstance(key, tuple):
            algorithm, name = key
            applicable = [algorithm] if algorithm in specs.get(name, {}) else []
        else:
            name = key
            applicable = sorted(specs.get(name, {}))
        if name not in specs:
            problems.append(f"陈旧条目：PARAM_DISPLAY[{key!r}] 不是任何算法的参数")
            continue
        if not applicable:
            problems.append(f"空适用集：PARAM_DISPLAY[{key!r}] 在任何算法里都取不到")
            continue
        text = f"{entry['label']} {entry['plain']}"
        # 该参数名下所有出现过的 choice 字面量，逐个检查是否对每条适用算法都合法
        ever = {value for spec in specs[name].values() for value in (spec.get("choices") or [])}
        for value in sorted(ever):
            if not _mentions(text, value):
                continue
            for algorithm in applicable:
                legal = set(specs[name][algorithm].get("choices") or [])
                if value not in legal:
                    problems.append(
                        f"PARAM_DISPLAY[{key!r}].plain 提到 {value!r}，但 {algorithm} 的 {name} "
                        f"choices 是 {sorted(legal)}（文案: {text!r}）"
                    )
        # 「默认 X」断言：X 是字面量时**必须**等于该算法的 default（不再要求 X 先落在已知值里）
        for claimed in re.findall(r"默认[值是为：:]*\s*([A-Za-z0-9_.+-]+)", text):
            for algorithm in applicable:
                actual = specs[name][algorithm].get("default")
                if actual is None:
                    problems.append(
                        f"PARAM_DISPLAY[{key!r}] 说「默认 {claimed}」，但 {algorithm} 的 {name} "
                        f"在 registry 里没有 default（不该声称默认值）"
                    )
                elif not _same_value(claimed, actual):
                    problems.append(
                        f"PARAM_DISPLAY[{key!r}] 说「默认 {claimed}」，但 {algorithm} 的 {name} "
                        f"default 是 {actual!r}"
                    )
    return problems


def test_param_copy_never_lies():
    """人工文案对算法库的可证伪断言必须成立（选择项 / 默认值）。

    * 文案里出现**任何 registry choice 字面量**时，该值必须是「这条文案适用的每个算法」
      的合法 choice；否则红并指出 算法/参数/取值。
    * 出现「默认 X」（X 是字面量）时，X 必须等于该算法的 registry default；
      **任意而错误的数字也会被抓**（例如写「默认 4096」而 registry 是 1024）。
    * **不禁止数字**：「工作台建议区间 0.01–0.1」这类是建议、不是对库的断言，允许保留。
    """
    problems = _param_copy_lies()
    assert not problems, "人工文案与 registry 不一致：\n" + "\n".join(problems)


def test_every_algorithm_has_a_hand_written_card():
    """18 个算法都有卡片文案（不让某个算法悄悄回落到 registry 摘要）。"""
    missing = [name for name in condopt_registry.names() if name not in bridge_module.ALGO_DISPLAY]
    assert not missing, f"这些算法缺人工卡片文案: {missing}"
    for name, entry in bridge_module.ALGO_DISPLAY.items():
        assert name in condopt_registry.names(), f"ALGO_DISPLAY 有陈旧算法 {name!r}"
        assert entry["risk"] in ("low", "medium", "high")
        assert entry["title"] and entry["one_line"] and entry["tradeoff"]
