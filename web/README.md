# 课隙 EduKit · AI 教育前端复用包

> 为 2026-10-24 AI 教育黑客松准备。**无构建、无 npm、断网能跑。**
> 输入 → 四段处理 → 流式输出 → 教师复核，90 秒内能演完一条窄闭环。

![工作台](preview/overview.png)

## 60 秒开始

双击 `start.cmd`（会启动本地服务器并打开浏览器），或者：

```powershell
cd web        # 本目录：该复用包在工作台里的位置
python -m http.server 8787
```

打开 `http://127.0.0.1:8787/`。直接双击 `index.html` 也能看，但本地服务器下 KaTeX 字体和剪贴板更稳。

- `?demo=1`：打开后自动跑第一个场景。
- `?demo=grading`：自动跑指定场景（`grading` / `early-warning` / `study-cards` / `tutor`）。
- `?demo=present` 或 `?present=1`：直接进入演示视图。
- 全部输出默认是**离线 mock**，不需要 API key，也不发网络请求。

## 这是什么

- 一个可直接演示的**教育 AI 工作台壳**：场景切换、附件、阶段推进、流式 Markdown、KaTeX 公式、教师复核、留痕、导出。
- 一套**12 个教育场景组件**：场景卡、评分条、证据块、风险徽章、闪卡、步骤条、指标卡、对照栏、小测、复核签章、空状态、边界提示。
- 一个**接后端的单点适配层**：只替换 `EduKit.EduAPI.run`，SSE 事件契约与 `Preparation/starters/edu-demo` 兼容。
- 一份**离线依赖包**：marked、DOMPurify、KaTeX、Lucide、canvas-confetti 等，全部在 `vendor/`。

## 这不是什么

- 不是模型、不是后端、不是 RAG。默认 mock 是预置文本，不代表真实模型能力，演示时要主动说明。
- 不做真实 PDF/图片解析；附件只读出 base64 交给适配层，解析逻辑应在你的后端。
- 不是完整产品：没有登录、数据库、班级管理。黑客松只保留一条可演示的窄闭环。

## 四个内置场景

| 场景 | 输入 | 输出 | 复核方式 |
| --- | --- | --- | --- |
| 作业批改 `grading` | 评分规则 + 学生答案 | 逐项证据、建议分、学生反馈 | 改最终分 + 采纳/调整/退回 |
| 学情预警 `early-warning` | 学习行为 JSON | 风险等级、证据链、干预动作 | 处置意见 + 备注 |
| 学习卡片 `study-cards` | 一页讲义 | 5 张卡片 + 难度分层 | 采用 / 修改 / 退回 |
| AI 导师 `tutor` | 学生原话/错题 | 卡点诊断 + 三级提示 + 追问 | 发送前审核 |

![批改结果](preview/result.png)

## 目录

```
frontend/
  index.html                  工作台（单页，双击可跑）
  components/index.html       12 个组件 + 可复制代码
  assets/
    data.js                   ★ 场景、样例、离线 mock —— 换题先改这里
    base.css                  色板 / 字体 / 按钮 / 表单 / 通用控件
    components.css            教育场景组件样式
    app.css                   工作台布局
    app.js                    状态、SSE、Markdown、KaTeX、复核
    gallery.css / gallery.js  组件库页面
    adapter.example.js        接真实后端的示例适配器
  vendor/                     离线依赖 + licenses/
  preview/                    本 README 的截图
  README.md                   本文件
  SOURCES.md                  第三方版本、来源与许可证
```

## 三条复用路径

| 你手里的时间 | 改什么 | 得到什么 |
| --- | --- | --- |
| 5 分钟 | `assets/data.js` | 换项目名、四个场景、样例与离线输出 |
| 15 分钟 | `assets/base.css` 顶部变量 + `data.js` | 换一套品牌视觉，布局不动 |
| 30 分钟 | `assets/adapter.example.js` | 接真实模型/后端，保留 mock 兜底 |

### 1. 换题：只改 `assets/data.js`

一个场景的字段：

| 字段 | 作用 |
| --- | --- |
| `key` | 唯一 id，也用于 `?demo=key` |
| `no` / `title` / `tagline` / `short` | 侧栏编号、标题、说明、短标签 |
| `inputLabel` / `placeholder` / `accept` / `filesHint` | 输入区文案与附件类型 |
| `stages` | 四段处理阶段，顺序对应 SSE 的 `stage.index` |
| `example` | “载入示例”填入的文本 |
| `mock` | 离线演示的 Markdown；用 `String.raw` 保留 LaTeX 反斜杠 |
| `outputs` | 输入区底部“本次产出”标签 |
| `review` | `{type:"score", score:62, max:100}` 或 `{type:"decision", options:[...]}` |

加一个场景就会自动出现在侧栏、`1`–`9` 快捷键和 `?demo=` 里，前端不用动。

### 2. 换视觉：只改 `assets/base.css` 的变量

```css
:root {
  --paper: #f5f1e8;      /* 页面底色 */
  --paper-2: #fffdf8;    /* 卡片底色 */
  --ink: #1b2430;        /* 主文字 */
  --accent: #0f6b62;     /* 主色 */
  --flame: #c2410c;      /* 强调/复核 */
  --serif: "Songti SC", "Noto Serif SC", "SimSun", Georgia, serif;
}
```

深色主题在 `html[data-theme="dark"]` 里，改同一组变量即可。组件库页面会自动跟随。

### 3. 接后端：替换一个函数

默认实现走 `mockRun`。把 `assets/adapter.example.js` 放到 `app.js` 之后加载，或者直接照着改：

```js
EduKit.EduAPI.run = async function (payload, emit) {
  const response = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error("HTTP " + response.status);

  for await (const event of EduKit.parseSSE(response)) {
    emit(event); // {type:"stage" | "delta" | "done" | ...}
  }
};
```

请求体：

```json
{
  "scenario": "grading",
  "text": "评分规则……学生答案……",
  "files": [
    { "name": "a.png", "mime": "image/png", "data": "<纯 base64，不带 data: 前缀>" }
  ]
}
```

SSE 事件（标准 `event:` + `data:` 帧）：

| 事件 | 数据 | 说明 |
| --- | --- | --- |
| `meta` | `{"run_id","mode","model"}` | `mode:"api"` 时顶栏切“真实模型” |
| `stage` | `{"index":0,"label":"解析材料","status":"running"}` | `status`: `pending` / `running` / `done` |
| `delta` | `{"text":"## 批改结论\n"}` | 追加到 Markdown 输出 |
| `notice` | `{"message":"..."}` | 顶部 toast |
| `error` | `{"message","hint"}` | 失败提示，不中断页面 |
| `done` | `{"run_id","elapsed_ms","mode"}` | 结束并展示复核区 |

注意：

- 后端必须返回 `text/event-stream`，并逐帧 flush，不能缓冲到结束才发。
- 前后端不同源时要加 CORS；`file://` 页面无法直接 `fetch` 到 `http://`，请用本地服务器托管本目录。
- 真实调用失败时，`adapter.example.js` 会在“一个 delta 都没收到”的情况下自动回退 `EduKit.mockRun`，演示不会断。
- 复核记录默认只写 `localStorage`。要入库就在 `saveReview()` 里加一个 `POST /api/save`。

## 12 个组件

打开 `components/index.html` 看效果、展开代码、一键复制。组件只依赖 `base.css` + `components.css`。

| # | 组件 | 典型用途 |
| --- | --- | --- |
| 01 | 场景选择卡 | 左栏导航 / 首页入口 |
| 02 | 分项评分条 | 把总分拆成可检查的评分项 |
| 03 | 原文证据块 | 结论旁边放可溯源原文 |
| 04 | 风险等级徽章 | 低 / 中 / 高分流，不做诊断 |
| 05 | 教师复核签章 | 把“人在回路”做成可见交付物 |
| 06 | 学习闪卡 | 知识点自测、现场互动 |
| 07 | 原文 / 适配对照 | 无障碍改写、分层材料 |
| 08 | 小测选项 | 单选、正确/错误态 |
| 09 | 处理步骤条 | 流式生成前的“思考过程” |
| 10 | 指标卡 | 建议分、复核用时、证据条数 |
| 11 | 空状态 | 告诉用户下一步点哪里 |
| 12 | 边界提示条 | 隐私、转介、mock 说明 |

![组件库](preview/components.png)

## 90 秒演示脚本

1. **0–10s**：打开 `?demo=present`，只留结果区。一句话：“AI 给建议，老师拍板。”
2. **10–30s**：点“载入示例”→“开始处理”。让评委看四段状态依次点亮、右侧文字流式出现。
3. **30–50s**：停在评分表和“逐项证据”。指出原文引用与公式：`Δ = 16 + 20 = 36`。
4. **50–70s**：在“教师复核”里点“调整分数”，把 62 改成 65，写一句“按课堂标准调整”，保存。展示签章、跑马灯记录。
5. **70–85s**：按 `Esc` 退出演示视图，点“组件库”或切换场景，证明不是一张死页面。
6. **85–90s**：说边界：mock 不等于模型能力；最终分由教师确认；高风险只转介。

## 快捷键

| 操作 | 键 |
| --- | --- |
| 运行 | `Ctrl` / `⌘` + `Enter` |
| 切换场景 | `1` – `4` |
| 演示视图 | `P` |
| 退出演示 / 关闭弹窗 | `Esc` |

## 视觉与无障碍

- 深浅两套主题，跟随 `localStorage`，不闪白。
- 键盘可达：按钮、radio、`summary` 都可聚焦；`:focus-visible` 有清晰描边。
- 颜色之外始终有文字（风险等级、评分、状态），不靠颜色单独传达信息。
- `prefers-reduced-motion` 下关闭动画。
- 打印样式会隐藏输入区和工具栏，结果可直接存 PDF。

## 边界与隐私

- 默认 mock 输出是预置演示，不是模型真实能力；演示时必须说明。
- 批改只给建议分，最终分由教师确认；高风险求助只给转介建议，不自动发送、不诊断。
- 样例均为虚构。不要把真实学生姓名、学号、成绩、心理记录发给公共模型。
- 默认模式不发网络请求；复核记录只写浏览器 `localStorage`，清站点数据即清空。
- 接真实后端后，数据治理边界由你的后端决定，本前端不额外上传。

## 已验证

- Edge（Chromium）1440×900 下跑通：首屏、`?demo=1` 自动运行、KaTeX 公式、表格、复核面板、深色主题、组件库长页。
- 四个场景（`grading` / `early-warning` / `study-cards` / `tutor`）自动跑完：4/4 阶段完成、复核区出现、公式与表格正常，控制台无异常。
- 复核流程实测：选择“调整分数”→ 填 65 → 写意见 → 保存，签章出现，`localStorage` 写入 1 条记录。
- 390 px 视口实测无横向溢出；组件库 12 个代码块均已填充，复制按钮 12/12。
- `node --check` 通过 `app.js` / `data.js` / `gallery.js` / `adapter.example.js`。
- 本地服务器加载全部资源返回 200，无 404；KaTeX 字体实际命中。
- `file://` 直接打开也能完整跑 mock 流程（Edge 实测，无需额外启动参数）。
- mock 流式约 4–5 秒跑完，阶段推进与输出同步。

## 已知限制

- mock 文本固定，换输入不会真的改变判断；现场可改 `data.js` 里的 `mock` 做“伪实时”。
- 附件只做 Data URL 读取与预览，不做 OCR/PDF 抽取。
- `file://` 下部分浏览器的字体、剪贴板、`localStorage` 行为不一致，演示请用本地服务器。
- 没有构建/压缩；这是有意为之，90 分钟内改得动比体积更重要。

## 第三方与许可

第三方库的版本、来源、许可证见 [SOURCES.md](SOURCES.md)，许可证原文在 `vendor/licenses/`。所有依赖已下载到本地，运行时不访问 CDN。

自写页面与组件代码可自由用于本次黑客松；如果复用第三方代码，请保留其版权声明。
