"""浏览器驱动的**串行套件运行器**（CONTRACT §11.5-6）。

为什么要有它：驱动级的串行锁只保证「两个驱动不重叠」，但两个**套件运行器**仍会在各自驱动的
间隙交错（2026-11-21 实测：后台跑套件时又前台跑了一个驱动 → `Page.enable` 超时）。本运行器
在**整个套件期间**持有 `suite_guard` 锁，套件内的驱动不再各自抢锁。

归因规则（§11.5-6）：
  * exit 0 → 通过；
  * exit 2 → 判据 FAIL（产品/断言）；
  * exit 3 → 卫生失败（本进程 profile 残留）；
  * exit 4 → **环境/并发类**（拿不到锁、或失败时存在并发证据）——不得记为产品缺陷，须串行重跑。

用法：py -3 _verify/run_browser_suite.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_profile as bp  # noqa: E402

HERE = Path(__file__).resolve().parent

SUITE = [
    ("geom_states", "v21_geom_states.py"),
    ("narrow_layout", "v21_narrow_layout.py"),
    ("advparams_space", "v21_advparams_space.py"),
    ("advanced_params_overlap", "v21_advanced_params_overlap.py"),
    ("param_overlap", "v21_param_overlap.py"),
    ("param_overlap2", "v21_param_overlap2.py"),
    ("geom_diag", "v21_geom_diag.py"),
    ("crosscheck", "v21_crosscheck.py"),
    ("frontend_kickoff", "v21_frontend_kickoff.py"),
    ("geom_matrix", "v21_geom_matrix.py"),
]


def run_suite() -> int:
    logs = HERE / "logs"
    logs.mkdir(exist_ok=True)
    results: list[tuple[str, int]] = []
    for name, script in SUITE:
        started = time.time()
        proc = subprocess.run([sys.executable, str(HERE / script)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        output = (proc.stdout or "") + (proc.stderr or "")
        (logs / f"suite_{name}.txt").write_text(output, encoding="utf-8")
        summary = next((line for line in reversed(output.splitlines()) if line.startswith("== ")), "")
        hygiene = next((line for line in reversed(output.splitlines()) if line.startswith("[卫生]")), "")
        print(f"{name:<26} exit={proc.returncode}  {summary.strip()}")
        if hygiene:
            print(f"{'':<26} {hygiene.strip()}")
        for line in output.splitlines():
            if line.startswith("[并发]"):
                print(f"{'':<26} {line.strip()}")
        results.append((name, proc.returncode))

    product_fail = [n for n, c in results if c == 2]
    hygiene_fail = [n for n, c in results if c == 3]
    env_conflict = [n for n, c in results if c == 4]
    other_fail = [n for n, c in results if c not in (0, 2, 3, 4)]
    print(f"\n通过 {sum(1 for _, c in results if c == 0)}/{len(results)}"
          f"；判据FAIL(2)={product_fail or '无'}；卫生失败(3)={hygiene_fail or '无'}；"
          f"环境/并发(4)={env_conflict or '无'}；其他={other_fail or '无'}")
    if env_conflict:
        print("（环境/并发类按 §11.5-6 **不计为产品缺陷**，但必须串行重跑确认）")
    return 0 if not (product_fail or hygiene_fail or other_fail) and not env_conflict else (
        2 if (product_fail or other_fail) else (3 if hygiene_fail else 4))


def main() -> int:
    print("=== 浏览器驱动串行套件（套件级锁）===")
    return bp.suite_guard(run_suite, "browser-suite")


if __name__ == "__main__":
    raise SystemExit(main())
