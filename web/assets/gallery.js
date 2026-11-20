/* ============================================================
   EduKit · gallery.js
   代码展开 / 一键复制 / 闪卡翻面 / 深浅色
   ============================================================ */

(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const toast = $("toast");
  const toastText = $("toastText");
  const themeBtn = $("themeBtn");

  let toastTimer = null;
  function showToast(message) {
    toastText.textContent = message;
    toast.classList.add("is-on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("is-on"), 2400);
  }

  function dedent(html) {
    const lines = String(html).replace(/\t/g, "  ").replace(/^\n+|\n+$/g, "").split("\n");
    const indents = lines.filter((line) => line.trim()).map((line) => (line.match(/^ */) || [""])[0].length);
    const min = indents.length ? Math.min(...indents) : 0;
    return lines.map((line) => line.slice(min)).join("\n").trim();
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (error) {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
      area.remove();
      return ok;
    }
  }

  function applyTheme(theme, persist = true) {
    const next = theme === "dark" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    if (persist) {
      try { localStorage.setItem("edukit.theme", next); } catch (error) { /* ignore */ }
    }
    themeBtn.innerHTML = `<i data-lucide="${next === "dark" ? "sun" : "moon"}"></i>`;
    themeBtn.setAttribute("aria-label", next === "dark" ? "切换到浅色" : "切换到深色");
    refreshIcons();
  }

  function refreshIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      try { window.lucide.createIcons(); } catch (error) { /* ignore */ }
    }
  }

  // 用 <template> 里的标记填充代码块
  document.querySelectorAll(".demo-card").forEach((card) => {
    const template = card.querySelector("template");
    const code = card.querySelector("code");
    if (template && code) code.textContent = dedent(template.innerHTML);
  });

  document.addEventListener("click", async (event) => {
    const copyButton = event.target.closest(".copy-btn");
    if (copyButton) {
      event.preventDefault();
      event.stopPropagation();
      const card = copyButton.closest(".demo-card");
      const code = card && card.querySelector("code");
      if (!code) return;
      const ok = await copyText(code.textContent);
      if (ok) {
        const original = copyButton.textContent;
        copyButton.textContent = "已复制";
        setTimeout(() => { copyButton.textContent = original; }, 1400);
        showToast("HTML 已复制，直接贴进页面即可");
      } else {
        showToast("复制失败，请手动选中代码");
      }
      return;
    }

    const flip = event.target.closest("[data-flip]");
    if (flip) {
      flip.classList.toggle("is-flipped");
      return;
    }

    const scenario = event.target.closest(".demo-preview .scenario-card");
    if (scenario) {
      const list = scenario.parentElement;
      list.querySelectorAll(".scenario-card").forEach((node) => node.classList.remove("is-active"));
      scenario.classList.add("is-active");
    }
  });

  themeBtn.addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });

  let stored = "light";
  try { stored = localStorage.getItem("edukit.theme") || "light"; } catch (error) { /* ignore */ }
  applyTheme(stored, false);
  refreshIcons();
})();
