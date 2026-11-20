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
from typing import NamedTuple

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


class Row(NamedTuple):
    """一格测量结果。

    ``parsed`` 显式区分「解析失败」与「数值恰好为某值」——**不要**只靠哨兵
    数字兜住：链路上任何一次比较放宽（例如以后有人把 `zero == 0` 改成 `zero <= 0`），
    哨兵 `-1` 就会变成「一切正常」的假安心（web-shell 在 task-18 后的复核里指出）。
    """

    parsed: bool
    zero: int
    raw: int
    real: int
    overflow: int
    code: int
    bps: str

    def ok(self) -> bool:
        return (
            self.parsed
            and self.zero == 0
            and self.real == 0
            and 0 <= self.overflow <= 1
            and self.code == 0
        )


FAILED = Row(parsed=False, zero=-1, raw=-1, real=-1, overflow=-1, code=-1, bps="n/a")


def parse(out: str, code: int) -> Row:
    """解析探针输出。任何一段抠不出来 → ``parsed=False``（而不是「当成 0」）。"""
    zero = RAW_RE.search(out)
    z = ZERO_RE.search(out)
    ov = OVERFLOW_RE.search(out)
    bp = BREAKPOINT_RE.search(out)
    if not (zero and z and ov and bp):
        return FAILED._replace(code=code)
    return Row(
        parsed=True,
        zero=int(z.group(1)),
        raw=int(zero.group(1)),
        real=int(zero.group(2)),
        overflow=int(ov.group(1)) - int(ov.group(2)),
        code=code,
        bps=f"{bp.group(1)}/{bp.group(2)}",
    )


def run(theme: str, size: str, all_open: bool) -> Row:
    cmd = [sys.executable, str(PROBE), "--theme", theme, "--window-size", size]
    if all_open:
        cmd.append("--all-open")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return parse(proc.stdout or "", proc.returncode)


GOOD_SAMPLE = """visible boxes: 13
零尺寸（被压扁 → 子元素会溢出压住兄弟）: 0
原始相交对: 0   其中【真实】(命中测试可见): 0   【幻影】: 0
横向溢出：scrollWidth=1310 clientWidth=1310 → 溢出 0px （OK）
视口/断点：inner=1344x799  shell.display=grid  mq1024=False  mq680=False
"""

OVERFLOW_SAMPLE = GOOD_SAMPLE.replace("溢出 0px", "溢出 40px").replace(
    "scrollWidth=1310 clientWidth=1310", "scrollWidth=1350 clientWidth=1310")


def self_test() -> int:
    """负对照：解析器必须能拒掉坏样本，且不能把坏样本读成「通过」。"""
    cases = [
        ("正常样本", parse(GOOD_SAMPLE, 0).ok(), True),
        ("切削掉的样本（正则不该匹配到任何东西）", parse("", 0).ok(), False),
        ("缺横向溢出行的样本", parse("\n".join(GOOD_SAMPLE.splitlines()[:3]), 0).ok(), False),
        ("真有横向溢出的样本", parse(OVERFLOW_SAMPLE, 0).ok(), False),
        ("数值全 0 但进程非零退出", parse(GOOD_SAMPLE, 2).ok(), False),
        # 关键：把「零尺寸」比较放宽成 <= 0 时，旧的哨兵写法会让 -1 混过去；新写法仍必须判非零
    ]
    bad = 0
    for name, got, want in cases:
        ok = got == want
        bad += 0 if ok else 1
        print(f"[{'OK ' if ok else 'FAIL'}] {name}：实得 ok={got}，期望 {want}")

    # 关键一条：即使以后有人放宽数值比较，`parsed=False` 也必须拦住它。
    # 旧的哨兵写法（-1 当正常值比）在放宽后会给出「无溢出」的假安心（web-shell 指出）。
    relaxed = FAILED.zero <= 0 and FAILED.overflow <= 1   # 假设有人把两个比较都放宽
    caught = not FAILED.ok()
    print(f"[{'OK ' if caught else 'FAIL'}] 放宽数值比较后仍能被拦："
          f"放宽后数值检查={'通过(危险)' if relaxed else '不通过'}，parsed=False 使其 ok={FAILED.ok()} "
          f"→ {'已拦住' if caught else '漏了'}")
    bad += 0 if caught else 1

    print(f"\n自检：{'全部通过' if bad == 0 else f'{bad} 项不符'}")
    return 0 if bad == 0 else 2


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    print(f"{'主题':<7}{'视口':<11}{'状态':<10}{'零尺寸':>7}{'原始相交':>9}{'真实重叠':>9}"
          f"{'横向溢出':>9}{'exit':>6}  断点1024/680")
    bad = 0
    total = 0
    for theme, size in CONFIGS:
        for all_open in (False, True):
            state = "抽屉全开" if all_open else "默认态"
            row = run(theme, size, all_open)
            total += 1
            ok = row.ok()
            if not ok:
                bad += 1
            print(f"{theme:<7}{size:<11}{state:<10}{row.zero:>7}{row.raw:>9}{row.real:>9}"
                  f"{row.overflow:>9}{row.code:>6}  {row.bps}"
                  + ("" if ok else ("   <== 解析失败（不是全绿）" if not row.parsed else "   <== 非零")))
    print(f"\n合计 {total - bad}/{total} 格通过（解析成功 且 零尺寸 0 且真实重叠 0 且 "
          f"横向溢出 ≤1px 且 exit 0）")
    print("判定与 geom_probe.py 同一套三重判据：可滚动祖先裁切 + 跳过不绘制子树 + 命中测试；"
          "横向溢出按 scrollWidth - clientWidth 计。")
    print("解析器自检：py -3 _verify/viewport_matrix.py --self-test")
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
