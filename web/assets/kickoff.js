/* condopt 工作台 · 对话式开工（CONTRACT §10）
 * ---------------------------------------------------------------------------
 * 把「01 开工建议」从 45 字段表单改成对话：
 *   - 左栏：聊天记录（用户/助手气泡）+ 快捷回复芯片 + 输入框（kickoff.js 本文件）
 *   - step === "options"：把 2–4 个算法渲染成**可点的链接卡**（§10.1）
 *   - 点卡 → 右栏参数页（§10.3）：助手解释 + 参数表（label/plain/impact，advanced 折叠）
 *   - 「建 study」复用 `POST /api/action {init}`（§10.4：只有一条写路径）
 *
 * 两条纪律（lead 明确要求）：
 *   1. **state 原样回传**：本文件只把服务端上一轮给的对象原样送回，不解析、不拼装。
 *      只有 `state.step` 被**读**来显示进度（§10.2 把它列为前端可见字段），从不写。
 *   2. **不在回答里堆术语**：文案由后端给；离线演示的兜底文案同样说人话
 *      （「越大越好还是越小越好」，不说 direction）。
 *
 * 端点不可用时（老后端 404/405 / 无后端）→ 明确提示 + 内置离线演示，不白屏（§10.5-6）。
 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));

  const ENDPOINTS = { chat: "/api/kickoff/chat", plan: "/api/kickoff/plan" };

  // 进度条上的「人话」步骤名（只用于显示；状态本身在服务端）
  const STEP_LABELS = {
    objective: "① 优化目标",
    budget: "② 预算与次数",
    variables: "③ 可调条件",
    noise: "④ 噪声与光滑性",
    constraints: "⑤ 硬约束",
    options: "⑥ 选算法",
    done: "已完成",
  };

  // 离线演示用（**仅离线**）：形状照抄 §10.1/§10.2/§10.3，文案说人话，且明确标注是演示。
  const DEMO = {
    turns: [
      {
        reply: {
          text: "先说清楚要优化的结果：**你希望哪个指标变大还是变小？**\n\n"
            + "为什么问这个：所有后续建议都围绕它排序——目标说反了，推荐的实验条件就会反着走。",
          quick_replies: [
            { label: "收率越大越好", value: { objective: { name: "yield", direction: "max" } } },
            { label: "纯度越大越好", value: { objective: { name: "purity", direction: "max" } } },
            { label: "成本越小越好", value: { objective: { name: "cost", direction: "min" } } },
          ],
        },
        state: { step: "objective", answers: {} },
      },
      {
        reply: {
          text: "一次实验贵不贵、总共打算做多少次？\n\n"
            + "为什么问这个：次数少就必须让每一轮都更「值」，次数多则可以更激进地探索。",
          quick_replies: [
            { label: "便宜 · 约 40 次", value: { budget: 40, effort: "quick" } },
            { label: "一般 · 约 160 次", value: { budget: 160, effort: "normal" } },
            { label: "很贵 · 约 24 次", value: { budget: 24, effort: "thorough" } },
          ],
        },
        state: { step: "budget", answers: { objective: { name: "yield", direction: "max" } } },
      },
      {
        reply: {
          text: "有哪些条件可以调？（可多选）\n\n"
            + "为什么问这个：能调的条件决定了搜索空间有多大；一个都选不出来的话，没有可推荐的实验。",
          quick_replies: [
            { label: "温度、时间", value: { variables: [{ name: "T" }, { name: "time_min" }] } },
            { label: "再加溶剂（分类）", value: { variables: [{ name: "T" }, { name: "time_min" }, { name: "solvent" }] } },
          ],
        },
        state: { step: "variables", answers: {} },
      },
      {
        reply: {
          text: "测量噪声大吗？结果随条件的变化是平滑还是跳变？\n\n"
            + "为什么问这个：噪声大就要更保守地拟合，跳变明显则不能假设「附近条件结果也接近」。",
          quick_replies: [
            { label: "噪声小 · 比较平滑", value: { noise: "low", smoothness: "smooth" } },
            { label: "噪声中等", value: { noise: "medium", smoothness: "medium" } },
            { label: "噪声大 · 有跳变", value: { noise: "high", smoothness: "rugged" } },
          ],
        },
        state: { step: "noise", answers: {} },
      },
      {
        reply: {
          text: "有没有硬约束（必须满足的指标）？\n\n"
            + "为什么问这个：约束会挡住一部分推荐——先说清楚，省得推荐出来的条件你根本不能用。",
          quick_replies: [
            { label: "没有硬约束", value: { constraints: [] } },
            { label: "纯度 ≥ 95", value: { constraints: [{ name: "purity", op: ">=", value: 95 }] } },
          ],
        },
        state: { step: "constraints", answers: {} },
      },
    ],
    options: [
      {
        key: "bayes", title: "贝叶斯优化（bayes）",
        one_line: "样本效率最高，实验次数少时最可能逼近最优",
        why: ["可调条件不多（≤ 12）", "结果随条件变化比较平滑", "次数少、每次实验贵"],
        tradeoff: "它假设「条件相近则结果相近」；如果你的结果有跳变，效果会打折",
        recommended: true, risk: "low",
      },
      {
        key: "random", title: "随机搜索（random）",
        one_line: "最简单、最不容易被假设坑到，但同样次数下通常更慢接近最优",
        why: ["完全不假设结果形状", "适合当作基线对照"],
        tradeoff: "预算利用率低；想用 24 次拿到好结果一般不如贝叶斯",
        recommended: false, risk: "low",
      },
      {
        key: "cmaes", title: "进化策略（cmaes）",
        one_line: "适合连续参数多、结果有噪声或不够平滑的情况",
        why: ["连续变量为主", "噪声较大"],
        tradeoff: "需要更多次实验才能收敛；分类条件处理起来不如贝叶斯自然",
        recommended: false, risk: "medium",
      },
    ],
    why: ["离线演示：按契约 §10.1 的规则路由示例给出，不是真实 router 判断"],
    params: [
      { name: "kernel", type: "choice", default: "matern52", choices: ["matern52", "matern32", "rbf"],
        label: "核函数", plain: "越光滑的数据用 rbf；一般用 matern52 更稳", impact: "high", advanced: false },
      { name: "length_scale", type: "float", default: 0.3, low: 0.01, high: 10.0,
        label: "初始影响范围", plain: "取小了只看眼前、取大了会太保守；不确定就留默认", impact: "high", advanced: false },
      { name: "alpha", type: "float", default: 0.000001, low: 0.0, high: 0.1,
        label: "噪声容忍", plain: "测量噪声越大可以调大一点，避免把噪声当规律", impact: "medium", advanced: false },
      { name: "ard", type: "bool", default: true,
        label: "逐维影响范围", plain: "让每个条件有自己的影响范围；一般开着更好", impact: "medium", advanced: false },
      { name: "normalize_y", type: "bool", default: true,
        label: "结果归一化", plain: "结果数值范围很大时建议开着", impact: "low", advanced: true },
      { name: "n_restarts", type: "int", default: 3, low: 1, high: 20,
        label: "重启次数", plain: "多试几次初始化，慢一点但更稳", impact: "low", advanced: true },
    ],
    paramsSource: "离线演示：参数表来自内置示例，不是后端 registry",
  };

  const state = {
    mounted: false,
    started: false,
    offline: false,
    offlineReason: "",
    busy: false,
    messages: [],      // [{role, content}] —— 只用于回传给服务端与显示
    lastState: null,   // 服务端状态，**原样**保存与回传；null = 还没有（第一轮不带 state）
    step: "",
    quickReplies: [],
    options: null,
    plan: null,
    params: {},        // 用户改过的参数值 name -> value
    algorithm: "",
    studyId: "",
    built: null,       // init 成功后的 run 快照
    pending: null,     // ask 返回的待测建议
    demoIndex: 0,
    demoState: {},
  };

  /* ---------------------------------------------------------------- utils */

  function toast(message) {
    const box = $("toast");
    const text = $("toastText");
    if (!box || !text) return;
    text.textContent = message;
    box.classList.add("is-on");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => box.classList.remove("is-on"), 3200);
  }

  function refreshIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      try { window.lucide.createIcons(); } catch (error) { /* 图标不阻塞 */ }
    }
  }

  // 「端点不存在 / 后端不可达」→ 走离线演示；**不是**这个范围的错误（E_VALIDATION 等）必须如实报给用户。
  // 注意：server.py 对未实现的路由返回 404 + `E_NOT_FOUND`（不是 HTTP_xxx），所以两者都要算。
  function isEndpointMissing(error) {
    const code = String((error && error.code) || "");
    if (!code) return true;
    if (error && (error.status === 404 || error.status === 405)) return true;
    return code === "NETWORK" || code === "E_NOT_FOUND" || code.startsWith("HTTP_");
  }

  function online() {
    return Boolean(window.CONDOPT && window.CONDOPT.online);
  }

  // markdown → 安全 HTML：有 marked + DOMPurify 就走渲染，否则退化成纯文本（绝不注入）
  function md(text) {
    const raw = String(text ?? "");
    if (window.marked && window.DOMPurify) {
      try {
        return window.DOMPurify.sanitize(window.marked.parse(raw, { breaks: true, gfm: true }));
      } catch (error) { /* 落到下面 */ }
    }
    return esc(raw).replace(/\n/g, "<br>");
  }

  function fmtValue(value) {
    if (value === null || value === undefined || value === "") return "（空）";
    if (Array.isArray(value)) return JSON.stringify(value);
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function newStudyId() {
    const now = new Date();
    const pad = (value) => String(value).padStart(2, "0");
    return `run-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`;
  }

  async function postJson(url, body) {
    let response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body || {}),
      });
    } catch (error) {
      const failure = new Error("网络层失败：无法连接后端（" + (error && error.message ? error.message : "fetch error") + "）");
      failure.code = "NETWORK";
      throw failure;
    }
    let data = null;
    try { data = await response.json(); } catch (error) { data = null; }
    if (!response.ok || (data && data.ok === false)) {
      const failure = new Error((data && (data.message || data.error)) || ("HTTP " + response.status));
      failure.code = (data && data.error) || ("HTTP_" + response.status);
      failure.status = response.status;
      failure.hint = data && data.hint;
      failure.raw = data;
      throw failure;
    }
    return data || {};
  }

  /* ---------------------------------------------------------------- 聊天渲染 */

  function mount() {
    return $("condoptKickoff");
  }

  function render() {
    const box = mount();
    if (!box) return;
    if (!state.mounted) {
      state.mounted = true;
      bind();
    }
    const stepLabel = STEP_LABELS[state.step] || (state.step ? state.step : "");
    const bubbles = state.messages.map((message) => {
      const who = message.role === "user" ? "你" : "助手";
      const why = message.role === "assistant" && message.why
        ? `<div class="condopt-bubble-why">为什么问这个：${esc(message.why)}</div>` : "";
      return `<div class="condopt-bubble is-${message.role === "user" ? "user" : "assistant"}">
        <span class="condopt-bubble-who">${who}</span>
        <div class="condopt-bubble-text">${md(message.content)}</div>
        ${why}
      </div>`;
    }).join("");

    const optionsHtml = (state.step === "options" && state.options && state.options.length)
      ? renderOptions(state.options) : "";

    const chipsHtml = (!state.busy && state.quickReplies && state.quickReplies.length)
      ? `<div class="condopt-chips" id="condoptChips">${state.quickReplies.map((chip, index) => `
          <button class="condopt-chip" type="button" data-chip-index="${index}">
            <i data-lucide="corner-down-right"></i>${esc(chip.label || chip.value || "（未命名）")}
          </button>`).join("")}</div>`
      : "";

    const offlineHtml = state.offline
      ? `<p class="condopt-kickoff-offline"><strong>离线演示</strong>：${esc(state.offlineReason || "后端未连接")}
         —— 下面是内置示例对话与示例参数，<strong>不是后端 router 的真实判断</strong>。
         启动服务（<code>py -3 server.py</code>）后自动切回真实接口。</p>` : "";
    // `state.missing` 是后端给的**只读**提示（还缺哪些信息才能收敛）；只显示，绝不回写。
    const missing = (state.lastState && Array.isArray(state.lastState.missing)) ? state.lastState.missing : [];
    const missingHtml = (!state.offline && missing.length && state.step !== "options")
      ? `<p class="condopt-chat-missing">还缺：${missing.map((item) => esc(String(item))).join("、")}（补齐后才会给算法选项）</p>` : "";
    const importHint = state.importHint
      ? `<div class="condopt-options"><button class="btn btn-sm btn-ghost" type="button" id="condoptKickoffToImport">
           <i data-lucide="file-input"></i>去 06「导入数据」用这份历史数据
         </button></div>` : "";

    box.innerHTML = `
      <div class="condopt-kickoff-head">
        <span class="kicker">对话式开工</span>
        <span class="badge">${state.offline ? "离线演示" : "规则顾问"}</span>
        ${stepLabel ? `<span class="condopt-kickoff-step">${esc(stepLabel)}</span>` : ""}
      </div>
      ${offlineHtml}
      <div class="condopt-chat-log" id="condoptChatLog">${bubbles}</div>
      ${missingHtml}
      ${optionsHtml}
      ${importHint}
      ${chipsHtml}
      <div class="condopt-chat-input">
        <input class="input" type="text" id="condoptChatText" autocomplete="off"
               placeholder="${state.offline ? "离线演示：可以点上面的选项继续" : "也可以直接打字回答（例如：温度、时间；大概 40 次）"}">
        <button class="btn btn-sm btn-accent" type="button" id="condoptChatSend">
          <i data-lucide="send-horizontal"></i>发送
        </button>
      </div>
      <p class="condopt-chat-note">助手一次只问一个问题；不确定就点芯片。上面的选项是一次性回答，答案会直接决定推荐哪个算法。</p>
      <p class="condopt-chat-typing" id="condoptChatTyping" hidden>助手正在整理…</p>`;

    refreshIcons();
    const log = $("condoptChatLog");
    if (log) log.scrollTop = log.scrollHeight;
  }

  function renderOptions(options) {
    const cards = options.map((option) => {
      const key = String(option.key || "");
      const why = (option.why || []).map((item) => `<span>${esc(item)}</span>`).join("");
      const risk = option.risk ? `<span class="condopt-badge-risk is-${esc(String(option.risk))}">风险 ${esc(String(option.risk))}</span>` : "";
      const picked = state.algorithm && state.algorithm === key;
      // router 规则判死的条目（分数 ≤ -100）**仍然列出**（列出来本身就是解释价值），只置灰。
      const dead = option.disqualified === true;
      return `<button class="condopt-opt-card ${option.recommended ? "is-recommended" : ""}${dead ? " is-disqualified" : ""}"
          type="button" data-opt-key="${esc(key)}"${dead ? ' title="路由器规则判定不适合你这组答案；仍可点进去看参数"' : ""}>
        <span class="condopt-opt-top">
          <span class="condopt-opt-title">${esc(option.title || key)}</span>
          ${option.recommended ? `<span class="condopt-badge-rec">推荐</span>` : ""}
          ${dead ? `<span class="condopt-badge-risk is-high">规则判死（不推荐）</span>` : ""}
          ${picked ? `<span class="condopt-badge-rec">已选</span>` : ""}
          ${risk}
        </span>
        ${option.one_line ? `<span class="condopt-opt-one">${esc(option.one_line)}</span>` : ""}
        ${why ? `<span class="condopt-opt-why">${why}</span>` : ""}
        ${option.tradeoff ? `<span class="condopt-opt-tradeoff">取舍：${esc(option.tradeoff)}</span>` : ""}
        <span class="condopt-opt-go"><i data-lucide="sliders-horizontal"></i>${picked ? "再看参数页" : "点进去调参数"}</span>
      </button>`;
    }).join("");
    return `<div class="condopt-options">
      <span class="condopt-options-head">按你上面的回答，这几个算法适合你（点一张卡进入参数页）：</span>
      ${cards}</div>`;
  }

  /* ---------------------------------------------------------------- 流程 */

  function bind() {
    const box = mount();
    if (!box || box.dataset.bound === "1") return;
    box.dataset.bound = "1";
    box.addEventListener("click", (event) => {
      const chip = event.target.closest("[data-chip-index]");
      if (chip) {
        const index = Number(chip.dataset.chipIndex);
        const item = state.quickReplies[index];
        // 芯片：把用户看到的 label 当作他说的话发出去（请求体形状仍是契约的 {messages,state}）
        if (item) send(String(item.label || item.value || ""), { freeText: false, chip: item });
        return;
      }
      const option = event.target.closest("[data-opt-key]");
      if (option) { openPlan(option.dataset.optKey); return; }
      if (event.target.closest("#condoptKickoffToImport")) {
        const item = document.querySelector('#scenarioList [data-key="import"]');
        if (item) item.click();
        return;
      }
      if (event.target.closest("#condoptChatSend")) { submitTyped(); }
    });
    box.addEventListener("keydown", (event) => {
      if (event.target && event.target.id === "condoptChatText" && event.key === "Enter") {
        event.preventDefault();
        submitTyped();
      }
    });
  }

  function submitTyped() {
    const input = $("condoptChatText");
    if (!input) return;
    const text = String(input.value || "").trim();
    if (!text) { toast("先打一句话，或点上面的选项"); return; }
    input.value = "";
    send(text, { freeText: true });
  }

  async function send(content, options = {}) {
    if (state.busy) return;
    state.busy = true;
    state.messages.push({ role: "user", content });
    state.quickReplies = [];
    render();
    setTyping(true);
    try {
      if (state.offline) { demoAdvance(); return; }
      const body = {
        // 契约 §10.2：messages + state（state **原样回传**，前端不解析不拼装）。
        // lastState 为 null（还没问过第一轮）时**不带 state 字段**，而不是塞一个 {}。
        messages: state.messages.map((message) => ({ role: message.role, content: message.content })),
      };
      if (state.lastState) body.state = state.lastState;
      if (options.freeText) body.free_text = true;
      const data = await postJson(ENDPOINTS.chat, body);
      state.lastState = data.state || null;    // 原样保存（后端没给就保持 null，下一轮同样不带）
      state.step = String((data.state && data.state.step) || "");
      const reply = data.reply || {};
      state.quickReplies = Array.isArray(reply.quick_replies) ? reply.quick_replies : [];
      const pool = Array.isArray(data.options) && data.options.length ? data.options
        : (Array.isArray(data.candidates) && data.candidates.length ? data.candidates : null);
      state.options = state.step === "options" ? pool : null;
      if (state.step === "options" && !state.options) {
        // 契约 §10.2：信息不足时不许给「凑出来的」选项；后端也会明说缺什么。这里如实转达。
        state.offlineReason = "";
      }
      state.messages.push({ role: "assistant", content: reply.text || "（后端没有返回文字）", why: reply.why || "" });
      // 「我贴一份 → 06 导入」这条芯片**按设计不收敛**（停在 variables 并指向 06 场景）。
      // 这是设计内行为，不是 bug；这里只在用户真的点了这类芯片时给一个跳转入口，省得他找。
      const clickedLabel = String((options.chip && options.chip.label) || "");
      state.importHint = /导入|历史数据/.test(clickedLabel) && state.step === "variables";
      if (window.CONDOPT && window.CONDOPT.setOnline) window.CONDOPT.setOnline(true);
    } catch (error) {
      if (isEndpointMissing(error)) {
        enterOffline(error.code === "NETWORK"
          ? "后端未连接（网络层失败）"
          : `后端还没有 /api/kickoff/chat（${error.code}）`);
        demoAdvance();
      } else {
        state.messages.push({
          role: "assistant",
          content: `没能继续：**${error.message}**${error.hint ? "\n\n" + error.hint : ""}\n\n`
            + "（可以改一个字再发一次；如果是配置问题，修好后我会接着按服务端保存的状态继续问。）",
        });
      }
    } finally {
      state.busy = false;
      setTyping(false);
      render();
    }
  }

  // 开聊：`messages: []` + `state: {}` 让服务端问第一个问题（不伪造一条空的用户消息）
  async function start() {
    if (state.busy) return;
    state.busy = true;
    setTyping(true);
    try {
      if (state.offline) { demoStart(); return; }
      // 第一轮**不带 state**：契约说 state 是「服务端上一轮返回的 state 原样回传」，
      // 此刻还没有上一轮；实测后端对 `state:{}` 会 422（state.step 非法），
      // 缺省/ null 才走「新对话」。前端不构造状态。
      const data = await postJson(ENDPOINTS.chat, { messages: [] });
      state.lastState = data.state || null;
      state.step = String((data.state && data.state.step) || "");
      const reply = data.reply || {};
      state.quickReplies = Array.isArray(reply.quick_replies) ? reply.quick_replies : [];
      state.messages = [{ role: "assistant", content: reply.text || "（后端没有返回文字）", why: reply.why || "" }];
      state.options = null;
    } catch (error) {
      if (isEndpointMissing(error)) {
        enterOffline(error.code === "NETWORK"
          ? "后端未连接（网络层失败）"
          : `后端还没有 /api/kickoff/chat（${error.code}）`);
        demoStart();
      } else {
        state.messages = [{
          role: "assistant",
          content: `开始提问失败：**${error.message}**${error.hint ? "\n\n" + error.hint : ""}`,
        }];
      }
    } finally {
      state.busy = false;
      setTyping(false);
      render();
    }
  }

  function setTyping(on) {
    const node = $("condoptChatTyping");
    if (node) node.hidden = !on;
  }

  function enterOffline(reason) {
    if (!state.offline) {
      state.offline = true;
      state.offlineReason = reason;
      state.lastState = null;
      state.step = "";
      state.quickReplies = [];
      state.options = null;
    }
  }

  /* ------------------------------------------------- 离线演示（§10.5-6：不白屏） */

  function demoStart() {
    const turn = DEMO.turns[0];
    state.step = (state.demoState.step = turn.state.step);
    state.lastState = turn.state;
    state.quickReplies = turn.reply.quick_replies;
    state.messages = [];
    state.messages.push({ role: "assistant", content: turn.reply.text });
    state.demoIndex = 0;
  }

  function demoAdvance() {
    state.demoIndex += 1;
    if (state.demoIndex <= DEMO.turns.length - 1) {
      const turn = DEMO.turns[state.demoIndex];
      state.demoState = Object.assign({}, state.demoState, turn.state);
      state.step = turn.state.step;
      state.lastState = state.demoState;
      state.quickReplies = turn.reply.quick_replies;
      state.options = null;
      state.messages.push({ role: "assistant", content: turn.reply.text });
      return;
    }
    // 第 5 轮之后 = 算法选项（§10.1）
    state.step = "options";
    state.demoState = Object.assign({}, state.demoState, { step: "options" });
    state.lastState = state.demoState;
    state.quickReplies = [];
    state.options = DEMO.options;
    state.messages.push({
      role: "assistant",
      content: "问题问完了。按你的回答，**这几个算法适合你**——每张卡都写了为什么和取舍，点一张进参数页。"
        + "\n\n为什么给这三个：规则顾问按「可调条件数量 / 结果光滑程度 / 实验预算」三项匹配，离线演示用的是内置示例判断。",
    });
  }

  /* ---------------------------------------------------------------- 参数页（§10.3） */

  async function openPlan(algorithm) {
    if (state.busy) return;
    state.algorithm = algorithm;
    state.busy = true;
    try {
      if (state.offline) {
        state.plan = {
          ok: true, algorithm, why: DEMO.why, params: DEMO.params,
          answers: state.lastState.answers || {}, warnings: [], demo: true,
          params_note: DEMO.paramsSource,
        };
      } else {
        const data = await postJson(ENDPOINTS.plan, { state: state.lastState, algorithm });
        state.plan = data;
      }
      state.params = {};
      (state.plan.params || []).forEach((param) => {
        if (param && param.name !== undefined && param.default !== undefined) state.params[param.name] = param.default;
      });
      state.algorithm = state.plan.algorithm || algorithm;
      state.studyId = state.studyId || newStudyId();
      renderPlan();
      const box = $("condoptKickoffPlan");
      if (box) { box.hidden = false; box.scrollIntoView({ block: "nearest" }); }
    } catch (error) {
      renderPlanError(error);
    } finally {
      state.busy = false;
    }
  }

  function paramRow(param, advanced) {
    const name = String(param.name || "");
    const value = state.params[name];
    const type = String(param.type || "str");
    const impact = String(param.impact || "").toLowerCase();
    const impactHtml = impact
      ? `<span class="condopt-impact is-${esc(impact)}">影响：${impact === "high" ? "大" : (impact === "medium" ? "中" : "小")}</span>` : "";
    let control = "";
    if (type === "choice") {
      const choices = Array.isArray(param.choices) ? param.choices : [];
      const options = choices.map((choice) => `<option value="${esc(choice)}" ${String(choice) === String(value) ? "selected" : ""}>${esc(choice)}</option>`);
      if (!choices.some((choice) => String(choice) === String(value))) {
        options.unshift(`<option value="${esc(value)}" selected>${esc(fmtValue(value))}</option>`);
      }
      control = `<select class="input" data-param-name="${esc(name)}">${options.join("")}</select>`;
    } else if (type === "bool") {
      control = `<label class="condopt-param-check">
        <input type="checkbox" data-param-name="${esc(name)}" ${value ? "checked" : ""}>${value ? "开启" : "关闭"}</label>`;
    } else if (type === "int" || type === "float") {
      const step = type === "int" ? "1" : "any";
      const min = param.low !== undefined && param.low !== null ? ` min="${esc(param.low)}"` : "";
      const max = param.high !== undefined && param.high !== null ? ` max="${esc(param.high)}"` : "";
      control = `<input class="input" type="number" step="${step}"${min}${max}
        data-param-name="${esc(name)}" value="${esc(value === undefined || value === null ? "" : value)}">`;
    } else {
      control = `<input class="input" type="text" data-param-name="${esc(name)}" value="${esc(value === undefined || value === null ? "" : value)}">`;
    }
    return `<div class="condopt-param${advanced ? " is-advanced" : ""}" data-param-row="${esc(name)}"
      ${param.display_source ? `data-display-source="${esc(param.display_source)}"` : ""}>
      <div>
        <div class="condopt-param-label">
          <strong>${esc(param.label || name)}</strong>
          <code class="condopt-param-name">${esc(name)}</code>
          ${impactHtml}
          ${param.display_source === "registry" ? `<span class="condopt-opt-why"><span>说明来自算法库</span></span>` : ""}
        </div>
        <span class="condopt-param-plain">${esc(param.plain || param.description || "（后端未给说明）")}</span>
        <span class="condopt-param-default">默认：<code>${esc(fmtValue(param.default))}</code>${
          param.low !== undefined && param.low !== null ? ` · 范围 ${esc(fmtValue(param.low))}–${esc(fmtValue(param.high))}` : ""}</span>
      </div>
      <div class="condopt-param-control">${control}</div>
    </div>`;
  }

  function renderPlan() {
    const box = $("condoptKickoffPlan");
    if (!box || !state.plan) return;
    const plan = state.plan;
    const params = Array.isArray(plan.params) ? plan.params : [];
    const basic = params.filter((param) => !param.advanced);
    const advanced = params.filter((param) => param.advanced);
    const why = Array.isArray(plan.why) ? plan.why : [];
    const answers = plan.answers || (state.lastState && state.lastState.answers) || {};

    const sourceNote = plan.display_source_note || plan.params_source || plan.params_note
      || "参数来自 registry（代码取值）；label/plain 是展示层中文说明";
    const registryFallback = params.filter((param) => param.display_source === "registry").length;
    const warnings = Array.isArray(plan.warnings) ? plan.warnings : [];
    const specWarning = warnings.some((item) => /spec_score/.test(String(item)));

    box.hidden = false;
    box.innerHTML = `
      <div class="condopt-plan-head">
        <span class="kicker">参数页</span>
        <span class="badge badge-teal">${esc(plan.algorithm || state.algorithm)}</span>
        <span class="hint">${esc(sourceNote)}</span>
      </div>
      <div class="condopt-plan-why">
        <strong>助手为什么推荐它：</strong>
        ${why.length ? `<ul>${why.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`
          : `<span>${esc(plan.rationale || "（后端未给出理由）")}</span>`}
        <div class="condopt-plan-note">下面每个参数都写了「人话说明」；只改你有把握的，其余保持默认即可。</div>
      </div>
      ${warnings.length ? `<div class="condopt-plan-warnings${specWarning ? " is-costly" : ""}">
        <strong>${specWarning ? "注意（这是真代价，不是噪音）：" : "注意："}</strong>
        <ul>${warnings.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>
      </div>` : ""}
      ${registryFallback ? `<p class="condopt-plan-note">其中 ${registryFallback} 个参数没有工作台中文说明，已回落到算法库原始 <code>description</code>（这些一般是高级参数）。</p>` : ""}
      <div class="condopt-param-group" id="condoptParamBasic">
        ${basic.map((param) => paramRow(param, false)).join("") || `<p class="hint">该算法没有基础参数。</p>`}
      </div>
      ${advanced.length ? `<details class="condopt-advanced-params">
        <summary>高级参数（${advanced.length} 个）<span class="hint">默认折叠；不确定就别动</span></summary>
        <div class="condopt-advanced-params-body">
          ${advanced.map((param) => paramRow(param, true)).join("")}
        </div>
      </details>` : ""}
      <div class="condopt-plan-build">
        <label class="condopt-inline"><span>study id</span>
          <input class="input" type="text" id="condoptKickoffRunId" value="${esc(state.studyId)}"></label>
        <button class="btn btn-sm btn-primary" type="button" id="condoptKickoffBuild">
          <i data-lucide="plus-circle"></i>建 study（走 POST /api/action init）
        </button>
        <button class="btn btn-sm btn-ghost" type="button" id="condoptKickoffSyncJson">
          <i data-lucide="file-json"></i>同步到 answers JSON
        </button>
        <span class="hint">对话只是攒 answers 的另一种入口；建 study 与「开工表单」是同一条写路径。</span>
      </div>
      ${state.built ? renderBuilt() : ""}`;
    refreshIcons();
  }

  function renderBuilt() {
    const run = state.built || {};
    return `
      ${state.paramsDropped ? `<p class="condopt-import-problems"><strong>注意：你改的算法子参数没有写进 study。</strong>
        这台后端的 <code>init</code> 还不接受 <code>params</code>${state.autoDropped ? "（或算法仍是 auto，后端不允许 auto + 参数）" : ""}，
        所以本次是按算法默认参数建的 study。修好后重试即可带上参数。</p>` : ""}
      <div class="condopt-run-semantics">
        <span class="condopt-stat"><span>study</span><strong>${esc(run.id || state.studyId || "—")}</strong></span>
        <span class="condopt-stat"><span>算法</span><strong>${esc(run.algorithm || state.algorithm || "—")}</strong></span>
        <span class="condopt-stat"><span>n_obs</span><strong>${esc(fmtValue(run.n_obs))}</strong></span>
        <span class="condopt-stat"><span>剩余预算</span><strong>${esc(fmtValue(run.remaining))}</strong></span>
        <span class="hint">与「导入数据」「结果回填」同一套 run 语义：同一个 study 目录、同一份 dataset.jsonl。</span>
      </div>
      <div class="condopt-import-row">
        <button class="btn btn-sm btn-accent" type="button" id="condoptKickoffNext">
          <i data-lucide="play"></i>生成下一批
        </button>
        <button class="btn btn-sm btn-ghost" type="button" id="condoptKickoffToSample">
          <i data-lucide="grid-3x3"></i>去「下一批取样」场景
        </button>
        <span class="hint" id="condoptKickoffNextHint">${state.pending
          ? `已生成 ${state.pending.length} 条待测条件（在「下一批取样」场景里逐条执行）`
          : "按当前算法与参数生成第一批待测条件"}</span>
      </div>
      ${state.pending && state.pending.length ? `<div class="condopt-table-wrap"><table class="condopt-table">
        <thead><tr><th>孔板</th><th>条件</th></tr></thead>
        <tbody>${state.pending.map((item) => `<tr>
          <td>${esc(item.plate || "—")}</td>
          <td><code>${esc(JSON.stringify(item.x || item))}</code></td></tr>`).join("")}</tbody>
      </table></div>` : ""}`;
  }

  function renderPlanError(error) {
    const box = $("condoptKickoffPlan");
    if (!box) return;
    box.hidden = false;
    const offline = isEndpointMissing(error);
    box.innerHTML = `
      <div class="condopt-plan-head">
        <span class="kicker">参数页</span>
        <span class="badge">${offline ? "离线" : esc(error.code || "错误")}</span>
      </div>
      <p class="condopt-import-error">${offline
        ? `拿不到参数页：后端还没有 <code>/api/kickoff/plan</code>（${esc(error.message)}）。`
        : `参数页失败：${esc(error.message)}`}${error.hint ? `<br>${esc(error.hint)}` : ""}</p>
      <div class="condopt-import-row">
        <button class="btn btn-sm btn-ghost" type="button" id="condoptKickoffRetry">
          <i data-lucide="rotate-ccw"></i>重试
        </button>
        <span class="hint">端点不可用不影响已选算法：修好后点重试即可。</span>
      </div>`;
    refreshIcons();
  }

  function readParams() {
    const box = $("condoptKickoffPlan");
    if (!box) return {};
    const out = {};
    box.querySelectorAll("[data-param-name]").forEach((control) => {
      const name = control.dataset.paramName;
      if (!name) return;
      if (control.type === "checkbox") out[name] = control.checked;
      else if (control.type === "number") {
        const text = String(control.value || "").trim();
        out[name] = text === "" ? null : Number(text);
      } else out[name] = control.value;
    });
    return out;
  }

  async function buildStudy() {
    if (state.busy) return;
    const plan = state.plan;
    if (!plan) return;
    const idInput = $("condoptKickoffRunId");
    const id = String((idInput && idInput.value) || state.studyId || "").trim();
    if (!id) { toast("先填一个 study id"); return; }
    state.studyId = id;
    const params = readParams();
    state.params = params;
    const answers = plan.answers || (state.lastState && state.lastState.answers) || {};
    if (!window.CONDOPT || typeof window.CONDOPT.action !== "function") {
      toast("前端未就绪：CONDOPT.action 不可用");
      return;
    }
    state.busy = true;
    const button = $("condoptKickoffBuild");
    if (button) button.disabled = true;
    try {
      // §10.4：init 不变 —— 对话只是攒 answers 的另一种入口，与开工表单同一条写路径。
      // payload 形状（task-15 落地）：`{id, answers, algorithm, params}`，params 是**平表**
      // `{参数名: 值}`（readParams() 收集的就是这个形状，不嵌套、不按算法分组）。
      // 后端规则：`algorithm:"auto"` + 非空 params → 422「algorithm=auto 时不能指定算法参数」，
      // 所以这里优先用 plan 解析出来的**具体算法名**；真拿不到具体算法时宁可不带 params
      // （并用下方 notice 说明），也不发一个注定 422 的组合。
      const resolved = plan.algorithm || state.algorithm || "auto";
      const algorithm = resolved === "auto" ? (state.algorithm || "auto") : resolved;
      const hasParams = params && Object.keys(params).length > 0;
      const sendParams = algorithm === "auto" ? {} : params;
      const autoDroppedParams = hasParams && algorithm === "auto";
      const base = { id, answers, algorithm };
      if (Object.keys(sendParams).length) base.params = sendParams;
      let data;
      let paramsDropped = autoDroppedParams;
      try {
        data = await window.CONDOPT.action("init", null, base);
      } catch (error) {
        // 兼容 task-15 之前的后端：它不接受 `params`（"init 的 payload 未知字段: params"）。
        // 退回不带 params 建 study，并**明确告知**参数没写进去（不静默、不假装成功）。
        if (error && error.code === "E_VALIDATION" && /params/.test(String(error.message || ""))) {
          paramsDropped = true;
          data = await window.CONDOPT.action("init", null, { id, answers, algorithm });
        } else {
          throw error;
        }
      }
      const result = (data && data.result) || {};
      state.built = result.run || { id };
      state.paramsDropped = paramsDropped;
      state.autoDropped = autoDroppedParams;
      state.sentParams = Object.keys(sendParams).length ? sendParams : null;
      syncAnswersToJson(answers);
      toast(paramsDropped
        ? "已建 study（注意：后端暂不收 params，算法子参数未写入）"
        : ((data && data.message) || "已建 study"));
      if (window.CONDOPT.refreshRuns) await window.CONDOPT.refreshRuns({ quiet: true });
      if (window.CONDOPT.refreshStudy) await window.CONDOPT.refreshStudy({ quiet: true });
      if (window.CONDOPT.openReviewPanel) window.CONDOPT.openReviewPanel();
      renderPlan();
    } catch (error) {
      const node = $("condoptKickoffPlan");
      if (node) {
        const warn = document.createElement("p");
        warn.className = "condopt-import-error";
        warn.innerHTML = `建 study 失败：${esc(error.message)}${error.hint ? `（${esc(error.hint)}）` : ""}`;
        node.appendChild(warn);
      }
      toast("建 study 失败：" + error.message);
    } finally {
      state.busy = false;
      if (button) button.disabled = false;
    }
  }

  async function nextBatch() {
    if (state.busy) return;
    const runId = (state.built && state.built.id) || state.studyId;
    if (!runId) { toast("先建 study"); return; }
    if (!window.CONDOPT || typeof window.CONDOPT.action !== "function") return;
    state.busy = true;
    const button = $("condoptKickoffNext");
    if (button) button.disabled = true;
    try {
      const answers = (state.plan && state.plan.answers) || {};
      const payload = {};
      if (Number.isFinite(Number(answers.batch))) payload.n = Number(answers.batch);
      const data = await window.CONDOPT.action("ask", runId, payload);
      const result = (data && data.result) || {};
      const pending = result.pending || (result.suggestions ? { length: result.suggestions.length } : null);
      state.pending = Array.isArray(result.pending) ? result.pending
        : (Array.isArray(result.suggestions) ? result.suggestions : []);
      if (window.CONDOPT.refreshStudy) await window.CONDOPT.refreshStudy({ quiet: true });
      toast(`已生成下一批 ${state.pending.length || (pending && pending.length) || ""} 条待测条件`);
      renderPlan();
    } catch (error) {
      toast("生成下一批失败：" + error.message);
    } finally {
      state.busy = false;
      if (button) button.disabled = false;
    }
  }

  // 把（后端给的）answers 写进高级抽屉里的 JSON，让「开始处理」/拍板与对话走同一份载荷。
  function syncAnswersToJson(answers) {
    const input = $("input");
    if (!input || !answers) return;
    try {
      input.value = JSON.stringify(answers, null, 2);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    } catch (error) { /* 同步失败不影响建 study */ }
  }

  // 参数页里的按钮用事件委托（renderPlan 会整块重绘）
  document.addEventListener("click", (event) => {
    if (!event.target || !event.target.closest) return;
    if (event.target.closest("#condoptKickoffBuild")) { buildStudy(); return; }
    if (event.target.closest("#condoptKickoffNext")) { nextBatch(); return; }
    if (event.target.closest("#condoptKickoffSyncJson")) {
      syncAnswersToJson((state.plan && state.plan.answers) || (state.lastState && state.lastState.answers));
      toast("已同步到「高级：直接改 answers JSON」");
      return;
    }
    if (event.target.closest("#condoptKickoffToSample")) {
      const item = document.querySelector('#scenarioList [data-key="sample"]');
      if (item) item.click();
      return;
    }
    if (event.target.closest("#condoptKickoffRetry")) { openPlan(state.algorithm || "auto"); }
  });

  /* ---------------------------------------------------------------- 对外 */

  window.CONDOPT_KICKOFF = {
    handlesScenario: (key) => key === "kickoff",
    // 01 场景进入时调用（condopt.js 的 syncScenario 转发）：首次进入自动开聊
    render() {
      const box = mount();
      if (!box) return;
      box.hidden = false;
      const plan = $("condoptKickoffPlan");
      if (plan && state.plan) plan.hidden = false;
      // 只开聊一次。**不**用 bootstrap 的在线状态预判离线：POST /api/kickoff/chat 自己会
      // 用 404/405/网络失败告诉我们端点是否存在（bootstrap 还没回来时预判会误报离线）。
      if (!state.messages.length && !state.started) {
        state.started = true;
        start();
      } else {
        render();
      }
    },
    hide() {
      const box = mount();
      if (box) box.hidden = true;
      const plan = $("condoptKickoffPlan");
      if (plan) plan.hidden = true;
    },
    state,
  };

  // defer 脚本按顺序执行：condopt.js 的 boot() 第一次 syncScenario() 时本文件还没定义。
  // 所以这里加载完成后主动请它再同步一次，保证 01 首屏就是聊天（即使 bootstrap 失败也是）。
  if (window.CONDOPT && typeof window.CONDOPT.resync === "function") {
    window.CONDOPT.resync();
  } else if (document.readyState !== "loading") {
    const mountBox = mount();
    if (mountBox && !mountBox.hidden) window.CONDOPT_KICKOFF.render();
  }
})();
