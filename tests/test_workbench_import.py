"""导入已有数据（CONTRACT §9）的后端契约测试。

覆盖 §9.7：
1. ``scan``：CSV/TSV/JSON/JSONL 画像 + 路径安全（``..``/根外/目录/后缀/超限 → 400）；
2. ``map``：未开 ``--import-llm`` → ``E_LLM_UNAVAILABLE``；无 consent → ``E_CONSENT_REQUIRED``；
   ``scan_id`` 不匹配 → 400；模型非 JSON → ``E_LLM_BAD_RESPONSE``（含一次格式修复重试）；
   **用本机 stdlib stub 网关跑成功路径**，断言只发「表头 + 画像 + 前 N 行样本」、密钥不泄漏；
3. ``apply``：先验导入计数/去重/拒绝原因、``budget`` 校验、``import_audit.jsonl``、
   ``params_schema`` 与 ``registry.describe_all()`` 逐字段一致；
4. 端到端 scan → map(stub) → apply → ``ask``；
5. 默认配置（不开 ``--import-llm``）下不出网。

全部离线：唯一的外部 HTTP 是本机 127.0.0.1 的 stub 网关。
"""

from __future__ import annotations

import contextlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]  # 本仓库根（server.py / bridge.py 在根上）
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bridge as bridge_module  # noqa: E402
import server as server_module  # noqa: E402
from condopt import registry as condopt_registry  # noqa: E402

TEST_KEY = "sk-test-secret-should-never-leak-1234567890"
TIMEOUT = 300.0

VALID_MAPPING = {
    "variables": [
        {"column": "T", "name": "T", "kind": "continuous", "low": 20.0, "high": 200.0, "unit": "degC"},
        {"column": "time", "name": "time", "kind": "continuous", "low": 1.0, "high": 60.0},
        {"column": "solvent", "name": "solvent", "kind": "categorical", "choices": ["MeCN", "EtOH"]},
    ],
    "objective": {"column": "yield", "name": "yield", "direction": "max"},
    "constraints": [],
    "fidelity": None,
    "cost": None,
}


# --------------------------------------------------------------------------- #
# 工具：本机 stub 网关 / HTTP 客户端 / server 生命周期
# --------------------------------------------------------------------------- #


class StubGateway:
    """stdlib stub：实现 OpenAI 兼容 ``POST {base}/chat/completions`` 并记录收到的请求。"""

    def __init__(self, responder=None):
        self.requests: list[dict] = []
        self.responder = responder or (lambda index, body: (
            json.dumps({"mapping": VALID_MAPPING, "confidence": 0.9, "rationale": "stub"}), 200
        ))
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # noqa: A003 - 静音
                return

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8", "replace")
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    body = {"__unparsed__": raw}
                gateway.requests.append(
                    {
                        "path": self.path,
                        "body": body,
                        "raw": raw,
                        "authorization": self.headers.get("Authorization"),
                        "headers": dict(self.headers),
                    }
                )
                index = len(gateway.requests) - 1
                content, status = gateway.responder(index, body)
                payload = json.dumps(
                    {"choices": [{"message": {"role": "assistant", "content": content}}]}
                ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):  # noqa: N802
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05},
                                       daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def sent_text(self) -> str:
        """把所有请求体拼成一个字符串，便于断言「没发整文件」。"""
        return "\n".join(item["raw"] for item in self.requests)


@contextlib.contextmanager
def stub_gateway(responder=None):
    gateway = StubGateway(responder)
    try:
        yield gateway
    finally:
        gateway.close()


@contextlib.contextmanager
def running_server(
    runs_dir: Path,
    *,
    import_llm: bool = False,
    base_url: str | None = None,
    api_key_env: str = "TEST_KEY",
    allow_roots=None,
    max_import_bytes: int | None = None,
):
    instance = bridge_module.Bridge(
        runs_dir,
        import_llm=import_llm,
        llm_base_url=base_url,
        llm_api_key_env=api_key_env,
        allow_import_roots=allow_roots,
        max_import_bytes=max_import_bytes,
    )
    httpd = server_module.create_server(bridge=instance, host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def request(base: str, method: str, path: str, body: dict | None = None):
    import urllib.error
    import urllib.request

    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            return response.status, response.headers, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read().decode("utf-8", "replace")


def api(base: str, path: str, body: dict):
    status, _, text = request(base, "POST", path, body)
    return status, json.loads(text), text


def write_table(path: Path, rows: int = 30, duplicate: bool = True, bad: bool = True) -> Path:
    """造一张宽表：T/time/solvent/yield/purity/batch，含重复行与一个坏行。"""
    lines = ["T,time,solvent,yield,purity,batch"]
    for index in range(rows):
        lines.append(
            f"{40 + index * 2},{(index % 20) + 1},{'MeCN' if index % 2 else 'EtOH'},"
            f"{50 + index * 0.5},{95 + index % 3},P1"
        )
    if duplicate:
        lines.append(lines[1])
    if bad:
        lines.append("n/a,3,MeCN,70,97,P1")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_long_table(path: Path, rows: int = 200, marker: str = "SENTINEL-ROW-150") -> Path:
    """长表：样本窗口之外埋一个唯一标记，用来证明「没发整文件」。"""
    lines = ["T,time,solvent,yield,purity,batch"]
    for index in range(rows):
        cell = marker if index == 150 else f"{40 + index * 0.5}"
        lines.append(f"{cell},{(index % 20) + 1},{'MeCN' if index % 2 else 'EtOH'},{50 + index * 0.1},96,P1")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# §9.3 scan
# --------------------------------------------------------------------------- #


def test_scan_csv_profiles_match_real_statistics(tmp_path):
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv", rows=30)
    with running_server(runs, allow_roots=[tmp_path]) as base:
        status, payload, _ = api(base, "/api/import/scan", {"path": str(data)})
    assert status == 200, payload
    assert payload["ok"] is True
    assert payload["n_rows"] == 32  # 30 + 1 重复 + 1 坏行
    assert payload["file"]["encoding"] == "utf-8"
    assert payload["file"]["delimiter"] == ","
    assert payload["file"]["has_header"] is True
    columns = {item["name"]: item for item in payload["columns"]}
    assert set(columns) == {"T", "time", "solvent", "yield", "purity", "batch"}
    assert columns["T"]["inferred"] == "integer"
    assert columns["T"]["min"] == 40.0 and columns["T"]["max"] == 98.0
    assert columns["T"]["non_numeric"] == 1  # 'n/a' 行如实统计
    assert columns["solvent"]["inferred"] == "text"
    assert set(columns["solvent"]["choices"]) == {"MeCN", "EtOH"}
    assert columns["solvent"]["missing"] == 0
    assert all(item["samples"] for item in payload["columns"])
    mapping = payload["rule_mapping"]
    assert mapping["objective"]["column"] == "yield"
    assert {item["column"] for item in mapping["variables"]} >= {"T", "solvent"}
    assert any("batch" in item for item in payload["warnings"])
    assert payload["scan_id"] and len(payload["scan_id"]) == 16


def test_scan_tsv_json_jsonl(tmp_path):
    runs = tmp_path / "runs"
    tsv = tmp_path / "lab.tsv"
    tsv.write_text("T\tyield\n40\t50\n80\t70\n", encoding="utf-8")
    jsonl = tmp_path / "obs.jsonl"
    jsonl.write_text(
        "\n".join(json.dumps({"x": {"T": 40 + index}, "y": 50 + index}) for index in range(5)),
        encoding="utf-8",
    )
    bundle = tmp_path / "obs.json"
    bundle.write_text(json.dumps([{"x": {"T": 40}, "y": 50}, {"x": {"T": 60}, "y": 70}]), encoding="utf-8")
    with running_server(runs, allow_roots=[tmp_path]) as base:
        status, tsv_payload, _ = api(base, "/api/import/scan", {"path": str(tsv)})
        assert status == 200
        assert tsv_payload["file"]["delimiter"] == "\t"
        assert {item["name"] for item in tsv_payload["columns"]} == {"T", "yield"}

        status, jsonl_payload, _ = api(base, "/api/import/scan", {"path": str(jsonl)})
        assert status == 200
        assert jsonl_payload["n_rows"] == 5
        assert {item["name"] for item in jsonl_payload["columns"]} == {"x.T", "y"}
        assert jsonl_payload["rule_mapping"]["objective"]["column"] == "y"

        status, json_payload, _ = api(base, "/api/import/scan", {"path": str(bundle)})
        assert status == 200
        assert json_payload["n_rows"] == 2


def test_scan_path_security(tmp_path):
    runs = tmp_path / "runs"
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    inside = write_table(allowed / "ok.csv", rows=3)
    outside = write_table(tmp_path / "outside.csv", rows=3)
    nested = allowed / "nested"
    nested.mkdir()

    with running_server(runs, allow_roots=[allowed]) as base:
        # 根外
        status, payload, _ = api(base, "/api/import/scan", {"path": str(outside)})
        assert status == 400 and payload["error"] == "E_BAD_REQUEST"
        # .. 逃逸
        status, payload, _ = api(base, "/api/import/scan", {"path": str(allowed / ".." / "outside.csv")})
        assert status == 400
        # 目录
        status, payload, _ = api(base, "/api/import/scan", {"path": str(nested)})
        assert status == 400
        # 后缀白名单
        bad_suffix = allowed / "notes.md"
        bad_suffix.write_text("# hi\n", encoding="utf-8")
        status, payload, _ = api(base, "/api/import/scan", {"path": str(bad_suffix)})
        assert status == 400 and ".csv" in payload["hint"]
        # 超限
        status, payload, _ = api(base, "/api/import/scan", {"path": str(inside)})
        assert status == 200
    # 用小上限重开，验证 20MB 上限逻辑
    with running_server(runs, allow_roots=[allowed], max_import_bytes=10) as base:
        status, payload, _ = api(base, "/api/import/scan", {"path": str(inside)})
        assert status == 400 and "过大" in payload["message"]
    with running_server(runs, allow_roots=[allowed]) as base:
        # 不存在 + 缺字段 + 未知字段
        status, payload, _ = api(base, "/api/import/scan", {"path": str(allowed / "nope.csv")})
        assert status == 400
        status, payload, _ = api(base, "/api/import/scan", {})
        assert status == 422
        status, payload, _ = api(base, "/api/import/scan", {"path": str(inside), "x": 1})
        assert status == 422 and "x" in payload["message"]


# --------------------------------------------------------------------------- #
# §9.4 map
# --------------------------------------------------------------------------- #


def test_map_unavailable_without_switch_and_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY", TEST_KEY)
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv")
    with stub_gateway() as stub, running_server(runs, allow_roots=[tmp_path], base_url=stub.base_url) as base:
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        assert status == 200
        status, payload, _ = api(
            base, "/api/import/map", {"scan_id": scan["scan_id"], "consent": True}
        )
        assert status == 503
        assert payload["error"] == "E_LLM_UNAVAILABLE"
        assert "import-llm" in (payload["message"] + payload["hint"])
        # 未开开关时一次都不能碰网关
        assert stub.requests == []
        # §9.4（lead 2026-11-20 裁定）：没开开关时**先报 E_LLM_UNAVAILABLE**，
        # 不能因为没给 consent 就把「后端没配置」说成「用户没同意」。
        status, payload, _ = api(base, "/api/import/map", {"scan_id": scan["scan_id"]})
        assert status == 503 and payload["error"] == "E_LLM_UNAVAILABLE"
        assert stub.requests == []


def test_map_missing_key_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_KEY", raising=False)
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv")
    with stub_gateway() as stub, running_server(
        runs, import_llm=True, base_url=stub.base_url, allow_roots=[tmp_path]
    ) as base:
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        status, payload, _ = api(base, "/api/import/map", {"scan_id": scan["scan_id"], "consent": True})
        assert status == 503 and payload["error"] == "E_LLM_UNAVAILABLE"
        assert "TEST_KEY" in (payload["message"] + payload["hint"])
        assert stub.requests == []


def test_map_requires_consent_and_valid_scan_id(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY", TEST_KEY)
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv")
    with stub_gateway() as stub, running_server(
        runs, import_llm=True, base_url=stub.base_url, allow_roots=[tmp_path]
    ) as base:
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        status, payload, _ = api(base, "/api/import/map", {"scan_id": scan["scan_id"]})
        assert status == 400 and payload["error"] == "E_CONSENT_REQUIRED"
        status, payload, _ = api(base, "/api/import/map", {"scan_id": "deadbeefdeadbeef", "consent": True})
        assert status == 400 and payload["error"] == "E_BAD_REQUEST"
        assert stub.requests == []  # 两种拒绝都不该碰网关
        # 未知字段不静默忽略
        status, payload, _ = api(
            base, "/api/import/map", {"scan_id": scan["scan_id"], "consent": True, "oops": 1}
        )
        assert status == 422 and "oops" in payload["message"]


def test_map_stub_success_sends_only_header_profiles_and_samples(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TEST_KEY", TEST_KEY)
    runs = tmp_path / "runs"
    data = write_long_table(tmp_path / "long.csv", rows=200)
    with stub_gateway() as stub, running_server(
        runs, import_llm=True, base_url=stub.base_url, allow_roots=[tmp_path]
    ) as base:
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        assert status == 200 and scan["n_rows"] == 200
        status, payload, text = api(
            base, "/api/import/map",
            {"scan_id": scan["scan_id"], "consent": True, "sample_rows": 12, "model": "stub-model"},
        )
    assert status == 200, payload
    assert payload["source"] == "llm"
    assert payload["model"] == "stub-model"
    assert payload["sent"]["rows"] == 12
    assert payload["sent"]["url"] == f"{stub.base_url}/chat/completions"
    assert payload["sent"]["redacted"] is False
    assert payload["mapping"]["objective"]["column"] == "yield"
    assert [item["column"] for item in payload["mapping"]["variables"]] == ["T", "time", "solvent"]
    assert payload["confidence"] == 0.9

    # 真正的 OpenAI 兼容调用：路径 + Bearer
    assert len(stub.requests) == 1
    sent = stub.requests[0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["authorization"] == f"Bearer {TEST_KEY}"
    assert sent["body"]["model"] == "stub-model"
    messages = sent["body"]["messages"]
    assert messages[0]["role"] == "system" and "JSON" in messages[0]["content"]
    user_payload = json.loads(messages[1]["content"])
    assert set(user_payload) == {"columns", "sample_rows", "n_rows", "supported_kinds", "supported_ops", "note"}
    assert len(user_payload["sample_rows"]) == 12
    assert user_payload["n_rows"] == 200
    # 只发表头 + 画像 + 前 12 行：样本窗口外的标记与整文件内容都不在请求体里
    assert "SENTINEL-ROW-150" not in stub.sent_text()
    assert "T,time,solvent,yield,purity,batch" not in stub.sent_text()
    # 密钥不泄漏：响应、请求体、日志都不能出现
    assert TEST_KEY not in text
    captured = capsys.readouterr()
    assert TEST_KEY not in captured.out and TEST_KEY not in captured.err
    assert TEST_KEY not in stub.sent_text()


def test_map_retries_once_on_non_json(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY", TEST_KEY)

    def responder(index, body):
        if index == 0:
            return "我认为 T 是条件列（这不是 JSON）", 200
        return json.dumps({"mapping": VALID_MAPPING, "confidence": 0.5, "rationale": "repair"}), 200

    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv")
    with stub_gateway(responder) as stub, running_server(
        runs, import_llm=True, base_url=stub.base_url, allow_roots=[tmp_path]
    ) as base:
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        status, payload, _ = api(base, "/api/import/map", {"scan_id": scan["scan_id"], "consent": True})
    assert status == 200, payload
    assert len(stub.requests) == 2
    assert any("重试" in item for item in payload["warnings"])
    assert payload["mapping"]["objective"]["column"] == "yield"


def test_map_bad_response_after_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY", TEST_KEY)
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv")
    with stub_gateway(lambda index, body: ("still not json at all", 200)) as stub, running_server(
        runs, import_llm=True, base_url=stub.base_url, allow_roots=[tmp_path]
    ) as base:
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        status, payload, _ = api(base, "/api/import/map", {"scan_id": scan["scan_id"], "consent": True})
    assert status == 502, payload
    assert payload["error"] == "E_LLM_BAD_RESPONSE"
    assert "still not json" in payload["hint"]  # 原始响应前 500 字符
    assert len(stub.requests) == 2  # 只重试一次


def test_map_downgrades_illegal_kind_and_unknown_column(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY", TEST_KEY)
    bad_mapping = dict(VALID_MAPPING)
    bad_mapping["variables"] = [
        {"column": "T", "name": "T", "kind": "weird-kind", "low": 20, "high": 200},
        {"column": "ghost", "name": "ghost", "kind": "continuous", "low": 0, "high": 1},
        {"column": "solvent", "name": "solvent", "kind": "categorical"},
    ]
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv")
    with stub_gateway(lambda index, body: (json.dumps({"mapping": bad_mapping}), 200)) as stub, running_server(
        runs, import_llm=True, base_url=stub.base_url, allow_roots=[tmp_path]
    ) as base:
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        status, payload, _ = api(base, "/api/import/map", {"scan_id": scan["scan_id"], "consent": True})
    assert status == 200, payload
    kinds = {item["column"]: item["kind"] for item in payload["mapping"]["variables"]}
    assert kinds["T"] == "integer"  # 降级到画像推断
    assert "ghost" not in kinds  # 表里没有的列被忽略
    assert any("weird-kind" in item for item in payload["warnings"])
    assert any("ghost" in item for item in payload["warnings"])


def test_map_rules_fallback_offline(tmp_path):
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv")
    with running_server(runs, allow_roots=[tmp_path]) as base:
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        status, payload, _ = api(
            base, "/api/import/map", {"scan_id": scan["scan_id"], "fallback": "rules"}
        )
    assert status == 200 and payload["source"] == "rules"
    assert payload["sent"] is None and payload["mapping"]["objective"]["column"] == "yield"


# --------------------------------------------------------------------------- #
# §9.5 apply
# --------------------------------------------------------------------------- #


def test_apply_end_to_end_and_params_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY", TEST_KEY)
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv", rows=30)
    with stub_gateway() as stub, running_server(
        runs, import_llm=True, base_url=stub.base_url, allow_roots=[tmp_path]
    ) as base:
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        assert status == 200
        status, mapped, _ = api(base, "/api/import/map", {"scan_id": scan["scan_id"], "consent": True})
        assert status == 200
        # 前端「让模型读表」显式发 fallback:"llm"，后端必须接受
        status, again, _ = api(
            base, "/api/import/map", {"scan_id": scan["scan_id"], "consent": True, "fallback": "llm"}
        )
        assert status == 200 and again["source"] == "llm"
        status, applied, text = api(
            base, "/api/import/apply",
            {
                "id": "campaign_from_csv",
                "path": str(data),
                "scan_id": scan["scan_id"],
                "mapping": mapped["mapping"],
                "algorithm": "sobol",
                "budget": 40,
                "batch": 2,
                "reason": "导入历史 30 次实验作为先验",
            },
        )
        assert status == 200, applied
        result = applied["result"]
        imported = result["imported"]
        assert imported["accepted"] == 30
        assert imported["duplicate"] == 1
        assert len(imported["rejected"]) == 1
        assert "'T'" in imported["rejected"][0]["reason"] and "n/a" in imported["rejected"][0]["reason"]
        assert result["run"]["n_obs"] == 30
        assert result["run"]["remaining"] == 10
        assert result["next"] == "ask"
        assert any("剩余预算" in item for item in result["warnings"])

        # params_schema 与 registry 逐字段一致
        spec = next(item for item in condopt_registry.describe_all() if item["name"] == "sobol")
        assert result["params_schema"] == json.loads(json.dumps(spec["params"], ensure_ascii=False))

        # 审计
        audit_lines = (
            runs / "campaign_from_csv" / "import_audit.jsonl"
        ).read_text(encoding="utf-8").strip().splitlines()
        assert len(audit_lines) == 1
        audit = json.loads(audit_lines[0])
        assert audit["source"] == "llm" and audit["model"] == "qwen3.8-27b" and audit["consent"] is True
        assert audit["accepted"] == 30 and audit["budget"] == 40 and audit["reason"]
        assert len(audit["file_sha256"]) == 64

        # 端到端：直接拿下一批
        status, asked, _ = api(base, "/api/action", {"action": "ask", "run": "campaign_from_csv",
                                                    "payload": {"n": 2}})
        assert status == 200 and len(asked["result"]["pending"]) == 2
        assert TEST_KEY not in text


def test_apply_budget_validation(tmp_path):
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv", rows=10)
    with running_server(runs, allow_roots=[tmp_path]) as base:
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        status, mapped, _ = api(base, "/api/import/map", {"scan_id": scan["scan_id"], "fallback": "rules"})
        mapping = mapped["mapping"]
        mapping["variables"] = [item for item in mapping["variables"] if item["column"] in ("T", "time", "solvent")]

        status, payload, _ = api(
            base, "/api/import/apply",
            {"id": "too_small", "path": str(data), "mapping": mapping, "budget": 3},
        )
        assert status == 422 and payload["error"] == "E_VALIDATION"
        # §9.5（2026-11-20 冻结）：错误体必须带 suggested_budget（数字）供前端一键采用
        assert isinstance(payload["suggested_budget"], int) and payload["suggested_budget"] >= 10
        assert str(payload["suggested_budget"]) in payload["hint"]
        assert not (runs / "too_small").exists(), "校验失败不得留下半成品 run"

        status, payload, _ = api(
            base, "/api/import/apply",
            {"id": "no_budget", "path": str(data), "mapping": mapping},
        )
        assert status == 422 and "budget" in payload["message"]
        assert isinstance(payload["suggested_budget"], int)

        # count_toward_budget=false：condopt 语义无法关闭 → 明确 warning，不拦流程、不静默忽略
        status, payload, _ = api(
            base, "/api/import/apply",
            {"id": "count_off", "path": str(data), "mapping": mapping, "budget": 20,
             "count_toward_budget": False},
        )
        assert status == 200, payload
        assert any("count_toward_budget" in item for item in payload["warnings"])
        assert payload["run"]["n_obs"] == 10

        # 缺变量/缺目标/未知字段
        empty = dict(mapping, variables=[])
        status, payload, _ = api(base, "/api/import/apply",
                                 {"id": "novars", "path": str(data), "mapping": empty, "budget": 20})
        assert status == 422 and "variables" in payload["message"]
        ghost = dict(mapping, variables=[{"column": "ghost", "name": "g", "kind": "continuous",
                                          "low": 0, "high": 1}])
        status, payload, _ = api(base, "/api/import/apply",
                                 {"id": "ghost", "path": str(data), "mapping": ghost, "budget": 20})
        assert status == 422 and "ghost" in payload["message"]
        status, payload, _ = api(base, "/api/import/apply",
                                 {"id": "extra", "path": str(data), "mapping": mapping, "budget": 20, "zzz": 1})
        assert status == 422 and "zzz" in payload["message"]

        # 正常路径
        status, payload, _ = api(base, "/api/import/apply",
                                 {"id": "ok_run", "path": str(data), "mapping": mapping, "budget": 20})
        assert status == 200, payload
        assert payload["result"]["imported"]["accepted"] == 10
        assert payload["result"]["run"]["remaining"] == 10


def test_apply_scan_id_mismatch_is_rejected(tmp_path):
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv", rows=5)
    with running_server(runs, allow_roots=[tmp_path]) as base:
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        status, mapped, _ = api(base, "/api/import/map", {"scan_id": scan["scan_id"], "fallback": "rules"})
        mapping = mapped["mapping"]
        mapping["variables"] = [item for item in mapping["variables"] if item["column"] == "T"]
        # 扫描后文件被改：scan_id 失配
        data.write_text(data.read_text(encoding="utf-8") + "60,5,MeCN,60,96,P1\n", encoding="utf-8")
        status, payload, _ = api(
            base, "/api/import/apply",
            {"id": "stale", "path": str(data), "scan_id": scan["scan_id"], "mapping": mapping, "budget": 20},
        )
        assert status == 400 and "scan_id" in payload["message"]
        assert not (runs / "stale").exists()


def test_apply_imports_into_existing_study_with_budget_guard(tmp_path):
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv", rows=5)
    mapping = dict(VALID_MAPPING)
    mapping["variables"] = [item for item in VALID_MAPPING["variables"] if item["column"] == "T"]
    with running_server(runs, allow_roots=[tmp_path]) as base:
        status, first, _ = api(base, "/api/import/apply",
                               {"id": "twice", "path": str(data), "mapping": mapping, "budget": 20})
        assert status == 200, first
        assert first["result"]["imported"]["accepted"] == 5
        # 同一批数据再导入（这次故意给一个不同的 budget）：全部算重复，n_obs 不变，且沿用既有 budget
        status, second, _ = api(base, "/api/import/apply",
                                {"id": "twice", "path": str(data), "mapping": mapping, "budget": 99})
        assert status == 200, second
        assert second["result"]["imported"]["accepted"] == 0
        # 6 条有效行（5 唯一 + 1 表内重复）全部命中既有 dataset 的去重
        assert second["result"]["imported"]["duplicate"] == 6
        assert len(second["result"]["imported"]["rejected"]) == 1
        assert second["result"]["run"]["n_obs"] == 5
        assert any("沿用其 budget" in item for item in second["result"]["warnings"])


# --------------------------------------------------------------------------- #
# §9.7.5 默认配置离线 + bootstrap 暴露
# --------------------------------------------------------------------------- #


def test_default_config_is_offline_and_bootstrap_exposes_import_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_KEY", raising=False)
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv", rows=4)
    with stub_gateway() as stub, running_server(
        runs, allow_roots=[tmp_path], api_key_env="TEST_KEY"
    ) as base:
        status, boot, _ = http_get(base, "/api/bootstrap")
        assert status == 200
        # §2.1（2026-11-20 冻结）：import_llm 是让前端在外发前逐字列明 URL/模型的唯一来源
        info = boot["import_llm"]
        assert set(info) >= {"configured", "url", "model", "key_env", "enabled"}
        assert info["enabled"] is False
        assert info["configured"] is False  # 指定了 TEST_KEY 且它未设置
        assert info["key_env"] == "TEST_KEY"
        assert info["url"] == bridge_module.DEFAULT_LLM_BASE_URL
        assert info["model"] == bridge_module.DEFAULT_LLM_MODEL
        settings = boot["import"]
        assert settings["enabled"] is False and settings["key_env"] == "TEST_KEY"
        assert TEST_KEY not in json.dumps(boot)
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        assert status == 200
        status, payload, _ = api(base, "/api/import/map", {"scan_id": scan["scan_id"], "consent": True})
        assert status == 503 and payload["error"] == "E_LLM_UNAVAILABLE"
        assert stub.requests == []  # 默认配置零外呼
        # 规则回退仍然可用（离线）
        status, payload, _ = api(base, "/api/import/map",
                                 {"scan_id": scan["scan_id"], "fallback": "rules"})
        assert status == 200 and payload["source"] == "rules"


def http_get(base: str, path: str):
    status, _, text = request(base, "GET", path)
    return status, json.loads(text), text


def test_import_scenario_metadata_and_no_sse_channel(tmp_path):
    """§9.6：导入场景要出现在 /api/bootstrap，且 /api/run 明确拒绝（不假装有流）。"""
    runs = tmp_path / "runs"
    with running_server(runs, allow_roots=[tmp_path]) as base:
        status, boot, _ = http_get(base, "/api/bootstrap")
        assert status == 200
        scenarios = {item["key"]: item for item in boot["scenarios"]}
        assert list(scenarios) == ["kickoff", "sample", "feedback", "delivery", "board", "import"]
        import_scenario = scenarios["import"]
        assert import_scenario["no"] == "06"
        assert import_scenario["action"] == "import"
        assert import_scenario["needs_run"] is False
        assert import_scenario["stages"] == ["读取文件", "画像与列类型", "让模型读表", "导入为先验"]

        status, payload, _ = api(base, "/api/run", {"scenario": "import", "payload": {}})
        assert status == 400 and payload["error"] == "E_BAD_REQUEST"
        assert "/api/import/scan" in payload["hint"]


def test_import_scenario_matches_frontend_data_js(tmp_path):
    """后端 Scenario 与前端 data.js 逐字段一致（漂移就是 bug，不靠兜底遮住）。"""
    data_js = (ROOT / "web" / "assets" / "data.js").read_text(encoding="utf-8")
    if '"import"' not in data_js:
        pytest.skip("前端 data.js 尚未落地 import 场景")
    # data.js 是 JS 字面量：抓 import 场景块里的关键字段做对照（不做完整 JS 解析）
    block = data_js[data_js.index('key: "import"'):]
    block = block[: block.index("},") + 2]
    for fragment in (
        'no: "06"',
        'title: "导入数据"',
        'short: "导入"',
        'action: "import"',
        "needs_run: false",
        'stages: ["读取文件", "画像与列类型", "让模型读表", "导入为先验"]',
    ):
        assert fragment in block, f"data.js import 场景缺少 {fragment}"
    runs = tmp_path / "runs"
    with running_server(runs, allow_roots=[tmp_path]) as base:
        status, boot, _ = http_get(base, "/api/bootstrap")
    backend = next(item for item in boot["scenarios"] if item["key"] == "import")
    assert backend["stages"] == ["读取文件", "画像与列类型", "让模型读表", "导入为先验"]
    assert backend["no"] == "06" and backend["action"] == "import" and backend["needs_run"] is False


# --------------------------------------------------------------------------- #
# §2.1 / §9.4（2026-11-20 追加裁定）的针对性回归
# --------------------------------------------------------------------------- #


def test_bootstrap_import_llm_reports_effective_values(tmp_path, monkeypatch):
    """import_llm 必须报「实际生效值」（含环境变量覆盖），且绝不回显密钥。"""
    monkeypatch.setenv("TEST_KEY", TEST_KEY)
    runs = tmp_path / "runs"
    with stub_gateway() as stub, running_server(
        runs, import_llm=True, base_url=stub.base_url, api_key_env="TEST_KEY",
        allow_roots=[tmp_path],
    ) as base:
        status, boot, text = http_get(base, "/api/bootstrap")
    assert status == 200
    info = boot["import_llm"]
    assert info["enabled"] is True
    assert info["configured"] is True
    assert info["url"] == stub.base_url
    assert info["key_env"] == "TEST_KEY"
    assert info["model"] == bridge_module.DEFAULT_LLM_MODEL
    assert TEST_KEY not in text
    assert TEST_KEY not in json.dumps(boot)


def test_map_missing_consent_only_when_llm_is_reachable(tmp_path, monkeypatch):
    """可调模型但没给 consent → E_CONSENT_REQUIRED；不可调 → E_LLM_UNAVAILABLE（顺序不可换）。"""
    monkeypatch.setenv("TEST_KEY", TEST_KEY)
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv", rows=4)
    with stub_gateway() as stub, running_server(
        runs, import_llm=True, base_url=stub.base_url, api_key_env="TEST_KEY", allow_roots=[tmp_path]
    ) as base:
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        status, payload, _ = api(base, "/api/import/map", {"scan_id": scan["scan_id"]})
        assert status == 400 and payload["error"] == "E_CONSENT_REQUIRED"
        assert stub.requests == []
    # 可调但缺密钥：也是 E_LLM_UNAVAILABLE，不是 consent
    monkeypatch.delenv("TEST_KEY", raising=False)
    with stub_gateway() as stub2, running_server(
        runs, import_llm=True, base_url=stub2.base_url, api_key_env="TEST_KEY", allow_roots=[tmp_path]
    ) as base:
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        status, payload, _ = api(base, "/api/import/map", {"scan_id": scan["scan_id"]})
        assert status == 503 and payload["error"] == "E_LLM_UNAVAILABLE"
    # 规则路径永远不需要 consent（前端发 consent:false + fallback:"rules"）
    with running_server(runs, allow_roots=[tmp_path]) as base:
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        status, payload, _ = api(
            base, "/api/import/map", {"scan_id": scan["scan_id"], "consent": False, "fallback": "rules"}
        )
        assert status == 200 and payload["source"] == "rules" and payload["sent"] is None


def test_map_and_apply_accept_frozen_fidelity_shape(tmp_path, monkeypatch):
    """§9.4 冻结形状：fidelity/cost 是对象；fidelity 可带 levels 换算。"""
    monkeypatch.setenv("TEST_KEY", TEST_KEY)
    runs = tmp_path / "runs"
    data = tmp_path / "mf.csv"
    rows = ["T,yield,fidelity,cost"]
    for index in range(12):
        level = "low" if index % 2 else "high"
        rows.append(f"{40 + index}, {50 + index}, {level}, {0.3 if level == 'low' else 1.0}")
    data.write_text("\n".join(rows) + "\n", encoding="utf-8")

    mapping = {
        "variables": [{"column": "T", "name": "T", "kind": "continuous", "low": 40.0, "high": 60.0}],
        "objective": {"column": "yield", "name": "yield", "direction": "max"},
        "constraints": [],
        "fidelity": {"column": "fidelity", "name": "fidelity", "levels": {"low": 0.3, "high": 1.0}},
        "cost": {"column": "cost", "name": "cost"},
    }
    with stub_gateway(lambda index, body: (json.dumps({"mapping": mapping}), 200)) as stub, running_server(
        runs, import_llm=True, base_url=stub.base_url, api_key_env="TEST_KEY", allow_roots=[tmp_path]
    ) as base:
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        assert status == 200
        # 规则映射的 fidelity/cost 也必须是对象形状
        rule = scan["rule_mapping"]
        assert isinstance(rule["fidelity"], dict) and rule["fidelity"]["column"] == "fidelity"
        assert isinstance(rule["cost"], dict) and rule["cost"]["column"] == "cost"
        status, mapped, _ = api(base, "/api/import/map", {"scan_id": scan["scan_id"], "consent": True})
        assert status == 200, mapped
        assert mapped["mapping"]["fidelity"]["levels"] == {"low": 0.3, "high": 1.0}
        status, applied, _ = api(
            base, "/api/import/apply",
            {"id": "mf_import", "path": str(data), "mapping": mapping, "budget": 30, "algorithm": "sobol"},
        )
    assert status == 200, applied
    assert applied["imported"]["accepted"] == 12
    # 平顶响应（§9.5）与 result 镜像都在
    assert applied["params_schema"] == applied["result"]["params_schema"]
    assert applied["run"]["id"] == "mf_import"


def test_apply_fills_missing_categorical_choices_from_data(tmp_path):
    """前端确认表不带 choices：apply 必须用数据里的真实取值补齐（不 422、不伪造）。"""
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv", rows=6)
    mapping = {
        "variables": [
            {"column": "T", "name": "T", "kind": "continuous", "low": 30.0, "high": 100.0},
            {"column": "solvent", "name": "solvent", "kind": "categorical"},
        ],
        "objective": {"column": "yield", "name": "yield", "direction": "max"},
        "constraints": [],
        "fidelity": None,
        "cost": None,
    }
    with running_server(runs, allow_roots=[tmp_path]) as base:
        status, payload, _ = api(
            base, "/api/import/apply",
            {"id": "no_choices", "path": str(data), "mapping": mapping, "budget": 20, "algorithm": "sobol"},
        )
    assert status == 200, payload
    assert payload["imported"]["accepted"] == 6
    assert any("choices 由数据补齐" in item for item in payload["warnings"])
    import json as _json

    problem = _json.loads((runs / "no_choices" / "problem.json").read_text(encoding="utf-8"))
    solvent = next(item for item in problem["space"]["variables"] if item["name"] == "solvent")
    assert set(solvent["choices"]) == {"MeCN", "EtOH"}


# --------------------------------------------------------------------------- #
# §9.3 sample_preview（2026-11-20 14:40）：同意界面的字节数必须是真值
# --------------------------------------------------------------------------- #


def test_scan_sample_preview_is_the_real_byte_count(tmp_path, monkeypatch):
    """硬不变量：同文件同 sample_rows 下 scan.sample_preview.bytes == map.sent.bytes == stub 实测字节数。"""
    monkeypatch.setenv("TEST_KEY", TEST_KEY)
    runs = tmp_path / "runs"
    data = write_long_table(tmp_path / "long.csv", rows=200)
    with stub_gateway() as stub, running_server(
        runs, import_llm=True, base_url=stub.base_url, api_key_env="TEST_KEY", allow_roots=[tmp_path]
    ) as base:
        for sample_rows in (12, 25):
            status, scan, _ = api(base, "/api/import/scan", {"path": str(data), "sample_rows": sample_rows})
            assert status == 200, scan
            preview = scan["sample_preview"]
            assert preview["rows"] == sample_rows
            assert preview["endpoint"] == f"{stub.base_url}/chat/completions"
            assert preview["bytes"] > 0

            status, mapped, _ = api(
                base, "/api/import/map",
                {"scan_id": scan["scan_id"], "consent": True, "sample_rows": sample_rows},
            )
            assert status == 200, mapped
            assert mapped["sent"]["rows"] == sample_rows
            # ① 后端两条路径自洽
            assert mapped["sent"]["bytes"] == preview["bytes"], (
                f"sample_rows={sample_rows}: scan 预告 {preview['bytes']} != map 实发 {mapped['sent']['bytes']}"
            )
            # ② 独立证据：在 stub 侧量真正收到的 body 字节数（UTF-8）
            raw = stub.requests[-1]["raw"]
            assert len(raw.encode("utf-8")) == preview["bytes"], (
                f"sample_rows={sample_rows}: stub 实测 {len(raw.encode('utf-8'))} != scan 预告 {preview['bytes']}"
            )
            assert mapped["sent"]["url"] == preview["endpoint"]


def test_scan_sample_preview_available_without_llm_config(tmp_path, monkeypatch):
    """未开开关 / 未配密钥也要能给预览（构造提示词不需要密钥），且不出网。"""
    monkeypatch.delenv("TEST_KEY", raising=False)
    runs = tmp_path / "runs"
    data = write_long_table(tmp_path / "long.csv", rows=40)
    with stub_gateway() as stub, running_server(
        runs, api_key_env="TEST_KEY", base_url=stub.base_url, allow_roots=[tmp_path]
    ) as base:
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        assert status == 200, scan
        preview = scan["sample_preview"]
        assert preview["rows"] == 12  # 缺省 12
        assert preview["bytes"] > 0
        assert preview["endpoint"] == f"{stub.base_url}/chat/completions"
        assert stub.requests == []  # 预览不出网


def test_scan_preview_uses_the_same_model_as_map(tmp_path, monkeypatch):
    """自定义 model 时也要一致：scan 与 map 传同一个 model，字节数仍然相等。"""
    monkeypatch.setenv("TEST_KEY", TEST_KEY)
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv", rows=30)
    with stub_gateway() as stub, running_server(
        runs, import_llm=True, base_url=stub.base_url, api_key_env="TEST_KEY", allow_roots=[tmp_path]
    ) as base:
        status, scan, _ = api(
            base, "/api/import/scan", {"path": str(data), "sample_rows": 8, "model": "stub-model-xyz"}
        )
        assert status == 200
        status, mapped, _ = api(
            base, "/api/import/map",
            {"scan_id": scan["scan_id"], "consent": True, "sample_rows": 8, "model": "stub-model-xyz"},
        )
    assert status == 200
    assert mapped["sent"]["bytes"] == scan["sample_preview"]["bytes"]
    assert len(stub.requests[-1]["raw"].encode("utf-8")) == scan["sample_preview"]["bytes"]
    assert stub.requests[-1]["body"]["model"] == "stub-model-xyz"


def test_scan_sample_rows_validation(tmp_path):
    """sample_rows 缺省 12、允许 1..50，越界/类型错 → 422（不静默夹取）。"""
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv", rows=5)
    with running_server(runs, allow_roots=[tmp_path]) as base:
        for bad in (0, 51, -1, 999):
            status, payload, _ = api(base, "/api/import/scan", {"path": str(data), "sample_rows": bad})
            assert status == 422, f"sample_rows={bad}: {status} {payload}"
            assert payload["error"] == "E_VALIDATION" and "越界" in payload["message"]
        status, payload, _ = api(base, "/api/import/scan", {"path": str(data), "sample_rows": "12"})
        assert status == 422 and "整数" in payload["message"]
        status, payload, _ = api(base, "/api/import/scan", {"path": str(data), "sample_rows": 1})
        assert status == 200 and payload["sample_preview"]["rows"] == 1
        # 未知字段仍不静默忽略
        status, payload, _ = api(base, "/api/import/scan", {"path": str(data), "zzz": 1})
        assert status == 422 and "zzz" in payload["message"]
        # map 的越界同样 422（与 scan 口径一致，避免「预告 12、实发 50」）
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        status, payload, _ = api(
            base, "/api/import/map", {"scan_id": scan["scan_id"], "consent": False, "sample_rows": 80}
        )
        assert status == 422 and "越界" in payload["message"]


def test_map_warns_when_actual_bytes_drift_from_preview(tmp_path, monkeypatch):
    """运行时守卫：实际发送量与 scan 预告不一致时必须显式 warning（不静默）。"""
    monkeypatch.setenv("TEST_KEY", TEST_KEY)
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv", rows=30)
    with stub_gateway() as stub, running_server(
        runs, import_llm=True, base_url=stub.base_url, api_key_env="TEST_KEY", allow_roots=[tmp_path]
    ) as base:
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data), "sample_rows": 12})
        preview_bytes = scan["sample_preview"]["bytes"]
        # 正常路径：与预告一致 → 不该有 drift warning
        status, mapped, _ = api(
            base, "/api/import/map", {"scan_id": scan["scan_id"], "consent": True, "sample_rows": 12}
        )
        assert mapped["sent"]["bytes"] == preview_bytes
        assert not any("与 scan 预告" in item for item in mapped["warnings"])
        # 换模型 / 换 sample_rows（未重扫）→ 必须显式提醒
        status, drifted, _ = api(
            base, "/api/import/map",
            {"scan_id": scan["scan_id"], "consent": True, "sample_rows": 12, "model": "another-model-name-longer"},
        )
        assert status == 200
        assert drifted["sent"]["bytes"] != preview_bytes
        assert any("与 scan 预告" in item for item in drifted["warnings"]), drifted["warnings"]
        assert len(stub.requests[-1]["raw"].encode("utf-8")) == drifted["sent"]["bytes"]


# --------------------------------------------------------------------------- #
# task-3 F6：不可用于 HTTP 头的密钥是「配置错误」，不是 500
# --------------------------------------------------------------------------- #


def test_map_unusable_api_key_is_config_error_not_500(tmp_path, monkeypatch, capsys):
    """密钥含非 ASCII / 控制字符（CRLF 注入）→ 503 E_LLM_UNAVAILABLE，不回显密钥、不出网。"""
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv", rows=6)
    # Windows 的环境变量不能含 NUL，所以 NUL/其它控制字符走下面的 helper 单测。
    bad_keys = ["sk-中文密钥-绝不外泄", "sk-🔑-emoji-key-绝不外泄", "sk-with\nnewline", "sk-with\rreturn"]
    for bad_key in bad_keys:
        monkeypatch.setenv("TEST_KEY", bad_key)
        with stub_gateway() as stub, running_server(
            runs, import_llm=True, base_url=stub.base_url, api_key_env="TEST_KEY", allow_roots=[tmp_path]
        ) as base:
            status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
            assert status == 200
            status, payload, text = api(
                base, "/api/import/map", {"scan_id": scan["scan_id"], "consent": True}
            )
            assert status == 503, f"{bad_key!r}: status={status} {text[:200]}"
            assert payload["error"] == "E_LLM_UNAVAILABLE"
            assert "TEST_KEY" in (payload["message"] + payload["hint"])
            assert bad_key not in text and bad_key not in json.dumps(payload)
            # 不泄漏 Python 异常/回溯
            for marker in ("Traceback", "UnicodeEncodeError", "  File \""):
                assert marker not in text, f"{marker} 出现在响应里: {text[:200]}"
            assert stub.requests == []  # 配置错误时一次都不能外呼
    captured = capsys.readouterr()
    for bad_key in bad_keys:
        assert bad_key not in captured.out and bad_key not in captured.err
    assert "UnicodeEncodeError" not in captured.err


def test_map_normal_ascii_key_still_works(tmp_path, monkeypatch):
    """反向：普通 ASCII 密钥不受 F6 校验影响。"""
    monkeypatch.setenv("TEST_KEY", TEST_KEY)
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv", rows=6)
    with stub_gateway() as stub, running_server(
        runs, import_llm=True, base_url=stub.base_url, api_key_env="TEST_KEY", allow_roots=[tmp_path]
    ) as base:
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        status, payload, _ = api(base, "/api/import/map", {"scan_id": scan["scan_id"], "consent": True})
    assert status == 200 and payload["source"] == "llm"
    assert stub.requests[0]["authorization"] == f"Bearer {TEST_KEY}"


def test_api_key_header_check_rejects_nul_and_controls():
    """helper 单测：NUL/控制字符（CRLF 注入面）同样判为配置错误，而不是等到 add_header 才炸。"""
    for bad in ("sk-\x00-nul", "sk-\x7f-del", "sk-\x01-ctl"):
        try:
            bridge_module._check_api_key_for_header(bad, "TEST_KEY")
        except bridge_module.WorkbenchError as exc:
            assert exc.error == "E_LLM_UNAVAILABLE" and exc.status == 503
            assert "TEST_KEY" in exc.message and bad not in exc.message
        else:  # pragma: no cover - 失败路径
            raise AssertionError(f"应拒绝 {bad!r}")
    # 正常 ASCII / latin-1 不被拒
    bridge_module._check_api_key_for_header("sk-abcdef-123456", "TEST_KEY")


# --------------------------------------------------------------------------- #
# task-3 F7/F8：baseURL 配置错必须 503（不是 500 InvalidURL）；显式置空不回退默认端点
# --------------------------------------------------------------------------- #

#: 含空白 / 坏端口 / 无 scheme / 坏 scheme / 无主机 —— 全部是**配置错**。
BAD_BASE_URLS = (
    "http://127.0.0.1:9/v1 /v1",
    "http://127.0.0.1:notaport/v1",
    "127.0.0.1:9/v1",
    "ftp://127.0.0.1:9/v1",
    "http:///v1",
)


def test_F7_bad_base_url_is_config_error_not_500(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TEST_KEY", TEST_KEY)
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv", rows=6)
    for bad_url in BAD_BASE_URLS:
        monkeypatch.setenv("CONDOPT_IMPORT_LLM_BASE_URL", bad_url)
        with stub_gateway() as stub, running_server(
            runs, import_llm=True, api_key_env="TEST_KEY", allow_roots=[tmp_path]
        ) as base:
            status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
            assert status == 200, scan
            # scan 阶段就要提示配置错，且不给假 endpoint
            assert any("配置不合法" in item for item in scan["warnings"]), scan["warnings"]
            assert scan["sample_preview"]["endpoint"] == ""
            assert scan["sample_preview"]["bytes"] > 0  # 预览真值仍然给得出

            status, payload, text = api(
                base, "/api/import/map", {"scan_id": scan["scan_id"], "consent": True}
            )
            assert status == 503, f"{bad_url!r}: status={status} {text[:200]}"
            assert payload["error"] == "E_LLM_UNAVAILABLE"
            assert "CONDOPT_IMPORT_LLM_BASE_URL" in (payload["message"] + payload["hint"])
            for marker in ("Traceback", "InvalidURL", "  File \""):
                assert marker not in text, f"{bad_url!r} 响应里出现 {marker}: {text[:200]}"
            assert stub.requests == []  # 配置错时一次都不能外呼
    captured = capsys.readouterr()
    assert "InvalidURL" not in captured.out  # sanitize 后日志里也只留类名于 stderr
    # bootstrap 要把「地址不合法」如实告诉前端（供同意弹窗事前预警）
    monkeypatch.setenv("CONDOPT_IMPORT_LLM_BASE_URL", BAD_BASE_URLS[0])
    with running_server(runs, import_llm=True, api_key_env="TEST_KEY", allow_roots=[tmp_path]) as base:
        status, boot, _ = http_get(base, "/api/bootstrap")
    assert status == 200
    assert boot["import_llm"]["url_valid"] is False
    assert boot["import"]["url_valid"] is False and boot["import"]["url_error"]
    assert boot["import_llm"]["url"] == BAD_BASE_URLS[0]


def test_F8_explicit_empty_base_url_does_not_fall_back_to_default(tmp_path, monkeypatch):
    """显式置空/纯空白 → 配置错误 503；绝不静默回退到真实默认端点（F8）。"""
    monkeypatch.setenv("TEST_KEY", TEST_KEY)
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv", rows=6)
    for blank in ("", "   "):
        monkeypatch.setenv("CONDOPT_IMPORT_LLM_BASE_URL", blank)
        with stub_gateway() as stub, running_server(
            runs, import_llm=True, api_key_env="TEST_KEY", allow_roots=[tmp_path]
        ) as base:
            status, boot, _ = http_get(base, "/api/bootstrap")
            assert status == 200
            info = boot["import_llm"]
            assert info["url"] == "" and info["url_valid"] is False
            assert info["url"] != bridge_module.DEFAULT_LLM_BASE_URL  # 没有被悄悄换成默认端点
            status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
            assert status == 200 and scan["sample_preview"]["endpoint"] == ""
            status, payload, text = api(
                base, "/api/import/map", {"scan_id": scan["scan_id"], "consent": True}
            )
            assert status == 503 and payload["error"] == "E_LLM_UNAVAILABLE"
            assert "空" in payload["message"] or "空白" in payload["message"]
            assert "CONDOPT_IMPORT_LLM_BASE_URL" in (payload["message"] + payload["hint"])
            assert stub.requests == []
            # 提示里可以举例说明合法写法，但绝不能把默认端点当作「本次生效端点」拼出来
            assert f"{bridge_module.DEFAULT_LLM_BASE_URL}/chat/completions" not in text


def test_base_url_unset_uses_default_and_stays_valid(tmp_path, monkeypatch):
    """反向：环境变量未设置 → 用默认端点且 url_valid=True（不误伤正常配置）。"""
    monkeypatch.setenv("TEST_KEY", TEST_KEY)
    monkeypatch.delenv("CONDOPT_IMPORT_LLM_BASE_URL", raising=False)
    runs = tmp_path / "runs"
    with running_server(runs, import_llm=True, api_key_env="TEST_KEY", allow_roots=[tmp_path]) as base:
        status, boot, _ = http_get(base, "/api/bootstrap")
    assert status == 200
    assert boot["import_llm"]["url"] == bridge_module.DEFAULT_LLM_BASE_URL
    assert boot["import_llm"]["url_valid"] is True
    assert boot["import"]["url_error"] is None


def test_D5_non_ascii_host_is_reported_invalid_before_the_call(tmp_path, monkeypatch):
    """D5：非 ASCII 主机在 bootstrap 里就必须 url_valid=false，与实际 503 行为一致。"""
    monkeypatch.setenv("TEST_KEY", TEST_KEY)
    monkeypatch.setenv("CONDOPT_IMPORT_LLM_BASE_URL", "http://例子.测试/v1")
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv", rows=6)
    with stub_gateway() as stub, running_server(
        runs, import_llm=True, api_key_env="TEST_KEY", allow_roots=[tmp_path]
    ) as base:
        status, boot, _ = http_get(base, "/api/bootstrap")
        assert status == 200
        info = boot["import_llm"]
        assert info["url_valid"] is False, info
        assert "非 ASCII" in boot["import"]["url_error"]
        status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
        assert status == 200
        assert scan["sample_preview"]["endpoint"] == ""
        assert any("配置不合法" in item for item in scan["warnings"])
        status, payload, text = api(
            base, "/api/import/map", {"scan_id": scan["scan_id"], "consent": True}
        )
        assert status == 503 and payload["error"] == "E_LLM_UNAVAILABLE"
        assert "非 ASCII" in payload["message"]
        assert "CONDOPT_IMPORT_LLM_BASE_URL" in (payload["message"] + payload["hint"])
        assert "Traceback" not in text and "UnicodeEncodeError" not in text
        assert stub.requests == []
    # 反向：punycode 写法是合法配置（url_valid=true）
    monkeypatch.setenv("CONDOPT_IMPORT_LLM_BASE_URL", "http://xn--fsqu00a.xn--0zwm56d/v1")
    with running_server(runs, import_llm=True, api_key_env="TEST_KEY", allow_roots=[tmp_path]) as base:
        status, boot, _ = http_get(base, "/api/bootstrap")
    assert status == 200 and boot["import_llm"]["url_valid"] is True


def test_D5_invariant_url_valid_iff_an_http_attempt_is_made(tmp_path, monkeypatch):
    """不变量：url_valid == 「构造期无错 == map 会真的发起 HTTP 尝试」。

    - `url_valid=false` ⇒ map 在**配置层**就 503，且一次外呼都没有（stub 收到 0 次）；
    - `url_valid=true`  ⇒ map 真的发起了尝试：指向 stub 的必然 200 且 stub 收到请求；
      连不上的（对照组）也必须报「无法连接」这种**运行期**错误，而不是配置错误。
    """
    monkeypatch.setenv("TEST_KEY", TEST_KEY)
    runs = tmp_path / "runs"
    data = write_table(tmp_path / "lab.csv", rows=6)

    def one_case(build_url):
        """每个用例用**自己的** stub 起服务，这样 stub 收到的请求数就是该用例的真实外发次数。"""
        with stub_gateway() as stub:
            port = stub.httpd.server_address[1]
            url = build_url(port)
            monkeypatch.setenv("CONDOPT_IMPORT_LLM_BASE_URL", url)
            with running_server(
                runs, import_llm=True, api_key_env="TEST_KEY", allow_roots=[tmp_path]
            ) as base:
                status, boot, _ = http_get(base, "/api/bootstrap")
                assert status == 200
                info = boot["import_llm"]
                status, scan, _ = api(base, "/api/import/scan", {"path": str(data)})
                assert status == 200
                status, payload, _ = api(
                    base, "/api/import/map", {"scan_id": scan["scan_id"], "consent": True}
                )
                return {
                    "url": url,
                    "url_valid": info["url_valid"],
                    "url_error": boot["import"]["url_error"],
                    "status": status,
                    "error": payload.get("error"),
                    "message": str(payload.get("message")),
                    "hits": len(stub.requests),
                    "endpoint": scan["sample_preview"]["endpoint"],
                }

    invalid_cases = [
        lambda port: f"http://127.0.0.1:{port}/v1 /v1",       # 空白
        lambda port: "http://127.0.0.1:notaport/v1",          # 坏端口
        lambda port: f"127.0.0.1:{port}/v1",                  # 无 scheme
        lambda port: f"ftp://127.0.0.1:{port}/v1",            # 非法 scheme
        lambda port: "http://例子.测试/v1",                    # 非 ASCII 主机（D5）
        lambda port: f"http://127.0.0.1:{port}/v1/chat/completions",  # 误拼端点
        lambda port: "",                                      # 显式置空
        lambda port: "   ",                                   # 纯空白
    ]
    for build in invalid_cases:
        case = one_case(build)
        assert case["url_valid"] is False, f"{case['url']!r} 应判为无效配置"
        assert case["url_error"], f"{case['url']!r} 应有 url_error"
        assert case["status"] == 503 and case["error"] == "E_LLM_UNAVAILABLE", case
        assert case["hits"] == 0, f"{case['url']!r} 配置错时不许外呼，实际 {case['hits']} 次"
        assert case["endpoint"] == "", f"{case['url']!r} 不该给假 endpoint"
        assert "CONDOPT_IMPORT_LLM_BASE_URL" in (case["message"] + str(case["url_error"]))

    valid_cases = [
        lambda port: f"http://127.0.0.1:{port}/v1",
        lambda port: f"http://127.0.0.1:{port}/v1/",
        lambda port: f"http://localhost:{port}/v1",
        lambda port: f"http://127.0.0.1:{port}",              # 不带 /v1：仍然能发（网关自定路径）
    ]
    for build in valid_cases:
        case = one_case(build)
        assert case["url_valid"] is True and case["url_error"] is None, case
        assert case["status"] == 200 and case["error"] is None, case
        assert case["hits"] >= 1, f"{case['url']!r} url_valid=true 却没有真的外发"
        assert case["endpoint"] == f"{case['url'].rstrip('/')}/chat/completions"

    # 对照：语法合法但连不上的地址 → url_valid=true（配置层没错），失败在运行期
    case = one_case(lambda port: "http://127.0.0.1:9/v1")
    assert case["url_valid"] is True and case["url_error"] is None
    assert case["status"] == 503 and "无法连接" in case["message"], case
    assert "配置不合法" not in case["message"]

    # punycode（ASCII 化的 IDNA 域名）在配置层同样有效；这里**只查 bootstrap**，
    # 不真的去解析该域名，保持整套测试零外网依赖。
    monkeypatch.setenv("CONDOPT_IMPORT_LLM_BASE_URL", "http://xn--fsqu00a.xn--0zwm56d/v1")
    with running_server(runs, import_llm=True, api_key_env="TEST_KEY", allow_roots=[tmp_path]) as base:
        status, boot, _ = http_get(base, "/api/bootstrap")
    assert status == 200
    assert boot["import_llm"]["url_valid"] is True
    assert boot["import"]["url_error"] is None
