"""迁移 3：`__main__` 从 `bp.guard_residue(main)` 换成 `bp.run_guarded(main, "<tag>")`。

`run_guarded` = 串行加锁（CONTRACT §11.5-6：浏览器驱动必须串行跑；并发冲突报**环境/并发类失败**并附证据）
+ `guard_residue`（跑完自证「本进程新增残留 == 0」，跨 settle 多次计数）。
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

TAGS = {
    "v21_geom_states.py": "states",
    "v21_geom_matrix.py": "matrix",
    "v21_geom_diag.py": "geomdiag",
    "v21_crosscheck.py": "crosscheck",
    "v21_narrow_layout.py": "narrow",
    "v21_frontend_kickoff.py": "fe",
    "v21_advparams_space.py": "advspace",
    "v21_advanced_params_overlap.py": "advparams",
    "v21_param_overlap.py": "paramoverlap",
    "v21_param_overlap2.py": "paramoverlap2",
}


def main() -> int:
    for name, tag in TAGS.items():
        path = HERE / name
        text = path.read_text(encoding="utf-8")
        original = text
        text = text.replace("raise SystemExit(bp.guard_residue(main))",
                            f'raise SystemExit(bp.run_guarded(main, "{tag}"))')
        if text != original:
            path.write_text(text, encoding="utf-8")
            print(f"[已改] {name}  → run_guarded(main, \"{tag}\")")
        else:
            already = "run_guarded" in text
            print(f"[{'已是' if already else '!! 未找到 guard_residue'}] {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
