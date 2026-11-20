"""验收项 5：data.js ↔ CONTRACT §3.1 ↔ 后端 /api/bootstrap 三方逐字段比对；
index.html 本地资源存在性；KaTeX @font-face 目标存在性。

运行：py -3 _verify/v05_contract.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    REPO, Report, Server, WORKBENCH, contract_stages, fresh, get, js, log,
)

WEB = WORKBENCH / "web"
EXTRACT = Path(__file__).resolve().parent / "extract_data.js"
OUT = Path(__file__).resolve().parent / "logs" / "data_scenarios.json"


def main() -> int:
    report = Report("项5 data.js ↔ 契约 ↔ 后端")
    gantt = contract_stages()
    log(f"CONTRACT §3.1 解析结果（真源）: {json.dumps(gantt, ensure_ascii=False)}")

    proc = subprocess.run(
        ["node", str(EXTRACT), str(WEB / "assets" / "data.js"), str(OUT)],
        capture_output=True, text=True, cwd=str(REPO), encoding="utf-8",
    )
    log(f"$ node _verify/extract_data.js web/assets/data.js logs/data_scenarios.json")
    log(proc.stdout.strip() or proc.stderr.strip())
    if proc.returncode != 0:
        report.check("node 抽取 data.js 成功", False, proc.stderr[-400:])
        return report.finish("v05_contract.log")
    report.check("node 真实执行 data.js 并抽出五场景", True, f"exit=0 -> {OUT.name}")

    data = json.loads(OUT.read_text(encoding="utf-8"))
    scen = data["scenarios"]
    data_keys = [s["key"] for s in scen]
    report.check(
        f"data.js 场景数 = CONTRACT §3.1 表行数（{len(gantt)}）",
        data["n_scenarios"] == len(gantt),
        f"data.js n={data['n_scenarios']} keys={data_keys} 契约表={list(gantt)}",
    )
    report.check(
        "data.js 场景 key 顺序 = CONTRACT §3.1 表顺序",
        data_keys == list(gantt),
        f"实际={data_keys} 契约={list(gantt)}",
    )

    fields = ("no", "title", "action", "needs_run", "stages")
    for item in scen:
        row = gantt.get(item["key"])
        if row is None:
            report.check(
                f"5.{item['key']} CONTRACT §3.1 表已收录该场景",
                False,
                f"契约表只有 {list(gantt)}，缺 {item['key']!r} —— data.js 里有但真源表里没有，"
                f"『与契约一字不差』无法比对",
            )
            continue
        diffs = {f: (item[f], row[f]) for f in fields if item[f] != row[f]}
        report.check(
            f"5.{item['key']} 与 §3.1 逐字段一致（no/title/action/needs_run/stages）",
            not diffs,
            f"no={item['no']} action={item['action']} needs_run={item['needs_run']} "
            f"stages={item['stages']}" + (f" 差异={diffs}" if diffs else ""),
        )
        report.check(
            f"5.{item['key']} mock/review/example/outputs 齐全",
            item["has_mock"] and item["has_review"] and item["has_example"] and item["has_outputs"],
            f"mock={item['has_mock']} review={item['has_review']} example={item['has_example']} outputs={item['has_outputs']}",
        )
    report.check(
        "data.js 没有契约表之外的野生场景",
        not (set(data_keys) - set(gantt)),
        f"多出={sorted(set(data_keys) - set(gantt)) or '无'}",
    )

    # ---- index.html 引用的本地资源存在性 -------------------------------- #
    log("\n--- index.html 本地资源 ---")
    index_text = (WEB / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", index_text, flags=re.IGNORECASE)
    local, external, skipped = [], [], []
    for ref in refs:
        if ref.startswith(("data:", "#", "mailto:")):
            skipped.append(ref)
        elif ref.startswith(("http://", "https://", "//")):
            external.append(ref)
        else:
            local.append(ref)
    missing = []
    for ref in sorted(set(local)):
        clean = ref.split("?")[0].split("#")[0]
        target = (WEB / clean.lstrip("/")).resolve()
        if not target.is_file():
            missing.append(ref)
    report.check(
        "index.html 引用的本地资源全部存在",
        not missing,
        f"引用 {len(set(local))} 个本地资源（{sorted(set(local))}），缺失={missing or '无'}",
    )
    report.check(
        "index.html 无 http(s)/协议相对外链",
        not external,
        f"外链={external or '无'}；跳过={skipped[:5]}",
    )

    # ---- KaTeX @font-face 目标（历史缺陷点） ---------------------------- #
    log("\n--- KaTeX @font-face 目标存在性 ---")
    katex_css = WEB / "vendor" / "katex" / "katex.min.css"
    css_text = katex_css.read_text(encoding="utf-8")
    urls = re.findall(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""", css_text)
    font_urls = [u for u in urls if "fonts/" in u]
    bad = []
    for ref in sorted(set(font_urls)):
        target = (katex_css.parent / ref.split("?")[0]).resolve()
        if not target.is_file():
            bad.append(ref)
    report.check(
        f"katex.min.css 的 {len(set(font_urls))} 个 url(fonts/…) 目标全部存在（历史缺陷：fonts/fonts/ 多一层）",
        not bad,
        f"缺失={bad or '无'}；样例={sorted(set(font_urls))[:2]}",
    )
    nested = list((WEB / "vendor" / "katex" / "fonts").glob("fonts/*"))
    report.check("vendor/katex/fonts/ 下不存在残留的 fonts/fonts/ 嵌套目录", not nested, f"残留={nested or '无'}")

    # ---- 与后端 /api/bootstrap 三方对照 -------------------------------- #
    log("\n--- data.js ↔ /api/bootstrap ---")
    with Server(fresh("contract")) as server:
        status, _, text = get(server.base, "/api/bootstrap")
        boot = js(text)
        backend = {item["key"]: item for item in boot["scenarios"]}
        report.check(
            "后端 scenarios key 顺序与 data.js 一致",
            list(backend) == [s["key"] for s in scen],
            f"后端={list(backend)} data.js={[s['key'] for s in scen]}",
        )
        mismatches = []
        for item in scen:
            b = backend[item["key"]]
            for field in ("no", "title", "tagline", "short", "action", "needs_run", "stages"):
                if b.get(field) != item[field]:
                    mismatches.append((item["key"], field, b.get(field), item[field]))
        report.check(
            "data.js 与后端 Scenario 逐字段差异 0 处",
            not mismatches,
            f"差异={mismatches or '无（key/no/title/tagline/short/action/needs_run/stages）'}",
        )
    return report.finish("v05_contract.log")


if __name__ == "__main__":
    raise SystemExit(main())
