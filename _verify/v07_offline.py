"""验收项 7：web 下 html/js/css（排除 vendor/licenses 与注释）无 http(s) 外链。

运行：py -3 _verify/v07_offline.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import Report, WORKBENCH, log  # noqa: E402

WEB = WORKBENCH / "web"
SUFFIXES = {".html", ".js", ".css"}


def classify(path: Path, text: str) -> list[tuple[str, str, str]]:
    """返回 [(url, 类别, 位置)]；类别 ∈ {'offender','comment','non-network'}。

    ``non-network`` = 出现在 ``data:`` URI 内部或 XML ``xmlns`` 命名空间里的字符串，
    浏览器不会发出任何请求（例如内联 favicon 里百分号编码的 ``xmlns='http://www.w3.org/2000/svg'``）。
    """
    out = []
    is_js = path.suffix == ".js"
    is_html = path.suffix == ".html"
    for match in re.finditer(r"https?://[^\s\"'<>`)}\]]+", text):
        start = match.start()
        line_start = text.rfind("\n", 0, start) + 1
        prefix = text[line_start:start]
        in_line = is_js and "//" in prefix
        block_open, block_close = text.rfind("/*", 0, start), text.rfind("*/", 0, start)
        in_block = block_open > block_close
        if is_html:
            html_open, html_close = text.rfind("<!--", 0, start), text.rfind("-->", 0, start)
            in_block = in_block or html_open > html_close
        # 是否落在 data: URI 的值内部（上一个引号之后出现过 data:）
        in_data_uri = text.rfind("data:", 0, start) > max(
            text.rfind('"', 0, start), text.rfind("'", 0, start), 0
        )
        is_xmlns = re.search(r"xmlns\s*[:=]\s*['\"]?$", prefix) is not None
        if in_line or in_block:
            kind = "comment"
        elif in_data_uri or is_xmlns:
            kind = "non-network"
        else:
            kind = "offender"
        line_no = text.count("\n", 0, start) + 1
        out.append((match.group(0), kind, f"{path.relative_to(WORKBENCH)}:{line_no}"))
    return out


def main() -> int:
    report = Report("项7 离线静态扫描")
    files = [
        p for p in sorted(WEB.rglob("*"))
        if p.is_file()
        and p.suffix.lower() in SUFFIXES
        and "vendor" not in p.parts
        and "licenses" not in p.parts
    ]
    log(f"扫描范围（{len(files)} 个 html/js/css，已排除 vendor/ 与 licenses/）：")
    for p in files:
        log(f"  - {p.relative_to(WORKBENCH)}  {p.stat().st_size} bytes")

    offenders: list[str] = []
    comments: list[str] = []
    non_network: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for url, kind, where in classify(path, text):
            if kind == "comment":
                comments.append(f"{where}  [注释] {url}")
            elif kind == "non-network":
                non_network.append(f"{where}  [data:/xmlns] {url}")
            else:
                offenders.append(f"{where}  [可执行] {url}")

    log(f"\n命中的 http(s) 出现点：{len(offenders) + len(comments) + len(non_network)} 个"
        f"（可执行 {len(offenders)} / 注释 {len(comments)} / data-URI或xmlns {len(non_network)}）")
    for item in offenders:
        log(f"  OFFENDER {item}")
    for item in non_network:
        log(f"  non-net  {item}")
    for item in comments:
        log(f"  comment  {item}")

    report.check(
        "非 vendor/licenses 的 html/js/css 中无「真正可发请求」的 http(s) 外链",
        not offenders,
        f"可执行外链={offenders or '无'}；"
        f"data-URI/xmlns 内的字符串 {len(non_network)} 处（非网络引用）、注释 {len(comments)} 处已分类排除",
    )

    # 额外：确认没有远程脚本/样式标签、没有 CDN 关键字
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        bad_tags = re.findall(r"""<(?:script|link)[^>]*\b(?:src|href)\s*=\s*["'](?:https?:)?//""", text, re.IGNORECASE)
        report.check(
            f"7.{path.relative_to(WEB)} 无远程 <script>/<link>",
            not bad_tags,
            f"命中={bad_tags or '无'}",
        )

    # 额外：无 WebSocket / sendBeacon / 远程 fetch 字面量
    joined = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)
    report.check(
        "7.无 new WebSocket / sendBeacon 远程地址",
        not re.search(r"new\s+WebSocket\s*\(\s*[\"'](?:wss?|http)", joined)
        and "sendBeacon(" not in joined,
        "未发现 new WebSocket(wss://…) 或 sendBeacon",
    )
    report.check(
        "7.所有 fetch 只打同源相对路径",
        not re.search(r"""fetch\(\s*["'](?:https?:)?//""", joined),
        "未发现 fetch 绝对/协议相对地址",
    )
    return report.finish("v07_offline.log")


if __name__ == "__main__":
    raise SystemExit(main())
