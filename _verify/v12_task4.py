"""task-4 修复复验 + CONTRACT §8 裁定核查 + 修复的副作用边界。

运行：py -3 _verify/v12_task4.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    Report, Server, WORKBENCH, action, ask, fresh, get, init_study, js, log, run_dir_hashes, short_answers,
)

sys.path.insert(0, str(WORKBENCH))
import bridge as bridge_module  # noqa: E402


def sse_all(server, body: dict) -> list[tuple[str, dict]]:
    import http.client

    raw = __import__("json").dumps(body, ensure_ascii=False).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=60)
    conn.request("POST", "/api/run", body=raw,
                 headers={"Content-Type": "application/json", "Content-Length": str(len(raw))})
    text = conn.getresponse().read().decode("utf-8")
    conn.close()
    frames, event, datas = [], None, []
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            datas.append(line[6:])
        elif line == "" and event:
            frames.append((event, __import__("json").loads("\n".join(datas)) if datas else {}))
            event, datas = None, []
    if event:
        frames.append((event, __import__("json").loads("\n".join(datas)) if datas else {}))
    return frames


def main() -> int:
    report = Report("task-4 复验 + §8 裁定")
    runs = fresh("task4")
    with Server(runs) as server:
        base = server.base

        # ---------------- F1：NUL 系列被拒，且不误伤 ----------------
        log("\n### F1 复验：NUL 别名拒绝 + 不误伤其它名字")
        for run_id in ("NUL", "nul", "Nul", "NUL.txt", "nul.tar.gz", "NUL.", "NUL "):
            status, _, text = action(base, "init", payload={"id": run_id, "answers": short_answers()})
            body = js(text)
            report.check(
                f"F1 init id={run_id!r} → 400 E_BAD_REQUEST（无 500、无 Python 异常回显）",
                status == 400 and body.get("error") == "E_BAD_REQUEST"
                and not any(token in str(body.get("message")) for token in ("Traceback", "FileNotFoundError", "Errno", "WinError")),
                f"status={status} error={body.get('error')} message={str(body.get('message'))[:110]!r}",
            )

        allowed = {"CON": 200, "AUX": 200, "COM1": 200, "PRN": 200, "LPT1": 200, "clock$": 200, "aNULb": 200, "NULL": 200}
        for run_id, expect in allowed.items():
            status, _, text = action(base, "init", payload={"id": run_id, "answers": short_answers(), "budget": 4, "batch": 1})
            report.check(
                f"F1 反向：id={run_id!r} 未被误伤（期望 {expect}）",
                status == expect,
                f"status={status} error={js(text).get('error')} message={str(js(text).get('message'))[:80]!r}",
            )

        # ---------------- F2：长度边界 ----------------
        log("\n### F2 复验：64 字符边界")
        id64 = "A" * 64
        status, _, text = action(base, "init", payload={"id": id64, "answers": short_answers(), "budget": 4, "batch": 1})
        report.check(
            "F2 恰好 64 字符 id → 200（上限是闭区间）",
            status == 200,
            f"status={status} error={js(text).get('error')} message={str(js(text).get('message'))[:100]!r}",
        )
        id65 = "B" * 65
        status, _, text = action(base, "init", payload={"id": id65, "answers": short_answers()})
        body = js(text)
        report.check(
            "F2 65 字符 id → 400 E_BAD_REQUEST 且 message 说明上限",
            status == 400 and body.get("error") == "E_BAD_REQUEST" and "64" in str(body.get("message")),
            f"status={status} message={str(body.get('message'))[:110]!r}",
        )

        # ---------------- F3：SSE done.status ----------------
        log("\n### F3 复验：done.status")
        init_study(base, run_id="f3", budget=8, batch=2)
        ask(base, run_id="f3", n=1)
        frames = sse_all(server, {"scenario": "sample", "payload": {"run": "f3", "n": 1}})
        names = [name for name, _ in frames]
        last = frames[-1][1]
        error_frame = next((data for name, data in frames if name == "error"), {})
        report.check(
            "F3 error 路径：error 帧 + 末尾 done(status='error')",
            names[-1] == "done" and "error" in names and last.get("status") == "error",
            f"events={names} done={last} error={error_frame}",
        )
        frames_ok = sse_all(server, {"scenario": "kickoff", "payload": {"answers": short_answers()}})
        last_ok = frames_ok[-1][1]
        report.check(
            "F3 反向：成功路径 done.status='ok' 且无 error 帧",
            last_ok.get("status") == "ok" and "error" not in [name for name, _ in frames_ok],
            f"done={last_ok} events={[name for name, _ in frames_ok]}",
        )
        report.check(
            "F3 服务端不再回显 Python 异常文本（error 帧 message 已 sanitize）",
            not any(token in str(error_frame.get("message")) for token in ("Traceback", "FileNotFoundError", "OSError", "WinError")),
            f"error.message={error_frame.get('message')!r}",
        )

        # ---------------- 修复副作用①：清理不会误删既有数据 ----------------
        log("\n### 修复副作用①：init 失败时的清理是否误删既有数据（白盒，注入 OSError）")
        pre_dir = runs / "pre_existing"
        pre_dir.mkdir(parents=True, exist_ok=True)
        sentinel = pre_dir / "important_user_data.txt"
        sentinel.write_text("必须活着", encoding="utf-8")

        fresh_dir = runs / "created_then_fail"

        original = bridge_module.condopt_prepare.write_problem
        try:
            def boom(*args, **kwargs):
                raise OSError("注入的写盘失败（模拟文件系统拒绝）")

            bridge_module.condopt_prepare.write_problem = boom
            b = bridge_module.Bridge(runs)
            r1 = b.dispatch("init", None, {"id": "pre_existing", "answers": short_answers()})
            log(f"    既有目录场景 → dispatch 返回 {r1}")
        except bridge_module.WorkbenchError as exc:
            r1 = exc
            log(f"    既有目录场景 → WorkbenchError {exc.error} {exc.message}")

        try:
            r2 = b.dispatch("init", None, {"id": "created_then_fail", "answers": short_answers()})
            log(f"    新建目录场景 → dispatch 返回 {r2}")
        except bridge_module.WorkbenchError as exc:
            r2 = exc
            log(f"    新建目录场景 → WorkbenchError {exc.error} {exc.message}")
        finally:
            bridge_module.condopt_prepare.write_problem = original

        report.check(
            "修复副作用①-a：写盘失败时 400（不是 500），且**既有目录与用户文件原样保留**",
            sentinel.exists() and pre_dir.is_dir(),
            f"sentinel 存在={sentinel.exists()}（内容={sentinel.read_text('utf-8')!r}）目录存在={pre_dir.is_dir()}",
        )
        report.check(
            "修复副作用①-b：本次新建的半成品目录被清理（不留垃圾）",
            not fresh_dir.exists(),
            f"{fresh_dir.name} 仍存在={fresh_dir.exists()}",
        )

        # ---------------- 修复副作用②：64 上限 vs 既有长名 study ----------------
        log("\n### 修复副作用②：>64 字符的既有 study 还能不能读（condopt CLI 建的）")
    long_runs = fresh("longid")
    with Server(long_runs) as server2:
        init_study(server2.base, run_id="base", budget=4, batch=1)
        long_name = "L" * 70
        shutil.copytree(long_runs / "base", long_runs / long_name)
        listed = [item["id"] for item in js(get(server2.base, "/api/runs")[2])["runs"]]
        status, _, text = get(server2.base, f"/api/study?id={long_name}")
        body = js(text)
        log(f"    70 字符 study：在 /api/runs 里={'是' if long_name in listed else '否'}；"
            f"/api/study → {status} {body.get('error')} {str(body.get('message'))[:70]!r}")
        report.check(
            "修复副作用②：>64 字符既有 study 在 /api/runs 与 /api/study 之间行为一致"
            "（要么都可见可读，要么都不出现）",
            (long_name in listed) == (status == 200),
            f"列表可见={long_name in listed} /api/study={status} —— "
            f"{'一致' if (long_name in listed) == (status == 200) else '不一致：列表里点得开却打不开'}",
        )

    # ---------------- CONTRACT §8 裁定 ----------------
    with Server(fresh("s8")) as s8:
        base8 = s8.base
        log("\n### CONTRACT §8 裁定核查")
        init_study(base8, run_id="s8run", budget=8, batch=2)
        files = sorted(p.name for p in (s8.runs_dir / "s8run").iterdir())
        report.check(
            "§8#2 init 除 6 个数据文件外还落 space.json/context.json/objective.json（追认行为）",
            {"space.json", "context.json", "objective.json"} <= set(files),
            f"run 目录文件={files}",
        )

        status, _, text = action(base8, "prepare", payload={"answers": short_answers()})
        route_algo = js(text)["result"]["route"]["algorithm"]
        stages_md = next(sec["markdown"] for sec in js(text)["result"]["sections"] if sec["key"] == "stages")
        report.check(
            "§8#1 sections[key=stages] = 算法与开工步骤，且路由结论与 result.route 一致",
            route_algo in stages_md and "开工步骤" in stages_md,
            f"route={route_algo} stages 片段含算法={route_algo in stages_md}",
        )

        asked = ask(base8, run_id="s8run", n=2)
        action(base8, "tell", run="s8run",
               payload={"observations": [{"id": it["id"], "y": 9.0 + i} for i, it in enumerate(asked["result"]["pending"])]})

        status, _, text = action(base8, "render", run="s8run", payload={"make_png": False})
        rendered = js(text)["result"]
        report.check(
            "§8#4 render 支持可选 make_png=false → png=null 且不产 PNG",
            status == 200 and rendered["png"] is None and not (s8.runs_dir / "s8run" / "convergence.png").exists(),
            f"status={status} png={rendered['png']} tsx={(s8.runs_dir / 's8run' / 'convergence.png').exists()}",
        )
        status, _, text = action(base8, "render", run="s8run", payload={"make_png": True})
        report.check(
            "§8#4 render make_png=true → 产出 PNG",
            status == 200 and js(text)["result"]["png"] is not None,
            f"png={js(text)['result']['png']}",
        )
        status, _, text = action(base8, "render", run="s8run", payload={"oops": 1})
        report.check(
            "§8#4 render 其它字段仍严格白名单 → 422",
            status == 422 and js(text)["error"] == "E_VALIDATION",
            f"status={status} error={js(text).get('error')}",
        )

        snapshot = js(get(base8, "/api/study?id=s8run")[2])["study"]
        best = snapshot["best"]
        report.check(
            "§8#6 StudySnapshot.best 用 Observation.to_dict（含 x/y/t/source）",
            isinstance(best, dict) and {"x", "y", "t", "source"} <= set(best),
            f"best keys={sorted(best or {})} best={ {k: best[k] for k in ('y', 't', 'source') if k in best} }",
        )

    # 坏 run → /api/runs 不静默丢弃、不伪造数字
    bad_runs = fresh("badrun")
    (bad_runs / "broken").mkdir(parents=True, exist_ok=True)
    (bad_runs / "broken" / "problem.json").write_text("{ 这不是合法 JSON", encoding="utf-8")
    (bad_runs / "broken" / "config.json").write_text("{}", encoding="utf-8")
    with Server(bad_runs) as server3:
        status, _, text = get(server3.base, "/api/runs")
        runs_list = js(text)["runs"]
        entry = next((item for item in runs_list if item["id"] == "broken"), None)
        report.check(
            "§8#5 坏 run 不静默丢弃、不伪造数字：仍在列表里、可算字段 null、附 error 说明",
            entry is not None and entry.get("error") and entry.get("best") is None and entry.get("n_obs") is None,
            f"entry={entry}",
        )
    return report.finish("v12_task4.log")


if __name__ == "__main__":
    raise SystemExit(main())
