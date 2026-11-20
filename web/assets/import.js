/* ============================================================
   condopt 工作台 · assets/import.js
   ------------------------------------------------------------
   场景 06「导入数据」的完整前端流程（唯一真源：CONTRACT.md §9）：

     粘路径 → 读取预览（列画像 + 样例）→ 映射确认表（每列角色可改）
       → 二次确认「发 N 行样本给 <model>」→ 模型建议 + 理由 + 置信度
       → 应用 → **立刻**用返回的 params_schema 渲染「算法 + 子参数」表单

   三个端点（§9.3 / §9.4 / §9.5）：
     POST /api/import/scan   {"path"}                              ← 不出网
     POST /api/import/map    {"scan_id","consent","sample_rows","model","fallback"}
     POST /api/import/apply  {"id","path","scan_id","mapping","algorithm","params",
                              "budget","batch","reason","count_toward_budget"}

   边界（§9.1，本文件严格遵守）：
   - 除 map 之外没有任何外网调用；map 之前必须经过二次确认弹窗，确认后才带 consent:true。
   - 未配置模型（E_LLM_UNAVAILABLE）时**不静默降级**，而是明确提示缺什么，并给出
     「改走离线规则映射（fallback:"rules"）」的可点选项。
   - 后端不可用时走内置示例数据，每一处都标注「离线演示」——不读用户文件，也不假装读过。

   与上游 app.js 的关系：app.js 用 [condopt hook #10] 把「开始处理」交给本文件
   （CONDOPT_IMPORT.handlesRun/runScenario），因此 import 场景不进入 SSE 流程。
   ============================================================ */

(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));

  const ENDPOINTS = {
    scan: "/api/import/scan",
    map: "/api/import/map",
    apply: "/api/import/apply",
  };

  const KINDS = ["continuous", "integer", "log", "categorical", "bool", "ordinal"];
  const ROLES = [
    { value: "variable", label: "条件变量" },
    { value: "objective", label: "目标" },
    { value: "constraint", label: "约束" },
    { value: "fidelity", label: "保真" },
    { value: "cost", label: "成本" },
    { value: "ignore", label: "忽略" },
  ];
  const DEFAULT_SAMPLE_ROWS = 12;
  const MAX_SAMPLE_ROWS = 50;

  const clampSampleRows = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number)) return DEFAULT_SAMPLE_ROWS;
    return Math.max(1, Math.min(MAX_SAMPLE_ROWS, Math.round(number)));
  };

  const S = {
    scenarioKey: null,
    scan: null,          // scan 响应
    mapping: null,       // 正在编辑的 mapping（apply 以它为准，§9.4：mapping 是建议不是真源）
    suggestion: null,    // map 响应
    mock: false,         // 是否处于离线演示
    mockStage: 0,        // 离线演示走到哪一步
    applied: null,       // apply 响应
    previewFor: null,    // 当前 sample_preview 对应的 {sampleRows, model}（用于不变量比对）
    invariant: null,     // 最近一次「scan 预告 vs map 实发」比对结果
    paramsSchema: [],
    params: {},
    paramsDirty: false,
    algorithm: "auto",
    algorithms: [],
    model: "",
    sampleRows: DEFAULT_SAMPLE_ROWS,
    importId: "",
    budget: "",
    batch: 4,
    reason: "导入历史数据作为先验",
    busy: false,
    lastError: null,
    stages: [0, 0, 0, 0], // 0 pending / 1 running / 2 done / 3 failed
  };

  /* ---------------------------------------------------------- 通用 */

  function toast(message) {
    const box = $("toast");
    const text = $("toastText");
    if (!box || !text) return;
    text.textContent = message;
    box.classList.add("is-on");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => box.classList.remove("is-on"), 3600);
  }

  function refreshIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      try { window.lucide.createIcons(); } catch (error) { /* 图标不阻塞 */ }
    }
  }

  function fmtBytes(bytes) {
    const value = Number(bytes);
    if (!Number.isFinite(value)) return "—";
    if (value < 1024) return value + " B";
    if (value < 1024 * 1024) return (value / 1024).toFixed(1) + " KB";
    return (value / (1024 * 1024)).toFixed(2) + " MB";
  }

  // 「同意」要基于精确数字：人类可读形式后面再补一个精确字节数（相同就不重复）。
  function fmtBytesExact(bytes) {
    const value = Number(bytes);
    if (!Number.isFinite(value)) return "—";
    const human = fmtBytes(value);
    const exact = value + " B";
    return human === exact ? exact : human + "（" + exact + "）";
  }

  function fmtNum(value, digits = 3) {
    if (value == null || value === "") return "—";
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    return Number.isInteger(number) ? String(number) : number.toFixed(digits);
  }

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    let data = null;
    try { data = await response.json(); } catch (error) { data = null; }
    if (!response.ok || !data || data.ok === false) {
      const error = new Error((data && data.message) || ("HTTP " + response.status));
      error.code = (data && data.error) || ("HTTP_" + response.status);
      error.hint = (data && data.hint) || "";
      error.raw = data;
      throw error;
    }
    return data;
  }

  const currentScenario = () => {
    const list = (window.EDU_DATA && window.EDU_DATA.scenarios) || [];
    return list.find((item) => item.key === "import") || null;
  };
  const isActive = () => {
    const key = (window.EduKit && EduKit.state && EduKit.state.scenarioKey) || null;
    return key === "import";
  };

  /* ---------------------------------------------------------- 阶段条（由本流程驱动，import 没有 SSE） */

  function setStage(index, status) {
    S.stages[index] = status;
    renderStages();
  }

  function renderStages() {
    const list = $("stages");
    const scenario = currentScenario();
    if (!list || !scenario) return;
    const map = { 0: "pending", 1: "running", 2: "done", 3: "failed" };
    list.innerHTML = scenario.stages.map((label, index) => {
      const state = map[S.stages[index] || 0];
      const number = state === "done" ? "✓" : String(index + 1).padStart(2, "0");
      return `
        <li class="stage is-${state}">
          <span class="stage-no">${number}</span>
          <span class="stage-label">${esc(label)}</span>
        </li>`;
    }).join("");
  }

  /* ---------------------------------------------------------- 模型 / 端点信息（§9.1） */

  // 目标 URL 与模型 id 必须来自后端（外发确认要列明，不能猜）。
  // §2.1 的 `import_llm` 给 url/model/key_env/configured/enabled/url_valid；
  // 而 `url_error`（F7/F8 的原文原因）只出现在同一份 bootstrap 的 `import`(import_settings) 里，
  // 所以这里**合并多个候选位置**（先到先得、跳过 null/undefined），而不是只取第一个，
  // 否则会丢掉 url_error 原文。读不到就如实说「后端未提供」，绝不写死一个 URL 去骗确认弹窗。
  function llmInfo() {
    const boot = (window.CONDOPT && window.CONDOPT.state && window.CONDOPT.state.boot) || {};
    const candidates = [
      boot.import_llm,
      boot.condopt && boot.condopt.import_llm,
      boot.condopt && boot.condopt.import,
      boot.import,
    ].filter((item) => item && typeof item === "object");

    const merged = {};
    candidates.forEach((item) => {
      Object.keys(item).forEach((key) => {
        const value = item[key];
        if (value === undefined || value === null || key in merged) return;
        merged[key] = value;
      });
    });

    return {
      url: merged.url || merged.base_url || merged.baseURL || "",
      model: merged.model || "",
      keyEnv: merged.key_env || merged.api_key_env || merged.keyEnv || "",
      configured: merged.configured,
      enabled: merged.enabled,
      // F7/F8：地址合法性。老后端没有这两个字段 → 保持 undefined（前端不假装知道）。
      urlValid: typeof merged.url_valid === "boolean" ? merged.url_valid : undefined,
      urlError: typeof merged.url_error === "string" && merged.url_error.trim() ? merged.url_error.trim() : "",
    };
  }

  // 地址不合法时的统一「事前预警」文案（弹窗与左栏都用它，避免两处说法不一致）。
  function urlProblem() {
    const info = llmInfo();
    if (info.urlValid !== false) return null;
    return {
      message: info.urlError
        ? `模型网关地址配置不合法：${info.urlError}（现在点确认只会拿到 503，不会真正外发）`
        : "模型网关地址配置不合法（后端未给出原因）；现在点确认只会拿到 503，不会真正外发。",
    };
  }

  /* ---------------------------------------------------------- 映射模型 */

  function emptyMapping() {
    return { variables: [], objective: null, constraints: [], fidelity: null, cost: null };
  }

  function normalizeMapping(raw) {
    const mapping = emptyMapping();
    if (!raw || typeof raw !== "object") return mapping;
    if (Array.isArray(raw.variables)) {
      mapping.variables = raw.variables
        .map((item) => ({
          column: String((item && (item.column || item.name)) || ""),
          name: String((item && (item.name || item.column)) || ""),
          kind: KINDS.includes(item && item.kind) ? item.kind : "continuous",
          low: item && item.low != null ? item.low : null,
          high: item && item.high != null ? item.high : null,
          unit: (item && item.unit) || "",
        }))
        .filter((item) => item.column);
    }
    if (raw.objective && (raw.objective.column || raw.objective.name)) {
      mapping.objective = {
        column: String(raw.objective.column || raw.objective.name),
        name: String(raw.objective.name || raw.objective.column),
        direction: raw.objective.direction === "min" ? "min" : "max",
      };
    }
    if (Array.isArray(raw.constraints)) {
      mapping.constraints = raw.constraints
        .map((item) => ({
          column: String((item && (item.column || item.name)) || ""),
          op: (item && ["<=", ">=", "=="].includes(item.op)) ? item.op : ">=",
          value: item && item.value != null ? item.value : null,
        }))
        .filter((item) => item.column);
    }
    const pick = (item) => (item && (item.column || item.name)
      ? { column: String(item.column || item.name), name: String(item.name || item.column) }
      : null);
    mapping.fidelity = pick(raw.fidelity);
    // §9.4（2026-11-20 冻结）：fidelity 可带可选 levels，用于把文件里的原值换算成数值；cost 没有 levels。
    if (mapping.fidelity && raw.fidelity && raw.fidelity.levels && typeof raw.fidelity.levels === "object") {
      mapping.fidelity.levels = raw.fidelity.levels;
    }
    mapping.cost = pick(raw.cost);
    return mapping;
  }

  // levels 在 UI 里是 "low=0.3, med=0.6, high=1.0" 这样的文本，落到 mapping 是 {"low":0.3,...}
  function parseLevels(text) {
    const levels = {};
    String(text || "").split(",").forEach((pair) => {
      const [rawKey, rawValue] = pair.split("=");
      const key = String(rawKey || "").trim();
      const value = Number(String(rawValue || "").trim());
      if (key && Number.isFinite(value)) levels[key] = value;
    });
    return levels;
  }

  function formatLevels(levels) {
    if (!levels || typeof levels !== "object") return "";
    return Object.keys(levels).map((key) => key + "=" + levels[key]).join(", ");
  }

  function roleOf(column) {
    const mapping = S.mapping || emptyMapping();
    if (mapping.objective && mapping.objective.column === column) return "objective";
    if ((mapping.constraints || []).some((item) => item.column === column)) return "constraint";
    if ((mapping.variables || []).some((item) => item.column === column)) return "variable";
    if (mapping.fidelity && mapping.fidelity.column === column) return "fidelity";
    if (mapping.cost && mapping.cost.column === column) return "cost";
    return "ignore";
  }

  function guessKind(column) {
    if (!column) return "continuous";
    const inferred = String(column.inferred || "").toLowerCase();
    if (inferred === "bool" || inferred === "boolean") return "bool";
    if (inferred === "integer" || inferred === "int") return "integer";
    if (inferred === "text" || inferred === "string" || inferred === "category" || inferred === "categorical") {
      return (column.choices && column.choices.length) ? "categorical" : "continuous";
    }
    return "continuous";
  }

  function setRole(columnName, role) {
    if (!S.mapping) S.mapping = emptyMapping();
    const mapping = S.mapping;
    mapping.variables = (mapping.variables || []).filter((item) => item.column !== columnName);
    mapping.constraints = (mapping.constraints || []).filter((item) => item.column !== columnName);
    if (mapping.objective && mapping.objective.column === columnName) mapping.objective = null;
    if (mapping.fidelity && mapping.fidelity.column === columnName) mapping.fidelity = null;
    if (mapping.cost && mapping.cost.column === columnName) mapping.cost = null;

    const column = ((S.scan && S.scan.columns) || []).find((item) => item.name === columnName) || null;
    if (role === "variable") {
      mapping.variables.push({
        column: columnName, name: columnName, kind: guessKind(column),
        low: column && column.min != null ? column.min : null,
        high: column && column.max != null ? column.max : null,
        unit: "",
      });
    } else if (role === "objective") {
      mapping.objective = { column: columnName, name: columnName, direction: "max" };
    } else if (role === "constraint") {
      mapping.constraints.push({
        column: columnName, op: ">=",
        value: column && column.min != null ? column.min : null,
      });
    } else if (role === "fidelity") {
      mapping.fidelity = { column: columnName, name: columnName };
    } else if (role === "cost") {
      mapping.cost = { column: columnName, name: columnName };
    }
  }

  function mappingProblems() {
    const mapping = S.mapping || emptyMapping();
    const problems = [];
    const used = new Set();
    const dup = (column) => (used.has(column) ? true : (used.add(column), false));
    (mapping.variables || []).forEach((item) => { if (dup(item.column)) problems.push(`列 ${item.column} 被指定了多次角色`); });
    if (mapping.objective) {
      if (dup(mapping.objective.column)) problems.push(`列 ${mapping.objective.column} 被指定了多次角色`);
    } else {
      problems.push("还没有指定「目标」列：apply 需要一列作为目标 y");
    }
    (mapping.constraints || []).forEach((item) => { if (dup(item.column)) problems.push(`列 ${item.column} 被指定了多次角色`); });
    if (mapping.fidelity && dup(mapping.fidelity.column)) problems.push(`列 ${mapping.fidelity.column} 被指定了多次角色`);
    if (mapping.cost && dup(mapping.cost.column)) problems.push(`列 ${mapping.cost.column} 被指定了多次角色`);
    (mapping.variables || []).forEach((item) => {
      if (item.low != null && item.high != null && Number(item.low) > Number(item.high)) {
        problems.push(`变量 ${item.column} 的下界大于上界`);
      }
    });
    return problems;
  }

  // §2.1：bootstrap.import_llm 给出服务端**实际生效**的 url/model/key_env 与 configured/enabled。
  // 有这两个布尔值时，就在用户点确认**之前**把「这条路现在走不走得通」说清楚，避免白跑一趟确认弹窗；
  // 没有这些字段（旧后端）就什么都不说，仍以服务端返回为准。
  function llmReadiness() {
    const info = llmInfo();
    // F7/F8：地址不合法优先报——否则会出现「通道已开启」的假安心，而地址根本发不出去。
    const problem = urlProblem();
    if (problem) return { blocked: true, message: problem.message };
    if (typeof info.enabled !== "boolean" && typeof info.configured !== "boolean") return null;
    if (info.enabled === false) {
      return { blocked: true, message: "后端未带 --import-llm 启动，模型通道关闭；现在点确认只会拿到 E_LLM_UNAVAILABLE。" };
    }
    if (info.configured === false) {
      return {
        blocked: true,
        message: `模型通道已开启，但密钥环境变量 ${info.keyEnv || "（后端未告知名称）"} 未配置；现在点确认只会拿到 E_LLM_UNAVAILABLE。`,
      };
    }
    return { blocked: false, message: `模型通道已开启${info.keyEnv ? "，密钥环境变量 " + info.keyEnv + " 已就绪" : ""}。` };
  }

  /* ---------------------------------------------------------- 离线演示数据（只在后端不可用时使用） */

  function demoScan(path) {
    const columns = [
      { name: "T", inferred: "number", missing: 0, unique: 22, min: 40, max: 120, samples: ["60.0", "82.5", "45.0", "99.0", "71.5", "55.0"] },
      { name: "time_min", inferred: "number", missing: 0, unique: 24, min: 5, max: 120, samples: ["30.0", "62.5", "15.0", "88.0", "44.0", "20.5"] },
      { name: "cat_loading", inferred: "number", missing: 0, unique: 20, min: 0.5, max: 3, samples: ["1.20", "2.05", "0.80", "2.60", "1.55", "1.00"] },
      { name: "solvent", inferred: "text", missing: 0, unique: 3, choices: ["MeCN", "EtOH", "water"], samples: ["MeCN", "EtOH", "water", "MeCN", "EtOH", "water"] },
      { name: "yield_pct", inferred: "number", missing: 1, unique: 26, min: 41.2, max: 96.4, samples: ["78.3", "61.9", "84.2", "70.5", "91.0", "66.4"] },
      { name: "purity_pct", inferred: "number", missing: 0, unique: 30, min: 88.1, max: 99.5, samples: ["96.2", "93.5", "98.1", "95.0", "99.0", "92.4"] },
      { name: "note", inferred: "text", missing: 6, unique: 0, choices: [], samples: ["", "", "", "", "", ""] },
    ];
    return {
      ok: true,
      scan_id: "demo-scan-local",
      // 离线演示绝不暗示「读到了你的文件」：把输入的路径标成未读取。
      file: {
        path: path ? path + "（未读取 · 离线演示）" : "（离线演示：没有读任何文件）",
        bytes: 8421, mtime: null, encoding: "utf-8", delimiter: ",", has_header: true,
      },
      n_rows: 90,
      columns,
      rule_mapping: {
        variables: [
          { column: "T", name: "T", kind: "continuous", low: 40, high: 120, unit: "degC" },
          { column: "time_min", name: "time_min", kind: "continuous", low: 5, high: 120, unit: "min" },
          { column: "cat_loading", name: "cat_loading", kind: "continuous", low: 0.5, high: 3, unit: "eq" },
          { column: "solvent", name: "solvent", kind: "categorical", choices: ["MeCN", "EtOH", "water"] },
        ],
        objective: { column: "yield_pct", name: "yield_pct", direction: "max" },
        constraints: [{ column: "purity_pct", op: ">=", value: 95 }],
        fidelity: null,
        cost: null,
      },
      warnings: ["第 7 列 'note' 全部为空，未参与映射"],
    };
  }

  function demoMap(scan) {
    return {
      ok: true,
      source: "rules",
      model: "",
      sent: { url: "", rows: 0, bytes: 0, redacted: false },
      mapping: normalizeMapping(scan.rule_mapping),
      confidence: 0.62,
      rationale: "离线演示：没有调用模型。这是内置示例映射，用来演示「模型建议 → 合并到映射表 → 人工微调」这条链路；真实运行时会显示模型的理由与置信度。",
      warnings: ["离线演示：未外发任何数据，未调用模型"],
    };
  }

  function demoApply() {
    return {
      ok: true,
      run: { id: "import-offline-demo", n_obs: 88, remaining: 32, budget: 120, algorithm: "bayes", direction: "max", best: 96.4 },
      imported: {
        accepted: 88,
        duplicate: 2,
        rejected: [{ row: 7, reason: "'T' 不是数值：'n/a'" }],
      },
      warnings: ["离线演示：导入 88 条后剩余预算 32；如需更多轮次请调大 budget"],
      params_schema: [
        { name: "kernel", type: "choice", default: "matern52", choices: ["matern52", "matern32", "rbf"], description: "GP 核函数；光滑问题 rbf 更贴合，粗糙/不连续用 Matérn。" },
        { name: "length_scale", type: "float", default: 0.3, low: 0.01, high: 10.0, description: "初始 ARD 长度尺度（小样本通常更稳）。" },
        { name: "acquisition", type: "choice", default: "ei", choices: ["ei", "logei", "ucb", "pi", "ts"], description: "采集函数：EI 利用与探索平衡，UCB 更偏探索。" },
        { name: "n_initial", type: "integer", default: 9, low: 0, high: 64, description: "初始设计点数（空间填充）。" },
        { name: "alpha", type: "float", default: 0.0001, low: 0.0, high: 1e-2, description: "观测噪声方差；带复孔时按 (σ²/k)/y_var 逐点估计。" },
      ],
      next: "ask",
    };
  }

  /* ---------------------------------------------------------- 渲染 · 左栏（操作与确认） */

  function stageBadge() {
    if (!S.mock) return "";
    return `<p class="condopt-import-warn"><strong>离线演示</strong>：后端未连接，下面是内置示例数据——没有读你的文件，也没有外发任何内容。</p>`;
  }

  function renderLeft() {
    const box = $("condoptImport");
    if (!box) return;
    if (!isActive()) { box.hidden = true; box.innerHTML = ""; return; }
    box.hidden = false;

    const ready = llmReadiness();
    const urlBlockedLeft = Boolean(urlProblem());
    const parts = [];
    parts.push(stageBadge());

    // 步骤 1：读取
    parts.push(`
      <div class="condopt-import-step">
        <div class="condopt-import-step-head">
          <span class="condopt-import-step-no">1</span>
          <strong>读取文件</strong>
          <span class="hint">只读该文件本身（POST /api/import/scan，不出网）</span>
        </div>
        <div class="condopt-import-row">
          <button class="btn btn-sm btn-accent" type="button" data-import="scan">
            <i data-lucide="file-search"></i>读取路径中的文件
          </button>
          <span class="hint">路径取上面的输入框（支持 .csv / .tsv / .txt / .json / .jsonl）</span>
        </div>
        ${errorCard("scan")}
      </div>`);

    if (S.scan) {
      const file = S.scan.file || {};
      parts.push(`
        <div class="condopt-import-step">
          <div class="condopt-import-step-head">
            <span class="condopt-import-step-no">2</span>
            <strong>列画像与样例</strong>
            <span class="hint">${esc(String(S.scan.n_rows ?? "—"))} 行 · ${esc(String((S.scan.columns || []).length))} 列</span>
          </div>
          <p class="condopt-import-meta">
            <code>${esc(file.path || "—")}</code>
            · ${fmtBytes(file.bytes)} · ${esc(file.encoding || "—")} · 分隔符 <code>${esc(file.delimiter || "—")}</code>
            · 表头 <code>${file.has_header ? "有" : "无"}</code>
            · scan_id <code>${esc(S.scan.scan_id || "—")}</code>
          </p>
          ${(S.scan.warnings || []).length ? listWarnings(S.scan.warnings) : ""}
          ${profileTable(S.scan.columns || [])}
          ${previewTable(S.scan.columns || [])}
        </div>`);

      parts.push(`
        <div class="condopt-import-step">
          <div class="condopt-import-step-head">
            <span class="condopt-import-step-no">3</span>
            <strong>映射确认表</strong>
            <span class="hint">每列角色可改；apply 以你确认的映射为准</span>
          </div>
          <div class="condopt-import-tools">
            <button class="btn btn-ghost btn-sm" type="button" data-import="rules">
              <i data-lucide="scale"></i>用规则映射重置
            </button>
            ${S.suggestion ? `<button class="btn btn-ghost btn-sm" type="button" data-import="merge">
              <i data-lucide="git-merge"></i>合并模型建议</button>` : ""}
          </div>
          ${mappingEditor()}
        </div>`);

      parts.push(`
        <div class="condopt-import-step">
          <div class="condopt-import-step-head">
            <span class="condopt-import-step-no">4</span>
            <strong>让模型读表</strong>
            <span class="hint">${S.mock ? "离线演示：不会外发" : "唯一会出网的步骤，需二次确认"}</span>
          </div>
          ${S.mock
            ? `<div class="condopt-import-row">
                 <button class="btn btn-sm btn-ghost" type="button" data-import="mockmap">
                   <i data-lucide="eye"></i>查看内置示例建议
                 </button>
               </div>`
            : `<div class="condopt-import-row">
                 <label class="condopt-inline"><span>样本行数</span>
                   <input class="input" type="number" min="1" max="${MAX_SAMPLE_ROWS}" data-import-field="sampleRows" value="${esc(S.sampleRows)}"></label>
                 <label class="condopt-inline"><span>模型 id</span>
                   <input class="input" type="text" data-import-field="model" value="${esc(S.model)}" placeholder="留空 = 后端默认"></label>
                 <button class="btn btn-sm btn-accent" type="button" data-import="consent">
                   <i data-lucide="cloud-upload"></i>让模型读表…
                 </button>
               </div>
               <p class="hint">弹窗会列明目标 URL / 模型 id / 行数 / 字节数，并说明只发样本不发整文件。</p>
               ${previewLine()}
               ${ready ? `<p class="${ready.blocked ? "condopt-import-problems" : "hint"}">${urlBlockedLeft ? "事前预警" : "通道状态"}：${esc(ready.message)}</p>` : ""}
               ${ready && ready.blocked ? `<div class="condopt-import-row">
                 <button class="btn btn-sm btn-ghost" type="button" data-import="rulesmap">
                   <i data-lucide="shield-off"></i>直接改走离线规则映射（不出网）
                 </button>
               </div>` : ""}`}
          ${errorCard("map")}
        </div>`);

      parts.push(`
        <div class="condopt-import-step">
          <div class="condopt-import-step-head">
            <span class="condopt-import-step-no">5</span>
            <strong>导入为先验</strong>
            <span class="hint">先验计入 budget（§9.5）</span>
          </div>
          <div class="condopt-import-grid">
            <label class="condopt-field"><span>新 study id</span>
              <input class="input" type="text" data-import-field="importId" value="${esc(S.importId)}"></label>
            <label class="condopt-field"><span>budget（≥ 导入行数）</span>
              <input class="input" type="number" min="1" data-import-field="budget" value="${esc(S.budget)}"></label>
            <label class="condopt-field"><span>batch</span>
              <input class="input" type="number" min="1" data-import-field="batch" value="${esc(S.batch)}"></label>
            <label class="condopt-field"><span>算法</span>
              <select class="input" data-import-field="algorithm">
                <option value="auto" ${S.algorithm === "auto" ? "selected" : ""}>auto（交给 router）</option>
                ${S.algorithms.map((item) => `<option value="${esc(item.name)}" ${item.name === S.algorithm ? "selected" : ""}>${esc(item.name)} · ${esc(item.family || "")}</option>`).join("")}
              </select></label>
            <label class="condopt-field condopt-field-wide"><span>导入理由（写进 import_audit.jsonl）</span>
              <input class="input" type="text" data-import-field="reason" value="${esc(S.reason)}"></label>
            <label class="condopt-field condopt-field-bool condopt-field-locked" title="condopt 的 import_observations 没有「不计预算」的开关">
              <span>先验计入 budget</span>
              <input type="checkbox" checked disabled></label>
          </div>
          <p class="hint">先验导入<strong>必然</strong>计入 budget（condopt <code>import_observations</code> 语义，无法关闭），
            所以这里固定勾选且不可改；budget 至少要 ≥ 导入行数。</p>
          ${mappingProblems().length ? listProblems(mappingProblems()) : ""}
          <div class="condopt-import-row">
            <button class="btn btn-sm btn-primary" type="button" data-import="apply">
              <i data-lucide="database"></i>应用映射并导入
            </button>
            <span class="hint">导入后会立刻显示算法子参数表（不用再点一次）。</span>
          </div>
          ${errorCard("apply")}
        </div>`);
    }

    box.innerHTML = parts.join("");
    refreshIcons();
  }

  function errorCard(kind) {
    const error = S.lastError;
    if (!error || error.stage !== kind) return "";
    const action = error.code === "E_LLM_UNAVAILABLE"
      ? `<button class="btn btn-sm btn-ghost" type="button" data-import="rulesmap">
           <i data-lucide="shield-off"></i>改走离线规则映射（不出网）
         </button>`
      : "";
    const retry = (kind === "map" || kind === "apply")
      ? `<button class="btn btn-sm btn-ghost" type="button" data-import="${kind === "map" ? "consent" : "apply"}">
           <i data-lucide="refresh-cw"></i>重试
         </button>`
      : "";
    const suggested = error.suggestedBudget
      ? `<button class="btn btn-sm btn-ghost" type="button" data-import="usebudget" data-value="${esc(error.suggestedBudget)}">
           <i data-lucide="wand-2"></i>用建议值 budget=${esc(error.suggestedBudget)}
         </button>`
      : "";
    return `
      <div class="condopt-import-error">
        <p><strong>${esc(error.code || "错误")}</strong>：${esc(error.message || "")}</p>
        ${error.hint ? `<p class="condopt-import-hint">${esc(error.hint)}</p>` : ""}
        ${error.extra ? `<p class="condopt-import-hint">${esc(error.extra)}</p>` : ""}
        <div class="condopt-import-row">${action}${retry}${suggested}</div>
      </div>`;
  }

  function listWarnings(warnings) {
    return `<ul class="condopt-import-warnings">${warnings.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`;
  }

  function listProblems(problems) {
    return `<ul class="condopt-import-problems">${problems.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`;
  }

  function profileTable(columns) {
    const head = ["列", "推断类型", "缺失", "唯一值", "min", "max", "取值 / 样例"];
    return `
      <div class="condopt-table-wrap">
        <table class="condopt-table">
          <thead><tr>${head.map((item) => `<th>${esc(item)}</th>`).join("")}</tr></thead>
          <tbody>${columns.map((column) => `
            <tr>
              <td><code>${esc(column.name)}</code></td>
              <td>${esc(column.inferred || "—")}</td>
              <td>${fmtNum(column.missing, 0)}</td>
              <td>${fmtNum(column.unique, 0)}</td>
              <td>${fmtNum(column.min)}</td>
              <td>${fmtNum(column.max)}</td>
              <td class="condopt-cell-soft">${
                (column.choices && column.choices.length)
                  ? esc(column.choices.slice(0, 4).join(" / ")) + ((column.choices.length > 4) ? " …" : "")
                  : esc((column.samples || []).slice(0, 3).join(", "))
              }</td>
            </tr>`).join("")}</tbody>
        </table>
      </div>`;
  }

  // 契约 §9.3 只给「每列的 samples」，所以这里把各列 samples 按位对齐展示。
  // 文案必须说清这是对齐后的样例，不是"读了整表"——不能夸大。
  function previewTable(columns) {
    const depth = Math.min(6, Math.max(0, ...columns.map((column) => (column.samples || []).length)));
    if (!depth) return "";
    const rows = [];
    for (let index = 0; index < depth; index += 1) {
      rows.push(columns.map((column) => ((column.samples || [])[index] ?? "")));
    }
    return `
      <div class="condopt-table-wrap">
        <table class="condopt-table condopt-table-preview">
          <thead><tr>${columns.map((column) => `<th>${esc(column.name)}</th>`).join("")}</tr></thead>
          <tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${esc(cell)}</td>`).join("")}</tr>`).join("")}</tbody>
        </table>
      </div>
      <p class="hint">前 ${depth} 行样例（由各列的 <code>samples</code> 按位对齐；后端只回画像与样例，前端不读整表）。</p>`;
  }

  function mappingEditor() {
    const columns = (S.scan && S.scan.columns) || [];
    const mapping = S.mapping || emptyMapping();
    const variable = (name) => (mapping.variables || []).find((item) => item.column === name) || null;
    return `<div class="condopt-map">${columns.map((column) => {
      const role = roleOf(column.name);
      const extras = [];
      if (role === "variable") {
        const item = variable(column.name) || {};
        extras.push(field("名称", "name", item.name, column.name));
        extras.push(`<label class="condopt-field"><span>类型</span><select class="input" data-map-field="kind" data-column="${esc(column.name)}">
          ${KINDS.map((kind) => `<option value="${esc(kind)}" ${item.kind === kind ? "selected" : ""}>${esc(kind)}</option>`).join("")}
        </select></label>`);
        extras.push(field("下界", "low", item.low, column.name, "number"));
        extras.push(field("上界", "high", item.high, column.name, "number"));
        extras.push(field("单位", "unit", item.unit, column.name));
      } else if (role === "objective") {
        const item = mapping.objective || {};
        extras.push(field("名称", "name", item.name, column.name));
        extras.push(`<label class="condopt-field"><span>方向</span><select class="input" data-map-field="direction" data-column="${esc(column.name)}">
          <option value="max" ${item.direction !== "min" ? "selected" : ""}>max（越大越好）</option>
          <option value="min" ${item.direction === "min" ? "selected" : ""}>min（越小越好）</option>
        </select></label>`);
      } else if (role === "constraint") {
        const item = (mapping.constraints || []).find((entry) => entry.column === column.name) || {};
        extras.push(`<label class="condopt-field"><span>关系</span><select class="input" data-map-field="op" data-column="${esc(column.name)}">
          ${["<=", ">=", "=="].map((op) => `<option value="${esc(op)}" ${item.op === op ? "selected" : ""}>${esc(op)}</option>`).join("")}
        </select></label>`);
        extras.push(field("阈值", "value", item.value, column.name, "number"));
      } else if (role === "fidelity") {
        const item = mapping.fidelity || {};
        extras.push(field("名称", "name", item.name, column.name));
        extras.push(`<label class="condopt-field condopt-field-wide"><span>档位换算 levels（可选）</span>
          <input class="input" type="text" data-map-levels="${esc(column.name)}"
                 value="${esc(formatLevels(item.levels))}" placeholder="low=0.3, med=0.6, high=1.0"></label>`);
      } else if (role === "cost") {
        extras.push(field("名称", "name", (mapping[role] || {}).name, column.name));
      }
      return `
        <div class="condopt-map-row">
          <div class="condopt-map-head">
            <code>${esc(column.name)}</code>
            <span class="condopt-map-inferred">推断 ${esc(column.inferred || "—")}${column.missing ? ` · 缺失 ${column.missing}` : ""}</span>
            <select class="input" data-map-role="${esc(column.name)}" aria-label="${esc(column.name)} 的列角色">
              ${ROLES.map((item) => `<option value="${esc(item.value)}" ${role === item.value ? "selected" : ""}>${esc(item.label)}</option>`).join("")}
            </select>
          </div>
          ${extras.length ? `<div class="condopt-map-fields">${extras.join("")}</div>` : `<p class="hint">该列不参与导入（会被忽略）。</p>`}
        </div>`;
    }).join("")}</div>`;
  }

  function field(label, name, value, column, type) {
    return `<label class="condopt-field"><span>${esc(label)}</span>
      <input class="input" type="${type || "text"}" data-map-field="${esc(name)}" data-column="${esc(column)}"
             value="${value == null ? "" : esc(value)}"></label>`;
  }

  /* ---------------------------------------------------------- 渲染 · 右栏（模型建议 / 结果 / 子参数） */

  // 左栏也在弹窗之前把真值摆出来：用户不用点开弹窗就知道「这次会发多少」。
  function previewLine() {
    const preview = (S.scan && S.scan.sample_preview) || null;
    if (!preview) {
      return `<p class="hint">本次将发送 ≈ ${fmtBytes(estimateSendBytes(S.scan, clampSampleRows(S.sampleRows)))}
        （前端估算：这个后端没有返回 <code>sample_preview</code> 真值）。</p>`;
    }
    const stale = S.previewFor && (S.previewFor.sampleRows !== clampSampleRows(S.sampleRows)
      || String(S.previewFor.model || "") !== String(S.model || ""));
    return `<p class="hint">本次将发送：<strong>${esc(String(preview.rows))}</strong> 行 /
      <strong>${fmtBytes(preview.bytes)}</strong>（${esc(String(preview.bytes))} B）· 后端按当前 sample_rows 算出的真值${
      stale ? " —— 参数刚改过，正在重扫刷新…" : ""}</p>`;
  }

  function renderRight() {
    const box = $("condoptImportResult");
    if (!box) return;
    if (!isActive()) { box.hidden = true; box.innerHTML = ""; return; }
    box.hidden = false;

    const parts = [];
    if (S.mock) parts.push(`<p class="condopt-import-warn"><strong>离线演示</strong>：右栏内容来自内置示例，不是你的数据、也不是模型输出。</p>`);
    // 错误也放到右栏：左栏的错误卡可能在滚动区外，用户看不到可行动的按钮。
    if (S.lastError) parts.push(errorCard(S.lastError.stage));

    if (S.suggestion) {
      const sent = S.suggestion.sent || {};
      const confidence = Number(S.suggestion.confidence);
      const percent = Number.isFinite(confidence) ? Math.round(confidence * 100) : null;
      parts.push(`
        <div class="condopt-suggest">
          <div class="condopt-suggest-head">
            <span class="kicker">模型建议</span>
            <span class="badge ${S.suggestion.source === "llm" ? "badge-teal" : ""}">${S.suggestion.source === "llm" ? "模型" : (S.suggestion.source === "rules" ? "规则映射" : esc(S.suggestion.source || "—"))}</span>
            <span class="hint">模型 <code>${esc(S.suggestion.model || "—")}</code>
              · 后端实测发送 ${esc(String(sent.rows ?? "—"))} 行 / ${fmtBytes(sent.bytes)}</span>
          </div>
          <div class="condopt-confidence">
            <span>置信度</span>
            <div class="condopt-confidence-bar"><i style="width:${percent == null ? 0 : Math.max(0, Math.min(100, percent))}%"></i></div>
            <strong>${percent == null ? "—" : percent + "%"}</strong>
          </div>
          <p class="condopt-suggest-rationale">${esc(S.suggestion.rationale || "（模型未给出理由）")}</p>
          ${invariantLine()}
          ${(S.suggestion.warnings || []).length ? listWarnings(S.suggestion.warnings) : ""}
          <div class="condopt-import-row">
            <button class="btn btn-sm btn-accent" type="button" data-import="merge">
              <i data-lucide="git-merge"></i>合并到映射表
            </button>
            <span class="hint">映射是建议不是真源；合并后仍可在左侧逐列微调。</span>
          </div>
        </div>`);
    } else if (S.scan) {
      parts.push(`<p class="condopt-empty-hint">已读到 <strong>${esc(String(S.scan.n_rows ?? "—"))}</strong> 行 / <strong>${esc(String((S.scan.columns || []).length))}</strong> 列。
        在左侧确认列角色后，${S.mock ? "可以查看内置示例建议" : "点「让模型读表…」做二次确认再外发"}。</p>`);
    } else {
      parts.push(`<p class="condopt-empty-hint">先在左侧粘贴文件路径并点「读取路径中的文件」；<br>扫描与规则映射不出网，只有「让模型读表」会在确认后外发样本。</p>`);
    }

    if (S.applied) {
      const imported = S.applied.imported || {};
      const run = S.applied.run || {};
      parts.push(`
        <div class="condopt-imported">
          <div class="condopt-suggest-head">
            <span class="kicker">导入结果</span>
            <span class="hint">study <code>${esc(run.id || "—")}</code> · 算法 <code>${esc(run.algorithm || "—")}</code></span>
          </div>
          <div class="condopt-stats">
            <div class="condopt-stat"><span>接受</span><strong>${esc(String(imported.accepted ?? "—"))}</strong></div>
            <div class="condopt-stat"><span>重复</span><strong>${esc(String(imported.duplicate ?? "—"))}</strong></div>
            <div class="condopt-stat"><span>拒绝</span><strong>${esc(String((imported.rejected || []).length))}</strong></div>
            <div class="condopt-stat"><span>n_obs</span><strong>${esc(String(run.n_obs ?? "—"))}</strong></div>
            <div class="condopt-stat"><span>剩余预算</span><strong>${esc(String(run.remaining ?? "—"))}</strong></div>
          </div>
          ${(S.applied.warnings || []).length ? listWarnings(S.applied.warnings) : ""}
          ${(imported.rejected || []).length ? `
            <details class="condopt-rejected"><summary>${(imported.rejected || []).length} 行被拒（逐行原因，不静默丢）</summary>
              <div class="condopt-table-wrap"><table class="condopt-table">
                <thead><tr><th>行</th><th>原因</th></tr></thead>
                <tbody>${(imported.rejected || []).map((item) => `<tr><td>${esc(String(item.row ?? "—"))}</td><td>${esc(item.reason || "")}</td></tr>`).join("")}</tbody>
              </table></div>
            </details>` : ""}
        </div>`);

      parts.push(`
        <div class="condopt-params">
          <div class="condopt-suggest-head">
            <span class="kicker">算法 + 子参数（来自 registry，代码取值）</span>
            <span class="hint">导入完成后自动展开，不用再点一次</span>
          </div>
          ${paramsForm()}
          <div class="condopt-import-row">
            <button class="btn btn-sm btn-primary" type="button" data-import="next">
              <i data-lucide="play"></i>生成下一批
            </button>
            <span class="hint" data-params-dirty>${S.paramsDirty ? "参数已改动：会先 override 留痕再 ask" : "参数与默认值一致：直接 ask"}</span>
          </div>
          ${errorCard("next")}
        </div>`);
    }

    box.innerHTML = parts.join("");
    refreshIcons();
  }

  // §9.3 不变量的可视化：相等就明说「两个数字相等」；不等就红色打违约（后端 map.warnings 里
  // 也会有一条差异提示，两条都会显示，不互相替代）。
  function invariantLine() {
    const item = S.invariant;
    if (!item) return "";
    if (!item.sameParams) {
      return `<p class="condopt-invariant is-stale">不变量未比对：本次 <code>sample_rows</code>/模型与 scan 预告时不同，预览可能是旧的（已 <code>console.warn</code>）。</p>`;
    }
    if (item.ok) {
      return `<p class="condopt-invariant is-ok">不变量成立：scan 预告 <strong>${fmtBytes(item.previewBytes)}</strong>（${item.previewBytes} B）
        = map 实发 <strong>${fmtBytes(item.sentBytes)}</strong>（${item.sentBytes} B）· 行数 ${item.previewRows} = ${item.sentRows}</p>`;
    }
    return `<p class="condopt-invariant is-bad">⚠ 不变量违约：scan 预告 <strong>${item.previewBytes} B</strong>
      ≠ map 实发 <strong>${item.sentBytes} B</strong>（同文件同 sample_rows 必须相等）。这不是估算差异，是 bug；已 <code>console.warn</code>。</p>`;
  }

  function paramsForm() {
    const schema = S.paramsSchema || [];
    if (!schema.length) return `<p class="hint">后端没有返回 params_schema。</p>`;
    return `<div class="condopt-params-grid">${schema.map((item) => {
      const value = S.params[item.name];
      const label = `<span title="${esc(item.description || "")}">${esc(item.name)}</span>`;
      if (item.type === "choice" && Array.isArray(item.choices)) {
        return `<label class="condopt-field">${label}
          <select class="input" data-param="${esc(item.name)}">
            ${item.choices.map((choice) => `<option value="${esc(choice)}" ${String(value) === String(choice) ? "selected" : ""}>${esc(choice)}</option>`).join("")}
          </select></label>`;
      }
      if (item.type === "bool" || item.type === "boolean") {
        return `<label class="condopt-field condopt-field-bool">${label}
          <input type="checkbox" data-param="${esc(item.name)}" ${value ? "checked" : ""}></label>`;
      }
      const type = (item.type === "int" || item.type === "integer") ? "number" : (["float", "number"].includes(item.type) ? "number" : "text");
      const step = type === "number" ? ' step="any"' : "";
      return `<label class="condopt-field">${label}
        <input class="input" type="${type}"${step} data-param="${esc(item.name)}" value="${value == null ? "" : esc(value)}"></label>`;
    }).join("")}</div>
    <p class="hint">hover 参数名可看 registry 里的说明；改动会在「生成下一批」时先 override 留痕。</p>`;
  }

  /* ---------------------------------------------------------- 外发二次确认弹窗（§9.1） */

  // 改 sample_rows / model 后必须重取预览（「同意」要对着真值）。400ms 去抖；
  // 重扫走 previewOnly，**不动用户改过的映射**（除非列名集合变了）。
  let previewTimer = null;
  function refreshPreview() {
    if (S.mock || !S.scan) return;
    clearTimeout(previewTimer);
    previewTimer = setTimeout(() => {
      previewTimer = null;
      doScan({ previewOnly: true });
    }, 400);
  }

  function estimateSendBytes(scan, rows) {
    const columns = (scan.columns || []).map((column) => ({
      name: column.name,
      inferred: column.inferred,
      samples: (column.samples || []).slice(0, rows),
    }));
    const payload = { header: columns.map((column) => column.name), columns, rows };
    try {
      return new Blob([JSON.stringify(payload)]).size;
    } catch (error) {
      return JSON.stringify(payload).length;
    }
  }

  function ensureDialog() {
    let dialog = $("condoptImportConsent");
    if (dialog) return dialog;
    dialog = document.createElement("dialog");
    dialog.id = "condoptImportConsent";
    dialog.className = "condopt-consent";
    document.body.appendChild(dialog);
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
      const button = event.target.closest("[data-consent]");
      if (!button) return;
      const value = button.dataset.consent;
      dialog.close();
      if (value === "send") confirmSend();
      else if (value === "rulesmap") mapWithRules();   // 地址不合法时的一键出路（不出网）
    });
    return dialog;
  }

  function openConsent() {
    if (!S.scan) { toast("先读取文件"); return; }
    const info = llmInfo();
    const rows = clampSampleRows(S.sampleRows);
    S.sampleRows = rows;
    const fileBytes = (S.scan.file && S.scan.file.bytes) || 0;

    // 发送量的取值优先级（契约 §9.3 `sample_preview` + §9.5）：
    //   1) 后端 map 实测 `sent{rows,bytes}`（已经跑过一次 map 时）
    //   2) 后端 scan 预告 `sample_preview{rows,bytes}` —— **真值**：与 map 同一条请求体构造路径
    //   3) 前端估算（老后端 / 无该字段时），此时必须明确标注「估算」
    // 只有当 1)/2) 都没有时，才退回 3) 并保留措辞；有真值就绝不写「估算」。
    const sent = (S.suggestion && S.suggestion.sent) || null;
    const preview = (S.scan.sample_preview && typeof S.scan.sample_preview === "object") ? S.scan.sample_preview : null;
    const positive = (value) => (Number.isFinite(Number(value)) && Number(value) > 0 ? Number(value) : null);

    let shownRows = null;
    let shownBytes = null;
    let rowsNote = "";
    let bytesNote = "";
    if (sent && positive(sent.rows) && positive(sent.bytes)) {
      shownRows = positive(sent.rows);
      shownBytes = positive(sent.bytes);
      rowsNote = "（后端实测值）";
      bytesNote = "（后端实测值）";
    } else if (preview && positive(preview.rows) && positive(preview.bytes)) {
      shownRows = positive(preview.rows);
      shownBytes = positive(preview.bytes);
      rowsNote = "（后端预告真值 · 与实际发送同一条构造路径）";
      bytesNote = "（后端预告真值 · scan 时按当前 sample_rows 算出）";
    } else {
      shownRows = rows;
      shownBytes = estimateSendBytes(S.scan, rows);
      rowsNote = "（本次 sample_rows；上限 " + MAX_SAMPLE_ROWS + "）";
      bytesNote = "（前端按表头+画像+样本估算，仅预览）";
    }
    // 预览是否与当前参数一致：改过 sample_rows/model 但重扫还没回来时，别把旧真值当成新参数的真值
    const previewStale = preview && S.previewFor
      && (S.previewFor.sampleRows !== rows || String(S.previewFor.model || "") !== String(S.model || ""));

    const dialog = ensureDialog();
    const readiness = llmReadiness();
    const channel = typeof info.enabled === "boolean"
      ? (info.enabled ? "已开启（--import-llm）" : "未开启（缺 --import-llm）")
      : "后端未告知";
    const keyState = typeof info.configured === "boolean"
      ? (info.configured ? "已就绪" : "未配置")
      : "后端未告知";
    // 实际 POST 的是 `{baseURL}/chat/completions`（§9.1）。优先级：
    //   1) 后端 map 实测 `sent.url`
    //   2) 后端 scan 预告 `sample_preview.endpoint`（就是实际生效的 chat/completions，且含路径）
    //   3) 由 `import_llm.url` 按 §9.1 约定推导（老后端；此时明确标注「推导」）
    // F7/F8：地址不合法时后端把 `sample_preview.endpoint` 给成**空串**——
    // 此时**绝不回退**到我自己拼的假端点，如实显示「地址未配置/不合法」。
    const urlBlocked = Boolean(urlProblem());
    const previewHasField = preview !== null;
    const endpointFromSent = sent && sent.url ? String(sent.url) : "";
    const endpointFromPreview = preview && preview.endpoint ? String(preview.endpoint) : "";
    const endpointUnknown = urlBlocked || (previewHasField && !endpointFromPreview && !endpointFromSent);
    const endpointDerived = (!urlBlocked && info.url) ? String(info.url).replace(/\/+$/, "") + "/chat/completions" : "";
    const endpoint = endpointUnknown ? "" : (endpointFromSent || endpointFromPreview || endpointDerived);
    const endpointNote = endpointFromSent
      ? "（后端上一次 map 的实测端点。）"
      : (endpointFromPreview
        ? "（后端 scan 预告的实际生效端点。）"
        : (endpointDerived
          ? `<p class="hint">（由 <code>${esc(info.url)}</code> 按 §9.1 的 <code>POST {baseURL}/chat/completions</code> 推导；后端返回 <code>sample_preview.endpoint</code> 后会直接用它。）</p>`
          : ""));
    const urlWarning = urlBlocked
      ? `<p class="condopt-import-error"><strong>地址不合法，不能外发</strong>：${esc(urlProblem().message)}${
        info.urlError ? `<br>后端原文 <code>url_error</code>：${esc(info.urlError)}` : ""}
        ${info.url ? `<br>当前配置的 URL：<code>${esc(info.url)}</code>` : ""}</p>`
      : (endpointUnknown && !S.mock
        ? `<p class="condopt-import-problems">后端没有给出可用的目标地址（<code>sample_preview.endpoint</code> 为空串），因此这里不显示任何 URL——不会外发。</p>`
        : "");
    dialog.innerHTML = `
      <div class="dialog-head">
        <div>
          <span class="kicker">唯一会出网的一步</span>
          <h2>确认把样本发给模型？</h2>
        </div>
        <button class="btn btn-ghost btn-icon" data-consent="cancel" aria-label="取消"><i data-lucide="x"></i></button>
      </div>
      <div class="dialog-body">
        <div class="condopt-consent-grid">
          <div class="condopt-consent-row"><span>目标 URL</span><code>${
            endpoint ? esc(endpoint)
              : (S.mock ? "离线演示：不会外发"
                : (endpointUnknown ? "地址未配置/不合法（不会外发）" : "后端未提供（§2.1 import_llm.url 缺失）"))
          }</code></div>
          ${endpointNote && !endpointNote.startsWith("<p") ? `<p class="hint">${esc(endpointNote)}</p>` : endpointNote}
          <div class="condopt-consent-row"><span>模型 id</span><code>${esc(S.model || info.model || "（后端默认）")}</code></div>
          <div class="condopt-consent-row"><span>通道状态</span><span>模型通道 ${esc(channel)} · 密钥（<code>${esc(info.keyEnv || "未告知")}</code>）${esc(keyState)}</span></div>
          <div class="condopt-consent-row"><span>将发送的行数</span><strong>${shownRows}</strong>
            <span class="hint">${esc(rowsNote)}</span></div>
          <div class="condopt-consent-row"><span>将发送的字节数</span><strong>${shownBytes != null && bytesNote.includes("估算") ? "≈ " + fmtBytesExact(shownBytes) : fmtBytesExact(shownBytes)}</strong>
            <span class="hint">${esc(bytesNote)}</span></div>
          <div class="condopt-consent-row"><span>文件本身</span><span class="hint">共 ${fmtBytes(fileBytes)} —— <strong>不发整文件</strong>，只发表头 + 列画像 + 前 ${rows} 行样本</span></div>
        </div>
        ${urlWarning}
        ${previewStale ? `<p class="condopt-import-problems">注意：你刚改过 sample_rows 或模型 id，<strong>预览还没按新参数刷新</strong>（后台正在重扫）。先点「取消」，等左侧提示刷新完成后再确认，才会是基于真值的同意。</p>` : ""}
        ${readiness && readiness.blocked && !urlBlocked ? `<p class="condopt-import-problems">注意：${esc(readiness.message)}可以取消后改走离线规则映射（不出网）。</p>` : ""}
        ${urlBlocked ? `<div class="condopt-import-row">
          <button class="btn btn-sm btn-ghost" data-consent="rulesmap" id="condoptConsentRules">
            <i data-lucide="shield-off"></i>改走离线规则映射（不出网）
          </button>
        </div>` : ""}
        <p class="hint">密钥只从环境变量读取，不写盘、不回显、不进日志。取消即不发送（不会有任何静默降级）。</p>
        <label class="condopt-consent-check">
          <input type="checkbox" id="condoptConsentCheck" ${urlBlocked ? "disabled" : ""}>
          <span>我了解上面这些样本会被发送到上述 URL，用于判断列角色。</span>
        </label>
        <div class="condopt-import-row">
          <button class="btn btn-accent btn-sm" data-consent="${urlBlocked ? "cancel" : "send"}" id="condoptConsentSend" disabled>
            <i data-lucide="${urlBlocked ? "ban" : "cloud-upload"}"></i>${urlBlocked ? "地址不合法，无法发送" : `确认并发送 ${rows} 行`}
          </button>
          <button class="btn btn-ghost btn-sm" data-consent="cancel">取消</button>
        </div>
      </div>`;
    refreshIcons();
    const check = $("condoptConsentCheck");
    const send = $("condoptConsentSend");
    // 地址不合法时勾选框是 disabled、发送按钮已被换上 data-consent="cancel"（点了只关弹窗），
    // 这里再兜一层：blocked 时任何情况下都不允许把发送按钮解禁。
    if (check && send) check.addEventListener("change", () => { send.disabled = urlBlocked || !check.checked; });
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  /* ---------------------------------------------------------- 流程动作 */

  function fail(stage, error) {
    let extra = "";
    let suggestedBudget = null;
    let code = error.code || "E_INTERNAL";
    const message = error.message || "未知错误";
    const hint = error.hint || "";

    if (code === "E_LLM_UNAVAILABLE") {
      const blob = hint + " " + message;
      const envMatch = blob.match(/\b[A-Z][A-Z0-9_]{3,}\b/);
      const info = llmInfo();
      const keyEnv = info.keyEnv || (envMatch ? envMatch[0] : "");
      const mentionsFlag = blob.includes("--import-llm");
      if (mentionsFlag && keyEnv) {
        extra = `后端未开启模型通道：启动命令需要 --import-llm，并且要配置密钥环境变量 \`${keyEnv}\`（只从环境变量读，不写盘、不回显）。`;
      } else if (mentionsFlag) {
        extra = "后端未开启模型通道：启动命令加 `--import-llm` 才会允许外发。";
      } else if (keyEnv) {
        extra = `模型通道缺少密钥：环境变量 \`${keyEnv}\` 没有配置（只从环境变量读，不会回显）。`;
      } else {
        extra = "后端未说明具体缺什么：可能是缺 `--import-llm` 开关，或缺少密钥环境变量；可改走离线规则映射。";
      }
    } else if (code === "E_CONSENT_REQUIRED") {
      extra = "这一步必须有显式的二次确认（consent:true）；请从「让模型读表…」按钮进入确认弹窗。";
    } else if (code === "E_LLM_BAD_RESPONSE") {
      extra = "模型的响应不是合法 JSON（后端已做一次只修格式的重试）。可以重试，或改走离线规则映射。";
    } else if (code === "E_VALIDATION") {
      const match = (hint + " " + message).match(/(\d+)/);
      const raw = error.raw || {};
      // §9.5（2026-11-20 冻结）：主路径读 `suggested_budget`（数字）；另两个是向后兼容兜底。
      const explicit = Number(raw.suggested_budget);
      const camel = Number(raw.suggestedBudget);
      suggestedBudget = (Number.isFinite(explicit) && explicit > 0) ? explicit
        : ((Number.isFinite(camel) && camel > 0) ? camel : (match ? Number(match[1]) : null));
      if (suggestedBudget != null) {
        extra = `budget 必须 ≥ 已接受行数：建议 budget=${suggestedBudget}（不会静默放大）。`;
      }
    }

    S.lastError = { stage, code, message, hint, extra, suggestedBudget };
    setStage(stage === "apply" ? 3 : (stage === "map" ? 2 : 0), 3);
    renderLeft();
    renderRight();
    toast(code + "：" + message);
  }

  function clearError() { S.lastError = null; }

  // previewOnly=true：只刷新预览（改 sample_rows / model 后重扫），**保留用户改过的映射**；
  // 只有「列名集合变了」（换文件/换路径）才回退到新的 rule_mapping 全量重置。
  async function doScan(options = {}) {
    if (S.busy) return;
    const previewOnly = Boolean(options.previewOnly);
    const path = (($("input") || {}).value || "").trim();
    if (!path) { toast("先粘贴一个文件路径"); if ($("input")) $("input").focus(); return; }
    if (!previewOnly) clearError();
    S.busy = true;
    if (!previewOnly) setStage(0, 1);
    try {
      // §9.3：sample_rows 与 model 必须与随后 map 同参，否则 sample_preview.bytes 不等于 sent.bytes。
      const body = { path, sample_rows: clampSampleRows(S.sampleRows) };
      if (S.model) body.model = S.model;
      const data = await postJson(ENDPOINTS.scan, body);
      const previousColumns = ((S.scan && S.scan.columns) || []).map((column) => column.name).join("\u0000");
      const nextColumns = ((data.columns) || []).map((column) => column.name).join("\u0000");
      const columnsChanged = previousColumns !== nextColumns;
      S.mock = false;
      S.scan = data;
      S.previewFor = { sampleRows: body.sample_rows, model: S.model || "" };
      S.invariant = null;
      if (!previewOnly || columnsChanged || !S.mapping) {
        S.mapping = normalizeMapping(data.rule_mapping);
      }
      if (!previewOnly) {
        S.suggestion = null;
        S.applied = null;
        S.paramsSchema = [];
        S.importId = defaultImportId(data);
        S.budget = defaultBudget(data);
      }
      setStage(0, 2);
      setStage(1, 2);
      if (previewOnly) {
        toast(columnsChanged
          ? "列集合变化：已用新的规则映射重置确认表"
          : `已按 sample_rows=${body.sample_rows} 重新取预览（你的映射改动保留）`);
      } else {
        toast(`已读取 ${data.n_rows} 行 / ${(data.columns || []).length} 列`);
      }
    } catch (error) {
      // 后端不可用（网络层失败 / 404 / 405）→ 走离线演示；后端明确报错（4xx/5xx 带 error 码）→ 如实报错。
      if (!error.code || error.code.startsWith("HTTP_")) {
        if (previewOnly) {
          // 重扫失败不清空用户已有的 scan；只提示这次预览没刷新
          toast("后端不可用：预览未能刷新（保留原值）");
        } else {
          S.mock = true;
          S.mappingRevision = null;
          S.scan = demoScan(path);
          S.previewFor = null;
          S.mapping = normalizeMapping(S.scan.rule_mapping);
          S.suggestion = null;
          S.applied = null;
          S.paramsSchema = [];
          S.importId = "import-offline-demo";
          S.budget = 120;
          setStage(0, 2);
          setStage(1, 2);
          toast("后端不可用：已切换到离线演示（不读文件、不外发）");
        }
      } else {
        S.mock = false;
        fail("scan", error);
      }
    } finally {
      S.busy = false;
      renderLeft();
      renderRight();
      syncRunLabel();
    }
  }

  function defaultImportId(scan) {
    const path = (scan.file && scan.file.path) || "data";
    const base = String(path).split(/[\\/]/).pop().replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_-]+/g, "-").slice(0, 24) || "data";
    return `import-${base}`;
  }

  function defaultBudget(scan) {
    const rows = Number(scan.n_rows) || 0;
    const batch = Math.max(1, Number(S.batch) || 4);
    return Math.max(rows + batch * 4, rows + 4);
  }

  function openConsentOrMock() {
    if (S.mock) { mockMap(); return; }
    openConsent();
  }

  async function confirmSend() {
    if (S.busy || !S.scan) return;
    clearError();
    S.busy = true;
    setStage(2, 1);
    const requestedRows = clampSampleRows(S.sampleRows);
    const requestedModel = S.model || "";
    try {
      const data = await postJson(ENDPOINTS.map, {
        scan_id: S.scan.scan_id,
        consent: true,
        sample_rows: requestedRows,
        model: requestedModel || undefined,
        fallback: "llm",
      });
      if (data.model) S.model = data.model;
      S.suggestion = data;
      // §9.3 硬不变量双向核对：scan.sample_preview.bytes 必须 == map.sent.bytes。
      // 不相等就是**不变量违约**（不是「估算差异」）——显式打出来 + console.warn，绝不静默吞掉。
      // 只有在「预览确实对应本次参数」时才比对，否则说明预览是旧的，不能拿它判违约。
      S.invariant = checkInvariant(data, requestedRows, requestedModel);
      setStage(2, 2);
      toast(`模型建议已返回（置信度 ${Math.round((Number(data.confidence) || 0) * 100)}%）`);
    } catch (error) {
      fail("map", error);
    } finally {
      S.busy = false;
      renderLeft();
      renderRight();
      syncRunLabel();
    }
  }

  function checkInvariant(mapResponse, requestedRows, requestedModel) {
    const preview = (S.scan && S.scan.sample_preview) || null;
    const sent = (mapResponse && mapResponse.sent) || null;
    if (!preview || !sent) return null; // 老后端 or 规则路径：没有可比的两边
    const previewBytes = Number(preview.bytes);
    const sentBytes = Number(sent.bytes);
    if (!Number.isFinite(previewBytes) || !Number.isFinite(sentBytes)) return null;
    const previewFor = S.previewFor;
    const matched = !previewFor
      || (previewFor.sampleRows === requestedRows && String(previewFor.model || "") === String(requestedModel || ""));
    const result = {
      previewBytes,
      sentBytes,
      previewRows: Number(preview.rows),
      sentRows: Number(sent.rows),
      sameParams: matched,
      ok: previewBytes === sentBytes,
    };
    if (!matched) {
      console.warn("[condopt] 不变量未比对：本次参数 (sample_rows=" + requestedRows + ", model=" + (requestedModel || "默认")
        + ") 与 scan 预告的参数不一致，预览可能是旧的", result);
      return result;
    }
    if (!result.ok) {
      console.warn("[condopt] 不变量违约：scan.sample_preview.bytes=" + previewBytes
        + " ≠ map.sent.bytes=" + sentBytes + "（同文件同 sample_rows 必须相等）", result);
    }
    return result;
  }

  // E_LLM_UNAVAILABLE 的一键出路：显式改走离线规则映射（不出网）。
  async function mapWithRules() {
    if (S.busy || !S.scan) return;
    clearError();
    S.busy = true;
    setStage(2, 1);
    try {
      const data = await postJson(ENDPOINTS.map, {
        scan_id: S.scan.scan_id,
        consent: false,          // 规则映射不出网，所以没有 consent 可给
        sample_rows: clampSampleRows(S.sampleRows),
        fallback: "rules",
      });
      S.suggestion = data;
      setStage(2, 2);
      toast("已改走离线规则映射（未外发任何数据）");
    } catch (error) {
      fail("map", error);
    } finally {
      S.busy = false;
      renderLeft();
      renderRight();
      syncRunLabel();
    }
  }

  function mockMap() {
    if (!S.scan) return;
    S.suggestion = demoMap(S.scan);
    S.mockStage = 1;
    setStage(2, 2);
    renderLeft();
    renderRight();
    syncRunLabel();
    toast("离线演示：内置示例建议（未调用模型、未外发）");
  }

  function mergeSuggestion() {
    if (!S.suggestion || !S.suggestion.mapping) return;
    const incoming = normalizeMapping(S.suggestion.mapping);
    const merged = emptyMapping();
    // 以模型建议为骨架，保留用户已经手改过、且模型没提到的列的角色
    merged.variables = incoming.variables.slice();
    merged.objective = incoming.objective;
    merged.constraints = incoming.constraints.slice();
    merged.fidelity = incoming.fidelity;
    merged.cost = incoming.cost;
    const mine = S.mapping || emptyMapping();
    (mine.variables || []).forEach((item) => {
      if (!merged.variables.some((entry) => entry.column === item.column)
        && roleOf(item.column) === "variable" && item.column !== (merged.objective && merged.objective.column)) {
        merged.variables.push(item);
      }
    });
    S.mapping = merged;
    renderLeft();
    renderRight();
    toast("已把模型建议合并到映射表，可继续逐列微调");
  }

  async function doApply() {
    if (S.busy || !S.scan) return;
    const problems = mappingProblems();
    if (problems.length) { toast(problems[0]); return; }
    if (S.mock) { applyMock(); return; }
    clearError();
    S.busy = true;
    setStage(3, 1);
    const payload = {
      id: S.importId || defaultImportId(S.scan),
      path: (($("input") || {}).value || "").trim(),
      scan_id: S.scan.scan_id,
      mapping: S.mapping,
      algorithm: S.algorithm || "auto",
      params: {},
      budget: Number(S.budget) || (S.scan.n_rows || 0) + 4,
      batch: Math.max(1, Number(S.batch) || 4),
      reason: S.reason || "导入历史数据作为先验",
      // 契约 §9.5（2026-11-20 裁定）：count_toward_budget 不是可选项 —— condopt 的
      // import_observations 无法关闭「先验计入 budget」。UI 不给假选择，恒发 true。
      count_toward_budget: true,
    };
    try {
      const data = await postJson(ENDPOINTS.apply, payload);
      S.applied = data;
      S.paramsSchema = Array.isArray(data.params_schema) ? data.params_schema : [];
      S.params = {};
      S.paramsSchema.forEach((item) => { S.params[item.name] = item.default; });
      S.paramsDirty = false;
      setStage(3, 2);
      toast(`已导入：接受 ${((data.imported || {}).accepted ?? "—")} 条`);
      // 让 study 列表与 runId 跟上（这样「生成下一批」和侧栏都指向新 study）
      if (window.CONDOPT) {
        const runId = (data.run || {}).id;
        if (runId) {
          CONDOPT.state.currentRun = runId;
          if (window.EduKit && EduKit.state) EduKit.state.runId = runId;
        }
        CONDOPT.refreshRuns({ quiet: true });
      }
      // 人工拍板留痕（复用 condopt.js 的复核区，不复制一份渲染）
      if (window.CONDOPT && typeof CONDOPT.openReviewPanel === "function") CONDOPT.openReviewPanel();
    } catch (error) {
      fail("apply", error);
    } finally {
      S.busy = false;
      renderLeft();
      renderRight();
      syncRunLabel();
    }
  }

  function applyMock() {
    const data = demoApply();
    S.applied = data;
    S.paramsSchema = data.params_schema.slice();
    S.params = {};
    S.paramsSchema.forEach((item) => { S.params[item.name] = item.default; });
    S.paramsDirty = false;
    S.mockStage = 2;
    setStage(3, 2);
    renderLeft();
    renderRight();
    syncRunLabel();
    toast("离线演示：内置示例导入结果（没有写盘）");
  }

  async function nextBatch() {
    const run = (S.applied && S.applied.run) || {};
    if (S.mock) { toast("离线演示：生成下一批需要后端"); return; }
    if (!run.id) { toast("还没有可用的 study"); return; }
    clearError();
    S.busy = true;
    try {
      // 契约 §3：run 走请求顶层，payload 只放该 action 自己的字段
      if (S.paramsDirty) {
        await postAction("override", run.id, {
          algorithm: S.algorithm === "auto" ? (run.algorithm || "auto") : S.algorithm,
          reason: "导入数据后手动调整子参数",
          params: S.params,
        });
      }
      const asked = await postAction("ask", run.id, { n: Math.max(1, Number(S.batch) || 4) });
      const pending = (asked.result && asked.result.pending) || [];
      toast(`已生成下一批 ${pending.length} 条待测条件`);
      if (window.CONDOPT) CONDOPT.refreshRuns({ quiet: true });
      const box = $("condoptImportResult");
      if (box) {
        box.insertAdjacentHTML("beforeend", `
          <div class="condopt-imported">
            <div class="condopt-suggest-head"><span class="kicker">下一批</span>
              <span class="hint">${esc((asked.message) || "")}</span></div>
            <div class="condopt-table-wrap"><table class="condopt-table">
              <thead><tr><th>id</th><th>plate</th><th>well</th><th>batch</th></tr></thead>
              <tbody>${pending.map((item) => `<tr><td><code>${esc(item.id)}</code></td><td>${esc(item.plate || "—")}</td><td>${esc(item.well || "—")}</td><td>${esc(String(item.batch ?? "—"))}</td></tr>`).join("")}</tbody>
            </table></div>
            <div class="condopt-import-row">
              <button class="btn btn-sm btn-ghost" type="button" data-import="gosample">
                <i data-lucide="arrow-right"></i>去「下一批取样」场景看待测表
              </button>
            </div>
          </div>`);
        refreshIcons();
      }
    } catch (error) {
      fail("next", error);
    } finally {
      S.busy = false;
    }
  }

  async function postAction(action, run, payload) {
    const response = await fetch("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, run: run == null ? null : run, payload: payload || {} }),
    });
    let data = null;
    try { data = await response.json(); } catch (error) { data = null; }
    if (!response.ok || !data || data.ok === false) {
      const error = new Error((data && data.message) || ("HTTP " + response.status));
      error.code = (data && data.error) || ("HTTP_" + response.status);
      error.hint = (data && data.hint) || "";
      error.raw = data;
      throw error;
    }
    return data;
  }

  function goToScenario(key) {
    const button = document.querySelector(`#scenarioList [data-key="${key}"]`);
    if (button) button.click();
  }

  /* ---------------------------------------------------------- 主按钮文案（“开始处理”＝下一步） */

  function syncRunLabel() {
    if (!isActive()) return;
    const label = $("runBtnLabel");
    if (!label) return;
    let text = "下一步：读取文件";
    if (S.scan && !S.suggestion && !S.applied) text = S.mock ? "查看内置示例建议" : "下一步：让模型读表";
    else if (S.suggestion && !S.applied) text = "下一步：导入为先验";
    else if (S.applied) text = "已导入 · 可点「生成下一批」";
    label.textContent = text;
  }

  async function advance() {
    if (!isActive()) return;
    if (!S.scan) { await doScan(); return; }
    if (!S.suggestion) { openConsentOrMock(); return; }
    if (!S.applied) { await doApply(); return; }
    toast("已导入；请点右侧「生成下一批」");
  }

  /* ---------------------------------------------------------- 事件绑定 */

  // 左栏与右栏共用同一套动作分发：错误卡在两边都能用（否则右栏里的按钮点了没反应）。
  function handleAction(action, node) {
    if (action === "scan") doScan();
    else if (action === "consent") openConsent();
    else if (action === "rulesmap") mapWithRules();
    else if (action === "mockmap") mockMap();
    else if (action === "merge") mergeSuggestion();
    else if (action === "apply") doApply();
    else if (action === "next") nextBatch();
    else if (action === "gosample") goToScenario("sample");
    else if (action === "rules") {
      S.mapping = normalizeMapping(S.scan && S.scan.rule_mapping);
      clearError();
      renderLeft();
      renderRight();
      toast("已按规则映射重置");
    } else if (action === "usebudget") {
      S.budget = Number(node && node.dataset.value) || S.budget;
      clearError();
      renderLeft();
      renderRight();
      toast("已填入建议 budget");
    }
  }

  function bind() {
    const left = $("condoptImport");
    if (left) {
      left.addEventListener("click", (event) => {
        const button = event.target.closest("[data-import]");
        if (button) handleAction(button.dataset.import, button);
      });
      left.addEventListener("change", (event) => {
        const levels = event.target.closest("[data-map-levels]");
        if (levels) { updateFidelityLevels(levels); return; }
        const role = event.target.closest("[data-map-role]");
        if (role) { setRole(role.dataset.mapRole, role.value); renderLeft(); return; }
        const mapField = event.target.closest("[data-map-field]");
        if (mapField) { updateMapField(mapField); return; }
        const fieldNode = event.target.closest("[data-import-field]");
        if (fieldNode) updateImportField(fieldNode);
      });
      left.addEventListener("input", (event) => {
        const levels = event.target.closest("[data-map-levels]");
        if (levels) { updateFidelityLevels(levels); return; }
        const mapField = event.target.closest("[data-map-field]");
        if (mapField) { updateMapField(mapField); return; }
        const fieldNode = event.target.closest("[data-import-field]");
        if (fieldNode) updateImportField(fieldNode);
      });
    }
    const right = $("condoptImportResult");
    if (right) {
      right.addEventListener("click", (event) => {
        const button = event.target.closest("[data-import]");
        if (button) handleAction(button.dataset.import, button);
      });
      right.addEventListener("change", (event) => {
        const param = event.target.closest("[data-param]");
        if (param) updateParam(param);
      });
      right.addEventListener("input", (event) => {
        const param = event.target.closest("[data-param]");
        if (param) updateParam(param);
      });
    }
  }

  function updateImportField(node) {
    const name = node.dataset.importField;
    if (name === "batch") S.batch = Number(node.value) || 1;
    else if (name === "sampleRows") { S.sampleRows = clampSampleRows(node.value); refreshPreview(); }
    else if (name === "model") { S.model = node.value; refreshPreview(); }
    else S[name] = node.value;
    if (name === "batch" && S.scan && !S.budget) S.budget = defaultBudget(S.scan);
  }

  function updateMapField(node) {
    const column = node.dataset.column;
    const name = node.dataset.mapField;
    const mapping = S.mapping || (S.mapping = emptyMapping());
    const numeric = node.type === "number";
    const value = numeric ? (node.value === "" ? null : Number(node.value)) : node.value;
    const variable = (mapping.variables || []).find((item) => item.column === column);
    if (variable) { variable[name] = value; if (name === "name" && !value) variable.name = column; return; }
    if (mapping.objective && mapping.objective.column === column) { mapping.objective[name] = value; return; }
    const constraint = (mapping.constraints || []).find((item) => item.column === column);
    if (constraint) { constraint[name] = value; return; }
    if (mapping.fidelity && mapping.fidelity.column === column) { mapping.fidelity[name] = value; return; }
    if (mapping.cost && mapping.cost.column === column) { mapping.cost[name] = value; }
  }

  function updateFidelityLevels(node) {
    const column = node.dataset.mapLevels;
    if (!S.mapping || !S.mapping.fidelity || S.mapping.fidelity.column !== column) return;
    const levels = parseLevels(node.value);
    // 空输入就删掉 levels（§9.4：levels 是可选字段，不发空对象）
    if (Object.keys(levels).length) S.mapping.fidelity.levels = levels;
    else delete S.mapping.fidelity.levels;
  }

  function updateParam(node) {
    const name = node.dataset.param;
    const schema = (S.paramsSchema || []).find((item) => item.name === name);
    if (!schema) return;
    let value = node.type === "checkbox" ? node.checked : node.value;
    if (node.type === "number") value = node.value === "" ? null : Number(node.value);
    S.params[name] = value;
    S.paramsDirty = (S.paramsSchema || []).some((item) => String(S.params[item.name]) !== String(item.default));
    const hint = document.querySelector("[data-params-dirty]");
    if (hint) hint.textContent = S.paramsDirty ? "参数已改动：会先 override 留痕再 ask" : "参数与默认值一致：直接 ask";
  }

  /* ---------------------------------------------------------- 场景切换同步 */

  // /api/bootstrap 比用户手速慢时（冷启动/慢后端），import 面板首次渲染会拿不到
  // import_llm 与算法清单。这里等一次它到位再补渲染，避免「面板停在旧状态」。
  let bootPoll = null;
  function ensureBootstrapRendered() {
    if (!isActive() || bootPoll) return;
    const ready = () => Boolean(window.CONDOPT && window.CONDOPT.state && window.CONDOPT.state.boot);
    if (ready()) return;
    let tries = 0;
    bootPoll = setInterval(() => {
      tries += 1;
      if (ready() || tries > 25) {
        clearInterval(bootPoll);
        bootPoll = null;
        if (ready() && isActive()) syncScenario();
      }
    }, 400);
  }

  function syncScenario() {
    const active = isActive();
    const box = $("condoptImport");
    if (!active) {
      if (box) { box.hidden = true; box.innerHTML = ""; }
      const result = $("condoptImportResult");
      if (result) { result.hidden = true; result.innerHTML = ""; }
      return;
    }
    ensureBootstrapRendered();
    // 新场景：初始化默认 id/budget（等 scan 回来再按行数算）
    if (!S.scan && !S.importId) S.importId = "";
    // 算法清单来自 /api/bootstrap（与开工表单同源）
    const boot = (window.CONDOPT && window.CONDOPT.state && window.CONDOPT.state.boot) || {};
    S.algorithms = (boot.condopt && boot.condopt.algorithms) || S.algorithms || [];
    const info = llmInfo();
    if (!S.model && info.model) S.model = info.model;
    const empty = document.querySelector(".stream .empty");
    if (empty) empty.hidden = true;   // import 用右栏自己的结果区，隐藏上游空状态
    renderStages();
    renderLeft();
    renderRight();
    syncRunLabel();
  }

  function watch() {
    const meta = $("scenarioMeta");
    if (meta) new MutationObserver(() => syncScenario()).observe(meta, { childList: true, characterData: true, subtree: true });
    // 上游「开始处理」在场景切换后会重置空状态，这里补一次
    const stream = $("stream");
    if (stream) new MutationObserver(() => {
      if (isActive()) {
        const empty = document.querySelector(".stream .empty");
        if (empty && !empty.hidden) empty.hidden = true;
      }
    }).observe(stream, { attributes: true, attributeFilter: ["hidden"], subtree: true });
  }

  /* ---------------------------------------------------------- 启动 */

  window.CONDOPT_IMPORT = {
    handlesRun: (key) => key === "import",
    runScenario: () => advance(),
    state: S,
    scan: doScan,
    openConsent,
    apply: doApply,
  };

  bind();
  watch();
  syncScenario();
  console.info("[condopt] import 场景层 ready（CONTRACT §9）");
})();
