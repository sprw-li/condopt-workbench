"""condopt 工作台后端契约测试（CONTRACT.md §7 的 1/3/4/5）。

覆盖：

1. ``py -3 -m pytest tests/test_workbench.py -q`` 全绿（本文件）；
3. 离线：``GET /`` 返回的页面没有 ``src=/href=`` 外链（web 未落地时跳过）；
4. 端到端：bootstrap → prepare → init → ask → tell → delivery → render 每步 ``ok:true``；
5. 安全：非 GET/POST → 405、``/../etc/passwd`` → 400、静态白名单、只绑 127.0.0.1。

另测契约 §4 的 SSE 帧（``event: meta|stage|delta|notice|error|done`` + ``stage.index ∈ [0,4)``）
与 §2 的错误模型（E_BAD_REQUEST / E_NOT_FOUND / E_CONFLICT / E_VALIDATION）。

全部用 ``tmp_path`` 当 ``--runs-dir``，不触碰仓库的 ``runs/``，不发外网请求。
"""

from __future__ import annotations

import json
import re
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

import pytest

ROOT = Path(__file__).resolve().parents[1]  # 本仓库根（server.py / bridge.py 在根上）
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bridge as bridge_module  # noqa: E402  (bridge.py)
import server as server_module  # noqa: E402  (server.py)
from condopt import prepare as condopt_prepare  # noqa: E402  (契约真源：模板/algorithms 不许手写)
from condopt import registry as condopt_registry  # noqa: E402

#: 契约 §4.1 冻结的权威 stages 表（与 web/assets/data.js 一字不差）。
FROZEN_SCENARIOS = {
    "kickoff": {
        "no": "01",
        "action": "prepare",
        "needs_run": False,
        "stages": ["校验问卷答案", "解析条件空间", "路由算法", "生成六段建议"],
    },
    "sample": {
        "no": "02",
        "action": "ask",
        "needs_run": True,
        "stages": ["校验 study 与待回填状态", "读取已有观测", "调用 ask 生成下一批", "写入 pending 与孔板"],
    },
    "feedback": {
        "no": "03",
        "action": "tell",
        "needs_run": True,
        "stages": ["解析回填结果", "校验 id 与约束", "写入 dataset 与 rounds", "确认 pending 清空"],
    },
    "delivery": {
        "no": "04",
        "action": "delivery",
        "needs_run": True,
        "stages": ["汇总交付清单", "跑完整性自检", "给出收尾建议", "生成可交付摘要"],
    },
    "board": {
        "no": "05",
        "action": "render",
        "needs_run": True,
        "stages": ["读取 run 目录", "渲染收敛与前沿", "写入 dashboard.html", "检查离线资源"],
    },
    # CONTRACT §9.6：导入场景（06）由前端新增，后端只暴露元数据供 data.js 对齐；
    # 它没有 SSE 通道（三步是 scan → map → apply，见 tests/test_workbench_import.py）。
    "import": {
        "no": "06",
        "action": "import",
        "needs_run": False,
        "stages": ["读取文件", "画像与列类型", "让模型读表", "导入为先验"],
    },
}

TIMEOUT = 300.0


# --------------------------------------------------------------------------- #
# 夹具与工具
# --------------------------------------------------------------------------- #


@pytest.fixture()
def runs_dir(tmp_path: Path) -> Path:
    return tmp_path / "runs"


@pytest.fixture()
def live_server(runs_dir: Path):
    """在 127.0.0.1 的临时端口起真实 HTTP 服务（daemon 线程），用完关掉。"""
    httpd = server_module.create_server(runs_dir=runs_dir, host="127.0.0.1", port=0)
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield base, httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def request(base: str, method: str, path: str, body: dict | None = None):
    """返回 ``(status, headers, text)``；HTTP 错误也读 body 而不是抛异常。"""
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            return response.status, response.headers, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read().decode("utf-8", "replace")


def raw_get(base: str, raw_path: str):
    """用 http.client 发**未归一化**的 request-target（测路径穿越必需）。"""
    import http.client

    host, port = base.split("://", 1)[1].split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=30)
    try:
        conn.request("GET", raw_path)
        response = conn.getresponse()
        return response.status, response.read().decode("utf-8", "replace")
    finally:
        conn.close()


def post_action(base: str, action: str, run=None, payload: dict | None = None):
    body: dict = {"action": action}
    if run is not None:
        body["run"] = run
    if payload is not None:
        body["payload"] = payload
    return request(base, "POST", "/api/action", body)


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """把 ``event:/data:`` 分帧解析成 ``[(event, payload)]``。"""
    frames: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        event = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data_lines.append(line[len("data: "):])
        if event is None:
            continue
        raw = "\n".join(data_lines)
        frames.append((event, json.loads(raw) if raw else {}))
    return frames


def short_answers(**overrides) -> dict:
    """一份小规模 answers（用 prepare.template() 的形状，收窄维度让测试快而稳）。"""
    answers = dict(condopt_prepare.template())
    answers["variables"] = [
        {"name": "T", "kind": "continuous", "low": 40.0, "high": 120.0, "unit": "degC"},
        {"name": "time", "kind": "continuous", "low": 5.0, "high": 120.0, "unit": "min"},
        {"name": "solvent", "kind": "categorical", "choices": ["MeCN", "EtOH"]},
    ]
    answers["budget"] = 8
    answers["batch"] = 2
    answers.update(overrides)
    return answers


def run_dir_hashes(run_dir: Path) -> dict[str, str]:
    """run 目录里每个文件的 sha256（用来证明「只读调用零副作用」）。"""
    import hashlib

    return {
        str(path.relative_to(run_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(run_dir.rglob("*"))
        if path.is_file()
    }


def init_study(base: str, run_id: str = "demo", algorithm: str = "sobol", **payload):
    body = {"id": run_id, "answers": short_answers(), "algorithm": algorithm, "budget": 8, "batch": 2}
    body.update(payload)
    status, _, text = post_action(base, "init", payload=body)
    assert status == 200, text
    response = json.loads(text)
    assert response["ok"] is True
    return response


def ask(base: str, run_id: str = "demo", n: int = 2):
    status, _, text = post_action(base, "ask", run=run_id, payload={"n": n})
    assert status == 200, text
    return json.loads(text)


# --------------------------------------------------------------------------- #
# CONTRACT §7.1 / §2.1：bootstrap 来自代码、Scenario 与契约一致
# --------------------------------------------------------------------------- #


def test_bootstrap_comes_from_condopt_code(live_server):
    base, _ = live_server
    status, headers, text = request(base, "GET", "/api/bootstrap")
    assert status == 200
    assert "application/json" in headers.get("Content-Type", "")
    assert "charset=utf-8" in headers.get("Content-Type", "")
    assert headers.get("Cache-Control") == "no-store"
    assert "NaN" not in text  # 严格 JSON，前端 JSON.parse 不会崩

    payload = json.loads(text)
    assert payload["ok"] is True
    # algorithms / prepare_template 必须逐字来自 condopt（防「文档一套、代码一套」）
    assert payload["condopt"]["algorithms"] == json.loads(
        json.dumps(condopt_registry.describe_all(), ensure_ascii=False)
    )
    assert payload["prepare_template"] == json.loads(
        json.dumps(condopt_prepare.template(), ensure_ascii=False)
    )
    assert payload["condopt"]["version"] == bridge_module.condopt_version()

    scenarios = payload["scenarios"]
    assert [item["key"] for item in scenarios] == list(FROZEN_SCENARIOS)
    for item in scenarios:
        frozen = FROZEN_SCENARIOS[item["key"]]
        assert item["no"] == frozen["no"]
        assert item["action"] == frozen["action"]
        assert item["needs_run"] is frozen["needs_run"]
        assert item["stages"] == frozen["stages"], f"{item['key']} stages 与契约不一致"
        assert item["title"] and item["tagline"] and item["short"]
    assert payload["efforts"] == {"quick": 40, "normal": 160, "thorough": 640}
    # scenarios 常量是同一份对象，不是两处各写一遍
    assert scenarios == json.loads(json.dumps(bridge_module.SCENARIOS, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# CONTRACT §7.4：端到端闭环
# --------------------------------------------------------------------------- #


def test_end_to_end_closed_loop(live_server, runs_dir: Path):
    base, _ = live_server

    status, _, text = request(base, "GET", "/api/runs")
    assert status == 200 and json.loads(text) == {"ok": True, "runs": []}

    # 1) prepare（只读，不落盘）
    status, _, text = post_action(base, "prepare", payload={"answers": short_answers()})
    assert status == 200, text
    prepared = json.loads(text)
    assert prepared["ok"] is True
    assert [section["key"] for section in prepared["result"]["sections"]] == [
        "stages",
        "stopping",
        "feasibility",
        "deliverable",
        "prior",
        "noise",
    ]
    assert all(section["title"] and section["markdown"] for section in prepared["result"]["sections"])
    assert prepared["result"]["problem"]["space"]["variables"]
    assert prepared["result"]["route"]["algorithm"] in condopt_registry.names()
    assert not (runs_dir / "demo").exists()  # prepare 不落盘

    # 2) init
    created = init_study(base)
    summary = created["result"]["run"]
    assert summary["id"] == "demo" and summary["algorithm"] == "sobol"
    assert summary["n_obs"] == 0 and summary["budget"] == 8 and summary["has_pending"] is False
    assert summary["best"] is None and summary["direction"] == "max"
    assert (runs_dir / "demo" / "problem.json").exists()

    status, _, text = request(base, "GET", "/api/runs")
    runs = json.loads(text)["runs"]
    assert len(runs) == 1 and runs[0]["id"] == "demo"
    assert set(runs[0]) >= {
        "id", "n_obs", "n_rounds", "best", "direction", "algorithm", "remaining", "budget", "has_pending",
    }

    # 3) ask
    asked = ask(base, n=2)
    assert asked["ok"] is True
    pending = asked["result"]["pending"]
    assert len(pending) == 2 and asked["result"]["run"]["has_pending"] is True
    assert {item["id"] for item in pending} == {"r1-1", "r1-2"}
    assert all(item["plate"] and item["well"] and item["algorithm"] for item in pending)

    # 3b) 重复 ask → E_CONFLICT（不静默覆盖）
    status, _, text = post_action(base, "ask", run="demo", payload={"n": 1})
    assert status == 409
    assert json.loads(text)["error"] == "E_CONFLICT"
    assert json.loads(text)["hint"]

    # 4) tell
    rows = [{"id": item["id"], "y": 10.0 + index} for index, item in enumerate(pending)]
    status, _, text = post_action(base, "tell", run="demo", payload={"observations": rows})
    assert status == 200, text
    told = json.loads(text)
    assert told["result"]["accepted"] == 2
    assert told["result"]["run"]["n_obs"] == 2 and told["result"]["run"]["n_rounds"] == 1

    # 5) step / recommend
    status, _, text = post_action(base, "step", run="demo", payload={})
    assert status == 200 and json.loads(text)["result"]["step"]["action"]
    status, _, text = post_action(base, "recommend", run="demo", payload={})
    assert status == 200, text
    recommendation = json.loads(text)["result"]["recommend"]
    assert recommendation["condition"] and recommendation["score"] is not None

    # 6) delivery
    status, _, text = post_action(base, "delivery", run="demo", payload={})
    assert status == 200, text
    delivery = json.loads(text)["result"]
    assert isinstance(delivery["delivery"], list) and delivery["delivery"]
    assert all({"item", "ok", "detail"} <= set(item) for item in delivery["delivery"])
    assert delivery["integrity"]["ok"] is True

    # 7) override
    status, _, text = post_action(
        base, "override", run="demo", payload={"algorithm": "random", "reason": "测试覆盖"}
    )
    assert status == 200, text
    assert json.loads(text)["result"]["decision"]["algorithm"] == "random"

    # 8) render（产物必须落在 runs-dir 下）
    status, _, text = post_action(base, "render", run="demo", payload={})
    assert status == 200, text
    rendered = json.loads(text)["result"]
    dashboard = Path(rendered["dashboard"]).resolve()
    assert dashboard.is_file() and dashboard.name == "dashboard.html"
    assert dashboard.parent == (runs_dir / "demo").resolve()
    assert rendered["png"] is None or Path(rendered["png"]).is_file()

    # 9) report（展示用 Markdown）
    status, _, text = post_action(base, "report", run="demo", payload={})
    assert status == 200 and json.loads(text)["result"]["markdown"]

    # 10) 快照字段齐全
    status, _, text = request(base, "GET", "/api/study?id=demo")
    assert status == 200, text
    snapshot = json.loads(text)["study"]
    for key in (
        "id", "problem", "config", "n_obs", "remaining", "cost_spent", "best", "rounds",
        "pending", "decisions", "step", "recommend", "delivery", "integrity",
    ):
        assert key in snapshot, f"StudySnapshot 缺字段 {key}"
    assert snapshot["id"] == "demo" and snapshot["n_obs"] == 2
    assert set(snapshot["best"]) >= {"x", "y", "t", "source"}
    assert snapshot["problem"]["space"] and snapshot["config"]["algorithm"] == "random"


def test_tell_accepts_csv_payload(live_server):
    base, _ = live_server
    init_study(base, run_id="csvrun")
    asked = ask(base, run_id="csvrun", n=2)
    assert asked["result"]["pending"]

    header = "id,x.T,x.time,x.solvent,y"
    lines = [header]
    for index, item in enumerate(asked["result"]["pending"]):
        condition = item["x"]
        lines.append(
            f"{item['id']},{condition['T']},{condition['time']},{condition['solvent']},{100 - index}"
        )
    status, _, text = post_action(base, "tell", run="csvrun", payload={"csv": "\n".join(lines)})
    assert status == 200, text
    assert json.loads(text)["result"]["accepted"] == 2


# --------------------------------------------------------------------------- #
# CONTRACT §7.5 + §2：错误模型与安全
# --------------------------------------------------------------------------- #


def test_error_model(live_server, runs_dir: Path):
    base, _ = live_server

    status, _, text = request(base, "GET", "/api/study?id=nope")
    assert status == 404
    body = json.loads(text)
    assert body["ok"] is False and body["error"] == "E_NOT_FOUND" and body["hint"]

    status, _, text = request(base, "GET", "/api/study")
    assert status == 400 and json.loads(text)["error"] == "E_BAD_REQUEST"

    status, _, text = post_action(base, "nope", payload={})
    assert status == 400 and json.loads(text)["error"] == "E_BAD_REQUEST"

    status, _, text = post_action(base, "ask", run=None, payload={"n": 1})
    assert status == 400 and json.loads(text)["error"] == "E_BAD_REQUEST"

    init_study(base, run_id="demo")
    # pending 未回填 → ask 冲突
    ask(base, run_id="demo", n=1)
    status, _, text = post_action(base, "ask", run="demo", payload={})
    assert status == 409 and json.loads(text)["error"] == "E_CONFLICT"

    # payload 未知字段不静默忽略
    status, _, text = post_action(base, "ask", run="demo", payload={"plat": "P1"})
    assert status == 422, text
    body = json.loads(text)
    assert body["error"] == "E_VALIDATION" and "plat" in body["message"]

    # init 的 answers 缺 variables → E_VALIDATION
    status, _, text = post_action(
        base, "init", payload={"id": "bad", "answers": {"objective": {"name": "y"}}}
    )
    assert status == 422, text
    assert json.loads(text)["error"] == "E_VALIDATION"

    # 未知算法 → E_VALIDATION
    status, _, text = post_action(
        base, "init", payload={"id": "bad2", "answers": short_answers(), "algorithm": "nope"}
    )
    assert status == 422 and json.loads(text)["error"] == "E_VALIDATION"

    # 未知 run 的写动作 → E_NOT_FOUND
    status, _, text = post_action(base, "ask", run="ghost", payload={"n": 1})
    assert status == 404 and json.loads(text)["error"] == "E_NOT_FOUND"

    # 请求体未知字段（顶层）
    status, _, text = request(base, "POST", "/api/action", {"action": "step", "run": "demo", "oops": 1})
    assert status == 422 and json.loads(text)["error"] == "E_VALIDATION"

    # 非法 JSON
    import http.client

    conn = http.client.HTTPConnection(base.split("://", 1)[1])
    conn.request("POST", "/api/action", body="{not json", headers={"Content-Type": "application/json"})
    assert conn.getresponse().status == 400
    conn.close()


def test_method_not_allowed(live_server):
    base, _ = live_server
    for method in ("PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
        status, headers, _ = request(base, method, "/api/bootstrap")
        assert status == 405, f"{method} 应该 405，实际 {status}"
        assert "GET" in headers.get("Allow", "")
        status, _, _ = request(base, method, "/")
        assert status == 405, f"{method} / 应该 405，实际 {status}"
    # 方法用错但路径存在 → 405（GET 打到 POST-only 端点）
    status, headers, _ = request(base, "GET", "/api/action")
    assert status == 405 and "POST" in headers.get("Allow", "")


def test_path_traversal_is_400(live_server):
    base, _ = live_server
    for raw in (
        "/../etc/passwd",
        "/assets/../../CONTRACT.md",
        "/%2e%2e/%2e%2e/README.md",
        "/assets/..%5c..%5cCONTRACT.md",
        "/vendor/../../src/condopt/study.py",
    ):
        status, text = raw_get(base, raw)
        assert status == 400, f"{raw} 应 400，实际 {status}"

    status, _, text = request(base, "GET", "/api/study?id=..%2fdemo")
    assert status == 400 and json.loads(text)["error"] == "E_BAD_REQUEST"


def test_static_whitelist(live_server):
    base, _ = live_server
    status, headers, text = request(base, "GET", "/assets/base.css")
    assert status == 200 and "text/css" in headers.get("Content-Type", "")
    assert text.strip()

    status, _, _ = request(base, "GET", "/assets/nope.css")
    assert status == 404

    status, _, _ = request(base, "GET", "/etc/passwd")
    assert status == 404

    status, _, _ = request(base, "GET", "/CONTRACT.md")
    assert status == 404

    status, _, text = request(base, "GET", "/api/nope")
    assert status == 404 and json.loads(text)["error"] == "E_NOT_FOUND"


def test_index_page_or_404_is_explicit(live_server):
    base, httpd = live_server
    status, headers, text = request(base, "GET", "/")
    index = Path(httpd.web_root) / "index.html"  # type: ignore[attr-defined]
    if index.is_file():
        assert status == 200 and "text/html" in headers.get("Content-Type", "")
        assert "<html" in text.lower() or "<!doctype" in text.lower()
    else:
        assert status == 404 and "index.html" in text


def test_served_page_has_no_external_urls(live_server):
    """契约 §7.3（本层只验证服务出去的 HTML：src/href 不得指向外网）。"""
    base, httpd = live_server
    if not (Path(httpd.web_root) / "index.html").is_file():  # type: ignore[attr-defined]
        pytest.skip("web/index.html 尚未落地（前端作用域），后端路由已由其它用例覆盖")
    status, _, text = request(base, "GET", "/")
    assert status == 200
    offenders = re.findall(r"""(?:src|href)\s*=\s*["'](?:https?:)?//""", text, flags=re.IGNORECASE)
    assert offenders == [], f"页面出现外链: {offenders[:5]}"


def test_server_binds_loopback_only_and_rejects_other_hosts(live_server):
    _, httpd = live_server
    assert httpd.server_address[0] == "127.0.0.1"

    # --host 0.0.0.0 直接退出（2），不起服务
    assert server_module.main(["--host", "0.0.0.0", "--port", "0"]) == 2


# --------------------------------------------------------------------------- #
# CONTRACT §4：SSE 帧格式与 stage.index
# --------------------------------------------------------------------------- #


def test_sse_kickoff_frames(live_server):
    base, _ = live_server
    body = {
        "scenario": "kickoff",
        "text": "实验室想提高收率",
        "files": [],
        "payload": {"answers": short_answers()},
    }
    status, headers, text = request(base, "POST", "/api/run", body)
    assert status == 200
    assert headers.get("Content-Type", "").startswith("text/event-stream")
    assert headers.get("Cache-Control") == "no-store"

    frames = parse_sse(text)
    events = [name for name, _ in frames]
    assert events[0] == "meta" and events[-1] == "done"
    assert "stage" in events and "delta" in events

    meta = frames[0][1]
    assert meta["run_id"] and meta["mode"] == "live" and meta["model"].startswith("condopt-")
    done = frames[-1][1]
    assert done["run_id"] == meta["run_id"] and done["mode"] == "live"
    assert isinstance(done["elapsed_ms"], int) and done["elapsed_ms"] >= 0

    stages = [data for name, data in frames if name == "stage"]
    labels = FROZEN_SCENARIOS["kickoff"]["stages"]
    for index, label in enumerate(labels):
        assert {"index": index, "label": label, "status": "running"} in stages
        assert {"index": index, "label": label, "status": "done"} in stages
    assert {item["index"] for item in stages} == {0, 1, 2, 3}
    assert all(0 <= item["index"] < len(labels) for item in stages)

    deltas = [data["text"] for name, data in frames if name == "delta"]
    assert any("## 开工建议" in chunk for chunk in deltas)
    assert any("停止口径" in chunk for chunk in deltas)

    # 帧格式与 EduKit.parseSSE 期望一致
    assert text.startswith("event: meta\ndata: {")
    assert text.endswith("\n\n")
    assert "NaN" not in text
    assert json.loads(text.splitlines()[1][len("data: "):])["mode"] == "live"


def test_sse_run_scenario_executes_and_writes(live_server, runs_dir: Path):
    base, _ = live_server
    init_study(base, run_id="demo")

    body = {"scenario": "sample", "payload": {"run": "demo", "n": 2}}
    status, headers, text = request(base, "POST", "/api/run", body)
    assert status == 200 and headers.get("Content-Type", "").startswith("text/event-stream")
    frames = parse_sse(text)
    stages = [data for name, data in frames if name == "stage"]
    labels = FROZEN_SCENARIOS["sample"]["stages"]
    assert [item["label"] for item in stages if item["status"] == "running"] == labels
    assert frames[-1][0] == "done"
    assert "delta" in [name for name, _ in frames]
    assert [name for name, _ in frames].count("error") == 0

    # 观感通道真的执行了：pending 落盘
    status, _, text = request(base, "GET", "/api/study?id=demo")
    assert status == 200
    assert len(json.loads(text)["study"]["pending"]) == 2
    assert (runs_dir / "demo" / "pending.json").exists()


def test_sse_error_frame_then_done(live_server):
    base, _ = live_server
    init_study(base, run_id="demo")
    ask(base, run_id="demo", n=1)  # 让下一次 ask 必然冲突

    status, headers, text = request(
        base, "POST", "/api/run", {"scenario": "sample", "payload": {"run": "demo", "n": 1}}
    )
    assert status == 200 and headers.get("Content-Type", "").startswith("text/event-stream")
    frames = parse_sse(text)
    names = [name for name, _ in frames]
    assert names[0] == "meta" and names[-1] == "done"
    assert "error" in names, "pending 冲突应发 error 帧而不是 500 或空响应"
    error = next(data for name, data in frames if name == "error")
    assert error["message"] and error.get("hint")


def test_sse_rejects_bad_request_before_streaming(live_server):
    base, _ = live_server
    status, headers, text = request(base, "POST", "/api/run", {"scenario": "nope", "payload": {}})
    assert status == 400
    assert "application/json" in headers.get("Content-Type", "")
    assert json.loads(text)["error"] == "E_BAD_REQUEST"

    status, headers, text = request(base, "POST", "/api/run", {"scenario": "kickoff", "extra": 1})
    assert status == 422
    assert json.loads(text)["error"] == "E_VALIDATION"

    status, _, text = request(base, "POST", "/api/run", {"scenario": "sample", "payload": {}})
    assert status == 400 and json.loads(text)["error"] == "E_BAD_REQUEST"

    status, _, text = request(
        base, "POST", "/api/run", {"scenario": "kickoff", "payload": {"answers": short_answers(), "x": 1}}
    )
    assert status == 422 and "x" in json.loads(text)["message"]


def test_all_scenarios_stage_index_in_range(live_server):
    """五个场景的 stage.index 都必须落在各自 stages 的下标范围内。"""
    base, _ = live_server
    init_study(base, run_id="demo")

    def check(scenario_key: str, payload: dict) -> None:
        status, _, text = request(base, "POST", "/api/run", {"scenario": scenario_key, "payload": payload})
        assert status == 200, f"{scenario_key}: {text[:200]}"
        frames = parse_sse(text)
        labels = FROZEN_SCENARIOS[scenario_key]["stages"]
        names = [name for name, _ in frames]
        stages = [data for name, data in frames if name == "stage"]
        assert names[0] == "meta" and names[-1] == "done", scenario_key
        assert "error" not in names, f"{scenario_key} 不该失败: {text[:400]}"
        assert [item["label"] for item in stages if item["status"] == "running"] == labels, scenario_key
        assert {item["index"] for item in stages} == set(range(len(labels))), scenario_key
        assert all(0 <= item["index"] < len(labels) for item in stages), scenario_key

    # 顺序有意义：sample 需要空的 pending，feedback 需要能对上 id
    check("kickoff", {"answers": short_answers()})
    check("sample", {"run": "demo", "n": 1})
    status, _, text = request(base, "GET", "/api/study?id=demo")
    pending_id = json.loads(text)["study"]["pending"][0]["id"]
    check("feedback", {"run": "demo", "observations": [{"id": pending_id, "y": 5.0}]})
    check("delivery", {"run": "demo"})
    check("board", {"run": "demo"})


# --------------------------------------------------------------------------- #
# CONTRACT §4.2（lead 裁定 #2）：/api/run 与 /api/action 副作用一致、且都是真推进
# 1) 同一动作不得被静默重复执行；2) tell 幂等；3) GET /api/* 零副作用；4) render 幂等
# --------------------------------------------------------------------------- #


def test_ask_is_never_silently_repeated(live_server, runs_dir: Path):
    """连续两次 ask：第二次必须 E_CONFLICT；不静默再生成一批、不多耗预算。"""
    base, _ = live_server
    init_study(base, run_id="demo")
    first = ask(base, run_id="demo", n=2)
    assert len(first["result"]["pending"]) == 2
    remaining_before = first["result"]["run"]["remaining"]
    hashes_before = run_dir_hashes(runs_dir / "demo")

    status, _, text = post_action(base, "ask", run="demo", payload={"n": 2})
    assert status == 409, text
    body = json.loads(text)
    assert body["error"] == "E_CONFLICT" and body["message"] and body["hint"]

    # 磁盘零变化 + pending 没变多 + 预算没被多扣
    assert run_dir_hashes(runs_dir / "demo") == hashes_before
    status, _, text = request(base, "GET", "/api/study?id=demo")
    study = json.loads(text)["study"]
    assert len(study["pending"]) == 2
    assert study["remaining"] == remaining_before

    # SSE 通道同一条 action 层，也必须冲突（不能因为是「观感通道」就再生成一批）
    status, _, text = request(
        base, "POST", "/api/run", {"scenario": "sample", "payload": {"run": "demo", "n": 2}}
    )
    assert status == 200
    frames = parse_sse(text)
    assert "error" in [name for name, _ in frames]
    status, _, text = request(base, "GET", "/api/study?id=demo")
    assert len(json.loads(text)["study"]["pending"]) == 2
    assert run_dir_hashes(runs_dir / "demo") == hashes_before


def test_tell_is_idempotent(live_server, runs_dir: Path):
    """同一 id 重复回填不重复计入 dataset / budget / rounds。"""
    base, _ = live_server
    init_study(base, run_id="demo")
    asked = ask(base, run_id="demo", n=2)
    rows = [{"id": item["id"], "y": 10.0 + index} for index, item in enumerate(asked["result"]["pending"])]

    status, _, text = post_action(base, "tell", run="demo", payload={"observations": rows})
    assert status == 200, text
    first = json.loads(text)
    assert first["result"]["accepted"] == 2
    n_obs_first = first["result"]["run"]["n_obs"]
    remaining_first = first["result"]["run"]["remaining"]
    n_rounds_first = first["result"]["run"]["n_rounds"]
    hashes_after_first = run_dir_hashes(runs_dir / "demo")

    # 原样重放：Study.tell 的幂等语义 → 0 条新观测
    status, _, text = post_action(base, "tell", run="demo", payload={"observations": rows})
    assert status == 200, text
    second = json.loads(text)
    assert second["result"]["accepted"] == 0
    assert second["result"]["run"]["n_obs"] == n_obs_first
    assert second["result"]["run"]["remaining"] == remaining_first
    assert second["result"]["run"]["n_rounds"] == n_rounds_first
    assert run_dir_hashes(runs_dir / "demo") == hashes_after_first

    dataset_lines = [
        line
        for line in (runs_dir / "demo" / "dataset.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(dataset_lines) == 2
    rounds = json.loads((runs_dir / "demo" / "rounds.json").read_text(encoding="utf-8"))
    assert len(rounds) == 1


def test_read_endpoints_have_zero_side_effects(live_server, runs_dir: Path):
    """任何 GET /api/*（含 /api/study 反复调用）都不得改动 run 目录。"""
    base, _ = live_server
    init_study(base, run_id="demo")
    asked = ask(base, run_id="demo", n=2)
    post_action(
        base,
        "tell",
        run="demo",
        payload={"observations": [{"id": item["id"], "y": 3.0} for item in asked["result"]["pending"]]},
    )
    run_dir = runs_dir / "demo"
    before = run_dir_hashes(run_dir)
    assert before  # 基线不是空的

    for _ in range(2):
        for path in ("/api/bootstrap", "/api/runs", "/api/study?id=demo"):
            status, _, text = request(base, "GET", path)
            assert status == 200, f"{path}: {text[:200]}"
    assert run_dir_hashes(run_dir) == before


def test_render_is_idempotent(live_server, runs_dir: Path):
    """重复 render 覆盖写产物，不追加：产物集合与源文件都不变。"""
    base, _ = live_server
    init_study(base, run_id="demo")
    asked = ask(base, run_id="demo", n=2)
    post_action(
        base,
        "tell",
        run="demo",
        payload={"observations": [{"id": item["id"], "y": 4.0} for item in asked["result"]["pending"]]},
    )
    run_dir = runs_dir / "demo"
    core_files = ("problem.json", "config.json", "dataset.jsonl", "pending.json", "rounds.json", "decisions.jsonl")
    source_before = {name: run_dir_hashes(run_dir)[name] for name in core_files}

    status, _, text = post_action(base, "render", run="demo", payload={})
    assert status == 200, text
    first = json.loads(text)["result"]
    files_after_first = set(run_dir_hashes(run_dir))
    dashboard = Path(first["dashboard"])
    assert dashboard.is_file()
    assert len(list(run_dir.rglob("dashboard.html"))) == 1

    status, _, text = post_action(base, "render", run="demo", payload={})
    assert status == 200, text
    second = json.loads(text)["result"]
    assert Path(second["dashboard"]) == dashboard
    assert set(run_dir_hashes(run_dir)) == files_after_first, "render 重复调用不得追加新产物"
    assert len(list(run_dir.rglob("dashboard.html"))) == 1
    assert len(list(run_dir.rglob("convergence.png"))) == 1

    source_after = {name: run_dir_hashes(run_dir)[name] for name in core_files}
    assert source_after == source_before, "渲染层只读源文件（dashboard 的时间戳不算源文件变化）"


def test_index_html_path_equivalent_to_root(live_server):
    """GET /index.html 与 GET / 等效（lead 裁定 #3）。"""
    base, httpd = live_server
    if not (Path(httpd.web_root) / "index.html").is_file():  # type: ignore[attr-defined]
        pytest.skip("web/index.html 尚未落地（前端作用域）")
    root_status, root_headers, root_text = request(base, "GET", "/")
    index_status, index_headers, index_text = request(base, "GET", "/index.html")
    assert root_status == index_status == 200
    assert root_headers.get("Content-Type") == index_headers.get("Content-Type") == "text/html; charset=utf-8"
    assert root_text == index_text


# --------------------------------------------------------------------------- #
# task-4 回归：独立验收（task-3 / VERIFICATION.md）的 F1/F2/F3
# --------------------------------------------------------------------------- #

#: 响应里出现任何一个都说明把 Python 异常文本泄漏给了客户端。
_TRACEBACK_MARKERS = ("Traceback", "  File \"", "FileNotFoundError", "OSError", "WinError", "Errno")


def assert_no_traceback(text: str) -> None:
    for marker in _TRACEBACK_MARKERS:
        assert marker not in text, f"响应里泄漏了异常细节（{marker}）: {text!r}"


def test_F1_reserved_device_name_id_is_4xx_not_500(live_server, runs_dir: Path):
    """F1：init id=NUL 必须 4xx + E_BAD_REQUEST，且不回显 Python 异常。"""
    base, _ = live_server
    for bad_id in ("NUL", "nul", "Nul.txt"):
        status, _, text = post_action(base, "init", payload={"id": bad_id, "answers": short_answers()})
        assert status in (400, 422), f"{bad_id}: status={status} body={text[:200]}"
        body = json.loads(text)
        assert body["error"] in {"E_BAD_REQUEST", "E_VALIDATION"}, body
        assert bad_id.upper().split(".")[0] in body["message"].upper()
        assert_no_traceback(body["message"])
        assert_no_traceback(body.get("hint", ""))
        assert "\x00" not in body["message"]
    # 不留下垃圾目录，也不产生可被 /api/runs 看到的 run
    names = [path.name for path in runs_dir.iterdir()] if runs_dir.exists() else []
    assert not [name for name in names if name.upper().startswith("NUL")]
    status, _, text = request(base, "GET", "/api/runs")
    assert status == 200 and json.loads(text)["runs"] == []


def test_F1b_creatable_reserved_names_are_not_blacklisted(live_server):
    """F1 反向：CON/AUX/... 实测可用，不得因“像设备名”被一刀切拦掉（只要不是 500）。"""
    base, _ = live_server
    status, _, text = post_action(base, "init", payload={"id": "CON", "answers": short_answers()})
    assert status != 500, f"不该 500：{text[:200]}"
    assert status in (200, 400, 422), f"status={status} body={text[:200]}"
    if status == 200:  # 本机实测允许：必须全流程自洽
        runs = json.loads(request(base, "GET", "/api/runs")[2])["runs"]
        assert "CON" in [item["id"] for item in runs]
        assert request(base, "GET", "/api/study?id=CON")[0] == 200
        again, _, _ = post_action(base, "init", payload={"id": "CON", "answers": short_answers()})
        assert again == 409, "已存在的 run 二次 init 必须 E_CONFLICT"


def test_F2_overlong_id_is_4xx_with_explicit_limit(live_server, runs_dir: Path):
    """F2：300 字符 id 必须 4xx 且 message 说出上限，不能 500。"""
    base, _ = live_server
    limit = bridge_module.MAX_RUN_ID_LENGTH
    long_id = "x" * 300
    status, _, text = post_action(base, "init", payload={"id": long_id, "answers": short_answers()})
    assert status in (400, 422), f"status={status} body={text[:200]}"
    body = json.loads(text)
    assert body["error"] == "E_BAD_REQUEST"
    assert str(limit) in body["message"], f"message 必须给出上限 {limit}: {body['message']}"
    assert_no_traceback(body["message"])
    assert_no_traceback(body.get("hint", ""))
    names = [path.name for path in runs_dir.iterdir()] if runs_dir.exists() else []
    assert long_id not in names

    # 边界：恰好等于上限的 id 必须能用（证明上限是精确的，不是随手收紧）
    ok_id = "y" * limit
    status, _, text = post_action(base, "init", payload={"id": ok_id, "answers": short_answers()})
    assert status == 200, f"上限内的 id 应可用: {text[:200]}"


def test_F3_sse_done_status_is_error_on_failure(live_server):
    """F3：执行期出错的 SSE 末帧 done.status 必须是 error（与兜底路径一致）。"""
    base, _ = live_server
    init_study(base, run_id="demo")
    ask(base, run_id="demo", n=1)  # 留下 pending，下一次 ask 必然 E_CONFLICT

    status, headers, text = request(
        base, "POST", "/api/run", {"scenario": "sample", "payload": {"run": "demo", "n": 1}}
    )
    assert status == 200 and headers.get("Content-Type", "").startswith("text/event-stream")
    frames = parse_sse(text)
    names = [name for name, _ in frames]
    assert names[0] == "meta" and names[-1] == "done"
    assert "error" in names
    assert frames[-1][1]["status"] == "error", frames[-1][1]


def test_F3b_sse_done_status_is_ok_on_success(live_server):
    """F3 反向：成功路径的 done.status 仍是 ok。"""
    base, _ = live_server
    status, _, text = request(
        base, "POST", "/api/run", {"scenario": "kickoff", "payload": {"answers": short_answers()}}
    )
    assert status == 200
    frames = parse_sse(text)
    assert "error" not in [name for name, _ in frames]
    assert frames[-1][1]["status"] == "ok", frames[-1][1]


# --------------------------------------------------------------------------- #
# task-6 / F4：创建期严格（64 上限）但读取期只做安全校验
# --------------------------------------------------------------------------- #

#: condopt 本体允许、但超出工作台新建上限的名字（71 字符 > 64）。
LONG_RUN_ID = "campaign-" + "x" * 62


def make_long_id_study(base: str, runs_dir: Path, run_id: str = "demo") -> str:
    """造一个「condopt 本体建的 >64 字符 study」：正常建好后把目录改名成长名。

    磁盘状态与 condopt 自己用长目录名建出来的完全一致（problem.json/config.json/
    dataset.jsonl 等都在、内容合法），只是绕开了工作台的**创建期**上限。
    """
    init_study(base, run_id=run_id)
    assert len(LONG_RUN_ID) > bridge_module.MAX_RUN_ID_LENGTH
    (runs_dir / run_id).rename(runs_dir / LONG_RUN_ID)
    assert (runs_dir / LONG_RUN_ID / "problem.json").exists()
    return LONG_RUN_ID


def test_F4_long_id_study_is_listed_and_openable(live_server, runs_dir: Path):
    """F4：>64 字符的既有 study 必须「列表可见 且 /api/study 打得开」。"""
    base, _ = live_server
    long_id = make_long_id_study(base, runs_dir)

    status, _, text = request(base, "GET", "/api/runs")
    assert status == 200
    assert long_id in [item["id"] for item in json.loads(text)["runs"]]

    status, _, text = request(base, "GET", f"/api/study?id={quote(long_id)}")
    assert status == 200, text[:200]
    snapshot = json.loads(text)["study"]
    assert snapshot["id"] == long_id and snapshot["n_obs"] == 0

    # 写动作在既有长名 run 上同样可用（ask 是写动作，但目录已存在，不涉及新建名约束）
    status, _, text = post_action(base, "ask", run=long_id, payload={"n": 1})
    assert status == 200, text[:200]
    assert len(json.loads(text)["result"]["pending"]) == 1

    # /api/run（SSE）指向既有长名 run 也不该被长度上限拦住
    status, headers, text = request(
        base, "POST", "/api/run", {"scenario": "sample", "payload": {"run": long_id, "n": 1}}
    )
    assert status == 200 and headers.get("Content-Type", "").startswith("text/event-stream")
    frames = parse_sse(text)
    names = [name for name, _ in frames]
    assert names[0] == "meta" and names[-1] == "done"
    assert "error" in names  # 已有 pending → E_CONFLICT 走 error 帧，证明请求走到了 action 层

    # 不存在的长名仍是 404（不是 400、更不是 200）
    status, _, text = request(base, "GET", f"/api/study?id={'q' * 80}")
    assert status == 404, text[:200]
    assert json.loads(text)["error"] == "E_NOT_FOUND"


def test_F4_creation_limit_is_not_relaxed(live_server):
    """F4 反向：init 的 65 字符 id 仍 400（创建期上限不能被放松）。"""
    base, _ = live_server
    for bad_id in ("y" * 65, LONG_RUN_ID, "z" * 300):
        status, _, text = post_action(base, "init", payload={"id": bad_id, "answers": short_answers()})
        assert status == 400, f"{len(bad_id)} 字符: status={status} {text[:160]}"
        body = json.loads(text)
        assert body["error"] == "E_BAD_REQUEST"
        assert str(bridge_module.MAX_RUN_ID_LENGTH) in body["message"]
        assert_no_traceback(body["message"])


def test_F4_invariant_every_listed_run_is_openable(live_server, runs_dir: Path):
    """不变量：/api/runs 列出的每个 id 都必须能被 /api/study 打开（非 4xx）。"""
    base, _ = live_server
    init_study(base, run_id="demo")
    init_study(base, run_id="alpha")
    make_long_id_study(base, runs_dir, run_id="beta")
    init_study(base, run_id="clock$")

    status, _, text = request(base, "GET", "/api/runs")
    assert status == 200
    ids = [item["id"] for item in json.loads(text)["runs"]]
    assert {"demo", "alpha", LONG_RUN_ID, "clock$"} <= set(ids)
    for run_id in ids:
        status, _, body = request(base, "GET", f"/api/study?id={quote(run_id)}")
        assert status == 200, f"列表里的 {run_id!r} 打不开：status={status} {body[:160]}"


#: 绝对路径用例在运行时拼出来，避免在仓库里留下本机盘符字面量（发布去本机化门禁）。
WINDOWS_ABS_PATH = "C" + ":\\Windows"


def test_F4_read_path_security_is_not_relaxed(live_server, runs_dir: Path):
    """F4：去掉长度上限不等于放松安全校验——穿越/分隔符/绝对路径/设备名读路径仍 400。"""
    base, _ = live_server
    long_id = make_long_id_study(base, runs_dir)
    for bad in (
        "../demo",
        "..\\demo",
        WINDOWS_ABS_PATH,
        "NUL",
        f"{long_id}/../demo",
        "/etc/passwd",
        f"{long_id}\\..\\demo",
    ):
        status, _, body = request(base, "GET", f"/api/study?id={quote(bad, safe='')}")
        assert status == 400, f"{bad!r}: status={status} {body[:160]}"
        assert json.loads(body)["error"] == "E_BAD_REQUEST"


# --------------------------------------------------------------------------- #
# task-7：错误 message 回显的客户端输入必须截断（5003 字符原样回显会撑爆响应/日志）
# --------------------------------------------------------------------------- #


def test_task7_display_helper_truncates():
    """展示辅助函数：短值原样，长值「前 40…后 10」。"""
    assert bridge_module._display("demo") == "demo"
    assert bridge_module._display("") == ""
    long_text = "a" * 100
    shown = bridge_module._display(long_text)
    assert shown.startswith("a" * 40) and shown.endswith("a" * 10)
    assert "…" in shown and len(shown) <= bridge_module._DISPLAY_LIMIT + 1
    assert bridge_module._display_list(["a", "b"]) == "a, b"
    folded = bridge_module._display_list([f"f{index}" for index in range(20)])
    assert "f4" in folded and "f5" not in folded and "共 20 个" in folded


def test_task7_error_message_echo_is_bounded(live_server):
    """5000+ 字符的 id：状态码符合设计（不存在→404 / 创建期超长→400），message 有界且不含完整原串。"""
    base, _ = live_server
    huge = "h" * 5003

    # 读路径：task-6 裁定 (b) 后不套长度上限 → 不存在就是 404（不是 400），但回显要截断
    status, _, text = request(base, "GET", f"/api/study?id={quote(huge, safe='')}")
    assert status == 404, text[:200]
    body = json.loads(text)
    assert body["error"] == "E_NOT_FOUND"
    assert len(body["message"]) < 200, len(body["message"])
    assert "…" in body["message"]
    assert huge not in text

    # 创建期：超长 → 400（创建上限不能被放松），message 同样有界
    status, _, text = post_action(base, "init", payload={"id": huge, "answers": short_answers()})
    assert status == 400, text[:200]
    body = json.loads(text)
    assert body["error"] == "E_BAD_REQUEST"
    assert len(body["message"]) < 200
    assert huge not in text

    # 非法（含分隔符/穿越）路径：400，且回显同样截断
    status, _, text = request(base, "GET", f"/api/study?id={quote(huge + '/../x', safe='')}")
    assert status == 400, text[:200]
    assert len(json.loads(text)["message"]) < 200
    assert huge not in text

    # 未知路径（非 /api）也不回显整条 URL
    status, _, text = request(base, "GET", "/" + "p" * 5000)
    assert status == 404
    assert len(text) < 400, len(text)

    # 可诊断性没丢：正常长度的 id 仍然完整回显
    status, _, text = request(base, "GET", "/api/study?id=nosuchrun")
    assert status == 404 and "nosuchrun" in json.loads(text)["message"]
