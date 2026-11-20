"""验收项 3（真实 HTTP 端到端闭环） + 项 4（/api/run 五场景 SSE 逐帧）。

运行：py -3 _verify/v03_e2e.py
"""

from __future__ import annotations

import http.client as http_client
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    Report, Server, action, contract_stages, fresh, get, http, init_study, js, log, short_answers,
)

ALLOWED_EVENTS = {"meta", "stage", "delta", "notice", "error", "done"}


def sse_frames(port: int, body: dict) -> tuple[list[tuple[str, dict, float]], float, int, float]:
    """真实 socket 逐行读取 SSE；返回 (frames[(event,data,t)], 首字节延迟, status, 总耗时)。"""
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    conn = http_client.HTTPConnection("127.0.0.1", port, timeout=300)
    started = time.monotonic()
    conn.request(
        "POST", "/api/run", body=raw,
        headers={"Content-Type": "application/json", "Content-Length": str(len(raw))},
    )
    response = conn.getresponse()
    status = response.status
    frames: list[tuple[str, dict, float]] = []
    event = None
    data_lines: list[str] = []
    first_byte = None
    while True:
        line = response.fp.readline()
        if not line:
            break
        if first_byte is None:
            first_byte = time.monotonic() - started
        text = line.decode("utf-8", "replace").rstrip("\n")
        if text == "":
            if event is not None:
                payload_raw = "\n".join(data_lines)
                try:
                    payload = json.loads(payload_raw) if payload_raw else {}
                except json.JSONDecodeError:
                    payload = {"__unparsable__": payload_raw}
                frames.append((event, payload, time.monotonic() - started))
            event, data_lines = None, []
        elif text.startswith("event: "):
            event = text[len("event: "):]
        elif text.startswith("data: "):
            data_lines.append(text[len("data: "):])
    total = time.monotonic() - started
    conn.close()
    return frames, (first_byte if first_byte is not None else total), status, total


def main() -> int:
    report = Report("项3 端到端闭环 + 项4 SSE 五场景")
    runs = fresh("e2e")
    with Server(runs) as server:
        base, port = server.base, server.port

        log("\n### 项3：真实 HTTP 端到端闭环（临时 runs-dir）")
        log(f"runs-dir = {runs}")
        log(f"命令等价：py -3 server.py --host 127.0.0.1 --port {port} --runs-dir {runs}")

        status, headers, text = get(base, "/api/bootstrap")
        boot = js(text)
        report.check(
            "3.1 GET /api/bootstrap → 200 ok:true",
            status == 200 and boot.get("ok") is True,
            f"status={status} algorithms={len(boot['condopt']['algorithms'])} "
            f"scenarios={len(boot['scenarios'])} cache-control={headers.get('Cache-Control')} "
            f"content-type={headers.get('Content-Type')}",
        )

        status, _, text = action(base, "prepare", payload={"answers": short_answers()})
        prep = js(text)
        sections = [s["key"] for s in prep.get("result", {}).get("sections", [])]
        report.check(
            "3.2 POST /api/action prepare → 200 ok:true",
            status == 200 and prep.get("ok") is True,
            f"status={status} sections={sections} route={prep.get('result', {}).get('route', {}).get('algorithm')} "
            f"落盘={'是' if (runs / 'demo').exists() else '否(正确)'}",
        )

        created = init_study(base, run_id="demo")
        report.check(
            "3.3 POST /api/action init → 200 ok:true",
            created["ok"] is True,
            f"status=200 run={created['result']['run']}",
        )

        status, _, text = action(base, "ask", run="demo", payload={"n": 4})
        asked = js(text)
        pending = asked["result"]["pending"]
        report.check(
            "3.4 POST /api/action ask → 200 ok:true",
            status == 200 and asked.get("ok") is True and len(pending) == 4,
            f"status={status} pending_ids={[p['id'] for p in pending]} "
            f"plates={sorted({p['plate'] for p in pending})} has_pending={asked['result']['run']['has_pending']}",
        )

        rows = [{"id": item["id"], "y": 10.0 + i} for i, item in enumerate(pending)]
        status, _, text = action(base, "tell", run="demo", payload={"observations": rows})
        told = js(text)
        report.check(
            "3.5 POST /api/action tell → 200 ok:true",
            status == 200 and told.get("ok") is True,
            f"status={status} accepted={told['result']['accepted']} n_obs={told['result']['run']['n_obs']} "
            f"best={told['result']['run']['best']} pending_now={told['result'].get('still_pending')}",
        )

        status, _, text = action(base, "delivery", run="demo", payload={})
        delivered = js(text)
        report.check(
            "3.6 POST /api/action delivery → 200 ok:true",
            status == 200 and delivered.get("ok") is True,
            f"status={status} items={len(delivered['result']['delivery'])} "
            f"integrity_ok={delivered['result']['integrity'].get('ok')}",
        )

        status, _, text = action(base, "render", run="demo", payload={})
        rendered = js(text)
        dashboard = Path(rendered["result"]["dashboard"]) if rendered.get("ok") else None
        report.check(
            "3.7 POST /api/action render → 200 ok:true",
            status == 200 and rendered.get("ok") is True and dashboard is not None and dashboard.is_file(),
            f"status={status} dashboard={rendered['result']['dashboard']} png={rendered['result']['png']} "
            f"exists={bool(dashboard and dashboard.is_file())}",
        )

        # ------------------------------------------------------------------ #
        log("\n### 项4：/api/run 五场景 SSE")
        init_study(base, run_id="sse")
        gantt = contract_stages()

        plan = [
            ("kickoff", {"answers": short_answers()}),
            ("sample", {"run": "sse", "n": 2}),
            ("feedback", "__PENDING__"),
            ("delivery", {"run": "sse"}),
            ("board", {"run": "sse"}),
        ]
        for key, payload in plan:
            if payload == "__PENDING__":
                status, _, text = get(base, "/api/study?id=sse")
                study = js(text)["study"]
                payload = {
                    "run": "sse",
                    "observations": [{"id": item["id"], "y": 20.0 + i} for i, item in enumerate(study["pending"])],
                }

            frames, first_byte, status, total = sse_frames(port, {"scenario": key, "text": "", "files": [], "payload": payload})
            names = [name for name, _, _ in frames]
            stages = [data for name, data, _ in frames if name == "stage"]
            gantt_row = gantt[key]
            expected = gantt_row["stages"]

            allowed_ok = set(names) <= ALLOWED_EVENTS and names
            order_ok = names[0] == "meta" and names[-1] == "done"
            labels_running = [item["label"] for item in stages if item["status"] == "running"]
            labels_ok = labels_running == expected
            index_ok = sorted({item["index"] for item in stages}) == list(range(4)) and all(
                expected[item["index"]] == item["label"] for item in stages
            )
            error_free = "error" not in names
            delta_ok = any(name == "delta" for name in names)

            done = next((data for name, data, _ in frames if name == "done"), {})
            elapsed_ms = done.get("elapsed_ms")
            last_t = frames[-1][2] if frames else 0.0
            timeline = " ".join(f"{name}@{ts*1000:.0f}ms" for name, _, ts in frames)

            report.check(
                f"4.{key} 事件名合法（仅 meta|stage|delta|notice|error|done）",
                allowed_ok and order_ok,
                f"names={sorted(set(names))} 首帧={names[0]} 末帧={names[-1]}",
            )
            report.check(
                f"4.{key} stage.index 落在 §3.1 权威 stages 下标内",
                labels_ok and index_ok,
                f"running={labels_running} indices={sorted({i['index'] for i in stages})} "
                f"契约={expected}",
            )
            report.check(
                f"4.{key} 有 delta 且无 error 帧",
                delta_ok and error_free,
                f"delta={names.count('delta')} notice={names.count('notice')} error={names.count('error')}",
            )
            log(f"    时序：首字节 {first_byte*1000:.0f}ms 总耗时 {total*1000:.0f}ms "
                f"done.elapsed_ms={elapsed_ms} 末帧 {last_t*1000:.0f}ms")

            if key == "board":
                # 逐帧证明：若服务端把整段缓冲到执行完才吐，首帧到达时刻会 ≈ 总耗时；
                # 实测首帧远早于总耗时，且中间帧随执行进度陆续到达。
                early = sum(1 for _, _, ts in frames if ts < total * 0.5)
                streaming_ok = first_byte < total * 0.5 and early >= 3 and total > 0.1
                report.check(
                    "4.逐帧到达证明（board：首帧到达远早于流结束，中间帧随执行进度陆续到达）",
                    bool(streaming_ok),
                    f"首字节 {first_byte*1000:.0f}ms / 总 {total*1000:.0f}ms（{first_byte/total:.1%}），"
                    f"总耗时 50% 前已到达 {early}/{len(frames)} 帧；"
                    f"逐帧时间线: {timeline}",
                )
    return report.finish("v03_e2e.log")


if __name__ == "__main__":
    raise SystemExit(main())
