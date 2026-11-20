/* 从 web/assets/data.js 抽出五场景（真实执行 JS，不手抄）。
   用法：node _verify/extract_data.js <data.js 路径> <输出 json 路径> */
const fs = require("fs");
const vm = require("vm");

const [, , srcPath, outPath] = process.argv;
const src = fs.readFileSync(srcPath, "utf8");
const sandbox = { window: {}, String, console, JSON };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: srcPath });

const scenarios = sandbox.window.EDU_DATA.scenarios;
const keys = Object.keys(scenarios[0]);
const payload = {
  n_scenarios: scenarios.length,
  project: sandbox.window.EDU_DATA.project,
  first_scenario_keys: keys,
  scenarios: scenarios.map((s) => ({
    key: s.key,
    no: s.no,
    title: s.title,
    tagline: s.tagline,
    short: s.short,
    action: s.action,
    needs_run: s.needs_run,
    stages: s.stages,
    has_mock: typeof s.mock === "string" && s.mock.length > 0,
    has_review: !!s.review,
    has_example: typeof s.example === "string" && s.example.length > 0,
    has_outputs: Array.isArray(s.outputs) && s.outputs.length > 0,
  })),
};
fs.writeFileSync(outPath, JSON.stringify(payload, null, 2), "utf8");
console.log(`extracted ${scenarios.length} scenarios -> ${outPath}`);
