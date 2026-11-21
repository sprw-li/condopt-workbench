"""机器检查：文档里点名的 `_verify/*.py` 必须真实存在。

## 为什么需要它（本轮第 10 个同族问题）

契约 §11.2 曾点名 `_verify/v22_demo_coexist.py`，**这个文件从未存在** ——
于是「§11.2 已验证」在整整一轮里**没有任何可跑的判据**，而所有门禁全绿。
根因不是谁写错一个字，而是**没有任何检查把「文档点名的产物」与「仓库里真实存在的文件」绑起来**。

这与 F9/F11/串行锁那几条同族：**宣称的比执行的多**。区别是这条最容易做、收益最直接：
把「名字」变成「必须存在的文件」。

## 判据

1. 扫描 `CONTRACT.md`（冻结的唯一真源）与 `README.md` 里所有被反引号包起来的
   `_verify/<path>.py` 之类的路径；
2. 每个路径必须在仓库里**真实存在**，否则报红并列出「文档:行号 → 缺失路径」；
3. `--self-test` 负对照：伪造一个不存在的路径 → 必须报红；只有真实路径 → 必须通过。
   （不带负对照的检查等于没有检查——本轮反复验证过的教训。）

用法：
    py -3 _verify/check_contract_paths.py
    py -3 _verify/check_contract_paths.py --self-test
退出码：0 = 全部存在；2 = 有缺失或自检失败。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = ["CONTRACT.md", "README.md"]

#: 只认「仓库内路径」形态：`_verify/xxx.py` / `web/assets/x.js` / `tests/x.py`
PATH_RE = re.compile(r"`((?:_verify|web|tests|examples)/[A-Za-z0-9_./-]+\.(?:py|js|css|html|md))`")


def scan(doc_text: str) -> list[tuple[int, str]]:
    """返回 [(行号, 路径)]。"""
    found: list[tuple[int, str]] = []
    for lineno, line in enumerate(doc_text.splitlines(), start=1):
        for match in PATH_RE.finditer(line):
            found.append((lineno, match.group(1)))
    return found


def missing_in(doc_path: Path, doc_text: str) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for lineno, rel in scan(doc_text):
        if rel in seen:
            continue
        seen.add(rel)
        if not (REPO / rel).exists():
            problems.append(f"{doc_path.name}:{lineno} 点名了不存在的文件：{rel}")
    return problems


def self_test() -> int:
    """负对照：伪造路径必须被抓，真实路径必须放过。"""
    cases = [
        ("真实路径（CONTRACT.md 现状）", "见 `_verify/geom_probe.py`", 0),
        ("伪造路径", "见 `_verify/definitely_not_here.py`", 1),
        ("多行里混一个假的", "`_verify/geom_probe.py`\n`_verify/nope.py`", 1),
        ("只是文字提及、不是路径形态", "见 `verify 脚本` 与 `some/path.txt`", 0),
    ]
    bad = 0
    for name, text, want in cases:
        problems = []
        for lineno, rel in scan(text):
            if not (REPO / rel).exists():
                problems.append(rel)
        got = len(problems)
        # 「真实路径」用例里若 REPO 布局变了会给出 >0，这里只断言相对关系
        ok = (got == want) if want != 0 else (got == 0)
        bad += 0 if ok else 1
        print(f"[{'OK ' if ok else 'FAIL'}] {name}：缺失数={got}，期望 {want}")
    print(f"\n自检：{'全部通过' if bad == 0 else f'{bad} 项不符'}")
    return 0 if bad == 0 else 2


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    problems: list[str] = []
    total = 0
    for name in DOCS:
        doc = REPO / name
        if not doc.is_file():
            print(f"[SKIP] {name} 不存在")
            continue
        text = doc.read_text(encoding="utf-8")
        refs = scan(text)
        total += len(refs)
        problems += missing_in(doc, text)
        print(f"[ok  ] {name}：点名仓库内文件 {len(refs)} 处")

    print(f"\n合计点名 {total} 处（含重复）")
    if problems:
        print(f"发现 {len(problems)} 处指向不存在的文件：")
        for line in problems:
            print(f"  [RED] {line}")
        print("\n结论：失败 —— 文档点名的产物必须真实存在，否则『已验证』没有证据。")
        return 2
    print("结论：通过 —— 文档点名的 `_verify/**`、`web/**`、`tests/**` 文件全部存在。")
    print("自检：py -3 _verify/check_contract_paths.py --self-test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
