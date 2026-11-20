"""D14：不复用测试文件，自己用真实 HTTP 复验三条既有语义（SSE 帧 / q9 冲突 / 导入 budget）。

运行：py -3 _verify/v21_regression_semantics.py
"""

from __future__ import annotations

import http.client as http_client
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    Report, Server, action, fresh, get, http, js, log, short_answers,
)

ALLOWED = {"meta", "stage", "delta", "notice", "error", "done"}


def sse_frames(port: int, body: dict):
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    conn = http_client.HTTPConnection("127.0.0.1", port, timeout=60)
    conn.request("POST", "/api/run", body=raw,
                 headers={"Content-Type": "application/json", "Content-Length": str(len(raw))})
    response = conn.getresponse()
    text = response.read().decode("utf-8")
    conn.close()
    names = [line[7:] for line in text.splitlines() if line.startswith("event: ")]
    datas = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
    return response.status, names, datas


def main() -> int:
    report = Report("D14 既有语义独立复验（真实 HTTP）")
    runs = fresh("regression")
    with Server(runs) as server:
        base, port = server.base, server.port

        # --- 1) SSE 帧（kickoff 场景）---
        status, names, datas = sse_frames(port, {"scenario": "kickoff", "payload": {"answers": short_answers()}})
        stages = [d for n, d in zip(names, datas) if n == "stage"]
        report.check(
            "1) SSE：状态 200，帧名 ∈ {meta,stage,delta,notice,error,done}，首 meta 末 done，stage.index ∈ [0,4)",
            status == 200 and set(names) <= ALLOWED and names[0] == "meta" and names[-1] == "done"
            and sorted({s["index"] for s in stages}) == [0, 1, 2, 3],
            f"status={status} events={sorted(set(names))} 帧序列首尾={names[0]}/{names[-1]} "
            f"stage indices={sorted({s['index'] for s in stages})}",
        )

        # --- 2) q9 冲突：已有 pending 再 ask → 409，且不重复消耗 ---
        action(base, "init", payload={"id": "cdemo", "answers": short_answers(), "budget": 8, "batch": 2})
        status, _, text = action(base, "ask", run="cdemo", payload={"n": 2})
        first = js(text)
        status2, _, text2 = action(base, "ask", run="cdemo", payload={"n": 2})
        conflict = js(text2)
        snap = js(get(base, "/api/study?id=cdemo")[2])["study"]
        report.check(
            "2) q9 冲突：第二次 ask → 409 E_CONFLICT，pending 仍为 2、remaining 未多扣",
            status == 200 and status2 == 409 and conflict.get("error") == "E_CONFLICT"
            and len(snap["pending"]) == 2
            and snap["remaining"] == first["result"]["run"]["remaining"],
            f"首次 status={status} accepted={len(first['result']['pending'])}；"
            f"二次 status={status2} error={conflict.get('error')} message={str(conflict.get('message'))[:60]!r}；"
            f"pending={len(snap['pending'])} remaining={snap['remaining']}（首次后 {first['result']['run']['remaining']}）",
        )

        # --- 3) 导入 budget 校验：budget < 行数 → 422 且带 suggested_budget，且不建 run ---
        data_dir = fresh("regdata")
        csv_path = data_dir / "log.csv"
        lines = ["T,time,solvent,yield"] + [
            f"{40 + i % 17 * 5},{5 + i % 12 * 10},{['MeCN', 'EtOH', 'water'][i % 3]},{20 + i % 31 * 2.5}"
            for i in range(30)
        ]
        csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with Server(fresh("regruns2"), extra=["--allow-import-root", str(data_dir)]) as imp:
            status, _, text = http(imp.base, "POST", "/api/import/scan", body={"path": str(csv_path)})
            scan = js(text)
            mapping = scan.get("rule_mapping") or {}
            status, _, text = http(imp.base, "POST", "/api/import/apply", body={
                "id": "should_not_exist", "path": str(csv_path), "scan_id": scan.get("scan_id"),
                "mapping": mapping, "budget": 3, "batch": 2,
            })
            parsed = js(text)
            report.check(
                "3) 导入 budget：budget=3 < 30 行 → 422 E_VALIDATION 且 suggested_budget 是数字、run 未创建",
                status == 422 and parsed.get("error") == "E_VALIDATION"
                and isinstance(parsed.get("suggested_budget"), (int, float))
                and not (imp.runs_dir / "should_not_exist").exists(),
                f"status={status} error={parsed.get('error')} suggested_budget={parsed.get('suggested_budget')!r} "
                f"残留 run 目录={(imp.runs_dir / 'should_not_exist').exists()} "
                f"message={str(parsed.get('message'))[:80]!r}",
            )
    return report.finish("v21_regression_semantics.log")


if __name__ == "__main__":
    raise SystemExit(main())
