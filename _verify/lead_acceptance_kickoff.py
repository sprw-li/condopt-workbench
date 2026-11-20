"""Lead 验收：对话式开工真实链路（chat 五轮 → 选项 → plan → init(params) → 落盘核对）。

只打真实 HTTP，不 import 实现物；最后读 runs/<id>/config.json 证明参数真的写进去了。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("CONDOPT_WORKBENCH_URL", "http://127.0.0.1:8770")


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def write_runs_dir() -> Path:
    """runs 目录**问服务要**（bootstrap.condopt.runs_dir），不靠猜。

    猜错会得到「config.json 未生成」这种假 FAIL —— 我第一版就是这么错的。
    """
    return Path(get("/api/bootstrap")["condopt"]["runs_dir"])


def post(path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def main() -> int:
    failures: list[str] = []
    runs_dir = write_runs_dir()
    print(f"runs_dir（问服务要的）= {runs_dir}")

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    status, first = post("/api/kickoff/chat", {"messages": []})
    check("首轮不带 state → 200", status == 200 and first.get("ok"), f"status={status}")
    check("首轮回复非空且含『为什么问这个』", "为什么问这个" in first["reply"]["text"])
    check("首轮快捷回复 ≥2", len(first["reply"]["quick_replies"]) >= 2)

    bad_status, bad = post("/api/kickoff/chat", {"messages": [], "state": {}})
    check("state:{} → 422 且错误可读", bad_status == 422 and bad.get("error") == "E_VALIDATION",
          f"status={bad_status} msg={bad.get('message', '')[:60]}")

    state = first["state"]
    print(f"\n--- 逐轮作答（点芯片 = 发标签文本）---")
    for turn in range(1, 9):
        chips = first["reply"]["quick_replies"]
        pick = chips[0] if turn != 4 else chips[-1]
        label = pick["label"]
        status, res = post("/api/kickoff/chat", {"messages": [{"role": "user", "content": label}], "state": state})
        if status != 200:
            check(f"第 {turn} 轮 200", False, f"status={status} {res}")
            break
        state = res["state"]
        print(f"  轮{turn}: 答「{label}」→ step={state['step']} missing={state.get('missing')}")
        if state["step"] == "options":
            first = res
            break
        first = res

    options = first.get("options") or []
    check("五轮内收敛到 options", state["step"] == "options", f"step={state['step']}")
    check("选项数在 2..4", 2 <= len(options) <= 4, f"n={len(options)}: {[o['key'] for o in options]}")
    recommended = [o for o in options if o.get("recommended")]
    check("recommended 唯一", len(recommended) == 1, f"{[o['key'] for o in recommended]}")
    for o in options:
        print(f"    · {o['key']:<12} risk={o.get('risk')} score={o.get('score')} recom={o.get('recommended')}")
        print(f"      {o['one_line']}")
        print(f"      why={o.get('why')}")
        print(f"      tradeoff={o.get('tradeoff')}")

    chosen = recommended[0]["key"] if recommended else options[0]["key"]
    status, plan = post("/api/kickoff/plan", {"state": state, "algorithm": chosen})
    check("plan → 200", status == 200 and plan.get("ok"), f"status={status}")
    check("plan.algorithm 是具体算法名（非 auto）", plan.get("algorithm") not in (None, "auto"), plan.get("algorithm"))
    check("plan.params 非空", bool(plan.get("params")), f"n={len(plan.get('params') or [])}")
    print(f"  why: {plan.get('why')}")
    print(f"  warnings: {plan.get('warnings')}")
    print(f"  参数（前 3）: {[ (p['name'], p['type'], p.get('default'), p.get('display_source')) for p in (plan.get('params') or [])[:3]]}")

    # 故意改两个参数，验证真的落盘
    params = {p["name"]: p.get("default") for p in plan["params"] if p.get("default") is not None}
    changed = {}
    if "kernel" in params:
        params["kernel"] = "rbf"
        changed["kernel"] = "rbf"
    if "alpha" in params:
        params["alpha"] = 0.05
        changed["alpha"] = 0.05
    check("构造出至少一个被改参数", bool(changed), str(changed))

    run_id = f"lead-accept-kickoff-{__import__('time').strftime('%H%M%S')}"
    payload = {"id": run_id, "answers": plan["answers"], "algorithm": plan["algorithm"], "params": params}
    status, init = post("/api/action", {"action": "init", "run": None, "payload": payload})
    check("init（带 params）→ 200", status == 200 and init.get("ok"), f"status={status} {init.get('message', '')[:80]}")

    cfg_path = runs_dir / run_id / "config.json"
    check("config.json 已生成", cfg_path.is_file(), str(cfg_path))
    if cfg_path.is_file():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        stored = cfg.get("params") or {}
        landed = {k: stored.get(k) for k in changed}
        check("改过的参数逐字落盘", landed == changed, f"stored={landed} want={changed}")
        check("config.algorithm == plan.algorithm", cfg.get("algorithm") == plan["algorithm"],
              f"{cfg.get('algorithm')} vs {plan['algorithm']}")
        print(f"  config.params（改过的键）: {landed}")

    print(f"\n结论：{'全部通过' if not failures else '存在 FAIL: ' + ', '.join(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
