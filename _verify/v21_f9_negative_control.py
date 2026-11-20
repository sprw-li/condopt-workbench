"""F9 负对照（我独立复验 lead 要求的「故意写错 choice → 测试必须红」）。

不修改实现物：在**内存里**把 `bridge.PARAM_DISPLAY` 换成故意说谎的版本（或临时增加一条），
调用 api-backend 自己的判定函数 `test_param_copy_never_lies`（直接 import 那个测试模块并执行），
断言它**必须抛 AssertionError 并点名** 算法/参数/取值。

对照组（必须保持绿）：
  * 原始（未篡改）的 PARAM_DISPLAY → 测试应通过；
  * 篡改后 → 测试必须红。

这样既验证了「守卫真的会抓错」，也验证了「它平时不是因为永远红才抓到」。

运行：py -3 _verify/v21_f9_negative_control.py
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


def load_test_module():
    """把 tests/test_kickoff.py 当普通模块加载（只为拿到它的判定函数）。"""
    path = REPO / "tests" / "test_kickoff.py"
    spec = importlib.util.spec_from_file_location("_vc_test_kickoff", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_guard(module) -> tuple[bool, str]:
    """执行 test_param_copy_never_lies；返回 (是否通过, 失败信息)。"""
    try:
        module.test_param_copy_never_lies()
        return True, ""
    except AssertionError as exc:
        return False, str(exc)[:600]


def main() -> int:
    report = Report("F9 负对照：守卫能否抓到故意写错的文案")
    module = load_test_module()
    original = copy.deepcopy(bridge_module.PARAM_DISPLAY)

    # ---- 对照 0：原始数据必须通过（否则守卫是「永远红」，抓错能力无意义）----
    ok, msg = run_guard(module)
    report.check(
        "负对照-0：未篡改的 PARAM_DISPLAY 通过守卫（证明它不是永远红）",
        ok,
        f"通过={ok} {msg[:200] if msg else ''}",
    )

    try:
        # ---- 篡改 1：把 mfbo 的 acquisition 文案改成列出 pi（旧版 F9 的原样重现）----
        bridge_module.PARAM_DISPLAY[("mfbo", "acquisition")] = {
            "label": "采集函数",
            "plain": "ei/logei 平衡探索与利用；pi 更偏利用；ts 用后验采样。",
            "impact": "high",
            "advanced": False,
        }
        ok1, msg1 = run_guard(module)
        log(f"\n篡改1（mfbo.acquisition 提到 pi/ts）→ 守卫通过={ok1}")
        log(f"   失败信息: {msg1[:300]}")
        report.check(
            "负对照-1：把 `pi` 写进 mfbo 的 acquisition 文案 → 守卫必须红，且点名 mfbo/acquisition/pi",
            (not ok1) and "mfbo" in msg1 and "acquisition" in msg1 and "pi" in msg1,
            f"通过={ok1}；信息含 mfbo/acquisition/pi="
            f"{('mfbo' in msg1, 'acquisition' in msg1, 'pi' in msg1)}",
        )

        # ---- 篡改 2：把通用（name-only）acquisition 文案也写错（覆盖兜底键路径）----
        bridge_module.PARAM_DISPLAY = copy.deepcopy(original)
        bridge_module.PARAM_DISPLAY["acquisition"] = {
            "label": "采集函数",
            "plain": "ei/logei 平衡；pi 更偏利用；ts 用后验采样。",
            "impact": "high",
            "advanced": False,
        }
        ok2, msg2 = run_guard(module)
        log(f"\n篡改2（通用 acquisition 提到 pi/ts）→ 守卫通过={ok2}")
        log(f"   失败信息: {msg2[:300]}")
        report.check(
            "负对照-2：把 `pi` 写进**通用** acquisition 文案 → 守卫必须红（覆盖 name 兜底键路径）",
            (not ok2) and "pi" in msg2,
            f"通过={ok2}；信息含 pi={'pi' in msg2}",
        )

        # ---- 篡改 3：默认值说谎（「默认 2048」而 kernel 无默认数字，用 n_candidates 更合适）----
        bridge_module.PARAM_DISPLAY = copy.deepcopy(original)
        bridge_module.PARAM_DISPLAY[("bayes", "n_candidates")] = {
            "label": "候选点数",
            "plain": "每轮评估默认 4096 个候选点，越大越准也越慢。",
            "impact": "low",
            "advanced": True,
        }
        ok3, msg3 = run_guard(module)
        log(f"\n篡改3（bayes.n_candidates 默认值写成 4096）→ 守卫通过={ok3}")
        log(f"   失败信息: {msg3[:300]}")
        report.check(
            "负对照-3：把「默认 4096」写进 n_candidates（registry 默认 1024）→ 守卫必须红",
            not ok3,
            f"通过={ok3}；信息={msg3[:200]!r}",
        )

        # ---- 篡改 4：允许保留的「建议区间」不应被误杀（反向对照，防止守卫过严）----
        bridge_module.PARAM_DISPLAY = copy.deepcopy(original)
        bridge_module.PARAM_DISPLAY[("bayes", "alpha")] = {
            "label": "观测噪声方差",
            "plain": "噪声大就调大（0.01–0.1），复孔多可调小。",   # 建议区间，不是对库的断言
            "impact": "high",
            "advanced": False,
        }
        ok4, msg4 = run_guard(module)
        log(f"\n篡改4（建议区间 0.01–0.1）→ 守卫通过={ok4}（期望 True，不该误杀）")
        report.check(
            "负对照-4（防过严）：工作台建议区间「0.01–0.1」不得被误判为谎言",
            ok4,
            f"通过={ok4} {msg4[:200] if msg4 else '（未被误杀）'}",
        )
    finally:
        bridge_module.PARAM_DISPLAY = original

    # ---- 恢复后必须回到绿 ----
    ok_final, msg_final = run_guard(module)
    report.check(
        "负对照-5：恢复原始 PARAM_DISPLAY 后守卫回到绿（无副作用残留）",
        ok_final,
        f"通过={ok_final} {msg_final[:150] if msg_final else ''}",
    )
    return report.finish("v21_f9_negative_control.log")


if __name__ == "__main__":
    raise SystemExit(main())
