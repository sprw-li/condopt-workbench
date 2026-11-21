"""§11.1 独立验收驱动：中文 IME 组合态不得把未确认内容当答案发出去。

**验收方驱动**：只看真实浏览器发出的网络请求（CDP `Network.requestWillBeSent`），
不读前端源码推断行为，也不采信前端自己的自述。

判据（CONTRACT §11.1）：
  1. `Input.imeSetComposition` 造组合中间态（拼音 `shou`）→ `/api/kickoff/chat` 请求数 **0**；
  2. 组合中按 Enter → **0**（前端必须查 `KeyboardEvent.isComposing`，所以这里两种注入都做：
     CDP 真键盘事件 + 显式 `isComposing:true` 的合成事件）；
  3. `compositionend` 提交「收率」→ 按 Enter 后**恰好 1 次**，且请求体 `content` 是最终中文；
  4. 清空输入框后按 Enter → 不发请求。

临时资源按 §11.5 交给 ``browser_profile``（%TEMP% profile、树杀、连续两次确认、残留自证）。

用法：``py -3 _verify/v22_ime.py``（退出码 0 = 全部 PASS）
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

VERIFY = Path(__file__).resolve().parent
if str(VERIFY) not in sys.path:
    sys.path.insert(0, str(VERIFY))

import browser_profile  # noqa: E402  §11.5 卫生
from common import REPO, Report, Server, fresh, log  # noqa: E402

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import shutil  # noqa: E402
import subprocess  # noqa: E402
import websocket  # noqa: E402

CHAT_INPUT = "#condoptChatText"
CHAT_SEND = "#condoptChatSend"
CHAT_URL_MARK = "/api/kickoff/chat"


def find_edge() -> str:
    """Edge 路径：环境变量 → PATH → 常见安装位置（不在仓库里写死本机盘符）。"""
    import os

    override = os.environ.get("CONDOPT_EDGE")
    if override:
        return override
    found = shutil.which("msedge")
    if found:
        return found
    for candidate in (
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft/Edge/Application/msedge.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return "msedge"


EDGE = find_edge()


class CDP:
    def __init__(self, ws_url: str) -> None:
        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
        self.ws.settimeout(0.2)
        self.next_id = 0
        self.events: list[dict] = []

    def call(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        self.next_id += 1
        mid = self.next_id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            data = json.loads(raw)
            if data.get("id") == mid:
                if "error" in data:
                    raise RuntimeError(f"{method}: {data['error']}")
                return data.get("result", {})
            self.events.append(data)
        raise TimeoutError(f"CDP {method} 超时")

    def pump(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:  # noqa: BLE001
                return
            self.events.append(json.loads(raw))

    def evaluate(self, expression: str):
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=60,
        )
        if result.get("exceptionDetails"):
            return {"__exception__": result["exceptionDetails"].get("text")}
        return result.get("result", {}).get("value")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def start_edge(port: int, profile: Path) -> subprocess.Popen:
    cmd = [
        EDGE, "--headless=new",
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--hide-scrollbars",
        "--window-size=1512,1000",
        "about:blank",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as resp:
                json.loads(resp.read())
                return proc
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    raise RuntimeError("Edge DevTools 端点未就绪（设 CONDOPT_EDGE 指到 msedge.exe）")


def new_target(port: int) -> str:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["webSocketDebuggerUrl"]


# --------------------------------------------------------------------------- #
# 断言用的观测原语
# --------------------------------------------------------------------------- #


def chat_requests(cdp: CDP) -> int:
    """**只**数 CDP `Network.requestWillBeSent` 里 URL 命中 /api/kickoff/chat 的条数。"""
    count = 0
    for event in cdp.events:
        if event.get("method") != "Network.requestWillBeSent":
            continue
        url = ((event.get("params") or {}).get("request") or {}).get("url", "")
        if CHAT_URL_MARK in url:
            count += 1
    return count


def last_chat_request(cdp: CDP) -> dict:
    found: dict = {}
    for event in cdp.events:
        if event.get("method") != "Network.requestWillBeSent":
            continue
        request = (event.get("params") or {}).get("request") or {}
        if CHAT_URL_MARK in request.get("url", ""):
            found = request
    return found


def press_enter(cdp: CDP) -> None:
    for kind in ("keyDown", "keyUp"):
        cdp.call(
            "Input.dispatchKeyEvent",
            {
                "type": kind,
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
                "nativeVirtualKeyCode": 13,
            },
        )


def wait_for(cdp: CDP, expression: str, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = cdp.evaluate(expression)
        if value and not isinstance(value, dict):
            return True
        cdp.pump(0.3)
    return False


def main() -> int:
    report = Report("§11.1 中文 IME 独立验收（CDP 网络事件计数）")
    profile = browser_profile.make_profile("ime")
    port = browser_profile.free_port()
    proc = start_edge(port, profile)
    browser_profile.attach(profile, proc)
    log(f"Edge headless pid={proc.pid} devtools=127.0.0.1:{port} profile={profile}")
    cdp: CDP | None = None
    try:
        cdp = CDP(new_target(port))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Network.enable")

        with Server(fresh("v22_ime"), port=None) as server:
            cdp.call("Page.navigate", {"url": server.base + "/"})
            deadline = time.time() + 60
            while time.time() < deadline:
                cdp.pump(0.4)
                if any(e.get("method") == "Page.loadEventFired" for e in cdp.events):
                    break
            # 等聊天窗与输入框就绪
            ready = wait_for(cdp, f"!!document.querySelector('{CHAT_INPUT}')")
            report.check("聊天输入框存在（首屏是对话，不是旧表单）", ready,
                         f"selector={CHAT_INPUT}")
            if not ready:
                return report.finish("v22_ime.log")
            cdp.evaluate(f"document.querySelector('{CHAT_INPUT}').focus()")
            cdp.pump(0.5)

            # ---- 1) 组合中间态：零请求 -------------------------------------
            baseline = chat_requests(cdp)
            cdp.call("Input.imeSetComposition", {"text": "shou", "selectionStart": 4, "selectionEnd": 4})
            cdp.pump(1.2)
            after_compose = chat_requests(cdp)
            value_now = cdp.evaluate(f"document.querySelector('{CHAT_INPUT}').value")
            report.check("组合中间态（imeSetComposition 拼音 shou）→ 零 /api/kickoff/chat 请求",
                         after_compose == baseline,
                         f"请求数 {baseline} → {after_compose}；输入框现值={value_now!r}")

            # ---- 2) 组合中按 Enter：零请求（两种注入都验）-------------------
            press_enter(cdp)
            cdp.pump(1.0)
            after_enter = chat_requests(cdp)
            report.check("组合中按 Enter（CDP 真键盘事件）→ 零请求",
                         after_enter == baseline,
                         f"请求数 {baseline} → {after_enter}")
            # 再加一次显式 isComposing:true 的合成事件（前端必须查 isComposing，不能只看 keydown）
            synthetic = cdp.evaluate(
                "(function(){var el=document.querySelector('" + CHAT_INPUT + "');"
                "if(!el) return 'no-input';"
                "el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',"
                "keyCode:13,isComposing:true,bubbles:true,cancelable:true}));"
                "return 'dispatched';})()"
            )
            cdp.pump(1.0)
            after_synth = chat_requests(cdp)
            report.check("组合中按 Enter（显式 isComposing:true 合成事件）→ 零请求",
                         after_synth == baseline,
                         f"dispatch={synthetic}；请求数 {baseline} → {after_synth}")

            # ---- 3) 提交「收率」+ Enter：恰好一次，且 content 是最终中文 -----
            cdp.call("Input.insertText", {"text": "收率"})   # 提交组合（触发 compositionend/input）
            cdp.pump(0.8)
            committed = chat_requests(cdp)
            report.check("compositionend 提交「收率」后（未按 Enter）→ 仍零请求",
                         committed == baseline,
                         f"请求数 {baseline} → {committed}")
            press_enter(cdp)
            cdp.pump(3.0)
            sent = chat_requests(cdp)
            request = last_chat_request(cdp)
            post = request.get("postData") or ""
            content = None
            try:
                messages = json.loads(post).get("messages") or []
                content = (messages[-1] or {}).get("content") if messages else None
            except (json.JSONDecodeError, AttributeError):
                content = None
            report.check("提交后按 Enter → 恰好一次 /api/kickoff/chat", sent == baseline + 1,
                         f"请求数 {baseline} → {sent}")
            report.check("请求体 content 是最终中文「收率」（不是拼音/空串）", content == "收率",
                         f"content={content!r}；postData={post[:160]!r}")

            # ---- 3b) 第二条提交路径：合成 compositionend（与 CDP insertText 并行验证）----
            before_path2 = chat_requests(cdp)
            committed2 = cdp.evaluate(
                "(function(){var el=document.querySelector('" + CHAT_INPUT + "');"
                "if(!el) return 'no-input';el.focus();"
                "el.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true,data:''}));"
                "el.value='收率';"
                "el.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:true,data:'收率'}));"
                "el.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'收率'}));"
                "return el.value;})()"
            )
            cdp.pump(0.8)
            after_commit2 = chat_requests(cdp)
            report.check("第二条路径：合成 compositionstart/input(isComposing)/compositionend 后（未按 Enter）→ 零请求",
                         after_commit2 == before_path2,
                         f"输入框={committed2!r}；请求数 {before_path2} → {after_commit2}")
            press_enter(cdp)
            cdp.pump(2.5)
            sent2 = chat_requests(cdp)
            request2 = last_chat_request(cdp)
            post2 = request2.get("postData") or ""
            content2 = None
            try:
                messages2 = json.loads(post2).get("messages") or []
                content2 = (messages2[-1] or {}).get("content") if messages2 else None
            except (json.JSONDecodeError, AttributeError):
                content2 = None
            report.check("第二条路径：compositionend 提交后按 Enter → 恰好一次且 content 是「收率」",
                         sent2 == before_path2 + 1 and content2 == "收率",
                         f"请求数 {before_path2} → {sent2}；content={content2!r}")

            # ---- 4) 空串不发 -------------------------------------------------
            before_empty = chat_requests(cdp)
            cdp.evaluate(
                "(function(){var el=document.querySelector('" + CHAT_INPUT + "');"
                "el.value='';el.dispatchEvent(new Event('input',{bubbles:true}));return el.value;})()"
            )
            cdp.pump(0.5)
            press_enter(cdp)
            cdp.pump(1.5)
            after_empty = chat_requests(cdp)
            report.check("清空后按 Enter → 不发请求（空输入不发送）",
                         after_empty == before_empty,
                         f"请求数 {before_empty} → {after_empty}")
    finally:
        if cdp is not None:
            cdp.close()
        browser_profile.kill_tree(proc)
        if not browser_profile.cleanup(profile):
            report.check("一次性 profile 清理干净（连续两次确认不存在）", False,
                         f"残留 {profile}")
        else:
            report.check("一次性 profile 清理干净（连续两次确认不存在）", True, str(profile))
    return report.finish("v22_ime.log")


if __name__ == "__main__":
    # §11.5-6：串行锁（同一台机器上浏览器驱动必须串行跑）。
    # 用 ``run_guarded`` 而不是 ``guard_residue`` —— 只有前者会取锁；
    # 拿不到锁 → exit 4（环境/并发类），不记成产品缺陷、不静默重试。
    raise SystemExit(browser_profile.run_guarded(main, "ime"))
