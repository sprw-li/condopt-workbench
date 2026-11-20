"""定向复现：Windows 设备名 run id（CON/AUX/COM1/PRN/NUL）与超长 id 的行为。

运行：py -3 _verify/probe_devnames.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import Report, Server, action, fresh, get, js, log, short_answers  # noqa: E402


def snapshot(base: str, run_id: str):
    status, _, text = get(base, f"/api/study?id={run_id}")
    return status, js(text)


def main() -> int:
    report = Report("复现 · Windows 设备名 / 超长 run id")
    runs = fresh("devname")
    with Server(runs) as server:
        base = server.base
        for run_id in ("CON", "AUX", "COM1", "PRN", "NUL", "LPT1", "clock$"):
            status, _, text = action(base, "init", payload={"id": run_id, "answers": short_answers(), "budget": 8, "batch": 2})
            body = js(text)
            on_disk = (runs / run_id).is_dir() and (runs / run_id / "problem.json").exists()
            listed = [r["id"] for r in js(get(base, "/api/runs")[2])["runs"]]
            snap_status, snap = snapshot(base, run_id)
            # 第二次 init 同 id：应 E_CONFLICT（说明状态真的持久化了）
            again = action(base, "init", payload={"id": run_id, "answers": short_answers(), "budget": 8, "batch": 2})
            again_body = js(again[2])
            log(
                f"id={run_id!r:10} init={status} ok={body.get('ok')} error={body.get('error')} "
                f"| 磁盘目录={ (runs / run_id).is_dir() } problem.json={(runs / run_id / 'problem.json').exists()} "
                f"| 在 /api/runs 里={'是' if run_id in listed else '否'} | /api/study={snap_status} "
                f"| 二次 init={again[0]} {again_body.get('error')}"
            )
            report.check(
                f"init id={run_id!r}：ok:true 必须与磁盘/列表/快照一致",
                (status == 200) == bool(on_disk) and (status == 200) == (run_id in listed) and snap_status == status,
                f"init={status} 磁盘={on_disk} 列表={run_id in listed} 快照={snap_status} "
                f"二次 init={again[0]}({again_body.get('error')})",
            )
            # 「不静默重复建」只对**真的建成功过**的 id 成立：若首次就被校验层拒绝（从未创建），
            # 第二次报「run 已存在」反而是假话，此时正确行为是同一个 4xx。
            if status == 200:
                report.check(
                    f"init id={run_id!r}：重复 init 必须 E_CONFLICT（不静默重复建）",
                    again[0] == 409,
                    f"二次 init={again[0]} {again_body.get('error')} {str(again_body.get('message'))[:80]!r}",
                )
            else:
                report.check(
                    f"init id={run_id!r}：首次即被拒的 id，二次 init 必须同样 4xx（不能变成 409 假话）",
                    again[0] == status and again[0] in (400, 422),
                    f"首次={status}({body.get('error')}) 二次={again[0]}({again_body.get('error')})",
                )

        long_id = "L" * 300
        status, _, text = action(base, "init", payload={"id": long_id, "answers": short_answers()})
        log(f"id=300字符 init → {status} {json.dumps(js(text), ensure_ascii=False)[:220]}")
        report.check(
            "超长 id 是客户端错误（4xx）而不是 500 E_INTERNAL",
            status in (400, 404, 409, 422),
            f"status={status} error={js(text).get('error')} message={str(js(text).get('message'))[:160]!r}",
        )

        # 前端下拉框会不会把这些名字塞进 UI
        listed = js(get(base, "/api/runs")[2])["runs"]
        log(f"/api/runs 共 {len(listed)} 条：{[item['id'] for item in listed]}")
    return report.finish("probe_devnames.log")


if __name__ == "__main__":
    raise SystemExit(main())
