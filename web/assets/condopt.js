/* ============================================================
   condopt 工作台 · condopt.js
   ------------------------------------------------------------
   这是 condopt 专属的适配层，加载顺序必须在 assets/app.js 之后。
   它只做四件事，全部围绕 CONTRACT.md：

     1. 把 EduKit.EduAPI.run 指向 POST /api/run（SSE），
        失败且一个 delta 都没收到时回退 EduKit.mockRun（上游行为，保留）。
     2. 用 GET /api/bootstrap 的 prepare_template 生成「开工表单」——
        字段完全是后端模板的投影，本文件不手写任何领域字段。
     3. study 列表 / run 选择：GET /api/runs + GET /api/study?id=。
     4. 闭环动作条与人工拍板：POST /api/action（init/prepare/ask/tell/
        delivery/render/step/recommend/report/override）。

   上游 app.js 只留了 6 个钩子（见 ADAPTATION.md），本文件通过
   window.CONDOPT.hasPayload / buildPayload / onReview 被调用。

   无后端时：不白屏。所有 condopt 区块隐藏或降级为提示，
   EduAPI.run 回退离线演示，mock 文本自带「离线演示」标注。
   ============================================================ */

(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));

  /* 只用相对同源路径：不引 CDN，不发外网请求（契约 §5）。 */
  const ENDPOINTS = {
    bootstrap: "/api/bootstrap",
    runs: "/api/runs",
    study: "/api/study",
    action: "/api/action",
    run: "/api/run",
  };

  // 「开工表」字段名的**显示标签**（P2：面向用户的语言）。
  // 字段本身仍然 100% 由 /api/bootstrap 的 prepare_template 决定，这里只翻译标签；
  // 模板里出现未知键时原样显示该键——不隐瞒、也不编造不存在的字段。
  const FIELD_LABELS = {
    objective: "目标",
    variables: "条件空间",
    budget: "预算",
    batch: "批大小",
    noise: "噪声",
    smoothness: "光滑性",
    domain: "领域",
    prior: "先验",
    notes: "备注",
    spec: "规格",
    constraints: "约束",
    multi_fidelity: "多保真",
    fidelity: "保真度",
    compute: "算力",
    n_objectives: "目标数",
    n_constraints: "约束数",
    n_observations: "已有观测数",
    transfer_from: "历史数据来源",
    spec_calibrate_from: "规格校准来源",
  };

  const state = {
    online: false,
    boot: null,
    algorithms: [],
    template: null,
    varKeys: [],
    kindOptions: [],
    runs: [],
    currentRun: null,
    scenarioKey: null,
    syncing: false,
    busy: false,
  };

  /* ---------------------------------------------------------- utils */

  function toast(message) {
    // 复用 app.js 的 toast 元素；app.js 没有导出 toast()，这里只写 DOM。
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

  function getPath(object, path) {
    return path.split(".").reduce((node, key) => (node == null ? undefined : node[key]), object);
  }

  function setPath(object, path, value) {
    const keys = path.split(".");
    let node = object;
    for (let index = 0; index < keys.length - 1; index += 1) {
      const key = /^\d+$/.test(keys[index]) ? Number(keys[index]) : keys[index];
      if (node[key] == null || typeof node[key] !== "object") node[key] = /^\d+$/.test(keys[index + 1]) ? [] : {};
      node = node[key];
    }
    const last = keys[keys.length - 1];
    node[/^\d+$/.test(last) ? Number(last) : last] = value;
  }

  function parseJsonObject(text) {
    const raw = String(text || "").trim();
    if (!raw) return null;
    try {
      const value = JSON.parse(raw);
      return value && typeof value === "object" && !Array.isArray(value) ? value : null;
    } catch (error) {
      return null;
    }
  }

  function looksLikeDelimited(text) {
    const raw = String(text || "").trim();
    if (!raw || raw.startsWith("{") || raw.startsWith("[")) return false;
    const firstLine = raw.split(/\r?\n/)[0] || "";
    return firstLine.includes(",") || firstLine.includes("\t") || firstLine.includes(";");
  }

  function scenarioByKey(key) {
    const list = (window.EDU_DATA && window.EDU_DATA.scenarios) || [];
    return list.find((item) => item.key === key) || null;
  }

  /* ---------------------------------------------------------- HTTP */

  async function getJson(url) {
    const response = await fetch(url, { method: "GET", headers: { Accept: "application/json" } });
    let data = null;
    try { data = await response.json(); } catch (error) { data = null; }
    if (!response.ok || !data || data.ok === false) {
      const message = (data && (data.message || data.error)) || ("HTTP " + response.status);
      throw new Error(message);
    }
    return data;
  }

  async function postAction(action, run, payload) {
    const body = { action, run: run == null ? null : run, payload: payload || {} };
    const response = await fetch(ENDPOINTS.action, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let data = null;
    try { data = await response.json(); } catch (error) { data = null; }
    if (!response.ok || !data || data.ok === false) {
      const code = data && data.error ? data.error + " · " : "";
      const hint = data && data.hint ? "（" + data.hint + "）" : "";
      const error = new Error(code + ((data && data.message) || ("HTTP " + response.status)) + hint);
      // 保留结构化字段：调用方（如 kickoff.js 判断 init 是否拒收 params）不该去正则解析错误文本。
      error.code = (data && data.error) || ("HTTP_" + response.status);
      error.hint = (data && data.hint) || "";
      error.status = response.status;
      error.raw = data;
      throw error;
    }
    return data;
  }

  /* ---------------------------------------------------------- EduAPI.run → /api/run（SSE） */

  function installAdapter() {
    if (!window.EduKit || !window.EduKit.EduAPI) {
      console.warn("[condopt] 未找到 EduKit.EduAPI，请确认 assets/app.js 先于 condopt.js 加载");
      return;
    }

    EduKit.EduAPI.run = async function condoptRun(payload, emit) {
      // 结构化载荷缺失（answers.json 不是合法 JSON / 没选 study）→ 直接走离线演示，
      // 不发一次注定被拒的请求，也不假装后端在线。
      if (!payload || !payload.payload) {
        emit({ type: "notice", message: "未生成结构化载荷（检查 answers.json 或先选 study），已走离线演示" });
        setOnline(false);
        return EduKit.mockRun(payload, emit);
      }

      let received = false;
      try {
        const response = await fetch(ENDPOINTS.run, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          throw new Error(detail.message || detail.error || ("HTTP " + response.status));
        }
        if (!response.body) throw new Error("当前浏览器不支持流式响应");

        for await (const event of EduKit.parseSSE(response)) {
          received = true;
          setOnline(true);
          emit(event);
          if (event && event.type === "done") {
            refreshRuns({ quiet: true });
            refreshStudy({ quiet: true });
          }
        }
        if (!received) throw new Error("后端没有返回任何 SSE 帧");
      } catch (error) {
        // 与上游 adapter.example.js 同一策略：一个 delta 都没收到才回退，
        // 已收到部分输出则如实抛错（不掩盖）。
        if (!received) {
          emit({ type: "notice", message: "后端不可用，已回退离线演示：" + error.message });
          setOnline(false);
          return EduKit.mockRun(payload, emit);
        }
        throw error;
      }
    };
  }

  /* ---------------------------------------------------------- 结构化载荷（被 app.js 钩子调用） */

  // 返回的是 **CONTRACT §4 的 `payload`**（POST /api/run 的请求体里 payload 字段），
  // 所以它带着 `run`（§4 请求示例：{"payload":{"answers":{},"run":"demo","n":4}}）。
  // 同一份数据要发去 POST /api/action 时，`run` 必须提到请求顶层、payload 只留该 action 的字段
  // —— 见 runAction() 里的注释。两处不能混用，否则后端按契约 §3 判「未知字段」。
  function buildPayload(context) {
    const scenario = scenarioByKey(context.scenario);
    if (!scenario || !scenario.action) return null;
    const action = scenario.action;
    const text = context.text || "";

    // import（06）没有 §4 的 SSE 通道：它由 app.js 钩子 #10 交给 assets/import.js 自管。
    // 这里返回 null 表明「本层不产载荷」，避免把 path 当成 run 发出去。
    if (action === "import") return null;

    if (action === "prepare") {
      const answers = parseJsonObject(text);
      if (!answers) {
        toast("answers.json 不是合法 JSON，无法生成结构化载荷");
        return null;
      }
      return { answers };
    }

    const loose = parseJsonObject(text) || {};
    // 左侧 study 下拉框是 run 的权威选择；JSON 里的 run 只在没有选中 study 时兜底
    // （否则示例文本里的占位 id（如 "demo"）会盖掉用户真正选中的 study，导致 404→假回退）。
    const run = String(state.currentRun || loose.run || "").trim();
    if (!run) {
      toast("先建一个 study，或在 JSON 里写 run");
      return null;
    }

    if (action === "ask") {
      const payload = { run };
      if (Number.isFinite(Number(loose.n))) payload.n = Number(loose.n);
      if (typeof loose.plate === "string" && loose.plate) payload.plate = loose.plate;
      return payload;
    }

    if (action === "tell") {
      if (Array.isArray(loose.observations) && loose.observations.length) return { run, observations: loose.observations };
      if (typeof loose.csv === "string" && loose.csv.trim()) return { run, csv: loose.csv };
      if (looksLikeDelimited(text)) return { run, csv: text };
      toast("回填需要 observations 数组或 CSV 文本");
      return null;
    }

    // delivery / board / 其它 needs_run 场景：只要 run
    return { run };
  }

  function hasPayload(key) {
    const scenario = scenarioByKey(key);
    return Boolean(scenario && scenario.action);
  }

  /* ---------------------------------------------------------- 开工表单（模板投影，不手写字段） */

  function buildSchema() {
    const template = state.template;
    if (!template || typeof template !== "object") return null;

    const scalarKeys = Object.keys(template).filter((key) => key !== "objective" && key !== "variables");
    const objectiveKeys = template.objective && typeof template.objective === "object"
      ? Object.keys(template.objective) : [];

    const variables = Array.isArray(template.variables) ? template.variables : [];
    const varKeys = [];
    const kindOptions = [];
    variables.forEach((row) => {
      Object.keys(row || {}).forEach((key) => { if (!varKeys.includes(key)) varKeys.push(key); });
      const kind = row && row.kind;
      if (typeof kind === "string" && kind && !kindOptions.includes(kind)) kindOptions.push(kind);
    });

    state.varKeys = varKeys;
    state.kindOptions = kindOptions;
    return { scalarKeys, objectiveKeys, varKeys, kindOptions, template };
  }

  function controlHtml(path, value, options = {}) {
    const label = options.label || path.split(".").pop();
    const type = options.type || (typeof value);
    const key = esc(path);
    if (type === "boolean") {
      return `<label class="condopt-field condopt-field-bool"><span>${esc(label)}</span>
        <input type="checkbox" data-path="${key}" ${value ? "checked" : ""}></label>`;
    }
    if (type === "number") {
      return `<label class="condopt-field"><span>${esc(label)}</span>
        <input class="input" type="number" step="any" data-path="${key}" value="${value == null ? "" : esc(value)}"></label>`;
    }
    if (type === "choices" || (Array.isArray(value))) {
      const text = Array.isArray(value) ? value.join(", ") : "";
      return `<label class="condopt-field"><span>${esc(label)}</span>
        <input class="input" type="text" data-path="${key}" data-kind="list" value="${esc(text)}"
               placeholder="逗号分隔"></label>`;
    }
    if (type === "enum") {
      const current = String(value == null ? "" : value);
      const list = options.choices || [];
      const items = list.map((item) => `<option value="${esc(item)}" ${item === current ? "selected" : ""}>${esc(item)}</option>`).join("");
      const extra = list.includes(current) ? "" : `<option value="${esc(current)}" selected>${esc(current || "（空）")}</option>`;
      return `<label class="condopt-field"><span>${esc(label)}</span>
        <select class="input" data-path="${key}">${extra}${items}</select></label>`;
    }
    if (typeof value === "string" && value.length > 48) {
      return `<label class="condopt-field condopt-field-wide"><span>${esc(label)}</span>
        <textarea class="input condopt-note" rows="2" data-path="${key}">${esc(value)}</textarea></label>`;
    }
    return `<label class="condopt-field"><span>${esc(label)}</span>
      <input class="input" type="text" data-path="${key}" value="${value == null ? "" : esc(value)}"></label>`;
  }

  function variableRowHtml(row, index) {
    // §11.4：每行只渲染**该行实际拥有的键**（不是所有行的并集）。
    // 并集渲染会给 categorical 行补出 low/high/unit、给 bool 行补出 low/high/... 等幻影控件，
    // 实测真实模板：6 行并集 6 键 → 控件 45 个，而模板叶片字段只有 33 个（一一对应被破坏）。
    // 新加的空白行（row 不存在）仍用并集，让用户能把任意键填出来。
    const keys = (row && typeof row === "object") ? Object.keys(row) : state.varKeys;
    const fields = keys.map((key) => {
      const value = row ? row[key] : undefined;
      const path = "variables." + index + "." + key;
      if (key === "kind" && state.kindOptions.length) {
        return controlHtml(path, value == null ? "" : value, { label: key, type: "enum", choices: state.kindOptions });
      }
      return controlHtml(path, value, { label: key });
    }).join("");
    return `<fieldset class="condopt-var-row" data-row="${index}">
      <legend>#${index + 1}</legend>
      ${fields}
      <button class="btn btn-ghost btn-sm condopt-var-remove" type="button" data-remove-row="${index}">
        <i data-lucide="trash-2"></i>删除
      </button>
    </fieldset>`;
  }

  function renderForm() {
    const form = $("condoptForm");
    const box = $("condoptFormFields");
    if (!form || !box) return;

    const schema = buildSchema();
    if (!schema) {
      form.hidden = true;
      return;
    }
    form.hidden = false;

    const model = state.formModel || {};
    const parts = [];

    if (schema.objectiveKeys.length) {
      parts.push(`<div class="condopt-group"><span class="condopt-group-title">objective</span><div class="condopt-grid">${
        schema.objectiveKeys.map((key) => controlHtml("objective." + key, getPath(model, "objective." + key))).join("")
      }</div></div>`);
    }

    const variables = Array.isArray(model.variables) ? model.variables : [];
    parts.push(`<div class="condopt-group"><span class="condopt-group-title">variables（条件空间）</span>${
      variables.map((row, index) => variableRowHtml(row, index)).join("")
    }</div>`);

    if (schema.scalarKeys.length) {
      parts.push(`<div class="condopt-group"><span class="condopt-group-title">context</span><div class="condopt-grid">${
        schema.scalarKeys.map((key) => controlHtml(key, model[key])).join("")
      }</div></div>`);
    }

    box.innerHTML = parts.join("");
    const hint = $("condoptFormHint");
    if (hint) {
      // P2：面向用户的语言，不暴露 prepare_template 这种内部函数名；
      // 原始键名留在 title 里，排查时 hover 可见。
      hint.textContent = "字段由后端自动生成：" + formFieldSummary();
      hint.title = "prepare_template keys: " + Object.keys(state.template || {}).join(", ");
    }
    refreshIcons();
  }

  function formFieldSummary() {
    const template = state.template;
    if (!template) return "（模板不可用）";
    return Object.keys(template).map((key) => FIELD_LABELS[key] || key).join(" / ");
  }

  function readFormIntoModel() {
    const box = $("condoptFormFields");
    if (!box) return;
    const model = state.formModel || (state.formModel = {});
    box.querySelectorAll("[data-path]").forEach((control) => {
      const path = control.dataset.path;
      const isList = control.dataset.kind === "list";
      let value;
      if (control.type === "checkbox") value = control.checked;
      else if (isList) {
        value = control.value.split(",").map((item) => item.trim()).filter(Boolean);
      } else if (control.type === "number") {
        value = control.value === "" ? null : Number(control.value);
      } else value = control.value;
      setPath(model, path, value);
    });
  }

  function serializeModel(dispatch) {
    const input = $("input");
    if (!input) return;
    input.value = JSON.stringify(prune(state.formModel || {}), null, 2);
    if (dispatch) {
      try { input.dispatchEvent(new Event("input", { bubbles: true })); } catch (error) { /* charCount 不关键 */ }
    }
  }

  // 表单控件留空的字段（null / "" / []）不写进 answers，避免把 "low": null 这种噪声交给后端校验。
  function prune(value) {
    if (Array.isArray(value)) {
      return value.map((item) => prune(item)).filter((item) => !(item == null || item === "" || (Array.isArray(item) && !item.length)));
    }
    if (value && typeof value === "object") {
      const out = {};
      Object.keys(value).forEach((key) => {
        const pruned = prune(value[key]);
        if (pruned == null || pruned === "" || (Array.isArray(pruned) && !pruned.length)) return;
        out[key] = pruned;
      });
      return out;
    }
    return value;
  }

  function resetFormFromTemplate() {
    state.formModel = state.template ? JSON.parse(JSON.stringify(state.template)) : {};
    renderForm();
    serializeModel(true);
    toast("已按后端模板重置表单");
  }

  function addVariableRow() {
    readFormIntoModel();
    const model = state.formModel || (state.formModel = {});
    if (!Array.isArray(model.variables)) model.variables = [];
    const blank = {};
    state.varKeys.forEach((key) => { blank[key] = key === "kind" ? (state.kindOptions[0] || "") : ""; });
    model.variables.push(blank);
    renderForm();
    serializeModel(true);
  }

  function removeVariableRow(index) {
    readFormIntoModel();
    const model = state.formModel || (state.formModel = {});
    if (!Array.isArray(model.variables)) return;
    model.variables.splice(index, 1);
    renderForm();
    serializeModel(true);
  }

  // 用户直接改 JSON → 反向同步表单（解析失败就保留上一次的表单，不清空）
  function syncFormFromJson() {
    const input = $("input");
    if (!input || state.scenarioKey !== "kickoff") return;
    const parsed = parseJsonObject(input.value);
    if (!parsed) return;
    state.formModel = parsed;
    renderForm();
  }

  /* ---------------------------------------------------------- study 列表 / 快照 */

  function renderRunInfo(snapshot) {
    const info = $("condoptRunInfo");
    if (!info) return;
    if (!snapshot) {
      info.textContent = state.currentRun ? "（未取到快照）" : "先建 study（开工建议 → 建 study）";
      return;
    }
    const best = snapshot.best && snapshot.best.y != null ? Number(snapshot.best.y).toFixed(3) : "—";
    const bits = [
      "n_obs " + (snapshot.n_obs == null ? "—" : snapshot.n_obs),
      "best " + best,
      "剩余 " + (snapshot.remaining == null ? "—" : snapshot.remaining),
      (snapshot.pending && snapshot.pending.length ? "pending " + snapshot.pending.length : "pending 0"),
    ];
    if (snapshot.config && snapshot.config.algorithm) bits.push(String(snapshot.config.algorithm));
    info.textContent = bits.join(" · ");
  }

  function renderRunOptions() {
    const select = $("condoptRunSelect");
    const block = $("condoptRuns");
    if (!select || !block) return;
    block.hidden = false;
    if (!state.runs.length) {
      select.innerHTML = `<option value="">（没有 study）</option>`;
      select.disabled = true;
      renderRunInfo(null);
      return;
    }
    select.disabled = false;
    select.innerHTML = state.runs.map((run) => {
      const best = run.best == null ? "—" : Number(run.best).toFixed(2);
      return `<option value="${esc(run.id)}" ${run.id === state.currentRun ? "selected" : ""}>${
        esc(run.id)} · n=${run.n_obs == null ? "—" : run.n_obs} · best=${esc(best)}</option>`;
    }).join("");
  }

  async function refreshRuns(options = {}) {
    try {
      const data = await getJson(ENDPOINTS.runs);
      state.runs = Array.isArray(data.runs) ? data.runs : [];
      setOnline(true);
      if (!state.currentRun || !state.runs.some((run) => run.id === state.currentRun)) {
        state.currentRun = state.runs.length ? state.runs[0].id : null;
      }
      renderRunOptions();
      // run 列表到位后要重渲染动作条：只读动作（report）只有在“有 run”时才出现，
      // 且“还没有 study”的提示也要跟着更新。
      renderActions(scenarioByKey(state.scenarioKey));
      renderReviewExtra();
      if (state.currentRun) {
        // 上游公开 getter：让 app.js 的“保存拍板留痕”有 runId 可写（不改 app.js）。
        if (window.EduKit && EduKit.state) EduKit.state.runId = state.currentRun;
        await refreshStudy({ quiet: true });
      } else {
        renderRunInfo(null);
      }
    } catch (error) {
      setOnline(false);
      state.runs = [];
      const block = $("condoptRuns");
      if (block) block.hidden = true;
      if (!options.quiet) toast("没有可用 study：" + error.message);
    }
  }

  async function refreshStudy(options = {}) {
    if (!state.currentRun) { renderRunInfo(null); return; }
    try {
      const data = await getJson(ENDPOINTS.study + "?id=" + encodeURIComponent(state.currentRun));
      setOnline(true);
      renderRunInfo(data.study || null);
    } catch (error) {
      if (!options.quiet) toast("读取 study 失败：" + error.message);
      renderRunInfo(null);
    }
  }

  /* ---------------------------------------------------------- 动作条 */

  function actionButtons(scenario) {
    if (!scenario || !scenario.action) return [];
    const primary = {
      prepare: [{ action: "init", label: "建 study", icon: "plus-circle" }],
      ask: [{ action: "ask", label: "取下一批", icon: "grid-3x3" }],
      tell: [{ action: "tell", label: "回填结果", icon: "clipboard-check" }],
      delivery: [{ action: "delivery", label: "交付自查", icon: "package-check" }],
      render: [{ action: "render", label: "渲染看板", icon: "layout-dashboard" }],
    }[scenario.action] || [];
    primary.forEach((item) => { item.needsRun = item.action !== "init"; });

    // 契约 §3 里的只读动作：任何 needs_run 场景都能用；report 需要有 run 才有意义。
    const readonly = [];
    if (scenario.needs_run) {
      readonly.push({ action: "step", label: "下一步", icon: "footprints", needsRun: true });
      readonly.push({ action: "recommend", label: "推荐条件", icon: "target", needsRun: true });
    }
    if (state.currentRun) {
      readonly.push({ action: "report", label: "报告 Markdown", icon: "file-text", needsRun: true });
    }
    return primary.concat(readonly);
  }

  function renderActions(scenario) {
    const block = $("condoptActions");
    const row = $("condoptActionRow");
    if (!block || !row) return;
    if (!scenario || !scenario.action) { block.hidden = true; return; }
    // import 场景的动作在它自己的流程面板里（scan/map/apply），不要在这里再放一套
    // /api/action 按钮，否则用户会看到两个语义不同的入口。
    if (scenario.action === "import") { block.hidden = true; return; }

    const buttons = actionButtons(scenario);
    const needsRun = buttons.some((item) => item.needsRun);
    const parts = [];

    if (scenario.action === "prepare") {
      const algoOptions = [`<option value="auto">auto（交给 router）</option>`]
        .concat(state.algorithms.map((item) => `<option value="${esc(item.name)}">${esc(item.name)} · ${esc(item.family || "")}</option>`));
      parts.push(`<label class="condopt-inline"><span>study id</span>
        <input class="input" type="text" id="condoptNewRunId" value="${esc(defaultRunId())}"></label>`);
      parts.push(`<label class="condopt-inline"><span>算法</span>
        <select class="input" id="condoptNewRunAlgo">${algoOptions.join("")}</select></label>`);
    }

    buttons.forEach((item) => {
      parts.push(`<button class="btn btn-sm ${item.action === scenario.action ? "btn-accent" : "btn-ghost"}"
        type="button" data-condopt-action="${esc(item.action)}">
        <i data-lucide="${esc(item.icon)}"></i>${esc(item.label)}</button>`);
    });

    row.innerHTML = parts.join("");
    block.hidden = false;

    const hint = $("condoptActionHint");
    if (hint) {
      // 与 P1 同一类问题：离线时不要用「动作走 POST /api/action」暗示这些按钮现在能用。
      hint.textContent = (!state.online && !state.template)
        ? "后端未连接：闭环动作（POST /api/action）不可用；可先用下面的“开始处理”看离线演示。"
        : (needsRun && !state.currentRun
          ? "还没有 study：先在「开工建议」里建 study，然后用左侧 study 下拉框选一个。"
          : "动作走 POST /api/action（结构化结果）；上面的“开始处理”走 POST /api/run（流式观感）。");
    }
    refreshIcons();
  }

  function defaultRunId() {
    const now = new Date();
    const pad = (value) => String(value).padStart(2, "0");
    return `run-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`;
  }

  function showActionResult(title, data) {
    const box = $("condoptResult");
    if (!box) return;
    const result = data && data.result != null ? data.result : {};
    let json = JSON.stringify(result, null, 2);
    if (json.length > 6000) json = json.slice(0, 6000) + "\n…（已截断，完整结果在后端的结构化返回里）";
    const dashboard = result && typeof result.dashboard === "string" ? result.dashboard : "";
    box.hidden = false;
    box.innerHTML = `
      <div class="condopt-result-head">
        <span class="kicker">${esc(title)}</span>
        <span class="hint">${esc((data && data.message) || "")}</span>
      </div>
      ${dashboard ? `<p class="condopt-path">看板已生成：<code>${esc(dashboard)}</code>（只读 run 目录，单文件、无外链）</p>` : ""}
      <pre class="condopt-json">${esc(json)}</pre>`;
  }

  function showActionError(title, error) {
    const box = $("condoptResult");
    if (!box) return;
    box.hidden = false;
    box.innerHTML = `<div class="condopt-result-head"><span class="kicker">${esc(title)}</span>
      <span class="condopt-error">失败：${esc(error && error.message ? error.message : "未知错误")}</span></div>`;
  }

  async function runAction(action) {
    if (state.busy) return;
    const scenario = scenarioByKey(state.scenarioKey);
    if (!scenario) return;

    let run = state.currentRun;
    let payload = {};

    if (action === "init") {
      readFormIntoModel();
      const answers = state.formModel || {};
      const idInput = $("condoptNewRunId");
      const algoInput = $("condoptNewRunAlgo");
      const id = String((idInput && idInput.value) || "").trim();
      if (!id) { toast("先填一个 study id"); return; }
      payload = { id, answers, algorithm: (algoInput && algoInput.value) || "auto" };
      if (Number.isFinite(Number(answers.budget))) payload.budget = Number(answers.budget);
      if (Number.isFinite(Number(answers.batch))) payload.batch = Number(answers.batch);
      run = null;
    } else {
      if (!run) { toast("先选一个 study"); return; }
      // 契约 §3：`run` 是 POST /api/action 的**请求顶层字段**，payload 里只放该 action 自己的
      // 字段（未知字段会被后端判 E_VALIDATION）。注意与 §4 的 SSE 通道不同：
      // /api/run 的 payload 里是带 run 的（见 buildPayload 的注释与契约 §4 请求示例）。
      if (action === "ask") {
        const loose = parseJsonObject(($("input") || {}).value) || {};
        payload = {};
        if (Number.isFinite(Number(loose.n))) payload.n = Number(loose.n);
        if (typeof loose.plate === "string" && loose.plate) payload.plate = loose.plate;
      } else if (action === "tell") {
        const withRun = buildPayload({ scenario: state.scenarioKey, text: ($("input") || {}).value || "" });
        if (!withRun) return;
        payload = Object.assign({}, withRun);
        delete payload.run;
      } else {
        payload = {}; // delivery / render / step / recommend / report：无额外字段
      }
    }

    state.busy = true;
    const buttons = ($("condoptActionRow") || {}).querySelectorAll ? $("condoptActionRow").querySelectorAll("button") : [];
    Array.from(buttons).forEach((button) => { button.disabled = true; });
    try {
      const data = await postAction(action, run, payload);
      setOnline(true);
      showActionResult(`${action} · 结构化结果`, data);
      toast((data && data.message) || (action + " 完成"));
      if (action === "init") {
        const created = data.result && data.result.run && data.result.run.id;
        if (created) state.currentRun = created;
        await refreshRuns({ quiet: true });
        if (state.currentRun) await refreshStudy({ quiet: true });
        openReviewPanel();
      } else {
        await refreshRuns({ quiet: true });
        await refreshStudy({ quiet: true });
        openReviewPanel();
      }
    } catch (error) {
      setOnline(false);
      showActionError(action, error);
      toast("动作失败：" + (error && error.message ? error.message : "未知错误"));
    } finally {
      state.busy = false;
      Array.from(buttons).forEach((button) => { button.disabled = false; });
      refreshIcons();
    }
  }

  /* ---------------------------------------------------------- 人工拍板 / 留痕 */

  function renderReviewExtra() {
    const box = $("condoptReviewExtra");
    if (!box) return;
    const scenario = scenarioByKey(state.scenarioKey);
    if (!scenario || !scenario.action) { box.innerHTML = ""; return; }
    if (!state.algorithms.length) {
      box.innerHTML = `<p class="hint">算法清单不可用（后端未连接）：覆写动作已禁用，仍可本地留痕。</p>`;
      return;
    }
    box.innerHTML = `<label class="condopt-inline condopt-inline-wide"><span>覆写算法 override</span>
      <select class="input" id="condoptOverrideAlgo">
        <option value="">（不覆写）</option>
        ${state.algorithms.map((item) => `<option value="${esc(item.name)}" title="${esc(item.summary || "")}">${
          esc(item.name)} · ${esc(item.family || "")}</option>`).join("")}
      </select></label>
      <p class="hint">选择算法后点“保存拍板留痕”会 POST /api/action {override, algorithm, reason}，写入 decisions.jsonl；reason 取上面的理由框。</p>`;
  }

  // app.js 的 finishRun() 会调 showReview()，但动作条不经过流式，所以这里补一个打开逻辑。
  // #decisions / #quickNotes 用与 app.js 相同的数据属性，上游的委托监听器会自动接管点击。
  function openReviewPanel() {
    const review = $("review");
    const scenario = scenarioByKey(state.scenarioKey);
    if (!review || !scenario) return;
    const config = scenario.review || { type: "decision" };
    if (review.hidden === false && $("decisions") && $("decisions").children.length) {
      renderReviewExtra();
      return;
    }
    review.hidden = false;
    const scoreBox = $("scoreBox");
    if (scoreBox) scoreBox.classList.add("is-hidden");
    const decisions = $("decisions");
    if (decisions) {
      const options = config.options || ["采纳", "调整后执行", "转人工"];
      decisions.innerHTML = options.map((option) => `
        <button class="decision" type="button" data-value="${esc(option)}">${esc(option)}</button>`).join("");
    }
    const quick = $("quickNotes");
    if (quick && Array.isArray(config.notes)) {
      quick.innerHTML = config.notes.map((note) => `<button type="button" data-note="${esc(note)}">${esc(note)}</button>`).join("");
    }
    const hint = $("reviewHint");
    if (hint && config.hint) hint.textContent = config.hint;
    const status = $("reviewStatus");
    if (status) { status.textContent = "待拍板"; status.classList.remove("is-done"); }
    renderReviewExtra();
  }

  // 覆写被拦下时，把复核区恢复到「未保存」状态：否则 app.js 已经把保存按钮禁用、
  // 状态显示“已复核”，用户没有机会改选算法重试（本地留痕已写，但 decisions 没写）。
  function reopenReview(message) {
    const button = $("saveReview");
    if (button) {
      button.disabled = false;
      button.innerHTML = '<i data-lucide="check"></i>重存拍板留痕';
    }
    const status = $("reviewStatus");
    if (status) { status.textContent = "待拍板"; status.classList.remove("is-done"); }
    const stamp = $("savedStamp");
    if (stamp) stamp.hidden = true;
    const saved = $("reviewSaved");
    if (saved) saved.textContent = message;
    refreshIcons();
  }

  function onReview(record) {
    const scenario = scenarioByKey(record && record.scenario);
    const box = $("condoptReviewExtra");
    const select = document.getElementById("condoptOverrideAlgo");
    const algorithm = select && select.value ? select.value : "";
    const reason = (record && record.note) || "";
    const wantsOverride = /覆写/.test(record && record.decision ? record.decision : "");

    if (wantsOverride && !algorithm) {
      toast("选择“覆写”时要指定算法：补齐后再点一次");
      reopenReview("覆写未提交：先选算法（复核区下方的覆写算法），再点“重存拍板留痕”");
      return;
    }
    if (wantsOverride && !reason) {
      toast("覆写必须写理由：AI 的覆写必须留痕");
      reopenReview("覆写未提交：理由不能为空（覆写必须留痕）");
      return;
    }

    if (!wantsOverride) {
      const saved = $("reviewSaved");
      if (saved) saved.textContent = "已记录本机 · " + (state.currentRun || record.run_id || "本次会话") + "（非覆写，不写 decisions）";
      return;
    }

    if (!state.currentRun) {
      if (box) box.insertAdjacentHTML("beforeend", `<p class="condopt-error">覆写需要一个 study：先建 study 或选一个 run。</p>`);
      toast("覆写需要一个 study");
      reopenReview("覆写未提交：没有选中 study");
      return;
    }

    postAction("override", state.currentRun, { algorithm, reason })
      .then((data) => {
        const saved = $("reviewSaved");
        if (saved) saved.textContent = "已写入 decisions.jsonl · override " + algorithm + " · " + state.currentRun;
        showActionResult("override · 结构化结果", data);
        toast((data && data.message) || ("已覆写为 " + algorithm + " 并留痕"));
        return refreshStudy({ quiet: true });
      })
      .catch((error) => {
        const saved = $("reviewSaved");
        if (saved) saved.textContent = "覆写未写入后端（" + error.message + "），仅本地留痕";
        toast("覆写失败：" + error.message);
      });
  }

  /* ---------------------------------------------------------- 场景同步 */

  function syncScenario() {
    const key = (window.EduKit && EduKit.state && EduKit.state.scenarioKey) || state.scenarioKey;
    if (!key) return;
    const changed = key !== state.scenarioKey;
    state.scenarioKey = key;
    const scenario = scenarioByKey(key);
    if (!scenario) return;

    const isKickoff = scenario.action === "prepare";
    const isImport = scenario.action === "import";
    const form = $("condoptForm");
    if (!isKickoff && form) form.hidden = true;

    // §10：01 场景首屏 = 对话（kickoff.js）；旧的 45 字段表单降级进「高级」抽屉
    const kickoffBox = $("condoptKickoff");
    const advanced = $("condoptAdvanced");
    if (advanced) advanced.hidden = !isKickoff;
    if (isKickoff) {
      if (window.CONDOPT_KICKOFF) window.CONDOPT_KICKOFF.render();
      // 高级抽屉里的 JSON 仍与表单双向同步（旧能力不删），但只在需要时渲染表单
      const input = $("input");
      const parsed = input ? parseJsonObject(input.value) : null;
      state.formModel = parsed || (state.template ? JSON.parse(JSON.stringify(state.template)) : {});
      if (advanced && advanced.open) renderForm();
    } else if (window.CONDOPT_KICKOFF) {
      window.CONDOPT_KICKOFF.hide();
    }
    // 06 场景有自己的结果区；切走时把它收起来，避免两个面板同时显示
    const importResult = $("condoptImportResult");
    if (importResult && !isImport) importResult.hidden = true;

    renderActions(scenario);
    renderReviewExtra();

    const resultBox = $("condoptResult");
    if (resultBox && changed) { resultBox.hidden = true; resultBox.innerHTML = ""; }
  }

  function watchScenario() {
    const meta = $("scenarioMeta");
    if (!meta) return;
    const observer = new MutationObserver(() => syncScenario());
    observer.observe(meta, { childList: true, characterData: true, subtree: true });
  }

  /* ---------------------------------------------------------- 顶栏徽章（P1） */

  // 被 app.js 的 [condopt hook #8] 调用：**还没跑过场景（没有 SSE meta 帧）**时，
  // 按「后端是否已连接」给徽章文案。只有真正回退到 EduKit.mockRun 时才写「离线演示」。
  // SSE meta 一到，app.js 自己会切「真实模型」，本函数不再参与。
  function modeBadge() {
    if (state.online) {
      return {
        text: "后端已连接 · 待运行",
        pill: "后端已连接",
        note: "<strong>后端已连接</strong> · 运行后走真实 SSE（失败自动回退）",
        connected: true,
      };
    }
    return {
      text: "离线演示",
      pill: "离线 mock",
      note: "<strong>离线 mock</strong> · 可直接演示",
      connected: false,
    };
  }

  function setOnline(online) {
    const next = Boolean(online);
    const changed = next !== state.online;
    state.online = next;
    const runtime = $("runtimeText");
    if (runtime) {
      runtime.textContent = next
        ? "后端在线 · 只绑 127.0.0.1 · 数据不上传"
        : "离线演示 · 后端未连接（自动回退 mock）";
    }
    // 立即重画徽章：否则首屏要等用户跑一次场景才更新（P1 的根因）。
    if (window.EduKit && typeof EduKit.renderMode === "function") {
      try { EduKit.renderMode(); } catch (error) { /* 徽章不阻塞页面 */ }
    }
    if (changed) refreshIcons();
  }

  function bindEvents() {
    const form = $("condoptForm");
    if (form) {
      form.addEventListener("input", (event) => {
        if (!event.target.closest("[data-path]")) return;
        readFormIntoModel();
        serializeModel(true);
      });
      form.addEventListener("click", (event) => {
        const remove = event.target.closest("[data-remove-row]");
        if (remove) { removeVariableRow(Number(remove.dataset.removeRow)); return; }
        if (event.target.closest("#condoptFormReset")) { resetFormFromTemplate(); return; }
        if (event.target.closest("#condoptFormAddVar")) { addVariableRow(); }
      });
    }

    // §10.4：旧的 45 字段表单收进「高级：直接改 answers JSON」抽屉，**首次打开时才渲染**
    // （省掉首屏 1500px 的隐藏 DOM；旧能力一个不少，只是不再铺在最前面）。
    const advanced = $("condoptAdvanced");
    if (advanced) {
      advanced.addEventListener("toggle", () => {
        // 关闭的 <details> 在 Chromium 里只是 content-visibility:hidden —— 内容**仍占版面**
        // （实测 .condopt-advanced-body 仍是 317.8px 高的一块，压在 .attach-row/#condoptActions 上）。
        // 所以显式用 `hidden` 让它真正不参与布局（_verify/geom_probe.py 也据此跳过）。
        const body = $("condoptAdvancedBody");
        if (body) body.hidden = !advanced.open;
        if (!advanced.open) return;
        const kickoffForm = $("condoptForm");
        if (kickoffForm) kickoffForm.hidden = false;
        renderForm();
        refreshIcons();
      });
    }

    const actions = $("condoptActionRow");
    if (actions) {
      actions.addEventListener("click", (event) => {
        const button = event.target.closest("[data-condopt-action]");
        if (button) runAction(button.dataset.condoptAction);
      });
    }

    const reload = $("condoptReloadRuns");
    if (reload) reload.addEventListener("click", () => refreshRuns());

    const select = $("condoptRunSelect");
    if (select) {
      select.addEventListener("change", () => {
        state.currentRun = select.value || null;
        if (state.currentRun && window.EduKit && EduKit.state) EduKit.state.runId = state.currentRun;
        refreshStudy();
        renderActions(scenarioByKey(state.scenarioKey));
      });
    }

    const input = $("input");
    if (input) {
      input.addEventListener("input", () => {
        if (state.syncing) return;
        state.syncing = true;
        syncFormFromJson();
        state.syncing = false;
      });
    }

    // 载入示例 / 清空 会重写 #input（程序化赋值不触发 input 事件），
    // 所以这两个按钮额外同步一次表单。
    ["sampleBtn", "clearBtn"].forEach((id) => {
      const button = $(id);
      if (button) button.addEventListener("click", () => setTimeout(() => syncScenario(), 0));
    });
  }

  /* ---------------------------------------------------------- 启动 */

  async function boot() {
    installAdapter();
    bindEvents();
    setOnline(false);
    syncScenario();
    watchScenario();
    renderForm();
    renderReviewExtra();

    if (!state.template) {
      try {
        const data = await getJson(ENDPOINTS.bootstrap);
        state.boot = data;
        state.algorithms = (data.condopt && Array.isArray(data.condopt.algorithms)) ? data.condopt.algorithms : [];
        state.template = data.prepare_template || null;
        setOnline(true);
        patchScenarios(data.scenarios);
        syncScenario();
        await refreshRuns({ quiet: true });
      } catch (error) {
        setOnline(false);
        const hint = $("condoptFormHint");
        if (hint) hint.textContent = "后端未连接：开工表暂不可用，其它场景仍可离线演示（不白屏）。";
        const form = $("condoptForm");
        if (form) form.hidden = true;
        renderActions(scenarioByKey(state.scenarioKey));
      }
    }

    if (state.currentRun && window.EduKit && EduKit.state) EduKit.state.runId = state.currentRun;
    refreshIcons();
    console.info("[condopt] workbench layer ready · 后端在线=" + state.online);
  }

  // 契约 §4.1：/api/bootstrap 可用时以后端 Scenario.stages 为准并重渲染，
  // 防两边漂移（漂移本身是 bug，这里只做防抖，不替代 data.js 与契约一致）。
  function patchScenarios(remote) {
    if (!Array.isArray(remote) || !window.EDU_DATA) return;
    const local = window.EDU_DATA.scenarios || [];
    let drifted = false;
    remote.forEach((item) => {
      const target = local.find((scenario) => scenario.key === item.key);
      if (!target || !Array.isArray(item.stages)) return;
      if (JSON.stringify(target.stages) !== JSON.stringify(item.stages)) {
        drifted = true;
        target.stages = item.stages.slice();
      }
    });
    if (drifted) {
      console.warn("[condopt] data.js 的 stages 与 /api/bootstrap 不一致，已以后端为准并重渲染（请修 data.js）");
      const meta = $("scenarioMeta");
      const list = $("scenarioList");
      if (list && meta) {
        const current = local.find((scenario) => scenario.key === state.scenarioKey);
        if (current) {
          const button = list.querySelector('[data-key="' + current.key + '"]');
          // [app.js hook #11 配套] 「重选当前场景」现在是 no-op（契约 §11.2），
          // 所以 stages 漂移修复不能再靠 button.click()，必须走显式 force。
          if (window.EduKit && typeof EduKit.selectScenario === "function") {
            EduKit.selectScenario(current.key, { force: true, quiet: true });
          } else if (button) {
            button.click();  // 老 app.js（没有该导出）时的兜底
          }
        }
      }
    }
    return drifted;
  }

  /* 暴露给 app.js 的钩子（见 ADAPTATION.md）。 */
  window.CONDOPT = {
    hasPayload,
    buildPayload,
    onReview,
    // [condopt hook #8] 顶栏徽章文案（app.js 的 renderMode 调用）
    modeBadge,
    // [condopt hook #10] 自管执行流的场景（import 用 scan/map/apply 三个 JSON 端点，无 SSE 场景）：
    // 具体实现放在 assets/import.js 的 CONDOPT_IMPORT 上，本层只做转发，保持「一个入口」。
    handlesRun: (key) => Boolean(window.CONDOPT_IMPORT && window.CONDOPT_IMPORT.handlesRun(key)),
    runScenario: (key) => {
      if (!window.CONDOPT_IMPORT || !window.CONDOPT_IMPORT.handlesRun(key)) return Promise.resolve();
      return window.CONDOPT_IMPORT.runScenario(key);
    },
    // 供 import.js 复用「人工拍板 · 留痕」面板（避免复制一份复核区渲染逻辑）
    openReviewPanel,
    // 供 kickoff.js 复用唯一写路径：POST /api/action（§10.4：对话只是攒 answers 的另一种入口）
    action: postAction,
    setOnline,
    // 供 kickoff.js 在自身加载完成后重跑一次场景同步：
    // defer 脚本按顺序执行，condopt.js 的 boot() 首次 syncScenario() 时 kickoff.js 还没执行，
    // 否则「01 首屏是聊天」会晚到（bootstrap 失败时甚至永远不来）。
    resync: () => syncScenario(),
    state,
    refreshRuns,
    refreshStudy,
    get online() { return state.online; },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
