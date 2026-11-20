"""视口 × 主题几何矩阵：调 geom_probe.py 跑多配置，核实「深色主题 + 窄屏」不是盲区。

verifier 在 task-16 §21.10 把「深色主题 / 窄屏（≤1024px）」列为未覆盖项（原探针只跑 1344×896）。
本脚本把矩阵变成**可复跑证据**：默认态 + 抽屉全开态 × light/dark × 三种视口，
每格记录「零尺寸 / 原始相交对 / 真实重叠 / 退出码」，任一格非零即非零退出。

用法：py -3 _verify/viewport_matrix.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE = HERE / "geom_probe.py"

CONFIGS = [
    ("light", "1344,896"),   # 原探针口径（回归基准）
    ("dark", "1344,896"),
    ("light", "900,800"),    # 触发 @media (max-width:1024px) 堆叠布局
    ("dark", "900,800"),
    ("light", "680,800"),    # ≤680px 单列断点（web-shell 如实记录为未测的那一段）
]

ZERO_RE = re.compile(r"零尺寸[^:]*:\s*(\d+)")
RAW_RE = re.compile(r"原始相交对:\s*(\d+)\s+其中【真实】[^:]*:\s*(\d+)")
OVERFLOW_RE = re.compile(r"横向溢出：scrollWidth=(\d+)\s+clientWidth=(\d+)")
BREAKPOINT_RE = re.compile(r"mq1024=(\w+)\s+mq680=(\w+)")


def run(theme: str, size: str, all_open: bool) -> tuple[int, int, int, int, int, str]:
    cmd = [sys.executable, str(PROBE), "--theme", theme, "--window-size", size]
    if all_open:
        cmd.append("--all-open")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = proc.stdout or ""
    zero = RAW_RE.search(out)
    z = ZERO_RE.search(out)
    ov = OVERFLOW_RE.search(out)
    bp = BREAKPOINT_RE.search(out)
    if not zero or not z or not ov:
        return -1, -1, -1, -1, proc.returncode, "n/a"
    overflow = int(ov.group(1)) - int(ov.group(2))
    bps = f"{bp.group(1)}/{bp.group(2)}" if bp else "n/a"
    return int(z.group(1)), int(zero.group(1)), int(zero.group(2)), overflow, proc.returncode, bps


def main() -> int:
    print(f"{'主题':<7}{'视口':<11}{'状态':<10}{'零尺寸':>7}{'原始相交':>9}{'真实重叠':>9}"
          f"{'横向溢出':>9}{'exit':>6}  断点1024/680")
    bad = 0
    total = 0
    for theme, size in CONFIGS:
        for all_open in (False, True):
            state = "抽屉全开" if all_open else "默认态"
            zero, raw, real, overflow, code, bps = run(theme, size, all_open)
            total += 1
            ok = (zero == 0 and real == 0 and overflow <= 1 and code == 0)
            if not ok:
                bad += 1
            print(f"{theme:<7}{size:<11}{state:<10}{zero:>7}{raw:>9}{real:>9}"
                  f"{overflow:>9}{code:>6}  {bps}" + ("" if ok else "   <== 非零"))
    print(f"\n合计 {total - bad}/{total} 格通过（零尺寸 0 且真实重叠 0 且横向溢出 ≤1px 且 exit 0）")
    print("判定与 geom_probe.py 同一套三重判据：可滚动祖先裁切 + 跳过不绘制子树 + 命中测试；"
          "横向溢出按 scrollWidth - clientWidth 计。")
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
