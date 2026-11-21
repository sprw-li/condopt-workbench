"""补齐迁移：6 个脚本里 `start_edge(bp.free_port(), …)` 与 `new_target(<字面端口>)` 不匹配。

第一版迁移只换了 start_edge 的端口，却漏了脚本后面复用**字面**端口的 `new_target(945N)`
→ 指向一个没人监听的端口 → ConnectionRefused（我实跑才发现的，不是靠看代码）。
本脚本把「同一端口只算一次、两处都用变量」补齐，并打印改动。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = [
    "v21_advanced_params_overlap.py", "v21_advparams_space.py", "v21_crosscheck.py",
    "v21_frontend_kickoff.py", "v21_param_overlap.py", "v21_param_overlap2.py",
]


def main() -> int:
    for name in FILES:
        path = HERE / name
        text = path.read_text(encoding="utf-8")
        original = text
        notes = []

        match = re.search(r"( *)proc = G\.start_edge\(bp\.free_port\(\), bp\.make_profile\(([^)]*)\)\)\n", text)
        if match:
            indent, tag = match.group(1), match.group(2)
            text = text[:match.start()] + (
                f"{indent}port = bp.free_port()          # §11.5：动态端口，避免固定端口竞态\n"
                f"{indent}proc = G.start_edge(port, bp.make_profile({tag}))\n"
            ) + text[match.end():]
            notes.append(f"start_edge 改用变量 port（tag={tag}）")

        for literal in sorted(set(re.findall(r"G\.new_target\((\d{3,5})\)", text))):
            text = text.replace(f"G.new_target({literal})", "G.new_target(port)")
            notes.append(f"new_target({literal}) -> new_target(port)")

        if text != original:
            path.write_text(text, encoding="utf-8")
        print(f"[{'已改' if notes else '无需改'}] {name}")
        for note in notes:
            print(f"        {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
