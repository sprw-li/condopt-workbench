"""定案用诊断：**我的触发 A** 到底点到了哪个元素？是否发生了整页重载？

背景：两位队友观测冲突——我观察到「重选当前场景 → 气泡 5→1、step 回退」，web-shell 用
`#scenarioList [data-key="kickoff"]` 复现不出。我的触发是**按文本找元素**：

    [...document.querySelectorAll('button,a')].find(e => e.textContent.includes('开工建议')).click()

它可能命中的**不是**侧栏卡片。本探针把事实打出来，不做推断：
  1. 我的文本查找命中的元素是什么（tag/class/id/href/outerHTML 摘要）；
  2. 点击前后 `window.__mark` 是否存活（**存活 = 没有整页重载**）；
  3. 点击前后 气泡数 / step（两种触发各测一次：文本查找 vs data-key selector）；
  4. 页面是否发生过 `Page.loadEventFired` / `Page.frameNavigated`（CDP 侧的硬证据）。

运行（在克隆里）：py -3 _verify/v22_trigger_probe.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_profile as bp  # noqa: E402
import v21_geom_states as G  # noqa: E402
from common import Report, Server, fresh, log  # noqa: E402

STATE_JS = """
(() => {
  const bubbles = document.querySelectorAll('.condopt-bubble').length;
  const step = (() => { const m = document.querySelector('.condopt-kickoff-step');
    return m ? m.textContent.trim() : null; })();
  return JSON.stringify({bubbles, step, mark: window.__mark || null});
})()
"""

FIND_BY_TEXT = """
(() => {
  const el = [...document.querySelectorAll('button,a')].find(e => e.textContent.includes('开工建议'));
  if (!el) return JSON.stringify({found: false});
  return JSON.stringify({found: true, tag: el.tagName, id: el.id || null,
    cls: String(el.className || '').slice(0, 80),
    href: el.getAttribute && el.getAttribute('href'),
    inScenarioList: !!el.closest('#scenarioList'),
    dataKey: el.dataset ? (el.dataset.key || null) : null,
    html: el.outerHTML.slice(0, 160)});
})()
"""

CLICK_BY_TEXT = """
(() => {
  const el = [...document.querySelectorAll('button,a')].find(e => e.textContent.includes('开工建议'));
  if (!el) return false;
  el.click(); return true;
})()
"""

CLICK_BY_SELECTOR = """
(() => {
  const el = document.querySelector('#scenarioList [data-key="kickoff"]');
  if (!el) return false;
  el.click(); return true;
})()
"""


class Session(G.CDP):
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

    def navigations(self) -> int:
        return sum(1 for e in self.events
                   if e.get("method") in ("Page.loadEventFired", "Page.frameNavigated"))


def answer_two(session: Session) -> None:
    for _ in range(2):
        ok = session.js("(function(){const c=document.querySelectorAll('.condopt-chip');"
                        "if(!c.length)return false;c[0].click();return true;})()")
        if not ok:
            break
        session.drain(1.4)


def probe(report: Report, session: Session, label: str, click_js: str, selector_js: str) -> None:
    before = json.loads(session.js(STATE_JS))
    found = json.loads(session.js(selector_js))
    session.js("window.__mark = 'alive'; true")
    nav_before = session.navigations()
    ok = session.js(click_js)
    trajectory = []
    for _ in range(8):
        session.drain(0.5)
        trajectory.append(json.loads(session.js(STATE_JS)))
    after = trajectory[-1]
    nav_after = session.navigations()
    log(f"\n=== {label} ===")
    log(f"  命中元素: {json.dumps(found, ensure_ascii=False)}")
    log(f"  click 返回={ok}；导航事件 {nav_before}→{nav_after}（增加=发生了整页重载）")
    log(f"  window.__mark: 点击前='alive' 点击后={after['mark']!r}（None = 重载把标记清了）")
    log(f"  气泡/step: 前 {before['bubbles']}/{before['step']!r} → 后 {after['bubbles']}/{after['step']!r}")
    log(f"  轨迹: {[(s['bubbles'], s['step'], s['mark']) for s in trajectory]}")
    report.check(
        f"{label}：触发后**未发生整页重载**（window.__mark 存活）",
        after["mark"] == "alive" and nav_after == nav_before,
        f"mark={after['mark']!r} 导航事件={nav_before}→{nav_after}",
    )
    report.check(
        f"{label}：已答轮次不丢（气泡不减、step 不回退）",
        after["bubbles"] >= before["bubbles"] and after["step"] == before["step"],
        f"前 {before['bubbles']}/{before['step']!r} → 后 {after['bubbles']}/{after['step']!r}",
    )


def main() -> int:
    report = Report("触发 A 定案探针（我的文本查找 vs data-key selector）")
    runs = fresh("trigger")
    port = bp.free_port()
    profile = bp.make_profile("v22trigger")
    proc = G.start_edge(port, profile)
    try:
        with Server(runs) as server:
            session = Session(G.new_target(port))
            session.call("Page.enable")
            session.call("Runtime.enable")
            session.call("Page.navigate", {"url": f"{server.base}/?demo=kickoff"})
            session.drain(5.0)

            # --- 触发 1：我的文本查找 ---
            answer_two(session)
            probe(report, session, "触发1：按文本找 button/a（我原来的写法）",
                  CLICK_BY_TEXT, FIND_BY_TEXT)

            # --- 重置到「已答两轮」再测触发 2：data-key selector ---
            session.call("Page.navigate", {"url": f"{server.base}/?demo=kickoff"})
            session.drain(5.0)
            answer_two(session)
            probe(report, session, "触发2：web-shell 的 #scenarioList [data-key=kickoff]",
                  CLICK_BY_SELECTOR, FIND_BY_TEXT)
            session.close()
    finally:
        bp.kill_tree(proc)
    return report.finish("v22_trigger_probe.log")


if __name__ == "__main__":
    raise SystemExit(bp.run_guarded(main, "v22trigger"))
