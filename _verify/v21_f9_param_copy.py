"""F9 独立检查器（verifier 侧对照实现）：`plan` 的展示文案是否对**该算法**说谎。

检查两件可证伪的事（与 lead 的裁定 (b) 同向，但这是我的独立实现，用于交叉对照）：
  C1 **choice 字面量**：文案里出现的、属于该参数名在**任一算法**下的合法 choices 的字面量，
     必须也属于**本算法**对该参数的合法 choices。
  C2 **默认值断言**：文案里若出现「默认 X」，X 必须等于本算法 registry 的 default。

故意不做的事：**不禁数字**。「噪声大就调大（0.01–0.1）」是工作台建议区间，不是对库的断言
（lead 的细化，我同意），所以只在出现「默认」时才当断言校验。

用途：
  * 现在跑 → 应当**报出**已知的 F9（`mfbo` 的 acquisition 文案提到 pi/ts）＝ 阳性对照；
  * task-17 修完后跑 → 应当全绿。

运行：py -3 _verify/v21_f9_param_copy.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import Report, Server, fresh, http, js, log  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from condopt import registry as condopt_registry  # noqa: E402
import bridge as bridge_module  # noqa: E402

CHAT = "/api/kickoff/chat"
PLAN = "/api/kickoff/plan"


def chat(base, content, state=None):
    body = {"messages": [{"role": "user", "content": content}]}
    if state is not None:
        body["state"] = state
    status, _, text = http(base, "POST", CHAT, body=body)
    return status, js(text)


def drive_to_options(base):
    status, resp = chat(base, "开始")
    state = resp["state"]
    for _ in range(8):
        if state.get("step") == "options":
            break
        chips = (resp.get("reply") or {}).get("quick_replies") or []
        if not chips:
            break
        status, resp = chat(base, chips[0]["value"], state=state)
        state = resp["state"]
    return state


def token_present(text: str, literal: str) -> bool:
    """字面量是否作为**独立 token** 出现（避免 'ts' 命中 'its' 之类）。"""
    if not literal:
        return False
    pattern = r"(?<![0-9A-Za-z_])" + re.escape(literal) + r"(?![0-9A-Za-z_])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def main() -> int:
    report = Report("F9 展示文案是否对该算法说谎（独立对照实现）")
    described = {a["name"]: a for a in condopt_registry.describe_all()}

    runs = fresh("f9copy")
    with Server(runs) as server:
        state = drive_to_options(server.base)
        report.check("前置：对话走到 options", state.get("step") == "options", f"step={state.get('step')!r}")

        # 先把「同名参数在全部算法下的 choices 并集」建好，用于发现可疑字面量
        choices_by_name: dict[str, set[str]] = {}
        for a in described.values():
            for p in a.get("params") or []:
                for c in (p.get("choices") or []):
                    choices_by_name.setdefault(p["name"], set()).add(str(c))

        choice_lies = []
        default_lies = []
        checked = 0
        for algo in described:
            status, _, text = http(server.base, "POST", PLAN, body={"state": state, "algorithm": algo})
            planned = js(text)
            if status != 200:
                report.check(f"plan({algo}) 可用", False, f"status={status} {str(planned.get('message'))[:80]!r}")
                continue
            spec = {p["name"]: p for p in described[algo].get("params") or []}
            for entry in planned.get("params") or []:
                name = entry.get("name")
                text_blob = f"{entry.get('label') or ''} {entry.get('plain') or ''}"
                checked += 1
                # C1：文案提到的 choice 字面量必须属于本算法
                legal = {str(c) for c in (spec.get(name, {}).get("choices") or [])}
                suspicious = choices_by_name.get(name, set()) - legal
                for literal in sorted(suspicious):
                    if token_present(text_blob, literal):
                        choice_lies.append((algo, name, literal, sorted(legal), sorted(suspicious)))
                # C2：「默认 X」必须等于 registry default
                for match in re.finditer(r"默认\s*[:：]?\s*([A-Za-z_][A-Za-z0-9_\-]*|\d+(?:\.\d+)?(?:e-?\d+)?)", text_blob):
                    claimed = match.group(1)
                    actual = spec.get(name, {}).get("default")
                    same = str(claimed).lower() == str(actual).lower()
                    if not same:
                        try:
                            same = float(claimed) == float(actual)
                        except (TypeError, ValueError):
                            same = False
                    if not same:
                        default_lies.append((algo, name, claimed, actual))

        log(f"\n扫描 {len(described)} 个算法 / {checked} 个参数条目")
        if choice_lies:
            log("choice 文案疑点：")
            for algo, name, literal, legal, suspicious in choice_lies:
                log(f"   {algo}.{name}: 文案提到 {literal!r}，但本算法合法 choices={legal}（可疑集={suspicious}）")
        if default_lies:
            log("默认值疑点：")
            for algo, name, claimed, actual in default_lies:
                log(f"   {algo}.{name}: 文案说默认 {claimed!r}，registry 实际 {actual!r}")

        report.check(
            "C1 文案里的 choice 字面量都合法（对本算法）",
            not choice_lies,
            f"违规 {len(choice_lies)} 处：{choice_lies[:6] or '无'}",
        )
        report.check(
            "C2 文案里的「默认 X」都等于 registry default",
            not default_lies,
            f"违规 {len(default_lies)} 处：{default_lies[:6] or '无'}",
        )
        # ---- 检测器自检（负对照）：证明 C1/C2 真的会「抓到谎言」，而不是永远返回 0 ----
        # 这一条不依赖实现里是否还留着 bug，所以修完 F9 之后它仍然有效。
        self_test_cases = [
            ("旧版 mfbo.acquisition 文案（提到 pi/ts）", "ei/logei 平衡；pi 更偏利用；ts 用后验采样。",
             ["ei", "logei", "ucb"], ["ei", "logei", "ucb", "pi", "ts"], True),
            ("合法文案（只提本算法有的）", "ei/logei 平衡探索与利用；ucb 更偏探索。",
             ["ei", "logei", "ucb"], ["ei", "logei", "ucb", "pi", "ts"], False),
            ("token 边界：its 里的 ts 不算", "its capacity 与 ucb 无关。",
             ["ei"], ["ei", "ts"], False),
        ]
        detector_ok = True
        details = []
        for label, blob, legal_list, union_list, should_fire in self_test_cases:
            legal, union = set(legal_list), set(union_list)
            fired = any(token_present(blob, lit) for lit in sorted(union - legal))
            details.append(f"{label}: fired={fired}（期望 {should_fire}）")
            if fired is not should_fire:
                detector_ok = False
        report.check(
            "自检（负对照）：检测器对『提到非法 choice』的文案会 fire、对合法文案与子串不 fire",
            detector_ok,
            "；".join(details),
        )

        # ---- 修复形态核对：per-algorithm 文案存在，且回退文案不再复述算法特有选项 ----
        per_algo_keys = [k for k in getattr(bridge_module, "PARAM_DISPLAY", {}) if isinstance(k, tuple)]
        fallback_text = (getattr(bridge_module, "PARAM_DISPLAY", {}).get("acquisition") or {}).get("plain") or ""
        report.check(
            "修复形态：PARAM_DISPLAY 已有 (algorithm, name) 键；name 兜底文案不再列举算法特有选项",
            bool(per_algo_keys) and not token_present(fallback_text, "pi") and not token_present(fallback_text, "ts"),
            f"tuple 键 {len(per_algo_keys)} 个（样例 {per_algo_keys[:3]}）；"
            f"兜底 acquisition 文案={fallback_text!r}",
        )
    return report.finish("v21_f9_param_copy.log")


if __name__ == "__main__":
    raise SystemExit(main())
