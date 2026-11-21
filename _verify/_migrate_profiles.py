"""一次性迁移：把 9 个浏览器驱动脚本的 profile 与端口收口到 §11.5 口径。

只做四类机械替换（逐文件打印改了什么），判断逻辑一律不动：
  1. 在 sys.path 设定之后插入 `import browser_profile as bp`；
  2. `Path.home() / ".condopt-xxx"` → `bp.make_profile("xxx")`；
  3. `start_edge(945N, ...)` → `start_edge(bp.free_port(), ...)`（消除固定端口竞态）；
  4. `raise SystemExit(main())` → `raise SystemExit(bp.guard_residue(main))`（自证新增残留 == 0）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGETS = [
    "v21_advanced_params_overlap.py", "v21_advparams_space.py", "v21_crosscheck.py",
    "v21_frontend_kickoff.py", "v21_geom_diag.py", "v21_geom_matrix.py",
    "v21_narrow_layout.py", "v21_param_overlap.py", "v21_param_overlap2.py",
]
IMPORT_LINE = "import browser_profile as bp  # §11.5 临时资源卫生（profile 不残留 + 动态端口）"


def migrate(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    original = text
    notes: list[str] = []

    # 1) 插入 bp 导入（放在 sys.path.insert 之后，确保能找到模块）
    if "import browser_profile as bp" not in text:
        anchor = re.search(r"^sys\.path\.insert\(0, str\(Path\(__file__\)\.resolve\(\)\.parent\)\)\n",
                           text, flags=re.MULTILINE)
        if anchor:
            text = text[:anchor.end()] + IMPORT_LINE + "\n" + text[anchor.end():]
            notes.append("+ import bp")

    # 2) profile 路径
    for match in sorted(set(re.findall(r'Path\.home\(\) / (f?"\.condopt-[^"]+")', text)), reverse=True):
        literal = match
        if literal.startswith('f"'):
            tag = literal[2:-1].replace('.condopt-', '').replace('{', '{').replace('}', '}')
            replacement = f'bp.make_profile(f"{tag}")'
        else:
            tag = literal.strip('"').replace('.condopt-', '')
            replacement = f'bp.make_profile("{tag}")'
        text = text.replace(f"Path.home() / {literal}", replacement)
        notes.append(f"profile: {literal} -> {replacement}")

    # 3) 固定调试端口 → 动态。
    #    注意 v21_geom_diag.py 里端口在变量里被复用于 new_target()，不能只换调用点。
    if "v21_geom_diag" in path.name:
        text = text.replace("    proc = G.start_edge(9451, profile)",
                            "    port = bp.free_port()\n    proc = G.start_edge(port, profile)")
        text = text.replace("cdp = G.CDP(G.new_target(9451))", "cdp = G.CDP(G.new_target(port))")
        notes.append("port: 9451 -> bp.free_port()（并同步 new_target）")
    else:
        for match in sorted(set(re.findall(r"start_edge\((\d{4}),", text)), reverse=True):
            text = text.replace(f"start_edge({match},", "start_edge(bp.free_port(),")
            notes.append(f"port: {match} -> bp.free_port()")

    # 4) 退出时自证残留
    if "bp.guard_residue(main)" not in text:
        text = text.replace("raise SystemExit(main())", "raise SystemExit(bp.guard_residue(main))")
        notes.append("+ guard_residue(main)")

    if text != original:
        path.write_text(text, encoding="utf-8")
    return notes


def main() -> int:
    changed = 0
    for name in TARGETS:
        path = HERE / name
        if not path.is_file():
            print(f"!! 缺文件 {name}")
            return 1
        notes = migrate(path)
        status = "已改" if notes else "无需改"
        changed += 1 if notes else 0
        print(f"[{status}] {name}")
        for note in notes:
            print(f"        {note}")
    print(f"\n共处理 {len(TARGETS)} 个文件，其中 {changed} 个被修改")
    return 0


if __name__ == "__main__":
    sys.exit(main())
