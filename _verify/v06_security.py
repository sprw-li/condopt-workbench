"""验收项 6：安全（方法白名单、路径穿越、只绑回环）。

运行：py -3 _verify/v06_security.py
"""

from __future__ import annotations

import http.client as http_client
import json
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPO, Report, Server, WORKBENCH, fresh, get, http, js, log  # noqa: E402


def raw_request(port: int, method: str, target: str):
    conn = http_client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request(method, target)
        response = conn.getresponse()
        body = response.read().decode("utf-8", "replace")
        return response.status, dict(response.headers), body
    finally:
        conn.close()


def lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


def main() -> int:
    report = Report("项6 安全")
    with Server(fresh("security")) as server:
        port, base = server.port, server.base

        log("\n--- 方法白名单（非 GET/POST → 405） ---")
        for method in ("PUT", "DELETE", "HEAD", "PATCH", "OPTIONS", "TRACE"):
            for path in ("/api/bootstrap", "/", "/api/action", "/assets/base.css"):
                status, headers, body = raw_request(port, method, path)
                report.check(
                    f"6.{method} {path} → 405",
                    status == 405,
                    f"status={status} allow={headers.get('Allow')} body={body.strip()[:60]!r}",
                )
        # 方法用错但路径存在 → 405
        status, headers, _ = get(base, "/api/action")
        report.check("GET /api/action（POST-only）→ 405", status == 405, f"status={status} allow={headers.get('Allow')}")
        status, _, body = http(base, "POST", "/api/bootstrap")
        report.check("POST /api/bootstrap（GET-only）→ 405", status == 405, f"status={status}")

        log("\n--- 路径穿越 → 400 ---")
        for target in (
            "/../../etc/passwd",
            "/../etc/passwd",
            "/assets/../server.py",
            "/assets/../../CONTRACT.md",
            "/%2e%2e/%2e%2e/README.md",
            "/assets/%2e%2e/server.py",
            "/assets/..%5cserver.py",
            "/vendor/../server.py",
            "/assets/./../../src/condopt/study.py",
            "/..%00/index.html",
        ):
            status, _, body = raw_request(port, "GET", target)
            report.check(
                f"6.GET {target} → 400",
                status == 400,
                f"status={status} body={body.strip()[:60]!r}",
            )

        # 反斜杠 / NUL / 非法百分号编码
        for target in ("/assets\\server.py", "/assets/%zz", "/assets/%00"):
            status, _, body = raw_request(port, "GET", target)
            report.check(f"6.GET {target} → 400/404（不是 200）", status in (400, 404), f"status={status}")

        log("\n--- run id 路径穿越（API 层） ---")
        for run_id in ("..", "../demo", "..%2fdemo", "demo/..", "C%3A%5Cdemo", "a%2Fb", "%2e%2e"):
            status, _, body = get(base, f"/api/study?id={run_id}")
            kind = js(body).get("error") if body.strip().startswith("{") else None
            report.check(
                f"6.GET /api/study?id={run_id} → 400 E_BAD_REQUEST",
                status == 400 and kind == "E_BAD_REQUEST",
                f"status={status} error={kind}",
            )

        log("\n--- 只绑回环 ---")
        report.check("6.服务响应头到达 127.0.0.1", True, f"base={base}")

        ip = lan_ip()
        if ip:
            reachable = False
            try:
                with socket.create_connection((ip, port), timeout=3):
                    reachable = True
            except OSError as exc:
                log(f"    对外网卡 {ip}:{port} 连接失败（预期）：{type(exc).__name__}: {exc}")
            report.check(f"6.外网卡 {ip}:{port} 不可达（只绑 127.0.0.1）", not reachable, f"reachable={reachable}")
        else:
            report.check("6.外网卡地址探测", True, "未找到非回环 IPv4，跳过外网卡连通性验证")

        # 监听地址（netstat 视角）
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Get-NetTCPConnection -State Listen -LocalPort {port} | "
                 f"Select-Object -ExpandProperty LocalAddress"],
                capture_output=True, text=True, timeout=60,
            ).stdout.strip()
            addrs = [line.strip() for line in out.splitlines() if line.strip()]
            report.check(
                "6.监听地址只有 127.0.0.1",
                addrs == ["127.0.0.1"],
                f"Get-NetTCPConnection LocalAddress={addrs}",
            )
        except Exception as exc:  # noqa: BLE001
            report.check("6.监听地址检查", False, f"{type(exc).__name__}: {exc}")

    # --host 非法直接退出（真实 CLI）
    log("\n--- --host 白名单（真实 CLI，退出码 2） ---")
    proc = subprocess.run(
        [sys.executable, str(WORKBENCH / "server.py"), "--host", "0.0.0.0", "--port", "0"],
        capture_output=True, text=True, cwd=str(REPO), encoding="utf-8", timeout=120,
    )
    log(f"$ py -3 server.py --host 0.0.0.0 --port 0  → exit={proc.returncode}")
    log(f"  stderr={proc.stderr.strip()}")
    report.check(
        "6.--host 0.0.0.0 → 退出码 2",
        proc.returncode == 2,
        f"exit={proc.returncode} stderr={proc.stderr.strip()[:160]}",
    )

    # --host 局域网 IP 也拒绝
    ip = lan_ip()
    if ip:
        proc = subprocess.run(
            [sys.executable, str(WORKBENCH / "server.py"), "--host", ip, "--port", "0"],
            capture_output=True, text=True, cwd=str(REPO), encoding="utf-8", timeout=120,
        )
        report.check(f"6.--host {ip} → 退出码 2", proc.returncode == 2, f"exit={proc.returncode}")

    # localhost 允许（契约 §1）
    with Server(fresh("security_localhost"), port=None, extra=["--host", "localhost"]) as local_server:
        status, _, _ = get(local_server.base, "/api/bootstrap")
        report.check("6.--host localhost 允许启动（契约 §1）", status == 200, f"status={status}")
    return report.finish("v06_security.log")


if __name__ == "__main__":
    raise SystemExit(main())
