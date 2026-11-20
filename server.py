"""condopt 工作台 · 本地 HTTP 服务（stdlib only）。

唯一真源契约：``CONTRACT.md``（冻结 2026-11-19）。安全线沿袭
``scripts/serve.py``：只绑 ``127.0.0.1``、无鉴权、无外网请求、不解析 CLI 文本。

用法::

    py -3 server.py [--host 127.0.0.1] [--port 8770] [--runs-dir PATH] [--open]
    py -3 server.py --port 0 --selftest     # 起服务→自检→退出（退出码 0）

分层（docs/ui/FEASIBILITY.md §4.1）：本文件只做 HTTP 与静态资源；一切 condopt 调用
都在 :mod:`bridge` 里进程内完成，runs 目录是唯一真源。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, unquote, urlsplit

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import bridge as bridge_module  # noqa: E402  (bridge.py)
from bridge import (  # noqa: E402
    E_BAD_REQUEST,
    E_INTERNAL,
    E_NOT_FOUND,
    E_VALIDATION,
    SCENARIOS,
    SCENARIOS_BY_KEY,
    Bridge,
    WorkbenchError,
    _display,
    _display_list,
    json_dumps,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8770
ALLOWED_HOSTS = ("127.0.0.1", "localhost")
WEB_ROOT = _HERE / "web"
STATIC_PREFIXES = ("/assets/", "/vendor/", "/components/")
GET_EXACT = ("/", "/api/bootstrap", "/api/runs", "/api/study")
POST_EXACT = ("/api/action", "/api/run", "/api/import/scan", "/api/import/map", "/api/import/apply")
MAX_BODY_BYTES = 8 * 1024 * 1024

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".map": "application/json; charset=utf-8",
}


def _internal_error(exc: BaseException) -> WorkbenchError:
    """500 的 message 不回显 Python 异常文本（task-4 F1）；细节只写服务端 stderr。"""
    sys.stderr.write(f"[workbench] 内部错误：{type(exc).__name__}: {exc}\n")
    sys.stderr.flush()
    return WorkbenchError(
        E_INTERNAL,
        f"服务端内部错误（{type(exc).__name__}）",
        "看服务端日志；这通常是 bug，请带复现步骤上报",
    )


def content_type_for(path: Path) -> str:
    if path.suffix.lower() in _CONTENT_TYPES:
        return _CONTENT_TYPES[path.suffix.lower()]
    guessed = mimetypes.guess_type(str(path))[0]
    if guessed is None:
        return "application/octet-stream"
    if guessed.startswith("text/") and "charset" not in guessed:
        return f"{guessed}; charset=utf-8"
    return guessed


class _WorkbenchHTTPServer(ThreadingHTTPServer):
    """带 bridge 引用的线程服务器；连接被浏览器掐断时不打 traceback。"""

    daemon_threads = True
    allow_reuse_address = True

    bridge: Bridge
    web_root: Path

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001 - stdlib 签名
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class WorkbenchHandler(BaseHTTPRequestHandler):
    """路由：``GET /`` + 静态白名单 + ``/api/*``；非 GET/POST 一律 405。"""

    server_version = "condopt-1.0"
    protocol_version = "HTTP/1.1"

    # -- 基础设施 -----------------------------------------------------------
    @property
    def bridge(self) -> Bridge:
        return self.server.bridge  # type: ignore[attr-defined]

    @property
    def web_root(self) -> Path:
        return self.server.web_root  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib 名字
        if getattr(self.server, "verbose", False):
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def _send_bytes(
        self,
        status: int,
        content_type: str,
        body: bytes,
        extra: dict | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_text(self, status: int, text: str, extra: dict | None = None) -> None:
        self._send_bytes(status, "text/plain; charset=utf-8", text.encode("utf-8"), extra)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json_dumps(payload).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", body)

    def _send_error(self, exc: WorkbenchError) -> None:
        self._send_json(exc.status, exc.to_dict())

    def _send_unknown(self, path: str) -> None:
        error = WorkbenchError(
            E_NOT_FOUND,
            f"未知路径: {_display(path)}",
            "可用: /、/index.html、/assets/*、/vendor/*、/components/*、/api/bootstrap、"
            "/api/runs、/api/study、POST /api/action、POST /api/run、"
            "POST /api/import/scan /map /apply",
        )
        if path.startswith("/api/"):
            self._send_error(error)
        else:
            self._send_text(HTTPStatus.NOT_FOUND, f"404 not found：{_display(path)}\n")

    def _method_not_allowed(self, allow: str = "GET, POST") -> None:
        self._send_text(
            HTTPStatus.METHOD_NOT_ALLOWED,
            f"405 method not allowed：{self.command}（只接受 {allow}）\n",
            {"Allow": allow},
        )

    # -- 路径安全 -----------------------------------------------------------
    def _safe_path(self) -> str | None:
        """解码并拒绝穿越；返回 None 表示 400。"""
        raw_path = urlsplit(self.path).path
        try:
            decoded = unquote(raw_path, errors="strict")
        except UnicodeDecodeError:
            return None
        if (
            not decoded.startswith("/")
            or "\\" in decoded
            or "\x00" in decoded
            or any(part == ".." for part in decoded.split("/"))
        ):
            return None
        return decoded

    # -- 静态资源 -----------------------------------------------------------
    def _serve_file(self, candidate: Path) -> None:
        try:
            resolved = candidate.resolve()
        except OSError:
            self._send_text(HTTPStatus.NOT_FOUND, "404 not found\n")
            return
        root = self.web_root.resolve()
        if resolved != root and root not in resolved.parents:
            self._send_text(HTTPStatus.BAD_REQUEST, "400 bad path：资源必须先出白名单目录\n")
            return
        if not resolved.is_file():
            self._send_text(HTTPStatus.NOT_FOUND, f"404 not found：{resolved.name}\n")
            return
        self._send_bytes(
            HTTPStatus.OK,
            content_type_for(resolved),
            resolved.read_bytes(),
        )

    # -- GET ---------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - stdlib 名字
        path = self._safe_path()
        if path is None:
            self._send_text(HTTPStatus.BAD_REQUEST, "400 bad path\n")
            return
        if path in ("/", "/index.html"):
            index = self.web_root / "index.html"
            if not index.is_file():
                self._send_text(
                    HTTPStatus.NOT_FOUND,
                    "404 not found：web/index.html 尚未就绪（前端页面未落地）\n",
                )
                return
            self._serve_file(index)
            return
        if path.startswith(STATIC_PREFIXES):
            self._serve_file(self.web_root / path.lstrip("/"))
            return
        if path == "/api/bootstrap":
            self._send_json(HTTPStatus.OK, self.bridge.bootstrap())
            return
        if path == "/api/runs":
            self._send_json(HTTPStatus.OK, {"ok": True, "runs": self.bridge.list_runs()})
            return
        if path == "/api/study":
            query = parse_qs(urlsplit(self.path).query)
            run_id = (query.get("id") or [None])[0]
            try:
                snapshot = self.bridge.study_snapshot(run_id)
            except WorkbenchError as exc:
                self._send_error(exc)
                return
            except Exception as exc:  # noqa: BLE001
                self._send_error(_internal_error(exc))
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "study": snapshot})
            return
        if path in POST_EXACT:
            self._method_not_allowed("POST")
            return
        self._send_unknown(path)

    # -- POST --------------------------------------------------------------
    def _read_json_body(self) -> dict:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise WorkbenchError(E_BAD_REQUEST, "缺少 Content-Length", "请求体必须是 JSON 对象")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise WorkbenchError(E_BAD_REQUEST, f"非法 Content-Length: {raw_length!r}") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise WorkbenchError(E_BAD_REQUEST, f"请求体过大或非法: {length} bytes")
        raw = self.rfile.read(length) if length else b""
        if not raw.strip():
            raise WorkbenchError(E_BAD_REQUEST, "空请求体", "至少提供 scenario/action")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkbenchError(E_BAD_REQUEST, f"JSON 解析失败: {exc}", "请求体必须是 UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise WorkbenchError(E_BAD_REQUEST, "请求体必须是 JSON 对象")
        return payload

    def do_POST(self) -> None:  # noqa: N802 - stdlib 名字
        path = self._safe_path()
        if path is None:
            self._send_text(HTTPStatus.BAD_REQUEST, "400 bad path\n")
            return
        if path == "/api/action":
            self._handle_action()
            return
        if path == "/api/run":
            self._handle_run()
            return
        if path == "/api/import/scan":
            self._handle_import(
                self.bridge.scan_import, {"path", "sample_rows", "model"}, pass_path=True
            )
            return
        if path == "/api/import/map":
            self._handle_import(self.bridge.map_import, {"scan_id", "consent", "sample_rows", "model", "fallback"})
            return
        if path == "/api/import/apply":
            self._handle_import(
                self.bridge.apply_import,
                {"id", "path", "scan_id", "mapping", "algorithm", "params", "budget", "batch", "reason",
                 "count_toward_budget"},
            )
            return
        if path in GET_EXACT or path.startswith(STATIC_PREFIXES):
            self._method_not_allowed("GET")
            return
        self._send_unknown(path)

    def _handle_action(self) -> None:
        try:
            body = self._read_json_body()
            allowed = {"action", "run", "payload"}
            unknown = sorted(key for key in body if key not in allowed)
            if unknown:
                raise WorkbenchError(
                    E_VALIDATION,
                    f"请求体未知字段: {_display_list(unknown)}",
                    f"允许字段: {sorted(allowed)}",
                )
            action = body.get("action")
            if not isinstance(action, str) or not action:
                raise WorkbenchError(
                    E_BAD_REQUEST,
                    "缺少 action",
                    "可用: prepare/init/ask/tell/step/recommend/delivery/override/render/report",
                )
            message, result = self.bridge.dispatch(action, body.get("run"), body.get("payload"))
        except WorkbenchError as exc:
            self._send_error(exc)
            return
        except Exception as exc:  # noqa: BLE001 - 任何未预期异常都是 500，不掀翻服务
            self._send_error(_internal_error(exc))
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "action": action, "message": message, "result": result})

    # -- 导入已有数据（CONTRACT §9）----------------------------------------
    def _handle_import(self, func, allowed: set, pass_path: bool = False) -> None:
        """三个导入端点共用的信封校验（未知字段不静默忽略）+ 错误映射。"""
        try:
            body = self._read_json_body()
            unknown = sorted(key for key in body if key not in allowed)
            if unknown:
                raise WorkbenchError(
                    E_VALIDATION,
                    f"请求体未知字段: {_display_list(unknown)}",
                    f"允许字段: {sorted(allowed)}",
                )
            if pass_path:
                path = body.get("path")
                if not isinstance(path, str) or not path.strip():
                    raise WorkbenchError(
                        E_VALIDATION, "scan 需要 path（文件路径）", '例如 {"path": "data/lab_log.csv"}'
                    )
                # scan 的可选字段：sample_rows（§9.3 的 sample_preview）、model（让预览与随后
                # map 用同一个模型，保证 §9.3 的字节数不变量成立）。
                kwargs = {key: body[key] for key in ("sample_rows", "model") if key in body}
                result = func(path, **kwargs)
            else:
                result = func(body)
        except WorkbenchError as exc:
            self._send_error(exc)
            return
        except Exception as exc:  # noqa: BLE001 - 未预期异常仍是 500，但 message 不回显异常文本
            self._send_error(_internal_error(exc))
            return
        self._send_json(HTTPStatus.OK, result)

    # -- SSE ---------------------------------------------------------------
    def _sse_open(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _sse_emit(self, event: str, data: dict) -> None:
        frame = f"event: {event}\ndata: {json_dumps(data)}\n\n".encode("utf-8")
        self.wfile.write(frame)
        self.wfile.flush()

    def _handle_run(self) -> None:
        """EduKit 兼容的流式通道；帧格式见契约 §4。"""
        try:
            body = self._read_json_body()
            allowed = {"scenario", "text", "files", "payload"}
            unknown = sorted(key for key in body if key not in allowed)
            if unknown:
                raise WorkbenchError(
                    E_VALIDATION,
                    f"请求体未知字段: {_display_list(unknown)}",
                    f"允许字段: {sorted(allowed)}",
                )
            scenario = body.get("scenario")
            if not isinstance(scenario, str) or scenario not in SCENARIOS_BY_KEY:
                raise WorkbenchError(
                    E_BAD_REQUEST,
                    f"未知 scenario: {_display(scenario)!r}",
                    f"可用: {', '.join(SCENARIOS_BY_KEY)}",
                )
            if "text" in body and body["text"] is not None and not isinstance(body["text"], str):
                raise WorkbenchError(E_VALIDATION, "text 必须是字符串")
            if "files" in body and body["files"] is not None and not isinstance(body["files"], list):
                raise WorkbenchError(E_VALIDATION, "files 必须是数组")
            payload = body.get("payload") or {}
            if not isinstance(payload, dict):
                raise WorkbenchError(E_VALIDATION, "payload 必须是 JSON 对象")
            # 预校验（可能立刻返回 4xx JSON）：scenario 与基本字段错误不进入流。
            self.bridge.validate_run_request(scenario, payload)
        except WorkbenchError as exc:
            self._send_error(exc)
            return
        except Exception as exc:  # noqa: BLE001
            self._send_error(_internal_error(exc))
            return

        self._sse_open()
        try:
            for event, data in self.bridge.stream_scenario(scenario, payload):
                self._sse_emit(event, data)
        except WorkbenchError as exc:  # 理论上已被预校验拦住；保住「最后仍发 done」
            try:
                self._sse_emit("error", {"message": exc.message, **({"hint": exc.hint} if exc.hint else {})})
                self._sse_emit("done", {"run_id": "error", "elapsed_ms": 0, "mode": "live", "status": "error"})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001 - 页面不崩：error 帧 + done 帧
            try:
                self._sse_emit("error", {"message": f"{type(exc).__name__}: {exc}", "hint": "看服务端日志"})
                self._sse_emit("done", {"run_id": "error", "elapsed_ms": 0, "mode": "live", "status": "error"})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.close_connection = True

    # -- 其它方法一律 405 ---------------------------------------------------
    def do_PUT(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_DELETE(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PATCH(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_HEAD(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_TRACE(self) -> None:  # noqa: N802
        self._method_not_allowed()


def create_server(
    runs_dir: str | Path | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    web_root: str | Path | None = None,
    bridge: Bridge | None = None,
    verbose: bool = False,
    import_llm: bool = False,
    allow_import_roots: Iterable[str | Path] | None = None,
    max_import_bytes: int | None = None,
) -> _WorkbenchHTTPServer:
    """创建（不启动）工作台服务器。``port=0`` 交给系统分配；读 ``server_address`` 拿端口。"""
    instance = bridge or Bridge(
        runs_dir,
        import_llm=import_llm,
        allow_import_roots=allow_import_roots,
        max_import_bytes=max_import_bytes,
    )
    httpd = _WorkbenchHTTPServer((host, int(port)), WorkbenchHandler)
    httpd.bridge = instance  # type: ignore[attr-defined]
    httpd.web_root = Path(web_root).resolve() if web_root else WEB_ROOT  # type: ignore[attr-defined]
    httpd.verbose = verbose  # type: ignore[attr-defined]
    httpd.daemon_threads = True
    return httpd


# --------------------------------------------------------------------------- #
# 启动 / 自检
# --------------------------------------------------------------------------- #


def _selftest_requests(base: str, httpd: _WorkbenchHTTPServer) -> list[tuple[str, bool, str]]:
    """在已启动的服务上跑一遍契约 §7 的自检；返回 (名字, 是否通过, 说明)。"""
    import urllib.error
    import urllib.request

    results: list[tuple[str, bool, str]] = []

    def http(method: str, url: str, body: dict | None = None, timeout: float = 60.0):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")

    # 1) bootstrap：algorithms / prepare_template 必须来自代码
    status, text = http("GET", f"{base}/api/bootstrap")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = {}
    algorithms = (payload.get("condopt") or {}).get("algorithms") or []
    template = payload.get("prepare_template") or {}
    scenarios = payload.get("scenarios") or []
    ok = (
        status == 200
        and payload.get("ok") is True
        and len(algorithms) == len(bridge_module.condopt_registry.describe_all())
        and "variables" in template
        and [item.get("key") for item in scenarios] == [item["key"] for item in SCENARIOS]
    )
    results.append(("GET /api/bootstrap", ok, f"status={status} algorithms={len(algorithms)} scenarios={len(scenarios)}"))

    # 2) /api/runs
    status, text = http("GET", f"{base}/api/runs")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = {}
    results.append(("GET /api/runs", status == 200 and payload.get("ok") is True and isinstance(payload.get("runs"), list), f"status={status}"))

    # 3) 静态首页（前端未落地时降级为「只验证 404 语义」，因为后端路由另有 4/5/6/7 项验证）
    index = Path(httpd.web_root) / "index.html"  # type: ignore[attr-defined]
    status, text = http("GET", f"{base}/")
    if index.is_file():
        lowered = text[:2048].lower()
        results.append(("GET /", status == 200 and ("<html" in lowered or "<!doctype" in lowered), f"status={status}"))
    else:
        results.append(("GET / （web/index.html 未落地，只验证 404 语义）", status == 404, f"status={status}"))

    # 4) 非 GET/POST → 405
    status, _ = http("PUT", f"{base}/api/runs")
    results.append(("PUT /api/runs → 405", status == 405, f"status={status}"))

    # 5) 路径穿越 → 400（用裸 socket 发未归一化的 request-target）
    import http.client as http_client

    conn = http_client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
    conn.request("GET", "/../etc/passwd")
    traversal_status = conn.getresponse().status
    conn.close()
    results.append(("GET /../etc/passwd → 400", traversal_status == 400, f"status={traversal_status}"))

    # 6) 未知路径 → 404
    status, _ = http("GET", f"{base}/api/nope")
    results.append(("GET /api/nope → 404", status == 404, f"status={status}"))

    # 7) SSE：kickoff（只读，不落盘）
    body = {"scenario": "kickoff", "payload": {"answers": bridge_module.condopt_prepare.template()}}
    status, text = http("POST", f"{base}/api/run", body)
    events = [line[len("event: "):].strip() for line in text.splitlines() if line.startswith("event: ")]
    indices = []
    for line in text.splitlines():
        if line.startswith("data: "):
            try:
                data = json.loads(line[len("data: "):])
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and "index" in data:
                indices.append(data["index"])
    n_stages = len(SCENARIOS_BY_KEY["kickoff"]["stages"])
    ok = (
        status == 200
        and events[:1] == ["meta"]
        and events[-1:] == ["done"]
        and "stage" in events
        and "delta" in events
        and bool(indices)
        and all(0 <= index < n_stages for index in indices)
    )
    results.append(("POST /api/run kickoff（SSE 帧）", ok, f"status={status} events={events} indices={sorted(set(indices))}"))

    # 8) 导入端点（默认配置）：只验证装配与白名单，**不得出网**
    status, text = http("POST", f"{base}/api/import/scan", {"path": "CONTRACT.md"})
    results.append(("POST /api/import/scan 错误后缀 → 400", status == 400 and "后缀" in text, f"status={status}"))
    status, text = http("POST", f"{base}/api/import/map", {"scan_id": "deadbeefdeadbeef", "consent": True})
    results.append(("POST /api/import/map 无效 scan_id → 400（开流前，不出网）", status == 400, f"status={status}"))
    return results


def selftest(
    host: str = DEFAULT_HOST,
    port: int = 0,
    runs_dir: str | Path | None = None,
    web_root: str | Path | None = None,
    verbose: bool = False,
    **bridge_options,
) -> int:
    """起服务 → 自检 → 退出。返回退出码（0=通过）。绝不阻塞在 serve_forever 上。"""
    httpd = create_server(
        runs_dir=runs_dir, host=host, port=port, web_root=web_root, verbose=verbose, **bridge_options
    )
    bound_host, bound_port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{bound_port}"
    print(f"[selftest] 服务已启动：{base}（web_root={httpd.web_root}）")  # type: ignore[attr-defined]
    failures = 0
    checks: list[tuple[str, bool, str]] = []
    try:
        started = time.monotonic()
        checks = _selftest_requests(base, httpd)
        for name, ok, detail in checks:
            print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")
            if not ok:
                failures += 1
        print(f"[selftest] 完成：{len(checks) - failures}/{len(checks)} 项通过，"
              f"用时 {time.monotonic() - started:.1f}s")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    return 0 if failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="server",
        description="condopt 工作台服务（stdlib，仅 127.0.0.1）",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="只接受 127.0.0.1 / localhost")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口（0=自动分配）")
    parser.add_argument("--runs-dir", default=None, help="study 根目录（默认 state/runs）")
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    parser.add_argument("--selftest", action="store_true", help="起服务自检后退出（退出码 0 表示通过）")
    parser.add_argument("--verbose", action="store_true", help="打印访问日志")
    parser.add_argument(
        "--import-llm", action="store_true",
        help="允许 POST /api/import/map 调模型读表（默认关闭；还需环境变量密钥）",
    )
    parser.add_argument(
        "--allow-import-root", action="append", default=None,
        help="可导入文件的允许根（可多次；默认仓库根 + runs-dir）",
    )
    parser.add_argument(
        "--max-import-bytes", type=int, default=None,
        help="导入文件大小上限（字节，默认 20MB）",
    )
    args = parser.parse_args(argv)

    if args.host not in ALLOWED_HOSTS:
        print(
            f"错误：--host 只接受 {' / '.join(ALLOWED_HOSTS)}（工作台无鉴权，不得对外监听），"
            f"收到 {args.host!r}",
            file=sys.stderr,
        )
        return 2

    bridge_options = {
        "import_llm": args.import_llm,
        "allow_import_roots": args.allow_import_root,
        "max_import_bytes": args.max_import_bytes,
    }
    if args.selftest:
        return selftest(
            host=args.host,
            port=args.port,
            runs_dir=args.runs_dir,
            verbose=args.verbose,
            **bridge_options,
        )

    try:
        httpd = create_server(
            runs_dir=args.runs_dir,
            host=args.host,
            port=args.port,
            verbose=args.verbose,
            **bridge_options,
        )
    except OSError as exc:
        print(f"错误：无法监听 {args.host}:{args.port}（{exc}）", file=sys.stderr)
        return 1

    bound_host, bound_port = httpd.server_address[:2]
    url = f"http://{bound_host}:{bound_port}/"
    print("condopt 工作台已启动：")
    print(f"  URL      : {url}")
    print(f"  runs-dir : {httpd.bridge.runs_dir}")  # type: ignore[attr-defined]
    print(f"  web-root : {httpd.web_root}")  # type: ignore[attr-defined]
    settings = httpd.bridge.import_settings()  # type: ignore[attr-defined]
    print(
        "  导入     : 模型读表 "
        + ("已启用" if settings["enabled"] else "未启用（--import-llm 关闭）")
        + f"，密钥 {'已配置' if settings['configured'] else '未配置'}（{settings['key_env']}）"
    )
    print(f"  规则     : 只绑 {bound_host}、仅 GET/POST、无鉴权；除显式开启的导入读表外无外网请求")
    print("  Ctrl+C 停止")
    if args.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
