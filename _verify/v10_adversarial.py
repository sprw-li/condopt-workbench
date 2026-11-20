"""验收项 10（对抗性必测）+ 项 11（verifier 自选的额外边界）。

运行：py -3 _verify/v10_adversarial.py
"""

from __future__ import annotations

import concurrent.futures
import http.client as http_client
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    Report, Server, action, ask, fresh, get, http, init_study, js, log, run_dir_hashes, short_answers,
)

STRICT = json.JSONDecoder(parse_constant=lambda name: (_ for _ in ()).throw(ValueError(f"非法常量 {name}")))


def raw_post(port: int, path: str, headers: dict, body: bytes = b"", read=True):
    conn = http_client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.putrequest("POST", path, skip_accept_encoding=True)
        for key, value in headers.items():
            conn.putheader(key, value)
        conn.endheaders()
        if body:
            conn.send(body)
        if not read:
            return None
        response = conn.getresponse()
        return response.status, response.read().decode("utf-8", "replace")
    finally:
        conn.close()


def main() -> int:
    report = Report("项10 对抗性 + 项11 额外边界")
    with Server(fresh("adv")) as server:
        base, port, runs = server.base, server.port, server.runs_dir

        # ---------------------------------------------------------------- #
        log("\n### 项10.1 已有 pending 时再 ask → E_CONFLICT，且预算/磁盘零变化")
        init_study(base, run_id="demo")
        first = ask(base, run_id="demo", n=2)
        remaining_before = first["result"]["run"]["remaining"]
        hashes_before = run_dir_hashes(runs / "demo")
        status, _, text = action(base, "ask", run="demo", payload={"n": 2})
        body = js(text)
        report.check(
            "10.1 重复 ask → 409 E_CONFLICT",
            status == 409 and body.get("error") == "E_CONFLICT" and body.get("message") and body.get("hint"),
            f"status={status} error={body.get('error')} message={body.get('message')!r} hint={body.get('hint')!r}",
        )
        after = get(base, "/api/study?id=demo")
        study = js(after[2])["study"]
        report.check(
            "10.1 冲突后 pending 未变多、剩余预算未多扣、run 目录哈希不变",
            len(study["pending"]) == 2
            and study["remaining"] == remaining_before
            and run_dir_hashes(runs / "demo") == hashes_before,
            f"pending={len(study['pending'])} remaining={study['remaining']}（冲突前 {remaining_before}）"
            f" 磁盘变化={run_dir_hashes(runs / 'demo') != hashes_before}",
        )

        # ---------------------------------------------------------------- #
        log("\n### 项10.2 /api/action 未知字段 → E_VALIDATION")
        for label, payload in (
            ("顶层未知字段", {"action": "ask", "run": "demo", "oops": 1}),
            ("payload 未知字段（ask.plat 拼错）", {"action": "ask", "run": "demo", "payload": {"plat": "P1"}}),
            ("payload 未知字段（delivery 带 n）", {"action": "delivery", "run": "demo", "payload": {"n": 1}}),
        ):
            status, _, text = http(base, "POST", "/api/action", body=payload)
            body = js(text)
            report.check(
                f"10.2 {label} → 422 E_VALIDATION 且并非静默忽略",
                status == 422 and body.get("error") == "E_VALIDATION" and ("未知字段" in body.get("message", "")),
                f"status={status} error={body.get('error')} message={body.get('message')!r}",
            )

        # ---------------------------------------------------------------- #
        log("\n### 项10.3 同一 id 重复 tell → 幂等")
        hashes_t1 = run_dir_hashes(runs / "demo")
        rows = [{"id": item["id"], "y": 10.0 + i} for i, item in enumerate(study["pending"])]
        status, _, text = action(base, "tell", run="demo", payload={"observations": rows})
        told1 = js(text)
        hashes_t2 = run_dir_hashes(runs / "demo")
        status, _, text = action(base, "tell", run="demo", payload={"observations": rows})
        told2 = js(text)
        hashes_t3 = run_dir_hashes(runs / "demo")
        dataset_lines = [line for line in (runs / "demo" / "dataset.jsonl").read_text("utf-8").splitlines() if line.strip()]
        rounds = json.loads((runs / "demo" / "rounds.json").read_text("utf-8"))
        report.check(
            "10.3 首次 tell 计入、第二次同 id tell accepted=0 且磁盘逐字节不变",
            told1["result"]["accepted"] == 2
            and told2["result"]["accepted"] == 0
            and told2["result"]["run"]["n_obs"] == told1["result"]["run"]["n_obs"]
            and hashes_t3 == hashes_t2
            and len(dataset_lines) == 2
            and len(rounds) == 1,
            f"accepted={told1['result']['accepted']}→{told2['result']['accepted']} "
            f"n_obs={told1['result']['run']['n_obs']}→{told2['result']['run']['n_obs']} "
            f"dataset 行数={len(dataset_lines)} rounds={len(rounds)} 二次磁盘变化={hashes_t3 != hashes_t2}",
        )

        # ---------------------------------------------------------------- #
        log("\n### 项10.4 任何 GET /api/* 零副作用（§4.2-2）")
        before = run_dir_hashes(runs / "demo")
        for _ in range(5):
            for path in ("/api/bootstrap", "/api/runs", "/api/study?id=demo"):
                status, _, _ = get(base, path)
                assert status == 200
        report.check(
            "10.4 GET /api/*（含 /api/study 反复 5 轮）不改动 run 目录",
            run_dir_hashes(runs / "demo") == before,
            f"文件数={len(before)} 哈希全等={run_dir_hashes(runs / 'demo') == before}",
        )

        # ================================================================ #
        log("\n### 项11.1 并发两个 ask：必须被串行锁挡住（只有一个成功）")
        init_study(base, run_id="race")
        barrier = threading.Barrier(2)

        def do_ask():
            barrier.wait()
            return action(base, "ask", run="race", payload={"n": 2})

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = [f.result() for f in (pool.submit(do_ask), pool.submit(do_ask))]
        codes = sorted(item[0] for item in results)
        bodies = [js(item[2]) for item in results]
        ok_count = sum(1 for b in bodies if b.get("ok"))
        conflict_count = sum(1 for b in bodies if b.get("error") == "E_CONFLICT")
        snapshot = js(get(base, "/api/study?id=race")[2])["study"]
        report.check(
            "11.1 并发 ask：恰好 1 个成功、1 个 E_CONFLICT，pending 不重复生成",
            codes == [200, 409] and ok_count == 1 and conflict_count == 1 and len(snapshot["pending"]) == 2,
            f"状态码={codes} ok={ok_count} conflict={conflict_count} pending={len(snapshot['pending'])} "
            f"消息={[b.get('message') for b in bodies]}",
        )

        log("\n### 项11.2 并发两个 tell（同 id）：不得重复计入")
        init_study(base, run_id="race2")
        asked = ask(base, run_id="race2", n=2)
        rows = [{"id": item["id"], "y": 1.0 + i} for i, item in enumerate(asked["result"]["pending"])]
        barrier2 = threading.Barrier(2)

        def do_tell():
            barrier2.wait()
            return action(base, "tell", run="race2", payload={"observations": rows})

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = [f.result() for f in (pool.submit(do_tell), pool.submit(do_tell))]
        accepted = sorted(js(item[2])["result"]["accepted"] for item in results if item[0] == 200)
        snap2 = js(get(base, "/api/study?id=race2")[2])["study"]
        report.check(
            "11.2 并发 tell：n_obs 只计一次（=2），无重复写入",
            snap2["n_obs"] == 2 and accepted == [0, 2],
            f"accepted={accepted} n_obs={snap2['n_obs']} 状态码={[item[0] for item in results]}",
        )

        log("\n### 项11.3 并发两个 init（同 id）：不得静默覆盖")
        barrier3 = threading.Barrier(2)

        def do_init():
            barrier3.wait()
            return action(base, "init", payload={"id": "race3", "answers": short_answers(), "budget": 8, "batch": 2})

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = [f.result() for f in (pool.submit(do_init), pool.submit(do_init))]
        codes3 = sorted(item[0] for item in results)
        report.check(
            "11.3 并发 init 同 id：1×200 + 1×409 E_CONFLICT",
            codes3 == [200, 409],
            f"状态码={codes3} 错误={[js(item[2]).get('error') for item in results]}",
        )

        # ---------------------------------------------------------------- #
        log("\n### 项11.4 /api/study?id= 各种畸形输入")
        from urllib.parse import quote

        def qs(run_id: str) -> str:
            return f"id={quote(run_id, safe='')}"

        cases = [
            ("id=", 400, "E_BAD_REQUEST"),
            ("", 400, "E_BAD_REQUEST"),
            (qs("   "), 400, "E_BAD_REQUEST"),
            ("id=%20%20", 400, "E_BAD_REQUEST"),
            (qs("不存在的study"), 404, "E_NOT_FOUND"),
            (qs("demo "), 200, None),
            # task-6 裁定 (b)：读路径不再套 MAX_RUN_ID_LENGTH，「超长且不存在」= 目录不存在 → 404。
            (qs("x" * 5000), 404, "E_NOT_FOUND"),
            (qs("中文名字"), 404, "E_NOT_FOUND"),
            ("id=a%00b", 400, "E_BAD_REQUEST"),
            (qs("demo;rm"), 404, "E_NOT_FOUND"),
            (qs(".."), 400, "E_BAD_REQUEST"),
            (qs("CON"), None, None),
            (qs("NUL"), None, None),
        ]
        for query, expect_status, expect_error in cases:
            path = "/api/study" + (f"?{query}" if query else "")
            status, _, text = get(base, path)
            parsed = js(text)
            label = query if len(query) <= 24 else query[:24] + f"…({len(query)}字符)"
            if expect_status is None:
                report.check(
                    f"11.4 GET {path[:40]!r} 是受控响应（非 500、非 200 假成功）",
                    status in (400, 404) and parsed.get("ok") is False,
                    f"status={status} error={parsed.get('error')} message={parsed.get('message')!r}",
                )
            else:
                report.check(
                    f"11.4 GET /api/study?{label} → {expect_status} {expect_error}",
                    status == expect_status and (expect_error is None or parsed.get("error") == expect_error),
                    f"status={status} error={parsed.get('error')} message={parsed.get('message')!r}",
                )

        log("\n### 项11.4b 错误 message 的 id 回显必须有界（task-7）")
        MESSAGE_LIMIT = 200
        long_cases = [
            ("超长且不存在", "x" * 5003, 404, "E_NOT_FOUND"),
            ("超长且不存在（重复字符）", "y" * 5003, 404, "E_NOT_FOUND"),
            ("NUL + 超长", "a\x00" + "z" * 5000, 400, "E_BAD_REQUEST"),
            ("路径穿越 + 超长", "../" + "w" * 5000, 400, "E_BAD_REQUEST"),
        ]
        for label, plain, expected_status, expected_error in long_cases:
            status, _, text = get(base, f"/api/study?id={quote(plain, safe='')}")
            parsed = js(text)
            message = str(parsed.get("message") or "")
            hint = str(parsed.get("hint") or "")
            # 「完整原串」既查字面量，也查长度足以证明被截断的 50 字符头/尾
            full_echo = plain in message or plain in hint or plain[:50] in message or plain[-50:] in message
            log(f"    [{label}] id 长 {len(plain)} → status={status} message 长度={len(message)} "
                f"hint 长度={len(hint)} message={message[:90]!r}")
            report.check(
                f"11.4b {label}：状态码/错误码符合设计（{expected_status} {expected_error}）",
                status == expected_status and parsed.get("error") == expected_error,
                f"status={status} error={parsed.get('error')}",
            )
            report.check(
                f"11.4b {label}：message 长度有界（<{MESSAGE_LIMIT}）且不含完整原串",
                len(message) < MESSAGE_LIMIT and not full_echo,
                f"message={len(message)} 字符（上限 {MESSAGE_LIMIT}）含完整原串={full_echo} "
                f"message={message[:120]!r}",
            )

        log("\n### 项11.5 SSE 在 action 抛错时仍发 error + done")
        init_study(base, run_id="sseerr")
        ask(base, run_id="sseerr", n=1)
        conn = http_client.HTTPConnection("127.0.0.1", port, timeout=60)
        raw = json.dumps({"scenario": "sample", "payload": {"run": "sseerr", "n": 1}}).encode()
        conn.request("POST", "/api/run", body=raw, headers={"Content-Type": "application/json", "Content-Length": str(len(raw))})
        resp = conn.getresponse()
        text = resp.read().decode("utf-8")
        conn.close()
        names = [line[len("event: "):] for line in text.splitlines() if line.startswith("event: ")]
        done_data = [json.loads(line[len("data: "):]) for line in text.splitlines() if line.startswith("data: ")]
        last = done_data[-1] if done_data else {}
        report.check(
            "11.5 冲突场景：HTTP 200 + error 帧 + 末尾 done（契约 §4「最后仍发 done」）",
            resp.status == 200 and names and names[0] == "meta" and names[-1] == "done" and "error" in names,
            f"status={resp.status} events={names} 末帧={last}",
        )
        report.check(
            "11.5 done.status 语义自洽（error 路径必须为 error，与 server.py 兜底路径一致；回归 F3）",
            last.get("status") == "error",
            f"末帧 done={last}",
        )

        log("\n### 项11.6 /api/run 预校验失败时在开流前返回 JSON 4xx")
        for label, body, expect in (
            ("未知 scenario", {"scenario": "nope", "payload": {}}, 400),
            ("顶层未知字段", {"scenario": "kickoff", "extra": 1}, 422),
            ("payload 未知字段", {"scenario": "kickoff", "payload": {"answers": short_answers(), "x": 1}}, 422),
            ("needs_run 但没有 run", {"scenario": "sample", "payload": {"n": 1}}, 400),
            ("text 不是字符串", {"scenario": "kickoff", "text": 5, "payload": {"answers": short_answers()}}, 422),
            ("files 不是数组", {"scenario": "kickoff", "files": "x", "payload": {"answers": short_answers()}}, 422),
            ("payload 不是对象", {"scenario": "kickoff", "payload": [1]}, 422),
        ):
            status, headers, text = http(base, "POST", "/api/run", body=body)
            parsed = js(text)
            report.check(
                f"11.6 /api/run {label} → {expect} JSON（未进入 SSE 流）",
                status == expect and parsed.get("error") and "application/json" in headers.get("Content-Type", ""),
                f"status={status} error={parsed.get('error')} content-type={headers.get('Content-Type')}",
            )

        # ---------------------------------------------------------------- #
        log("\n### 项11.7 请求体边界")
        status, text = raw_post(port, "/api/action", {"Content-Length": "9000000"})
        report.check(
            "11.7 Content-Length 9MB（>8MB 上限）→ 400 且不读体",
            status == 400 and js(text).get("error") == "E_BAD_REQUEST",
            f"status={status} body={text.strip()[:120]}",
        )

        sock = socket.create_connection(("127.0.0.1", port), timeout=30)
        sock.sendall(b"POST /api/action HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        head = sock.recv(4096).decode("utf-8", "replace")
        sock.close()
        report.check(
            "11.7 缺 Content-Length → 400",
            " 400 " in head and "Content-Length" in head,
            f"响应首行={head.splitlines()[0] if head else '空'}",
        )

        for label, headers, body, expect in (
            ("空请求体", {"Content-Length": "0"}, b"", 400),
            ("非法 JSON", {"Content-Length": "9", "Content-Type": "application/json"}, b"{not json", 400),
            ("JSON 数组而非对象", {"Content-Length": "5", "Content-Type": "application/json"}, b"[1,2]", 400),
            ("非 UTF-8 字节", {"Content-Length": "2", "Content-Type": "application/json"}, b"\xff\xfe", 400),
            ("Content-Length 非数字", {"Content-Length": "abc"}, b"", 400),
        ):
            status, text = raw_post(port, "/api/action", headers, body)
            report.check(
                f"11.7 {label} → {expect}",
                status == expect,
                f"status={status} body={text.strip()[:100]!r}",
            )

        # ---------------------------------------------------------------- #
        log("\n### 项11.8 run id 奇怪形态（完整设备名矩阵见 probe_devnames.py）")
        for run_id, expect in (("a b", 404), ("带空格 的", 404), (" demo ", 200)):
            status, _, text = get(base, f"/api/study?id={quote(run_id, safe='')}")
            report.check(
                f"11.8 /api/study?id={run_id!r} → {expect}",
                status == expect,
                f"status={status} error={js(text).get('error')}",
            )

        # 设备名对照：CON/AUX/COM1/PRN/LPT1 在本机是真能建的目录（probe_devnames.py 已逐个验证
        # 「200 且磁盘/列表/快照/二次 init 全部一致」），所以不该报缺陷；NUL 不是。
        status, _, text = action(base, "init", payload={"id": "NUL", "answers": short_answers()})
        body = js(text)
        report.check(
            "11.8 init id='NUL' → 客户端输入应给 4xx（回归 F1；不得 500、不得回显 Python 异常）",
            status in (400, 422),
            f"status={status} error={body.get('error')} message={str(body.get('message'))[:150]!r}",
        )

        long_id = "L" * 300
        status, _, text = action(base, "init", payload={"id": long_id, "answers": short_answers()})
        body = js(text)
        report.check(
            "11.8 init id=300 字符 → 客户端输入应给 4xx（回归 F2；不得 500 WinError 123）",
            status in (400, 422),
            f"status={status} error={body.get('error')} message={str(body.get('message'))[:150]!r}",
        )

        log("\n### 项11.9 answers 里的 UTF-8 中文变量名")
        answers = short_answers()
        answers["variables"] = [
            {"name": "温度", "kind": "continuous", "low": 40.0, "high": 120.0, "unit": "摄氏度"},
            {"name": "溶剂", "kind": "categorical", "choices": ["乙腈", "乙醇"]},
        ]
        status, _, text = action(base, "init", payload={"id": "中文run", "answers": answers, "algorithm": "sobol", "budget": 4, "batch": 1})
        body = js(text)
        if status == 200:
            rid = "中文run"
            st, _, tx = get(base, f"/api/study?id={quote(rid, safe='')}")
            snap = js(tx)["study"]
            ok_names = [v.get("name") for v in snap["problem"]["space"]["variables"]]
            report.check(
                "11.9 中文变量名 init → 200 且快照里中文名原样保留",
                st == 200 and "温度" in ok_names and "溶剂" in ok_names,
                f"init status={status} snapshot status={st} variables={ok_names}",
            )
        else:
            report.check(
                "11.9 中文变量名 init → 受控 E_VALIDATION（不是 500）",
                status == 422 and body.get("error") == "E_VALIDATION",
                f"status={status} error={body.get('error')} message={str(body.get('message'))[:160]!r}",
            )

        log("\n### 项11.10 响应必须是严格 JSON（无 NaN/Infinity）")
        ok_strict = True
        details = []
        for path in ("/api/bootstrap", "/api/runs", "/api/study?id=demo"):
            _, _, text = get(base, path)
            try:
                STRICT.decode(text)
            except ValueError as exc:
                ok_strict = False
                details.append(f"{path}: {exc}")
        # render/recommend 结果里可能有非有限浮点，重点看 /api/study
        status, _, text = action(base, "recommend", run="demo", payload={})
        try:
            STRICT.decode(text)
        except ValueError as exc:
            ok_strict = False
            details.append(f"recommend: {exc}")
        report.check("11.10 所有 JSON 响应可被严格解析（无 NaN/Infinity）", ok_strict, f"{details or '全部严格可解析'}")

        log("\n### 项11.11 --runs-dir 指向不存在的深层目录")
    nested = fresh("deep") / "a" / "b" / "c"
    with Server(nested) as server2:
        status, _, text = get(server2.base, "/api/runs")
        report.check(
            "11.11 --runs-dir 不存在时服务仍能起、/api/runs 返回空列表",
            status == 200 and js(text).get("runs") == [],
            f"status={status} runs={js(text).get('runs')}",
        )
        created = action(server2.base, "init", payload={"id": "deeprun", "answers": short_answers(), "budget": 4, "batch": 1})
        report.check(
            "11.11 init 自动创建 runs-dir 并可读写",
            created[0] == 200 and nested.joinpath("deeprun", "problem.json").exists(),
            f"status={created[0]} 目录已建={nested.joinpath('deeprun').exists()}",
        )

    return report.finish("v10_adversarial.log")


if __name__ == "__main__":
    raise SystemExit(main())
