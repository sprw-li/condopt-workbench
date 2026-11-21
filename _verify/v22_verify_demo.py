"""§11.2 `?demo=<key>` 与新聊天首屏共存 —— **verifier 自写**驱动。

> 契约 §11.2 点名 `_verify/v22_demo_coexist.py`，但**该文件在冻结提交里不存在**（见报告 §22.B 的缺口记录）。
> 本文件是我自己写的那一份。

判据（CONTRACT §11.2）：
  1. 加载 `/?demo=kickoff` → 等 3s → `#condoptKickoff` 仍存在、聊天气泡数 **!= 0**；
  2. 先点两条芯片（前进两轮）→ 再触发 `?demo=` 路径 → **已答轮次不丢**（气泡数不减少、`step` 不回退）；
  3. 全过程 `Runtime.exceptionThrown` == **0** 个未捕获异常；
  4. 演示输出落在右栏、**不覆盖**左栏聊天（复用 lead 的三重几何判据）。

运行（在克隆里）：py -3 _verify/v22_verify_demo.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_profile as bp  # noqa: E402
import geom_probe as LP  # lead 的三重判据
import v21_geom_states as G  # noqa: E402
from common import Report, Server, fresh, log  # noqa: E402

STATE_JS = """
(() => {
  const bubbles = document.querySelectorAll('.condopt-bubble').length;
  const kickoff = !!document.querySelector('#condoptKickoff');
  const chatVisible = (() => { const c = document.querySelector('.condopt-chat-log');
    return c ? c.getBoundingClientRect().height > 20 : false; })();
  return JSON.stringify({bubbles, kickoff, chatVisible});
})()
"""


class Session(G.CDP):
    """在 G.CDP 基础上收集事件（`Runtime.exceptionThrown`）。"""

    def __init__(self, ws_url: str) -> None:
        super().__init__(ws_url)
        self.events: list[dict] = []

    def drain(self, seconds: float) -> list[dict]:
        deadline = time.time() + seconds
        seen = []
        while time.time() < deadline:
            try:
                msg = json.loads(self.ws.recv())
            except Exception:  # noqa: BLE001
                continue
            if "method" in msg:
                self.events.append(msg)
                seen.append(msg)
        return seen


def exceptions(session: Session) -> list[dict]:
    return [e for e in session.events if e.get("method") == "Runtime.exceptionThrown"]


def main() -> int:
    report = Report("§11.2 ?demo= 与聊天首屏共存（verifier 自写驱动）")
    runs = fresh("demo")
    port = bp.free_port()
    profile = bp.make_profile("v22demo")
    proc = G.start_edge(port, profile)
    try:
        with Server(runs) as server:
            session = Session(G.new_target(port))
            session.call("Page.enable")
            session.call("Runtime.enable")

            # 0) 先取「不带 demo 加载同一场景」的基准（新契约判据 1 要求与它**一致**）
            session.call("Page.navigate", {"url": f"{server.base}/"})
            session.drain(4.5)
            baseline_state = json.loads(session.js(STATE_JS))
            baseline_step = session.js(
                "(function(){const m=document.querySelector('.condopt-kickoff-step');"
                "return m?m.textContent.trim():null;})()")

            # 1) 带 demo 参数加载 → 与基准**一致**（气泡数相等且仍停在第一步）
            session.call("Page.navigate", {"url": f"{server.base}/?demo=kickoff"})
            session.drain(6.0)                      # demo 约 520ms 后自动跑一遍 SSE
            first = json.loads(session.js(STATE_JS))
            first_step = session.js(
                "(function(){const m=document.querySelector('.condopt-kickoff-step');"
                "return m?m.textContent.trim():null;})()")
            report.check(
                "1) /?demo=kickoff 与「不带 demo 加载同一场景」**气泡数一致**且仍停在第一步"
                "（新契约：不是「气泡 != 0」这种太弱的断言）",
                first["kickoff"] and first["chatVisible"]
                and first["bubbles"] == baseline_state["bubbles"]
                and first_step == baseline_step,
                f"基准（无 demo）气泡={baseline_state['bubbles']} step={baseline_step!r}；"
                f"带 demo 气泡={first['bubbles']} step={first_step!r}",
            )

            # 2) 答两轮 → **触发 A：重选当前场景**（必须 no-op）
            clicked = 0
            for _ in range(6):
                if clicked >= 2:
                    break
                ok = session.js("(function(){const c=document.querySelectorAll('.condopt-chip');"
                                "if(!c.length)return false;c[0].click();return true;})()")
                if not ok:
                    break
                clicked += 1
                session.drain(1.4)
            before = json.loads(session.js(STATE_JS))
            step_before = session.js("(function(){const m=document.querySelector('.condopt-kickoff-step');"
                                     "return m?m.textContent.trim():null;})()")
            report.check(
                "2-前置) 已通过芯片答了 %d 轮（气泡=%d，step=%r）" % (clicked, before["bubbles"], step_before),
                clicked >= 2 and before["bubbles"] > 0,
                f"点击轮数={clicked} 气泡={before['bubbles']} step={step_before!r}",
            )
            # （原先这里有一条「整页导航后再触发 demo → 气泡 != 0」的检查，已删除：
            #   整页导航本身就会重置聊天，而且「!= 0」这种断言太弱（5→1 也能通过）。
            #   已由下面的「触发 A / 触发 B」按契约原文替代，两者都要求**单调不减**。）

            # 2b) 严格版（**按契约原文**）：不导航，同页触发 `?demo=` 的同一动作路径，
            #     断言「已答轮次不丢」= 气泡数**不减少** 且 step **不回退**。
            trajectory = []
            trigger = session.js(
                "(function(){const el=[...document.querySelectorAll('button,a')]"
                ".find(e=>e.textContent.includes('开工建议'));if(el)el.click();return !!el;})()")
            for _ in range(9):
                session.drain(0.5)
                snapshot = json.loads(session.js(STATE_JS))
                snapshot["step"] = session.js(
                    "(function(){const m=document.querySelector('.condopt-kickoff-step');"
                    "return m?m.textContent.trim():null;})()")
                trajectory.append(snapshot)
            after2b = trajectory[-1]
            report.check(
                "2-触发A) **重选当前场景必须 no-op**：气泡数不减、step 不回退"
                "（新契约 §11.2；当前已选中的是同一个场景）",
                after2b["bubbles"] >= before["bubbles"] and after2b["step"] == step_before,
                f"触发前 气泡={before['bubbles']} step={step_before!r} → 触发后 "
                f"气泡={after2b['bubbles']} step={after2b['step']!r}；"
                f"轨迹（气泡/step）={[(s['bubbles'], s['step']) for s in trajectory]}",
            )
            report.check(
                "2-触发A-前置) 该触发动作确实被点到了（不是「没找到元素」的假通过）",
                trigger is True,
                f"找到并点击侧栏场景项={trigger}",
            )

            # 2-触发B) 演示 `run()` 动作：同样必须保持已答轮次
            bubbles_pre_run = after2b["bubbles"]
            step_pre_run = after2b["step"]
            session.js("(function(){const b=document.querySelector('#runBtn');if(b)b.click();"
                       "return !!b;})()")
            session.drain(4.0)
            post_run = json.loads(session.js(STATE_JS))
            step_post_run = session.js(
                "(function(){const m=document.querySelector('.condopt-kickoff-step');"
                "return m?m.textContent.trim():null;})()")
            report.check(
                "2-触发B) 演示 `run()` 动作后，已答轮次仍不丢（气泡不减、step 不回退）",
                post_run["bubbles"] >= bubbles_pre_run and step_post_run == step_pre_run,
                f"run 前 气泡={bubbles_pre_run} step={step_pre_run!r} → run 后 "
                f"气泡={post_run['bubbles']} step={step_post_run!r}",
            )

            # 3) 未捕获异常
            errors = exceptions(session)
            report.check(
                "3) 全过程 Runtime.exceptionThrown == 0（没有未捕获异常）",
                not errors,
                f"异常数={len(errors)}" + (f"：{json.dumps(errors[0])[:200]}" if errors else ""),
            )

            # 4) 演示输出（右栏 .stream）不覆盖左栏聊天（三重判据）
            data = json.loads(session.js(LP.MEASURE_JS % json.dumps(LP.SELECTORS, ensure_ascii=False)))
            boxes, pairs = data["boxes"], data["pairs"]
            zero = [b for b in boxes if b["h"] <= 1 or b["w"] <= 1]
            real = [p for p in pairs if p["in_stack"]]
            report.check(
                "4) 几何：零尺寸 0 且真实重叠 0（含左栏聊天与右栏演示输出）",
                not zero and not real,
                f"可见块={len(boxes)} 零尺寸={len(zero)} 原始相交={len(pairs)} 真实重叠={len(real)}"
                + (f" 例：{real[0]}" if real else ""),
            )
            session.close()
    finally:
        bp.kill_tree(proc)
    return report.finish("v22_verify_demo.log")


if __name__ == "__main__":
    raise SystemExit(bp.run_guarded(main, "v22demo"))
