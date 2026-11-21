# condopt 工作台 · 独立验收报告（task-3）

> 验收人：`verifier`（独立于实现者）
> 结论日期：2026-11-20 01:50（Asia/Shanghai）
> 契约唯一真源：`CONTRACT.md`（§1–§7 + §8 追认裁定）
> 原则：**实现者的自证不作为证据**。本文件每条 PASS 都附我自己跑出来的原文输出；
> 跑不了的写 BLOCKED 并说明原因；不伪造。
>
> **最终基线**：task-6（F4 读路径改造）+ task-7（错误 message 截断）后的版本，
> 指纹见 §1.2。三轮复验的结论见 §14。

---

## 0. 结论速览

| # | 验收项 | 结果 | 权威日志 |
|---|---|---|---|
| 1 | `pytest tests/test_workbench.py -q` 全绿 | **PASS**（31 passed） | `logs/pytest_workbench_console.txt` |
| 2 | `server.py --port 0 --selftest` exit 0 且不挂住 | **PASS**（7/7） | `logs/selftest_console.txt` |
| 3 | 真实 HTTP 端到端闭环 7 步 | **PASS**（23/23） | `logs/v03_console.txt` |
| 4 | `/api/run` 五场景 SSE 契约 + 逐帧到达 | **PASS**（23/23） | 同上 |
| 5 | `data.js` ↔ §3.1 ↔ `/api/bootstrap` 逐字节比对 | **PASS**（19/19） | `logs/v05_console.txt` |
| 6 | 安全：方法/穿越/只绑回环 | **PASS**（52/52） | `logs/v06_console.txt` |
| 7 | 离线：无外链 + 零外网请求 | **PASS**（15/15） | `logs/v07_console.txt` |
| 8 | Edge headless 双截图 + 控制台/网络 | **PASS**（17/17） | `logs/v08_console.txt` |
| 9 | 回归：4 个 doc 门禁 + 全量 pytest + README 计数 | **部分**：4 门禁全绿、README 一致；**全量 pytest 1 failed（`RELEASE_NOTES.md` 计数未同步，非工作台缺陷，见 §10）** | `logs/full_pytest_console.txt` |
| 10 | 对抗性必测 4 条 | **PASS** | `logs/v10_console.txt` |
| 11 | verifier 自选边界（并发/畸形 id/请求体/UTF-8/回显截断） | **PASS**（56/56） | 同上 |
| — | `CONTRACT §8` 六条追认裁定 | **PASS**（30/30 含副作用专项） | `logs/v12_console.txt` |
| — | task-4 修复复验（F1/F2/F3） | **PASS** | `logs/v12_console.txt`、`logs/repro_min_console.txt` |
| — | task-6 修复复验（F4） | **PASS**（2/2） | `logs/probe_longid_console.txt` |
| — | task-7 修复复验（错误 message 截断） | **PASS** | `logs/v10_console.txt` §11.4b |

**缺陷总数 4 个（F1–F4，均低危）→ 全部已修复并经我独立复验通过。
另记 1 条中危隐患 R1（`GET /api/study` 依赖 condopt 无副作用），建议加固、当前无故障。**

---

## 1. 验收基线与「实现物在变」的处置（重要）

本次验收期间实现物被修改了两次（task-1/2 完成 → task-4 修复），我没有复用旧证据，
而是**在冻结版本上全量重跑**。最终证据对应的版本指纹：

```
$env:PYTHONUTF8='1'
$files = @('server.py','bridge.py','tests\test_workbench.py',
           'web\index.html','web\assets\data.js',
           'web\assets\condopt.js','web\assets\app.js',
           'README.md','CONTRACT.md','web\assets\app.css')
foreach($f in $files){ $h=(Get-FileHash $f -Algorithm SHA256).Hash; ... }
```

```
45FCF8DB5BDBC51C     25647  server.py
2FC04FE09B117A86     58224  bridge.py
1AF01282BB6A1804     38372  tests\test_workbench.py
AFC047FF523692CE     15867  web\index.html
B373053C804E9604     20679  web\assets\data.js
E638366E96F22B4E     43170  web\assets\condopt.js
A3EC70ED43131D76     33605  web\assets\app.js
AFBB32753BDD8754     31503  README.md
381A7411B2416DE4     14955  CONTRACT.md
6743C4C5F051A847     30052  web\assets\app.css
```

最后写入时间 `2026-11-20 01:05:34`，报告定稿 `01:25`，**静默 18 分钟以上**才开始收尾，
因此下表结果对应一个稳定版本。完整哈希见 `logs/revision_hashes.txt`。

### 1.2 最终基线（task-6 + task-7 之后）

验收期间实现物被修改了**四轮**（task-1/2 首版 → task-4 修 F1–F3 → task-5 前端措辞 →
task-6 修 F4 → task-7 修回显截断）。我不复用旧证据，每轮都在新版本上**全套重跑**。
最终证据对应：

```
F4CD303D274DBD0D     26346  server.py
48AC2BBDEBA7E41F     60412  bridge.py
9C920FBC7C583562     45819  tests\test_workbench.py
AFC047FF523692CE     15867  web\index.html
B373053C804E9604     20679  web\assets\data.js
E638366E96F22B4E     43170  web\assets\condopt.js
A3EC70ED43131D76     33605  web\assets\app.js
AEC56FFAB9CFC56E     31503  README.md
381A7411B2416DE4     14955  CONTRACT.md
6743C4C5F051A847     30052  web\assets\app.css
```

最后写入 `2026-11-20 01:45:54`（README），我在 `01:47` 起跑全套、`01:49` 复核哈希未变。
完整哈希见 `logs/revision_hashes_final.txt`。

> ⚠ 一处需要 lead 自己复核的观察（我没有权限判定、也不算 FAIL）：
> `CONTRACT.md` 自称「冻结」，但验收期间它从 230 行 / 13,514 字节变成了
> 248 行 / 14,955 字节（新增 §8「实现裁定」六条，2026-11-20 00:21 由 lead 写入）。
> 新增 §8 的做法（「契约真源以本节为补充，不再追溯修改上文」）本身是合理的，
> 但它确实晚于「冻结」声明。**我按含 §8 的最新版本验收**，并单独核了 §8 的六条（见 §12）。

---

## 2. 项1 · 后端契约测试

**命令原文**
```powershell
$env:PYTHONUTF8='1'; py -3 -m pytest tests/test_workbench.py -q
```

**实际输出（关键行）**
```
...............................                                          [100%]
31 passed in 6.30s
EXIT=0
```

> 注：`pyproject.toml` 设了 `addopts = "-q"`，所以命令行再加 `-q` 会变成双重 quiet，
> 摘要行被吞掉、只剩点号。为拿到 `N passed` 原文，我改用 `py -3 -m pytest tests/test_workbench.py`
> （仍带配置里的 `-q`）。两者都是 exit 0。

**PASS** — 证据：`logs/pytest_workbench_console.txt`

用例数演进（每一版我自己重跑）：首版 **20** → task-4 加 5 条回归 **25** → task-6/task-7 再增 **31**。

---

## 3. 项2 · 服务自检（必须不挂住）

**命令原文**
```powershell
$sw=[Diagnostics.Stopwatch]::StartNew()
$out = & py -3 server.py --port 0 --selftest 2>&1
$code = $LASTEXITCODE; $sw.Stop()
```

**实际输出（完整）**
```
[selftest] 服务已启动：http://127.0.0.1:53574（web_root=<repo>\web）
[PASS] GET /api/bootstrap  status=200 algorithms=18 scenarios=5
[PASS] GET /api/runs  status=200
[PASS] GET /  status=200
[PASS] PUT /api/runs → 405  status=405
[PASS] GET /../etc/passwd → 400  status=400
[PASS] GET /api/nope → 404  status=404
[PASS] POST /api/run kickoff（SSE 帧）  status=200 events=['meta','stage'×8,'delta'×8,'notice','done'] indices=[0, 1, 2, 3]
[selftest] 完成：7/7 项通过，用时 0.1s
EXITCODE=0 ELAPSED_MS=2689
```

进程 2.7 秒内自行退出（`$LASTEXITCODE` 已取到值即证明没有挂住；挂住时 PowerShell 会一直等）。

**PASS** — 证据：`logs/selftest_console.txt`

---

## 4. 项3 · 真实 HTTP 端到端闭环（临时 runs-dir）

真实子进程 + 真实 socket，不是内部函数调用：

```powershell
py -3 server.py --host 127.0.0.1 --port 55449 --runs-dir _verify\_runs\e2e
py -3 _verify\v03_e2e.py      # 客户端全程用 urllib 打真实端口
```

**实际输出（每步状态码 + 关键字段）**
```
[PASS] 3.1 GET /api/bootstrap → 200 ok:true  status=200 algorithms=18 scenarios=5 cache-control=no-store content-type=application/json; charset=utf-8
[PASS] 3.2 POST /api/action prepare → 200 ok:true  status=200 sections=['stages','stopping','feasibility','deliverable','prior','noise'] route=bayes 落盘=否(正确)
[PASS] 3.3 POST /api/action init → 200 ok:true  run={'id':'demo','n_obs':0,'n_rounds':0,'best':None,'direction':'max','algorithm':'sobol','remaining':8,'budget':8,'has_pending':False}
[PASS] 3.4 POST /api/action ask → 200 ok:true  pending_ids=['r1-1','r1-2','r1-3','r1-4'] plates=['P1'] has_pending=True
[PASS] 3.5 POST /api/action tell → 200 ok:true  accepted=4 n_obs=4 best=13.0 pending_now=[]
[PASS] 3.6 POST /api/action delivery → 200 ok:true  items=1 integrity_ok=True
[PASS] 3.7 POST /api/action render → 200 ok:true  dashboard=...\e2e\demo\dashboard.html png=...\convergence.png exists=True
```

**PASS** — 证据：`logs/v03_console.txt`、`logs/v03_e2e.log`

附加观察（不是缺陷，是 §8#2 追认的行为）：`init` 后 run 目录有 9 个文件
（6 个数据契约文件 + `space.json`/`context.json`/`objective.json`），已由 §8#2 追认。

---

## 5. 项4 · `/api/run` 五场景 SSE 契约一致性

用 `http.client` 逐行读取真实 SSE 流，**不打 urllib**（urllib 会缓冲，测不出逐帧）。

**stage.index 与 §3.1 权威表的对照方式**：权威表由脚本从 `CONTRACT.md` 表格解析
（`common.contract_stages()`，不手抄），再逐场景逐帧比对 `label` 与 `index`。

```
[PASS] 4.kickoff  事件名合法  names=['delta','done','meta','notice','stage'] 首帧=meta 末帧=done
[PASS] 4.kickoff  stage.index 落在 §3.1 下标内  running=['校验问卷答案','解析条件空间','路由算法','生成六段建议'] indices=[0,1,2,3]
[PASS] 4.sample   stage.index 落在 §3.1 下标内  running=['校验 study 与待回填状态','读取已有观测','调用 ask 生成下一批','写入 pending 与孔板'] indices=[0,1,2,3]
[PASS] 4.feedback stage.index 落在 §3.1 下标内  running=['解析回填结果','校验 id 与约束','写入 dataset 与 rounds','确认 pending 清空'] indices=[0,1,2,3]
[PASS] 4.delivery stage.index 落在 §3.1 下标内  running=['汇总交付清单','跑完整性自检','给出收尾建议','生成可交付摘要'] indices=[0,1,2,3]
[PASS] 4.board    stage.index 落在 §3.1 下标内  running=['读取 run 目录','渲染收敛与前沿','写入 dashboard.html','检查离线资源'] indices=[0,1,2,3]
```

**逐帧到达证明**（board 场景，最慢的一个）：
```
首字节 13ms / 总 184ms（7.1%），总耗时 50% 前已到达 6/12 帧
逐帧时间线: meta@13ms stage@13ms stage@13ms stage@13ms stage@36ms stage@36ms
            stage@184ms stage@184ms stage@184ms delta@184ms delta@184ms done@184ms
```
若服务端把整段缓冲到执行完才吐，首字节会 ≈ 总耗时（≈184ms）；实测 13ms，
且 `stage@36ms` 落在两次耗时之间 —— **确实逐帧 flush**。

> 说明：我第一版判据写错了（要求「末帧到达时刻 < done.elapsed_ms」，但 `done` 本身就是末帧，
> 逻辑上不可能成立）。已改成「首帧到达远早于流结束」，实测后才是上面这组数。
> 这条留在这里是因为：**判据写错会误报 FAIL，我把它修好了而不是改数字。**

**PASS** — 证据：`logs/v03_console.txt`

---

## 6. 项5 · `data.js` ↔ §3.1 ↔ 后端三方比对（用脚本，不眼看）

`data.js` 是浏览器脚本，无法直接 `json.loads`。我用 **node + vm 真实执行**它取出
`window.EDU_DATA`（`extract_data.js`），再与两方比对：

```powershell
node _verify\extract_data.js web\assets\data.js _verify\logs\data_scenarios.json
py -3 _verify\v05_contract.py
```

**实际输出（关键行）**
```
CONTRACT §3.1 解析结果（真源）: {"kickoff": {...}, "sample": {...}, "feedback": {...}, "delivery": {...}, "board": {...}}
extracted 5 scenarios -> logs\data_scenarios.json
[PASS] data.js 场景 key 顺序 = kickoff/sample/feedback/delivery/board  实际=[...] 契约=[...]
[PASS] 5.kickoff  与 §3.1 逐字段一致（no/title/action/needs_run/stages）  no=01 action=prepare needs_run=False
[PASS] 5.sample   与 §3.1 逐字段一致  no=02 action=ask needs_run=True
[PASS] 5.feedback 与 §3.1 逐字段一致  no=03 action=tell needs_run=True
[PASS] 5.delivery 与 §3.1 逐字段一致  no=04 action=delivery needs_run=True
[PASS] 5.board    与 §3.1 逐字段一致  no=05 action=render needs_run=True
[PASS] 5.* mock/review/example/outputs 齐全  全部 True
[PASS] index.html 引用的本地资源全部存在  引用 16 个本地资源，缺失=无
[PASS] index.html 无 http(s)/协议相对外链  外链=无
[PASS] katex.min.css 的 60 个 url(fonts/…) 目标全部存在（历史缺陷：fonts/fonts/ 多一层）  缺失=无
[PASS] vendor/katex/fonts/ 下不存在残留的 fonts/fonts/ 嵌套目录  残留=无
[PASS] 后端 scenarios key 顺序与 data.js 一致
[PASS] data.js 与后端 Scenario 逐字段差异 0 处  差异=无（key/no/title/tagline/short/action/needs_run/stages）
```

**PASS** — 证据：`logs/v05_console.txt`、`logs/data_scenarios.json`

> 历史缺陷回归确认：`katex.min.css` 有 **60** 个 `url(fonts/…)`（20 组 × ttf/woff/woff2），
> 我逐个验证目标文件存在 —— 即当初「20 个 `@font-face` 全 404」的缺陷已彻底修复。

---

## 7. 项6 · 安全

**实际输出（关键行）**
```
[PASS] 6.PUT /api/bootstrap → 405      allow=GET, POST
[PASS] 6.PUT / → 405
[PASS] 6.DELETE /api/bootstrap → 405   （×4 路径）
[PASS] 6.HEAD /api/bootstrap → 405     （×4 路径，body 为空是 HEAD 语义）
[PASS] 6.PATCH /api/bootstrap → 405    （×4 路径）
[PASS] 6.OPTIONS /api/bootstrap → 405  （×4 路径）
[PASS] 6.TRACE /api/bootstrap → 405    （×4 路径）
[PASS] GET /api/action（POST-only）→ 405  allow=POST
[PASS] POST /api/bootstrap（GET-only）→ 405
[PASS] 6.GET /../../etc/passwd → 400            status=400 body='400 bad path'
[PASS] 6.GET /../etc/passwd → 400
[PASS] 6.GET /assets/../server.py → 400
[PASS] 6.GET /assets/../../CONTRACT.md → 400
[PASS] 6.GET /%2e%2e/%2e%2e/README.md → 400
[PASS] 6.GET /assets/..%5cserver.py → 400
[PASS] 6.GET /..%00/index.html → 400
[PASS] 6.GET /assets\server.py → 400/404（不是 200）  status=400
[PASS] 6.GET /api/study?id=.. → 400 E_BAD_REQUEST      （另有 ../demo、..%2fdemo、demo/..、C%3A%5Cdemo、a%2Fb、%2e%2e 共 7 例）
[PASS] 6.外网卡 10.7.221.91:58975 不可达（只绑 127.0.0.1）  reachable=False
       对外网卡连接失败（预期）：ConnectionRefusedError [WinError 10061] 由于目标计算机积极拒绝
[PASS] 6.监听地址只有 127.0.0.1  Get-NetTCPConnection LocalAddress=['127.0.0.1']
[PASS] 6.--host 0.0.0.0 → 退出码 2  stderr=错误：--host 只接受 127.0.0.1 / localhost…收到 '0.0.0.0'
[PASS] 6.--host 10.7.221.91 → 退出码 2
[PASS] 6.--host localhost 允许启动（契约 §1）  status=200
```

**52/52 PASS** — 证据：`logs/v06_console.txt`

---

## 8. 项7 · 离线（静态扫描 + 运行时实测）

**命令原文**
```powershell
py -3 _verify\v07_offline.py
```

**实际输出（关键行）**
```
扫描范围（12 个 html/js/css，已排除 vendor/ 与 licenses/）：
  web\assets\_upstream_app.css 23426 / adapter.example.js 2163 / app.css 29307 / app.js 32669
  web\assets\base.css 9411 / components.css 12422 / condopt.js 40559 / data.js 20679
  web\assets\gallery.css 6926 / gallery.js 3900 / components\index.html 25517 / index.html 15808

命中的 http(s) 出现点：1 个（可执行 0 / 注释 0 / data-URI或xmlns 1）
  non-net  web\components\index.html:8  [data:/xmlns] http://www.w3.org/2000/svg
[PASS] 非 vendor/licenses 的 html/js/css 中无「真正可发请求」的 http(s) 外链  可执行外链=无
[PASS] 7.* 无远程 <script>/<link>          （12 个文件逐个检查）
[PASS] 7.无 new WebSocket / sendBeacon 远程地址
[PASS] 7.所有 fetch 只打同源相对路径
```

唯一命中项 `http://www.w3.org/2000/svg` 位于 `components/index.html:8` 的 **内联 favicon
`data:image/svg+xml,…` 内部**，是 percent-encoded 的 XML 命名空间标识符，浏览器不会发请求。
我第一版脚本把它算成「可执行外链」误报 FAIL，随后**改进分类器**（识别 data: URI 内部与
`xmlns=` 命名空间）而不是简单加白名单 —— 详见脚本 `classify()`。

运行时零外网请求另有实测（见 §9 的 `外网请求: 无`）。

**PASS** — 证据：`logs/v07_console.txt`

---

## 9. 项8 · Edge headless 视觉实测

用 **Edge 157.0.4341.60** + CDP（`websocket-client` 直连 DevTools，非 `--dump-dom` 静态快照），
真实点击「开始处理」，采 `Runtime.exceptionThrown` / `Runtime.consoleAPICalled` / `Log.entryAdded`
/ `Network.*`，再 `Page.captureScreenshot` 存 PNG。

```powershell
py -3 _verify\v08_browser.py
```

### 9.1 「前端静态页」（`python -m http.server` 托管 `web`，无 `/api/*`）

```
DOM      : {"title":"condopt 工作台","textLen":2556,"nodes":565,"mode":"离线演示",
            "runNote":"离线 mock · 可直接演示","mdLen":1706,"mdVisible":true,"stages":4,
            "katexLoaded":true,"scripts":9}
截图     : shots/frontend_static.png {'size':(1478,903),'mean_rgb':(240.2,238.5,232.6),
            'white_fraction':0.4671,'non_white_fraction':0.5329,'distinct_colors':5638}
外网请求 : 无
字体响应 : [('KaTeX_Math-Italic.woff2',200),('KaTeX_Main-Regular.woff2',200),('KaTeX_Size1-Regular.woff2',200)]
JS 错误  : 无
浏览器日志: ['Log[error] Failed to load resource: 404 …/api/bootstrap',
             "Log[error] Failed to load resource: 501 (Unsupported method ('POST')) …/api/run"]
```

判定：`/api/*` 的 404/501 是「没有后端」的**预期**响应（`http.server` 只会 501 掉 POST），
属 `ADAPTATION.md §5` 文档化的降级路径；关键是有 **无 JS 报错**、`mdLen=1706` 说明
mock 真的跑出来了、`mode=离线演示` 说明模式徽标正确。

### 9.2 「后端联调页」（真实 `server.py` + 一个 demo study）

```
DOM      : {"title":"condopt 工作台","textLen":3671,"nodes":805,
            "mode":"真实模型 · condopt-0.1.0","runNote":"真实模型 · 失败自动回退",
            "mdLen":1194,"mdVisible":true,"stages":4,"katexLoaded":true,"scripts":9}
截图     : shots/backend_live.png {'size':(1478,903),'mean_rgb':(244.1,242.3,236.1),
            'white_fraction':0.4284,'non_white_fraction':0.5716,'distinct_colors':4887}
外网请求 : 无
字体响应 : [('KaTeX_Math-Italic.woff2',200),('KaTeX_Main-Regular.woff2',200),('KaTeX_Size1-Regular.woff2',200)]
JS 错误  : 无
浏览器日志: 无
>=400 资源: 无
```

**两张 PNG 我不只看像素统计，还自己打开看了**（人工目视确认）：
- `_verify/shots/frontend_static.png`：产品壳完整、五场景侧栏、离线 mock 六段
  建议、KaTeX 表格正常、无白屏、无布局错乱。
- `_verify/shots/backend_live.png`：顶栏「真实模型 · condopt-0.1.0」、study 下拉
  `demo · n•2 · best=`、四段阶段条全绿勾、六段建议带 router 理由、人工拍板区与覆写选择器正常。

**17/17 PASS** — 证据：`logs/v08_console.txt`、`shots/frontend_static.png`、`shots/backend_live.png`

---

## 10. 项9 · 回归

```powershell
py -3 scripts/check_docs_links.py
py -3 scripts/check_doc_duplication.py --max-duplicates 2
py -3 scripts/check_commands_documented.py
py -3 scripts/check_api_docs.py
```

```
=== py -3 scripts/check_docs_links.py ===
docs_links: 127 files, 0 broken
EXIT=0
=== py -3 scripts/check_doc_duplication.py ===
doc_duplication: 0 near-duplicate rule sentences (threshold 0.6)
EXIT=0
=== py -3 scripts/check_commands_documented.py ===
commands_documented: 31 commands, 0 files with gaps
EXIT=0
=== py -3 scripts/check_api_docs.py ===
undocumented (non-core): feasibility_report, progress_trajectory
api_docs: 38/40 public methods mentioned, core gaps=0
EXIT=0
```

全量测试（最终基线，原文输出）：
```powershell
py -3 -m pytest
→ =========================== short test summary info ===========================
→ FAILED tests/test_release.py::test_release_notes_match_the_generator - Assert...
→ 1 failed, 615 passed in 438.73s (0:07:18)
→ FULL_PYTEST_EXIT=1
```

**这一条我必须如实标红：全量 pytest 当前不是全绿，但红的原因与工作台无关，是生成式文档 `RELEASE_NOTES.md` 未同步。**

```
$ py -3 -m pytest tests/test_release.py::test_release_notes_match_the_generator -q
E       AssertionError: assert '# 条件优化 route...计数直接取自代码与证据库。' == '...'
E         -  单测 | **616** |          ← 生成器 build() 实测值
E         +  单测 | **614** |          ← RELEASE_NOTES.md 里写死的旧值
```
```
$ py -3 -c "... m.counts()['tests'] ..."
generator tests count = 616
$ Select-String -Path RELEASE_NOTES.md -Pattern '单测'
| 单测 | **614** |
```

归因：`RELEASE_NOTES.md` 由 `scripts/build_release_notes.py` **生成**（其 `counts()` 真的跑
`pytest --collect-only -q` 再求和，不是手写常量）。task-7 在 `01:40:03` 又加了 2 条测试
（收集数 614 → 616），而 lead 上一次同步 `RELEASE_NOTES.md` 是 `01:34:09`，**早于该改动 6 分钟**，
于是「文档 614 / 代码 616」不一致，被 `test_release_notes_match_the_generator` 抓到。
这与工作台代码、契约、SSE、安全均无关；修复动作是**重跑生成器**（`py -3 scripts/build_release_notes.py`），
属 lead 的文档同步职责，我不越界改（也已按要求不动 README / RELEASE_NOTES）。

README 计数一致性（与上面这条无关，单独核对）：
```
$out = py -3 -m pytest --collect-only -q   # 逐文件求行数并求和
files=56 sum=616
README L100: - **测试**：616 个单测…
README L285: tests/                单测（616）
```
**README 616 == collect 616** ✅（README 已由 lead 于 `01:45:54` 同步）；仅 `RELEASE_NOTES.md` 落后。

> 过程中的一条真实波折（如实记录）：我第一次跑全量 pytest 时拿到
> `FAILED tests/test_release_gate_honesty.py::test_allow_partial_makes_the_opt_out_explicit`
> （断言 `returncode == 0`，实得 1）。当时 `release_check` 报的是
> `release_check: PARTIAL RUN (--skip-slow): 2/3 executed and green`，其中
> `readme_test_count` 红（README 仍写 605，而 task-4 新增测试后收集到 610）。
> **这不是工作台代码的缺陷，是 task-4 加了测试后 README 计数未同步**。
> lead 于 `01:05:34` 同步 README 后，同一条测试与全量 610 均转为 PASS。
> 我未修改 README（按要求交 lead 处理），此处仅留痕。

**PASS** — 证据：`logs/full_pytest_console.txt`

---

## 11. 项10 · 对抗性必测

```powershell
py -3 _verify\v10_adversarial.py
```

```
### 项10.1 已有 pending 时再 ask → E_CONFLICT
[PASS] 10.1 重复 ask → 409 E_CONFLICT
       message='2 suggestions are still pending; call `tell` with their measured results…'
       hint='先 POST /api/action {tell} 回填当前批次，或看 study.pending'
[PASS] 10.1 冲突后 pending 未变多、剩余预算未多扣、run 目录哈希不变
       pending=2 remaining=6（冲突前 6） 磁盘变化=False

### 项10.2 /api/action 未知字段 → E_VALIDATION
[PASS] 顶层未知字段 → 422  message='请求体未知字段: oops'
[PASS] payload 未知字段（ask.plat 拼错）→ 422  message='ask 的 payload 未知字段: plat'
[PASS] payload 未知字段（delivery 带 n）→ 422  message='delivery 的 payload 未知字段: n'

### 项10.3 同一 id 重复 tell → 幂等
[PASS] 首次计入、第二次同 id tell accepted=0 且磁盘逐字节不变
       accepted=2→0 n_obs=2→2 dataset 行数=2 rounds=1 二次磁盘变化=False

### 项10.4 任何 GET /api/* 零副作用（§4.2-2）
[PASS] GET /api/*（含 /api/study 反复 5 轮）不改动 run 目录  文件数=11 哈希全等=True
```

**PASS** — 证据：`logs/v10_console.txt`

---

## 12. 项11 · verifier 自选边界（实现者未覆盖）

| # | 边界 | 结果 | 关键输出 |
|---|---|---|---|
| 11.1 | **并发 2 个 ask** 是否被串行锁挡住 | PASS | 状态码 `[200, 409]`，ok=1 conflict=1，pending=2（没重复生成） |
| 11.2 | **并发 2 个 tell**（同 id） | PASS | accepted=`[0, 2]`，n_obs=2（没有重复计入） |
| 11.3 | **并发 2 个 init**（同 id） | PASS | 状态码 `[200, 409]`（不静默覆盖） |
| 11.4 | `/api/study?id=` 空/空格/超长/中文/不存在/`%00`/`..` | PASS | 空→400；`"   "`→400；5000 字符→400；中文→404；`a%00b`→400；`..`→400 |
| 11.5 | **SSE 在 action 抛错时是否仍发 done** | PASS | `meta→stage×5→error→done`，末帧 `status="error"`（修复后） |
| 11.6 | `/api/run` 信封错误在开流前返回 JSON 4xx | PASS | 未知 scenario→400；未知顶层字段→422；缺 run→400；text 非串→422；files 非数组→422；payload 非对象→422 |
| 11.7 | **请求体边界** | PASS | CL=9MB→400「请求体过大或非法: 9000000 bytes」；缺 CL→400；空体→400；坏 JSON→400；JSON 数组→400；非 UTF-8→400；CL 非数字→400 |
| 11.8 | run id 奇怪形态（空格 / Windows 设备名 / 超长） | PASS（修复后） | 见 §13 F1/F2 |
| 11.9 | **UTF-8 中文变量名** | PASS | `init` 200，快照里 `variables=['温度','溶剂']` 原样保留 |
| 11.10 | 响应必须是严格 JSON（无 NaN/Infinity） | PASS | 用 `parse_constant` 抛异常的严格解码器全通过 |
| 11.11 | `--runs-dir` 指向不存在的深层目录 | PASS | 服务正常起、`/api/runs`→`[]`、`init` 自动建目录成功 |

补充命令 `py -3 _verify\probe_devnames.py`（设备名完整矩阵）：
```
id='CON'    init=200 磁盘目录=True 在 /api/runs 里=是 /api/study=200 | 二次 init=409 E_CONFLICT
id='AUX'    init=200 … 二次 init=409 E_CONFLICT
id='COM1'   init=200 … 二次 init=409 E_CONFLICT
id='PRN'    init=200 … 二次 init=409 E_CONFLICT
id='LPT1'   init=200 … 二次 init=409 E_CONFLICT
id='clock$' init=200 … 二次 init=409 E_CONFLICT
== 复现 · Windows 设备名 / 超长 run id: 15/15 PASS ==
```

**PASS** — 证据：`logs/v10_console.txt`、`logs/probe_devnames_console.txt`

---

## 13. 发现的缺陷与处置（修复前 → 修复后对照）

### F1 · `init` 用 `NUL` 作 id → HTTP 500 且回显 Python 异常（低危，已修）

**修复前**
```
POST /api/action {"action":"init","payload":{"id":"NUL",...}}
→ HTTP 500 {"ok":false,"error":"E_INTERNAL",
   "message":"FileNotFoundError: [Errno 2] No such file or directory: '…\\NUL\\space.json'"}
期望：400/422（客户端输入非法）；实测：500
```

**根因**：`Bridge._run_dir` 只拦 `..`/分隔符/`:`，没有长度与 NTFS 设备别名校验；
`NUL` 在写 `space.json` 时才炸，异常冒泡成 500，且 `message` 原样回传异常文本与绝对路径。

**修复后（我复验）**
```
[PASS] F1 init id='NUL'     → 400 message="非法 run id: 'NUL'（Windows 保留设备名，无法作为目录）"
[PASS] F1 init id='nul'     → 400   （大小写不敏感）
[PASS] F1 init id='Nul'     → 400
[PASS] F1 init id='NUL.txt' → 400   （NTFS 设备别名形式）
[PASS] F1 init id='nul.tar.gz' → 400
[PASS] F1 init id='NUL.'    → 400
[PASS] F1 init id='NUL '    → 400   （strip 后仍判设备名）
[PASS] F1 反向：CON/AUX/COM1/PRN/LPT1/clock$/aNULb/NULL 全部 200（未被误伤）
```

> 这里我要特别记一笔：**我在报告 F1 时明确提醒 api-backend「不要封整表设备名」**，
> 因为我实测 `CON/AUX/COM1/PRN/LPT1/clock$` 在本机**真能建目录、能出现在 `/api/runs`、
> 能 `GET /api/study`、二次 init 正确 409**。修复只拦了实测会炸的 `^nul(\..*)?$`，
> 我逐个复验确认没有误伤。

### F2 · `init` 用 300 字符 id → HTTP 500（低危，已修）

**修复前**
```
→ HTTP 500 {"ok":false,"error":"E_INTERNAL","message":"OSError: [WinError 123] 文件名、目录名或卷标语法不正确。"}
```
**修复后（我复验）**
```
[PASS] F2 恰好 64 字符 id → 200（上限是闭区间）
[PASS] F2 65 字符 id     → 400 message='run id 过长（65 字符 > 上限 64）'
```

### F3 · SSE error 路径的 `done.status` 是 `"running"`（低危，已修）

**修复前**
```
event: error
event: done
data: {"run_id":"demo","elapsed_ms":24,"mode":"live","status":"running"}
```
**修复后（我复验）**
```
[PASS] F3 error 路径：events=['meta','stage'×5,'error','done'] done={"status":"error"}
[PASS] F3 反向：成功路径 done={"status":"ok"} 且无 error 帧
[PASS] F3 服务端不再回显 Python 异常文本（error 帧 message 已 sanitize）
```

> 定级依据（我自己的判断，不照抄实现者）：契约 §4 的硬要求是「发 error 帧，最后仍发 done」，
> 这条**修复前就已经通过**；`status` 是契约未规定的附加字段，且我读了
> `app.js:527-534` 确认前端 `done` 分支只取 `run_id`/`elapsed_ms`/`mode`、
> **不读 `status`**，故无用户可见影响 → 低危整洁性问题。web-shell 也独立核过同一处，结论一致。

### F4 · `>64` 字符的既有 study「列表可见但打不开」（低危，**未修**，本轮新发现）

这是 F2 的**修复引入的边界副作用**，由 lead 在派发复验时点名要求我实测「64 上限会不会与
condopt 自身对 study id 的约束冲突」。答案是：**会**。

我用 **condopt 本体**（绕开工作台的校验）建一个 70 字符的 study，再把工作台指向同一 runs-dir：

```powershell
py -3 _verify\probe_longid.py
```
```
condopt 本体已建立 70 字符 study：…\_runs\longid2\campaign-xxxx…（problem.json 存在=True）
GET /api/runs            → 列表=['campaign-xxxxxxxxxxx…']        ← 出现了
GET /api/study?id=<70字符> → HTTP 400 E_BAD_REQUEST 'run id 过长（70 字符 > 上限 64）'  ← 点不开
[FAIL] F4：列表可见=True /api/study=400 → 不一致
```

**影响**：`GET /api/runs` 用 `runs_dir.iterdir()` 枚举目录、**不套 64 字符校验**；
而 `/api/study`、`/api/action`、`/api/run` 都经 `_run_dir` 套了校验。
于是这类 study 会出现在前端 study 下拉框里，用户选中后所有动作都 400。
**不会丢数据、不会 500**，但 UI 上是一个「点得开却打不开」的死条目。

**最小复现**
```powershell
$env:PYTHONUTF8='1'; py -3 _verify\probe_longid.py
```
或手工：任何 `runs/` 下目录名长度 >64 且含 `problem.json` 的 run，都会在 `/api/runs` 出现、
`/api/study` 却 400。

**建议修法（供裁定，我不动手）**：二选一 ——
(a) `list_runs()` 也跳过（或在条目上标注）`len(name) > MAX_RUN_ID_LENGTH` 的目录，
    使「列表可见」与「可打开」两个集合一致；
(b) 把 64 上限**只用于 `init` 的新建校验**，读取既有 study 时放宽到文件系统实际限制。
我倾向 (a)：一条 run 若连读都读不了，出现在列表里只会误导用户。

**严重度**：低。需要「目录名 >64 字符」这种非常规场景，正常 id（`demo`、`run-20261120-0031`）不会触发。

**处置（task-6，lead 裁定 (b)，已修复并经我复验）**：lead 选择 (b) 而非我倾向的 (a)，理由是
「64 上限的目的是防止**新建目录**时文件系统报错，属创建期约束；读取既有 study 不该被拒，
且方案 (a) 会让该 study 完全不出现，比『可见但打不开』更糟」。
我接受这个理由——(b) 语义上更正确，我当初倾向 (a) 是出于「集合一致」的直觉，忽略了
「工作台的核心用途就是操作既有 study」这一更强约束。

复验输出（`py -3 _verify\probe_longid.py`，**2/2 PASS**）：
```
condopt 本体已建立 70 字符 study：…\_runs\longid2\campaign-xxxx…
  problem.json 存在=True
GET /api/runs            → 列表=['campaign-xxxxxxxxxxx…']
GET /api/study?id=<70字符> → HTTP 200 None 'None'
[PASS] F4（task-6 裁定 (b)）：condopt 本体建的长名 study「/api/runs 可见 且 /api/study 可打开」
       70 字符 study：/api/runs 可见=True（期望 True）；/api/study=200（期望 200）→ 符合裁定
[PASS] F4 反向：创建期 64 上限未被放松（init 65 字符仍 400）  init 65 字符 → 400 E_BAD_REQUEST
== F4 · >64 字符既有 study: 2/2 PASS ==
```

> 该探针我改过两次：① 断言从「两个集合必须一致」改成 task-6 裁定后的「可见且可打开」，
> 并把 PASS 文案从描述修复前现象的「不一致：列表里点得开却 400」改成现在的判据陈述
> （lead 点名要求，避免「断言通过但文案说失败」的误读）；② 修掉我自己引入的一个 bug：
> 第 45 行已把 `listed` 变成 `list[str]`，日志仍按 dict 取 `item['id']`，会抛
> `TypeError: string indices must be integers`。改为 `item[:20]` 后恢复正常。
> 这个 bug 是 api-backend 复跑时发现的，我确认属实并已修复。

---

## 13.5 F5 · 错误 message 原样回显超长 id（低危，task-7 已修）

这条不是我在首轮报出的，而是 lead 复跑 `v10_adversarial.py` 时从一条**过期断言**里发现的真实问题：
请求 5003 字符的 id 时，`{"message":"run 不存在: xxxx…(5003 字符全文)"}` 会把客户端原串整个回显，
造成响应与日志膨胀。

我在自己作用域内做了两件事（`_verify/**`）：

**① 修正过期断言**（读路径语义变了，不是缺陷）：
```python
# task-6 裁定 (b)：读路径不再套 MAX_RUN_ID_LENGTH，「超长且不存在」= 目录不存在 → 404
(qs("x" * 5000), 404, "E_NOT_FOUND"),
```
保留了全部非法字符类断言（`..`、`\x00`、`../demo`、分隔符等仍必须 400）。

**② 新增 §11.4b 回显有界断言**（4 组用例，每组 2 条断言）：

```
### 项11.4b 错误 message 的 id 回显必须有界（task-7）
    [超长且不存在] id 长 5003 → status=404 message 长度=60 hint 长度=57
        message='run 不存在: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx…xxxxxxxxxx'
[PASS] 11.4b 超长且不存在：状态码/错误码符合设计（404 E_NOT_FOUND）
[PASS] 11.4b 超长且不存在：message 长度有界（<200）且不含完整原串  message=60 字符 含完整原串=False
    [超长且不存在（重复字符）] id 长 5003 → status=404 message 长度=60
[PASS] 11.4b 超长且不存在（重复字符）：状态码/错误码符合设计（404 E_NOT_FOUND）
[PASS] 11.4b 超长且不存在（重复字符）：message 长度有界（<200）且不含完整原串
    [NUL + 超长] id 长 5002 → status=400 message 长度=67 hint 长度=36
        message="非法 run id: 'a\x00zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz…zzzzzzzzzz'"
[PASS] 11.4b NUL + 超长：状态码/错误码符合设计（400 E_BAD_REQUEST）
[PASS] 11.4b NUL + 超长：message 长度有界（<200）且不含完整原串
    [路径穿越 + 超长] id 长 5003 → status=400 message 长度=64 hint 长度=36
        message="非法 run id: '../wwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww…wwwwwwwwww'"
[PASS] 11.4b 路径穿越 + 超长：状态码/错误码符合设计（400 E_BAD_REQUEST）
[PASS] 11.4b 路径穿越 + 超长：message 长度有界（<200）且不含完整原串
```

断言口径：`len(message) < 200` **且** message/hint 里既不含完整原串、也不含原串的前 50 或后 50 字符
（后者用来确认头尾确实被截断，而不只是长度恰好变短）。
截断形态是「前 40 字符…后 10 字符」= 51 字符，与 `bridge._display` 的实现一致；
**判定不变**：非法字符 → 400，不存在 → 404；读路径**没有**重新加长度上限（否则会退回 F4）。

---

## 14. `CONTRACT §8` 六条追认裁定核查

§8 是契约冻结后新增的追认表，我按「新增字段允许、改名/改语义不允许」的口径单独核了一遍。

```
[PASS] §8#2 init 除 6 个数据文件外还落 space.json/context.json/objective.json（追认行为）
       run 目录文件=['config.json','context.json','dataset.jsonl','decisions.jsonl',
                     'objective.json','pending.json','problem.json','rounds.json','space.json']
[PASS] §8#1 sections[key=stages] = 算法与开工步骤，且路由结论与 result.route 一致  route=bayes 含算法=True
[PASS] §8#4 render 支持可选 make_png=false → png=null 且不产 PNG  status=200 png=None 产PNG=False
[PASS] §8#4 render make_png=true → 产出 PNG  png=…\s8run\convergence.png
[PASS] §8#4 render 其它字段仍严格白名单 → 422  error=E_VALIDATION
[PASS] §8#6 StudySnapshot.best 用 Observation.to_dict（含 x/y/t/source）
       best keys=['actor','cost','fidelity','meta','schema_version','source','t','x','y']
       best={'y':10.0,'t':1795108549.3250322,'source':'manual'}
[PASS] §8#5 坏 run 不静默丢弃、不伪造数字：仍在列表里、可算字段 null、附 error 说明
       entry={'id':'broken','n_obs':None,'n_rounds':None,'best':None,…,
              'error':'JSONDecodeError: Expecting property name enclosed in double quotes: line 1 column 3 (char 2)'}
```

§8#3（错误码补全）已由 §7 项10 与 §12 的 11.6 覆盖：未知 action→400、缺 run→400、
未知 payload 字段→422、`/api/run` 信封错误开流前 4xx、执行期失败才发 SSE `error`+`done`。

> 我顺手验了 §8#6 的一个细节：示例里写 `"t":12`，而实测 `t` 是真实时间戳
> （`1795108549.3250322`），与 §8#6「§2.2 示例里的 `t:12` 是错的，以真源为准」一致。

**7/7 PASS** — 证据：`logs/v12_console.txt`

---

## 15. 修复的副作用专项核查（lead 点名要求）

### 15.1 「失败时清理刚建的目录」会不会误删既有数据？

静态读代码时我看到清理前有 `created_dir = not run_dir.exists()` 守卫（bridge.py:660, 681-682），
但**只看代码不算数**，我用白盒注入 `OSError` 实测两种场景：

```powershell
py -3 _verify\v12_task4.py
```
```
既有目录场景 → WorkbenchError E_BAD_REQUEST 无法创建 run 'pre_existing'：本机文件系统拒绝了这个名称
新建目录场景 → WorkbenchError E_BAD_REQUEST 无法创建 run 'created_then_fail'：本机文件系统拒绝了这个名称
[workbench] init 'pre_existing' 失败：OSError: 注入的写盘失败（模拟文件系统拒绝）
[workbench] init 'created_then_fail' 失败：OSError: 注入的写盘失败（模拟文件系统拒绝）

[PASS] 修复副作用①-a：写盘失败时 400（不是 500），且既有目录与用户文件原样保留
       sentinel 存在=True（内容='必须活着'）目录存在=True
[PASS] 修复副作用①-b：本次新建的半成品目录被清理（不留垃圾）  created_then_fail 仍存在=False
```

结论：**不会误删**。守卫生效，既有目录里的用户文件（`important_user_data.txt`）完好；
本次新建的半成品被清掉。同时确认原始异常**只进 stderr**（`[workbench] init … 失败：OSError …`），
不再回显给 HTTP 客户端。

### 15.2 64 上限会不会与 condopt 自身约束冲突？

**会** → 即 §13 的 **F4**。这是本轮唯一未修复项，已上报。

---

## 16. 我没测到 / 测不了的部分（如实标注）

| 项 | 状态 | 原因 |
|---|---|---|
| 真机双击 `start.cmd` | **未做** | 会 `--open` 拉起真实浏览器窗口；headless 会话里无交互式桌面，改用等价显式命令 `py -3 server.py --port N --runs-dir …` 覆盖同一条代码路径 |
| 真实显示器上的人工目视（多分辨率 / 深浅色 / 演示视图 / 打印） | **部分** | 只做了 1512×1000 浅色 + 两张 PNG 的人工看图；深色主题、演示视图 `P`、`Esc`、`1`–`5` 快捷键、打印样式未逐项实测 |
| 多标签页并发 UI 连点 | **未做** | 服务端串行锁已用 3 个并发脚本实测（ask/tell/init）；浏览器多标签的 UI 竞态未测 |
| 长时间运行的内存 / 句柄泄漏 | **未做** | 无持续负载观测窗口；`ThreadingHTTPServer` + `daemon_threads` 的长期稳定性未验证 |
| README 提到的「沙箱导致的失败」基线 | **无法复现** | 本会话 file policy 是 `danger-full-access`，没有沙箱限制，因此全量 pytest 天然全绿；我无法用与 lead 相同的受限条件重放 |
| `vendor/`（marked / DOMPurify / KaTeX 本体）的安全审计 | **未做** | 我只验证了它们能加载、字体命中 200、渲染正常；未做 XSS 载荷注入与依赖版本 CVE 比对 |
| 真实 condopt 长跑（预算 160/640 档）的端到端 | **未做** | 所有 study 都用 `budget=4~8`、`batch=1~2` 的小样本，为控制在验收时长内；大预算下的 SSE 时长与 `elapsed_ms` 语义未实测 |
| `--open` 参数行为 | **未做** | 同第 1 条理由 |

---

## 17. 我最担心的一处风险

**`GET /api/study` 的正确性被押在 condopt 内部实现「永远无副作用」上。**

`bridge.py:study_snapshot()` 为了把 `step` 与 `recommend` 两个字段填满，会**真的执行**
`Study.next_action()` 和 `Study.recommend()`（`bridge.py:341-347`）。契约 §4.2-2 是硬约束：
「任何 `GET /api/*`（含 `/api/study`）不得写 run 目录」。

我实测当前是安全的：`v10_adversarial.py` 的 10.4 对 `/api/study` 连打 5 轮，
run 目录 11 个文件的 sha256 全等；`test_read_endpoints_have_zero_side_effects` 也是全绿。
但这个「安全」是**借来的**：它成立的前提是 condopt 的 `next_action()`/`recommend()`
今天恰好只读。将来这两个方法只要加一次记账、缓存落盘或 `opt_state` 刷新，
`GET /api/study` 就会**静默违反 §4.2-2**，而现有测试（比对哈希）只会在那一版
condopt 上碰巧抓到——如果它写的是 `opt_state.json` 这类允许变化的文件，甚至可能抓不到。

建议（给 lead 裁定，我不越界改）：
1. 在 condopt 侧给 `next_action()` / `recommend()` 加「只读」契约（docstring + 断言），或
2. 提供 `next_action(readonly=True)` / 由 `Study.reload()` 得到的新对象调用，或
3. 工作台侧给 `/api/study` 的这两个字段加失败兜底（已经是 `try/except`，但只兜异常、不兜写盘）。

严重度：中（当前无实际故障，但是**契约级**隐患，且违反时不会有任何报错）。

---

## 18. 证据文件索引

全部产物在 `_verify/`（临时脚本 + 日志 + 截图，均在我的写作用域内）：

| 路径 | 用途 |
|---|---|
| `common.py` | 验收公共库：真实子进程服务器、HTTP/SSE 客户端、契约 §3.1 表格解析、哈希工具 |
| `v03_e2e.py` | 项3 端到端 + 项4 SSE 五场景/逐帧 |
| `v05_contract.py` + `extract_data.js` | 项5 data.js ↔ §3.1 ↔ 后端 三方比对（node vm 真实执行） |
| `v06_security.py` | 项6 方法/穿越/只绑回环/`--host` 白名单 |
| `v07_offline.py` | 项7 离线静态扫描（含 data-URI/xmlns 分类器） |
| `v08_browser.py` | 项8 Edge headless + CDP：截图/控制台/网络/字体 |
| `v10_adversarial.py` | 项10 对抗性 + 项11 自选边界（含并发） |
| `v12_task4.py` | task-4 修复复验 + §8 裁定 + 修复副作用专项 |
| `repro_min.py` | **F1/F2/F3 最小复现**（一条命令跑完三例） |
| `probe_devnames.py` | Windows 设备名完整矩阵（含 6 个正常工作的对照） |
| `probe_longid.py` | **F4 回归探针**（task-6 裁定 (b)：长名 study 可见且可打开 + 创建期上限未放松） |
| `logs/*.log`、`logs/*_console.txt` | 各脚本的逐条 PASS/FAIL 原文 |
| `logs/revision_hashes.txt` | 首轮验收基线的文件 SHA256 |
| `logs/revision_hashes_final.txt` | **最终基线**（task-6/task-7 后）的文件 SHA256 |
| `shots/frontend_static.png` | 前端静态页截图（无后端，mock 降级） |
| `shots/backend_live.png` | 后端联调页截图（真实 SSE） |
| `_runs/` | 所有临时 runs-dir（不触碰仓库 `runs/`） |

---

## 19. 最终判定

**工作台验收：PASS（契约 §7 六项 + task-3 十组必测项全部通过；最终基线见 §1.2）**

- 契约一致性：五场景 stages 三方（契约/后端/前端）**零差异**
- 端到端闭环：真实 HTTP 七步全 `ok:true`
- SSE：帧名合法、`stage.index` 对齐、**确实逐帧 flush**、出错路径 `done.status="error"`
- 安全：405/400/只绑回环全部实测通过，外网卡确实不可达；超长 id 不再回显原串
- 离线：可执行外链 0，浏览器实测零外网请求，KaTeX 字体 200（历史缺陷已修）
- 视觉：两张 PNG 非白屏、无 JS 报错、我本人目视确认
- 回归：4 个 doc 门禁全绿，README 计数一致（616）；**全量 pytest 当前 `1 failed, 615 passed`，
  唯一失败是 `RELEASE_NOTES.md` 计数 614 未随 task-7 的 616 同步（生成式文档，非工作台代码缺陷）**
- 对抗性 + 并发：串行锁、幂等、冲突、零副作用全部符合 §4.2
- 长名 study：condopt 本体建的名字**列表可见且可打开**（task-6 裁定 (b)）

**最终复跑数字（全部在 §1.2 基线上，我自己跑）**

| 脚本 | 结果 |
|---|---|
| `v03_e2e.py` | 23/23 PASS |
| `v05_contract.py` | 19/19 PASS |
| `v06_security.py` | 52/52 PASS |
| `v07_offline.py` | 15/15 PASS |
| `v08_browser.py` | 17/17 PASS |
| `v10_adversarial.py` | **56/56 PASS** |
| `v12_task4.py` | 30/30 PASS |
| `probe_devnames.py` | 15/15 PASS |
| `probe_longid.py` | **2/2 PASS** |
| `repro_min.py` | exit 0（F1 400 / F2 400 / F3 `status="error"`） |
| `pytest tests/test_workbench.py` | **31 passed** |
| `pytest`（全量） | **1 failed, 615 passed** ← 唯一失败为 `RELEASE_NOTES.md` 计数未同步（§10） |
| `server.py --port 0 --selftest` | 7/7，exit 0 |

**待办（均不阻塞验收通过）**

| 编号 | 内容 | 严重度 | 状态 |
|---|---|---|---|
| F1 | `init` id=`NUL` → 500 | 低 | ✅ 已修并复验 |
| F2 | `init` id>64 字符 → 500 | 低 | ✅ 已修并复验 |
| F3 | SSE `done.status` 为 `running` | 低 | ✅ 已修并复验 |
| F4 | `>64` 字符既有 study「列表可见但打不开」 | 低 | ✅ 已修（task-6 裁定 (b)）并复验 |
| F5 | 错误 message 原样回显超长 id | 低 | ✅ 已修（task-7）并复验 |
| D1 | `RELEASE_NOTES.md` 计数 614 未随 task-7 的 616 同步 → 全量 pytest 1 failed | 文档（非代码） | ⬜ **待 lead 重跑 `scripts/build_release_notes.py`** |
| R1 | `GET /api/study` 依赖 condopt 无副作用 | 中（隐患） | ⬜ 建议加固（§17） |

---

## 20. 导入功能独立验收（CONTRACT §9，2026-11-20）

> 基线：`bridge.py` = `6B445304C6543329`（129,668 B）、`server.py` = `DCA031E2431E958B`、
> `tests/test_workbench_import.py` = `83F7DAA131656579`、`web/assets/import.js` = `C869D830B053628B`、
> `CONTRACT.md` = `64CF983AC9AF6FC1`。完整哈希见 `logs/revision_hashes_import.txt`。
> **实现者的自证不算证据**：本节的假网关、外部请求抓包、密钥泄漏扫描、路径对抗全部由我自己搭。

### 20.0 先修我自己的过期期望（lead 交办 A）

`v05_contract.py` 原先硬编码「场景数 = 5」并按 §4.1 的 5 行表做 `gantt[key]` 查表，§9.6 新增 `import` 后 2 FAIL + 1 crash。
我按「**期望从契约表推导，不硬编码**」的原则改（`common.contract_stages()` 改为收任何「6 列 + 首列反引号」的行；
`v05` 的场景数/顺序以 `len(gantt)` 为准，表里缺 key 时明确报「真源表未收录」而不是 `KeyError`）。

```powershell
py -3 _verify\v05_contract.py
→ CONTRACT §3.1 解析结果（真源）: {…5 个原场景…, "import": {"title":"导入数据","no":"06",
   "action":"import","needs_run":false,"stages":["读取文件","画像与列类型","让模型读表","导入为先验"]}}
[PASS] data.js 场景数 = CONTRACT §3.1 表行数（6）  data.js n=6 keys=[…,'import'] 契约表=[…,'import']
[PASS] data.js 场景 key 顺序 = CONTRACT §3.1 表顺序
[PASS] 5.kickoff / sample / feedback / delivery / board / import 与 §3.1 逐字段一致（6/6）
[PASS] data.js 没有契约表之外的野生场景  多出=无
[PASS] 后端 scenarios key 顺序与 data.js 一致  后端=[…6 个…] data.js=[…6 个…]
== 项5 data.js ↔ 契约 ↔ 后端: 22/22 PASS ==   EXIT=0
```
**PASS** — 证据：`logs/v05_console.txt`

### 20.1 覆盖率对照（lead 交办 B-1）

```powershell
py -3 -m pytest tests/test_workbench_import.py tests/test_workbench.py
→ 53 passed in 11.54s        （import 22 + workbench 31）
```
**PASS** — 证据：`logs/full_pytest_console.txt`

与 §9.7 六条的对照（**我不复用实现者的测试结论**，另用自建脚本 `v13_import.py` 独立复核，见下）：

| §9.7 | 条目 | 我的独立覆盖 |
|---|---|---|
| 1 | scan 四格式 + 列画像真实 + 路径安全 | ✅ B1–B4、J1（含 junction） |
| 2 | map 开关/consent/scan_id/坏 JSON/假网关成功路径/只发样本/密钥不泄漏 | ✅ C1–C4、D1–D5、G3a/G3b、G1、I1–I2 |
| 3 | apply 90 行、budget 校验、duplicate、坏行原因、审计、params_schema 一致 | ✅ E1–E8、G2a–G2c、F1 |
| 4 | 端到端 scan→map→apply→ask | ✅ F1 |
| 5 | 默认配置离线断言仍成立 | ✅ v07（16/16）+ v08 浏览器（零外网请求） |
| 6 | `README.md` 表述改为「默认不发」 | ✅ 实测原文已写「**默认不发任何外网请求**」（L113），与 §9.1 一致 |

### 20.2 真 server + 我自己的 stub 网关（lead 交办 B-2）

自建 stub：`127.0.0.1` 随机端口、OpenAI 兼容 `POST /chat/completions`、记录每次请求体，
用 `CONDOPT_IMPORT_LLM_BASE_URL` 指过去，密钥走 `BOHRIUM_API_KEY`。

```powershell
py -3 _verify\v13_import.py
== §9 导入功能独立验收: 50/50 PASS ==   EXIT=0   （经 task-11/12 三轮扩展至 **97/97 PASS + 1 BLOCKED**，见 §20.11/§20.13）
```
**PASS** — 证据：`logs/v13_console.txt`、`logs/v13_import.log`

**关键断言原文**：

```
### §9.4 map 假网关成功路径：只发表头+样本，不发整文件
[PASS] D1 consent:true → 200 source='llm'，返回 mapping/confidence/rationale/sent
       sent={'url':'http://127.0.0.1:…/v1/chat/completions','rows':12,'bytes':7175,'redacted':False}
[PASS] D2 **只发样本、绝不发整文件**：末行 blob 标记不在外发请求里，且出现的行标记数 ≤ sample_rows
       末行标记 BLOBMARK0087END 出现=False；外发请求里出现的行标记数=12/88（sample_rows=12）；
       外发 7175 字节 vs 整文件 19645 字节
[PASS] D2b 外发体里也不含整文件的其他大段内容（blob 填充串出现次数有限）
       180 字符填充串出现 15 次（整文件 88 行；<29 才说明只是样本）
[PASS] D5 假网关收到的确实是 chat/completions 形制（有 model + messages）

### §9.1 密钥不泄漏（全程）
[PASS] G1 本次全部 HTTP 响应中都没有出现密钥明文
       泄漏点=无（共检查 bootstrap/scan/map/apply/ask 等全部响应）
```

> **一次我自己制造的假缺陷（如实记录）**：第一版我把「整文件泄漏」的判别标记放在 **`yield` 列的末行数值**上，
> 结果该标记「泄漏」了 —— 因为 `yield` 的**列画像 `max`** 本来就包含它，而 §9.1 明确允许外发「列画像」。
> 那不是缺陷，是我的判别器选错了列。改用**不会被统计成 min/max 的文本 blob 列**、
> 并加上「出现的行标记数 / 填充串次数 / 字节数 vs 整文件」三重判别后，结论才是可信的。

**consent 门控（§9.4）**：
```
[PASS] C1 开关+密钥就绪但没有 consent → 400 E_CONSENT_REQUIRED（不是 503）
[PASS] C1b 缺 consent 时**没有**发出任何外发请求  假网关收到请求数=0
[PASS] C2 fallback:'rules' 不需要 consent → 200 source='rules' 且 sent=null
[PASS] C3 scan_id 不匹配 → 400（防「扫 A 打 B」）
[PASS] C4 map 未知字段 → 422 E_VALIDATION
```

**未开开关 / 开开关无密钥（lead 点名的实测点）**：
```
[PASS] H3 未开开关 + 带 consent → 503 E_LLM_UNAVAILABLE（而不是 E_CONSENT_REQUIRED）
[PASS] H4 未开开关时仍可显式走离线规则映射（不静默、需显式指定）  source=rules
[PASS] I1 开开关无密钥：bootstrap 报 enabled=True / configured=False
[PASS] I2 开开关无密钥 → 503 E_LLM_UNAVAILABLE，message 说清缺的环境变量名
       message='模型读表缺少密钥（环境变量 BOHRIUM_API_KEY 未设置）'
```

**坏 JSON + 「只修格式」重试（§9.4）**：
```
[PASS] G3a 首次非 JSON → 自动做一次「只修格式」重试后成功（共 2 次外发调用）
       status=200 外发调用次数=2 source=llm warnings 含重试说明=True
[PASS] G3b 两次都非 JSON → 502 E_LLM_BAD_RESPONSE，且 hint 附原始响应片段
       hint='原始响应前 500 字符: 这不是 JSON，只是普通说明文字。'
```

### 20.3 路径安全对抗（lead 交办 B-3）

```
### §9.2 scan：路径安全
[PASS] B4 scan .. → 400                      '路径不在允许根内: …'
[PASS] B4 scan 根外绝对路径 → 400             '路径不在允许根内: <drive>:\Windows\win.ini'
[PASS] B4 scan 目录 → 400                     '路径是目录，不是文件'
[PASS] B4 scan 错误后缀 → 400
[PASS] B4 scan 缺 path → 422

### §9.2 路径安全补测：符号链接逃逸 / 超限 / 设备名
    符号链接创建失败（Windows 需要开发者模式/管理员）：OSError WinError 1314 客户端没有所需的特权
    junction 回退：mklink /J esc_dir -> import_outside → rc=0 Junction created for … <<===>> …
    超限文件：big.csv = 22122506 字节（21.1 MB，契约上限 20 MB）
[PASS] J1 允许根外 → 400
[PASS] J1 超 20MB（实测 21.1 MB） → 400  '文件过大（22122506 字节 > 上限 20971520）'
[PASS] J1 Windows 设备名 NUL → 400
[PASS] J1 目录 junction 指向根外 → 400  '路径不在允许根内: …'
```

> **符号链接一栏我做了替代而不是放过**：Windows 上 `os.symlink` 需要开发者模式/管理员（实测
> `WinError 1314 客户端没有所需的特权`）。我没有把它算 PASS，而是改用**不需要特权的目录 junction**
> （`mklink /J`）制造同样的「允许根内的名字指向根外」语义 —— 结果 400，说明 `resolve()` 确实穿透
> junction 且根检查生效。这一条比 symlink 更贴近 Windows 的常见逃逸手法。
> 若 junction 也建不出来，脚本会打印 `[BLOCKED]` 并**不计入 PASS**（`common.Report` 新增第三态）。

### 20.4 预算诚实性（lead 交办 B-4）

```
[PASS] E1 budget 小于导入行数 → 422 E_VALIDATION 且带 suggested_budget（数字）
       status=422 suggested_budget=114 message='budget=5 小于导入行数 85：先验数据计入预算，不允许静默放大'
[PASS] E2 budget 不足时**不静默放大**：run 没有被创建出来   runs/should_not_exist 存在=False
[PASS] E5 n_obs 与 imported.accepted 一致；重复行计入 duplicate；坏行逐行给原因
       n_obs=85 imported={accepted:85, duplicate:1, rejected:2}
       rejected=[{'row':86,'reason':"'yield' 不是数值：'n/a'"},
                 {'row':87,'reason':"条件不在空间内：variable 'T': 400.0 outside [40.0, 120.0]"}]
[PASS] F1 apply 之后立刻 ask 出下一批（ok:true，pending 非空）  pending=2 remaining=27
```
**注意**：`suggested_budget=114` 与契约 §9.5 示例的 `120` 不同，是因为我的数据是 88 行（契约示例是 90 行）；
口径 `accepted + max(10, ceil(accepted/3))` 在 85 条上给出 114 —— 与 `bridge._suggested_budget` 的 docstring 一致，**不是缺陷**。

### 20.5 审计落盘（lead 交办 B-5，含 `count_toward_budget:false` 裁定）

```
[PASS] E8 import_audit.jsonl 有对应记录（含 path/sha256/accepted/budget/reason）
       记录={"t":1795156119.5262296,"path":"…lab_log.csv",
             "file_sha256":"b4673050…","source":"llm","model":"stub-model",…}
[PASS] G2a count_toward_budget:false → 仍 200 导入成功（不静默忽略开关，而是明确留痕）  accepted=85
[PASS] G2b 回应里有 warning 说明「condopt 语义是先验计入 budget，无法关闭」
       warnings='condopt 的 import_observations 语义是「先验计入 budget」，无法关闭；本次已按计入处理…'
[PASS] G2c 审计记录里 count_toward_budget_requested=false（裁定要求的字段）
       审计键=['accepted','budget','consent','count_toward_budget_requested','duplicate',
              'file_sha256','model','path','reason','rejected','source','t']
```
`file_sha256` 我另用 `hashlib.sha256(文件字节)` 独立复算比对，一致。

### 20.6 `params_schema` 来自代码（lead 交办 B-6）

```
[PASS] E6 params_schema 来自 registry（与 describe_all 逐字段一致，不手写）
       algorithm=constrained params_schema 字段=['kernel','length_scale','ard','alpha',
       'n_initial','n_candidates','acquisition','kappa']
```
我的比对方式：取 `condopt.registry.describe_all()[algorithm]["params"]`，逐项比 `name/type/default`，
**不是**只数个数。`auto` 路由到 `constrained`（因为 mapping 里有 `purity ≥ 95` 约束），随后
`ask` 也确实按该算法出点。

### 20.7 默认配置的离线断言仍成立（lead 交办 B-7）

```
py -3 _verify\v07_offline.py   → == 项7 离线静态扫描: 16/16 PASS ==
py -3 _verify\v08_browser.py   → == 项8 Edge headless 视觉 + 控制台 + 网络: 17/17 PASS ==
   前端静态页  外网请求 : 无     同源请求 20 条
   后端联调页  外网请求 : 无     同源请求 25 条
```
默认启动（**不带** `--import-llm`）时浏览器实测零外网请求，§9.7.5 成立；
六场景侧栏、导入场景页均正常渲染、无 JS 报错。

### 20.8 回归（lead 交办 B-8）

```
py -3 -m pytest
→ 651 passed in 642.03s (0:10:42)     FULL_PYTEST_EXIT=0   （task-10/11 前 638、task-11 后 649）

py -3 -m pytest --collect-only -q     → sum=651（最终）
README.md L100: - **测试**：651 个单测…        L285: tests/  单测（651）
RELEASE_NOTES.md: | 单测 | **651** |          ← 早前 614/616 的陈旧问题已由 lead 重新生成修复
```
```
v03_e2e 23/23 · v05_contract 22/22 · v06_security 52/52 · v07_offline 16/16
v10_adversarial 56/56 · v12_task4 30/30 · probe_devnames 15/15 · probe_longid 2/2
v13_import 97/97（+1 BLOCKED）· selftest 9/9 exit 0
```
**PASS** — 全部证据见 `logs/*_console.txt`。

### 20.9 本节发现的缺陷与文档不一致

| 编号 | 内容 | 严重度 | 状态 |
|---|---|---|---|
| F6 | 环境变量里的密钥含**非 ASCII / 控制字符（含 CRLF 头注入）** 时 → 500 `E_INTERNAL（UnicodeEncodeError）` | 低（需非常规输入） | ✅ 已修（task-11）并经我 7 用例复验 |
| F7 | `CONDOPT_IMPORT_LLM_BASE_URL` 含**空格 / 坏端口** → 500 `E_INTERNAL（InvalidURL）`（`Request(...)` 在 try 之外，`InvalidURL` 不是 `UnicodeEncodeError` 子类） | 低-中 | ✅ 已修（task-11 二轮）并经我 10 用例复验 |
| F8 | `CONDOPT_IMPORT_LLM_BASE_URL` **显式置空/纯空白**会静默回退到**真实默认端点**（实测真发出请求并拿到 401） | 低 | ✅ 已修：空/空白 → `url=""`、`url_valid=false`、503「不会回退到默认端点」 |
| D3 | `CONTRACT.md` §4.1 表 6 行但正文仍写「五个场景」、§5 仍写「data.js → 五场景」 | 文档 | ✅ 已由 lead 修（现全文无「五个/五场景」残留） |
| D4 | §2 端点表缺 `/api/import/scan·map·apply` 三行 | 文档（低） | ✅ 已由 lead 补全（现 §2 有 5 行 POST） |
| D5 | （新，低）`bootstrap.import_llm.url_valid` 对 **非 ASCII 主机**（`http://例子.测试/v1`）报 `true`，但 `map` 实际 503 —— 该字段作为前端「地址已配置」的预检信号并不完整 | 低（观感/一致性） | ✅ 已修（task-12）：非 ASCII → `url_valid=false` + `url_error` 指 punycode 写法；punycode 形式仍 `url_valid=true`。我用**自建不变量测试**复核（9 用例，见 §20.11） |
| — | （核对通过，非缺陷）`/api/run {scenario:"import"}` → 400 且 hint 指向 `/api/import/*`（`H0a`）；`GET /api/import/scan` → 405（`H0b`）—— 二者均符合 §9.6 | — | ✅ 已核 |

F6 的最小复现（一条命令）：
```powershell
$env:PYTHONUTF8='1'
py -3 _verify\v13_import.py      # 看最后一节「F6 探针」
# 或直接：把 BOHRIUM_API_KEY 设成含中文的值 → POST /api/import/map {consent:true} → 500 E_INTERNAL（UnicodeEncodeError）
```
我的判定：**低危，可不修**（真实 API key 是 ASCII）；若要修，建议在 `_llm_chat` 里对 `api_key`
做一次 ASCII/latin-1 校验，失败就报 `E_LLM_UNAVAILABLE`「密钥含非 ASCII 字符，检查环境变量 X」——
比让用户面对一个 500 更可诊断。

### 20.10 §20 的自我更正记录

本轮我自己犯了 4 个错，都改在**脚本**里而不是改结论：

1. **判别器选错列**（D2）：把「整文件泄漏」标记放在数值列末行，被列画像 `max` 名正言顺带出 → 误报；
   改为文本 blob 列 + 三重判别。
2. **超限文件算错大小**：`300 × 4096 × 10 B ≈ 12.3 MB`，根本没到 20 MB，于是「超限应 400」失败 —— 是我的算术错，不是实现错；改成「写到 >21 MB 并用实测字节断言」。
3. **stub 的调用计数是全程累计**，`call_count == 1` 只在脚本第一次调用时成立，导致「重试」用例失效；
   改为每模式独立计数 `mode_calls`。
4. **两处 `http()` 返回值解包**（`(status, text)` vs 实际 `(status, headers, text)`）导致脚本崩溃。

另外把 `probe_longid.py` 的 PASS 文案从「描述修复前现象」改成判据陈述、修掉一个 `list[str]` 按 dict 取值
的崩溃，并把 `Report` 加上 **BLOCKED 第三态**（跑不了 ≠ 跑了不对，但也不许写成 PASS）。

### 20.11 复验记录：task-11（F6）→ task-11 二轮（F7/F8）

**第一轮（task-11 后）**：F6 的**密钥**一半修好，**baseURL** 一半没修完。
```
[中文密钥][emoji][CRLF 头注入][LF 头注入][DEL 0x7F][控制字符 0x01]
→ 全部 503 E_LLM_UNAVAILABLE，点名 BOHRIUM_API_KEY、无密钥回显、无异常名、**外发请求数=0**
[BLOCKED] NUL 0x00 —— 本机放不进环境变量（ValueError: embedded null character），未计 PASS
[PASS] F6c 反向：ASCII 密钥 → 200 source='llm' 且真的外发 1 次
[FAIL] baseURL 含空格 / 端口不合法 → 500 E_INTERNAL（InvalidURL）   ← F7，我据此回报
```
> 我在这一轮发现并回报了 **F7**（`Request(...)` 在 try 之外；`http.client.InvalidURL` 不是
> `UnicodeEncodeError` 子类，两个 except 都接不到）与 **F8**（显式置空静默回退到真实默认端点）。

**第二轮（F7/F8 修复后，最终基线）**：
```
py -3 _verify\v13_import.py
== §9 导入功能独立验收: 97/97 PASS，1 BLOCKED（未计为失败） ==   EXIT=0
```
```
    [baseURL '含空格']        status=503 '模型网关地址含空白字符'                    url_valid=False
    [baseURL '端口不合法']     status=503 '模型网关地址的端口不合法'                  url_valid=False
    [baseURL '空字符串']       status=503 '…为空/纯空白（显式置空不会回退到默认端点）'   url='' url_valid=False
    [baseURL '纯空白']         status=503 同上                                      url='' url_valid=False
    [baseURL '没有 scheme']    status=503 '模型网关地址必须以 http:// 或 https:// 开头'
    [baseURL '非法 scheme']    status=503 同上
    [baseURL '误拼 /chat/completions'] status=503 '应填到 /v1 为止（不要带 /chat/completions）'
    [baseURL '非 ASCII 主机']  status=503 '地址或请求头不合法（无法发起 HTTP 请求）'   url_valid=True（见 D5）
    [baseURL '路径含 ..']      status=200（原样使用、连得上就正常调）
    [baseURL 无人监听（对照）]  status=503 '无法连接模型网关（URLError）'
    [PASS] F8 baseURL 空字符串 → 503 且明确说明「不会回退到默认端点」，不构造默认 URL
    [PASS] F6c 反向：ASCII 密钥 → 200 source='llm' 且真的外发了一次
```
`probe_empty_baseurl.py`（我先修掉自己探针的 bug 才拿到可信数据，见 §20.12）：
```
未设置（键不存在） → url='https://open.bohrium.com/openapi/model-gateway/v1'  url_valid=True
显式空串 ''       → url=''   url_valid=False     ← F8 已修：不再回退
显式空白串 '   '   → url=''   url_valid=False
```

**对 api-backend 邀请我压的 `..` 用例的判定**：`CONDOPT_IMPORT_LLM_BASE_URL='…/v1/../..'` → **200，原样使用**。
我认为**不需要额外拦截**：`baseURL` 是**操作者在启动命令/环境变量里设定的可信配置**，不是请求方输入；
它与 §9.2 防的 `path`（请求方提供、可被滥用）不是同一类信任边界。若哪天前端把用户输入拼进 baseURL，
才需要重新评估。保留为「已知且接受」。

**第二轮基线（历史快照，不是最终值 —— 最终值见 §20.13）**：`bridge.py` = `9188DC39B47FD6FA`（135,279 B）、
`server.py` = `DCA031E2431E958B`、`tests/test_workbench_import.py` = `59636BB973604612`、
`tests/test_workbench.py` = `3EA6F4922CF7EDF3`、`CONTRACT.md` = 26,816 B（§2 已补 import 三行）。

**第二轮数字（全部我自己跑，同一基线；后续 task-12 又加了 D5 修复与 2 项测试）**

| 入口 | 结果 |
|---|---|
| `pytest tests/test_workbench_import.py tests/test_workbench.py` | 64 passed |
| `pytest`（全量） | 649 passed，exit 0 |
| `--collect-only` / README / RELEASE_NOTES | 649 = 649 = 649 |
| `server.py --port 0 --selftest` | 9/9，exit 0 |
| `v13_import.py` | 83/83 PASS，1 BLOCKED |
| v03 / v05 / v06 / v07 / v10 / v12 | 23/22/52/16/56/30 全 PASS |
| probe_devnames / probe_longid | 15/15、2/2 PASS |

### 20.12 复验中的自我更正（三轮里我共犯了 5 个，全部改在脚本/流程，没改结论）
**第三轮（task-12 D5）新增 1 个：**

5. **读错了对象**：D5 修复后我加断言查 `bootstrap.import_llm.url_error`，结果 `None` → 误报 FAIL。
   实际 `url_error` 在 **`bootstrap.import`**（`import_settings()`），`import_llm` 只有 `url_valid`
   （代码 L540-547 vs L393-402 两处字段不同）。我没有改断言阈值，而是**读代码确认字段归属**后
   改为查 `bootstrap.import.url_error`，并顺手加了两块 `url_valid` 必须一致的断言。
   api-backend 的原话「`import_llm` 新增 `url_valid`，`import` 新增 `url_valid`/`url_error`」其实是准确的，
   是我把两个块合并读了。

**前两轮：**

1. **早跑了一步**：F7 修复写盘过程中我启动了快集与全量，撞上 `bridge.py` 半写状态
   （`SyntaxError: invalid character '：'`），多个脚本无摘要行退出。我**作废了该轮全量结果**
   （`job_kill`）并等 api-backend 静默后再跑，而不是把「半写版本」的失败当成回归。
2. **探针自己把要测的值删了**：`probe_empty_baseurl.py` 第一版对三种情形都传了
   `drop_env=["CONDOPT_IMPORT_LLM_BASE_URL"]`，而 `common.Server` 是先合并 `extra_env`、再执行 `drop_env` ——
   于是「显式空串」被我自己的 drop 删成了「未设置」，三行输出一模一样，看起来像 F8 没修。
   我是靠**两个 artifact 互相矛盾**（v13 说 `url=''`、探针说 default）发现并定位的。
3. **判别器选错列**（§20.2）：把「整文件泄漏」标记放在数值列末行，被列画像 `max` 名正言顺带出 → 误报。
4. **超限文件算错大小 / stub 计数是全程累计 / 两处返回值解包**（§20.10）。

### 20.13 第三轮复验（task-12 · D5）：最终结果

```powershell
py -3 _verify\v13_import.py
== §9 导入功能独立验收: 97/97 PASS，1 BLOCKED（未计为失败） ==   EXIT=0
```
```
    [baseURL 非 ASCII 主机] status=503 '环境变量 CONDOPT_IMPORT_LLM_BASE_URL 含非 ASCII 字符
                                          （主机名请用 punycode，即 xn--… 形式）'  url_valid=False
[PASS] D5 非 ASCII 主机 → url_valid=False、message 指向 punycode 写法、
       scan.sample_preview.endpoint 为空、bootstrap.import.url_error 非空
[PASS] F6e 不变量（合法 IP+端口）    url_valid=True → map 200 外发=1
[PASS] F6e 不变量（合法带尾斜杠）    url_valid=True → map 200 外发=1
[PASS] F6e 不变量（非 ASCII 主机）   url_valid=False → map 503 外发=0
[PASS] F6e 不变量（空串/纯空白/含空格/没有 scheme/坏端口/误拼）→ 全部 url_valid=False、503、外发=0
[PASS] F6f baseURL punycode 域名 → url_valid=True（不是一刀切禁国际化域名；只查 bootstrap，不做 DNS）
[PASS] F6f baseURL localhost / 不带 /v1 → url_valid=True
[PASS] F6c 反向：ASCII 密钥 → 200 source='llm' 且真的外发了一次
```

> **我没有把实现者的 `test_D5_invariant_...` 当证据**，而是自己重写了一遍不变量：
> 用我自己的 stub 计数器观测「`bootstrap.url_valid` 为真 ⇔ 真的发出了 HTTP 请求」，
> 9 个用例逐个核对（`valid ⇒ 200 且外发≥1`；`invalid ⇒ 503 且外发=0`）。结论与他们的测试一致，
> 但这是两条独立得出的一致，不是复述。

**最终基线（第三轮）**：`bridge.py` = `0DABACA0EAF5E7B6`（135,802 B）、
`server.py` = `DCA031E2431E958B`、`tests/test_workbench_import.py` = `01E35817D2D1DF19`、
`tests/test_workbench.py` = `3EA6F4922CF7EDF3`。

| 入口 | 结果 |
|---|---|
| `v13_import.py` | **97/97 PASS + 1 BLOCKED** |
| `pytest tests/test_workbench_import.py tests/test_workbench.py` | **66 passed** |
| `pytest`（全量） | **651 passed** in 642.03s，exit 0 |
| collect / README / RELEASE_NOTES | **651 = 651 = 651** |
| `--selftest` | 9/9，exit 0 |
| v03 / v05 / v06 / v07 / v10 / v12 | 23/22/52/16/56/30 全 PASS |
| probe_devnames / probe_longid | 15/15、2/2 PASS |

**唯一未测项**：NUL 密钥（Windows 无法把含 NUL 的值放进环境变量）→ **BLOCKED**，不计 PASS。


---

## 21. 对话式开工独立验收（CONTRACT §10，2026-11-21）

> 仓库：`<repo>`（独立仓库，condopt 从同级检出解析）。
> 基线哈希（`_verify/logs/revision_hashes_task16.txt`）：
> `server.py=CB89DC4EF8EF6EC7`、`bridge.py=BEDA3AE34EF2539E`、`CONTRACT.md=A804774AA8D8195B`、
> `tests/test_kickoff.py=70E77E6945144335`、`web/index.html=C45FCA1FA8E659DD`、
> `web/assets/kickoff.js=BA4BBB351C008CEA`、`web/assets/condopt.js=D83F1466D921613D`、
> `web/assets/app.css=B935FD05325B974D`。
> 原则不变：**实现者的自证不算证据**；14 条必测逐条自己重跑，并补了我自己的对抗用例。

### 21.0 结论速览

| 组 | 必测 | 结果 | 证据 |
|---|---|---|---|
| A | 1 版面 gate | **PASS**（0 零尺寸 / 0 重叠 / exit 0） | `logs/geom_probe_console.txt` |
| A | 2 四状态几何（真实浏览器） | **PASS**（12/12；9 个 raw 重叠全部证伪为幻影） | `logs/v21_geom_console.txt` |
| A | 3 关闭的 `<details>` 是否占版面 | **PASS**（收起态 0 高 + `display:none`；基线对照证明默认行为确实会占 317px） | 同上 |
| B | 4–9 chat/plan/离线 | **PASS**（44/44） | `logs/v21_kickoff_console.txt` |
| C | 10–12 init params | **PASS**（含 4 类 422 + 无残留目录） | 同上 |
| — | §10.5.5 前端 + 真点「建 study」 | **PASS**（10/10，服务端真的建出 run） | `logs/v21_frontend_console.txt` |
| D | 13 全量 pytest + selftest | **PASS**（**101 passed**；selftest **9/9**） | `logs/full_pytest_console.txt` |
| D | 14 既有语义单跑 | **PASS**（3 个用例 + 我自己用真实 HTTP 复验 3/3） | `logs/v21_regression3_console.txt`、`logs/v21_regression_semantics_console.txt` |

**FAIL：0 条。** 另有 2 条低危观察（F9 展示层、P1 探针盲区）见 §21.9。

### 21.1 A1 版面 gate（lead 的硬 gate）

```powershell
py -3 _verify\geom_probe.py
→ visible boxes: 9 / 15（取决于 demo 状态）
→ zero-size boxes (被压扁的容器): 0
→ suspicious overlaps (ratio<=0.92): 0
→ EXIT=0
```
**PASS** — 证据：`logs/geom_probe_console.txt`。
我另外**把 kickoff 新 UI 的容器加进它的 `sel` 列表**（task-16 允许的唯一改动）后再跑，
仍是 0/0/exit 0：`logs/geom_probe_extended_console.txt`。

### 21.2 A2 四状态几何（真实 Edge + 我扩的选择器）

因为 `geom_probe.py` 只覆盖一个 URL（且带 `?demo=kickoff`，会先跑 SSE），我另写了
`_verify/v21_geom_states.py`：在**同一个浏览器会话**里把 UI 驱动到四种状态，每态取几何、
按 `geom_probe` **相同的判定公式**（零尺寸 ≤1px；非父子包含关系且 ratio>0.92 算可疑重叠）算一遍。

```
S1 首屏      原始口径 零尺寸=0 重叠=0
S2 选项态    原始口径 零尺寸=0 重叠=1
S3 参数页    原始口径 零尺寸=0 重叠=4
S4 抽屉打开态 原始口径 零尺寸=0 重叠=4
== task-16 A2/A3 · kickoff 四状态几何: 12/12 PASS ==
```

**9 个 raw 重叠我没有直接报缺陷，而是逐个做了「是否真的被绘制」的判定**，结论全是幻影：

```
[.condopt-kickoff-head × .condopt-bubble[6]]  in_stack_a=True in_stack_b=False 栈深=8 top=div.condopt-kickoff-head
[.condopt-plan-build × .condopt-param[5]]     in_stack_a=False in_stack_b=False 栈深=0 top=None
[.condopt-plan-build × .condopt-param[6]]     in_stack_a=False in_stack_b=False 栈深=0 top=None
[.condopt-param[5] × .condopt-advanced-params] in_stack_a=False in_stack_b=False 栈深=0 top=None
```

判据是 `document.elementsFromPoint`（它返回**完整堆叠栈**，被压住的元素也会出现；
被 paint-skip 或滚出裁切区的元素**不会**出现）。两类成因：

1. **滚动容器裁切**：`.condopt-chat-log` / `.stream` 是 `overflow:auto`，子元素滚出可视区后
   `getBoundingClientRect()` 仍返回**未裁切**的布局矩形，于是与上方兄弟「相交」但根本看不见。
2. **paint-skip 幻影**：收起的 `<details>` 在 Chromium 里其内容子树不绘制，但矩形仍在。

决定性反证（`_verify/v21_advparams_space.py`，见 §21.3）：把那个幻影 body 强行设 `display:none`，
**`build_y` 与 `scrollHeight` 变化都是 0** —— 说明它对布局的真实贡献为零。

> **这条我踩过一个坑，如实记录**：我第一版只做「裁切感知」修正（把元素矩形与 `overflow!=visible`
> 祖先求交），结果 S3/S4 的 4 个重叠被错误地「消掉」了 —— 而同一个重叠在**滚到底**时又真实可见。
> 两种口径都不对：裁切感知会漏，原始口径会误报。最后是「三方交叉」才定案：
> ① `elementsFromPoint` 堆叠栈成员判定 ② 强行 `display:none` 的布局增量 ③ 截图人眼确认。

### 21.3 A3「关闭的 `<details>` 仍占版面」——我自己量

```
抽屉（#condoptAdvanced）收起态实测：
{"open": false, "bodyHidden": true, "bodyDisplay": "none", "bodyH": 0, "formHidden": true, "formH": 0, "advH": 44}
Chromium 基线（未加 display:none 的关闭 details）：
{"open": false, "innerH": 317, "display": "block", "contentVisibility": "visible", "detailsH": 23}
```
- 现状**已修**：收起时 body `display:none` + `hidden`，高度 0，抽屉只剩 summary 的 44px；
  机制是 `app.css:L1133` 的 `.condopt-advanced:not([open]) > .condopt-advanced-body { display:none }`
  加 JS 的 `body.hidden = !advanced.open` 双保险。
- 基线对照证明**这个修复是必要的**：不加处理时关闭的 `<details>` 内容确实仍占 317px。

我另外发现**新出现的**兄弟抽屉 `.condopt-advanced-params`（高级参数）**没有**同样加固：
它的收起态 body 仍报 678px 矩形，而 `plan-build` 位于 y=606 —— 两者矩形重叠 41,566 px²。
但经上面三项判定，这是 **paint-skip 幻影、非缺陷**（`display:none` 增量 0、命中测试落在 build 按钮上、
截图干净、开/关切换后 build 位置复原）。**记为低危加固不对称（F10），建议补一条同款 CSS**，见 §21.9。

### 21.4 B4–B9 对话与参数页（44/44）

```powershell
py -3 _verify\v21_kickoff.py
== §10 对话式开工（API 侧）: 44/44 PASS ==
```
关键原文：
```
[PASS] 4.1 state:{} → 422 E_VALIDATION  message="state.step 非法: 'None'" hint='允许: objective, budget, …, done'
[PASS] 4.2 state:null 等价于省略 → 200
[PASS] 5.1 五轮内收敛：step=='options' 且 2<=len(options)<=4  chip 答案轮数=5 options=4 keys=['bayes','sobol','lhs','random']
[PASS] 5.3 recommended 唯一  → ['bayes']
[PASS] 5.4 **我自己调 condopt.router.route() 复算** 我复算=bayes == 响应 recommended=bayes == candidates.recommended
[PASS] 5.5 用芯片 label（按钮点击回传文本，走 _match_chip）也能推进到下一步
[PASS] 6.1 信息不足 → 停在**缺的那一步**（variables），options=None（不凑答案）
[PASS] 6.2 reply.text='还差 **至少一个可调条件及其范围** 才能给你算法选项，所以先停在「variables」…不会用一个凑出来的选项糊过去'
[PASS] 7.1 free_text 乱输 → 停在 objective、options=null、'这句我没解析出需要的信息，所以不敢替你猜'
[PASS] 8.1 plan.params 与 registry.describe_all()[bayes].params 逐字段一致（name/type/default/choices）  参数数=12
[PASS] 8.2 plan.why == 我自己复算的 router.route().rationale（3 条逐字相同）
[PASS] 9.1 阳性对照：socket 补丁确实能拦住一次真实连接
[PASS] 9.2 屏蔽 socket 后 chat→plan→init 全部跑通  步进=['objective','budget','variables','noise','constraints','options']
```
`router` 复算方式：取 `plan` 返回的规范 `answers` → 我自己 `condopt_prepare.parse_answers()` +
`condopt_router.route(context)`，与响应里的 `recommended` / `why` 对比 —— **不是**读响应的自述。

### 21.5 C10–C12 init params

```
[PASS] 10.1 完整平表 → config.json 的 params **逐字等于**传入值（12 个键）
[PASS] 11.1 部分平表 → 传入键原样落地 且 整体 == registry.validate_params(algo, 传入)
[PASS] 12.1 未知键 → 422 'params 含 bayes 没有的参数: no_such_param'
[PASS] 12.1 非法值 → 422 "params 取值不合法: param 'kernel': expected one of ('matern52','matern32','rbf')…"
[PASS] 12.1 嵌套结构 → 422 'params 必须是平表，这些键的值是嵌套结构: kernel'
[PASS] 12.1 params 非对象 → 422 'params 必须是平表对象 {参数名: 值}'
[PASS] 12.2 ×4 → **不留残留 run 目录**（逐条查目录，均为 False）
[PASS] 12.3 algorithm='auto' + 非空 params → 422 'auto 路由自己决定算法…'
[PASS] 12.5 不传 params → 仍 200（旧行为兼容）
```

### 21.6 §10.5.5 前端 + 真点「建 study」

```powershell
py -3 _verify\v21_frontend_kickoff.py
== §10.5.5 前端一致性 + 建 study 端到端: 10/10 PASS ==
```
```
首屏：{"has_chat_log": true, "chat_visible": true, "adv_open": false,
       "form_hidden": true, "visible_form_inputs_on_first_screen": 0, "chips": 3}
选项态：{"cards": 4, "all_buttons": true, "has_title": 4, "has_one_line": 4, "has_why": 4,
        "has_tradeoff": 4, "recommended_badges": 1, "raw_urls": 0}
参数页：{"plan_head": true, "why_items": 3, "params": 12, "with_label": 12, "with_plain": 12,
        "with_impact": 12, "advanced_default_collapsed": true,
        "build_btn_text": "建 study（走 POST /api/action init）"}
点击前 runs=[]；点击后新增=['run-20261121-0131']；UI 里的 run id='run-20261121-0131'
新 run 的 config.params：algorithm='bayes' params 键数=12 /** 只新增 1 个，无残留中间态 **/
```
即：首屏确实是聊天（表单字段可见数 0）、选项是**真按钮**（无裸 URL）、参数页有解释与默认折叠的
advanced、点「建 study」**真的**走 `init` 并在服务端建出 run。

### 21.7 D13 全量回归

```powershell
py -3 -m pytest            → 101 passed in 25.37s       FULL_PYTEST_EXIT=0
py -3 server.py --port 0 --selftest → 9/9 通过，exit 0
收集数：tests/test_kickoff.py 35 + test_workbench.py 31 + test_workbench_import.py 35 = 101
```
**PASS** — 证据：`logs/full_pytest_console.txt`、`logs/selftest_console.txt`。
selftest 现在含两条导入用例（错误后缀 400、无效 scan_id 400），比上轮多 2 条。

### 21.8 D14 既有语义未被破坏

单跑三条最相关的既有用例：
```powershell
py -3 -m pytest tests/test_workbench.py::test_sse_kickoff_frames \
                 tests/test_workbench.py::test_ask_is_never_silently_repeated \
                 tests/test_workbench_import.py::test_apply_budget_validation -v -o addopts=""
→ 3 passed in 0.83s   EXIT=0
```
**并且我没有只跑他们的测试**，另用真实 HTTP 自己复验了这三条语义（`_verify/v21_regression_semantics.py`）：
```
[PASS] 1) SSE：帧名 ⊆ {meta,stage,delta,notice,error,done}，首 meta 末 done，stage indices=[0,1,2,3]
[PASS] 2) q9 冲突：二次 ask → 409 E_CONFLICT，pending 仍 2、remaining 未多扣（6→6）
[PASS] 3) 导入 budget：budget=3 < 30 行 → 422 且 suggested_budget=40、run 未创建
== 3/3 PASS ==
```

### 21.9 本节发现的问题（均非 FAIL）

> **注（2026-11-21 01:55 追记）**：本节写成时 F9 尚未修复；**在我准备复验的这段时间里
> task-17 已落地**，我已用独立实现的检查器复验并确认修复，行内状态已更新为「已修」。
> 原始证据（当时的 `plain` 文本与 `PARAM_DISPLAY` 键形态）保留在下方，以便对照。

| 编号 | 内容 | 严重度 | 状态 |
|---|---|---|---|
| **F9** | **`PARAM_DISPLAY` 只按参数名索引，跨算法共用一份文案**。实测 30 个参数名跨算法重复、其中 **5 个语义签名不同**（`acquisition`/`alpha`/`batch_strategy`/`mc_draws`/`n_candidates`）。最具体的例子（**修复前**）：`acquisition` 的人工文案写「pi 更偏利用；ts 用后验采样」，但 `mfbo` 的 choices 只有 `['ei','logei','ucb']` —— 文案列举了该算法**不存在的选项**。实际控件只渲染 registry 给出的合法选项，用户选不到非法值，所以**是文案问题、不是功能问题** | 低（展示层） | ✅ **已修（task-17）并经我独立复验**，见 §21.12 |
| **F10** | **两个 `<details>` 抽屉的收起态加固不对称**。`#condoptAdvanced` 有 `:not([open]) > body{display:none}`（app.css:L1133），新的 `.condopt-advanced-params` 没有。收起时其 body 仍报 678px 矩形并与「建 study」块重叠 41,566 px² —— 但我已用三重判定证明这是 **paint-skip 幻影、当前无用户可见影响**（`display:none` 增量 0、命中测试落在 build 按钮、截图干净、开/关可复原） | 低（潜在/加固） | ✅ **已修（task-18）并经我复验**（两态 3/3 + 4/4，含「收起不影响取值」独立验证），见 §21.13 |
| **P1** | **`geom_probe.py` 的判据有系统性盲区**：① 只测一个 URL/状态，选项态与参数页完全没覆盖；② 矩形相交无法区分「真绘制」与「滚出裁切区 / paint-skip 的幻影矩形」，**既会误报也会漏报**。我的 9 个 raw 重叠全部是幻影，若直接采信就会报 9 个假缺陷 | 中（工具） | ✅ **已修（lead 重写为三重判据）并经交叉对照**：他的判据跑我的四种状态，逐态与我的结论一致，见 §21.13 |
| **F11** | **task-17 守卫的一处缺口（我在负对照里抓到的）**：`test_param_copy_never_lies` 的「默认 X」检查带 `claimed in known` 前置，导致**任意而错误的数字**（如「默认 4096」而 registry 默认 1024）被静默忽略，与它 docstring 宣称的严格度不一致 | 低（守卫/测试） | ⬜ 建议去掉该前置 + 数字规范化，见 §21.13 |

**FAIL 最小复现**：无（0 条 FAIL）。
F9 的复现：`py -3 -c` 见 §21.9 上方代码块，或直接 `plan` 一个 `mfbo` 看 `acquisition` 的 `plain`。
F10 的复现：`py -3 _verify\v21_advanced_params_overlap.py`、`py -3 _verify\v21_advparams_space.py`。

### 21.10 我没测到 / 测不了的部分

| 项 | 状态 | 原因 |
|---|---|---|
| `--import-llm` 开启时「模型润色 `reply.text`」这条通道（§10.4） | **未测** | 契约说它**只润色措辞、不改 JSON/路由**，失败必须回落。默认关闭；我没有既开 `--import-llm` 又架 stub 网关去驱动聊天润色路径 |
| 真实键盘输入 / 中文 IME 在聊天框里的表现 | **未测** | 我用的是 API 驱动 + JS 点击芯片；没有模拟逐键输入与 IME 组合态 |
| `?demo=<key>` 与新聊天 UI 的共存 | **未测** | lead 提示该参数会自动跑一遍 SSE；为避免状态混淆我全程用不带 `demo` 的 URL。**这也意味着「带 demo 时聊天窗是否仍然正确」没被覆盖** |
| 06 导入场景里「我贴一份 →」分支的前端联动（`defer_to_import`） | **未测** | 我在 API 侧读到了该分支（`state.answers.defer_to_import`），但没有在 UI 里点那颗芯片走完整链路 |
| 旧 45 字段表单的**逐字段**功能 | **部分** | 只验证了它默认收起、展开后渲染 45 个字段、打开态无重叠；没有逐字段改值再建 study 比对 |
| 深色主题 / 演示视图 / `1`–`6` 快捷键在聊天 UI 下的表现 | **深色已补测；其余未测** | 深色主题已由 web-shell 补测、并经我双判据复核（§21.14）；**演示视图 / 快捷键仍未测** |
| 移动端/窄屏布局 | **已补测（含 ≤680px）** | 原只在 1344×896 测；现由 `viewport_matrix.py` 覆盖 light/dark × 1344/900/**680** × 两态 = 10/10，见 §21.15。真机移动端未测 |

### 21.11 我最担心的一处风险

> **2026-11-21 01:55 追记**：下面这条风险在**我准备复验的期间**已被 task-17 处理（见 §21.12），
> 因此它现在是「已缓解 + 留有独立检查器」的状态。原文保留，便于对照当时的判断。

**`plan` 的展示层文案与算法库之间没有机器可校验的绑定 —— 这次只差一步就真的说错了。**

§10.3 要求 `label`/`plain`/`impact` 是「展示层增补」并标明来源（这点实现**做到了**，
`display_source` 字段齐全，我逐条验过回落分支）。但 `PARAM_DISPLAY` 是**按参数名**索引的人工表，
而实测 30 个参数名跨算法复用、5 个语义签名不同。这次的 `acquisition` 文案已经列举了 `mfbo`
**不存在的 `pi`/`ts` 选项**（因为控件只渲染 registry 的 choices，用户点不到，所以没造成事故）。

我担心的不是这一条，而是**这类文案没有任何自动化检查**：`test_kickoff.py` 只断言结构与
`display_source`，不会、也无法判断「人工文案里描述的范围/选项是否属于该算法」。只要以后有人
在 `PARAM_DISPLAY` 里写一句带数字或选项名的说明（例如「默认 1024」「支持 pi」），它就会对
部分算法**静默说错**，而所有门禁仍然全绿。建议二选一：(a) `PARAM_DISPLAY` 按
`(algorithm, name)` 索引；(b) 加一条测试，禁止人工文案里出现数字或 `choices` 字面量
（把数字/选项交给 registry 渲染，文案只做定性描述）。我倾向 (b)，它同时把 F9 一起解决。


### 21.12 复验追记：F9 已修（task-17），我的独立检查器 5/5

写 §21 时 F9 是未修状态；**在我准备复验的这段时间里 task-17 已落地**。我没有采信「已修」的说法，
而是自己写了一个**独立于实现者的对照检查器** `_verify/v21_f9_param_copy.py` 去验，并先确认
「检测器真的会抓到谎言」（负对照），再跑真实数据。

**修复形态（我读代码 + 跑 API 双向确认）**：
```
bridge._param_display 签名 = (algorithm: str, param: Mapping) -> dict      ← 改动 (a)
PARAM_DISPLAY 键：tuple(algorithm,name)=6 个，name-only=31 个            ← 保留 name 兜底
tuple 键样例 = [('bayes','acquisition'), ('constrained','acquisition'), ('cqehvi','acquisition'),
               ('cqnehvi','acquisition'), ('qehvi','acquisition'), ('qnehvi','acquisition')]

mfbo.acquisition  → plain="ei/logei 平衡探索与利用；ucb 更偏探索。其余取值以控件列出的为准。"
bayes.acquisition → plain="ei/logei 平衡探索与利用；ucb 更偏探索；pi 更偏利用；ts 用后验采样。"
```
即：`bayes` 这类**合法拥有** pi/ts 的算法保留精确文案；`mfbo` 这类不拥有的走**不复述选项**的兜底文案。

**我的独立检查器结果**：
```
py -3 _verify\v21_f9_param_copy.py
扫描 18 个算法 / 196 个参数条目
[PASS] C1 文案里的 choice 字面量都合法（对本算法）  违规 0 处：无
[PASS] C2 文案里的「默认 X」都等于 registry default  违规 0 处：无
[PASS] 自检（负对照）：检测器对『提到非法 choice』的文案会 fire、对合法文案与子串不 fire
       旧版 mfbo.acquisition 文案（提到 pi/ts）: fired=True（期望 True）；
       合法文案（只提本算法有的）: fired=False（期望 False）；
       token 边界：its 里的 ts 不算: fired=False（期望 False）
[PASS] 修复形态：PARAM_DISPLAY 已有 (algorithm, name) 键；name 兜底文案不再列举算法特有选项
       tuple 键 6 个；兜底 acquisition 文案='ei/logei 平衡探索与利用；ucb 更偏探索。其余取值以控件列出的为准。'
== F9 展示文案是否对该算法说谎（独立对照实现）: 5/5 PASS ==
```
> **关键点：第 3 条是「检测器自检」**。我第一版把「应当报出 mfbo.acquisition」写成断言 ——
> 修完之后那条**必然**变成 FAIL，而 FAIL 的原因是「没有 bug 可报」，不是检测器失灵。
> 这种断言在修复后就失去意义，所以我改成**不依赖实现状态的负对照**：
> 用**旧版的文本样本**喂给检测器，要求它 fire；用合法文案与 `its`（含 `ts` 子串）要求它不 fire。
> 这样「检测器会不会抓错」这件事**永远可验证**，与实现是否还有 bug 无关。

**我覆盖的口径说明**（与 lead 的裁定 (b) 对齐并做了一处自己的取舍）：
- 我**不禁数字**（同意 lead 的细化）：`「噪声大就调大（0.01–0.1）」` 是工作台**建议区间**，
  不是对库的断言，禁它会误伤有用信息。
- 我只校验两类**可证伪断言**：文案里出现的 **choice 字面量**必须属于本算法的合法 choices；
  出现 **「默认 X」** 时 X 必须等于本算法的 registry default。
- 字面量匹配用 `(?<![0-9A-Za-z_])literal(?![0-9A-Za-z_])` 做 token 边界判断，
  避免 `ts` 命中 `its`（自检里专门有这一条）。

**仍未做的对照**（等 lead 的 task-17 测试与负对照证据、task-18）：
- 与 api-backend 的 `test_param_copy_never_lies` 做**互相对照**（两条独立实现应给出一致结论）；
- lead 要求的「故意写错 choice → 测试必须红」的负对照证据，我会看它的实现是否也能 fire（我这份已自证能 fire）；
- F10 两态归零；
- lead 新版 `geom_probe.py` 与我的 `v21_geom_states.py` 跑同一批状态、结论一致。


### 21.13 第二轮复验（F9 / F10 修复后 + 新版探针交叉对照，2026-11-21 02:20）

写 §21 之后，**F9（task-17）与 F10（task-18）的修复都已落地**，lead 也重写了 `geom_probe.py`。
我按约定的复验范围全部重跑，并在过程中发现**我自己脚本的一个判据 bug**（见下），一并修好。

**新基线哈希**（`logs/revision_hashes_task16_round2.txt`，与 §21 开头旧基线对照）：

| 文件 | 旧基线 | 新基线 | 说明 |
|---|---|---|---|
| `bridge.py` | `BEDA3AE34EF2539E` | **`1B8B904925701AEA`** | task-17（F9） |
| `web/assets/app.css` | `B935FD05325B974D` | **`047B679E7BD689DA`** | task-18（F10） |
| `tests/test_kickoff.py` | `70E77E6945144335` | **`B5F069DAD275DB47`** | +3 项测试（101→**104**） |
| `_verify/geom_probe.py` | `ACFE3E17EC6032D0` | **`632AA0E14856E1B5`** | lead 重写为三重判据 |
| `server.py` / `CONTRACT.md` / `kickoff.js` / `condopt.js` | — | 与旧基线**相同** | 未受影响 |

#### 复验结果总表

| 项 | 命令 | 结果 |
|---|---|---|
| 全量回归 | `py -3 -m pytest` | **104 passed in 37.35s**（旧 101 → 新增 3 项即 F9 守卫） |
| selftest | `py -3 server.py --port 0 --selftest` | **9/9**，exit 0 |
| §10 API 侧 | `py -3 _verify\v21_kickoff.py` | **44/44 PASS**（bridge 改动未破坏任何一条） |
| §10.5.5 前端 | `py -3 _verify\v21_frontend_kickoff.py` | **10/10 PASS**（真点「建 study」仍建出 run） |
| 既有语义 | `py -3 _verify\v21_regression_semantics.py` | **3/3 PASS** |
| F9 独立检查器 | `py -3 _verify\v21_f9_param_copy.py` | **5/5 PASS** |
| F9 负对照 | `py -3 _verify\v21_f9_negative_control.py` | **5/6**（发现守卫一处缺口，见下） |
| F10 两态 | `v21_advparams_space.py` / `v21_advanced_params_overlap.py` | **3/3** / **4/4 PASS** |
| 几何交叉对照 | `py -3 _verify\v21_crosscheck.py` | **7/7 PASS** |
| 四状态几何 | `py -3 _verify\v21_geom_states.py` | **12/12 PASS** |

#### F10：确认修好（两态归零 + 无副作用）

```
as-is (closed)   det_h=36  body_h=0  body_display=none  build_y=1657  scrollHeight=1647
body display:none det_h=36  body_h=0  body_display=none  build_y=1657  scrollHeight=1647
```
收起态 body 现在 `display:none`、高度 **0**（修复前是 678px 幻影矩形）；开/关切换后 build 位置复原。
加固已与旧抽屉**对称**（`app.css:L1383`）。

**web-shell 的关键论断我自己验了**（§21.13 的 B 组）：他们把参数抽屉做成**纯 CSS** `display:none`
而非 JS 设 `hidden`，为的是收起态仍能被 `readParams()` 读到值。我实测：
```
展开抽屉 → 把 n_candidates 改成 777 → 收起抽屉 → 点「建 study」
→ 新 run=run-20261121-0219，config.params.n_candidates = 777  ✅
```
即**收起不影响取值**，这个做法没有副作用。

**连带效果**：F10 修好后，我四状态里的 raw 相交对从 **9 个降到 3 个**，且剩下的 3 个全是同一个
幻影（滚出 `.condopt-chat-log` 的聊天气泡）。

#### 几何判据交叉对照（lead 的判据 × 我的状态覆盖）

我把自己驱动的**四种状态**接上 **lead 新版 `geom_probe.py` 的判定代码**（复用它的
`MEASURE_JS % SELECTORS`，不是我重写一份），与我的判据逐态比对：

```
S1 首屏     我 0/0 | lead 可见块=13 零尺寸=0 原始相交=0 真实=0 幻影=0
S2 选项态   我 0/0 | lead 可见块=28 零尺寸=0 原始相交=0 真实=0 幻影=0
S3 参数页   我 0/0 | lead 可见块=40 零尺寸=0 原始相交=0 真实=0 幻影=0
S4 抽屉打开态 我 0/0 | lead 可见块=56 零尺寸=0 原始相交=0 真实=0 幻影=0
== 交叉对照: 7/7 PASS ==
```
**四种状态两套判据逐态一致**，没有出现「一方报问题、另一方漏掉」。lead 新版把 paint-skip 与
裁切过滤做进了页面侧 JS，所以幻影对**根本没进相交列表**（原始相交=0）；我的实现是在 Python 侧
过滤，结论相同。**这条对照才算把「lead 的硬 gate」与我的覆盖合上了。**

#### F9：修复确认 + **我发现守卫仍有一处缺口**（负对照抓出来的）

修复形态（读码 + API 双向确认）：`_param_display(algorithm, param)`；`PARAM_DISPLAY` 现有
**6 个 `(algorithm, name)` 精确键 + 31 个 name 兜底键**；`bayes` 保留 pi/ts 精确文案，
`mfbo` 走不复述选项的兜底文案。我的独立检查器 C1/C2 **全绿**（18 算法 / 196 参数条目，违规 0 处）。

**负对照（我独立做的，不靠 api-backend 的自述）**：在内存里篡改 `bridge.PARAM_DISPLAY` 后
**直接调用他们自己的守卫函数** `test_param_copy_never_lies`，看它是否红：

| 篡改 | 期望 | 实际 |
|---|---|---|
| 未篡改（对照 0） | 绿 | ✅ 绿（证明守卫不是「永远红」） |
| `("mfbo","acquisition")` 文案写「pi 更偏利用」 | 红且点名 | ✅ **红**，信息：`PARAM_DISPLAY[('mfbo','acquisition')].plain 提到 'pi'，但 mfbo 的 acquisition choices 是 ['ei','logei','ucb']` |
| **通用** `acquisition` 文案写 pi（走兜底键路径） | 红 | ✅ **红**（同时点名 mfbo 与 mfparego） |
| `("bayes","n_candidates")` 写「默认 **4096**」（registry 默认 1024） | 红 | ❌ **绿 —— 没抓住** |
| 建议区间「0.01–0.1」（防过严的反向对照） | 绿 | ✅ 绿（未被误杀） |
| 恢复原数据后 | 绿 | ✅ 绿（无副作用残留） |

**缺口的根因**（`tests/test_kickoff.py:668-680`）：
```python
for claimed in re.findall(r"默认[值是为：:]*\s*([A-Za-z0-9_.+-]+)", text):
    ...
    known = {str(actual)} | {str(item) for item in choices}
    if claimed in known and claimed != str(actual):      # ← 关键
        problems.append(...)
```
`claimed in known` 这个前置条件意味着：**只有当「文案里说的值恰好是某个已知值（默认或某个 choice）、
但又不等默认」时才会报**。若文案写的是一个**任意而错误的数字**（4096），它不在 `known` 里，
于是被**静默忽略**。这与该测试的 docstring（「出现『默认 X』时 X 必须等于该算法的 registry default」）
不一致 —— **守卫宣称的比它实际执行的更严**。

> 严重度：**低**（测试/守卫缺口，不是产品缺陷；当前文案恰好都说真话）。
> 但它是「门禁给了虚假的安心」这类问题：F9 的风险本就是「文案静默说错」，
> 而这条缺口正是同一个洞的另一半。我自己的检查器 `v21_f9_param_copy.py` 的 C2 用了
> `str` 比较 + `float` 兜底，能抓住 4096 这一例（这也是两边口径的差异，建议对齐）。
> **建议**：去掉 `claimed in known` 前置（改为「不等于 default 即报」，并对数字做数值规范化，
> 避免 `1e-6` vs `1e-06` 误报），或至少同步 docstring 与实现。

#### 我修掉的**自己脚本**的判据 bug（如实记录）

F10 修好后的第一次重跑，我的 `v21_geom_states.py` 报了 **7 个零尺寸**（`.condopt-param[5..11]`）。
排查后确认这是**我的探针 bug，不是产品缺陷**：那些行自身是 `display:flex`，但其祖先
`.condopt-advanced-params-body` 现在是 `display:none`，`getBoundingClientRect()` 返回 **0×0**；
我的页面侧过滤只查了「自身 display / `[hidden]` 祖先」，**没查祖先的 `display:none`**，
于是把「未渲染」误判成「被压扁的容器」。而 lead 的新探针有 `paintSkipped`，所以没这个假阳性。

修法（`v21_geom_states.py`）：加一条 `if (el.getClientRects().length === 0) return;`
—— 无 client rect = 未渲染，一条覆盖自身/祖先 `display:none` 与收起 `<details>` 内容。
修后四状态 **12/12 PASS**。
**这条与 §21.2 里那个「裁切感知反而消掉真实重叠」是同一类错误的两个方向**：
矩形几何单独看，既会把幻影当真、也会把真渲染当零尺寸；必须叠加「是否绘制」的判据。


### 21.14 第三轮复验（F11 守卫 + 主题/视口矩阵，2026-11-21 03:02）

#### F11：api-backend 的守卫修复，我用**他自己抽出的函数**压了 16 条

他把守卫抽成 `tests/test_kickoff.py::_param_copy_lies(display=None)`，我据此在内存里篡改
`PARAM_DISPLAY` 直接压，不必改源码（比上一轮「调用测试函数再捕 AssertionError」更干净）。

```
py -3 _verify/v21_f11_guard.py
== F11 复验：文案守卫（_param_copy_lies）: 16/16 PASS ==
```

| 类别 | 用例 | 期望 | 实测 |
|---|---|---|---|
| 真实表 | 未篡改 | 0 条 | ✅ 0 条 |
| **没作弊** | 同键写「默认 4096」→报；写「默认 1024」→不报 | 随表变化 | ✅ 1 条 / 0 条 |
| 该红 | mfbo 精确键写 `pi` | 红 | ✅ 2 条，点名 `('mfbo','acquisition')` 与合法 choices |
| 该红 | 通用（name 兜底）键写 `pi` | 红 | ✅ 8 条（点名 mfbo、mfparego…） |
| 该红 | **「默认 4096」（registry 1024）← F11 本体** | 红 | ✅ 1 条（**上一轮此处是漏报**） |
| 该红 | 「默认 0.000001」（mfbo alpha 实际 0.0001） | 红 | ✅ 1 条 |
| 该红 | 陈旧条目（键非任何算法参数） | 红 | ✅ 1 条 |
| 该绿 | 建议区间「0.01–0.1」 | 绿 | ✅ 未被误伤 |
| 该绿 | 中文句「默认即均匀分布」 | 绿 | ✅ 未被误伤 |
| 该绿 | 「默认 1e-6」/「1E-06」/「0.000001」（registry 1e-06） | 绿 | ✅ 三例均判等价 |
| 规范化 | `_same_value` 等价 8 组 / 非等价 4 组 | 两个方向都对 | ✅ 无异常 |

**结论：F11 已修，且没有被放宽**——`claimed in known` 前置已去除（`grep` 确认该行不存在），
`_same_value` 的规范化不是「恒真」（`4096` vs `1024`、`ucb` vs `ei`、`true` vs `False` 都判不等）。
`bridge.py` 全程未变（`1B8B904925701AEA`，与上一轮基线一致）→ **没有为了过测试而改文案**。

> **事实核对（一处与 api-backend 陈述不符）**：他说「工作树 clean」，实际
> `git status --porcelain` 有 **2 个已修改未提交文件**：`_verify/geom_probe.py`、`web/ADAPTATION.md`
> （HEAD 确为 `9ea3cd6`、`bridge.py` 未变这两条属实）。不影响我的结论（我全部按**工作树现状**验证），
> 但「clean」这个说法不准确，且提示 `geom_probe.py` 的工作树版本与提交版本不同，
> 后续任何「按提交验收」都要指明用哪个版本。

#### 主题 × 视口 × 状态矩阵（独立复核 web-shell 的补测）

web-shell 用同一套三类判据补测了深色主题与窄屏，请我用同一套判据复核。我**同时跑两套判据**
（lead 新版探针的页面侧三重 + 我自己的 Python 侧判据），**4 配置 × 4 状态 = 16 格**：

```
py -3 _verify/v21_geom_matrix.py
== 几何矩阵：主题 × 视口 × 状态（独立复核 web-shell 的补测）: 16/16 PASS ==
```
每格均 `lead: 零尺寸=0 相交=0 真实=0 | 我: 零尺寸=0 可见重叠=0`：
```
light@1344x896 / dark@1344x896 / light@900x800(866x703) / dark@900x800(866x703)
  × {首屏(块13), 选项态(28), 参数页(40), 参数页+两抽屉开(56)}
```
与 web-shell 的结论**一致**。截图：`_verify/shots/matrix_{light,dark}_900x800_param.png`。

> 第一次跑时最后一格（dark@900x800·参数页+两抽屉开）出现 CDP `Runtime.evaluate` 超时。
> 我加了重试后**一次通过且未触发任何重试** → 确认是客户端抖动、不是页面卡死
> （否则会是可复现的产品问题，我不会当抖动放过）。

#### 顺带核实 web-shell 的「窄屏」说法是否名副其实

「窗口调小」不等于「窄屏布局生效」——如果媒体查询没命中，那份窄屏矩阵测的还是桌面布局。我做了对照：
```
py -3 _verify/v21_narrow_layout.py   → 4/4 PASS
desktop 1344x896: inner=1310x799 mq1024=False shell=grid cols="230px 1080px" 可见导航链接=4
narrow   900x800: inner= 866x703 mq1024=True  shell=block cols="205px minmax(0,1fr)" 可见导航链接=1
窄屏横向溢出: scrollWidth 856 == clientWidth 856（无溢出）
```
所以 `@media (max-width:1024px)` **确实命中**、布局**确实变了**（grid→block、4→1）。
一处措辞校准：他们说的「顶栏导航隐藏」实测是**收起为 1 个条目**（topbar 本身仍 `display:grid`，
sidebar 仍 `display:flex`），方向一致、程度略强于实测。**不构成缺陷**，仅记录以免后续引用失真。

#### 仍存在的 3 个「聊天气泡幻影」——回答 web-shell 的提问

他问：那 3 个 `.condopt-kickoff-head × .condopt-bubble[6]` 幻影在新探针下是否还在。
**答案：我的脚本仍会列出这 3 对，但只在「原始口径」下列出**，且我的决定性判据判定它们是幻影
（`elementsFromPoint` 堆叠栈里没有那个气泡：`a=True b=False 栈深=8`），因此**不影响 A2 的 PASS**。
而 lead 新探针在页面侧就把裁切过滤做掉了，所以它的输出是 `原始相交=0`——
**两套实现结论一致（0 真实重叠），只是「是否把幻影列出来」的呈现口径不同**。
我保留列出是为了留痕：能看见「被过滤掉了什么」，比直接不显示更可审计。


### 21.15 视口矩阵扩到 ≤680px（`viewport_matrix.py`）+ 一处不可复现抖动 + 一个「失败要响」的正面例子

> **⚠️ 本小节有两处已被后续证据更正，先读这里再读正文（2026-11-21 03:35）**
>
> **更正 1（我的描述过时）**：我读到的 `viewport_matrix.py` 是 **65 行、02:52 的版本**，只有
> 4 列（零尺寸/原始相交/真实重叠/exit）。**冻结版本（`319e8fc`，60 行）已经多了两列**：
> `OVERFLOW_RE` 与 `BREAKPOINT_RE`，即**断点命中（`mq1024`/`mq680`）与横向溢出（`scrollWidth - clientWidth`）
> 都是实测的**。web-shell 与 lead 据此指出我错，**他们是对的**：这两项确实被量了。
> 我当时的说法只对「我自己那套只解析三个计数的矩阵」成立，**不能推广成「那一轮没测断点与溢出」**——
> 这是两种完全不同的结论，我已在 §21.16 用实测复现 `light 680,800 → mq680=True、溢出 0px`。
>
> **更正 2（我的「自我更正」本身是错的）**：正文第 3 点我写了「核对是不是端口/profile 撞车 → **不是**」，
> 并据此把那次失败定性为「不可复现偶发」。lead 随后复现并定位到**真实根因就是调试端口竞态**
> （固定 `--remote-debugging-port` 连跑时上一轮 Edge 未释放端口），并在 `9a44b54` 修掉。
> **我的原始假设（端口撞车）方向是对的，我的「否认」才是错的**——我是在**已经修好的代码上**核对，
> 于是看到 `_free_port()` 就断定机制不可能成立，忽略了「抖动发生在修复之前」这一时间关系。
> 教训：**用「当前代码长什么样」去否定「历史上发生过的失败机制」是无效推理**，必须对齐时间线。

我在 `_verify/` 下发现一个不是我写的脚本 `_verify/viewport_matrix.py`（我读到的版本 65 行，02:52），
是个 **runner**：调 `geom_probe.py` 跑 5 配置 × 2 状态，用正则从探针 stdout 里抠出
「零尺寸 / 原始相交 / 真实重叠 / 退出码」聚合成表，任一格非零即非零退出。
它**多覆盖了一个我一直列为未测的区间：`light @680,800`（≤680px 单列断点）**。
我做两件事：审它可靠不可靠，然后跑它。

#### 结果：10/10（含 ≤680px，缺口关闭）

```
py -3 _verify\viewport_matrix.py
主题     视口         状态        零尺寸  原始相交  真实重叠  exit
light  1344,896     默认态          0        0        0     0
light  1344,896     抽屉全开         0        0        0     0
dark   1344,896     默认态          0        0        0     0
dark   1344,896     抽屉全开         0        0        0     0
light  900,800      默认态          0        0        0     0
light  900,800      抽屉全开         0        0        0     0
dark   900,800      默认态          0        0        0     0
dark   900,800      抽屉全开         0        0        0     0
light  680,800      默认态          0        0        0     0
light  680,800      抽屉全开         0        0        0     0
合计 10/10 格通过（零尺寸 0 且真实重叠 0 且 exit 0）      EXIT=0
```
**`light @680,800` 两态均 0/0** → §21.10 里「≤680px 单列区间未测」这一条**可以划掉**
（判据与我/lead 是同一套三重判据；探针自带的 `--theme`/`--window-size` 参数我核对过确实注册了）。
至此已知未覆盖项只剩：`--import-llm` 润色通道、中文 IME、`?demo=` 与新聊天 UI 共存、
`defer_to_import` 前端联动、旧表单逐字段、演示视图/快捷键、**真机移动端**（桌面浏览器缩窄≠真机）。

#### 一处**不可复现**的抖动（不是产品缺陷，我不当缺陷报）

第一次跑 10 格里 **`dark 1344,896 抽屉全开` 一格 `exit=1` / 解析失败**。我没有直接下结论，而是分三步查：

1. 该格**单独连跑 3 次：3/3 全 `exit=0`、0/0/0**；
2. 空闲时**整表重跑：10/10 全绿**；
3. 核对「是不是端口/profile 撞车」→ **不是**：lead 已把探针做成并发安全
   （`_free_port()` 每次取空闲端口、profile 用 `condopt-geom-<pid>-<ms>` 独立临时目录、用完 `shutil.rmtree`）。

**结论：不可复现的偶发（疑似 Edge 启动/机器负载），非产品缺陷**，且**它偏向安全侧**
（失败→非零退出，不会伪装成通过）。我只在报告里留痕，不计入缺陷。

#### 顺带：这个 runner 是「失败要响」的**正面例子**，值得对照 F11

它从 stdout 用正则抠数字，这是最容易变成「正则没匹配 → 默认 0 → 一片绿」的地方。我专门审了这条路径：

```python
zero = RAW_RE.search(out); z = ZERO_RE.search(out)
if not zero or not z:
    return -1, -1, -1, proc.returncode      # ← 解析失败 → -1
...
ok = (zero == 0 and real == 0 and code == 0) # ← -1 == 0 为 False → 判非零、exit 2
```
**解析失败会返回 `-1` 而不是 `0`**，于是必然被判为非零并打出 `<== 非零`、整体 `exit 2` ——
**不会出现「什么都没解析到却报全绿」**。第一次那个 `-1` 正是这条路径救了场：
如果它写成 `int(m.group(1)) if m else 0`，那 10 格里就会有一格**假的绿**，而我会把它当成通过。
这与 F11 的教训（门禁宣称的比执行的严 / 空解析给虚假安心）恰好是一正一反两个例子。


---

### 21.16 按提交版本的冻结基线（清洁克隆，`main = 319e8fc`）

> **⚠️ 本节的冻结点已被 lead 更正为 `ec73626`（更新的提交，仅改 `_verify` 清理逻辑，产品代码零改动）。**
> `319e8fc` 的这轮证据仍然有效并保留在下；**最终冻结结论见 §21.17（按 `ec73626` 复跑 + 新增残留判据）**。

**结论：PASS —— `319e8fc559acaf3830cdd734a78000a74161b06e`（短哈希 `319e8fc`）。**
全部检查在**清洁克隆**里跑（`git clone` 只含已提交内容，天然排除工作树污染），并顺带验证
「新克隆能不能直接跑」这个发布口径。

```
git clone D:\Dev\condopt-workbench D:\Dev\_freeze_check_verifier
git rev-parse HEAD   → 319e8fc559acaf3830cdd734a78000a74161b06e      ✅ 与 lead 指定的冻结提交一致
git status --porcelain → 空（clean）                                  ✅
克隆内 runs/ 、state/ → 均不存在（符合预期；脚本各自用 common.fresh() 建自己的临时 run 根）
克隆文件总数 = 148；server.py / bridge.py / CONTRACT.md / VERIFICATION.md / web/index.html /
  _verify/{common.py,viewport_matrix.py,v21_kickoff.py,v21_frontend_kickoff.py,v21_f11_guard.py,v21_narrow_layout.py} 均在
```

| # | 检查 | 命令 | 期望 | 实得 |
|---|---|---|---|---|
| 1 | 全量回归 | `py -3 -m pytest -o addopts="" -q` | 104 passed | **104 passed in 28.40s**，exit 0 ✅ |
| 2 | 起服务自检 | `py -3 server.py --port 0 --selftest` | 9/9 | **9/9 项通过**，exit 0 ✅ |
| 3 | 视口×主题矩阵 | `py -3 _verify/viewport_matrix.py` | 10/10 | **10/10**，exit 0 ✅ |
| 4 | **runner 解析自检（负对照）** | `py -3 _verify/viewport_matrix.py --self-test` | 6/6 | **6/6 全部通过**，exit 0 ✅ |
| 5 | 矩阵（**严格按提交版本**，指向克隆自带 server） | `py -3 _verify/_freeze_matrix.py --url http://127.0.0.1:8788/` | 10/10 | **10/10**，exit 0 ✅ |
| 6 | §10 对话式开工（API 侧） | `py -3 _verify/v21_kickoff.py` | 44/44 | **44/44**，exit 0 ✅ |
| 7 | §10.5.5 前端 + 真点「建 study」 | `py -3 _verify/v21_frontend_kickoff.py` | 10/10 | **10/10**，exit 0 ✅ |
| 8 | F9/F11 文案守卫 | `py -3 _verify/v21_f11_guard.py` | 16/16 | **16/16**，exit 0 ✅ |
| 9 | 窄屏断点是否名副其实 | `py -3 _verify/v21_narrow_layout.py` | 4/4 | **4/4**，exit 0 ✅ |

**1) pytest（原文尾）**
```
tests/test_kickoff.py: 38   tests/test_workbench.py: 31   tests/test_workbench_import.py: 35
104 passed in 28.40s
```

**3) 提交版 `viewport_matrix.py`（10/10，含断点/溢出列）**
```
light  1344,896   默认态    0  0  0  exit=0  溢出 0  False/False
dark   1344,896   抽屉全开   0  0  0  exit=0  溢出 0  False/False
light  900,800    默认态    0  0  0  exit=0  溢出 0  True/False
dark   900,800    抽屉全开   0  0  0  exit=0  溢出 0  True/False
light  680,800    默认态    0  0  0  exit=0  溢出 0  True/True    ← ≤680 断点确实命中
light  680,800    抽屉全开   0  0  0  exit=0  溢出 0  True/True
合计 10/10 格通过（解析成功 且 零尺寸 0 且真实重叠 0 且 横向溢出 ≤1px 且 exit 0）
```

**4) runner 解析自检 6/6（`--self-test`，原文）**
```
[OK ] 正常样本：实得 ok=True，期望 True
[OK ] 切削掉的样本（正则不该匹配到任何东西）：实得 ok=False，期望 False
[OK ] 缺横向溢出行的样本：实得 ok=False，期望 False
[OK ] 真有横向溢出的样本：实得 ok=False，期望 False
[OK ] 数值全 0 但进程非零退出：实得 ok=False，期望 False
[OK ] 放宽数值比较后仍能被拦：放宽后数值检查=通过(危险)，parsed=False 使其 ok=False → 已拦住
自检：全部通过
```
> 第 6 条正是我提炼那条纪律（「解析失败必须返回一个不可能通过的值」）的**加强版**：
> 它不只靠哨兵 `-1`，而是用 `Row(parsed=…)/FAILED` **先要求解析成功**，再逐条判数值
> （`overflow` 另外要求 `>= 0`）。web-shell 指出「`overflow <= 1` 对哨兵 `-1` 成立、当时只靠
> `zero==0` 先失败兜住」——这条已被 lead 修掉（`319e8fc`），并用上面第 6 条自检显式证明
> 「**即使有人放宽数值比较，`parsed=False` 仍能拦住**」。**这是负对照做得对的样子**：
> 不只测「该红的红」，还测「放宽之后仍然拦得住」。

**5) 为什么还要跑第 5 项**：提交版 `viewport_matrix.py` 不传 `--url`，探针默认打
`http://127.0.0.1:8770/`，而 8770 上是 lead 的**工作树**进程（PID 77804）——那不是提交版本。
为了给出**严格按提交版本**的几何结论，我另用 `_verify/_freeze_matrix.py`（同配置/同正则/同判据，
仅多传 `--url`；**未改动任何已提交文件**）指向**克隆自己起的 server**（8788）重跑：
```
url=http://127.0.0.1:8788/
light  1344,896  默认态  0 0 0 exit=0  mq1024=False mq680=False 溢出 0
light  900,800   默认态  0 0 0 exit=0  mq1024=True  mq680=False 溢出 0
light  680,800   默认态  0 0 0 exit=0  mq1024=True  mq680=True  溢出 0   ← 与第 3 项逐格一致
light  680,800   抽屉全开  0 0 0 exit=0  mq1024=True  mq680=True  溢出 0
合计 10/10 格通过
```
即：**提交版本的几何结论与「打 8770 工作树」时逐格相同**，且 ≤680 断点命中与零横向溢出
在新克隆里**独立复现**。

**发布口径（顺带验到）**：克隆内既无 `runs/` 也无 `state/`，`v21_kickoff.py` / `v21_frontend_kickoff.py`
仍能在克隆里从零建 run 并跑完（各自用 `common.fresh()` 建临时 run 根），说明**新克隆开箱可跑**。
唯一外部依赖是 condopt 算法库（从同级检出解析，克隆路径仍在 `D:\Dev\` 下，解析正常）。

**证据**：`D:\Dev\_freeze_check_verifier\_verify\logs\{freeze_vm.txt, freeze_vm_selftest.txt,
freeze_matrix_bycommit.txt, freeze_v21_kickoff.txt, freeze_v21_frontend_kickoff.txt,
freeze_v21_f11_guard.txt, freeze_v21_narrow_layout.txt}`。

**残留**：临时克隆与 8788 服务仅用于本轮，验证完即停止；`_freeze_matrix.py` 是**新增的验证脚本**，
只存在于那个一次性克隆里，**未进入仓库、未改动任何已提交文件**。


---

### 21.17 冻结点更正为 `ec73626`（含临时 profile 残留判据）

**结论：PASS — `ec73626507d2c0599d5f27a8eb162c00fa7d51d7`（短哈希 `ec73626`）。**

lead 在 `319e8fc` 之后又提交了一个 `ec73626`（改 `_verify/geom_probe.py` 的**临时 profile 清理逻辑**，
产品代码零改动）。按本轮一直执行的口径「**已验收的提交 ≠ 声明冻结的提交**」，我按新冻结点重跑。
为避免「同一个克隆里换 commit」造成残留干扰，我做的是**全新克隆**（比 `checkout` 更强：连工作树都不可能是旧的）：
```
git clone D:\Dev\condopt-workbench D:\Dev\_freeze_check_verifier
git rev-parse HEAD     → ec73626507d2c0599d5f27a8eb162c00fa7d51d7   ✅ 与声明的冻结点一致
git status --porcelain → 空（clean）                                 ✅     文件总数 148
克隆内 runs/ 、state/   → 均不存在（脚本各自 common.fresh() 建临时 run 根）✅
```

| # | 检查 | 命令 | 期望 | 实得 |
|---|---|---|---|---|
| 1 | 全量回归 | `py -3 -m pytest -o addopts="" -q` | 104 passed | **104 passed in 24.89s** ✅ |
| 2 | 起服务自检 | `py -3 server.py --port 0 --selftest` | 9/9 | **9/9 项通过** ✅ |
| 3 | 视口×主题矩阵 | `py -3 _verify/viewport_matrix.py` | 10/10 | **10/10** ✅ |
| 4 | runner 解析自检 | `py -3 _verify/viewport_matrix.py --self-test` | 6/6 | **自检：全部通过** ✅ |
| 5 | **临时 profile 残留（本轮修复的验收判据）** | 见下 | 新增 == 0 | **0 → 0（三次递增长 settle 均为 0）** ✅ |
| 6 | 矩阵（**我的独立 runner**，指向克隆自带 server） | `py -3 _verify/_freeze_matrix.py --url …:8788/` | 10/10 | **10/10** ✅ |
| 7 | 四个关键脚本（**均为我自己的判据**） | `v21_kickoff` / `v21_frontend_kickoff` / `v21_f11_guard` / `v21_narrow_layout` | 44/44・10/10・16/16・4/4 | **44/44・10/10・16/16・4/4** ✅ |

**第 3 项原文（注意已含「横向溢出」与「断点 1024/680」两列——我先前说这两项没量是错的，见 §21.15 更正块）**
```
主题     视口         状态            零尺寸     原始相交     真实重叠     横向溢出  exit  断点1024/680
light  1344,896   默认态             0        0        0        0     0  False/False
dark   1344,896   抽屉全开            0        0        0        0     0  False/False
light  900,800    默认态             0        0        0        0     0  True/False
dark   900,800    抽屉全开            0        0        0        0     0  True/False
light  680,800    默认态             0        0        0        0     0  True/True     ← ≤680 断点确实命中
light  680,800    抽屉全开            0        0        0        0     0  True/True
合计 10/10 格通过（解析成功 且 零尺寸 0 且真实重叠 0 且 横向溢出 ≤1px 且 exit 0）
```

#### 第 5 项：残留判据我**独立量**，并且刻意做成了能抓「删了又被写回」

lead 的修复成本身要求「跑完新增残留数 == 0」而不是「没报错」。这条判据的陷阱在于：
**Edge 子进程可能在主进程被杀之后继续把 profile 写回来**，于是「删完立刻检查一次」会误判成功。
所以我没有只数一次，而是**多次计数 + 递增长 settle 窗口**：
```
跑前 #1 = 0       跑前 #2 = 0   （先确认起点干净）
… 整张矩阵 10 格 …
跑后 #1 (+3s)  = 0
跑后 #2 (+7s)  = 0
跑后 #3 (+13s) = 0        ← 若存在「写回」竞态，这几次之间会跳变；这里全程 0
```
**判据成立：起点 0、跑完整张矩阵后（跨 13 秒 settle）新增残留 == 0。**
只测一次与测三次的差别就在这：前者只能证明「那一刻不存在」，后者能证明「没有被写回来」。

#### 顺带：同一把尺子量到我自己的脚本在漏（我的问题，已如实记录并清理）

我用同一套计数也量了自己脚本创建的 profile（前缀 `.condopt-*`，在 `$HOME` 下），结果：
```
冻结复跑前：0（我先清理了历史遗留 14 个）
冻结复跑后：3  → .condopt-fe（v21_frontend_kickoff）、.condopt-narrow-1344 / .condopt-narrow-900（v21_narrow_layout）
```
即 **lead 的探针已经不漏（TEMP 0→0），但我自己的 5 个浏览器驱动脚本仍在漏**——
`v21_*` 系列把 profile 建在 `Path.home()/.condopt-<name>`，既不删也不重试。
这些是纯一次性浏览器 profile（非产品物），我已清理归零。**这属于我这边的工具债**，根因与 lead 刚修的完全同族
（一次性资源 + 删除失败被静默吞掉 + 子进程写回），修法也应当同族：改到 `%TEMP%` 下、
`taskkill /T /F` 树杀、删除后要求**连续两次确认不存在**、并把「跑完新增残留 == 0」作为脚本自检的一部分。
我**没有**在本轮改动这些已提交脚本（否则「按提交版本」的证据会被我自己的改动污染）——
若要收口，这应当是下一轮独立提交，并需重新验证。**如实留痕，不静默带过。**

**证据**（一次性克隆 `D:\Dev\_freeze_check_verifier`，按要求保留）：
`_verify\logs\{freeze2_vm.txt, freeze2_myrunner.txt, freeze2_v21_kickoff.txt,
freeze2_v21_frontend_kickoff.txt, freeze2_v21_f11_guard.txt, freeze2_v21_narrow_layout.txt}`。
其 8788 服务已停止；**8770 上 lead 的服务未受影响（仍 HTTP 200）**。


---

## 22. 收尾补齐独立验收（CONTRACT §11）

> 冻结点口径：代码 `ec73626`（PASS）/ 文档 `59742ed`；本轮新增提交见 §22.9。按 §11.5-7，
> 每条结论都注明**提交哈希 + 是否并发 + 是否在某次清扫之后**。

### 22.A §11.5 验收脚本自身的临时资源卫生（task-22 A 段，已完成）

#### 22.A.1 起因：我自己用同一把尺子量到自己在漏

上一轮（§21.17）我用「同一把尺子」量到 **lead 的探针已归零、而我自己的浏览器驱动仍在漏**：
`v21_frontend_kickoff.py`(.condopt-fe)、`v21_narrow_layout.py`(.condopt-narrow-*) 等把一次性
profile 建在 `Path.home()/.condopt-*`，**不删也不重试**。当时我**刻意没有顺手改**这些已提交脚本
（否则「按提交版本」的证据会被我自己的改动污染）。本轮 §11.5 正式排期，作为**独立提交**收口。

#### 22.A.2 改了什么

新增 `_verify/browser_profile.py`（卫生与串行基础设施）+ `_verify/run_browser_suite.py`（串行套件运行器）
+ `_verify/check_driver_entrypoints.py`（§11.5-8 机器检查）；把 **10 个 v21 驱动 + 遗留的 `v08_browser.py`**
全部改为：

| §11.5 要求 | 实现 |
|---|---|
| ① profile 建 `%TEMP%`、名字含 pid+时间戳 | `bp.make_profile(tag)` → `%TEMP%\condopt-verify-<tag>-<pid>-<stamp>` |
| ② 结束 `taskkill /T /F` 杀**整棵进程树** | `bp.kill_tree(proc)`：**在父进程还活着时**调用（`taskkill /T` 才能遍历子树） |
| ③ 删除后**连续两次确认不存在** | `bp.cleanup()`：`rmtree` → settle → 再确认；被写回则重试 |
| ④ 删不掉打 **stderr**，不许 `ignore_errors=True` | `cleanup` 全程不吞异常，失败返回 `False` 并打 stderr |
| ⑤ 脚本自证「跑完新增残留 == 0」（跨 settle **多次**计数） | `bp.guard_residue()`：三次采样取峰值，判「**本进程**新增」 |

另按 §11.5-6 增加**串行锁**（`run_guarded` / `suite_guard`）与 **exit 4 环境/并发类退出码**。

#### 22.A.3 过程中修掉的四个真问题（都不是靠看代码看出来的，是跑出来的）

1. **父进程先死 → 子进程脱管 → profile 删不掉**（最严重）
   原收尾是 `proc.terminate()` → `wait` → `kill`。Edge 会派生 renderer/GPU/utility 等**多个子进程**；
   父进程一死，子进程被 reparent，此时 `taskkill /T /PID <已死的父>` **再也走不到它们**，它们继续
   持有 profile。实测：10 个驱动里 2 个自证残留 != 0，系统里躺着 **61 个孤儿 msedge**，遗留目录
   **689 个条目**。修法：收尾**先 `bp.kill_tree(proc)`（父还活着时树杀）**，再由 `cleanup()`
   **按 profile 反查持有者**（`pids_using`）清掉残余，然后删除 + 连续两次确认。
2. **判据把别人的在途 profile 算成我的**（`api-backend` 并发跑 `v22_oldform_fields.py` 时实测）
   `guard_residue` 原本统计**全局**同前缀目录 → 把对方正在用的 `condopt-verify-oldform-*` 记成我的新增残留。
   修法：判据改 **pid 作用域**（只算含本进程 pid 的目录），全局数只作信息行。
3. **`guard_residue` 不取串行锁且不报警**（`api-backend` 实测证伪了我的假设，这是本轮第 9 个同族问题）
   我原以为「复用我的模块就自动获得串行」，实际**锁是 opt-in**：只有 `run_guarded` 会取锁。
   对方两个驱动用 `guard_residue(main)` 做入口，于是**并发跑却都 exit 0**——契约对存量脚本**假生效**。
   修法：`guard_residue` 在**无串行保护**时**大声警告**（并加自检第 11 条：无锁必须警告、持锁不得刷噪音）；
   所有入口一律改用 `run_guarded`。
4. **我自己把并发纪律破坏了**：我在后台跑 10 驱动套件时又在前台跑了一个驱动 → `Page.enable` 超时。
   这暴露了「驱动级锁只防两个驱动重叠，两个**套件运行器**仍会在驱动间隙交错」。修法：新增
   **套件级锁** `suite_guard`（整个套件期间持锁，套件内部驱动不再互相抢锁）。

#### 22.A.4 §11.5-8 机器检查（带负对照）

`_verify/check_driver_entrypoints.py`：扫描 `_verify/**`，判定「是浏览器驱动 且 未取串行锁」→ 红。

- **负对照 `--self-test`（4/4 通过）**：造 4 个样本文件，断言
  ① 合规驱动（`run_guarded`）不算违规；② **裸入口驱动必须被判违规**；③ **`guard_residue` 入口驱动必须被判违规**（假生效那类）；④ 引用卫生模块但不是驱动 → 不算违规。
- **检查器自身也修了两次**（都是「跑出来」的）：
  * **假阳**：`_migrate_*.py` 把 `--user-data-dir=…` / `start_edge(…)` 写在**替换字符串**里，被当成驱动；
  * **假阴**：只要求 `subprocess.Popen` 会漏掉「自己不起进程、而是调 `start_edge()`」的 **8 个驱动**。
  * 修法：改用 **AST 取真实被调用的函数名**（`Popen` / `start_edge` / `CDP`），两边都能分清。
- **真实扫描结果**：`39 个文件 → 浏览器驱动 14 个，取锁 13 个`；**唯一红项是 `geom_probe.py`**（lead 的文件，非我写作域）→ 已上报。

#### 22.A.5 验收结果

| 检查 | 命令 | 结果 |
|---|---|---|
| 卫生模块自检（含对抗样本） | `py -3 _verify/browser_profile.py --self-test` | **11/11** |
| 入口机器检查负对照 | `py -3 _verify/check_driver_entrypoints.py --self-test` | **4/4** |
| 入口机器检查（真实） | `py -3 _verify/check_driver_entrypoints.py` | 13/14 取锁；**1 红=`geom_probe.py`**（lead 域） |
| **串行套件（10 个驱动）** | `py -3 _verify/run_browser_suite.py` | **10/10，exit 0**；每个驱动**本进程新增残留 0**；全程**同前缀全局 = 0** |
| 全量回归 | `py -3 -m pytest -o addopts="" -q` | **114 passed** |
| 起服务自检 | `py -3 server.py --port 0 --selftest` | **9/9** |
| lead 的矩阵 | `py -3 _verify/viewport_matrix.py` | **10/10** |
| lead 的解析自检 | `py -3 _verify/viewport_matrix.py --self-test` | **6/6** |
| 收尾残留 | 目录 / 孤儿 Edge / 锁文件 | **0 / 0 / 不存在** |

**串行套件原文（本次为「安静机器」运行：无其他驱动、无锁、起始残留 0）**
```
geom_states   exit=0  12/12 PASS   [卫生] 本进程 before=0 after=0；同前缀全局=0
narrow_layout exit=0   4/4 PASS    [卫生] 本进程 before=0 after=0；同前缀全局=0
advparams_space exit=0 3/3 PASS    …
advanced_params_overlap exit=0 4/4 PASS
param_overlap exit=0   2/2 PASS
param_overlap2 exit=0  3/3 PASS
geom_diag     exit=0   3/3 PASS
crosscheck    exit=0   7/7 PASS
frontend_kickoff exit=0 10/10 PASS
geom_matrix   exit=0  16/16 PASS
通过 10/10；判据FAIL(2)=无；卫生失败(3)=无；环境/并发(4)=无；其他=无
```

**并发行为实测（§11.5-6 的正向验证）**：A 持锁时 B 立即判环境/并发类：
```
[并发] 已有驱动在跑：pid=105096 tag=narrow since=10:29:51；同前缀 profile 0 个（[]）
        → 按 CONTRACT §11.5-6 判为**环境/并发类失败**（exit 4），不得记为产品缺陷；请串行重跑。
B(geom_diag) EXIT=4     A(narrow_layout) EXIT=0（4/4 PASS，残留 0）
```

#### 22.A.6 结论与口径

- **A 段（§11.5 收口 + §11.5-8 机器检查）PASS**；提交哈希取决于 lead 的本轮提交（见 §22.9）。
- 上述结果是**工作树**运行；按冻结流程，**最终验收须在全新克隆上按提交哈希复跑**（§22.B 会照此执行）。
- **并发状态：安静机器（无其他驱动、无锁、起始残留 0）**；此前一轮同样 10/10 但**有并发**（信息行显示同前缀全局 1–2），已按 §11.5-7 区分记录。
