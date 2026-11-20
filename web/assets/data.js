/* ============================================================
   condopt 工作台 · 场景数据
   ------------------------------------------------------------
   上游：EduKit 复用包 assets/data.js（换题路径 #1：只改这个文件）。
   本文件把「四个教育场景」换成 condopt 的「五场景闭环」。
   契约：CONTRACT.md §3.1 / §4.1
     key 与顺序固定：kickoff / sample / feedback / delivery / board
     stages 必须与后端 Scenario.stages 一字不差（stage.index 按下标点亮）。
   注意：mock 是【离线演示】文本，不是模型输出，也不代表真实实验结果。
   ============================================================ */

/* mock 文本用 String.raw 包裹（保住 LaTeX 反斜杠），而 String.raw 里不能出现裸反引号
   （会提前结束模板字面量）；行内代码用 ${BT} 插值出一个反引号。 */
const BT = String.fromCharCode(96);

window.EDU_DATA = {
  project: {
    name: "condopt 工作台",
    claim: "算法是硬的，参数是活的，覆写必须留痕",
    kicker: "CONDOPT · EXPERIMENT CONDITION OPTIMIZATION",
  },

  scenarios: [
    /* ---------------------------------------------------------- 01 开工 */
    {
      key: "kickoff",
      no: "01",
      icon: "clipboard-list",
      title: "开工建议",
      tagline: "六段开工建议：算法·停止·可行率·先验·交付口径·噪声处置",
      short: "开工",
      action: "prepare",
      needs_run: false,
      inputLabel: "问卷答案 answers.json",
      placeholder: "填写左侧开工表单（字段来自 GET /api/bootstrap 的 prepare_template），或直接改这里的 JSON。",
      accept: ".json",
      filesHint: "准备阶段不读附件：answers 校验通过后由 prepare 生成 space/context/objective。",
      demoLine: "填问卷 → 校验条件空间 → 路由算法 → 生成六段建议与 problem.json",
      stages: ["校验问卷答案", "解析条件空间", "路由算法", "生成六段建议"],
      outputs: ["六段建议", "路由结果", "problem.json"],
      example: String.raw`{
  "objective": {"name": "yield", "direction": "max"},
  "variables": [
    {"name": "T", "kind": "continuous", "low": 40, "high": 120, "unit": "degC"},
    {"name": "time", "kind": "continuous", "low": 5, "high": 120, "unit": "min"},
    {"name": "conc", "kind": "log", "low": 0.02, "high": 1.0, "unit": "M"},
    {"name": "solvent", "kind": "categorical", "choices": ["MeCN", "EtOH", "water"]},
    {"name": "equiv", "kind": "integer", "low": 1, "high": 4},
    {"name": "inert", "kind": "bool"}
  ],
  "budget": 24,
  "batch": 4,
  "noise": "medium",
  "smoothness": "smooth",
  "domain": "chemistry",
  "prior": 0,
  "notes": "budget 是含先验数据在内的总评价次数"
}`,
      mock: String.raw`## 开工建议 · 离线演示

> **这是离线 mock 文本，不是模型输出，也不包含任何真实实验结论。**
> 真实运行时这六段由 condopt.prepare 的 noise_advice / stopping_advice / feasibility_advice / deliverable_advice / prior_advice 与 router 路由结果拼成。

### 00 条件空间（problem.json）

| 变量 | 类型 | 范围 / 取值 | 单位 |
| --- | --- | --- | --- |
| T | continuous | 40 – 120 | degC |
| time | continuous | 5 – 120 | min |
| conc | log | 0.02 – 1.0（跨数量级） | M |
| solvent | categorical | MeCN / EtOH / water | — |
| equiv | integer | 1 – 4 | eq |
| inert | bool | True / False | — |

- 有效维度 6；目标 ${BT}yield${BT}（max），无约束、无多保真。
- ${BT}conc${BT} 是 log 变量：编码在单位立方体上均匀取点，采样时再指数还原，避免线性尺度把低浓度区挤没。

### 01 路由（router）

- **推荐算法：${BT}bayes${BT}** —— 维度 6 ≤ 12、连续/分类混合、预算 24、光滑 → GP 代理可靠，样本效率最高。
- 备选：${BT}sobol${BT}（批量初始设计）、${BT}lhs${BT}（一次性分层批量）。
- 初始设计切分：先做 2^k 平衡 Sobol 设计，剩余预算交给 EI；${BT}batch=4${BT} 时用 local penalization 防扎堆（不伪造观测）。
- 理由与备选都写入 ${BT}decisions.jsonl${BT}，可审计、可复现。

### 02 停止口径（stopping）

- 预算 24 < 60 → 建议 **${BT}stagnation_first${BT}**（停滞即停），${BT}patience=3${BT}（<2 会被夹紧到 2）。
- 依据：patience=1 停得太早，=3 实测最优，≥5 几乎不触发；预算 ≥60 才建议 ${BT}budget_first${BT}。
- 停止只写建议；预算/目标/平台期/最大轮次的最终判定在 ${BT}next_action()${BT}。

### 03 可行率（feasibility）

- 本问卷未声明硬约束 → 不触发可行率预警，直接走无约束路径（${BT}bayes${BT}）。
- 若后续加约束（如 T ≤ 110），路由会转向 ${BT}constrained${BT}：先最大化可行概率找到安全点，再做带可行性的 EI_c，**不要事后过滤**。
- 观测可行率 < 5% 且 ≥ 10 条观测时会建议把硬限制写进变量范围/固定因子（${BT}encode_limits_as_bounds${BT}）。

### 04 先验数据（prior）

- 本次 ${BT}prior=0${BT}，冷启动。
- 有历史同空间、同目标数据时可暖启动：${BT}top_k${BT} 建议 1–3（>4 告警），导入观测计入预算（先验不是免费数据）。
- 迁移前先 ${BT}transfer-audit${BT} 估源可信度：≥0.8 用原值，0.5–0.8 压到 top_k 1–2 并打折，<0.5 不暖启动。

### 05 交付口径（deliverable）

- 必须包含：推荐条件、确认复孔 mean/std、${BT}in_spec${BT} 判定、观测 CSV、决策审计。
- 交付前自查走「交付自查」场景（${BT}delivery_checklist${BT} + ${BT}check_integrity${BT}），四种形态（单目标 / 约束 / 多目标 / 多保真）都要通过。
- 单次最高分不是结论：推荐优先用**确认复孔均值**，避免幸运噪声被当结论。

### 06 噪声处置（noise_plan · medium）

- 复孔 ${BT}n_replicates=3${BT}；批量 ≤ 6；建议加一条随机基线臂做 A/B。
- **不默认切 UCB**：混合/离散型问题 EI 更稳、UCB 更差，光滑低维反而 UCB 更好 → 地形依赖，只保留方向一致的复孔/小批量/基线臂。
- 带噪观测用 ${BT}objective_std${BT} + ${BT}n_replicates${BT} 回填，GP 的 alpha 逐点估计。

### 下一步

1. 点“开始处理”生成建议（本页离线演示）；
2. 建 study：${BT}POST /api/action {"action":"init"}${BT}；
3. 进入「下一批取样」场景做 ${BT}ask${BT}。`,
      review: {
        type: "decision",
        label: "开工拍板",
        hint: "最终用哪个算法由人拍板；覆写必须给理由并留痕",
        options: ["采纳路由建议", "覆写算法后开工", "退回修改问卷"],
          notes: ["维度与预算支持 GP，采纳 bayes", "改用空间填充先做初始设计", "补齐噪声/先验信息再路由"],
      },
    },

    /* ---------------------------------------------------------- 02 取样 */
    {
      key: "sample",
      no: "02",
      icon: "grid-3x3",
      title: "下一批取样",
      tagline: "按当前观测给出下一批待测条件（含孔板/孔位）",
      short: "取样",
      action: "ask",
      needs_run: true,
      inputLabel: "取样请求（JSON，可选自由文本）",
      placeholder: '例如 {"run":"demo","n":4,"plate":"P2"}；留空则用左侧选中的 study 与默认批量。',
      accept: ".json,.csv,.txt",
      filesHint: "取样不需要附件；条件表由 pending.json / suggestions.csv 写出。",
      demoLine: "选 study → 校验 pending → ask 生成下一批 → 写 pending 与孔板孔位",
      stages: ["校验 study 与待回填状态", "读取已有观测", "调用 ask 生成下一批", "写入 pending 与孔板"],
      outputs: ["pending.json", "suggestions.csv", "plate / well / batch"],
      example: String.raw`{"run": "demo", "n": 4, "plate": "P2"}`,
      mock: String.raw`## 下一批取样 · 离线演示

> **这是离线 mock 文本，不是模型输出。** 真实运行由 Study.ask 返回结构化 pending，
> 表格内容来自 pending.json / suggestions.csv，不是编造的预测。

- study：${BT}demo${BT} · 算法 ${BT}bayes${BT}（GP + EI）· 批量 4
- 已观测 24 · 剩余预算 8 · 本轮是第 7 轮

### 待测条件（pending.json）

| id | plate | well | batch | T / degC | time / min | conc / M | solvent | equiv | inert |
| --- | --- | --- | --- | ---: | ---: | ---: | --- | ---: | --- |
| p7-1 | P2 | A1 | 7 | 86.4 | 42.0 | 0.185 | EtOH | 2 | false |
| p7-2 | P2 | A2 | 7 | 63.1 | 88.5 | 0.640 | MeCN | 3 | true |
| p7-3 | P2 | A3 | 7 | 104.9 | 17.2 | 0.072 | water | 1 | false |
| p7-4 | P2 | A4 | 7 | 51.7 | 61.3 | 0.410 | MeCN | 4 | true |

### 为什么是这四点（可审计）

- 采样点由 GP 后验 + EI 给出；批量用 **local penalization** 逐个扣掉已选点邻域，避免一批全挤在同一个峰。
- ${BT}batch=4${BT} 且 ${BT}plate=P2${BT} 为续测同一块板：well 自动避让已用孔位，写入 ${BT}pending.json${BT} 与 ${BT}suggestions.csv${BT}。
- 每个建议都带 ${BT}batch${BT}，${BT}tell${BT} 时落到观测 ${BT}meta${BT}，之后能做板间效应检查（单因素 ANOVA，板间差异显著且相对 ≥2% 时警告）。
- 若连续 ${BT}patience=3${BT} 轮无显著改进，${BT}auto_route${BT} 会插入一个 ${BT}sobol${BT} 全局探索批次跳出错误吸引域，再回到 BO。

### 边界

- pending 未回填时再 ask 会返回 ${BT}E_CONFLICT${BT}（不静默覆盖）；
- 预测均值/σ 只在真实运行时给出，本页不展示编造的预测值；
- 条件表必须真实执行后再回填，**禁止伪造实验数据**。`,
      review: {
        type: "decision",
        label: "本批拍板",
        hint: "取样是人拍板后执行的；改批量/换板或覆写算法请写理由",
        options: ["采纳本批取样", "调整批量后重取", "覆写算法后重取", "暂停闭环"],
          notes: ["批量与执行能力匹配，按建议执行", "先换一块板再复孔", "覆盖率偏低，追加探索批次"],
      },
    },

    /* ---------------------------------------------------------- 03 回填 */
    {
      key: "feedback",
      no: "03",
      icon: "clipboard-check",
      title: "结果回填",
      tagline: "把实测结果回填进 dataset 与 rounds，清空 pending",
      short: "回填",
      action: "tell",
      needs_run: true,
      inputLabel: "实测结果（JSON observations 或 CSV）",
      placeholder: '例如 {"run":"demo","observations":[{"id":"p7-1","y":78.3}]}；也支持 CSV（id,plate,well,x.*,y,obj.*,con.*）。',
      accept: ".json,.jsonl,.csv,.tsv,.txt",
      filesHint: "CSV/TSV/分号表自动嗅探；列名前缀 x. / obj. / con. 决定归位。",
      demoLine: "粘贴实测结果 → 按 id 匹配 pending → 校验单位与约束 → 写 dataset 与 rounds",
      stages: ["解析回填结果", "校验 id 与约束", "写入 dataset 与 rounds", "确认 pending 清空"],
      outputs: ["dataset.jsonl", "rounds.json", "成本记账"],
      example: String.raw`{"run": "demo", "observations": [
  {"id": "p7-1", "y": 78.3, "cost": 1.0},
  {"id": "p7-2", "y": 61.9},
  {"id": "p7-3", "y": 84.2, "std": 1.8, "n_replicates": 3},
  {"id": "p7-4", "y": 70.5, "plate": "P3", "well": "B7"}
]}`,
      mock: String.raw`## 结果回填 · 离线演示

> **这是离线 mock 文本，不是模型输出。** 真实的 accepted / still_pending 计数来自 Study.tell 的返回值。

- study：${BT}demo${BT} · 提交 4 条 · 接受 4 条（全部匹配到 pending id）

### 校验与归位

| id | 提交值 | 归位 | 说明 |
| --- | ---: | --- | --- |
| p7-1 | 78.3 | 观测 y | id 命中 pending，追加进 ${BT}dataset.jsonl${BT} |
| p7-2 | 61.9 | 观测 y | id 命中；未给 cost，按默认计入 |
| p7-3 | 84.2 | 观测 y + objective_std=1.8 | ${BT}std${BT} + ${BT}n_replicates=3${BT} → GP 的 alpha 逐点估计，不把复孔当 3 个独立点 |
| p7-4 | 70.5 | 观测 y（plate=P3 / well=B7） | 实际换板/重排孔位时可覆写，写进观测 ${BT}meta${BT} |

### 回填纪律

- **丢弃未知 id 会报错而不是静默忽略**：id 不在 pending 里 → ${BT}E_VALIDATION${BT} 并指出 id。
- 幂等：同一个 id 重复 tell 不会写成两条观测。
- 部分回填：只交了一部分结果时返回 ${BT}still_pending${BT}，未回填的 id 保留在 pending，可补测重跑。
- 约束字段必须全部回填（如 ${BT}con.T_max${BT}）；缺列会导致约束优化器无法判定可行。
- 成本：${BT}cost${BT} 回写后 ${BT}cost_spent${BT} / ${BT}remaining_cost${BT} 更新；成本预算用尽时 ${BT}next_action${BT} 直接 stop（${BT}stop_category=cost_exhausted${BT}）。
- 多保真 study 要带 ${BT}fidelity${BT} 列；全保真点才进 Pareto 前沿与 best。

### 警示

- 示例目标是合成/虚构数值，仅用于演示闭环；真实结论必须来自真实测量。
- AI 不会、也不允许伪造实验数据。`,
      review: {
        type: "decision",
        label: "回填拍板",
        hint: "确认进 dataset 前由人核对单位与孔位；退回要写原因",
        options: ["确认回填", "退回重测该孔位", "标记失败孔位", "覆写算法后继续"],
          notes: ["单位与孔位已核对", "复孔数不足，退回补测", "约束字段必须全部回填"],
      },
    },

    /* ---------------------------------------------------------- 04 交付 */
    {
      key: "delivery",
      no: "04",
      icon: "package-check",
      title: "交付自查",
      tagline: "交付清单 + 完整性自检 + 收尾建议",
      short: "交付",
      action: "delivery",
      needs_run: true,
      inputLabel: "交付自查请求（JSON，可选）",
      placeholder: '例如 {"run":"demo"}；留空则用左侧选中的 study。',
      accept: ".json,.txt",
      filesHint: "自查只读 run 目录，不写数据。",
      demoLine: "汇总交付清单 → doctor 完整性自检 → 推荐判定与复孔统计 → 出交付摘要",
      stages: ["汇总交付清单", "跑完整性自检", "给出收尾建议", "生成可交付摘要"],
      outputs: ["delivery checklist", "integrity", "交付摘要"],
      example: String.raw`{"run": "demo"}`,
      mock: String.raw`## 交付自查 · 离线演示

> **这是离线 mock 文本，不是模型输出。** 真实清单来自 ${BT}Study.delivery_checklist()${BT}，
> 自检来自 ${BT}Study.check_integrity()${BT}（等价 CLI ${BT}condopt doctor${BT}）。

### 交付清单（delivery_checklist）

| # | 检查项 | 结果 |
| --- | --- | --- |
| 1 | 有预算内的可用观测 | ✓ 24 条 |
| 2 | 推荐条件已确认（确认复孔） | ✓ n=3 · mean 96.4 · std 0.8 |
| 3 | 目标/约束口径一致（objective.json） | ✓ |
| 4 | 约束全部回填（有约束时） | — 无约束 |
| 5 | 决策审计可读（decisions.jsonl） | ✓ |
| 6 | 看板可复现（dashboard.html） | ✓ 单文件、无 CDN |

### 完整性自检（check_integrity / doctor）

- suggestion_id 唯一：✓
- pending 与 dataset 不重叠：✓
- ${BT}opt_state.json${BT} 的算法与 ${BT}config.json${BT} 一致：✓
- problem / config / rounds JSON 可解析：✓

### 推荐判定（recommend）

- ${BT}selection_basis = plate_adjusted${BT}：检测到板间系统差异（单因素 ANOVA，相对 ≥2%），
  先用 one-way random-effects 的 BLUP 估计各板位移，再在**校正后**分数上选推荐条件，
  而不是直接拿 raw best。
- 返回值同时给 ${BT}raw_best${BT} 与 ${BT}plate_effects${BT}，审计时能看出“为什么没选那个最高分”。
- 有 spec 时优先在 Pareto 前沿上选 **in-spec 且 spec score 最高**的点（${BT}pareto_spec_plate_adjusted${BT}）；
  无 in-spec 点退回 knee。
- 确认复孔跟随**推荐条件**而不是 raw best；停止判定优先用确认均值，避免一次幸运测量提前收工。

### 收尾建议（step）

- ${BT}action = stop${BT}，${BT}stop_category = target_reached${BT}（确认均值已达标）。
- 若因**停滞**停止但仍有预算：建议先 ${BT}retune_for_exploration()${BT}（实测优于原样继续与探索重启），
  即 CLI ${BT}condopt retune --run demo${BT}。
- 规格不可达（≥5 观测仍 0 个 in-spec）→ 建议 ${BT}review_spec${BT}，可用 ${BT}suggest-spec${BT} / ${BT}fork --spec-mode suggested${BT} 重开。

### 交付边界

- 只交付真实测量数据 + 可复现命令；不交付推断值当结论。
- 报告里 ${BT}raw_best${BT} 与推荐条件并列展示，避免把原始最高分误当推荐。`,
      review: {
        type: "decision",
        label: "交付拍板",
        hint: "交付与否由人拍板；补齐证据/退回补测都要留痕",
        options: ["确认交付", "补齐证据后再交付", "退回补测", "覆写算法重跑收尾"],
          notes: ["确认复孔均值已达标", "规格不可达，先校准 spec", "补齐决策审计后再交付"],
      },
    },

    /* ---------------------------------------------------------- 05 看板 */
    {
      key: "board",
      no: "05",
      icon: "layout-dashboard",
      title: "看板",
      tagline: "渲染离线看板 dashboard.html（收敛/覆盖/前沿）",
      short: "看板",
      action: "render",
      needs_run: true,
      inputLabel: "看板渲染请求（JSON，可选）",
      placeholder: '例如 {"run":"demo"}；留空则用左侧选中的 study。',
      accept: ".json,.txt",
      filesHint: "渲染只读 run 目录，输出 dashboard.html（与 convergence.png，若本机可用）。",
      demoLine: "读 run 目录 → 内联 SVG 渲染各卡片 → 写 dashboard.html → 校验离线无外链",
      stages: ["读取 run 目录", "渲染收敛与前沿", "写入 dashboard.html", "检查离线资源"],
      outputs: ["dashboard.html", "convergence.png", "决策时间线"],
      example: String.raw`{"run": "demo"}`,
      mock: String.raw`## 看板渲染 · 离线演示

> **这是离线 mock 文本。** 真实的 ${BT}dashboard.html${BT} 由 ${BT}condopt.render.render_study${BT} 生成：
> 内联 SVG、无 CDN、无预测造假、只读 run 目录。

### 看板区块（dashboard.html）

| 区块 | 数据来源 | 说明 |
| --- | --- | --- |
| 收敛曲线 | ${BT}dataset.jsonl${BT} + ${BT}rounds.json${BT} | 每次观测散点 + best-so-far 折线；不含编造的预测曲线 |
| 空间覆盖 | 观测的条件变量 | 2D 投影 / 网格占用；白格=完全未覆盖，提示是否该加探索 |
| 参数重要性 | 已采样点 | Spearman \|ρ\|，明确标注“相关 ≠ 因果、仅在已采样区域成立”，n<20 显示占位 |
| Pareto 前沿 | 全保真观测 | 2–4 目标 HV（2D 闭式，3–4 递归切片）；多保真时只用全保真点 |
| 建议批次 | ${BT}pending.json${BT} | 条件表 + plate/well/batch + 来源（router / auto_route / override） |
| 决策时间线 | ${BT}decisions.jsonl${BT} | router 路由、override 覆写理由、transfer 迁移、knee_weights… |
| 预算进度 | ${BT}config.json${BT} + 观测数 | 已用 / 总预算；成本预算另计 |
| 下一步行动 | ${BT}step.json${BT} | tell / confirm / ask / stop + ${BT}next_command${BT} |
| 推荐判定 | ${BT}decision.json${BT} | selection_basis（raw / plate_adjusted / pareto_knee…）+ 理由链 |
| 规格校准 | ${BT}spec_calibration.json${BT} | 建议 scale / tolerance 与置信度（不改写 study） |

### 离线与口径

- 单文件 HTML，SVG 内联，**无 CDN、零外网请求**；双击即可打开，也可用 ${BT}scripts/serve.py${BT} 只读托管。
- ${BT}convergence.png${BT} 可选：本机 matplotlib 可用时生成，不可用时看板仍完整（${BT}warnings${BT} 里给出原因）。
- 看板只读 run 目录：**不写第二份状态**，也绝不新增/覆盖观测。

### 关于 mock 看板

- 本页不画假曲线。要看真实曲线请点“开始处理”走 ${BT}POST /api/action {render}${BT}，
  后端返回 ${BT}{"dashboard":"<绝对路径>","png":...}${BT}，再打开那个文件。`,
      review: {
        type: "decision",
        label: "看板拍板",
        hint: "看板口径由人确认；追加探索或收尾都要留痕",
        options: ["确认看板口径", "追加一轮探索", "覆写算法后重渲", "收尾归档"],
          notes: ["看板口径与 objective 一致", "空间覆盖偏低，追加探索", "收尾归档并保留 dashboard.html"],
      },
    },

    /* ---------------------------------------------------------- 06 导入数据 */
    {
      key: "import",
      no: "06",
      icon: "file-input",
      title: "导入数据",
      tagline: "粘路径 → 读取预览 → 确认列角色 → 让模型读表 → 作先验导入并落调参表单",
      short: "导入",
      action: "import",
      needs_run: false,
      inputLabel: "数据文件路径",
      placeholder: String.raw`粘贴一个文件路径，例如 runs/_dbg_bf/suggestions.csv；相对路径按后端的 --allow-import-root 解析。支持 .csv /.tsv /.txt /.json /.jsonl。`,
      accept: ".csv,.tsv,.txt,.json,.jsonl",
      filesHint: "扫描与规则映射不出网；只有「让模型读表」会外发，且需二次确认（见 CONTRACT §9.1）。",
      demoLine: "粘路径 → 读取列画像 → 确认列角色 → 二次确认外发 → 应用为先验 → 调算法子参数",
      stages: ["读取文件", "画像与列类型", "让模型读表", "导入为先验"],
      outputs: ["列画像", "映射确认表", "算法子参数"],
      example: String.raw`runs/_dbg_bf/suggestions.csv`,
      mock: String.raw`## 导入已有数据 · 离线演示

> **这是离线 mock 文本。** 真实运行时，每一步都由后端的三个只读端点驱动，
> 不解析任何 CLI 文本，也不在这里编造统计。

### 四个阶段（下标即 ${BT}stage.index${BT}）

| # | 阶段 | 后端动作 | 出网？ |
| --- | --- | --- | --- |
| 0 | 读取文件 | ${BT}POST /api/import/scan${BT}：按允许根校验路径、读文件、算列画像 | 否 |
| 1 | 画像与列类型 | 同上返回：类型 / 缺失 / 唯一值 / min / max / 样例 + 规则映射 ${BT}rule_mapping${BT} | 否 |
| 2 | 让模型读表 | ${BT}POST /api/import/map${BT}：只发**表头 + 列画像 + 前 N 行样本**（默认 12，上限 50） | **是（唯一例外）** |
| 3 | 导入为先验 | ${BT}POST /api/import/apply${BT}：按你确认的 mapping 落成观测，计入 budget | 否 |

### 边界（不做就说明白）

- 默认**完全离线**：没有 ${BT}--import-llm${BT} 开关或缺少密钥环境变量时，${BT}map${BT} 返回
  ${BT}E_LLM_UNAVAILABLE${BT}，**不静默降级**；你可以显式改走 ${BT}fallback:"rules"${BT}（规则映射，离线）。
- 外发前必须二次确认：弹窗列明目标 URL、模型 id、将发送的行数与字节数，并说明**只发样本不发整文件**；
  确认后才带 ${BT}consent:true${BT}。
- 密钥只从环境变量读，不写盘、不回显、不进日志；响应里只有「是否已配置」。
- 路径安全：只接受允许根内的文件（${BT}--allow-import-root${BT}），${BT}..${BT} / 根外 / 目录 / 其它后缀一律 400。
- 大小上限默认 20 MB（${BT}--max-import-bytes${BT}）。

### 落到「调数子参数」

${BT}apply${BT} 成功后，用返回的 ${BT}params_schema${BT}（来自 ${BT}registry${BT} 的算法参数 schema，代码取值、不手写）
**立刻**渲染「算法 + 子参数」表单——不需要再点一次。改完参数点「生成下一批」即可
（若参数与默认值不同，会先 ${BT}override${BT} 留痕，再 ${BT}ask${BT}）。

### 离线时会发生什么

本页在无后端时展示**内置示例数据**（列画像 / 规则映射 / 示例模型建议 / 示例参数表），
并在每一处标注「离线演示」——不读你的文件，也不假装读过。`,
      review: {
        type: "decision",
        label: "导入拍板",
        hint: "导入前由人确认列角色与预算；作废这次导入要写理由",
        options: ["采纳映射并导入", "修改映射后导入", "先只做规则映射", "放弃导入"],
        notes: ["列角色人工核对过", "先验计入预算，batch 需下调", "只因模型不可用才改规则映射"],
      },
    },
  ],
};
