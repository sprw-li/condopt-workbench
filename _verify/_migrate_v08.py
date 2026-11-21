"""迁移 4：把上一轮遗留的浏览器驱动 `v08_browser.py` 也纳入 §11.5 卫生与串行（新）

它用的是老写法：`tempfile.mkdtemp(prefix="dsh-edge-")` + `edge.kill()`（父进程先死→子进程脱管）
+ 裸 `SystemExit(main())`（既不取锁、也不自证残留）。
本迁移：① profile 改 `bp.make_profile`；② 收尾改 `bp.kill_tree`；③ 入口改 `bp.run_guarded`。
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "v08_browser.py"

IMPORT_LINE = "import browser_profile as bp  # §11.5 临时资源卫生（profile 不残留 + 串行锁）"


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    original = text
    notes = []

    if "import browser_profile as bp" not in text:
        anchor = "from common import (  # noqa: E402"
        if anchor in text:
            text = text.replace(anchor, IMPORT_LINE + "\n" + anchor, 1)
            notes.append("+ import bp")

    text = text.replace('profile = Path(tempfile.mkdtemp(prefix="dsh-edge-"))',
                        'profile = bp.make_profile("v08browser")')
    if "bp.make_profile" in text:
        notes.append("profile: mkdtemp(dsh-edge-) -> bp.make_profile")

    text = text.replace("                edge.kill()\n", "                bp.kill_tree(edge)\n")
    if "bp.kill_tree(edge)" in text:
        notes.append("收尾: edge.kill() -> bp.kill_tree(edge)")

    text = text.replace("raise SystemExit(main())", 'raise SystemExit(bp.run_guarded(main, "v08browser"))')
    if "run_guarded(main" in text:
        notes.append("入口: SystemExit(main()) -> run_guarded(main, 'v08browser')")

    if text != original:
        TARGET.write_text(text, encoding="utf-8")
    print(f"[{'已改' if notes else '无需改'}] v08_browser.py")
    for note in notes:
        print(f"        {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
