/* ============================================================
   EduKit · app.js
   ------------------------------------------------------------
   默认离线 mock，双击 index.html 就能演示。
   接自己的后端：只替换下面的 EduAPI.run 一个函数（见 README）。
   事件：{type:"meta"|"stage"|"delta"|"notice"|"error"|"done"}
   ============================================================ */

(() => {
  "use strict";

  const DATA = window.EDU_DATA;
  const $ = (id) => document.getElementById(id);
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));

  // ------------------------------------------------------------------ state
  const state = {
    scenarioKey: DATA.scenarios[0].key,
    files: [],
    running: false,
    output: "",
    mode: "mock",
    model: "",
    runId: null,
    elapsed: 0,
    startedAt: 0,
    timerId: null,
    stageStatus: [],
    decision: null,
    saved: false,
    confettiPlayed: false,
  };

  const el = {
    brandName: $("brandName"),
    brandClaim: $("brandClaim"),
    scenarioCount: $("scenarioCount"),
    scenarioList: $("scenarioList"),
    scenarioMeta: $("scenarioMeta"),
    scenarioTitle: $("scenarioTitle"),
    scenarioTagline: $("scenarioTagline"),
    demoLine: $("demoLine"),
    input: $("input"),
    inputLabel: $("inputLabel"),
    charCount: $("charCount"),
    filesHint: $("filesHint"),
    fileInput: $("fileInput"),
    fileChips: $("fileChips"),
    outputTags: $("outputTags"),
    runBtn: $("runBtn"),
    runBtnLabel: $("runBtnLabel"),
    runNote: $("runNote"),
    sampleBtn: $("sampleBtn"),
    clearBtn: $("clearBtn"),
    modeBadge: $("modeBadge"),
    modeText: $("modeText"),
    modePill: $("modePill"),
    timer: $("timer"),
    stages: $("stages"),
    stream: $("stream"),
    empty: $("empty"),
    md: $("md"),
    resultMeta: $("resultMeta"),
    review: $("review"),
    reviewHint: $("reviewHint"),
    reviewStatus: $("reviewStatus"),
    scoreBox: $("scoreBox"),
    scoreLabel: $("scoreLabel"),
    aiScore: $("aiScore"),
    scoreMax: $("scoreMax"),
    finalScore: $("finalScore"),
    decisions: $("decisions"),
    quickNotes: $("quickNotes"),
    reviewNote: $("reviewNote"),
    saveReview: $("saveReview"),
    reviewSaved: $("reviewSaved"),
    savedStamp: $("savedStamp"),
    copyBtn: $("copyBtn"),
    downloadBtn: $("downloadBtn"),
    printBtn: $("printBtn"),
    themeBtn: $("themeBtn"),
    presentBtn: $("presentBtn"),
    helpBtn: $("helpBtn"),
    helpDialog: $("helpDialog"),
    toast: $("toast"),
    toastText: $("toastText"),
    exitPresent: $("exitPresent"),
    runtimeText: $("runtimeText"),
  };

  const current = () => DATA.scenarios.find((item) => item.key === state.scenarioKey) || DATA.scenarios[0];
  const byKey = (key) => DATA.scenarios.find((item) => item.key === key);

  // ------------------------------------------------------------------ api
  /**
   * 默认实现：离线 mock。
   * 接真实后端时替换 run(payload, emit)：
   *   const res = await fetch("/api/run", { method: "POST", ... });
   *   for await (const evt of EduKit.parseSSE(res)) emit(evt);
   */
  const EduAPI = window.EduKit = {
    version: "1.0.0",
    scenarios: DATA.scenarios,
    EduAPI: {
      async run(payload, emit) {
        return mockRun(payload, emit);
      },
    },
    parseSSE,
    mockRun,
    // [condopt hook #9] 只做「导出上游已有的渲染入口」：condopt.js 在 /api/bootstrap
    // 成功后需要立刻重画徽章，否则首屏只能等用户跑一次场景才更新。
    // 不新增任何渲染逻辑，也没有 CONDOPT 时用不到它。
    renderMode,
    get state() { return state; },
  };

  async function mockRun(payload, emit) {
    const scenario = byKey(payload.scenario) || current();
    const runId = makeRunId();
    const started = performance.now();
    const stages = scenario.stages;
    const total = scenario.mock.length;

    emit({ type: "meta", run_id: runId, mode: "mock", model: "offline-mock" });
    emit({ type: "stage", index: 0, label: stages[0], status: "running" });
    await sleep(220);

    let stageIndex = 0;
    const chunks = chunkText(scenario.mock, 3);
    for (let i = 0; i < chunks.length; i += 1) {
      const text = chunks[i];
      emit({ type: "delta", text });

      const consumed = Math.min((i + 1) * 3, total);
      if (stageIndex < stages.length - 1 && consumed >= (total * (stageIndex + 1)) / stages.length) {
        emit({ type: "stage", index: stageIndex, status: "done" });
        stageIndex += 1;
        emit({ type: "stage", index: stageIndex, status: "running" });
      }
      await sleep(10);
    }

    for (let index = 0; index < stages.length; index += 1) {
      emit({ type: "stage", index, status: "done" });
    }
    emit({
      type: "done",
      run_id: runId,
      elapsed_ms: Math.round(performance.now() - started),
      mode: "mock",
    });
  }

  function chunkText(text, size) {
    const chunks = [];
    for (let index = 0; index < text.length; index += size) chunks.push(text.slice(index, index + size));
    return chunks;
  }

  async function* parseSSE(response) {
    if (!response.body) throw new Error("当前浏览器不支持流式响应");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut = buffer.indexOf("\n\n");
      while (cut >= 0) {
        const block = buffer.slice(0, cut).trim();
        buffer = buffer.slice(cut + 2);
        const event = parseSSEBlock(block);
        if (event) yield event;
        cut = buffer.indexOf("\n\n");
      }
    }
    buffer += decoder.decode();
    for (const block of buffer.split("\n\n")) {
      const event = parseSSEBlock(block.trim());
      if (event) yield event;
    }
  }

  function parseSSEBlock(block) {
    if (!block) return null;
    let type = "message";
    const data = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith("event:")) type = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (!data.length) return null;
    const raw = data.join("\n");
    let payload;
    try {
      payload = JSON.parse(raw);
    } catch (error) {
      payload = { text: raw };
    }
    const body = payload && typeof payload === "object" && !Array.isArray(payload) ? payload : { text: String(payload) };
    return { type, ...body };
  }

  // ------------------------------------------------------------------ boot
  function init() {
    el.brandName.textContent = DATA.project.name;
    el.brandClaim.textContent = DATA.project.claim;
    el.scenarioCount.textContent = String(DATA.scenarios.length).padStart(2, "0");
    document.title = DATA.project.name;

    applyTheme(readStore("edukit.theme") || "light", false);
    renderScenarioList();
    bindEvents();
    selectScenario(state.scenarioKey, { quiet: true });
    renderMode();

    const params = new URLSearchParams(location.search);
    const themeParam = params.get("theme");
    if (themeParam === "dark" || themeParam === "light") applyTheme(themeParam, false);
    const demo = params.get("demo");
    const present = params.get("present");
    if (present === "1" || demo === "present") setPresent(true);
    if (demo && demo !== "0" && demo !== "present") {
      const target = byKey(demo);
      if (target) selectScenario(target.key, { quiet: true });
    }
    if (demo && demo !== "0") {
      setTimeout(() => run(), 520);
    }

    refreshIcons();
    console.info("[EduKit] ready · 替换 EduKit.EduAPI.run 即可接入自己的后端");
  }

  // ------------------------------------------------------------------ render
  function renderScenarioList() {
    el.scenarioList.innerHTML = DATA.scenarios.map((scenario) => `
      <button class="scenario-card ${scenario.key === state.scenarioKey ? "is-active" : ""}"
              data-key="${esc(scenario.key)}" type="button">
        <span class="scenario-no">${esc(scenario.no)}</span>
        <span class="scenario-icon"><i data-lucide="${esc(scenario.icon)}"></i></span>
        <span class="scenario-copy">
          <strong>${esc(scenario.title)}</strong>
          <small>${esc(scenario.short || shortLine(scenario.tagline))}</small>
        </span>
        <span class="scenario-arrow">→</span>
      </button>
    `).join("");
    refreshIcons();
  }

  function shortLine(text) {
    return String(text || "").split("→")[0].trim();
  }

  function selectScenario(key, options = {}) {
    if (state.running) {
      toast("先等这次处理结束，再切换场景");
      return;
    }
    const scenario = byKey(key);
    if (!scenario) return;

    state.scenarioKey = key;
    state.files = [];
    state.decision = null;
    el.fileInput.value = "";
    renderFiles();
    renderScenarioList();

    el.scenarioMeta.textContent = `${scenario.no} / ${scenario.title}`;
    el.scenarioTitle.textContent = scenario.title;
    el.scenarioTagline.textContent = scenario.tagline;
    el.inputLabel.textContent = scenario.inputLabel;
    el.input.placeholder = scenario.placeholder;
    el.fileInput.accept = scenario.accept;
    el.filesHint.textContent = scenario.filesHint;
    el.demoLine.textContent = scenario.demoLine;
    el.input.value = scenario.example || "";
    updateCharCount();

    el.outputTags.innerHTML = `<span class="kicker">本次产出</span>` + (scenario.outputs || ["结构化输出", "可复核记录"])
      .map((tag) => `<span class="out-tag">${esc(tag)}</span>`).join("");
    el.runBtnLabel.textContent = "开始处理";
    resetOutput();
    if (!options.quiet) el.input.focus({ preventScroll: true });
    el.stream.scrollTop = 0;
  }

  function resetOutput() {
    state.output = "";
    state.runId = null;
    state.elapsed = 0;
    state.saved = false;
    state.decision = null;
    state.confettiPlayed = false;
    state.stageStatus = current().stages.map(() => "pending");

    renderStages();
    el.md.hidden = true;
    el.md.innerHTML = "";
    el.md.classList.remove("is-streaming");
    el.empty.hidden = false;
    el.resultMeta.hidden = true;
    el.resultMeta.innerHTML = "";
    el.review.hidden = true;
    el.timer.textContent = "";
    el.copyBtn.disabled = true;
    el.downloadBtn.disabled = true;
    el.printBtn.disabled = true;
    el.finalScore.value = "";
    el.reviewNote.value = "";
    el.reviewStatus.textContent = "待复核";
    el.reviewStatus.classList.remove("is-done");
    el.reviewSaved.textContent = "记录只保存在本机浏览器";
    el.savedStamp.hidden = true;
    el.saveReview.disabled = false;
    setSaveLabel("保存复核记录");
  }

  function renderStages() {
    const stages = current().stages;
    el.stages.innerHTML = stages.map((label, index) => {
      const status = state.stageStatus[index] || "pending";
      const number = status === "done" ? "✓" : String(index + 1).padStart(2, "0");
      return `
        <li class="stage is-${status}">
          <span class="stage-no">${number}</span>
          <span class="stage-label">${esc(label)}</span>
        </li>`;
    }).join("");
  }

  function renderMode() {
    // [condopt hook #1] 契约 §4 的 meta.mode 是 "live"，上游只认 "api"；
    // 不改会让“后端真的在线”仍显示离线演示。两种都当真实后端，逻辑其余不变。
    const live = state.mode === "api" || state.mode === "live";
    // [condopt hook #8] 还没跑过场景（没有 SSE meta 帧）时，允许 condopt.js 按
    // “后端是否已连接”给徽章文案：bootstrap/runs 已通 = 后端已连接，真正回退 mock 才写离线演示。
    // 返回 null / 未提供钩子时，逐字沿用上游的「离线演示 / 离线 mock」文案。
    const badge = !live && window.CONDOPT && typeof window.CONDOPT.modeBadge === "function"
      ? window.CONDOPT.modeBadge()
      : null;
    el.modeBadge.classList.toggle("is-live", live);
    el.modeBadge.classList.toggle("is-connected", Boolean(badge && badge.connected));
    el.modeText.textContent = live ? `真实模型 · ${state.model || "api"}` : (badge ? badge.text : "离线演示");
    el.modePill.textContent = live ? "真实模型" : (badge ? badge.pill : "离线 mock");
    el.modePill.className = live ? "badge badge-teal" : "badge";
    el.runNote.innerHTML = live
      ? "<strong>真实模型</strong> · 失败自动回退"
      : (badge && badge.note ? badge.note : "<strong>离线 mock</strong> · 可直接演示");
  }

  function renderFiles() {
    el.fileChips.innerHTML = state.files.map((file, index) => {
      const thumb = file.mime && file.mime.startsWith("image/") && file.data
        ? `<img class="file-thumb" src="${esc(file.data)}" alt="">`
        : "";
      return `<span class="chip">${thumb}<span class="chip-name">${esc(file.name)}</span><button class="x" type="button" data-remove="${index}" aria-label="移除附件">×</button></span>`;
    }).join("");
  }

  // ------------------------------------------------------------------ markdown
  let renderTimer = null;
  let lastRender = 0;

  function scheduleRender() {
    if (renderTimer) return;
    const wait = Math.max(0, 90 - (performance.now() - lastRender));
    renderTimer = setTimeout(() => {
      renderTimer = null;
      lastRender = performance.now();
      renderMarkdown();
    }, wait);
  }

  function renderMarkdown(force = false) {
    if (!state.output) {
      el.md.hidden = true;
      return;
    }
    el.empty.hidden = true;
    el.md.hidden = false;

    let html;
    try {
      html = marked.parse(state.output, { gfm: true, breaks: true });
    } catch (error) {
      html = esc(state.output).replace(/\n/g, "<br>");
    }
    try {
      el.md.innerHTML = DOMPurify.sanitize(html);
    } catch (error) {
      el.md.innerHTML = esc(state.output);
    }

    if (window.katex && window.renderMathInElement) {
      try {
        window.renderMathInElement(el.md, {
          delimiters: [
            { left: "$$", right: "$$", display: true },
            { left: "$", right: "$", display: false },
            { left: "\\[", right: "\\]", display: true },
            { left: "\\(", right: "\\)", display: false },
          ],
          throwOnError: false,
          strict: false,
          ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code"],
        });
      } catch (error) {
        /* 公式不阻塞演示 */
      }
    }
    if (force) scrollToBottom(true);
  }

  function scrollToBottom(force = false) {
    const stream = el.stream;
    const nearBottom = stream.scrollTop + stream.clientHeight >= stream.scrollHeight - 120;
    if (force || nearBottom) {
      requestAnimationFrame(() => { stream.scrollTop = stream.scrollHeight; });
    }
  }

  // ------------------------------------------------------------------ run
  async function run() {
    if (state.running) return;
    // [condopt hook #10] 「导入数据」这类场景没有可用的 SSE 场景，它由 condopt 层用
    // scan→map→apply 三个 JSON 端点自管（CONTRACT §9.6）。声明接管就直接返回，
    // 不进入上游的「必须有文本/附件 → 走 SSE」流程。钩子缺席时行为与上游一致。
    if (window.CONDOPT && typeof window.CONDOPT.handlesRun === "function"
      && window.CONDOPT.handlesRun(state.scenarioKey)) {
      return window.CONDOPT.runScenario(state.scenarioKey);
    }
    const text = el.input.value.trim();
    // [condopt hook #3] 有结构化载荷的场景（sample/feedback/delivery/board）允许不带自由文本：
    // 数据在 payload 里而不是输入框里。app.js 只问“有没有载荷”，不解析它的内容。
    const hasStructured = Boolean(window.CONDOPT && typeof window.CONDOPT.hasPayload === "function"
      && window.CONDOPT.hasPayload(state.scenarioKey));
    if (!text && !state.files.length && !hasStructured) {
      toast("先输入内容，或添加一份附件");
      el.input.focus();
      return;
    }

    resetOutput();
    state.running = true;
    state.mode = "mock";
    state.model = "";
    state.startedAt = performance.now();
    state.output = "";
    state.stageStatus = current().stages.map(() => "pending");
    el.md.classList.add("is-streaming");
    el.empty.hidden = true;
    renderStages();
    renderMode();

    el.runBtn.disabled = true;
    el.runBtn.classList.add("is-running");
    el.runBtnLabel.textContent = "处理中…";
    startTimer();

    const payload = {
      scenario: state.scenarioKey,
      text,
      files: state.files.map((file) => ({
        name: file.name,
        mime: file.mime,
        // 与 edu-demo / OpenAI vision 常见格式一致：纯 base64，不带 data: 前缀
        data: file.data.includes(",") ? file.data.split(",")[1] : file.data,
      })),
    };

    // [condopt hook #4] 契约 §4：把结构化载荷透传给 EduAPI.run（后端从 payload.payload 取
    // answers / run / n / observations …）。这里只搬运，不改任何渲染逻辑；
    // 没有 condopt.js 时 payload 形状与上游逐字一致。
    if (window.CONDOPT && typeof window.CONDOPT.buildPayload === "function") {
      const extra = window.CONDOPT.buildPayload({
        scenario: state.scenarioKey,
        text,
        files: payload.files,
      });
      if (extra) payload.payload = extra;
    }

    try {
      await EduAPI.EduAPI.run(payload, handleEvent);
    } catch (error) {
      toast("运行失败：" + (error && error.message ? error.message : "未知错误"));
    } finally {
      finishRun();
    }
  }

  function handleEvent(event) {
    if (!event || !event.type) return;

    if (event.type === "meta") {
      state.runId = event.run_id || state.runId;
      state.mode = event.mode || state.mode;
      state.model = event.model || state.model;
      renderMode();
      return;
    }

    if (event.type === "stage") {
      const index = Number(event.index);
      if (!Number.isFinite(index)) return;
      while (state.stageStatus.length <= index) state.stageStatus.push("pending");
      state.stageStatus[index] = event.status || "running";
      renderStages();
      return;
    }

    if (event.type === "delta") {
      const text = event.text || "";
      if (!text) return;
      if (!state.output) {
        el.empty.hidden = true;
        el.md.hidden = false;
      }
      state.output += text;
      el.md.classList.add("is-streaming");
      scheduleRender();
      return;
    }

    if (event.type === "notice") {
      toast(event.message || "提示");
      return;
    }

    if (event.type === "error") {
      toast(event.message || "模型调用失败");
      if (event.hint) console.warn("[EduKit]", event.hint);
      return;
    }

    if (event.type === "done") {
      state.runId = event.run_id || state.runId;
      if (event.elapsed_ms) state.elapsed = event.elapsed_ms;
      if (event.mode) {
        state.mode = event.mode;
        renderMode();
      }
    }
  }

  function finishRun() {
    state.running = false;
    stopTimer();
    el.runBtn.disabled = false;
    el.runBtn.classList.remove("is-running");
    el.runBtnLabel.textContent = state.output ? "再次处理" : "开始处理";
    el.md.classList.remove("is-streaming");

    if (!state.runId) state.runId = makeRunId();
    if (!state.output) {
      el.empty.hidden = false;
      el.review.hidden = true;
      toast("没有收到输出，请检查输入或后端");
      return;
    }

    if (renderTimer) {
      clearTimeout(renderTimer);
      renderTimer = null;
    }
    renderMarkdown();
    showReview();
    el.copyBtn.disabled = false;
    el.downloadBtn.disabled = false;
    el.printBtn.disabled = false;
    renderResultMeta();
    el.stream.scrollTop = 0;
    refreshIcons();
  }

  function startTimer() {
    stopTimer();
    state.startedAt = performance.now();
    el.timer.textContent = "0.0 s";
    state.timerId = setInterval(() => {
      const seconds = (performance.now() - state.startedAt) / 1000;
      el.timer.textContent = seconds.toFixed(1) + " s";
    }, 100);
  }

  function stopTimer() {
    if (state.timerId) clearInterval(state.timerId);
    state.timerId = null;
    if (state.startedAt) {
      state.elapsed = Math.round(performance.now() - state.startedAt);
      el.timer.textContent = (state.elapsed / 1000).toFixed(1) + " s";
      state.startedAt = 0;
    }
  }

  function renderResultMeta() {
    const bits = [
      `run ${state.runId}`,
      `${state.output.length.toLocaleString("zh-CN")} 字`,
      `${(state.elapsed / 1000).toFixed(1)} s`,
      // [condopt hook #2] 同上：meta.mode="live" 也要标成真实后端，不能标成离线 mock。
      state.mode === "api" || state.mode === "live" ? "真实后端" : "离线 mock",
    ];
    if (state.saved) bits.push("已复核");
    el.resultMeta.innerHTML = bits.map((bit) => `<span>${esc(bit)}</span>`).join("");
    el.resultMeta.hidden = false;
  }

  // ------------------------------------------------------------------ review
  function showReview() {
    const scenario = current();
    const config = scenario.review || { type: "decision" };
    const isScore = config.type === "score";

    el.review.hidden = false;
    el.scoreBox.classList.toggle("is-hidden", !isScore);
    if (isScore) {
      el.scoreLabel.textContent = config.label || "AI 建议分";
      el.aiScore.textContent = String(config.score ?? "");
      el.scoreMax.textContent = "/ " + (config.max ?? 100);
      el.finalScore.max = String(config.max ?? 100);
      el.finalScore.placeholder = String(config.score ?? "");
    }

    const options = config.options || (isScore
      ? ["采纳建议分", "调整分数", "退回重做"]
      : ["采纳建议", "调整后使用", "转人工"]);
    el.decisions.innerHTML = options.map((option) => `
      <button class="decision" type="button" data-value="${esc(option)}">${esc(option)}</button>
    `).join("");

    const quick = config.notes && config.notes.length
      // [condopt hook #7] 场景可用 review.notes 自带拍板理由；没给就沿用上游 QUICK_NOTES。
      ? config.notes
      : (QUICK_NOTES[scenario.key] || QUICK_NOTES.default);
    el.quickNotes.innerHTML = quick.map((note) => `<button type="button" data-note="${esc(note)}">${esc(note)}</button>`).join("");

    el.reviewHint.textContent = config.hint
      // [condopt hook #5] 允许场景自带 review.hint 说明拍板口径（condopt 五场景各不同）；
      // 没给 hint 时与上游逐字一致。
      || (isScore ? "AI 只给建议分，最终分由你确认" : "AI 只给建议，发送前由人确认");
    el.reviewStatus.textContent = "待复核";
    el.reviewStatus.classList.remove("is-done");
    el.savedStamp.hidden = true;
    el.saveReview.disabled = false;
    setSaveLabel("保存复核记录");
    state.decision = null;
  }

  function setSaveLabel(label) {
    el.saveReview.innerHTML = `<i data-lucide="check"></i>${esc(label)}`;
    refreshIcons();
  }

  function saveReview() {
    if (!state.decision) {
      toast("先选择一个处理意见");
      return;
    }
    if (!state.runId) {
      toast("还没有可保存的运行记录");
      return;
    }
    const scenario = current();
    const record = {
      run_id: state.runId,
      scenario: scenario.key,
      title: scenario.title,
      decision: state.decision,
      ai_score: scenario.review && scenario.review.type === "score" ? scenario.review.score : null,
      final_score: el.finalScore.value === "" ? null : Number(el.finalScore.value),
      note: el.reviewNote.value.trim(),
      elapsed_ms: state.elapsed,
      mode: state.mode,
      reviewed_at: new Date().toISOString(),
    };

    let stored = false;
    try {
      const list = JSON.parse(localStorage.getItem("edukit.reviews") || "[]");
      list.unshift(record);
      localStorage.setItem("edukit.reviews", JSON.stringify(list.slice(0, 30)));
      stored = true;
    } catch (error) {
      /* 隐私模式或 file:// 限制时，至少保证本次演示可见 */
    }

    state.saved = true;
    el.reviewStatus.textContent = "已复核";
    el.reviewStatus.classList.add("is-done");
    el.savedStamp.hidden = false;
    el.reviewSaved.textContent = stored ? "已写入本机 · " + state.runId : "本次会话已记录 · " + state.runId;
    el.saveReview.disabled = true;
    setSaveLabel("已保存");
    renderResultMeta();

    if (!state.confettiPlayed && typeof window.confetti === "function") {
      state.confettiPlayed = true;
      window.confetti({
        particleCount: 55,
        spread: 58,
        startVelocity: 28,
        gravity: 0.9,
        scalar: 0.7,
        ticks: 120,
        origin: { x: 0.7, y: 0.85 },
        colors: ["#0f6b62", "#c2410c", "#d9a441", "#1b2430"],
      });
    }
    // [condopt hook #6] 复核 = “人工拍板 + 留痕”：交给 condopt.js 决定是否
    // POST /api/action {override, algorithm, reason} 落 decisions.jsonl。
    // 钩子抛错不影响本地留痕（上游的 localStorage 记录已经写完）。
    if (window.CONDOPT && typeof window.CONDOPT.onReview === "function") {
      try { window.CONDOPT.onReview(record); } catch (error) { console.warn("[condopt] onReview 失败", error); }
    }
    toast("复核记录已保存 · " + state.runId);
  }

  // ------------------------------------------------------------------ files
  async function addFiles(event) {
    const picked = Array.from(event.target.files || []);
    event.target.value = "";
    for (const file of picked) {
      if (state.files.length >= 3) {
        toast("最多 3 个附件");
        break;
      }
      if (file.size > 8 * 1024 * 1024) {
        toast(file.name + " 超过 8 MB，未添加");
        continue;
      }
      try {
        const data = await readFile(file);
        state.files.push({ name: file.name, mime: file.type || "", data, size: file.size });
      } catch (error) {
        toast(file.name + " 读取失败");
      }
    }
    renderFiles();
  }

  function readFile(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(reader.error || new Error("读取失败"));
      reader.readAsDataURL(file);
    });
  }

  // ------------------------------------------------------------------ tools
  function updateCharCount() {
    const count = el.input.value.length;
    el.charCount.textContent = count ? count.toLocaleString("zh-CN") + " 字" : "0 字";
  }

  function loadExample() {
    el.input.value = current().example || "";
    updateCharCount();
    resetOutput();
    el.input.focus({ preventScroll: true });
  }

  function clearAll() {
    if (state.running) return;
    el.input.value = "";
    state.files = [];
    el.fileInput.value = "";
    renderFiles();
    updateCharCount();
    resetOutput();
    el.input.focus({ preventScroll: true });
  }

  async function copyOutput() {
    if (!state.output) return;
    try {
      await navigator.clipboard.writeText(state.output);
    } catch (error) {
      const area = document.createElement("textarea");
      area.value = state.output;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try { document.execCommand("copy"); } catch (err) { /* ignore */ }
      area.remove();
    }
    toast("已复制 Markdown");
  }

  function downloadOutput() {
    if (!state.output) return;
    const blob = new Blob([state.output], { type: "text/markdown;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `edukit-${state.scenarioKey}-${state.runId || "draft"}.md`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    toast("已导出 Markdown");
  }

  function makeRunId() {
    const now = new Date();
    const pad = (value) => String(value).padStart(2, "0");
    const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`;
    return `${stamp}-${Math.random().toString(16).slice(2, 6)}`;
  }

  let toastTimer = null;
  function toast(message) {
    el.toastText.textContent = message;
    el.toast.classList.add("is-on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.toast.classList.remove("is-on"), 2800);
  }

  function readStore(key) {
    try { return localStorage.getItem(key); } catch (error) { return null; }
  }

  function writeStore(key, value) {
    try { localStorage.setItem(key, value); } catch (error) { /* ignore */ }
  }

  function applyTheme(theme, persist = true) {
    const next = theme === "dark" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    if (persist) writeStore("edukit.theme", next);
    el.themeBtn.innerHTML = `<i data-lucide="${next === "dark" ? "sun" : "moon"}"></i>`;
    el.themeBtn.setAttribute("aria-label", next === "dark" ? "切换到浅色" : "切换到深色");
    refreshIcons();
  }

  function setPresent(on) {
    const next = Boolean(on);
    document.body.classList.toggle("presenting", next);
    if (next) {
      el.presentBtn.innerHTML = `<i data-lucide="minimize-2"></i><span>退出演示</span>`;
      el.stream.scrollTop = 0;
    } else {
      el.presentBtn.innerHTML = `<i data-lucide="presentation"></i><span>演示视图</span>`;
    }
    refreshIcons();
  }

  function refreshIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      try { window.lucide.createIcons(); } catch (error) { /* ignore */ }
    }
  }

  // ------------------------------------------------------------------ events
  function bindEvents() {
    el.input.addEventListener("input", updateCharCount);

    el.sampleBtn.addEventListener("click", loadExample);
    el.clearBtn.addEventListener("click", clearAll);
    el.runBtn.addEventListener("click", run);
    el.fileInput.addEventListener("change", addFiles);

    el.fileChips.addEventListener("click", (event) => {
      const button = event.target.closest("[data-remove]");
      if (!button) return;
      state.files.splice(Number(button.dataset.remove), 1);
      renderFiles();
    });

    el.scenarioList.addEventListener("click", (event) => {
      const button = event.target.closest("[data-key]");
      if (button) selectScenario(button.dataset.key);
    });

    el.decisions.addEventListener("click", (event) => {
      const button = event.target.closest("[data-value]");
      if (!button) return;
      el.decisions.querySelectorAll(".decision").forEach((node) => node.classList.remove("is-active"));
      button.classList.add("is-active");
      state.decision = button.dataset.value;
      el.reviewStatus.textContent = "已选择 · 待保存";
    });

    el.quickNotes.addEventListener("click", (event) => {
      const button = event.target.closest("[data-note]");
      if (!button) return;
      const note = button.dataset.note;
      el.reviewNote.value = el.reviewNote.value.trim()
        ? el.reviewNote.value.trim().replace(/[；;]\s*$/, "") + "；" + note
        : note;
      el.reviewNote.focus();
    });

    el.saveReview.addEventListener("click", saveReview);

    el.copyBtn.addEventListener("click", copyOutput);
    el.downloadBtn.addEventListener("click", downloadOutput);
    el.printBtn.addEventListener("click", () => window.print());

    el.themeBtn.addEventListener("click", () => {
      applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    });
    el.presentBtn.addEventListener("click", () => {
      setPresent(!document.body.classList.contains("presenting"));
    });
    el.exitPresent.addEventListener("click", () => setPresent(false));

    el.helpBtn.addEventListener("click", () => {
      if (typeof el.helpDialog.showModal === "function") el.helpDialog.showModal();
      else el.helpDialog.setAttribute("open", "");
      refreshIcons();
    });
    el.helpDialog.addEventListener("click", (event) => {
      if (event.target === el.helpDialog) el.helpDialog.close();
    });
    el.helpDialog.querySelectorAll("[data-close]").forEach((button) => {
      button.addEventListener("click", () => el.helpDialog.close());
    });

    document.addEventListener("keydown", (event) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) || event.target.isContentEditable;

      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        run();
        return;
      }
      if (event.key === "Escape") {
        if (document.body.classList.contains("presenting")) setPresent(false);
        if (el.helpDialog.open) el.helpDialog.close();
        return;
      }
      if (typing) return;
      if (/^[1-9]$/.test(event.key)) {
        const scenario = DATA.scenarios[Number(event.key) - 1];
        if (scenario) selectScenario(scenario.key);
        return;
      }
      if (event.key === "p" || event.key === "P") setPresent(!document.body.classList.contains("presenting"));
    });
  }

  const QUICK_NOTES = {
    grading: ["已核对证据链", "分数按课堂标准调整", "要求学生重做并检验"],
    "early-warning": ["已联系班主任", "改为课后单独谈话", "暂不通知家长"],
    "study-cards": ["术语已核对", "难度需要下调", "补一张易错卡"],
    tutor: ["语气再温和一些", "不直接给答案", "转给任课教师"],
    default: ["已核对原文", "需要人工跟进", "先小范围试用"],
  };

  init();
})();
