"""F4 回归探针：>64 字符的 study 由 condopt 本体建立时，工作台必须「列表可见且可打开」。

背景：F4 曾因读路径共用 64 字符上限而变成「列表可见但 /api/study 400」。
lead 在 task-6 裁定 (b)：**创建期严格（init ≤64）、读取期只做安全校验**。
本探针用 condopt 自己的 API 建一个 70 字符名 study，再断言：
  * `/api/runs` 列出它 **且** `/api/study` 返回 200；
  * 反向：创建期上限没被放松（`init` 65 字符仍 400）。

运行：py -3 _verify/probe_longid.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    Report, Server, WORKBENCH, action, fresh, get, js, log, short_answers,
)

sys.path.insert(0, str(WORKBENCH))
import condopt.prepare as condopt_prepare  # noqa: E402
from condopt.study import Study  # noqa: E402


def main() -> int:
    report = Report("F4 · >64 字符既有 study")
    runs = fresh("longid2")
    long_name = "campaign-" + "x" * 61  # 长度 70
    answers = short_answers()
    run_dir = runs / long_name

    # 完全绕开工作台校验：用 condopt 本体建（等价于 CLI 直接建目录跑 study）
    space, objective, context = condopt_prepare.parse_answers(answers)
    condopt_prepare.write_problem(answers, run_dir)
    Study.create(
        run_dir=run_dir, space=space, objective=objective, algorithm="sobol",
        params=None, seed=0, context=context, budget=8, batch_size=2, auto_route=True,
    )
    log(f"condopt 本体已建立 {len(long_name)} 字符 study：{run_dir}")
    log(f"  problem.json 存在={ (run_dir / 'problem.json').exists() }")

    with Server(runs) as server:
        listed = [item["id"] for item in js(get(server.base, "/api/runs")[2])["runs"]]
        visible = long_name in listed
        status, _, text = get(server.base, f"/api/study?id={long_name}")
        body = js(text)
        log(f"GET /api/runs            → 列表={[item[:20] + '…' if len(item) > 20 else item for item in listed]}")
        log(f"GET /api/study?id=<70字符> → HTTP {status} {body.get('error')} {str(body.get('message'))[:80]!r}")
        # task-6 裁定 (b)：创建期严格（≤64）、读取期只做安全校验，因此长名 study 必须「可见且可打开」。
        report.check(
            "F4（task-6 裁定 (b)）：condopt 本体建的长名 study「/api/runs 可见 且 /api/study 可打开」",
            visible and status == 200,
            f"70 字符 study：/api/runs 可见={visible}（期望 True）；"
            f"/api/study={status}（期望 200）→ {'符合裁定' if visible and status == 200 else '仍不一致'}",
        )
        # 反向：创建期上限不能被放松
        status65, _, text65 = action(server.base, "init", payload={"id": "C" * 65, "answers": short_answers()})
        report.check(
            "F4 反向：创建期 64 上限未被放松（init 65 字符仍 400）",
            status65 == 400 and js(text65).get("error") == "E_BAD_REQUEST",
            f"init 65 字符 → {status65} {js(text65).get('error')}",
        )
    return report.finish("probe_longid.log")


if __name__ == "__main__":
    raise SystemExit(main())
