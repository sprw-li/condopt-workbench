/* ============================================================
   EduKit · adapter.example.js
   ------------------------------------------------------------
   把本文件放到 index.html 的 app.js 之后再加载，即可从 mock 切到
   自己的 SSE 后端：

     <script defer src="./assets/app.js"></script>
     <script defer src="./assets/adapter.example.js"></script>

   后端只需按下面的契约返回 text/event-stream：
     event: meta   data: {"run_id":"...","mode":"api","model":"deepseek-chat"}
     event: stage  data: {"index":0,"label":"解析材料","status":"running"}
     event: delta  data: {"text":"## 批改结论\n"}
     event: done   data: {"run_id":"...","elapsed_ms":6200,"mode":"api"}

   请求体：
     {"scenario":"grading","text":"...","files":[{"name":"a.png","mime":"image/png","data":"<纯 base64>"}]}
   ============================================================ */

(() => {
  "use strict";

  if (!window.EduKit || !window.EduKit.EduAPI) {
    console.warn("[EduKit] 请先加载 assets/app.js");
    return;
  }

  const endpoint = window.EDUKIT_ENDPOINT || "/api/run";

  EduKit.EduAPI.run = async function run(payload, emit) {
    let received = false;
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.error || "HTTP " + response.status);
      }
      if (!response.body) throw new Error("当前浏览器不支持流式响应");

      for await (const event of EduKit.parseSSE(response)) {
        received = true;
        emit(event);
      }
    } catch (error) {
      // 一个 delta 都没收到时，回退到离线 mock，保证现场演示不断。
      if (!received) {
        emit({ type: "notice", message: "真实接口不可用，已回退离线演示：" + error.message });
        return EduKit.mockRun(payload, emit);
      }
      throw error;
    }
  };

  console.info("[EduKit] adapter on · " + endpoint);
})();
