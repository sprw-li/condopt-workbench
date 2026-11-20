"""三个缺陷的最小复现（每条独立、可用 `py -3 _verify/repro_min.py` 一键重放）。

F1  init id="NUL"            → 500 E_INTERNAL（应为 4xx）
F2  init id="L"*300          → 500 E_INTERNAL OSError WinError 123（应为 4xx）
F3  SSE action 抛错路径     → done.status == "running"（应为 "error"）
"""

from __future__ import annotations

import http.client
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import Server, action, ask, fresh, get, init_study, js, log, short_answers  # noqa: E402


def f1() -> None:
    log("\n=== F1: init id='NUL' ===")
    with Server(fresh("repro_f1")) as server:
        status, _, text = action(server.base, "init", payload={"id": "NUL", "answers": short_answers()})
        log(f"POST /api/action {{\"action\":\"init\",\"payload\":{{\"id\":\"NUL\",...}}}}")
        log(f"→ HTTP {status} {text.strip()[:240]}")
        log(f"期望：400/422（客户端输入非法）；实测：{status}")


def f2() -> None:
    log("\n=== F2: init id=300 字符 ===")
    with Server(fresh("repro_f2")) as server:
        status, _, text = action(server.base, "init", payload={"id": "L" * 300, "answers": short_answers()})
        log('POST /api/action {"action":"init","payload":{"id":"L"*300,...}}')
        log(f"→ HTTP {status} {text.strip()[:240]}")
        log(f"期望：400/422（客户端输入非法）；实测：{status}")


def f3() -> None:
    log("\n=== F3: SSE error 路径的 done.status ===")
    with Server(fresh("repro_f3")) as server:
        init_study(server.base, run_id="demo")
        ask(server.base, run_id="demo", n=1)  # 制造 pending → 下一次 ask 必然 E_CONFLICT
        raw = json.dumps({"scenario": "sample", "payload": {"run": "demo", "n": 1}}).encode()
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=60)
        conn.request("POST", "/api/run", body=raw,
                     headers={"Content-Type": "application/json", "Content-Length": str(len(raw))})
        text = conn.getresponse().read().decode("utf-8")
        conn.close()
        log("POST /api/run {\"scenario\":\"sample\",\"payload\":{\"run\":\"demo\",\"n\":1}}（pending 未回填）")
        for line in text.splitlines():
            if line.startswith("event: ") or (line.startswith("data: ") and ("error" in line or "status" in line)):
                log(f"  {line}")
        last = [json.loads(line[len("data: "):]) for line in text.splitlines()
                if line.startswith("data: ")][-1]
        log(f"末帧 done.status={last.get('status')!r}（期望 'error'）")


if __name__ == "__main__":
    f1()
    f2()
    f3()
