# 第三方前端资产

全部从 jsDelivr / npm 下载到 `vendor/`，**运行时零 CDN 请求**，断网可演示。
导入页面时只加载实际用到的文件；`alpine` / `pico` / `chart` / `animate` 为备用，当前页面没有加载。

| 资产 | 版本 | 许可证 | 本地路径 | 状态 | 来源 |
| --- | --- | --- | --- | --- | --- |
| marked | 16.4.2 | MIT | `vendor/marked.umd.js` | 使用中 | https://cdn.jsdelivr.net/npm/marked@16/lib/marked.umd.js |
| DOMPurify | 3.4.17 | MPL-2.0 OR Apache-2.0 | `vendor/dompurify.min.js` | 使用中 | https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js |
| KaTeX | 0.16.11 | MIT | `vendor/katex/` | 使用中 | https://registry.npmjs.org/katex/-/katex-0.16.11.tgz |
| Lucide | 0.468.0 | ISC | `vendor/lucide.min.js` | 使用中 | https://cdn.jsdelivr.net/npm/lucide@0.468.0/dist/umd/lucide.min.js |
| canvas-confetti | 1.9.3 | ISC | `vendor/confetti.browser.min.js` | 使用中 | https://cdn.jsdelivr.net/npm/canvas-confetti@1.9.3/dist/confetti.browser.min.js |
| Alpine.js | 3.x | MIT | `vendor/alpine.min.js` | 备用 | https://cdn.jsdelivr.net/npm/alpinejs@3/dist/cdn.min.js |
| Pico CSS | 2.1.1 | MIT | `vendor/pico.min.css` | 备用 | https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css |
| Chart.js | 4.6.1 | MIT | `vendor/chart.umd.min.js` | 备用（仪表盘选题） | https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js |
| Animate.css | 4.1.1 | MIT | `vendor/animate.min.css` | 备用 | https://cdn.jsdelivr.net/npm/animate.css@4.1.1/animate.min.css |

许可证原文在 `vendor/licenses/`：

- `marked.LICENSE`
- `dompurify.LICENSE`
- `katex.LICENSE`（字体随 KaTeX 包分发）
- `lucide.LICENSE`
- `confetti.LICENSE`
- `alpine.LICENSE` / `pico.LICENSE` / `chart.LICENSE` / `animate.LICENSE`

## 使用纪律

- MIT / ISC / Apache-2.0：保留版权与许可证声明即可，黑客松作品通常没问题。
- DOMPurify 是 MPL-2.0 OR Apache-2.0 双许可；改它的源码再分发时注意 MPL 的文件级义务，直接引用 minified 文件不受影响。
- 本目录的第三方文件未做修改，仅原样下载；升级时替换同名文件并更新本表。
- 所有页面输出的 Markdown 都先经 DOMPurify 清洗，再交给 KaTeX 渲染，避免模型输出注入 HTML。
- 图标通过 Lucide 的 `data-lucide` 属性在本地生成 SVG，不请求图标 CDN。

## 未引入但值得知道的开源项目

这些没有下载，现场若想换路线先评估网络与安装成本：

| 项目 | 许可证 | 适合 | 现场建议 |
| --- | --- | --- | --- |
| shadcn/ui | MIT | React 组件体系 | 需要构建链，90 分钟不优先 |
| DaisyUI | MIT | Tailwind 组件类 | 需要 Tailwind 构建 |
| Chatbot UI | MIT | 完整聊天产品 | 功能多，裁剪成本高 |
| Gradio | Apache-2.0 | 快速上传/对比界面 | `pip install` 可做备用演示 |
| Streamlit | Apache-2.0 | 数据看板 | 分析类选题快 |
| Mermaid | MIT | 流程图/关系图 | 约 3 MB，非必要不引入 |
