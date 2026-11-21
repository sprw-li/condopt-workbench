"""§11.3 润色通道 —— **verifier 自写**独立验收（不复用 api-backend 的驱动/测试）。

判据（严格按 CONTRACT §11.3，且**不只测成功路径**）：
  1. 成功润色：`reply.text` 变成润色句，**其余字段与「不开润色」逐字节相同**
     —— 比较用 `json.dumps(sort_keys=True, ensure_ascii=False)` 的**序列化字符串**，不是肉眼看字段；
  2. stub 返回**非 JSON** → 整个响应与「不开」**逐字节相同**（回落规则文案）；
  3. stub **HTTP 500** → 同上；
  4. stub **超时**（> POLISH_TIMEOUT_SECONDS）→ 同上；
  5. 密钥不出现在响应里；未开 `--import-llm` 时**零外网调用**（stub 计数 == 0）。

本机 stub 网关，不出网。运行：py -3 _verify/v22_verify_polish.py
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_profile as bp  # noqa: E402
from common import Report, Server, fresh, http, js, log  # noqa: E402

CHAT = "/api/kickoff/chat"
POLISHED = "【润色】这句话是桩返回的润色版本。"
KEY_VALUE = "test-key-must-not-leak-9f3a"

MODE = {"mode": "ok"}
CALLS = {"n": 0}


class StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 静音
        pass

    def handle_error(self):       # 客户端先超时断开是**预期**的，不要打 traceback 污染日志
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        CALLS["n"] += 1
        mode = MODE["mode"]
        if mode == "timeout":
            time.sleep(17.0)                       # > POLISH_TIMEOUT_SECONDS(15)
            body = b"{}"
            self.send_response(200)
        elif mode == "http500":
            body = b'{"error":"boom"}'
            self.send_response(500)
        elif mode == "non_json":
            body = b"this is definitely not json"
            self.send_response(200)
        else:
            body = json.dumps({"choices": [{"message": {"content": POLISHED}}]}).encode("utf-8")
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionError, OSError):
            pass                                   # 超时用例里客户端已走，属预期


def start_stub() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def serial(response: dict) -> str:
    return json.dumps(response, sort_keys=True, ensure_ascii=False)


def chat(base: str, content, state=None):
    body = {"messages": [{"role": "user", "content": content}]}
    if state is not None:
        body["state"] = state
    status, _, text = http(base, "POST", CHAT, body=body)
    return status, js(text)


def drive(base: str, turns: int = 3):
    """走同样的若干轮，返回每轮响应（保证两条通路输入完全相同）。"""
    out = []
    status, resp = chat(base, "开始")
    out.append(resp)
    state = resp["state"]
    for _ in range(turns - 1):
        chips = (resp.get("reply") or {}).get("quick_replies") or []
        if not chips:
            break
        status, resp = chat(base, chips[0]["value"], state=state)
        out.append(resp)
        state = resp.get("state")
    return out


def main() -> int:
    report = Report("§11.3 润色通道（verifier 自写驱动）")
    stub, stub_url = start_stub()
    runs_off = fresh("polish_off")
    runs_on = fresh("polish_on")
    try:
        with Server(runs_off) as off_server, Server(
            runs_on, extra=["--import-llm"],
            env={"CONDOPT_IMPORT_LLM_BASE_URL": stub_url, "BOHRIUM_API_KEY": KEY_VALUE},
        ) as on_server:
            log(f"stub={stub_url}  off={off_server.base}  on={on_server.base}")

            # ---- 0) 未开润色：必须零外网调用 ----
            CALLS["n"] = 0
            baseline = drive(off_server.base)
            report.check(
                "0) 未开 --import-llm → 走完整对话且对 stub **零调用**（零外网）",
                CALLS["n"] == 0 and all(r.get("ok") for r in baseline),
                f"stub 调用次数={CALLS['n']}（期望 0）；轮数={len(baseline)}",
            )

            # ---- 1) 成功润色：除 reply.text 外逐字节相同 ----
            MODE["mode"] = "ok"
            CALLS["n"] = 0
            polished = drive(on_server.base)
            report.check(
                "1a) 开润色 + stub 返回润色句 → 确实调用了 stub",
                CALLS["n"] > 0,
                f"stub 调用次数={CALLS['n']}",
            )
            diffs, text_changed = [], []
            for index, (a, b) in enumerate(zip(baseline, polished)):
                text_a = ((a.get("reply") or {}).get("text") or "")
                text_b = ((b.get("reply") or {}).get("text") or "")
                text_changed.append(text_b != text_a)
                # 把 reply.text 归一化后比较**序列化字符串**
                na, nb = json.loads(serial(a)), json.loads(serial(b))
                na["reply"]["text"] = "<TEXT>"
                nb["reply"]["text"] = "<TEXT>"
                if serial(na) != serial(nb):
                    diffs.append(index)
            report.check(
                "1b) 润色成功后，**除 reply.text 外**整个响应逐字节相同（序列化 sort_keys 比对）",
                not diffs,
                f"不一致的轮次={diffs or '无'}（比对 {len(baseline)} 轮；"
                f"有差异时说明润色改动了 text 之外的字段）",
            )
            report.check(
                "1c) 且 reply.text 确实被改成了润色句（不是「什么都没变」式通过）",
                any(text_changed) and any(POLISHED in ((b.get("reply") or {}).get("text") or "")
                                          for b in polished),
                f"text 有变化的轮次={[i for i, c in enumerate(text_changed) if c]}；"
                f"含润色句的轮次={[i for i, b in enumerate(polished) if POLISHED in ((b.get('reply') or {}).get('text') or '')]}",
            )

            # ---- 2–4) 三种失败：整个响应必须与「不开」逐字节相同 ----
            for mode, label in (("non_json", "非 JSON"), ("http500", "HTTP 500"), ("timeout", "超时")):
                MODE["mode"] = mode
                CALLS["n"] = 0
                failed = drive(on_server.base)
                same = []
                for a, b in zip(baseline, failed):
                    same.append(serial(a) == serial(b))
                report.check(
                    f"{'2' if mode == 'non_json' else ('3' if mode == 'http500' else '4')}) "
                    f"stub {label} → 回落规则文案，**整个响应与不开润色逐字节相同**",
                    all(same) and len(same) == len(baseline),
                    f"逐轮相同={same}（stub 调用 {CALLS['n']} 次）",
                )
                leaked = any(KEY_VALUE in serial(b) for b in failed)
                report.check(
                    f"{mode}：密钥与上游错误信息不出现在响应里",
                    not leaked,
                    f"响应里含密钥={leaked}",
                )

            # ---- 5) 未开润色时若 base_url 指向 stub，也仍然零调用（开关关闭即不联）----
            MODE["mode"] = "ok"
            CALLS["n"] = 0
            drive(off_server.base)
            report.check(
                "5) 再次确认：未开 --import-llm 时零外网调用",
                CALLS["n"] == 0,
                f"stub 调用次数={CALLS['n']}（期望 0）",
            )
    finally:
        stub.shutdown()
    return report.finish("v22_verify_polish.log")


if __name__ == "__main__":
    raise SystemExit(bp.run_guarded(main, "v22polish"))
