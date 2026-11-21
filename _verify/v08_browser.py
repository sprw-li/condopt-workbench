"""验收项 8：Edge headless 真实渲染两张 PNG + 控制台/网络实测。

覆盖：
* 「前端静态页」（无 /api/*，走 mock 降级）与「后端联调页」（真实 SSE + 快照）
* 非白屏（DOM 文本量 + 截图像素统计）
* console error / 未捕获异常原文
* 页面加载期间的全部网络请求：外网请求 = 0；KaTeX @font-face 命中 200（历史缺陷点）

运行：py -3 _verify/v08_browser.py
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket  # websocket-client

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_profile as bp  # §11.5 临时资源卫生（profile 不残留 + 串行锁）
from common import (  # noqa: E402
    REPO, SHOTS, Report, Server, WORKBENCH, action, ask, free_port, fresh, get, init_study, js, log,
)

#: Edge 路径按环境变量/常见位置探测，仓库里不写死本机盘符路径（发布去本机化）。
def _find_edge() -> str:
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


EDGE = _find_edge()
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")


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
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"CDP recv 失败: {exc}") from exc
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
        except Exception:
            pass


def start_edge(port: int, profile: Path) -> subprocess.Popen:
    cmd = [
        EDGE, "--headless=new",
        f"--remote-debugging-port={port}",
        f"--remote-allow-origins=*",
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
    raise RuntimeError("Edge DevTools 端点未就绪")


def new_target(port: int) -> str:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["webSocketDebuggerUrl"]


def external(url: str) -> bool:
    if url.startswith(("data:", "blob:", "about:", "devtools:", "file:", "chrome:")):
        return False
    if url.startswith("http://") or url.startswith("https://"):
        rest = url.split("://", 1)[1]
        host = rest.split("/", 1)[0].split("@")[-1].split(":")[0]
        return host not in LOCAL_HOSTS
    return False


def run_page(cdp: CDP, url: str, label: str, interact: str | None, settle: float) -> dict:
    cdp.call("Page.enable")
    cdp.call("Runtime.enable")
    cdp.call("Network.enable")
    cdp.call("Log.enable")
    cdp.events.clear()

    cdp.call("Page.navigate", {"url": url})
    deadline = time.time() + 60
    loaded = False
    while time.time() < deadline and not loaded:
        cdp.pump(0.3)
        loaded = any(e.get("method") == "Page.loadEventFired" for e in cdp.events)
    cdp.pump(settle)

    if interact:
        out = cdp.evaluate(interact)
        log(f"    [{label}] interact → {json.dumps(out, ensure_ascii=False)[:200]}")
        cdp.pump(6.0)

    # 强制加载 KaTeX 字体（历史缺陷：fonts/fonts/ 20 个 @font-face 全 404）
    katex = cdp.evaluate(
        "(function(){try{var d=document.createElement('div');d.style.cssText='position:fixed;left:-9999px';"
        "katex.render('\\\\rho \\\\sum_{i=1}^{n}\\\\alpha_i', d);document.body.appendChild(d);return 'katex-ok';}"
        "catch(e){return 'katex-err:'+e.message;}})()"
    )
    log(f"    [{label}] KaTeX 强制渲染 → {katex}")
    cdp.pump(2.5)

    dom = cdp.evaluate(
        "JSON.stringify({title:document.title,textLen:(document.body.innerText||'').length,"
        "nodes:document.querySelectorAll('*').length,"
        "mode:(document.getElementById('modeText')||{}).innerText||null,"
        "runNote:(document.getElementById('runNote')||{}).innerText||null,"
        "mdLen:((document.getElementById('md')||{}).innerText||'').length,"
        "mdVisible:!!(document.getElementById('md')&&!document.getElementById('md').hidden),"
        "stages:document.querySelectorAll('#stages .stage, #stages li, #stages .stages-item').length,"
        "katexLoaded:typeof katex!=='undefined',"
        "scripts:document.scripts.length})"
    )
    shot = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})

    requests: dict[str, dict] = {}
    responses: list[dict] = []
    errors: list[str] = []          # 浏览器网络层日志（可能与后端缺失有关）
    js_errors: list[str] = []       # console.error / assert / 未捕获异常（真正的 JS 报错）
    for event in cdp.events:
        method = event.get("method")
        params = event.get("params") or {}
        if method == "Network.requestWillBeSent":
            requests[params["requestId"]] = {
                "url": params["request"]["url"],
                "type": params.get("type"),
                "initiator": (params.get("initiator") or {}).get("type"),
            }
        elif method == "Network.responseReceived":
            responses.append({
                "url": params["response"]["url"],
                "status": params["response"]["status"],
                "type": params.get("type"),
                "mime": params["response"].get("mimeType"),
            })
        elif method == "Network.loadingFailed":
            errors.append(f"Network.loadingFailed {params.get('errorText')} type={params.get('type')}")
        elif method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails") or {}
            text = ("Runtime.exceptionThrown: "
                    + str((details.get("exception") or {}).get("description") or details.get("text"))[:400])
            errors.append(text)
            js_errors.append(text)
        elif method == "Runtime.consoleAPICalled" and params.get("type") in ("error", "assert", "warning"):
            args = " ".join(str((a.get("value") if "value" in a else a.get("description"))) for a in params.get("args") or [])
            text = f"console.{params.get('type')}: {args[:400]}"
            errors.append(text)
            if params.get("type") in ("error", "assert"):
                js_errors.append(text)
        elif method == "Log.entryAdded" and (params.get("entry") or {}).get("level") in ("error", "warning"):
            entry = params["entry"]
            errors.append(f"Log[{entry.get('level')}] {entry.get('text')} {entry.get('url', '')}"[:400])

    exts = [item for item in requests.values() if external(item["url"])]
    fonts = [item for item in responses if item["type"] == "Font" or item["url"].endswith((".woff2", ".woff", ".ttf"))]
    failed = [item for item in responses if item["status"] >= 400]
    return {
        "label": label,
        "url": url,
        "dom": json.loads(dom) if isinstance(dom, str) and dom.startswith("{") else dom,
        "png": shot.get("data"),
        "requests": list(requests.values()),
        "external": exts,
        "fonts": fonts,
        "failed": failed,
        "errors": errors,
        "js_errors": js_errors,
        "loaded": loaded,
    }


def analyze_png(path: Path) -> dict:
    import warnings

    from PIL import Image

    warnings.filterwarnings("ignore", category=DeprecationWarning)
    image = Image.open(path).convert("RGB")
    pixels = list(image.getdata())
    total = len(pixels)
    white = sum(1 for r, g, b in pixels if r > 245 and g > 245 and b > 245)
    mean = tuple(round(sum(channel) / total, 1) for channel in zip(*pixels))
    return {
        "size": image.size,
        "mean_rgb": mean,
        "white_fraction": round(white / total, 4),
        "non_white_fraction": round(1 - white / total, 4),
        "distinct_colors": len(set(pixels)),
    }


def main() -> int:
    report = Report("项8 Edge headless 视觉 + 控制台 + 网络")
    SHOTS.mkdir(parents=True, exist_ok=True)
    static_port, debug_port = free_port(), free_port()

    static = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(static_port), "--bind", "127.0.0.1",
         "--directory", str(WORKBENCH / "web")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(REPO),
    )
    static_url = f"http://127.0.0.1:{static_port}/index.html"
    static_base = f"http://127.0.0.1:{static_port}"

    with Server(fresh("browser")) as backend:
        init_study(backend.base, run_id="demo", budget=8, batch=2)
        asked = ask(backend.base, run_id="demo", n=2)
        action(
            backend.base, "tell", run="demo",
            payload={"observations": [{"id": item["id"], "y": 10.0 + i} for i, item in enumerate(asked["result"]["pending"])]},
        )
        backend_url = f"{backend.base}/"

        profile = bp.make_profile("v08browser")
        edge = start_edge(debug_port, profile)
        try:
            log(f"Edge headless pid={edge.pid} devtools=127.0.0.1:{debug_port}")
            log(f"静态页 {static_url}（无 /api/* → mock 降级）")
            log(f"联调页 {backend_url}（真实后端 + 一个 demo study）")

            for label, url, interact, settle, filename in (
                (
                    "前端静态页",
                    static_url + "?demo=kickoff",
                    "(function(){var b=document.getElementById('runBtn');if(!b)return 'no-runBtn';b.click();return 'clicked';})()",
                    1.0,
                    "frontend_static.png",
                ),
                (
                    "后端联调页",
                    backend_url,
                    "(function(){var b=document.getElementById('runBtn');if(!b)return 'no-runBtn';b.click();return 'clicked';})()",
                    1.5,
                    "backend_live.png",
                ),
            ):
                cdp = CDP(new_target(debug_port))
                try:
                    info = run_page(cdp, url, label, interact, settle)
                finally:
                    cdp.close()

                shot_path = SHOTS / filename
                shot_path.write_bytes(base64.b64decode(info["png"]))
                stats = analyze_png(shot_path)
                dom = info["dom"] if isinstance(info["dom"], dict) else {}

                log(f"\n--- {label} ---")
                log(f"    URL      : {info['url']}")
                log(f"    DOM      : {json.dumps(dom, ensure_ascii=False)}")
                log(f"    截图     : {shot_path.name} {stats}")
                log(f"    请求数   : {len(info['requests'])}")
                log(f"    外网请求 : {[item['url'] for item in info['external']] or '无'}")
                log(f"    字体响应 : {[(item['url'].split('/')[-1], item['status']) for item in info['fonts']]}")
                log(f"    >=400    : {[(item['url'], item['status']) for item in info['failed']] or '无'}")
                log(f"    JS 错误  : {info['js_errors'] or '无'}")
                log(f"    浏览器日志: {info['errors'] or '无'}")

                report.check(f"8.{label} 页面已加载", info["loaded"], f"url={info['url']}")
                report.check(
                    f"8.{label} 非白屏（DOM 文本 + 像素）",
                    bool(dom.get("textLen", 0) > 200) and stats["non_white_fraction"] > 0.03 and stats["distinct_colors"] > 50,
                    f"textLen={dom.get('textLen')} nodes={dom.get('nodes')} "
                    f"非白像素={stats['non_white_fraction']:.1%} 颜色数={stats['distinct_colors']}",
                )
                report.check(
                    f"8.{label} 有阶段条 + 流式结果渲染（点击「开始处理」后）",
                    bool(dom.get("stages", 0) >= 4) and bool(dom.get("mdLen", 0) > 20),
                    f"stages={dom.get('stages')} mdLen={dom.get('mdLen')} mdVisible={dom.get('mdVisible')}",
                )
                report.check(
                    f"8.{label} 无 JS console.error / 未捕获异常",
                    not info["js_errors"],
                    f"JS 错误={info['js_errors'] or '无'}",
                )
                expected_degrade = label == "前端静态页"
                net_ok = (not info["errors"]) if not expected_degrade else all(
                    "/api/" in line for line in info["errors"]
                )
                report.check(
                    f"8.{label} 浏览器错误日志符合预期"
                    + ("（无后端：仅 /api/* 资源失败，属文档化的降级路径）" if expected_degrade else "（无任何 error）"),
                    net_ok and (not expected_degrade or info["errors"]),
                    f"原文={info['errors'] or '无'}",
                )
                if expected_degrade:
                    statuses = sorted({item["status"] for item in info["failed"]})
                    report.check(
                        "8.前端静态页 无后端降级路径生效（/api/* 失败 + mock 输出不白屏）",
                        statuses in ([404], [501], [404, 501]) and dom.get("mode") == "离线演示" and dom.get("mdLen", 0) > 20,
                        f"/api/* 状态={statuses} mode={dom.get('mode')} mdLen={dom.get('mdLen')}",
                    )
                else:
                    report.check(
                        "8.后端联调页 后端真的在线且五场景 SSE 已跑完",
                        dom.get("mode", "").startswith("真实") and dom.get("mdLen", 0) > 200,
                        f"mode={dom.get('mode')} runNote={dom.get('runNote')} mdLen={dom.get('mdLen')}",
                    )
                report.check(
                    f"8.{label} 零外网请求",
                    not info["external"],
                    f"外网={[item['url'] for item in info['external']] or '无'}；"
                    f"同源请求 {len(info['requests'])} 条：{[item['url'].split('/', 3)[-1] for item in info['requests']][:12]}",
                )
                report.check(
                    f"8.{label} KaTeX 字体实际命中 200（历史缺陷点）",
                    bool(info["fonts"]) and all(item["status"] == 200 for item in info["fonts"]),
                    f"字体请求 {len(info['fonts'])} 条，状态={sorted({item['status'] for item in info['fonts']})}；"
                    f"样例={[item['url'].split('/')[-1] for item in info['fonts'][:4]]}",
                )
                if not expected_degrade:
                    report.check(
                        f"8.{label} 无 >=400 资源",
                        not info["failed"],
                        f"{[(item['url'].split('/')[-1], item['status']) for item in info['failed']] or '无'}",
                    )
        finally:
            edge.terminate()
            try:
                edge.wait(timeout=15)
            except subprocess.TimeoutExpired:
                bp.kill_tree(edge)
            static.terminate()

    return report.finish("v08_browser.log")


if __name__ == "__main__":
    raise SystemExit(bp.run_guarded(main, "v08browser"))
