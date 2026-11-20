"""F11 复验：api-backend 抽出的守卫 `_param_copy_lies(display=None)` 是否真的抓得住、且没被放宽。

我不采信「11/11 命中」的自述，自己造矩阵，并**两个方向都测**：
  * 该红的必须红（漏报 = 门禁给虚假安心）；
  * 该绿的必须绿（过严 = 误伤有用文案，例如工作台建议区间）。
另外单独压 `_same_value` 的语义等价（规范化到底放没放宽）。
最后做一次「没作弊」检查：守卫的结果必须**随传入的表变化**，不能恒定输出空。

运行：py -3 _verify/v21_f11_guard.py
"""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPO, Report, log  # noqa: E402

sys.path.insert(0, str(REPO))
import bridge as bridge_module  # noqa: E402


def load_tests():
    path = REPO / "tests" / "test_kickoff.py"
    spec = importlib.util.spec_from_file_location("_vc_tests_kickoff", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def entry(plain: str, label: str = "测试文案") -> dict:
    return {"label": label, "plain": plain, "impact": "low", "advanced": True}


def main() -> int:
    report = Report("F11 复验：文案守卫（_param_copy_lies）")
    tests = load_tests()
    guard = getattr(tests, "_param_copy_lies", None)
    same_value = getattr(tests, "_same_value", None)
    original = copy.deepcopy(bridge_module.PARAM_DISPLAY)

    if guard is None:
        report.check("守卫已抽出为可调用函数 `_param_copy_lies`", False, "函数不存在")
        return report.finish("v21_f11_guard.log")
    report.check(
        "守卫已抽出为可调用函数 `_param_copy_lies(display=None)`（可直接压，不必改源码）",
        callable(guard),
        f"signature 可用；真实表调用返回 {len(guard())} 条问题",
    )

    # ---------- 0. 真实表必须干净 ----------
    real = guard()
    report.check("0) 真实 PARAM_DISPLAY → 0 条问题", real == [], f"问题 {len(real)} 条：{real[:3]}")

    # ---------- 1. 「没作弊」：结果必须随输入变化 ----------
    probe_a = {("bayes", "n_candidates"): entry("每轮评估默认 4096 个候选点。")}
    probe_b = {("bayes", "n_candidates"): entry("每轮评估默认 1024 个候选点。")}
    ra, rb = guard(probe_a), guard(probe_b)
    report.check(
        "1) 没作弊：同一键改成「默认 4096」报问题、改成「默认 1024」不报（结果随表变化，非恒定空）",
        bool(ra) and not rb,
        f"4096→{len(ra)} 条问题；1024→{len(rb)} 条问题",
    )

    # ---------- 2. 该红的必须红 ----------
    red_cases = [
        ("旧 F9 原样：mfbo 精确键写 pi",
         {("mfbo", "acquisition"): entry("ei/logei 平衡；pi 更偏利用；ts 用后验采样。")}, "pi"),
        ("通用（name 兜底）键写 pi",
         {"acquisition": entry("ei/logei 平衡；pi 更偏利用；ts 用后验采样。")}, "pi"),
        ("F11 核心：默认写成任意错误数字 4096（registry 1024）",
         {("bayes", "n_candidates"): entry("每轮评估默认 4096 个候选点。")}, "4096"),
        ("默认写成另一算法的默认值 0.000001（mfbo alpha 实际 0.0001）",
         {("mfbo", "alpha"): entry("噪声方差默认 0.000001 起步。")}, "0.000001"),
        ("陈旧条目：键不是任何算法的参数",
         {("bayes", "definitely_not_a_param"): entry("随便写点。")}, "definitely_not_a_param"),
    ]
    red_ok = True
    for label, display, needle in red_cases:
        problems = guard(display)
        hit = bool(problems) and needle in " ".join(problems)
        red_ok = red_ok and hit
        log(f"[{'红' if hit else '!! 漏报'}] {label} → {len(problems)} 条；{problems[:1]}")
        report.check(f"2) 该红：{label} → 守卫报出问题", hit, f"问题 {len(problems)} 条：{problems[:2]}")

    # ---------- 3. 该绿的必须绿（防过严 / 防误伤） ----------
    green_cases = [
        ("建议区间「0.01–0.1」不是对库的断言",
         {("bayes", "alpha"): entry("噪声大就调大（0.01–0.1），复孔多可调小。")}),
        ("纯中文句「默认即均匀分布」不含 ASCII 字面量，不算断言",
         {("bayes", "alpha"): entry("默认即均匀分布。")}),
        ("数值规范化：文案「默认 1e-6」vs registry 1e-06 视为相同",
         {("bayes", "alpha"): entry("观测噪声方差默认 1e-6。")}),
        ("数值规范化：文案「默认 1E-06」",
         {("bayes", "alpha"): entry("观测噪声方差默认 1E-06。")}),
        ("数值规范化：文案「默认 0.000001」",
         {("bayes", "alpha"): entry("观测噪声方差默认 0.000001。")}),
    ]
    green_ok = True
    for label, display in green_cases:
        problems = guard(display)
        clean = not problems
        green_ok = green_ok and clean
        log(f"[{'绿' if clean else '!! 误报'}] {label} → {len(problems)} 条；{problems[:1]}")
        report.check(f"3) 该绿：{label} → 守卫不报", clean, f"问题 {len(problems)} 条：{problems[:2]}")

    # ---------- 4. _same_value 语义（规范化是否被放宽成「什么都相等」）----------
    if callable(same_value):
        equiv = [("1e-6", 1e-06), ("1E-06", 1e-06), ("0.000001", 1e-06), ("1.0", 1),
                 ("0.30", 0.3), ("true", True), ("True", True), ("1024", 1024)]
        non_equiv = [("4096", 1024), ("ucb", "ei"), ("0.0001", 1e-06), ("true", False)]
        bad = []
        for claimed, actual in equiv:
            if not same_value(claimed, actual):
                bad.append(f"应相等却被判不等: {claimed!r} vs {actual!r}")
        for claimed, actual in non_equiv:
            if same_value(claimed, actual):
                bad.append(f"应不等却被判相等: {claimed!r} vs {actual!r}")
        report.check(
            "4) `_same_value` 规范化两个方向都正确（等价类判等、非等价判不等，没被放宽成恒真）",
            not bad,
            f"等价 {len(equiv)} 组、非等价 {len(non_equiv)} 组；异常={bad or '无'}",
        )
    else:
        report.check("4) `_same_value` 可调用", False, "函数不存在")

    # ---------- 5. 原有两类守卫没被放宽（回归）----------
    report.check(
        "5) 回归：choice 字面量类 + 默认值类两类守卫都仍然生效（红绿矩阵全过）",
        red_ok and green_ok,
        f"该红全中={red_ok}；该绿全清={green_ok}",
    )

    # ---------- 6. 恢复真实表后仍干净 ----------
    bridge_module.PARAM_DISPLAY = original
    report.check("6) 真实表未被我的测试污染（仍 0 条问题）", guard() == [], f"{guard()[:3]}")
    return report.finish("v21_f11_guard.log")


if __name__ == "__main__":
    raise SystemExit(main())
