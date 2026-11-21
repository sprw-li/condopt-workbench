"""§11.1 中文 IME —— **verifier 自写**独立验收（不复用 api-backend 的 `v22_ime.py`）。

判据（CONTRACT §11.1）：
  1. 组合中（`Input.imeSetComposition` 造拼音 `shou`）→ 对 `/api/kickoff/chat` **零请求**；
  2. 组合中按 Enter → **零请求**（前端必须查 `isComposing`，不能只看 keydown）；
  3. `compositionend`/`insertText` 提交「收率」→ 按 Enter 后才发**一次**请求，
     且请求体 `content` 是**最终中文文本**（不是拼音、不是空串）；
  4. 组合中把输入清空再提交空串 → 不发请求。

**请求计数只来自 CDP `Network.requestWillBeSent` 事件**（不读前端代码、不看响应体），
并且用**差值**（baseline → after）；`postData` 也从事件里取，保证「发出去的到底是什么」。

运行（在克隆里）：py -3 _verify/v22_verify_ime.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_profile as bp  # noqa: E402
import v21_geom_states as G  # noqa: E402
from common import Report, Server, fresh, log  # noqa: E402

CHAT_URL_MARK = "/api/kickoff/chat"


class Session:
    """自带事件收集的 CDP 会话（`G.CDP` 会丢弃事件，这里必须拿到 Network 事件）。"""

    def __init__(self, ws_url: str) -> None:
        import websocket

        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
        self.ws.settimeout(0.2)
        self.n = 0
        self.events: list[dict] = []

    def send(self, method: str, params: dict | None = None) -> int:
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params or {}}))
        return self.n

    def drain(self, seconds: float) -> list[dict]:
        """读事件直到超时；返回这段时间内收到的事件。"""
        deadline = time.time() + seconds
        seen = []
        while time.time() < deadline:
            try:
                msg = json.loads(self.ws.recv())
            except Exception:  # noqa: BLE001
                continue
            if "method" in msg:
                self.events.append(msg)
                seen.append(msg)
        return seen

    def call(self, method: str, params: dict | None = None, timeout: float = 30.0):
        mid = self.send(method, params)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = json.loads(self.ws.recv())
            except Exception:  # noqa: BLE001
                continue
            if "method" in msg:
                self.events.append(msg)
                continue
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(method)

    def js(self, expression: str):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                                "awaitPromise": True})
        return result.get("result", {}).get("value")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def chat_requests(session: Session) -> list[dict]:
    """从已收集的事件里取 `/api/kickoff/chat` 请求（含 postData）。"""
    out = []
    for event in session.events:
        if event.get("method") != "Network.requestWillBeSent":
            continue
        request = (event.get("params") or {}).get("request") or {}
        if CHAT_URL_MARK in (request.get("url") or ""):
            out.append(request)
    return out


def press_enter(session: Session) -> None:
    for kind in ("keyDown", "keyUp"):
        session.call("Input.dispatchKeyEvent", {
            "type": kind, "key": "Enter", "code": "Enter",
            "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
        })


def main() -> int:
    report = Report("§11.1 中文 IME（verifier 自写驱动）")
    runs = fresh("ime")
    port = bp.free_port()
    profile = bp.make_profile("v22ime")
    proc = G.start_edge(port, profile)
    try:
        with Server(runs) as server:
            session = Session(G.new_target(port))
            session.call("Page.enable")
            session.call("Runtime.enable")
            session.call("Network.enable")                 # 计数唯一来源
            session.call("Page.navigate", {"url": f"{server.base}/"})
            session.drain(4.0)
            for _ in range(40):
                if session.js("!!document.querySelector('#condoptChatText')"):
                    break
                time.sleep(0.4)
            ready = session.js("!!document.querySelector('#condoptChatText')")
            report.check("前置：聊天输入框已就绪", bool(ready), f"#condoptChatText 存在={ready}")
            if not ready:
                session.close()
                return report.finish("v22_verify_ime.log")

            session.js("document.querySelector('#condoptChatText').focus(); true")
            session.drain(0.6)
            base = len(chat_requests(session))

            # ---- 1) 组合态：不得发请求 ----
            session.call("Input.imeSetComposition", {"text": "shou", "selectionStart": 4, "selectionEnd": 4})
            session.drain(1.2)
            value = session.js("document.querySelector('#condoptChatText').value")
            after1 = len(chat_requests(session))
            report.check(
                "1) 组合中（拼音 shou）→ 对 /api/kickoff/chat 零请求，且输入框确实处于组合态",
                after1 == base and value == "shou",
                f"请求 {base}→{after1}（期望不变）；输入框值={value!r}",
            )

            # ---- 2) 组合中按 Enter：不得发请求 ----
            press_enter(session)
            session.drain(1.2)
            after2 = len(chat_requests(session))
            report.check(
                "2) 组合中按 Enter → 仍零请求（前端查了 isComposing，而非只看 keydown）",
                after2 == base,
                f"请求 {base}→{after2}（期望不变）",
            )

            # ---- 3) 提交中文后再 Enter：恰好 +1，且 content 是最终中文 ----
            session.call("Input.insertText", {"text": "收率"})
            session.drain(1.0)
            committed = session.js("document.querySelector('#condoptChatText').value")
            session.js("document.querySelector('#condoptChatText').focus(); true")
            press_enter(session)
            session.drain(2.5)
            requests = chat_requests(session)
            after3 = len(requests)
            contents = []
            for request in requests[base:]:
                try:
                    contents.append(json.loads(request.get("postData") or "{}")["messages"][-1]["content"])
                except Exception:  # noqa: BLE001
                    contents.append(None)
            report.check(
                "3) 提交「收率」后按 Enter → 恰好 +1 请求，且请求体 content == '收率'（非拼音/非空串）",
                after3 == base + 1 and contents == ["收率"],
                f"请求 {base}→{after3}（期望 +1）；提交后输入框={committed!r}；"
                f"postData 里的 content={contents!r}",
            )

            # ---- 4) 清空后提交空串：不得发请求 ----
            session.js("(function(){const i=document.querySelector('#condoptChatText');"
                       "i.value='';i.dispatchEvent(new Event('input',{bubbles:true}));"
                       "i.focus();return true;})()")
            session.drain(0.6)
            before4 = len(chat_requests(session))
            press_enter(session)
            session.drain(1.5)
            after4 = len(chat_requests(session))
            report.check(
                "4) 清空输入框后按 Enter → 不发请求（沿用「空输入不发送」）",
                after4 == before4,
                f"请求 {before4}→{after4}（期望不变）",
            )
            session.close()
    finally:
        bp.kill_tree(proc)
    return report.finish("v22_verify_ime.log")


if __name__ == "__main__":
    raise SystemExit(bp.run_guarded(main, "v22ime"))
