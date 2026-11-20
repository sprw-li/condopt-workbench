"""验收公共工具（verifier 专用，只读实现物）。

不 import server.py 的 HTTP 处理路径，只通过**真实子进程 + 真实 socket**
驱动服务；唯一从实现物读取的是 condopt 自己的公共 API（prepare.template / registry），
用来构造合法 answers 与对照 Contract 的权威表。
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

VERIFY = Path(__file__).resolve().parent
REPO = VERIFY.parent          # 本仓库根：_verify/ 的上一级
WORKBENCH = REPO              # 兼容旧命名：脚本里以它称呼工作台仓库根
LOGS = VERIFY / "logs"
SHOTS = VERIFY / "shots"
RUNS = VERIFY / "_runs"

if str(WORKBENCH) not in sys.path:
    sys.path.insert(0, str(WORKBENCH))
import bridge  # noqa: E402,F401  （触发 condopt 外部依赖定位）

os.environ.setdefault("PYTHONUTF8", "1")

LOG_FILES: list[Path] = []


def log(text: str = "") -> None:
    print(text, flush=True)


def log_to(name: str, text: str) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    path = LOGS / name
    written = []
    for line in text.splitlines():
        written.append(line)
    path.write_text("\n".join(written) + "\n", encoding="utf-8")
    log(f"[log written] {path}")


class Report:
    """收集 PASS/FAIL/BLOCKED，最后统一打印与落盘。

    BLOCKED 与 FAIL 分开：**跑不了**（缺权限/缺外部依赖）不等于**跑了且不对**，
    但也不许写成 PASS。exit code 只反映 FAIL。
    """

    def __init__(self, title: str) -> None:
        self.title = title
        self.rows: list[tuple[str, bool, str]] = []
        self.blocked_rows: list[tuple[str, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((name, bool(ok), detail))
        log(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")
        return bool(ok)

    def blocked(self, name: str, detail: str = "") -> None:
        self.blocked_rows.append((name, detail))
        log(f"[BLOCKED] {name}  {detail}")

    def finish(self, log_name: str | None = None) -> int:
        failed = [row for row in self.rows if not row[1]]
        log(f"== {self.title}: {len(self.rows) - len(failed)}/{len(self.rows)} PASS"
            + (f"，{len(self.blocked_rows)} BLOCKED（未计为失败）" if self.blocked_rows else "")
            + " ==")
        if log_name:
            lines = [f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}" for name, ok, detail in self.rows]
            lines += [f"[BLOCKED] {name}  {detail}" for name, detail in self.blocked_rows]
            log_to(log_name, "\n".join(lines))
        return 1 if failed else 0


# --------------------------------------------------------------------------- #
# 服务进程
# --------------------------------------------------------------------------- #


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_port(port: int, timeout: float = 60.0, proc: subprocess.Popen | None = None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


class Server:
    """真实子进程服务器；上下文管理器保证退出。"""

    def __init__(
        self,
        runs_dir: Path,
        port: int | None = None,
        extra: list[str] | None = None,
        env: dict | None = None,
        drop_env: list[str] | None = None,
    ) -> None:
        self.port = port if port is not None else free_port()
        self.runs_dir = Path(runs_dir)
        self.base = f"http://127.0.0.1:{self.port}"
        self.extra_env = dict(env or {})
        self.drop_env = list(drop_env or [])
        cmd = [
            sys.executable,
            str(WORKBENCH / "server.py"),
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "--runs-dir", str(self.runs_dir),
        ] + list(extra or [])
        self.cmd = cmd
        self.proc: subprocess.Popen | None = None
        self.stdout_file = open(LOGS / f"server-{self.port}.log", "w", encoding="utf-8")

    def __enter__(self) -> "Server":
        child_env = {**os.environ, "PYTHONUTF8": "1", **self.extra_env}
        for key in self.drop_env:
            child_env.pop(key, None)
        self.proc = subprocess.Popen(
            self.cmd, cwd=str(REPO), stdout=self.stdout_file, stderr=subprocess.STDOUT,
            env=child_env,
        )
        atexit.register(self.stop)
        if not wait_port(self.port, timeout=90, proc=self.proc):
            raise RuntimeError(f"server 未在端口 {self.port} 上就绪；见 {self.stdout_file.name}")
        return self

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        try:
            self.stdout_file.flush()
        except Exception:
            pass

    def __exit__(self, *exc) -> None:
        self.stop()


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def http(base: str, method: str, path: str, body=None, timeout: float = 300.0, raw_body: bytes | None = None):
    """返回 (status, headers, text)。HTTPError 不抛。"""
    data = raw_body
    if data is None and body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8", "replace")


def get(base: str, path: str, timeout: float = 300.0):
    return http(base, "GET", path, timeout=timeout)


def action(base: str, act: str, run=None, payload=None, timeout: float = 300.0):
    body: dict = {"action": act}
    if run is not None:
        body["run"] = run
    if payload is not None:
        body["payload"] = payload
    return http(base, "POST", "/api/action", body=body, timeout=timeout)


def js(body) -> dict:
    return json.loads(body)


# --------------------------------------------------------------------------- #
# 契约权威表（从 CONTRACT.md §3.1 表格解析 —— 不手抄）
# --------------------------------------------------------------------------- #

CONTRACT = WORKBENCH / "CONTRACT.md"


def contract_stages() -> dict[str, dict]:
    """解析 CONTRACT.md §3.1 的五场景权威表（含序号、action、needs_run、stages）。

    | key | title | no | action | needs_run | stages（4 段…） |
    | `kickoff` | 开工建议 | 01 | `prepare` | false | `校验问卷答案` / … |

    **不硬编码场景数**：任何「6 列 + 首列是反引号标识符」的行都收进来，
    这样契约表从 5 行扩到 6 行（§9.6 新增 `import`）时本函数无需改动。
    解析结果必须非空，否则视为契约表结构变了、应当报错而不是静默当成通过。
    """
    rows: dict[str, dict] = {}
    for line in CONTRACT.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("|---") or "stages" in line.split("|")[1]:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 6:
            continue
        key_cell, title, no, act, needs_run, stages_cell = cells
        if not (key_cell.startswith("`") and key_cell.endswith("`")):
            continue
        rows[key_cell.strip("`")] = {
            "title": title,
            "no": no,
            "action": act.strip("`"),
            "needs_run": needs_run == "true",
            "stages": [item.strip().strip("`") for item in stages_cell.split("/")],
        }
    if len(rows) < 5:
        raise RuntimeError(f"CONTRACT §3.1 只解析出 {len(rows)} 行，契约表结构可能变了：{list(rows)}")
    return rows


# --------------------------------------------------------------------------- #
# answers / run 目录
# --------------------------------------------------------------------------- #

import condopt.prepare as condopt_prepare  # noqa: E402
from condopt import registry as condopt_registry  # noqa: E402


def short_answers(**overrides) -> dict:
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
    return {
        str(path.relative_to(run_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(Path(run_dir).rglob("*"))
        if path.is_file()
    }


def fresh(name: str) -> Path:
    """一个空的、只属于本次验收的 runs 目录。"""
    import shutil

    path = RUNS / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def init_study(base: str, run_id: str = "demo", algorithm: str = "sobol", **payload):
    body = {"id": run_id, "answers": short_answers(), "algorithm": algorithm, "budget": 8, "batch": 2}
    body.update(payload)
    status, _, text = action(base, "init", payload=body)
    if status != 200:
        raise RuntimeError(f"init 失败 {status}: {text[:400]}")
    return js(text)


def ask(base: str, run_id: str = "demo", n: int = 4):
    status, _, text = action(base, "ask", run=run_id, payload={"n": n})
    if status != 200:
        raise RuntimeError(f"ask 失败 {status}: {text[:400]}")
    return js(text)
