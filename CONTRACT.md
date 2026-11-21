# condopt 工作台 · 接口契约 v1（冻结，2026-11-19）

> 本文件是**唯一真源**。后端与前端按它各自实现，任何一方要改契约先改这里并通知另一方。
> 冻结时间：2026-11-19 22:50（Asia/Shanghai）。冻结后新增字段允许（向后兼容），
> **改名/改语义不允许**。

## 0. 分层与唯一写者

沿用 `docs/ui/FEASIBILITY.md` §4.1 的三层架构，工作台**不引入第二份状态**：

| 层 | 实体 | 谁写 |
|---|---|---|
| 执行层（唯一写者） | `src/condopt`（`Study` / `prepare` / `registry`） | 工作台服务**进程内直接调用 Python API** |
| 产物层（唯一真源） | runs 目录：`problem.json` / `config.json` / `dataset.jsonl` / `pending.json` / `rounds.json` / `decisions.jsonl` | 只由 condopt 写 |
| 渲染层（无状态） | `web/`（复用本机 EduKit 前端） | 只读 API |

- 工作台**不解析 CLI 文本**（`report()` 这类文本只作为展示用 Markdown），决策信息一律取结构化返回值。
- 写操作全局串行（一把 `threading.Lock`），且**只在 runs 目录下写**；路径穿越一律 400。
- 服务只绑 `127.0.0.1`，无鉴权、无外网请求（与 `scripts/serve.py` 同一条安全线）。

## 1. 启动与安全

```
py -3 server.py [--host 127.0.0.1] [--port 8770] [--runs-dir PATH] [--open]
```

- `--host` 只接受 `127.0.0.1` / `localhost`，其它值直接退出（2）。
- `--runs-dir` 默认 `state/runs`；设为 `<repo>/runs` 即可直接操作仓库现有 study。
- 方法：仅 `GET` / `POST`；其余 → `405`。未知路径 → `404`。静态路径穿越 → `400`。
- 响应头：`Cache-Control: no-store`（HTML/JSON）、`Content-Type` 带 UTF-8。
- 所有 JSON 响应用 UTF-8 且 `ensure_ascii=False`。

## 2. HTTP 端点

| 方法 | 路径 | 用途 | 返回 |
|---|---|---|---|
| GET | `/` | 工作台首页 | `web/index.html` |
| GET | `/index.html` | 同上（等价路由，方便直接敲路径） | `web/index.html` |
| GET | `/assets/*` `/vendor/*` `/components/*` | 静态资源（白名单前缀） | 文件字节 |
| GET | `/api/bootstrap` | 首屏元数据 | `Bootstrap` |
| GET | `/api/runs` | 已有 study 列表 | `{"ok":true,"runs":[RunSummary]}` |
| GET | `/api/study?id=<run>` | 单个 study 快照 | `{"ok":true,"study":StudySnapshot}` |
| POST | `/api/action` | 闭环动作（JSON） | `{"ok":true,"action":str,"message":str,"result":obj}` |
| POST | `/api/run` | **流式**执行一个场景（SSE，兼容 EduKit） | `text/event-stream` |
| POST | `/api/import/scan` | 导入：本地只读读取 + 列画像 + 规则映射 + 发送预览（**不出网**） | `ScanResult`（§9.3） |
| POST | `/api/import/map` | 导入：调模型读表推断列映射（**唯一会出网的端点**） | `MappingResult`（§9.4） |
| POST | `/api/import/apply` | 导入：作先验导入并返回调参 schema | `ApplyResult`（§9.5） |

> 导入相关端点只在 §2 总表登记一行说明；**字段级真源在 §9**，冲突以 §9 为准。

错误模型（`/api/*` 全部一致）：

```json
{"ok": false, "error": "E_NOT_FOUND", "message": "run 不存在: demo2", "hint": "先用 GET /api/runs 看可用 study"}
```

错误码：`E_BAD_REQUEST`(400) `E_NOT_FOUND`(404) `E_CONFLICT`(409，例如 pending 未回填)
`E_VALIDATION`(422，condopt 的 `ParamSpec`/`answers` 校验失败)
`E_CONSENT_REQUIRED`(400，导入外发未同意) `E_LLM_UNAVAILABLE`(503，导入开关/密钥缺失)
`E_LLM_BAD_RESPONSE`(502，模型两次都未返回合法 JSON) `E_INTERNAL`(500)。

### 2.1 `Bootstrap`

```json
{
  "ok": true,
  "condopt": {
    "version": "0.1.0",
    "runs_dir": "<runs-dir>",
    "algorithms": [ "…见下…" ]
  },
  "prepare_template": { "…": "src/condopt/prepare.py::template() 原样返回" },
  "scenarios": [ Scenario, "…" ],
  "efforts": {"quick":40,"normal":160,"thorough":640},
  "import_llm": {"configured": false, "url": "https://open.bohrium.com/openapi/model-gateway/v1",
                 "model": "qwen3.8-27b", "key_env": "BOHRIUM_API_KEY", "enabled": false,
                 "url_valid": true},
  "import": {"url_error": null, "max_import_bytes": 20971520,
             "sample_rows_default": 12, "sample_rows_max": 50}
}
```

- `import_llm` 是**新增字段**（2026-11-20 14:05 加入，向后兼容），用途：让前端在**外发之前**的二次确认弹窗里
  逐字列明「将发往哪个 URL、哪个模型」。`url`/`model`/`key_env` 必须来自服务端**实际生效的配置**
  （含环境变量覆盖后的值），`configured` 表示密钥是否已就绪，`enabled` 表示服务端是否带 `--import-llm` 启动。
  **绝不返回密钥本身**，也不返回它的任何片段。
- `url_valid`（2026-11-20 15:15 加入）表示生效的网关地址是否可用；`false` 时 `map` 必然 503，
  前端应在**同意之前**预警并禁用确认（这正是「不给假安心」）。
- **字段落位（2026-11-20 15:20 冻结，与实现一致）**：`url_error`（人类可读的不合法原因）与
  `max_import_bytes` / `sample_rows_default` / `sample_rows_max` 放在 **`import`** 块里；
  `import_llm` 只带 `url_valid`（bool）。两个块里的 `url/model/key_env/enabled/configured/url_valid` 必须一致。
  `url` 保留**操作者实际配置的原始值**（便于弹窗显示「你配的是哪个值」）；地址不合法时
  `scan.sample_preview.endpoint` 给**空串**，**不得**拼一个看着能用、实则 503 的假 URL。

- `algorithms` 必须来自 `condopt.registry.describe_all()`，**不手写**（防「文档一套、代码一套」）。
- `prepare_template` 必须来自 `condopt.prepare.template()`，**不手写**。
- `Scenario` 由后端定义并被前端消费（见 §3.1）。

### 2.2 `RunSummary` / `StudySnapshot`

```json
RunSummary = {"id":"demo","n_obs":24,"n_rounds":6,"best":96.4,"direction":"max",
              "algorithm":"bayes","remaining":0,"budget":24,"has_pending":false}
```

`StudySnapshot`（全部字段都要在，缺数据给 `null` 或 `[]`，**不许伪造**）：

```json
{
  "id": "demo",
  "problem": { "…": "problem.json 原样" },
  "config":  { "…": "config.json 原样" },
  "n_obs": 24, "remaining": 0, "cost_spent": 0.0,
  "best": {"x":{...}, "y":96.4, "t":12, "source":"ask"},
  "rounds": [ {"round":1,"t":4,"n":4,"algorithm":"bayes","best":41.2} ],
  "pending": [ {"id":"p1","batch":7,"algorithm":"bayes","x":{...}} ],
  "decisions": [ {"t":0,"action":"initial_route","algorithm":"bayes","reason":"…"} ],
  "step": { "…": "Study.next_action() 原样" },
  "recommend": { "…": "Study.recommend() 原样" },
  "delivery": [ {"…": "Study.delivery_checklist() 原样"} ],
  "integrity": { "…": "Study.check_integrity() 原样" }
}
```

## 3. `POST /api/action`

请求：`{"action":"<name>","run":"<run-id 或 null>","payload":{…}}`

| action | payload | condopt 调用 | `result` 关键字段 |
|---|---|---|---|
| `prepare` | `{"answers":{…}}`（`prepare.template()` 的形状） | `prepare.parse_answers` + `noise_advice` / `stopping_advice` / `feasibility_advice` / `deliverable_advice` / `prior_advice` + `registry` 路由 | `{"sections":[{"key":"stages|stopping|feasibility|deliverable|prior|noise","title":str,"markdown":str}],"problem":{…},"route":{…}}` |
| `init` | `{"id":str,"answers":{…},"algorithm":"auto","params":{…}?,"budget":int?,"batch":int?}` | `prepare.write_problem` + `Study.create(...)` | `{"run":RunSummary}` |
| `ask` | `{"n":int?,"plate":str?}` | `Study.ask` | `{"run":RunSummary,"pending":[Pending]}` |
| `tell` | `{"observations":[{...}]}` 或 `{"csv":str}` | `Study.tell` | `{"run":RunSummary,"accepted":int}` |
| `step` | `{}` | `Study.next_action` | `{"step":{…}}` |
| `recommend` | `{}` | `Study.recommend` | `{"recommend":{…}}` |
| `delivery` | `{}` | `Study.delivery_checklist` + `check_integrity` | `{"delivery":[…],"integrity":{…}}` |
| `override` | `{"algorithm":str,"reason":str,"params":{…}?}` | `Study.override` | `{"decision":{…}}` |
| `render` | `{}` | `condopt.render.render_study` | `{"dashboard":"<绝对路径>","png":str|null,"warnings":[…]}` |
| `report` | `{}` | `Study.report()` | `{"markdown":str}` |

规则：

- 写动作（`init/ask/tell/override/render`）需要 `run`（除 `init`），且**全局串行**。
- `ask` 在已有未回填 pending 时 → `E_CONFLICT`（不静默覆盖）。
- `payload` 里的未知字段**不允许静默忽略** → `E_VALIDATION` 并指出字段名。
- `message` 是一句人话结论（进前端 toast/复核栏），`result` 是结构化数据（进前端渲染）。

## 4. `POST /api/run`（SSE，兼容 EduKit `adapter.example.js`）

请求（EduKit 原契约 + 一个 `payload` 扩展字段）：

```json
{"scenario":"kickoff","text":"可选自由文本","files":[],
 "payload":{"answers":{…},"run":"demo","n":4}}
```

响应：`Content-Type: text/event-stream`，逐帧 flush，帧格式与 EduKit 完全一致：

```
event: meta
data: {"run_id":"...","mode":"live","model":"condopt-0.1.0"}

event: stage
data: {"index":0,"label":"校验问卷答案","status":"running"}
data: {"index":0,"label":"校验问卷答案","status":"done"}

event: delta
data: {"text":"## 开工建议\n"}

event: notice
data: {"message":"..."}

event: error
data: {"message":"...","hint":"..."}

event: done
data: {"run_id":"...","elapsed_ms":812,"mode":"live"}
```

- `stage` 的 `index` 必须对应 `Scenario.stages[index]`（前端 `renderStages()` 依赖它）。
- `delta.text` 是 Markdown 片段，可含 KaTeX `$…$`；前端用 marked+DOMPurify+KaTeX 渲染。
- 后端把 action 的结构化结果渲染成 Markdown 再流式下发；**流式只负责观感，权威结果仍以 `/api/action` 的返回为准**。
- 任一帧失败 → 发 `error`（页面不崩），最后仍发 `done`。

### 4.2 副作用语义（2026-11-19 23:35 裁定，防「双通道重复写」）

`POST /api/run` 与 `POST /api/action` **走同一条 action 层，副作用完全一致**（不是 dry-run）：
用户在场景上点「开始处理」就是在真的推进实验，这对工作台是正确语义。

因此必须满足（缺一即 bug）：

1. **同一动作不得被静默重复执行**：`ask` 在已有未回填 `pending` 时返回 `E_CONFLICT`；
   `tell` 必须幂等（同一 `id` 重复回填不重复计入预算/数据集）；`render` 幂等（覆盖写产物）。
2. **`GET` 一律无副作用**：任何 `GET /api/*`（含 `/api/study`）不得写 run 目录。
3. 前端在收到 `done` 后必须刷新 study 快照；「场景开始处理」与「动作条」是同一动作的两个入口，
   UI 上要能看出当前 run 的 `pending` 状态，避免用户以为按钮没生效而连点。
4. 测试必须包含：连续两次 `ask` → 第二次 `E_CONFLICT`；连续两次同 `id` `tell` → 幂等。

### 4.1 `Scenario`（后端定义，前端消费）

```json
{"key":"kickoff","no":"01","title":"开工建议","tagline":"六段开工建议：算法·停止·可行率·先验·交付口径·噪声处置",
 "short":"开工","action":"prepare",
 "stages":["校验问卷答案","解析条件空间","路由算法","生成六段建议"],
 "needs_run":false}
```

六个场景（key 固定，顺序固定）。**下表是 `stages` 的权威清单（2026-11-19 22:57 冻结；2026-11-20 14:25 追加第 6 行 `import`）**：
后端 `Scenario.stages` 与前端 `data.js` 必须与本表**一字不差**；前端仍应在 `/api/bootstrap` 可用时
以后端值为准并重新渲染（防两边漂移），但漂移本身就是 bug，要修而不是靠兜底遮住。

| key | title | no | action | needs_run | stages（4 段，下标即 `stage.index`） |
|---|---|---|---|---|---|
| `kickoff` | 开工建议 | 01 | `prepare` | false | `校验问卷答案` / `解析条件空间` / `路由算法` / `生成六段建议` |
| `sample` | 下一批取样 | 02 | `ask` | true | `校验 study 与待回填状态` / `读取已有观测` / `调用 ask 生成下一批` / `写入 pending 与孔板` |
| `feedback` | 结果回填 | 03 | `tell` | true | `解析回填结果` / `校验 id 与约束` / `写入 dataset 与 rounds` / `确认 pending 清空` |
| `delivery` | 交付自查 | 04 | `delivery` | true | `汇总交付清单` / `跑完整性自检` / `给出收尾建议` / `生成可交付摘要` |
| `board` | 看板 | 05 | `render` | true | `读取 run 目录` / `渲染收敛与前沿` / `写入 dashboard.html` / `检查离线资源` |
| `import` | 导入数据 | 06 | `import` | false | `读取文件` / `画像与列类型` / `让模型读表` / `导入为先验` |

注：`import` **不提供 `POST /api/run` 的 SSE 场景**——它的流程走 `/api/import/{scan,map,apply}` 三个端点，
`POST /api/run {"scenario":"import"}` 明确返回 `400` 并指向这三个端点（避免前端把它误判为「流失败 → 回退 mock」）。

## 5. 静态资源与前端适配规则

- 复用来源：`<本机>/Project2/frontend`（EduKit 复用包，无构建/无 npm/断网可跑），已拷入 `web/`。
- **不改**：`web/vendor/**`（含 `licenses/`）、`web/assets/base.css` 的色板语义、`web/assets/components.css`。
  品牌变量可在 `app.css` 里覆盖，不改上游文件。
- **必改**：`web/assets/data.js` → condopt 六场景；`web/index.html` → condopt 产品名/导航。
- **新增**：`web/assets/condopt.js`（condopt 专属：结构化开工表单挂载、`EduKit.EduAPI.run` 指向 `/api/run`、闭环动作条、study 列表）。
- **最小改动**：`web/assets/app.js` 只允许「加钩子」式改动（例如把 `payload` 透传给 `EduAPI.run`），
  每处改动要在 `web/ADAPTATION.md` 里记一行「改了什么/为什么」。
- 前端**必须**在无后端时降级：`EduAPI.run` 失败且未收到任何 `delta` → 回退 `EduKit.mockRun`（上游已有此行为，保留）。
- 不引入构建步骤、不引 CDN、不发外网请求。

## 6. 文件所有权（并行写作用域，互不重叠）

| 所有者 | 文件 |
|---|---|
| `api-backend` | `server.py`、`bridge.py`、`tests/test_workbench.py` |
| `web-shell` | `web/**`（含新增 `ADAPTATION.md`） |
| `lead` | `CONTRACT.md`、`README.md`、`start.cmd`、`docs/WORKBENCH.md` |

任何人不得修改他人作用域内的文件；需要跨界改动 → 发消息给该所有者。

## 7. 验收（对工作台本身）

1. `py -3 -m pytest tests/test_workbench.py -q` 全绿。
2. `py -3 server.py --port 0 --selftest` 退出码 0（起服务→自检→退出，不挂住）。
3. 离线：`grep -r "https\?://" web` 除注释/许可外无外链；页面加载零外网请求。
4. 端到端：`GET /api/bootstrap` → `POST /api/action {prepare}` → `POST /api/action {init}` → `ask` → `tell` → `delivery` → `render`，每步 `ok:true`。
5. 安全：非 GET/POST → 405；`/../etc/passwd` → 400；只绑 127.0.0.1。
6. 契约一致性：`/api/run` 的 SSE 帧能被 `EduKit.parseSSE` 语义解析（前端 `adapter` 不改解析器）。

## 8. 实现裁定（与契约字面不同但已追认，2026-11-20 00:20）

契约冻结后由实现者提出、经 Lead 复核追认的 6 条。**契约真源以本节为补充，不再追溯修改上文。**

| # | 议题 | 裁定 |
|---|---|---|
| 1 | `prepare` 的 `sections[key="stages"]` 语义 | 内容为「算法与开工步骤」：router 结论 + 参数 + 理由 + 备选 + 开工顺序；数据全部来自 `route()` 与 `context`，不发明内容。`result.route` 另行原样返回。 |
| 2 | `init` 落盘位置 | 调 `prepare.write_problem(answers, run_dir)` —— 因此 run 目录除数据契约的 6 个文件外，**还会多出 condopt 自己写的 `space.json` / `context.json` / `objective.json`**。这是 condopt 既有行为（CLI 的 prepare 同样产物），保留，不算污染。 |
| 3 | 错误码补全 | 未知 action → `400 E_BAD_REQUEST`；缺 `run` → `400`；未知 payload 字段/类型错 → `422 E_VALIDATION`（message 点名字段）。`/api/run` 的**信封**错误（未知 scenario / 未知顶层字段 / 缺 run）开流前返回 4xx JSON；**执行期**失败才发 SSE `error` 帧后补 `done`（同时保住 adapter 的失败回退与「页面不崩」）。 |
| 4 | `render` 的可选参数 | payload 允许可选 `make_png`（默认 true），其余字段仍严格白名单。`png` 取 `render_study()["convergence_png"]`，无则 `null`。 |
| 5 | `/api/runs` 遇到坏 run | 不静默丢弃、不伪造数字：照常输出可算字段，其余 `null`，并附 `error` 字段说明。`RunSummary` 新增 `best_condition`（向后兼容）。 |
| 6 | `StudySnapshot.best` | 用 `Observation.to_dict()`（含 `x/y/t/source/fidelity`）。**§2.2 示例里的 `t:12` 是错的**，`t` 是真实时间戳；以真源为准。 |

### 8.1 已知且接受的代价

- `init` 会多落 3 个文件（见 #2）。
- `POST /api/run` 对 `sample/feedback/board` 是真写盘（§4.2），因此「场景开始处理」与「动作条」是同一动作的两个入口；UI 已展示 pending 状态以降低连点风险，服务端由 `E_CONFLICT` / `tell` 幂等 / `render` 幂等三重兜底。

## 9. 导入已有数据（v2 新增，2026-11-20 13:45 冻结）

> 目标：**粘一个文件路径 → 后端读 → 让模型判断「哪几列是条件、哪列是目标」→ 人工确认 → 作先验导入（计预算、可暖启动）→ 直接落到该算法的子参数表单。**
> 需求方原话：「给一个导入已有数据的方式，粘贴相关文件路径，后台 AI 直接读，读完了直接到调数子参数」。
> 需求方已明确选择：**用模型读**（不是纯规则）＋ **作先验导入、计预算、参与优化**。

### 9.1 边界：这是 §5「不发外网请求」的唯一例外，且默认关闭

- 工作台其余部分仍然完全离线。**只有 `POST /api/import/map` 会出网**，且必须同时满足：
  `--import-llm` 启动开关打开 **且** 环境变量提供密钥。
- 未满足时 `map` 返回 `E_LLM_UNAVAILABLE`，**不静默降级**；前端可显式让用户改走 `fallback:"rules"`（规则映射，离线）。
- 外发内容最小化：只发**表头 + 列画像 + 前 N 行样本**（`sample_rows` 默认 12，上限 50），不发整文件。
- 外发前必须 `consent: true`（前端做二次确认弹窗，列明：目标 URL、模型 id、行数、字节数）。
  缺失或 false → `400 E_CONSENT_REQUIRED`。
- 密钥只从环境变量读（默认 `BOHRIUM_API_KEY`），**不写盘、不回显、不进日志**；响应里只能出现「是否已配置」。
- 本机既有通道（复用，不新建）：`baseURL=https://open.bohrium.com/openapi/model-gateway/v1`，
  `api: openai-completions`（即 `POST {baseURL}/chat/completions`），来自
  `~/.dsh/profiles/desktop/cordis.patch.yml` 的 `providers.bohrium`。
  可用环境变量覆盖：`CONDOPT_IMPORT_LLM_BASE_URL` / `CONDOPT_IMPORT_LLM_API_KEY_ENV` / `CONDOPT_IMPORT_LLM_MODEL`。

### 9.2 路径安全（服务无鉴权，这条是硬约束）

- 只接受落在**允许根**内的路径：`--allow-import-root`（可多次给，默认 = 仓库根 + runs-dir）。
- 允许根外 / `..` / 符号链接逃逸 / 目录 / 设备名 → `400 E_BAD_REQUEST`（`resolve()` 后必须仍在允许根内）。
- 后缀白名单：`.csv` `.tsv` `.txt` `.json` `.jsonl`（其它后缀 → 400，说明支持哪些）。
- 大小上限默认 20 MB（`--max-import-bytes`）；超限 → 400。
- 三个端点（`scan` / `map` / `apply`）**全部只读文件本身**；只有 `apply` 写 runs 目录。

### 9.3 `POST /api/import/scan`

请求 `{"path": "<路径>"}`（相对路径按 `--allow-import-root` 的第一个根解析）。**不调模型、不出网。**

```json
{
  "ok": true,
  "scan_id": "<sha256(path+size+mtime) 前 16 位>",
  "file": {"path":"<绝对路径>","bytes":12345,"mtime":1795.1,"encoding":"utf-8","delimiter":",","has_header":true},
  "n_rows": 90,
  "columns": [
    {"name":"T","inferred":"number","missing":0,"unique":18,"min":40.0,"max":120.0,"samples":["40","55","70"]},
    {"name":"solvent","inferred":"text","missing":0,"unique":3,"choices":["MeCN","EtOH","water"],"samples":["MeCN","EtOH"]}
  ],
  "rule_mapping": { "…": "规则推断的映射，形状同 §9.4 的 mapping；置信度低的列标 warnings" },
  "warnings": ["第 4 列 'note' 全部为空，未参与映射"]
}
```

- 所有列画像字段必须来自**真实统计**，缺就 `null`，不许编造。
- `.json`/`.jsonl` 视为「已经是 condopt 观测」的行；CSV/TSV 视为「宽表，需要映射」。
- **`sample_preview`（2026-11-20 14:40 追加，让「同意」基于真值而不是估算）**：
  请求可带可选 `sample_rows`（缺省 12，上限 50）；响应必须带

  ```json
  "sample_preview": {"rows": 12, "bytes": 3781,
                     "endpoint": "https://open.bohrium.com/openapi/model-gateway/v1/chat/completions"}
  ```

  `rows`/`bytes` 必须是**「若此刻按这个 `sample_rows` 调 `map`，实际会发出的请求体行数与字节数」的真值**
  （用与 `map` 完全相同的提示词构造路径算出来，只是不发出去；构造提示词不需要密钥，所以未配置时也要能给）。
  **硬不变量**：同一文件、同一 `sample_rows` 下，`scan.sample_preview.bytes` 必须**等于**随后 `map.sent.bytes`
  （自动测试必须断言这条；不一致就是 bug，不是「估算差异」）。`endpoint` 按实际生效配置给出，供前端事前预警。

### 9.4 `POST /api/import/map`

请求：
```json
{"scan_id":"<来自 scan>","consent":true,"sample_rows":12,
 "model":"qwen3.8-27b","fallback":"rules"}
```
`scan_id` 必须对应本进程内最近一次成功的 scan（否则 `400 E_BAD_REQUEST`，防止「扫 A 打 B」）。

响应的 `mapping`（前端据此渲染确认表）：
```json
{
  "ok": true,
  "source": "llm" ,                       // 或 "rules"
  "model": "qwen3.8-27b",
  "sent": {"url":"https://open.bohrium.com/…","rows":12,"bytes":812,"redacted":false},
  "mapping": {
    "variables": [{"column":"T","name":"T","kind":"continuous","low":40,"high":120,"unit":"degC"}],
    "objective": {"column":"yield","name":"yield","direction":"max"},
    "constraints": [{"column":"purity","op":">=","value":95}],
    "fidelity": null,
    "cost": null
  },
  "confidence": 0.82,
  "rationale": "……",
  "warnings": ["'batch' 列疑似板号，未放入条件空间"]
}
```

- `kind` 只能取 `src/condopt` 支持的取值（`continuous` / `integer` / `log` / `categorical` / `bool` / `ordinal`）；非法值 → 服务器把该列降级为 `continuous` 或 `categorical` 并在 `warnings` 说明（不静默丢弃）。
- 模型返回非 JSON / JSON 不合法 → 先做一次「只修格式」的重试，仍失败 → `502 E_LLM_BAD_RESPONSE`，附原始响应前 500 字符。
- `constraints` / `fidelity` / `cost` 允许为 `null` 或空数组。
- **`consent` 只在真的会调模型时必填**（2026-11-20 14:05 裁定）：请求显式带 `fallback:"rules"`，
  或服务端未配置/未开启 LLM 而只能走规则时，**不需要** `consent`；只有在服务端**即将发起外发调用**时才校验，
  缺失 → `400 E_CONSENT_REQUIRED`。规则映射路径返回 `source:"rules"`，且**不得**声称已同意外发。
- `fidelity` / `cost` 非 null 时形状（2026-11-20 14:05 冻结）：
  `{"column":"<列名>","name":"<列名，缺省同 column>"}`；`fidelity` 可再带可选的
  `"levels":{"<文件里的原值>": <数值>}`（多保真档位换算）。`cost` 不定义 `levels`。
- **`mapping` 是建议，不是真源**：`apply` 一律以请求里用户确认过的 mapping 为准。

### 9.5 `POST /api/import/apply`

请求：
```json
{"id":"campaign_from_csv","path":"<同 scan 的 path>","scan_id":"<可选，用于校验未被换文件>",
 "mapping":{…},"algorithm":"auto","params":{},
 "budget":120,"batch":4,"reason":"导入历史 90 次实验作为先验",
 "count_toward_budget":true}
```

- 用 `mapping` 把每行转成观测 → `Study.create`（若 `id` 不存在）→ `Study.import_observations`。
- **`budget` 必须 ≥ 已接受行数**；若调用方给的 `budget` 小于导入行数 → `422 E_VALIDATION` 并给出建议值。
  错误体必须显式带 **`suggested_budget`（数字，2026-11-20 14:05 冻结字段名）**，例如
  `{"ok":false,"error":"E_VALIDATION","message":"…","suggested_budget":120}`，前端据此给「用建议值」按钮。
  这是 `import_observations` 的既有语义（先验计入 `budget`），**不许静默把预算放大**。
- 返回：
```json
{"ok":true,
 "run":{"id":"…","n_obs":90,"remaining":30,"budget":120,"algorithm":"bayes",…},
 "imported":{"accepted":88,"duplicate":2,"rejected":[{"row":7,"reason":"'T' 不是数值：'n/a'"}]},
 "warnings":["导入 88 条后剩余预算 32；如需更多轮次请调大 budget"],
 "params_schema":[{"name":"kernel","type":"choice","default":"matern52","choices":[…],"description":"…"}],
 "next": "ask"}
```
- `params_schema` 来自 `registry` 的 `AlgorithmSpec.params`（**代码取值，不手写**），前端据此渲染「调数子参数」表单。
- 拒绝行必须逐行给原因，不静默丢；`duplicate` 是 `import_observations` 的去重结果。
- 审计：把 `{"t":…,"path":…,"file_sha256":…,"source":"llm|rules","model":…,"consent":true,"accepted":…,"rejected":…,"budget":…,"reason":…}` 追加进 `runs/<id>/import_audit.jsonl`。
- **`count_toward_budget` 语义（2026-11-20 14:25 裁定）**：condopt 的 `import_observations` **无法**关闭
  「先验计入 budget」，所以这个开关**不是可选项**：
  - 前端把它**禁用并固定勾选**，旁边写明「先验导入必然计入预算（condopt 语义），无法关闭」——不要给一个做不到的选择；
  - 后端收到 `count_toward_budget:false` 时**照常导入**（不阻断有效数据导入），但必须：返回一条明确 `warnings`、
    在 `import_audit.jsonl` 记 `count_toward_budget_requested:false`，**不得**静默当作已满足用户意图。

### 9.6 前端要求（`web/`）

- 新增场景 `import`（编号 `06`，`action:"import"`，`needs_run:false`），并入场景侧栏与 `1`–`6` 快捷键。
- 流程：**路径输入 → 读取（预览列画像与前几行）→ 映射确认表（每列角色可改）→ 二次确认「发送 N 行给 <model>」→ 模型建议 + 理由 + 置信度 → 应用 → 落到算法子参数表单**。
- 「直接到调数子参数」= `apply` 成功后立刻用返回的 `params_schema` 渲染算法 + 子参数表单（不是让用户再点一次），并显示导入统计、剩余预算与「生成下一批」按钮。
- 模型不可用/未配置：明确提示缺什么（开关 / 环境变量名），可一键改走离线规则映射，**不静默降级**。
- mock 降级仍要工作（离线时展示示例映射并标注「离线演示」）。

### 9.7 验收（对导入功能）

1. `scan`：CSV/TSV/JSON/JSONL 各一例；列画像与真实统计一致；`..`/根外/目录/错误后缀/超限全部 400。
2. `map`：未开 `--import-llm` → `E_LLM_UNAVAILABLE`；无 `consent` → `E_CONSENT_REQUIRED`；`scan_id` 不匹配 → 400；
   模型返回非 JSON → `E_LLM_BAD_RESPONSE`；**用假网关（本机 stub）跑通成功路径**，断言「只发了表头+样本、没发整文件」与「密钥不出现在任何日志/响应里」。
3. `apply`：导入 90 行 → `n_obs=90`、`budget` 校验生效、重复行计入 `duplicate`、坏行给原因；
   `import_audit.jsonl` 有对应记录；`params_schema` 与 `registry.describe_all()` 逐字段一致。
4. 端到端：scan → map(假网关) → apply → 用返回的 `params_schema` 调 `ask` 出下一批，全 `ok:true`。
5. 默认配置下（不开 `--import-llm`）**全仓离线断言仍然成立**：`web` 无新增外链、页面加载零外网请求。
6. 回归：`README.md` 的「不发外网请求」表述必须改成「默认不发；导入时按 §9.1 显式开启」——**文档不能与实现矛盾**。

## 10. 对话式开工（第 1 环节重做，2026-11-21 00:20 冻结）

> 需求方原话：「直接开一个 agent 聊天窗讲清楚具体情况……应该 agent 回话并自动把可选的条件优化算法以数个链接的方式呈现，最后再点进去要调参数的界面在他的解释下调参数。」
> 现状问题：第 1 环节是一张**45 个字段的表单**（`prepare_template` 全量投影），
> 用户要先看懂 `kind: continuous|log|categorical`、`noise: medium`、`budget: 24` 才能开工——
> 门槛放在最前面，而这恰恰是 condopt 能替用户判断的部分。

### 10.1 交互形态（替换「开工建议」的表单，不删旧能力）

**01 场景改成对话**：左栏是聊天记录（用户/助手气泡），底部是输入框 + 快捷回复芯片。
助手每轮只问**一个**问题，并用**人话**给出「为什么问这个 / 不回答会怎样」。

流程（5 轮内必须收敛到选项）：

| 轮次 | 助手问 | 快捷回复示例 | 落到哪个字段 |
|---|---|---|---|
| 1 | 你要优化什么结果？越大越好还是越小越好？ | 收率越大越好 / 纯度越大越好 / 成本越小越好 | `objective.name` / `direction` |
| 2 | 一次实验大概多贵？总共准备做多少次？ | 便宜·40 次 / 一般·160 次 / 很贵·24 次 | `budget` / `effort` |
| 3 | 有哪些条件可以调？（可多选） | 温度 压力 溶剂（分类）/ 我贴一份 →（走 06 导入） | `variables` |
| 4 | 测量噪声大吗？响应面光滑吗？ | 噪声小·光滑 / 噪声中 / 噪声大·跳变 | `noise` / `smoothness` |
| 5 | 有没有硬约束（必须满足的指标）？ | 没有 / 纯度 ≥ 95 | `constraints` |

**第 5 轮结束后，助手回话 = 算法选项**（这是需求方要的「自动把可选算法以数个链接呈现」）：
一次给出 **2–4 个**可点选项，每个都是一张卡（前端用 `<button>` 渲染，不用裸 URL）：

```json
{
  "key": "bayes",
  "title": "贝叶斯优化（bayes）",
  "one_line": "样本效率最高，24 次预算里最可能逼近最优",
  "why": ["维度 ≤ 12", "目标光滑", "预算少、每次实验贵"],
  "tradeoff": "代理模型假设光滑；若响应面有跳变会退化",
  "recommended": true,
  "risk": "low"
}
```

点进去 → 右栏变成**该算法的参数页**（见 §10.3），并在参数页顶部保留**助手的解释**
（为什么推荐它、每个参数是什么、动了会怎样），而不是让用户面对一堆 `ParamSpec` 字段。

### 10.2 `POST /api/kickoff/chat`

请求：`{"messages":[{"role":"user|assistant","content":"..."}],"state":{…}}`
- **首轮必须省略 `state`（或传 `null`）**：`state:{}` 是非法的（会 422 `state.step 非法: 'None'`）。
  之后每一轮把上一轮响应里的 `state` **原样回传**——前端不解析、不拼装。
  （这条 2026-11-21 01:00 明确：契约早期示例写的是 `state:{…}`，容易让人第一轮踩坑。）
- `state` 是服务端上一轮返回的 `state` 原样回传（前端不解析、不拼装）——**状态在服务端，前端只负责显示**。
- 可选 `{"free_text":true}`：允许用户不用芯片、直接打字（由规则解析数字/枚举；解析不了就再问一次，**不许瞎猜**）。

响应：
```json
{
  "ok": true,
  "reply": {"text": "…markdown，1–3 句，含「为什么问这个」…", "quick_replies": [{"label":"…","value":{…}}]},
  "state": {"step":"objective|budget|variables|noise|constraints|options|done","answers":{…}},
  "ready": false,
  "options": null,
  "candidates": null
}
```
- `step` 到 `options` 时：`options` 是 §10.1 的选项数组（2–4 个），`candidates` 可为 null。
- 用户答完之后若信息不足以路由（例如没有任何可调变量）→ `reply.text` 必须**明说缺什么**，
  `step` 停在原处，**不许**给一个「凑出来的」选项。

### 10.3 `POST /api/kickoff/plan`

请求：`{"state":{…},"algorithm":"bayes"}`（或 `"auto"`）

响应：
```json
{
  "ok": true,
  "algorithm": "bayes",
  "why": ["…来自 router 的规则命中，原样透出…"],
  "params": [
    {"name":"kernel","type":"choice","default":"matern52","choices":["matern52","matern32","rbf"],
     "label":"核函数","plain":"越光滑的数据用 rbf，一般用 matern52",
     "impact":"high|medium|low","advanced":false}
  ],
  "answers": { "…可回传给 init 的 prepare_template 形状…" },
  "warnings": []
}
```
- `params` 必须来自 `registry.describe_all()` 的 `params`（**代码取值**）；
  `label` / `plain` / `impact` 是**展示层增补**（人工维护的中文说明，缺则回落 `description`），
  必须在响应里显式标明来源，不许把它伪装成算法库自带的元数据。
- `advanced:true` 的参数默认折叠，避免又把用户淹掉。

### 10.4 与既有接口的关系

- **`init` 扩展为接受 `params`（2026-11-21 01:00 裁定）**：参数页点「建 study」→
  `POST /api/action {init, payload:{id, answers, algorithm, params}}`。
  - `params` 形状是**平表** `{参数名: 值}`（与 condopt CLI `init --params` 的 JSON 一致，
    也与 `Study.create(params=…)` 一致）；不要嵌套 `{"params":{…}}`，也不要带 `algorithm` 前缀。
  - 服务端必须用 `registry.validate_params(algorithm, params)` 校验：未知键或值非法 → `422 E_VALIDATION`
    并**点名字段**；不允许静默忽略（否则用户以为改了、其实没生效）。
  - `algorithm == "auto"` 时**不接受非空 `params`**（还没路由出算法，参数无处安放）→ `422`，
    message 明确「auto 路由下不能指定算法参数，请先选定算法或改用 override」。
  - 落地后 `config.json` 里的 `params` 必须等于用户确认过的值（前端/验收都要能验证这一条）。
  - 兼容：不传 `params` 时行为与旧版完全一致（既有测试不许红）。
  对话与参数页只是把 `answers` 攒出来的**另一种入口**，最终仍走同一条写路径（唯一写者不变）。
- **不删旧表单**：03「结果回填」/ 06「导入数据」等仍用自己的表单；旧的「开工表」全量投影
  降级为参数页里的「高级：直接改 answers JSON」抽屉。
- **模型通道**：这是「顾问」而不是「读表」，**默认用确定性规则**（router + 人工文案），不需要密钥；
  若开了 `--import-llm`，可选让模型润色 `reply.text`（**只润色措辞，不改 JSON 结构、不改路由结论**），
  失败必须回落到规则文案。

### 10.5 验收

1. 五轮内收敛：空 state 连续喂 5 条芯片答案 → `step=="options"` 且 `2 <= len(options) <= 4`。
2. 每轮 `reply.text` 非空、含「为什么问这个」一类解释，`quick_replies` 至少 2 个（最后一轮除外）。
3. 信息不足（例如变量为空）→ 停在原步并说明缺什么，`options` 必须为 null。
4. `plan` 的 `params` 与 `registry.describe_all()[算法].params` 逐字段一致（name/type/default/choices）。
5. 前端：01 场景首屏是聊天（不是表单）；选项是可点按钮；点进后右栏出现参数页（含解释 + 默认折叠 advanced）；
   点「建 study」复用 `init` 且页面能看出它和「导入数据」「结果回填」是同一套 run 语义。
6. 离线可用：不开 `--import-llm` 时整条对话与参数页功能完整（无外网请求）。
7. 回归：`POST /api/action {prepare}` 旧行为不变（旧测试不红）。

## 11. 收尾补齐（§21.10 未覆盖项，2026-11-21 09:40 冻结）

> 上一轮冻结时如实留了 5 个未覆盖项。本节把它们变成**可跑的判据**再动手——
> 「只写在文字里的判据视为不存在」（团队铁律 10）。每条都必须有一条能跑出 exit code 的检查。

### 11.1 中文 IME（前端）

用户用拼音输入法在聊天框打字时，**组合中（未确认）的内容不得被当成答案发出去**。

判据（CDP 驱动真实组合事件，`_verify/v22_ime.py`）：
1. `Input.imeSetComposition` 造组合中间态（如拼音 `shou`）→ **零** `/api/kickoff/chat` 请求发出；
2. 组合中按 Enter → **零** 请求发出（必须查 `KeyboardEvent.isComposing`，不能只看 keydown）；
3. `compositionend` 提交「收率」→ 按 Enter 后才发一次请求，且请求体里的 `content` 是**最终中文文本**（不是拼音、不是空串）；
4. 组合中若把输入框清空再提交空串 → 不发请求（沿用「空输入不发送」）。

### 11.2 `?demo=<key>` 与新聊天首屏共存（前端）

`?demo=` 会在约 520ms 后自动跑一遍 SSE（上游演示路径）。它**不得破坏**聊天状态。
`?demo=` 的实现是 `selectScenario(key, {quiet:true})` + `setTimeout(run, 520)`，所以**演示路径 = 场景选择 + 运行两步**——
判据必须覆盖**两步**，只测 `run()` 会漏掉「重选场景把面板重置掉」这一类（实测就漏了）。

判据（`_verify/v22_verify_demo.py`）：

1. **首屏加载**（对照，不是「不为 0 就算过」）：`/?demo=kickoff` 等 3s 后，
   `#condoptKickoff` 存在，且气泡数与「不带 `demo` 加载同一场景」**一致**、`step` 仍在第一步；
2. **已答轮次不丢**（2026-11-21 12:00 收紧，触发集合写全）：
   - 先点两条芯片（`step` 前进两轮、气泡 5 个）；
   - **触发 A：再次进入「已经选中的同一个场景」**（点侧栏同一张卡）→ 必须**保持**（气泡不减少、`step` 不回退）；
   - **触发 B：演示运行**（「开始处理」/`?demo=` 的 `run()`）→ 同上；
   - **跨场景切换（选另一个场景再回来）允许重置**——这是合理行为，不算违反本条；
3. 全过程 `Runtime.exceptionThrown` 捕获到 **0** 个未捕获异常；
4. 演示输出落在右栏、不覆盖左栏聊天（几何不重叠：复用 `geom_probe.py` 的三重判据）。

> 背景（为什么触发 A 是硬要求）：实测在冻结提交 `c0087b2` 上，聊过两轮后点**当前已选中的**侧栏卡片，
> 气泡 5 → 1、`step` 回到第一步，**用户答案被清掉**。这不是「切换场景本来就会重置」能解释的——
> 点的是同一个场景，用户没有表达任何「重来」意图。故定：**再次选中当前场景必须是 no-op**。

### 11.3 `--import-llm` 润色通道（后端）

§10.4 已定：可选用模型**只润色 `reply.text`**，不改 JSON 结构、不改路由结论，失败必须回落规则文案。

判据（**本机 stub 网关，不出网**，`tests/test_kickoff.py` 新增）：
1. 开 `--import-llm` + stub 返回润色句 → `reply.text` 变为润色句，且
   **响应其余字段（`state` / `ready` / `options` / `candidates` / `quick_replies`）与不开时逐字节相同**；
2. stub 返回**非 JSON** → 回落规则文案，整个响应与不开时**逐字节相同**；
3. stub **超时 / HTTP 500** → 同上回落，且**不把异常暴露给用户**；
4. 未开 `--import-llm` → 走 `socket` 一调即抛的 monkeypatch 仍跑通（**零外网调用**）；
5. 密钥不得出现在任何响应或日志里。

### 11.4 旧表单逐字段（前端）

「高级：直接改 answers JSON」抽屉里的 45 字段表单必须与 `prepare_template` **一一对应**，且改动能原样进 `answers`。

判据（`_verify/v22_oldform_fields.py`）：

**先定口径（2026-11-21 10:10 裁定，避免两边各算各的）**——「字段数」= **叶子控件数**，按同一套规则从 `prepare_template` 数出来：

| 模板里的形状 | 算几个控件 |
|---|---|
| 标量（`budget` / `noise` / `notes` …） | 1 |
| 字典（`objective: {name, direction}`） | 展开成叶子键 → 2 |
| 数组（`variables: [ {…}, … ]`） | **每个元素各自展开**，再把各行叶子键数相加 |
| **列表值本身**（如 `choices: ["MeCN","EtOH","water"]`） | **算 1 个控件**（一个逗号分隔输入框），**不按元素展开** |

实测当前模板 = `objective` 2 + `variables` 6 行（5+5+5+3+4+2=24）+ 其余 7 个标量 = **33 个控件**。
（若把 `choices` 的 3 个元素也拆开会得到 35 —— **那不是本判据的口径**。）

1. 抽屉里渲染出的字段控件数 == 按上述规则算出的 **33**；
2. 逐字段改值 → 表单产出的 `answers` 里该字段**逐字段等于所改值**，且**类型保持**（bool→bool、int→int、float→float、list→list）；
3. 未触碰的字段不影响：产出与「只在 JSON 里改同一字段」的结果**键集一致**；
4. 空值字段不得写成像 `"None"` 的字符串（清空 = 移除该键或回到模板默认，二者都可，但必须确定且一致）；
5. **每一行只渲染它自己的键**：变量行之间键集不同（categorical 行没有 `low/high/unit`），按「全表键并集」渲染会造出**幻影控件**（实测 45 ≠ 33，已修）。

### 11.5 验收脚本自身的临时资源卫生（`_verify/**`）

浏览器驱动每建一个一次性 profile，就必须保证**跑完不残留**。

判据：
1. profile 建在 `%TEMP%`（不是 `Path.home()`），名字含 pid + 时间戳；
2. 结束用 `taskkill /T /F` 杀**整棵进程树**，再删除；
3. 删除后**连续两次确认不存在**才算干净（子进程会回写——§21.15 的教训）；
4. 删不掉必须打到 stderr，**不许 `ignore_errors=True` 静默吞掉**；
5. 脚本自检：「跑完新增残留 == 0」（跨 settle 窗口多次计数，不是只数一次）。
6. **同一台机器上浏览器驱动必须串行跑**（2026-11-21 10:20 补，有实测证据）：多个驱动同时启动
   Edge 时会互抢资源，观测到 `ConnectionResetError: [WinError 10054]` 这类**看起来像产品失败**
   的环境错误（当时矩阵与 IME 驱动并行，前者正常、后者被 reset）。
   - 驱动遇到此类冲突必须报成**环境/并发类失败并给出证据**（例如「检测到同前缀 profile 数 > 1」），
     **不许**把它记成产品缺陷，也不许静默重试到「通过」；
   - 需要并行时，请在证据里写明「并发运行」以及如何区分（例如前后各跑一次单例对照）。
7. **证据必须可溯源到环境**（2026-11-21 10:25 补）：每份驱动结论都要注明
   **提交哈希 + 是否处于并发运行 + 是否在某次清扫之后**。理由有实证：一次「树杀 47 个脱管 Edge」
   的清扫恰好在别的驱动并发运行时发生，差一点把别人的证据变成无效产物（该次未污染，但只差一点）。
   - 卫生实现**只允许一份**：各驱动共享同一个 `_verify/browser_profile.py`，
     不许各自实现一套（两份必然漂移）；残留判据按 **pid 作用域**统计本进程建的目录，
     同前缀全局计数只作信息行——否则并发时会互相记错账。
8. **锁必须强制，且要有机器判据**（2026-11-21 10:55 补，实际发现）：
   串行锁最初是 **opt-in**——只有 `run_guarded(fn, tag)` 取锁，旧入口 `guard_residue(fn)`
   **既不取锁也不报警**，于是「按 §11.5-6 串行」对锁出现之前写的驱动是**假生效**
   （实测：同机同刻并发跑 ime + oldform 两个驱动，两个都 exit 0——没有任何机制阻止）。
   这与 F9/F11 同族：**门禁宣称的比执行的多**。
   要求：
   - 所有驱动入口必须取锁（`run_guarded`）；拿不到锁 → **exit 4（环境/并发类）**，
     不记成产品缺陷、不静默重试；
   - 必须有一条**机器检查**扫描 `_verify/**`：「存在导入卫生模块却不取锁的驱动入口」→ 报红；
     该检查本身要有**负对照**（故意造一个不取锁的入口 → 检查必须红）。
