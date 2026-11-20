# ADAPTATION.md · condopt 工作台对上游 EduKit 复用包的改动清单

> 唯一真源：`CONTRACT.md`（冻结）。本文件记录**对上游文件改了什么 / 为什么**，
> 重点是 `assets/app.js` 的每一处钩子改动（契约 §5：app.js 只允许「加钩子」式改动）。
>
> 上游：本机 EduKit 复用包（`<本机>/Project2/frontend`，无构建 / 无 npm / 断网能跑）。
> 所有依赖仍在 `vendor/`，运行时不访问 CDN，也不发任何外网请求。

## 0. 接入说明（点顶栏「接入说明」就是本文件）

```powershell
# 1) 起后端（api-backend 的 server.py，只绑 127.0.0.1）
py -3 server.py --port 8770            # 默认 runs-dir = state/runs
py -3 server.py --runs-dir runs        # 直接操作仓库现有 study

# 2) 打开工作台
#    后端在线：http://127.0.0.1:8770/   （server.py 直接托管 web/，一个端口就够）
#    只想看前端：py -3 -m http.server 8787 --directory web
#              → 没有 /api/*，页面自动降级为离线演示（不白屏）
```

前端只依赖 CONTRACT §2 的五个端点：`/api/bootstrap`、`/api/runs`、`/api/study`、
`/api/action`、`/api/run`。前四个走 `fetch` 读 JSON；`/api/run` 走 SSE，由
`EduKit.parseSSE` 解析（解析器未改，契约 §7.6）。

## 1. 文件级改动总览

| 文件 | 动作 | 说明 |
|---|---|---|
| `web/index.html` | **改写**（新增/替换产品壳） | condopt 产品名与导航；保留顶栏 / 场景侧栏 / 结果区 / 复核区骨架、演示视图、深浅色、快捷键；新增 condopt 区块（study 列表 / 开工表单挂载点 / 动作条 / 覆写选择器） |
| `web/assets/data.js` | **替换内容** | 四个教育场景 → condopt 五场景（key 与顺序固定：`kickoff` `sample` `feedback` `delivery` `board`）；`stages` 与 CONTRACT §4.1 权威表一字不差 |
| `web/assets/condopt.js` | **新增** | condopt 专属层：`EduAPI.run` → `POST /api/run`、开工表单（模板投影）、study 列表、闭环动作条、人工拍板/覆写留痕 |
| `web/assets/import.js` | **新增**（task-9 / §9） | 06 导入数据：路径→scan→列画像→映射确认→外发二次确认→apply→落调参表单（`CONDOPT_IMPORT`） |
| `web/assets/kickoff.js` | **新增**（task-14 / §10） | 01 对话式开工：聊天窗 + 快捷回复芯片 + 算法选项链接卡 + 参数页（`CONDOPT_KICKOFF`） |
| `web/assets/app.js` | **7 处加钩子** | 见 §2；不改任何上游渲染逻辑，没有 condopt.js 时行为与上游一致 |
| `web/assets/app.css` | **追加覆盖段** | 品牌变量 + condopt 区块布局；上游 1–728 行原样保留 |
| `web/ADAPTATION.md` | **新增** | 本文件 |
| `web/vendor/katex/fonts/fonts/*` | **上移一层** | 修复拷贝层级（只移动、不改字节），见 §4 |

**未改动**：`vendor/**`（除 §4 的层级移动）、`assets/base.css`（色板语义未动）、
`assets/components.css`、`assets/gallery.css`、`assets/gallery.js`、`components/index.html`、
`assets/adapter.example.js`（保留为「接后端」的参考实现）、`README.md`、`SOURCES.md`。

`assets/_upstream_app.css` 是工作区里既有的上游 `app.css` 原件副本（23,426 字节，与本文件
改动前的 `app.css` 逐字节相同），**原样保留**，用于随时 diff 出「上游布局 + condopt 覆盖段」。

## 2. `assets/app.js` 的每一处改动（9 个钩子）

原则：只在调用点插入一个**受 `window.CONDOPT` 保护**的分支；`CONDOPT` 不存在时
逐行回落上游行为。每一处都在源码里带 `// [condopt hook #N]` 注释，可 grep 定位。

| # | 位置（改动后行号） | 改了什么 | 为什么 |
|---|---|---|---|
| 1 | `app.js:337` `renderMode()` | `state.mode === "api"` → 再接受 `"live"` | CONTRACT §4 的 `meta.mode` 是 `live`，上游只认 `api`；不改会出现「后端真的在线却显示离线演示」 |
| 2 | `app.js:592` `renderResultMeta()` | 同上，`live` 也标成「真实后端」 | 与 #1 同一原因；结果元信息不能自相矛盾 |
| 3 | `app.js:424` `run()` 前置校验 | 增加 `hasStructured`：有结构化载荷时允许不带自由文本 | `sample/feedback/delivery/board` 的数据在 `payload` 里，不在输入框里；上游的「必须先输入内容」会拦住闭环 |
| 4 | `app.js:462` `run()` 构造 payload | 有 `CONDOPT.buildPayload` 时附上 `payload.payload` | CONTRACT §4：EduKit 原契约 + 一个 `payload` 扩展字段。app.js 只搬运，不解析 |
| 5 | `app.js:630` `showReview()` | `reviewHint` 优先取 `scenario.review.hint` | 五场景拍板口径不同（覆写要写理由/确认回填前核对单位…），提示要跟着场景走 |
| 6 | `app.js:701` `saveReview()` 末尾 | 调用 `CONDOPT.onReview(record)` | 复核区复用为「人工拍板 + 留痕」：由 condopt.js 决定是否 `POST /api/action {override}` 写 `decisions.jsonl`；钩子抛错不影响上游的 localStorage 留痕 |
| 7 | `app.js:624` `showReview()` | 拍板理由优先取 `scenario.review.notes` | 兜底文案是教育场景的（「已核对原文」），condopt 场景自带 `notes`；未提供时仍回落上游 `QUICK_NOTES` |
| 8 | `app.js:340` `renderMode()` | 未 live 时允许 `CONDOPT.modeBadge()` 提供文案（`text`/`pill`/`note`/`connected`） | **P1**：上游只在收到 SSE `meta` 帧后才切 live，起后端后首屏仍写「离线演示」。现在 bootstrap/runs 一通就显示「后端已连接 · 待运行」，只有真正回退 `mockRun` 才写离线。钩子缺席时逐字沿用上游文案 |
| 9 | `app.js:112` `window.EduKit` | 导出上游已有的 `renderMode` | 让 condopt.js 在 `/api/bootstrap` 成功后**立刻重画徽章**（否则要等用户跑一次场景）。只导出入口，不新增渲染逻辑，避免在 condopt.js 里复制一份 DOM 写入 |
| 10 | `app.js:419` `run()` 开头 | `CONDOPT.handlesRun(key)` 为真时，把「开始处理」交给 `CONDOPT.runScenario(key)` 并 return | **06 导入数据没有 SSE 场景**（契约 §9 只有 scan/map/apply 三个 JSON 端点），上游的「必须有文本/附件 → 走 SSE」流程不适用。钩子缺席时行为与上游一致 |

追加说明（非 app.js 文件内容改动，但属于集成方式）：

- `index.html` **不再加载** `assets/adapter.example.js`：同一个 `EduKit.EduAPI.run` 由
  `condopt.js` 接管（否则两个适配器互相覆盖）。`adapter.example.js` 仍留在目录里作为参考。
- `condopt.js` 通过上游公开的 `EduKit.state` getter 同步 `runId`（`EduKit.state.runId = <run>`），
  让上游的「保存拍板留痕」有 run 可写。这是**用公开接口取值，不是改 app.js**。
- `condopt.js` 打开复核区时，往 `#decisions` / `#quickNotes` 里写的是与上游**相同 data 属性**
  的按钮（`data-value` / `data-note`），因此上游已注册的委托监听器会直接接管点击，无需新增钩子。
- 钩子 #6 在覆写被拦下时会把复核区恢复成「待拍板 / 重存拍板留痕」：上游保存一次就会禁用
  `#saveReview`（单次复核语义），若不恢复，用户选错算法后没有机会补选重试。

### 2.1 `run` 的两条通道（本项目最容易接错的一处）

契约对 `run` 的位置在两条通道上**不同**，混用会被后端按契约 §3 判「未知字段」：

| 通道 | 信封 | `run` 的位置 |
|---|---|---|
| `POST /api/action`（结构化，契约 §3） | `{"action":…,"run":"<id 或 null>","payload":{…}}` | **请求顶层**；payload 只放该 action 自己的字段（如 `ask` → `n`/`plate`；`delivery`/`render` → `{}`） |
| `POST /api/run`（SSE，契约 §4） | `{"scenario":…,"text":…,"files":[…],"payload":{…}}` | **payload 里**（§4 请求示例：`{"answers":{…},"run":"demo","n":4}`） |

因此：`condopt.js` 的 `buildPayload()`（被 app.js 钩子 #4 调用，喂给 `EduAPI.run`）返回的
是 §4 形状、**带 `run`**；而动作条走 `/api/action` 时在 `runAction()` 里把 `run` 提到顶层、
从 payload 里删掉。这处曾实测踩坑：把 `run` 同时放进两处 → `E_VALIDATION: ask 的 payload 未知字段: run`。

另一处实测踩坑：`data.js` 的 `sample/feedback/delivery/board` 示例里带占位 `"run": "demo"`，
若让它盖过左侧 study 下拉框的选中值，会用不存在的 run 去打接口（404 → 假「回退离线演示」）。
现在**下拉框是 run 的权威来源**，JSON 里的 `run` 只在没有选中 study 时兜底。

### 2.2 面向用户的措辞（P1 徽章 / P2 开工表标题）

任务 task-5 修的两处（lead 在真实截图里发现）：

**P1 · 后端已连接时徽章仍写「离线演示」**
- 根因核实：徽章文案由 app.js `renderMode()` 生成，而它只在 `state.mode` 变成 `api`/`live` 时才切；
  `state.mode` 仅由 SSE `meta` 帧设置 → **首屏（还没跑过场景）永远显示离线**，即使
  `/api/bootstrap`、`/api/runs`、`/api/study` 都已 200。
- 修法（不改上游语义）：`condopt.js` 导出 `CONDOPT.modeBadge()`，按 `state.online`（bootstrap/runs/SSE 任一成功）
  返回 `{text:"后端已连接 · 待运行", pill:"后端已连接", note:"…运行后走真实 SSE…", connected:true}`；
  未连接时返回 `{text:"离线演示", pill:"离线 mock", …}`。app.js 的钩子 #8 只在**未 live** 时采用它；
  一旦 SSE `meta` 到达，仍是上游的「真实模型 · condopt-0.1.0」。`setOnline()` 每次变更后调
  `EduKit.renderMode()`（钩子 #9 导出的入口）立即重画。
- 视觉：新增 `.mode-badge.is-connected`（app.css 追加段），与离线态可区分，但**不用** `is-live` 的脉冲——
  脉冲只表示「正在用真实 SSE 跑」，避免「已连接」与「正在跑真实模型」混为一谈。
- 语义边界：只有真正回退到 `EduKit.mockRun` 时徽章才回到「离线演示」（adapter 的 `received=false` 分支会 `setOnline(false)`）。

**P2 · 开工表标题偏开发语**
- 原文（截图）：`字段来自后端 prepare_template: objective / variables / budget / batch / noise / smoothness / domain / prior / notes`。
- 现在：`字段由后端自动生成：目标 / 条件空间 / 预算 / 批大小 / 噪声 / 光滑性 / 领域 / 先验 / 备注`。
- 实现：`condopt.js` 的 `FIELD_LABELS` 只做**显示翻译**（字段本身仍 100% 由 `prepare_template` 决定）；
  模板里出现未知键就原样显示该键，不隐瞒也不编造。原始键名放进 `title`（hover 可见），
  便于排查而不占用首屏文案。
- 同时把顶栏徽章的 `title`（原「默认离线 mock，不需要 API key」）改成中性的「后端连接状态：已连接时走真实 SSE；未连接时自动回退离线演示」。

**附带（同类诚实性，一行）**：离线时动作条的提示不再写「动作走 POST /api/action…」暗示可用，
改为「后端未连接：闭环动作不可用；可先用下面的『开始处理』看离线演示」，
避免离线首屏出现一个只会失败的「建 study」入口却没有任何说明。

## 3. `assets/data.js` 的六场景
| key | no | title | action | needs_run | stages（下标即 SSE `stage.index`） |
|---|---|---|---|---|---|
| `kickoff` | 01 | 开工建议 | `prepare` | false | 校验问卷答案 / 解析条件空间 / 路由算法 / 生成六段建议 |
| `sample` | 02 | 下一批取样 | `ask` | true | 校验 study 与待回填状态 / 读取已有观测 / 调用 ask 生成下一批 / 写入 pending 与孔板 |
| `feedback` | 03 | 结果回填 | `tell` | true | 解析回填结果 / 校验 id 与约束 / 写入 dataset 与 rounds / 确认 pending 清空 |
| `delivery` | 04 | 交付自查 | `delivery` | true | 汇总交付清单 / 跑完整性自检 / 给出收尾建议 / 生成可交付摘要 |
| `board` | 05 | 看板 | `render` | true | 读取 run 目录 / 渲染收敛与前沿 / 写入 dashboard.html / 检查离线资源 |
| `import` | 06 | 导入数据 | `import` | false | 读取文件 / 画像与列类型 / 让模型读表 / 导入为先验 |

- 每个场景含 `no/title/tagline/short/stages/example/mock/outputs/review`，外加上游 `app.js`
  会读的展示字段（`icon/inputLabel/placeholder/accept/filesHint/demoLine`）与
  `action/needs_run`（便于前端与后端 `Scenario` 直接比对）。
- `mock` 文本全部标注「离线演示」，内容对应真实能力（router 规则、停止口径、可行率处置、
  暖启动与迁移可信度、交付自查、看板区块），**不含编造的实验结论或预测值**。
- `mock` 用 `String.raw` 包裹以保住 LaTeX 反斜杠（如表格里的 `\|ρ\|`）。`String.raw` 里
  **不能出现裸反引号**（会提前结束模板字面量），所以行内代码写作 `${BT}`，`BT` 在文件顶部定义为
  `String.fromCharCode(96)`。
- **防漂移**：`condopt.js` 在 `/api/bootstrap` 可用时会比对后端 `scenarios[].stages`，
  不一致就以后端为准并重渲染，同时 `console.warn`。这**只是防抖**，不替代本表与契约一致
  （漂移本身是 bug，要修 data.js）。

## 4. 修复拷贝层级：`katex/fonts/fonts/*` → `katex/fonts/*`

- 现象：`vendor/katex/katex.min.css` 按 `url(fonts/KaTeX_*.woff2)` 请求
  `vendor/katex/fonts/KaTeX_*.woff2`，但拷贝后字体落在 `vendor/katex/fonts/fonts/`（多一层），
  导致 20 个 `@font-face` 全部 404。
- 处置（lead 授权 (A)，属「恢复上游原状」而非改上游内容）：把 `fonts/fonts/` 下 60 个文件
  上移一层，确认嵌套目录为空后删除该空目录。
- 证明（只移动、不改字节）：移动**前**逐文件哈希与上游
  `<本机>/Project2/frontend/vendor/katex/fonts` 相等；移动**后**再次用
  `Compare-Object` 逐行比对 `哈希 + 名称`，结果 `IDENTICAL: 60 / 60`（原文输出见 task-2 汇报）。
- 复核：`/vendor/katex/fonts/KaTeX_AMS-Regular.woff2` 等由 404 → **200**。

## 5. 降级与无外网

- `EduAPI.run`：`fetch /api/run` → 非 2xx / 连不上 / 一个 SSE 帧都没收到 → `notice` + 回退
  `EduKit.mockRun`（上游 `adapter.example.js` 的同一策略，保留）。已收到部分输出则如实抛错，
  不掩盖半途失败。
- 结构化载荷缺失（`answers.json` 不是合法 JSON、没选 study）→ 直接走 mock 并给 `notice`，
  不发一次注定被拒的请求。
- `/api/bootstrap` 不可用 → 开工表单隐藏并提示「后端未连接」、study 列表隐藏；
  场景侧栏、演示视图、mock 演示全部照常，**页面不白屏**。
- 所有请求都是同源相对路径；`index.html` 的 favicon 用内联 `data:` URI（不产生网络请求）。

**唯一例外（CONTRACT §9.1，task-9 引入）**：`POST /api/import/map` 在**用户二次确认后**会把
「表头 + 列画像 + 前 N 行样本」发给模型网关。除此之外，工作台默认仍然**完全离线**：
`scan` / `apply` 与其余所有端点都不出网，`map` 未开启 `--import-llm` 时直接返回
`E_LLM_UNAVAILABLE` 而**不静默降级**。这条例外只存在于 `assets/import.js`，并且必须经过
`#condoptImportConsent` 确认弹窗；工作台启动时不发任何外网请求。

## 6. 已跑验收（命令原文见 task-2 汇报）

```powershell
node --check web/assets/data.js
node --check web/assets/condopt.js
node --check web/assets/app.js
Get-ChildItem web -Recurse -File | Select-String -Pattern 'https?://'   # 外链清单
py -3 -m http.server 8813 --bind 127.0.0.1 --directory web              # 资源 200
msedge --headless=new --dump-dom "http://127.0.0.1:8813/index.html?demo=1"        # 无后端走 mock
```

已实测通过的项目（临时校验脚本跑完即删，不在交付物里）：

1. **JS 语法**：`node --check` 通过 `data.js` / `condopt.js` / `app.js`（node v22.23.4）。
2. **data.js ↔ 契约**：110 项结构断言全过（五场景 key/顺序、`stages` 与 CONTRACT §4.1 一字不差、
   `review`/`mock`/`example`/`outputs` 齐全、mock 均标注「离线演示」、`String.raw` 反斜杠保留）。
3. **data.js ↔ 后端**：`/api/bootstrap` 的 `scenarios` 与前端 `data.js` 逐字段比对，
   `key/no/title/tagline/short/action/needs_run/stages` **差异 0 处**（后端 5 场景 / 前端 5 场景）。
4. **资源 200**：`index.html` 直接引用的 15 个本地资源全部 200；KaTeX 20 个 `@font-face`
   实际命中 200（见 §4）；`app.js` 需要的 54 个元素 id 在 `index.html` 中 0 缺失。
5. **无后端降级**：`py -3 -m http.server` 起静态服务（`/api/*` 必然 404/501），
   用 Edge headless + CDP 驱动页面，五场景 `?demo=<key>` 全部：四段阶段跑完、Markdown 有输出、
   复核区出现、拍板选项渲染、覆写选择器优雅降级为「算法清单不可用」、无 console.error、无未捕获异常。
6. **在线闭环**：起 `server.py` 后，用 `condopt.js` **自己的 `buildPayload`** 产出的
   五场景载荷打真实后端：`prepare → init → ask → tell → delivery → render` 全 `ok:true`，
   另加 `step/recommend/report/override` 与「未知字段必须 E_VALIDATION」共 **12/12 通过**；
   `POST /api/run` 帧序列 `meta → stage×4 → delta → done`，`stage.index == [0,1,2,3]`。
7. **在线浏览器**：kickoff 表单 45 个字段由 `prepare_template` 生成（含 `variables.0.kind` 下拉）、
   动作条 `init`、study 下拉有真实 run、快照 `n_obs/best/剩余/pending`、模式标「真实后端」，
   18/18 断言通过；delivery 15/15；离线 board 15/15。
8. **拍板 → 覆写 → 留痕**（真实浏览器）：非覆写决定只写本地；覆写缺算法被拦下且可重试；
   覆写成功后 `decisions.jsonl` 出现 `action:"override"` 记录（含 reason 原文），签章出现。

## 7. 场景 06「导入数据」（task-9，CONTRACT §9）

需求原话：「给一个导入已有数据的方式，粘贴相关文件路径，后台 AI 直接读，读完了直接到调数子参数」。
已选：**用模型读** ＋ **作先验导入、计预算、参与优化**。

### 7.1 新增与改动

| 文件 | 改动 |
|---|---|
| `assets/data.js` | 新增第 6 场景 `import`（`no:"06"` `action:"import"` `needs_run:false`），`stages` = 契约权威四段；`example` 是一个真实存在的 CSV 路径；`mock` 标注「离线演示」并写明外发边界 |
| `assets/import.js` | **新增**：整条流程 UI（路径→scan→列画像/样例→映射确认表→二次确认→map→apply→params_schema 表单→生成下一批） |
| `assets/app.js` | **钩子 #10**：`handlesRun` 为真时把「开始处理」交给 `CONDOPT.runScenario` |
| `assets/condopt.js` | `buildPayload("import")` 返回 `null`（import 没有 §4 的 SSE 通道，避免把 path 当 run 发出去）；`renderActions` 对 import 隐藏动作条（它的动作在流程面板里）；把 `handlesRun/runScenario` 转发给 `CONDOPT_IMPORT`；把 `openReviewPanel` 暴露给 import.js 复用「人工拍板 · 留痕」 |
| `index.html` | 新增 `#condoptImport`（左栏流程）与 `#condoptImportResult`（右栏建议/结果/子参数）挂载点、`import.js` script；帮助弹窗加第 6 场景卡与「唯一会出网的地方（§9.1）」说明；快捷键文案 1–5 → 1–6 |
| `assets/app.css` | 仅追加覆盖段（画像表/预览表/映射表/建议卡/统计/子参数表/确认弹窗），未动 `base.css` 色板语义与 `components.css` |

### 7.2 布局与阶段条

- **左栏**＝操作与确认（5 个步骤卡：读取 / 列画像 / 映射确认 / 让模型读表 / 导入为先验）；
  **右栏**＝模型建议卡（来源/模型/发送行数与字节/置信度/理由/警告 + 合并按钮）、导入统计与逐行拒绝原因、**算法 + 子参数表**。
- `import` 没有 SSE，所以四段阶段条由流程本身驱动（`setStage`），仍用与上游 `renderStages()` **相同的
  DOM 结构与 class**（`li.stage.is-pending|is-running|is-done`），离开场景时由 app.js 重新接管。
- 上游「开始处理」按钮在 import 场景被复用为「下一步」：`读取文件 → 让模型读表 → 导入为先验`，
  文案随状态变化（`syncRunLabel()`）。

### 7.3 外发边界（§9.1 的前端实现）

- **唯一出网点**是 `让模型读表`。点它只**打开确认弹窗**，不发请求；弹窗列明
  `目标 URL / 模型 id / 将发送的行数 / 将发送的字节数 / 文件本身大小`，并写明
  **不发整文件，只发表头 + 列画像 + 前 N 行样本**（默认 12，上限 50）；勾选同意后按钮才可用，
  确认后才带 `consent:true` 调 `map`。
- 字节数是**前端按同样的字段（表头+画像+样本）估算**并标注「估算」，不是假装精确。
- **确认弹窗的 URL / 模型来自 `bootstrap.import_llm`（§2.1）**，即服务端**实际生效**的值（含环境变量覆盖）；
  同时显示「通道状态」（是否带 `--import-llm`、密钥环境变量名是否就绪）。字段缺失（旧后端）时如实写
  「后端未提供」，**不写死一个 URL 去骗确认**。
- `E_LLM_UNAVAILABLE` **不静默降级**：右栏与左栏都给出可行动错误卡，说明是缺 `--import-llm`
  还是缺哪个密钥环境变量（环境变量名优先取后端字段，其次从 hint 里提取），并提供
  **「改走离线规则映射（不出网）」**一键出路 —— 这一路显式 `consent:false`（不出网就不谎称已同意外发）。
- 其它错误态同样给动作：`E_CONSENT_REQUIRED`（说明必须从确认弹窗进入）、
  `E_LLM_BAD_RESPONSE`（说明后端已做一次只修格式的重试，可重试或走规则映射）、
  `E_VALIDATION`（budget < 导入行数时给「用建议值 budget=N」按钮；**不静默放大预算**）。

### 7.4 「直接到调数子参数」

`apply` 成功后**立刻**用响应里的 `params_schema` 渲染算法 + 子参数表单（`choice→select`、
`bool→checkbox`、`int/float→number`、其余→text；覆盖 registry 现有的全部 5 种 type），
不用用户再点一次；同时显示 `imported{accepted,duplicate,rejected}`、逐行拒绝原因与剩余预算警告，
并给「生成下一批」按钮（参数改过会先 `override` 留痕再 `ask`；随后可一键跳到「下一批取样」场景）。

### 7.5 离线降级

后端不可用时（网络层失败/404/405）自动切到**内置示例数据**：示例列画像、示例规则映射、
示例模型建议、示例导入结果与示例参数表，左栏与右栏各有一条「离线演示」横幅，
`file.path` 明确标成「（未读取 · 离线演示）」，并且**离线时不打开外发确认弹窗**
（没有外发，就不假装要外发）；按钮改为「查看内置示例建议」。

### 7.6 真联调结论（2026-11-20，真 server + 本机 stub 网关）

用真 `server.py`（端口 8771/8772/8773）+ 本机 stub 网关（`CONDOPT_IMPORT_LLM_BASE_URL`
指向它）跑完整 06 流程，逐条核对了先前靠猜的点：

| 联调点 | 结论 |
|---|---|
| `bootstrap.import_llm` 字段 | 与 §2.1 **逐字一致**：`{configured:bool, enabled:bool, url:str, model:str, key_env:str}`；**无密钥泄漏**。三个覆盖变量**各设一次**均生效：只设 `BASE_URL` → 弹窗端点跟着变（`.../v2/chat/completions`）、model/key_env 回落默认；只设 `MODEL` → 弹窗模型变 `qwen3.8-27b-OVERRIDDEN`；只设 `API_KEY_ENV` → 弹窗密钥变量名变 `MY_OTHER_KEY_NAME`。三次审计各 7/7 通过 |
| `scan.columns[].inferred` 取值集合 | 实测只有 `number` 与 `text`（我的 `guessKind` 覆盖两者，未知值按 `continuous` 并允许手改）。另有 §9.3 未列出的**增量字段**：`file.format:"wide"`、`columns[].choices`、`rule_mapping.variables[].choices`——都是新增，不影响既有读取 |
| `levels` 是否真参与换算 | **是**：文本列 `low/mid/high` + `levels{low:.3,mid:.6,high:1}` 导入后，`dataset.jsonl` 的 `fidelity` 集合 = `{0.3, 0.6, 1.0}`（`cost` 跟随）；`import_audit.jsonl` 含 path/sha256/source/model/consent/accepted/rejected/budget/reason |
| 慢 bootstrap 补渲染 | 用 CDP `Fetch` 拦截把 `/api/bootstrap` 拖 6 秒：scan 后此刻 `boot=false / 无通道状态行 / 算法仅 auto`，bootstrap 到位后补渲染为 `boot=true / 通道状态出现 / 算法 19 项`。6/6 通过 |
| 四类错误体 | `E_CONSENT_REQUIRED`=400；`E_LLM_UNAVAILABLE` **两种**：未开开关 → 503「服务端 --import-llm 关闭」，开了开关但缺密钥 → 503「缺少密钥（环境变量 X 未设置）」（前端两种都能说清）；`E_LLM_BAD_RESPONSE`=502（hint 带原始响应片段）；`E_VALIDATION`=422 且**带 `suggested_budget`**（13 行 → 建议 23） |
| `POST /api/run {"scenario":"import"}` | 返回 **400**「import 场景没有 SSE 通道」。前端 import 场景**从未请求 `/api/run`**（CDP 侧 fetch 日志：只有 `/api/bootstrap,/api/runs,/api/study,/api/import/*,/api/action`），因此不存在「流失败→误回退 mock」 |
| 端到端 | 真 CSV（14 行，含 1 行 `T=n/a`）→ scan(200) → map(consent=true, 200, `sent{url,rows:12,bytes:4642}`) → apply(200, `accepted:13, duplicate:0, rejected:[{row:7,…}]`) → 用真 `params_schema`（12 项）渲染子参数表 → 生成下一批（先 override 再 ask，返回 4 条孔板建议）。整条 UI 断言 **27/27**，无 console.error、无未捕获异常 |

### 7.7 §9.3 `sample_preview`：同意界面的数字改成**真值**（2026-11-20 追加）

Lead 裁定「同意界面上的数字必须是真值」，契约 §9.3 追加 `sample_preview{rows,bytes,endpoint}`
（`bytes` 用与 `map` **完全相同**的请求体构造路径算出，只是不发出去）。前端接法：

- `scan` 请求带 `sample_rows` 与 `model`（**两边同参**，否则不变量不成立）；
- 弹窗取值优先级：**map 实测 `sent{rows,bytes}` → scan 预告 `sample_preview`（真值）→ 前端估算兜底**；
  有真值时**不再出现**「估算，仅预览」与「由 baseURL 推导」字样，端点直接用 `sample_preview.endpoint`；
- 老后端（响应里没有 `sample_preview`，用 CDP 响应拦截摘掉字段模拟过）**保留**估算 + 明确标注「估算，仅预览」，
  并把端点标成「推导」，**不假装有真值**；
- 字节数显示为「人类可读 + 精确字节」：`4.5 KB（4642 B）`——同意要基于精确数字；
- 改 `sample_rows` / `model` → **400ms 去抖后重新 scan**（`previewOnly`）：只刷新预览，
  **保留用户已逐列改过的映射**；只有列名集合变化（换文件）才回退到新的 `rule_mapping`；
- **不变量双向核对**：`map` 回来后比 `sample_preview.bytes` vs `map.sent.bytes`：
  - 相等 → 结果卡显式打「不变量成立：scan 预告 4.5 KB（4642 B） = map 实发 4.5 KB（4642 B）· 行数 12 = 12」；
  - 不等 → 红色「⚠ 不变量违约」+ `console.warn`（**不静默、不当成估算差异**）；后端 `map.warnings` 里的差异提示也照实显示；
  - 参数与 scan 预告不一致（预览是旧的）→ 显示「不变量未比对」而不是假判违约。

**实测（真 server 8791 + 本机 stub 网关，三方数字）**：

```
sample_rows=12:  scan.sample_preview.bytes = 4642 / map.sent.bytes = 4642 / stub 网关实收 body = 4642
sample_rows=5 :  scan.sample_preview.bytes = 3566 / map.sent.bytes = 3566 / stub 网关实收 body = 3566
```
（stub 侧是 `Content-Length` 原始字节，直接量收到的 body，不是 decode 再 encode 的估算。）

| 用例 | 断言 |
|---|---|
| truth（默认 12） | **16/16**：弹窗含真值 4642、标注「后端预告真值」、无「估算/推导」、端点=预告端点、model 同时传给 scan 与 map、UI 明示不变量成立、apply 回归通过 |
| rows5（改 N + 先手改映射） | **14/14**：改 `sample_rows=5` 后预览真值 4642→3566、弹窗随之更新、三方相等；**`T.low=41.5` 与 `fidelity_tag=保真` 在重扫后原样保留** |
| oldbackend（摘掉字段） | **6/6**：仍标注「估算，仅预览」、端点标「推导」、不出现「预告真值」字样 |

截图：`_shots/preview_truth_dialog.png`（真值弹窗）、`preview_truth_invariant.png`（不变量成立那行）、
`preview_rows5_dialog.png`（改 N 后的新真值）、`preview_oldbackend_dialog.png`（老后端兜底）。




### 7.8 F7/F8：模型网关地址不合法的事前预警（2026-11-20 追加）

后端修了 F7/F8（含空白/坏端口/缺 scheme/误带 `/chat/completions`/显式置空 → 以前会静默回退默认端点，
或让 `urllib` 抛 `InvalidURL` 变成 500）。前端配合：

- `llmInfo()` 现在**合并** bootstrap 的多个候选位置（先到先得、跳过 `null/undefined`），
  因为**`import_llm` 里有 `url_valid` 却没有 `url_error`**，`url_error`（原文原因）只在同一个
  bootstrap 的 `import`(= `import_settings()`) 里 —— 只取第一个候选就会丢掉原文；
- `url_valid === false` 时：左栏先出「**事前预警**」（不是「通道已开启」的假安心），
  弹窗再出**红框**醒目预警，含 `url_error` 原文与「当前配置的 URL」，并说明「现在点确认只会拿到 503」；
- **确认发送按钮禁用**（文案「地址不合法，无法发送」，其 `data-consent` 被换成 `cancel`，
  点它只关弹窗）+ 同意勾选框禁用 + 给「改走离线规则映射（不出网）」一键出路；
- `sample_preview.endpoint` 为空串时**绝不**回退到我按 baseURL 拼的假端点，URL 行如实显示
  「地址未配置/不合法（不会外发）」；行数/字节数仍是后端真值（地址坏了也不编造）；
- 老后端（无 `url_valid`/`url_error`）**不假装有该信息**：退回估算 + 「推导」标注，确认按钮照常可用。

**实测（真 server，畸形值 `CONDOPT_IMPORT_LLM_BASE_URL='http://127.0.0.1:9/v1 /v1'`）**：

```
bootstrap.import_llm = {"enabled":true,"configured":true,"url":"http://127.0.0.1:9/v1 /v1",
                        "url_valid":false,"model":"stub-gateway-model","key_env":"CONDOPT_IT_FAKE_KEY"}
bootstrap.import     = {… 同上 …, "url_error":"模型网关地址含空白字符", "max_import_bytes":20971520,
                        "sample_rows_default":12, "sample_rows_max":50, …}
scan.sample_preview  = {"rows":12,"bytes":4624,"endpoint":""}          ← 空串，不拼假端点
scan.warnings        = […, "模型网关地址配置不合法：模型网关地址含空白字符（模型读表会返回 503）"]
```

| 用例 | 结果 | 关键断言 |
|---|---|---|
| `invalid`（畸形地址） | **16/16** | 左栏「事前预警」+ 原文；弹窗红框含 `url_error`；不出现任何可用 URL；**发送按钮 disabled +「地址不合法，无法发送」**；勾选框 disabled；规则映射出路可用（`source=rules`）；行数/字节仍是真值 |
| `valid`（合法地址，对照） | **6/6** | `url_valid=true`、无预警、弹窗显示真实端点 |
| `old`（CDP 同时摘掉 `url_valid/url_error` 与 `sample_preview`） | **11/11** | 不出现「事前预警」/「地址不合法」；退回「估算，仅预览」+「推导」；确认按钮仍可用 |

截图：`_shots/url_invalid_dialog.png`、`url_valid_dialog.png`、`url_oldbackend_dialog.png`。

> 两处与 lead 描述不一致、已按**实测**实现并上报（都不是 bug）：
> ① `import_llm.url` 实测**保留原始畸形值**（lead 描述为 `""`）——这更有用，UI 直接显示你配了哪个值；
> ② `url_error` 不在 `import_llm` 里，只在 `import` 里（故前端做候选合并）。



### 7.9 真联调之前用 stub 的验证（历史，已并入 §7.6 的结论）

后端 task-8 未就绪期间，用一个**临时 stub**（`_stub_import.py`，形状照抄 §9.3–§9.5，已删除）
+ CDP 驱动真实 Edge 点完整条流程，覆盖三条路径（happy / `E_LLM_UNAVAILABLE` / 离线），
共 66 条断言（29+24+13）。其中「外发前必须二次确认」是用 stub 侧计数器证明的：
**弹窗打开期间 `map_calls == 0`**，勾选并确认后才变成 1，且 `consent=true`。

## 8. 场景 01「对话式开工」（task-14，CONTRACT §10）

需求方原话：「直接开一个 agent 聊天窗讲清楚具体情况……应该 agent 回话并自动把可选的条件优化算法
以数个链接的方式呈现，最后再点进去要调参数的界面在他的解释下调参数。」
现状问题：01 原来是一张 **45 字段表单**（`prepare_template` 全量投影），门槛放在最前面。

### 8.1 新增 / 改动的文件

| 文件 | 动作 |
|---|---|
| `web/assets/kickoff.js` | **新增**：`window.CONDOPT_KICKOFF`（聊天 + 选项卡 + 参数页 + 建 study + 生成下一批） |
| `web/index.html` | 新增挂载点 `#condoptKickoff`（左栏·聊天）、`#condoptKickoffPlan`（右栏·参数页）；旧表单 + answers JSON 收进 `<details id="condoptAdvanced">`（默认收起，内容默认 `hidden`）；加载 `kickoff.js` |
| `web/assets/condopt.js` | `syncScenario()` 对 `action==="prepare"` 转发 `CONDOPT_KICKOFF.render()` / `hide()`；抽屉 `toggle` 时懒渲染旧表单；导出 `action`/`setOnline`/`resync`；`postAction` 抛错时保留 `code/hint/status` |
| `web/assets/app.css` | 追加聊天/芯片/选项卡/参数页样式 + **版面缺陷修复段**（见 §9） |

`app.js` **本任务未改**（10 个钩子不变）；旧「开工表单」的全部能力保留，只是不再首屏铺开。

### 8.2 交互与数据流

- **左栏首屏 = 聊天**：助手气泡（markdown）+「为什么问这个」+ 快捷回复芯片 + 输入框。
  芯片点一下就答（`content` 发**芯片的标签文本**，后端按标签精确匹配，实测可用，且聊天记录里显示的是用户
  真正说过的话而不是 JSON 串）；手打输入**带 `free_text:true`**（后端仅在此模式启用规则解析）。
- **`state` 原样回传**：`state` 只被保存与送回，前端不解析、不拼装。
  唯一例外是**首轮不带 `state`**：契约说 state 是「上一轮原样回传」，首轮没有上一轮；实测
  `state:{}` 会被后端判 422（`state.step 非法: 'None'`），省略才是「开新对话」。
  `state.missing` 是后端只读提示，只在界面上显示（「还缺：…」），**从不回写**。
- **第 5 轮后 = 算法选项链接卡**（`step==="options"`）：2–4 张 `<button>` 卡，
  含标题 / 一句话 / 为什么（why 标签）/ 取舍 / `recommended` 标记 / `risk`；
  `disqualified:true`（router 规则判死、分数 ≤ −100）**置灰但照常列出**（列出来本身就是解释价值），
  带「规则判死（不推荐）」标记且仍可点进去看参数。
- **点卡 → 右栏参数页**（`POST /api/kickoff/plan`）：
  顶部是 `why`（router 依据）+「助手为什么推荐它」；参数表用 `label` + `plain`（人话）+
  默认值 / 范围 + `impact` 徽章（影响：大/中/小）+ 按 `type` 渲染控件（choice→select、
  bool→checkbox、int/float→number、其余→text）；`advanced:true` 收进默认折叠的「高级参数」；
  `display_source==="registry"` 的参数标「说明来自算法库」（不把回落文案伪装成工作台元数据）；
  `warnings` 在独立框里**照实显示**，含 `spec_score` 的那条标成红框「真代价，不是噪音」。
- **建 study**：底部 `POST /api/action {init}`（与开工表单同一条写路径），成功后显示
  「study / 算法 / n_obs / 剩余预算 + 与导入数据、结果回填同一套 run 语义」，并给「生成下一批」
  （`ask`）与「去下一批取样场景」。
- **降级**：`/api/kickoff/*` 404/405/网络失败 → 明确写出原因（例如「后端还没有 /api/kickoff/chat
  （HTTP_501）」）+ 内置离线演示（示例对话 / 示例选项 / 示例参数，并**标明不是 registry**），不白屏。

### 8.3 同类加固必须对称（F10，task-18）

`.condopt-advanced`（旧表单抽屉）早就有 `.condopt-advanced:not([open]) > .condopt-advanced-body { display: none; }`
（外加 JS 同步 `hidden`）；但**新加的** `.condopt-advanced-params`（参数页的高级参数）一开始只依赖
Chromium 对闭合 `<details>` 子树的 paint-skip —— 收起时它的 body 仍报 **678px** 矩形，
与「建 study」块矩形重叠（verifier 量到 41,566 px²）。**今天没有用户可见影响**，
但同一份代码里两个同类组件加固程度不一致，是下一次改动会踩的隐患。已补同款规则：

```css
.condopt-advanced-params:not([open]) > .condopt-advanced-params-body { display: none; }
```

⚠ 这里**不能**沿用旧抽屉的「JS 设 `hidden` 属性」做法：后端下发的 7 个高级参数必须仍被
`readParams()` 读到值 —— `display:none` 不影响 `querySelectorAll` 取 `.value`/`.checked`，
只是不参与布局（已实测：收起态把 `n_candidates` 改成 777 → 该值仍进入 init 的 `params`
并落到 `config.json`）。

### 8.4 「幻影重叠」与「真实重叠」怎么区分

三条件**同时**成立才算真实重叠（与 verifier 的三重判定、以及 lead 修好的 `geom_probe.py` 同构）：

1. **矩形相交**（用**可滚动祖先裁切后**的有效矩形；未裁切的原始矩形会把滚出视区的东西算成重叠）；
2. **命中测试**：相交区域中心 `elementsFromPoint()` 的堆叠栈里**真的出现**该元素
   （先把该区域滚进视野，否则 `elementFromPoint` 一律 null，「没命中」就成了废话）；
3. **布局增量**：把覆盖方临时 `display:none`，被覆盖方的 y 与 `documentElement.scrollHeight`
   是否变化 —— 都为 0 说明它对布局零贡献，多为**幻影**（只作参考，不单独定案）。

两态复测（两个抽屉**收起/展开**，我的驱动 + lead 的探针各跑一遍，结论一致）：

```
我的驱动（web/_it/drive_drawers.mjs，跑完已删）：15/15
  both_closed: 0 零尺寸 / 0 真实重叠；left=display:none h=0，params=display:none h=0
  both_open  : 0 零尺寸 / 0 真实重叠；left=block，params=flex（没被误藏）
  当场复现修复前状态（内联 display 覆盖我的规则）：
    收起态 body 矩形 h=678px（与 verifier 同值）、与「建 study」重叠 71917 px²
    elementFromPoint 命中 DIV.condopt-plan-build，堆叠栈 [.condopt-plan-build, .condopt-kickoff-plan,
      .stream, .panel, .workbench] —— **栈里没有那个 body**
    布局增量 Δy=0、ΔscrollHeight=0  ⇒ 判定「幻影」（与 verifier 结论一致）
    复原我的规则后 body h=0  ⇒ F10 修复生效
lead 的探针（我只加了选择器，判定逻辑未动）：
  默认态      : 零尺寸 0 / 原始相交对 0 / 真实 0 / 幻影 0 / exit 0
  --all-open  : 零尺寸 0 / 原始相交对 0 / 真实 0 / 幻影 0 / exit 0
```

（重叠面积我与 verifier 量到 71917 vs 41566：**矩形高度一致（678px）**，面积差来自两者当时的
滚动位置不同 —— 未裁切的原始矩形会随滚动变化，这正好印证了「必须裁切感知」。
我按可滚动祖先裁切后重算的是 71917。）

截图：`_shots/f10_both_closed.png`（两抽屉收起、收起态 0 矩形）、
`_shots/f10_both_open.png`（两抽屉展开）、`_shots/f10_after_build.png`（建 study 后）。

### 8.5 真端点实测（`py -3 server.py`，30+6 项断言）与截图


```
kickoff（真后端）          36/36
kickoff/offline（纯静态）   9/9    ← 无任何 /api 也不白屏
kickoff/params → 磁盘      7/7    ← 参数页改动落到 runs/<id>/config.json（见 §8.6）
```
覆盖：首屏是聊天而不是表单 / 助手说人话（「越大越好还是越小越好」，无 `direction` 术语）/
芯片 ≥2 / 5 轮内收敛到 `options` 且 2–4 张卡 / 卡片字段齐全 / 唯一 `recommended` /
参数页解释 + `label`+`plain` + `impact` + 控件类型 + `advanced` 默认折叠 /
建 study 成功并显示 run 语义 / 生成下一批 / 旧表单仍在抽屉里可开 /
`disqualified` 置灰不下架 / `spec_score` 警告红框显示 / 无 `console.error`、无未捕获异常。

截图：`_shots/kickoff_1_chat.png`（首屏聊天）、`kickoff_2_options.png`（选项卡）、
`kickoff_3_plan.png`（参数页）、`kickoff_4_built.png`、`kickoff_5_next.png`、
`kickoff_6_advanced.png`、`kickoff_7_disqualified.png`、`kickoff_8_spec_warning.png`、
`kickoff_offline_1_chat.png` … `kickoff_offline_3_plan.png`。

### 8.6 `init` 的 `params`（task-15 已落地，端到端验证到磁盘）

契约 §10.4 说 `payload:{answers, algorithm, params}`。历史经过：先实测当前后端 `init` 白名单
只有 `['algorithm','answers','batch','budget','id']` → 带 `params` 会 422「init 的 payload 未知字段: params」；
lead 裁定「必须支持」并开 task-15。**task-15 已落地**，前端现在的行为：

- 发的 `params` 是**平表** `{参数名: 值}`（`readParams()` 直接收集参数页控件值，不嵌套、不按算法分组）；
- 后端规则「`algorithm:"auto"` + 非空 params → 422」：前端建 study 时用 `plan.algorithm` 解析出的
  **具体算法名**；万一拿不到具体算法，宁可不带 `params`（并在参数页说明），也不发注定 422 的组合；
- **兼容 task-15 之前的后端**：若仍回 `E_VALIDATION` 且点名 `params`，就退回不带 `params` 建 study，
  并在参数页**红字告知**「你改的算法子参数没有写进 study」（不静默、不假装成功）。这条兜底按要求保留，
  现在是不会触发的死路径，但换回旧后端仍然诚实。

**端到端验证（不是只看响应，直接读磁盘）**：参数页把 `kernel` 改成 `rbf`、`alpha` 改成 `0.05` → 建 study →
读 `runs/kickoff-params-landing/config.json`：

```
"params": { "kernel": "rbf", "alpha": 0.05, "length_scale": 0.3, "ard": true, ... }   ← 与 UI 传入逐字一致
"algorithm": "bayes"（具体名，非 auto）
```
UI 侧断言 7/7：`plan` 解析出具体算法 / 两个控件改动被收集 / 无「参数没写进去」红字 /
`sentParams` 平表含改动值 / 该次建 study 的 run 语义正常。截图 `kickoff_9_params_landed.png`。

**另一条已确认的细节**：chip 的 `value` 可以放在 `messages[].content` 里（对象或标签文本后端都认）。
前端选了**标签文本**（聊天记录可读，且实测被精确匹配）；纯文本规则解析只在 `free_text:true` 时启用，
所以手打输入框必带该标记。若后端将来只认 `value`，这里要改成把 `value` 序列化进 `content`。

## 9. 版面缺陷修复（`_verify/geom_probe.py` 归零）

**症状**（lead 用 DOM 几何量出来的，不是截图错觉）：`.field` 被压成 **0 高**，内部 `textarea`
（`min-height:230px`）溢出并盖住后继兄弟：

```
.field              h=   0.0     ← 被压成 0 高
#input              h= 230.0     ← 子元素溢出
实测重叠：#input × #condoptActions 面积 57126，#input × .attach-row 15527
```

**根因**：上游 `.panel-body{display:flex;flex-direction:column;flex:1;min-height:0;overflow-y:auto}`
配 `.field{flex:1;min-height:0}`。`.panel-body` 里块变多（开工表 + JSON 域 + 附件行 + 本次产出 +
闭环动作 + 结果 + 运行行）后列方向剩余空间为负，`flex:1` + `min-height:0` 允许把 `.field` 压到 0，
而它内部 `textarea` 有 `min-height` → 溢出（`overflow` 可见）压住兄弟。

**修法**（追加覆盖段，不改上游行）：

```css
.panel-body > .field, .panel-body > .attach-row, .panel-body > .outputs,
.panel-body > .condopt-form, .panel-body > .condopt-kickoff, .panel-body > .condopt-import,
.panel-body > .condopt-advanced, .panel-body > .condopt-actions, .panel-body > .condopt-result,
.panel-body > .run-row { flex: 0 0 auto; }
```

**同类的第二个坑**（自己发现）：关闭的 `<details>` 在 Chromium 里只是 `content-visibility:hidden`
——内容**仍然占版面**（实测 `.condopt-advanced-body` 仍是 317.8px 高的一块，压在
`.attach-row`/`#condoptActions` 上）。所以抽屉收起时把内容体**显式 `hidden`**（`toggle` 同步 +
CSS `:not([open])` 双保险），让它真正不参与布局。

**回归判据（原文输出见 task-14 汇报）**：

```
py -3 _verify\geom_probe.py
  → zero-size boxes: 0 / suspicious overlaps: 0 / exit 0
```
另加两态自检（探针看不到「抽屉打开」态，因为它是折叠的）：
`closed` 与 `open` 都 **0 零尺寸 / 0 可疑重叠**，截图 `layout_fix_1_closed.png`、
`layout_fix_2_advanced_open.png`。`_verify/geom_probe.py` 的 `sel` 里追加了 §8 的新容器选择器
（`#condoptKickoff` / `#condoptKickoffPlan` / `#condoptAdvanced` / `.condopt-advanced-body`），
**判定逻辑一字未改**。

> 同类的第二个抽屉 `.condopt-advanced-params`（参数页的高级参数）后来在 task-18 补了同款规则，
> 见 §8.3；「幻影重叠 vs 真实重叠」的判据见 §8.4。

## 10. 未决 / 需要注意

**task-9 的 4 个未决问题已由 lead 裁定并写进契约（2026-11-20 14:05），前端已对齐：**

| # | 裁定 | 前端现状 |
|---|---|---|
| 1 | §2.1 新增 `import_llm:{configured,url,model,key_env,enabled}`，且 `url/model/key_env` 必须是**服务端实际生效值**（绝不返回密钥） | 已读取该字段：确认弹窗**逐字列明**真实 URL 与模型；另加一行「通道状态」（是否带 `--import-llm`、密钥变量名是否就绪）。`enabled===false`/`configured===false` 时在**点确认之前**就给出预警 + 「直接改走离线规则映射」按钮（见 `_shots/align_noflag_noflag.png`） |
| 2 | `fallback:"rules"` **不需要** `consent`；规则路径不得声称已同意外发 | 规则路径发 `consent:false`（已实测 stub 侧 `last_consent===false`） |
| 3 | `fidelity`/`cost` 形状冻结为 `{column,name}`；`fidelity` 可带可选 `levels:{原值:数值}`，`cost` 无 `levels` | 最小形状已通过；并补了 `levels` 的可选输入（`low=0.3, mid=0.6, high=1.0` → `{"low":0.3,...}`），`apply` 实测收到该对象；`cost` 保持 `null` 不塞多余字段 |
| 4 | 建议预算字段名冻结为 `suggested_budget`（数字） | 主路径读 `suggested_budget`（校验收敛到正数），另两个名字作为向后兼容兜底；实测「用建议值 budget=120」按钮出现并正确回填 |

**真联调已核（详见 §7.6）**，以下 4 项由「待确认」转为「已验证」：

1. `import_llm` 字段与类型逐字一致、三个覆盖变量各自生效、无密钥泄漏 ✅
2. `scan.columns[].inferred` 实测只有 `number`/`text`，前端已覆盖 ✅（另有 `file.format`/`columns[].choices` 等增量字段，向后兼容）
3. `apply` 确实按 `levels` 换算保真档位入 `import_observations`（`fidelity ∈ {0.3,0.6,1.0}`）✅
4. 四类错误体字段已逐条核对 ✅（`E_LLM_UNAVAILABLE` 有两种成因文案，前端都能说清）

**仍开放：无。** 原「首次确认弹窗的字节数只能是前端估算」一条已由契约 §9.3 `sample_preview` 解决
（lead 采纳后冻结；前端已接入并实测三方数字相等，见 §7.7）。

**此前的（非 task-9）：**

5. `/api/run` 在 `sample/feedback/board` 上会**真的写 run 目录**（api-backend 的实现：
   流式通道不是 dry-run）。前端只把它当观感通道，权威结果仍取 `POST /api/action`，
   并在收到 `done` 后刷新 study 快照。
6. 开工表单只投影 `prepare_template` 的字段：`spec` / `constraints` / `transfer_from`
   等高级字段仍需直接编辑下面的 JSON（表单不会凭空造这些字段）。
7. `?demo=1` 等上游 URL 参数、`P`/`Esc`/`Ctrl+Enter`/`1`–`6` 快捷键、深浅色、
   演示视图与打印样式全部保留（上游行为未改）。
