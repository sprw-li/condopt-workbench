"""迁移 2：把各驱动的收尾从 `proc.terminate()` 改成**先树杀**。

根因（实测）：Edge 会派生多个子进程；脚本先 `proc.terminate()` 杀掉父进程后，
子进程被 reparent，此时 `taskkill /T /PID <已死的父>` **再也走不到它们** → profile 被占住删不掉。
所以 `bp.kill_tree(proc)` 必须在父进程**还活着**时调用（`taskkill /T` 才能遍历子树），
再由 `bp.cleanup()` 按 profile 反查清掉残余持有者 + 删除 + 连续两次确认。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = [
    "v21_geom_states.py", "v21_geom_matrix.py", "v21_geom_diag.py", "v21_crosscheck.py",
    "v21_narrow_layout.py", "v21_frontend_kickoff.py", "v21_advparams_space.py",
    "v21_advanced_params_overlap.py", "v21_param_overlap.py", "v21_param_overlap2.py",
]

#: 老写法（terminate → wait → kill 三件套）
OLD = re.compile(
    r"( *)proc\.terminate\(\)\n"
    r"(?: *.+\n)*?"                                  # 中间的 try/except 行
    r" *proc\.kill\(\)\n"
)


def main() -> int:
    for name in FILES:
        path = HERE / name
        text = path.read_text(encoding="utf-8")
        original = text
        notes = []

        # 1) 收尾三件套 → 先树杀（父还活着）
        if "bp.kill_tree(proc)" not in text:
            match = OLD.search(text)
            if match:
                indent = match.group(1)
                text = text[:match.start()] + f"{indent}bp.kill_tree(proc)   # §11.5：父还活着时树杀（先 terminate 会让子进程脱管）\n" + text[match.end():]
                notes.append("收尾：terminate/wait/kill → bp.kill_tree(proc)")

        if text != original:
            path.write_text(text, encoding="utf-8")
        print(f"[{'已改' if notes else '无需改'}] {name}")
        for note in notes:
            print(f"        {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
