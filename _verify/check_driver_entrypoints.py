"""机器检查（CONTRACT §11.5-8）：扫描 `_verify/**`，找出「是浏览器驱动、却不取串行锁」的入口。

为什么需要：锁是 opt-in。`guard_residue()` **既不取锁也不报警**，所以「按 §11.5-6 串行」对
锁出现之前写的驱动是**假生效**——api-backend 实测：同机同刻并发跑 ime + oldform，两个都 exit 0。
这类「宣称的比执行的多」是本轮第 9 个同族问题（F9/F11 那一家），所以本检查**必须带负对照**：
`--self-test` 会造 4 个样本文件（合规/裸入口/guard_residue 入口/非驱动），断言前两类必须被抓。

判定：
  * 「浏览器驱动」= 文件里出现 `user-data-dir` / `start_edge(` / `.CDP(`（真的起了浏览器）；
  * 「取了串行锁」= 出现 `run_guarded(main` 或 `suite_guard(`（套件级锁也算）；
  * 违规 = 是驱动 且 没有取锁。

用法：py -3 _verify/check_driver_entrypoints.py [--self-test]
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

DRIVER_HINTS = (r"user-data-dir", r"\.CDP\(", r"start_edge\(")
#: 真起浏览器还必须有进程创建调用——否则「迁移/补丁脚本」里作为**替换文本**出现的
#: `--user-data-dir=...` / `start_edge(...)` 会被误判成驱动（实测踩过：_migrate_*.py 假阳）
LAUNCH_HINT = r"subprocess\.Popen|Popen\("
LOCK_HINTS = (r"run_guarded\(main", r"suite_guard\(")
#: 本检查自身与被检模块不算驱动
SKIP = {"browser_profile.py", "check_driver_entrypoints.py"}


def _calls(text: str) -> set[str]:
    """用 AST 取出**真实被调用**的函数名（排除字符串里的同名文本）。

    为什么不用正则：迁移/补丁脚本会把 `start_edge(bp.free_port(), …)` 写在**替换字符串**里，
    正则会把它们误判成驱动（实测假阳）；而只要求 `subprocess.Popen` 又漏掉
    「自己不起进程、而是调 `start_edge()`」的那 8 个驱动（实测假阴）。AST 两边都能分清。
    """
    import ast

    names: set[str] = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def analyze(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    called = _calls(text)
    has_browser_flag = any(re.search(hint, text) for hint in DRIVER_HINTS)
    launches = ("Popen" in called) or ("start_edge" in called) or ("CDP" in called)
    is_driver = has_browser_flag and launches
    parse_failed = not called and text.strip() != ""
    takes_lock = any(re.search(hint, text) for hint in LOCK_HINTS)
    legacy_guard = bool(re.search(r"guard_residue\(main", text))
    bare_entry = bool(re.search(r"SystemExit\(main\(\)\)", text))
    return {"file": path.name, "is_driver": is_driver, "takes_lock": takes_lock,
            "legacy_guard": legacy_guard, "bare_entry": bare_entry, "parse_failed": parse_failed,
            "how": ("Popen" if "Popen" in called else ("start_edge" if "start_edge" in called else
                                                        ("CDP" if "CDP" in called else "-")))}


def scan(directory: Path) -> list[dict]:
    return [analyze(p) for p in sorted(directory.glob("*.py")) if p.name not in SKIP]


def report(rows: list[dict]) -> int:
    drivers = [r for r in rows if r["is_driver"]]
    compliant = [r for r in drivers if r["takes_lock"]]
    violations = [r for r in drivers if not r["takes_lock"]]
    print(f"扫描 {len(rows)} 个文件：浏览器驱动 {len(drivers)} 个，其中取锁 {len(compliant)} 个")
    for row in compliant:
        print(f"  [OK ] {row['file']}")
    for row in violations:
        why = "用 guard_residue(main)（不取锁）" if row["legacy_guard"] else (
            "裸 SystemExit(main())（无任何保护）" if row["bare_entry"] else "未取锁")
        print(f"  [RED] {row['file']}：是浏览器驱动但{why} → 违反 CONTRACT §11.5-8")
    if violations:
        print(f"\n结论：**不通过** —— {len(violations)} 个驱动入口不取串行锁"
              f"（这些驱动的『串行』是假生效）")
        return 2
    print("\n结论：通过 —— 所有浏览器驱动入口都取了串行锁")
    return 0


def self_test() -> int:
    """负对照：必须能抓出「裸入口」与「guard_residue 入口」，且不误报合规入口与非驱动。"""
    compliant_src = (
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "import browser_profile as bp\n"
        "def main():\n"
        "    return 0\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(bp.run_guarded(main, 'x'))\n"
    )
    bare_src = (
        "def main():\n"
        "    return 0\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n"
    )
    legacy_src = (
        "import browser_profile as bp\n"
        "def main():\n"
        "    return 0\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(bp.guard_residue(main))\n"
    )
    not_driver_src = (
        "import browser_profile as bp\n"
        "def main():\n"
        "    return bp.residue_count()\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n"
    )
    # 驱动特征：真的起浏览器（浏览器参数 + subprocess.Popen 同时出现）
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "ok_driver.py").write_text(
            "import subprocess\n"
            "import browser_profile as bp\n"
            "def main():\n"
            "    subprocess.Popen(['msedge', '--user-data-dir=x'])\n"
            "    return 0\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(bp.run_guarded(main, 'ok'))\n", encoding="utf-8")
        (root / "bad_bare.py").write_text(
            "import subprocess\n"
            "def main():\n"
            "    subprocess.Popen(['msedge', '--user-data-dir=x'])\n"
            "    return 0\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(main())\n", encoding="utf-8")
        (root / "bad_legacy.py").write_text(
            "import subprocess\n"
            "import browser_profile as bp\n"
            "def main():\n"
            "    subprocess.Popen(['msedge', '--remote-debugging-port=1'])\n"
            "    cdp = G.CDP('ws')\n"
            "    return 0\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(bp.guard_residue(main))\n", encoding="utf-8")
        (root / "not_a_driver.py").write_text(not_driver_src, encoding="utf-8")

        rows = {r["file"]: r for r in scan(root)}

    checks = [
        ("合规驱动（run_guarded）不算违规",
         rows["ok_driver.py"]["is_driver"] and rows["ok_driver.py"]["takes_lock"]),
        ("裸入口驱动 → 必须被判违规",
         rows["bad_bare.py"]["is_driver"] and not rows["bad_bare.py"]["takes_lock"]),
        ("guard_residue 入口驱动 → 必须被判违规（这就是「假生效」那类）",
         rows["bad_legacy.py"]["is_driver"] and not rows["bad_legacy.py"]["takes_lock"]),
        ("引用了卫生模块但不是浏览器驱动 → 不算违规",
         (not rows["not_a_driver.py"]["is_driver"])),
    ]
    failed = 0
    for name, ok in checks:
        print(f"[{'OK ' if ok else 'FAIL'}] {name}")
        failed += 0 if ok else 1
    print(f"\n自检：{len(checks) - failed}/{len(checks)} 通过")
    return 0 if failed == 0 else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dir", default=str(HERE))
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    return report(scan(Path(args.dir)))


if __name__ == "__main__":
    raise SystemExit(main())
