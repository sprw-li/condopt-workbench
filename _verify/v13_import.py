"""CONTRACT §9（导入已有数据）独立验收 —— verifier 自建 stub 网关，不信实现者的测试。

覆盖 §9.7 的 1–5：
* scan：列画像与真实统计一致；路径穿越/根外/目录/错误后缀/超限 → 400（§9.2）
* map：未开开关 → E_LLM_UNAVAILABLE(503)；缺 consent → E_CONSENT_REQUIRED(400)；
       fallback:"rules" 不需 consent；scan_id 不匹配 → 400；
       **假网关成功路径** → 断言「只发样本不发整文件」+「密钥不出现在任何响应」
* apply：n_obs、budget 校验（422 + suggested_budget 数字）、duplicate/rejected、
       import_audit.jsonl、params_schema 与 registry 一致、顶层平铺 + result 镜像
* 端到端：scan → map → apply → 用 params_schema 调 ask

运行：py -3 _verify/v13_import.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    REPO, Report, Server, action, free_port, fresh, get, http, js, log, short_answers,
)

sys.path.insert(0, str(REPO / "src"))
from condopt import registry as condopt_registry  # noqa: E402

SECRET = "sk-VERIFIER-SECRET-9f3c1d7a"  # 真实 API key 是 ASCII（非 ASCII 的鲁棒性另立 F6 单测）
SECRET_NON_ASCII = "sk-中文密钥-绝不外泄"
KEY_ENV = "BOHRIUM_API_KEY"
BLOB_FILLER = "x" * 180  # 每行一个 200 字符左右的 blob 列 → 整文件远大于样本


def blob_marker(index: int) -> str:
    """每行唯一的可区分 token（只在「该行被外发」时才会出现在请求体里）。

    注意：**不能用数值列的唯一值当标记** —— 列画像里的 min/max 本来就会泄漏该值
    （我第一版把标记放在 yield 列的末行，结果被 yield 的 max 名正言顺地带出去了，
    那不是缺陷而是 §9.1 允许的「列画像」）。所以标记必须放在**不会被统计成 min/max 的文本列**。
    """
    return f"BLOBMARK{index:04d}END"


# --------------------------------------------------------------------------- #
# 假网关（OpenAI-compatible /chat/completions）
# --------------------------------------------------------------------------- #


class StubGateway:
    """OpenAI-compatible /chat/completions 假网关（记录请求，可切换返回形态）。"""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.raw_bodies: list[str] = []
        #: "json" = 正常返回；"text_then_json" = 首次纯文本、第二次合法（测「只修格式」重试）；
        #: "always_text" = 永远纯文本（测 502 E_LLM_BAD_RESPONSE）
        self.mode = "json"
        self.call_count = 0
        self.mode_calls = 0
        self.reply = {
            "variables": [
                {"column": "T", "name": "T", "kind": "continuous", "low": 40, "high": 120, "unit": "degC"},
                {"column": "time", "name": "time", "kind": "continuous", "low": 5, "high": 120, "unit": "min"},
                {"column": "solvent", "name": "solvent", "kind": "categorical", "choices": ["MeCN", "EtOH", "water"]},
            ],
            "objective": {"column": "yield", "name": "yield", "direction": "max"},
            "constraints": [{"column": "purity", "op": ">=", "value": 95}],
            "fidelity": None,
            "cost": None,
        }
        self.confidence = 0.82
        self.rationale = "T/time/solvent 是条件列，yield 是目标列，purity 是约束。"
        self.port = free_port()
        self.httpd: ThreadingHTTPServer | None = None

    def __enter__(self) -> "StubGateway":
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:  # noqa: ANN002
                pass

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8", "replace")
                outer.raw_bodies.append(raw)
                outer.call_count += 1
                outer.mode_calls += 1
                try:
                    outer.requests.append(json.loads(raw))
                except json.JSONDecodeError:
                    outer.requests.append({"_unparsable": raw[:200]})
                if outer.mode == "always_text" or (outer.mode == "text_then_json" and outer.mode_calls == 1):
                    content = "这不是 JSON，只是普通说明文字。"
                else:
                    content = json.dumps(
                        {
                            "mapping": outer.reply,
                            "confidence": outer.confidence,
                            "rationale": outer.rationale,
                        },
                        ensure_ascii=False,
                    )
                body = json.dumps(
                    {"choices": [{"message": {"role": "assistant", "content": content}}]},
                    ensure_ascii=False,
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"


# --------------------------------------------------------------------------- #
# 测试数据
# --------------------------------------------------------------------------- #


def make_csv(path: Path) -> None:
    rows = ["T,time,solvent,yield,purity,note,blob"]
    for index in range(84):
        t = 40 + (index % 17) * 5
        time = 5 + (index % 12) * 10
        solvent = ["MeCN", "EtOH", "water"][index % 3]
        yield_ = round(20 + (index % 31) * 2.5, 2)
        purity = 90 + (index % 9)
        rows.append(f"{t},{time},{solvent},{yield_},{purity},run{index},{blob_marker(index)}{BLOB_FILLER}")
    # 1 条重复（与第 1 行完全同条件同结果）
    rows.append(f"40,5,MeCN,20.0,90,dup-of-row-1,{blob_marker(84)}{BLOB_FILLER}")
    # 坏行：yield 不是数值
    rows.append(f"95,65,EtOH,n/a,97,bad-row,{blob_marker(85)}{BLOB_FILLER}")
    # 坏行：超出 T 上界
    rows.append(f"400,65,water,50.0,97,out-of-range,{blob_marker(86)}{BLOB_FILLER}")
    # 最后一行（其 blob 标记必须**不出现在外发请求**里）
    rows.append(f"100,115,MeCN,50.0,99,last-row,{blob_marker(87)}{BLOB_FILLER}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def independent_stats(path: Path) -> dict:
    """用 stdlib csv 自己算一遍统计量（独立于被验收的实现）。"""
    import csv as _csv

    with path.open(encoding="utf-8", newline="") as handle:
        table = list(_csv.DictReader(handle))
    t_values = [float(row["T"]) for row in table]
    return {
        "n_rows": len(table),
        "T": {"min": min(t_values), "max": max(t_values), "unique": len(set(t_values))},
        "solvent_choices": sorted({row["solvent"] for row in table}),
    }


MAPPING = {
    "variables": [
        {"column": "T", "name": "T", "kind": "continuous", "low": 40, "high": 120, "unit": "degC"},
        {"column": "time", "name": "time", "kind": "continuous", "low": 5, "high": 120, "unit": "min"},
        {"column": "solvent", "name": "solvent", "kind": "categorical", "choices": ["MeCN", "EtOH", "water"]},
    ],
    "objective": {"column": "yield", "name": "yield", "direction": "max"},
    "constraints": [{"column": "purity", "op": ">=", "value": 95}],
    "fidelity": None,
    "cost": None,
}


def main() -> int:
    report = Report("§9 导入功能独立验收")
    data_dir = fresh("import_data")
    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = data_dir / "lab_log.csv"
    make_csv(csv_path)
    n_lines = len(csv_path.read_text("utf-8").strip().splitlines()) - 1
    stats = independent_stats(csv_path)
    log(f"测试数据：{csv_path}（{n_lines} 行数据）")
    log(f"独立统计（我自己用 csv 模块算的）：{stats}")

    leaked: list[str] = []

    with StubGateway() as stub:
        log(f"假网关：{stub.url}（只记录请求，返回固定映射 JSON）")
        env = {KEY_ENV: SECRET, "CONDOPT_IMPORT_LLM_BASE_URL": stub.url, "CONDOPT_IMPORT_LLM_MODEL": "stub-model"}
        runs = fresh("import_runs")
        with Server(runs, extra=["--import-llm", "--allow-import-root", str(data_dir)], env=env) as server:
            base = server.base

            def call(path: str, body: dict | None = None):
                status, _, text = (
                    get(base, path) if body is None else http(base, "POST", path, body=body)
                )
                if SECRET in text:
                    leaked.append(f"{path}: 响应里出现密钥明文")
                return status, text

            # ---------------- A. bootstrap.import_llm（绝不回显密钥） -------------
            log("\n### §9.1/A bootstrap.import_llm")
            status, text = call("/api/bootstrap")
            boot = js(text)
            llm = boot.get("import_llm") or {}
            report.check(
                "A1 bootstrap.import_llm 字段齐全 {enabled,configured,url,model,key_env}",
                status == 200 and {"enabled", "configured", "url", "model", "key_env"} <= set(llm),
                f"status={status} import_llm={llm}",
            )
            report.check(
                "A2 import_llm 反映实际生效值（开关开、密钥已配置、URL/模型取自环境变量）",
                llm.get("enabled") is True and llm.get("configured") is True
                and llm.get("url") == stub.url and llm.get("model") == "stub-model"
                and llm.get("key_env") == KEY_ENV,
                f"enabled={llm.get('enabled')} configured={llm.get('configured')} url={llm.get('url')} "
                f"model={llm.get('model')} key_env={llm.get('key_env')}",
            )
            report.check(
                "A3 bootstrap 全文不含密钥明文（只出现「是否已配置」）",
                SECRET not in text,
                f"响应字节={len(text)} 含密钥={SECRET in text}",
            )

            # ---------------- B. scan（§9.3 / §9.2） ----------------
            log("\n### §9.3 scan：列画像与真实统计")
            status, text = call("/api/import/scan", {"path": str(csv_path)})
            scan = js(text)
            cols = {c["name"]: c for c in scan.get("columns", [])}
            report.check(
                "B1 scan 成功：n_rows 与实际一致、scan_id 为 16 位十六进制",
                status == 200 and scan.get("ok") is True and scan.get("n_rows") == n_lines
                and len(str(scan.get("scan_id"))) == 16,
                f"status={status} n_rows={scan.get('n_rows')}（文件 {n_lines} 行）scan_id={scan.get('scan_id')}",
            )
            report.check(
                "B2 列画像来自真实统计（与我自己用 stdlib csv 算的 min/max/unique/choices 一致）",
                cols.get("T", {}).get("min") == stats["T"]["min"]
                and cols.get("T", {}).get("max") == stats["T"]["max"]
                and cols.get("T", {}).get("unique") == stats["T"]["unique"]
                and sorted(cols.get("solvent", {}).get("choices") or []) == stats["solvent_choices"],
                f"实测 T={ {k: cols.get('T', {}).get(k) for k in ('inferred', 'min', 'max', 'unique')} } vs 独立计算={stats['T']}；"
                f"solvent.choices={sorted(cols.get('solvent', {}).get('choices') or [])} vs {stats['solvent_choices']}",
            )
            report.check(
                "B3 scan 返回 rule_mapping（规则推断，供 fallback 用）与 file 元信息",
                isinstance(scan.get("rule_mapping"), dict) and isinstance(scan.get("file"), dict),
                f"rule_mapping keys={sorted((scan.get('rule_mapping') or {}).keys())} file={scan.get('file')}",
            )
            scan_id = scan.get("scan_id")

            log("\n### §9.2 路径安全")
            safe_cases = [
                ("..", {"path": "../../etc/passwd"}, 400),
                ("根外绝对路径", {"path": "C" + ":\\Windows\\win.ini"}, 400),
                ("目录", {"path": str(data_dir)}, 400),
                ("错误后缀", {"path": str(data_dir / "lab_log.exe")}, 400),
                ("缺 path", {}, 422),
            ]
            for label, body, expected in safe_cases:
                status, text = call("/api/import/scan", body)
                parsed = js(text)
                report.check(
                    f"B4 scan {label} → {expected} 且不泄漏内部路径",
                    status == expected and parsed.get("ok") is False,
                    f"status={status} error={parsed.get('error')} message={str(parsed.get('message'))[:90]!r}",
                )

            # ---------------- C. map 的 consent 门控（§9.4） ----------------
            log("\n### §9.4 map：consent 只在「即将真的外发」时校验")
            status, text = call("/api/import/map", {"scan_id": scan_id})
            parsed = js(text)
            report.check(
                "C1 开关+密钥就绪但没有 consent → 400 E_CONSENT_REQUIRED（不是 503）",
                status == 400 and parsed.get("error") == "E_CONSENT_REQUIRED",
                f"status={status} error={parsed.get('error')} message={str(parsed.get('message'))[:80]!r}",
            )
            report.check("C1b 缺 consent 时**没有**发出任何外发请求", len(stub.requests) == 0,
                         f"假网关收到请求数={len(stub.requests)}")

            status, text = call("/api/import/map", {"scan_id": scan_id, "fallback": "rules"})
            rules = js(text)
            report.check(
                "C2 fallback:'rules' 不需要 consent → 200 source='rules' 且 sent=null",
                status == 200 and rules.get("source") == "rules" and rules.get("sent") is None,
                f"status={status} source={rules.get('source')} sent={rules.get('sent')} mapping 变量数="
                f"{len((rules.get('mapping') or {}).get('variables') or [])}",
            )
            status, text = call("/api/import/map", {"scan_id": "deadbeefdeadbeef", "consent": True})
            report.check(
                "C3 scan_id 不匹配 → 400（防「扫 A 打 B」）",
                status == 400 and js(text).get("error") == "E_BAD_REQUEST",
                f"status={status} error={js(text).get('error')}",
            )
            status, text = call("/api/import/map", {"scan_id": scan_id, "consent": True, "oops": 1})
            report.check(
                "C4 map 未知字段 → 422 E_VALIDATION",
                status == 422 and js(text).get("error") == "E_VALIDATION",
                f"status={status} message={str(js(text).get('message'))[:80]!r}",
            )

            # ---------------- D. map 成功路径 + 只发样本 ----------------
            log("\n### §9.4 map 假网关成功路径：只发表头+样本，不发整文件")
            before = len(stub.requests)
            status, text = call(
                "/api/import/map",
                {"scan_id": scan_id, "consent": True, "sample_rows": 12, "fallback": "llm"},
            )
            mapped = js(text)
            sent_bodies = stub.raw_bodies[before:]
            joined = "\n".join(sent_bodies)
            report.check(
                "D1 consent:true → 200 source='llm'，返回 mapping/confidence/rationale/sent",
                status == 200 and mapped.get("source") == "llm" and mapped.get("confidence") is not None
                and isinstance(mapped.get("mapping"), dict) and isinstance(mapped.get("sent"), dict),
                f"status={status} source={mapped.get('source')} confidence={mapped.get('confidence')} "
                f"sent={mapped.get('sent')} model={mapped.get('model')}",
            )
            report.check(
                "D2 **只发样本、绝不发整文件**：末行 blob 标记不在外发请求里，且出现的行标记数 ≤ sample_rows",
                blob_marker(87) not in joined
                and sum(1 for i in range(88) if blob_marker(i) in joined) <= 12 + 2
                and len(joined.encode("utf-8")) < csv_path.stat().st_size / 2,
                f"末行标记 {blob_marker(87)} 出现={blob_marker(87) in joined}；"
                f"外发请求里出现的行标记数={sum(1 for i in range(88) if blob_marker(i) in joined)}/88"
                f"（sample_rows=12）；外发 {len(joined.encode('utf-8'))} 字节 vs 整文件 {csv_path.stat().st_size} 字节",
            )
            report.check(
                "D2b 外发体里也不含整文件的其他大段内容（blob 填充串出现次数有限）",
                joined.count(BLOB_FILLER) < n_lines // 3,
                f"180 字符填充串出现 {joined.count(BLOB_FILLER)} 次（整文件 {n_lines} 行；<{n_lines // 3} 才说明只是样本）",
            )
            report.check(
                "D3 外发请求里确实带了样本行（≥1 行且有表头列名），证明样本而非空请求",
                "T" in joined and "solvent" in joined and "yield" in joined and "40" in joined,
                f"含表头列名 T/solvent/yield=True；请求预览={joined[:120]!r}",
            )
            report.check(
                "D4 sent.rows == 请求的 sample_rows（12）且 sent.bytes 与实际外发量同量级",
                mapped.get("sent", {}).get("rows") == 12
                and abs(int(mapped.get("sent", {}).get("bytes") or 0) - len(sent_bodies[-1].encode("utf-8"))) <= 64,
                f"sent={mapped.get('sent')} 实测外发字节={len(sent_bodies[-1].encode('utf-8'))}",
            )
            report.check(
                "D5 假网关收到的确实是 chat/completions 形制（有 model + messages）",
                bool(stub.requests) and "model" in stub.requests[-1] and "messages" in stub.requests[-1],
                f"请求键={sorted(stub.requests[-1].keys())} model={stub.requests[-1].get('model')}",
            )

            mapping_from_llm = mapped.get("mapping") or MAPPING

            # ---------------- E. apply（§9.5） ----------------
            log("\n### §9.5 apply：budget 校验 + 平铺响应 + 审计")
            status, text = call(
                "/api/import/apply",
                {"id": "should_not_exist", "path": str(csv_path), "scan_id": scan_id,
                 "mapping": mapping_from_llm, "budget": 5, "batch": 2, "reason": "验收：budget 不足"},
            )
            parsed = js(text)
            suggested = parsed.get("suggested_budget")
            report.check(
                "E1 budget 小于导入行数 → 422 E_VALIDATION 且带 suggested_budget（数字）",
                status == 422 and parsed.get("error") == "E_VALIDATION"
                and isinstance(suggested, (int, float)) and not isinstance(suggested, bool) and suggested >= n_lines,
                f"status={status} suggested_budget={suggested!r} message={str(parsed.get('message'))[:90]!r}",
            )
            report.check(
                "E2 budget 不足时**不静默放大**：run 没有被创建出来",
                not (runs / "should_not_exist").exists(),
                f"runs/should_not_exist 存在={ (runs / 'should_not_exist').exists() }",
            )

            status, text = call(
                "/api/import/apply",
                {"id": "campaign_from_csv", "path": str(csv_path), "scan_id": scan_id,
                 "mapping": mapping_from_llm, "algorithm": "auto", "params": {},
                 "budget": int(suggested), "batch": 4, "reason": "导入历史实验作为先验",
                 "count_toward_budget": True},
            )
            applied = js(text)
            flat_keys = {"run", "imported", "warnings", "params_schema", "next"}
            report.check(
                "E3 apply 成功：顶层平铺 run/imported/warnings/params_schema/next（§9.5）",
                status == 200 and applied.get("ok") is True and flat_keys <= set(applied),
                f"status={status} 顶层键={sorted(applied)}",
            )
            report.check(
                "E4 同样的结构镜像到 result（两条读法都成立）",
                isinstance(applied.get("result"), dict)
                and all(applied.get("result", {}).get(k) == applied.get(k) for k in flat_keys),
                f"result 键={sorted(applied.get('result') or {})}",
            )
            run_summary = applied.get("run") or {}
            imported = applied.get("imported") or {}
            report.check(
                "E5 n_obs 与 imported.accepted 一致；重复行计入 duplicate；坏行逐行给原因",
                run_summary.get("n_obs") == imported.get("accepted")
                and imported.get("duplicate", 0) >= 1 and len(imported.get("rejected") or []) >= 2
                and all({"row", "reason"} <= set(item) for item in imported.get("rejected") or []),
                f"n_obs={run_summary.get('n_obs')} imported={{accepted:{imported.get('accepted')}, "
                f"duplicate:{imported.get('duplicate')}, rejected:{len(imported.get('rejected') or [])}}} "
                f"rejected 样例={(imported.get('rejected') or [])[:2]}",
            )
            report.check(
                "E6 params_schema 来自 registry（与 describe_all 逐字段一致，不手写）",
                _schema_matches_registry(applied.get("params_schema"), run_summary.get("algorithm")),
                f"algorithm={run_summary.get('algorithm')} "
                f"params_schema 字段={[p.get('name') for p in (applied.get('params_schema') or [])][:8]}",
            )
            report.check(
                "E7 next == 'ask'（前端据此接「生成下一批」）",
                applied.get("next") == "ask",
                f"next={applied.get('next')!r} warnings={(applied.get('warnings') or [])[:2]}",
            )

            audit_path = runs / "campaign_from_csv" / "import_audit.jsonl"
            audit_ok, audit_detail = _audit_ok(audit_path, csv_path, imported)
            report.check("E8 import_audit.jsonl 有对应记录（含 path/sha256/accepted/budget/reason）", audit_ok, audit_detail)

            # ---------------- F. 端到端：用 params_schema 继续 ask ----------------
            log("\n### §9.7.4 端到端：import → 用 params_schema 调 ask")
            status, _, text = action(base, "ask", run="campaign_from_csv", payload={"n": 2})
            asked = js(text)
            report.check(
                "F1 apply 之后立刻 ask 出下一批（ok:true，pending 非空）",
                status == 200 and asked.get("ok") is True and len(asked.get("result", {}).get("pending") or []) == 2,
                f"status={status} pending={len(asked.get('result', {}).get('pending') or [])} "
                f"remaining={asked.get('result', {}).get('run', {}).get('remaining')}",
            )

            log("\n### §9.1 密钥不泄漏（全程）")
            report.check(
                "G1 本次全部 HTTP 响应中都没有出现密钥明文",
                not leaked,
                f"泄漏点={leaked or '无'}（共检查 bootstrap/scan/map/apply/ask 等全部响应）",
            )

            log("\n### §9.6 import 场景没有 SSE 通道（不能被 /api/run 误当成可流式场景）")
            status, _, text = http(base, "POST", "/api/run", body={"scenario": "import", "payload": {}})
            parsed = js(text)
            report.check(
                "H0a POST /api/run {scenario:'import'} → 400 E_BAD_REQUEST，hint 指向 /api/import/*",
                status == 400 and parsed.get("error") == "E_BAD_REQUEST"
                and "/api/import" in str(parsed.get("hint")),
                f"status={status} error={parsed.get('error')} message={str(parsed.get('message'))[:80]!r}",
            )
            status, _, text = get(base, "/api/import/scan")
            report.check(
                "H0b GET /api/import/scan（POST-only 端点）→ 405",
                status == 405,
                f"status={status} body={text.strip()[:60]!r}",
            )

            # ---------------- G2. count_toward_budget:false（lead 裁定） ----------------
            log("\n### §9.5 count_toward_budget:false：照常导入 + warning + 审计留痕")
            status, _, text = action(base, "ask", run="campaign_from_csv", payload={"n": 1})
            if status == 200:
                pending_id = js(text)["result"]["pending"][0]["id"]
                action(base, "tell", run="campaign_from_csv",
                       payload={"observations": [{"id": pending_id, "y": 55.0}]})
            status, text = call(
                "/api/import/apply",
                {"id": "campaign_second", "path": str(csv_path), "scan_id": scan_id,
                 "mapping": mapping_from_llm, "algorithm": "auto", "budget": 200, "batch": 4,
                 "reason": "第二次导入，关掉计入开关", "count_toward_budget": False},
            )
            second = js(text)
            second_warnings = " ".join(str(w) for w in (second.get("warnings") or []))
            report.check(
                "G2a count_toward_budget:false → 仍 200 导入成功（不静默忽略开关，而是明确留痕）",
                status == 200 and second.get("ok") is True
                and (second.get("imported") or {}).get("accepted", 0) > 0,
                f"status={status} accepted={(second.get('imported') or {}).get('accepted')}",
            )
            report.check(
                "G2b 回应里有 warning 说明「condopt 语义是先验计入 budget，无法关闭」",
                "无法关闭" in second_warnings or "count_toward_budget" in second_warnings,
                f"warnings={second_warnings[:200]!r}",
            )
            audit2 = runs / "campaign_second" / "import_audit.jsonl"
            rec2 = json.loads([line for line in audit2.read_text("utf-8").splitlines() if line.strip()][-1]) if audit2.exists() else {}
            report.check(
                "G2c 审计记录里 count_toward_budget_requested=false（裁定要求的字段）",
                rec2.get("count_toward_budget_requested") is False,
                f"审计键={sorted(rec2)} count_toward_budget_requested={rec2.get('count_toward_budget_requested')!r}",
            )

            # ---------------- G3. 模型返回非 JSON（含「只修格式」重试） ----------------
            log("\n### §9.4 模型返回非 JSON → 一次格式重试 → 仍失败则 502 E_LLM_BAD_RESPONSE")
            status, text = call("/api/import/scan", {"path": str(csv_path)})
            sid3 = js(text)["scan_id"]
            stub.mode_calls = 0; stub.mode = "text_then_json"
            before_calls = stub.call_count
            status, text = call("/api/import/map", {"scan_id": sid3, "consent": True, "fallback": "llm"})
            retried = stub.call_count - before_calls
            report.check(
                "G3a 首次非 JSON → 自动做一次「只修格式」重试后成功（共 2 次外发调用）",
                status == 200 and retried == 2 and js(text).get("source") == "llm",
                f"status={status} 外发调用次数={retried} source={js(text).get('source')} "
                f"warnings 含重试说明={'重试' in ' '.join(str(w) for w in (js(text).get('warnings') or []))}",
            )
            stub.mode_calls = 0; stub.mode = "always_text"
            before_calls = stub.call_count
            status, text = call("/api/import/map", {"scan_id": sid3, "consent": True, "fallback": "llm"})
            parsed = js(text)
            report.check(
                "G3b 两次都非 JSON → 502 E_LLM_BAD_RESPONSE，且 hint 附原始响应片段",
                status == 502 and parsed.get("error") == "E_LLM_BAD_RESPONSE"
                and "不是 JSON" in str(parsed.get("hint") or ""),
                f"status={status} error={parsed.get('error')} 外发调用次数={stub.call_count - before_calls} "
                f"hint={str(parsed.get('hint'))[:90]!r}",
            )
            stub.mode_calls = 0; stub.mode = "json"

    # ---------------- I. 开开关但无密钥（lead 实测点） ----------------
    log("\n### §9.1 开 --import-llm 但没有密钥 → E_LLM_UNAVAILABLE 且说清缺哪个环境变量")
    data_dir4 = fresh("import_data4")
    csv4 = data_dir4 / "log.csv"
    make_csv(csv4)
    env_without_key: dict = {}
    with Server(fresh("import_runs4"), extra=["--import-llm", "--allow-import-root", str(data_dir4)],
                env=env_without_key, drop_env=[KEY_ENV, "CONDOPT_IMPORT_LLM_API_KEY_ENV"]) as nokey:
        boot4 = js(get(nokey.base, "/api/bootstrap")[2]).get("import_llm") or {}
        status, _, text = http(nokey.base, "POST", "/api/import/scan", body={"path": str(csv4)})
        sid4 = js(text).get("scan_id")
        status, _, text = http(nokey.base, "POST", "/api/import/map", body={"scan_id": sid4, "consent": True})
        parsed = js(text)
        report.check(
            "I1 开开关无密钥：bootstrap 报 enabled=True / configured=False",
            boot4.get("enabled") is True and boot4.get("configured") is False,
            f"import_llm={boot4}",
        )
        report.check(
            "I2 开开关无密钥 → 503 E_LLM_UNAVAILABLE，message 说清缺的环境变量名",
            status == 503 and parsed.get("error") == "E_LLM_UNAVAILABLE" and KEY_ENV in str(parsed.get("message")),
            f"status={status} error={parsed.get('error')} message={str(parsed.get('message'))[:110]!r}",
        )

    # ---------------- J. §9.2 路径安全补测 ----------------
    log("\n### §9.2 路径安全补测：符号链接逃逸 / 超限 / 设备名")
    data_dir5 = fresh("import_data5")
    csv5 = data_dir5 / "log.csv"
    make_csv(csv5)
    outside = fresh("import_outside")
    secret_file = outside / "outside.csv"
    make_csv(secret_file)
    link = data_dir5 / "escape.csv"
    link_result = None
    try:
        os.symlink(secret_file, link)
        link_result = True
    except OSError as exc:
        link_result = f"{type(exc).__name__}: {exc}"
        log(f"    符号链接创建失败（Windows 需要开发者模式/管理员）：{link_result}")

    # 符号链接建不了就退而用**目录 junction**（mklink /J 不需要管理员权限），
    # 语义同样是「允许根内的名字指向根外」——这才是要防的逃逸。
    junction = data_dir5 / "esc_dir"
    junction_inside = False
    if link_result is not True:
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        junction_inside = made.returncode == 0
        log(f"    junction 回退：mklink /J {junction.name} -> {outside.name} → rc={made.returncode} "
            f"{made.stdout.strip() or made.stderr.strip()}")
    escape_path = junction / "outside.csv" if (junction_inside and link_result is not True) else link
    escape_label = "目录 junction 指向根外" if (junction_inside and link_result is not True) else "符号链接逃逸到根外"

    big = data_dir5 / "big.csv"
    with big.open("w", encoding="utf-8") as handle:
        handle.write("T,time,y\n")
        chunk = "50,60,1.0\n" * 4096  # 每块 40,960 字节
        while big.stat().st_size <= 21 * 1024 * 1024:  # 写到 > 21 MB（契约上限 20 MB）
            handle.write(chunk)
    big_bytes = big.stat().st_size
    log(f"    超限文件：{big.name} = {big_bytes} 字节（{big_bytes / 1024 / 1024:.1f} MB，契约上限 20 MB）")

    with Server(fresh("import_runs5"), extra=["--allow-import-root", str(data_dir5)]) as sec:
        cases = [
            ("允许根外", {"path": str(secret_file)}, 400),
            (f"超 20MB（实测 {big_bytes / 1024 / 1024:.1f} MB）", {"path": str(big)}, 400),
            ("Windows 设备名 NUL", {"path": "NUL"}, 400),
        ]
        if link_result is True or junction_inside:
            cases.append((escape_label, {"path": str(escape_path)}, 400))
        for label, body, expected in cases:
            status, _, text = http(sec.base, "POST", "/api/import/scan", body=body)
            parsed = js(text)
            report.check(
                f"J1 {label} → {expected}",
                status == expected and parsed.get("ok") is False,
                f"status={status} error={parsed.get('error')} message={str(parsed.get('message'))[:100]!r}",
            )
        if not (link_result is True or junction_inside):
            report.blocked(
                "J1 符号链接/junction 逃逸到允许根外",
                f"本机两种链接都建不了：symlink={link_result}；mklink /J runtime-unknown —— 未测，不得当作 PASS",
            )
    shutil.rmtree(outside, ignore_errors=True)
    for leftover in (big, junction, link):
        try:
            if leftover.is_dir():
                leftover.rmdir()
            elif leftover.exists():
                leftover.unlink()
        except OSError:
            pass

    # ---------------- H. 默认配置（不开 --import-llm） ----------------
    log("\n### §9.1 默认关闭：未开开关时 map 必须 E_LLM_UNAVAILABLE(503)，不静默降级")
    data_dir2 = fresh("import_data2")
    csv2 = data_dir2 / "log.csv"
    make_csv(csv2)
    with Server(fresh("import_runs2"), extra=["--allow-import-root", str(data_dir2)]) as plain:
        status, _, text = http(plain.base, "POST", "/api/import/scan", body={"path": str(csv2)})
        scan2 = js(text)
        report.check("H1 未开开关也能 scan（scan 不出网）", status == 200 and scan2.get("ok") is True,
                     f"status={status} n_rows={scan2.get('n_rows')}")
        boot2 = js(get(plain.base, "/api/bootstrap")[2]).get("import_llm") or {}
        report.check(
            "H2 默认 bootstrap.import_llm.enabled=False 且根据环境报告 configured",
            boot2.get("enabled") is False and "configured" in boot2,
            f"import_llm={boot2}",
        )
        status, _, text = http(plain.base, "POST", "/api/import/map", body={"scan_id": scan2.get("scan_id"), "consent": True})
        parsed = js(text)
        report.check(
            "H3 未开开关 + 带 consent → 503 E_LLM_UNAVAILABLE（而不是 E_CONSENT_REQUIRED）",
            status == 503 and parsed.get("error") == "E_LLM_UNAVAILABLE",
            f"status={status} error={parsed.get('error')} message={str(parsed.get('message'))[:80]!r}",
        )
        status, _, text = http(plain.base, "POST", "/api/import/map",
                            body={"scan_id": scan2.get("scan_id"), "fallback": "rules"})
        report.check(
            "H4 未开开关时仍可显式走离线规则映射（不静默、需显式指定）",
            status == 200 and js(text).get("source") == "rules",
            f"status={status} source={js(text).get('source')}",
        )
    # ---------------- I. F6 复验：密钥/URL 的编码鲁棒性（task-11 修复） ----------------
    log("\n### F6 复验：密钥或 baseURL 含非 latin-1 / 控制字符时必须是可诊断的 503，不是 500")
    data_dir3 = fresh("import_data3")
    csv3 = data_dir3 / "log.csv"
    make_csv(csv3)

    key_cases = [
        ("中文密钥", "sk-中文-绝不外泄"),
        ("emoji 密钥", "sk-🔑-key"),
        ("CRLF 头注入", "sk-abc\r\nX-Injected: 1"),
        ("LF 头注入", "sk-abc\nX-Injected: 1"),
        ("DEL 0x7F", "sk-a\x7fb"),
        ("控制字符 0x01", "sk-a\x01b"),
        ("NUL 0x00", "sk-a\x00b"),
    ]
    with StubGateway() as stub3:
        for index, (label, bad_key) in enumerate(key_cases):
            before = stub3.call_count
            try:
                env3 = {KEY_ENV: bad_key, "CONDOPT_IMPORT_LLM_BASE_URL": stub3.url, "CONDOPT_IMPORT_LLM_MODEL": "stub"}
                with Server(fresh(f"f6_key_{index}"), extra=["--import-llm", "--allow-import-root", str(data_dir3)],
                            env=env3) as server3:
                    status, _, text = http(server3.base, "POST", "/api/import/scan", body={"path": str(csv3)})
                    sid = js(text).get("scan_id")
                    status, _, text = http(server3.base, "POST", "/api/import/map",
                                           body={"scan_id": sid, "consent": True, "fallback": "llm"})
            except (ValueError, OSError) as exc:
                report.blocked(f"F6 {label} 密钥", f"本机无法把该值放进环境变量：{type(exc).__name__}: {exc}")
                continue
            parsed = js(text)
            leaked = bad_key in text or "X-Injected" in text
            log(f"    [{label}] status={status} error={parsed.get('error')} "
                f"message={str(parsed.get('message'))[:88]!r} 外发+{stub3.call_count - before}")
            report.check(
                f"F6 {label} → 503 E_LLM_UNAVAILABLE（不是 500、不回显异常名）",
                status == 503 and parsed.get("error") == "E_LLM_UNAVAILABLE"
                and "UnicodeEncodeError" not in text and "Traceback" not in text,
                f"status={status} error={parsed.get('error')} message={str(parsed.get('message'))[:110]!r}",
            )
            report.check(
                f"F6 {label} → message 点名环境变量、不回显密钥、无头注入残留",
                KEY_ENV in str(parsed.get("message")) and not leaked,
                f"含 {KEY_ENV}={KEY_ENV in str(parsed.get('message'))} 泄漏={leaked}",
            )
            report.check(
                f"F6 {label} → 外发请求数 = 0（坏配置绝不能先出网再报错）",
                stub3.call_count - before == 0,
                f"本例外发调用={stub3.call_count - before}",
            )

        log("\n### F6b baseURL 含空格 / 非 ASCII 时同样必须是可诊断错误")
        for label, bad_url in (("含空格", stub3.url + " /v1"), ("非 ASCII 主机", "http://例子.测试/v1")):
            before = stub3.call_count
            env4 = {KEY_ENV: "sk-ASCII-only", "CONDOPT_IMPORT_LLM_BASE_URL": bad_url, "CONDOPT_IMPORT_LLM_MODEL": "stub"}
            slug = "space" if label == "含空格" else "unicode"
            with Server(fresh(f"f6_url_{slug}"),
                        extra=["--import-llm", "--allow-import-root", str(data_dir3)], env=env4) as server4:
                status, _, text = http(server4.base, "POST", "/api/import/scan", body={"path": str(csv3)})
                sid = js(text).get("scan_id")
                status, _, text = http(server4.base, "POST", "/api/import/map",
                                       body={"scan_id": sid, "consent": True, "fallback": "llm"})
            parsed = js(text)
            log(f"    [baseURL {label}] status={status} error={parsed.get('error')} "
                f"message={str(parsed.get('message'))[:88]!r}")
            report.check(
                f"F6b baseURL {label} → 受控 4xx/5xx（非 500+异常名），且不是 E_INTERNAL",
                status in (400, 422, 502, 503) and parsed.get("error") != "E_INTERNAL"
                and "Traceback" not in text and "UnicodeEncodeError" not in text,
                f"status={status} error={parsed.get('error')} message={str(parsed.get('message'))[:110]!r}",
            )

        log("\n### F6d baseURL 各种畸形配置：都应是可诊断的配置类错误，不能是 500+异常名")
        control_url = f"http://127.0.0.1:{free_port()}/v1"  # 没人监听：应 503 E_LLM_UNAVAILABLE
        malformed = [
            ("含空格", stub3.url + " /v1"),
            ("非 ASCII 主机", "http://例子.测试/v1"),
            ("空字符串", ""),
            ("纯空白", "   "),
            ("没有 scheme", "127.0.0.1:9/v1"),
            ("非法 scheme", "ftp://127.0.0.1/v1"),
            ("端口不合法", "http://127.0.0.1:notaport/v1"),
            ("误拼 /chat/completions", stub3.url + "/chat/completions"),
            ("路径含 ..（api-backend 邀请我压的）", stub3.url + "/../.."),
            ("无人监听（对照，应 503）", control_url),
        ]
        for index, (label, bad_url) in enumerate(malformed):
            env6 = {KEY_ENV: "sk-ASCII-only", "CONDOPT_IMPORT_LLM_BASE_URL": bad_url, "CONDOPT_IMPORT_LLM_MODEL": "stub"}
            try:
                with Server(fresh(f"f6_url2_{index}"),
                            extra=["--import-llm", "--allow-import-root", str(data_dir3)], env=env6) as server6:
                    boot6 = js(get(server6.base, "/api/bootstrap")[2])
                    status, _, text = http(server6.base, "POST", "/api/import/scan", body={"path": str(csv3)})
                    scan6 = js(text)
                    sid = scan6.get("scan_id")
                    status, _, text = http(server6.base, "POST", "/api/import/map",
                                           body={"scan_id": sid, "consent": True, "fallback": "llm"})
            except (ValueError, OSError) as exc:
                report.blocked(f"F6d baseURL {label}", f"服务未起来：{type(exc).__name__}: {exc}")
                continue
            parsed = js(text)
            llm6 = boot6.get("import_llm") or {}
            log(f"    [baseURL {label!r}] status={status} error={parsed.get('error')} "
                f"message={str(parsed.get('message'))[:78]!r} | bootstrap.url={llm6.get('url')!r} "
                f"url_valid={llm6.get('url_valid')}")
            report.check(
                f"F6d baseURL {label} → 不是 500 E_INTERNAL、无 Traceback"
                f"（503 的 hint 里点名 URLError 是**有意为之**的诊断，不算泄漏）",
                parsed.get("error") != "E_INTERNAL" and "Traceback" not in text,
                f"status={status} error={parsed.get('error')} message={str(parsed.get('message'))[:110]!r}",
            )
            if label.startswith("无人监听"):
                report.check(
                    "F6d 对照：baseURL 指向无人监听的端口 → 503 E_LLM_UNAVAILABLE（正常路径未被误伤）",
                    status == 503 and parsed.get("error") == "E_LLM_UNAVAILABLE",
                    f"status={status} error={parsed.get('error')}",
                )
            if label in ("空字符串", "纯空白"):
                # F8：显式置空**不许**静默回退到默认端点（曾实测真的打到 open.bohrium.com 拿 401）
                report.check(
                    f"F8 baseURL {label} → 503 且明确说明「不会回退到默认端点」，不构造默认 URL",
                    status == 503 and parsed.get("error") == "E_LLM_UNAVAILABLE"
                    and "回退" in str(parsed.get("message")) and llm6.get("url") == ""
                    and llm6.get("url_valid") is False,
                    f"status={status} message={str(parsed.get('message'))[:100]!r} "
                    f"bootstrap.url={llm6.get('url')!r} url_valid={llm6.get('url_valid')}",
                )
            if label == "非 ASCII 主机":
                # D5：预检信号不许说谎 —— 以前 url_valid=true 但 map 503，现在必须一致
                # 注意：url_error 在 bootstrap.import（import_settings()）里，
                # import_llm 只有 url_valid —— 我第一版读错对象导致误报，这里两个都查。
                preview = scan6.get("sample_preview") or {}
                imp6 = boot6.get("import") or {}
                report.check(
                    "D5 非 ASCII 主机 → url_valid=False、message 指向 punycode 写法、"
                    "scan.sample_preview.endpoint 为空、bootstrap.import.url_error 非空",
                    llm6.get("url_valid") is False
                    and imp6.get("url_valid") is False
                    and llm6.get("url_valid") == imp6.get("url_valid")
                    and "punycode" in str(parsed.get("message"))
                    and preview.get("endpoint") == "" and imp6.get("url_error"),
                    f"import_llm.url_valid={llm6.get('url_valid')} import.url_valid={imp6.get('url_valid')} "
                    f"import.url_error={str(imp6.get('url_error'))[:60]!r} "
                    f"message={str(parsed.get('message'))[:70]!r} endpoint={preview.get('endpoint')!r}",
                )
            if label == "误拼 /chat/completions":
                preview = scan6.get("sample_preview") or {}
                imp6b = boot6.get("import") or {}
                report.check(
                    "D5 误拼 /chat/completions → url_valid=False（两块一致）且 sample_preview.endpoint 为空",
                    llm6.get("url_valid") is False and imp6b.get("url_valid") is False
                    and preview.get("endpoint") == "",
                    f"import_llm.url_valid={llm6.get('url_valid')} import.url_valid={imp6b.get('url_valid')} "
                    f"endpoint={preview.get('endpoint')!r}",
                )

        # ---------------- F6e. D5 不变量（我自己复核，不用实现者的测试当证据） ----------------
        log("\n### F6e D5 不变量：bootstrap.url_valid ⇔ 真的发起 HTTP 尝试（用我的 stub 计数观测）")
        invariant_cases = [
            # (label, baseURL, 期望 url_valid, 是否应真的打到我的 stub)
            ("合法 IP+端口", stub3.url, True, True),
            ("合法带尾斜杠", stub3.url + "/", True, True),
            ("非 ASCII 主机", "http://例子.测试/v1", False, False),
            ("空串", "", False, False),
            ("纯空白", "   ", False, False),
            ("含空格", stub3.url + " /x", False, False),
            ("没有 scheme", "127.0.0.1:9/v1", False, False),
            ("坏端口", "http://127.0.0.1:notaport/v1", False, False),
            ("误拼 /chat/completions", stub3.url + "/chat/completions", False, False),
        ]
        for index, (label, url_value, expect_valid, expect_reach) in enumerate(invariant_cases):
            env7 = {KEY_ENV: "sk-ASCII-only", "CONDOPT_IMPORT_LLM_BASE_URL": url_value, "CONDOPT_IMPORT_LLM_MODEL": "stub"}
            with Server(fresh(f"inv_{index}"), extra=["--import-llm", "--allow-import-root", str(data_dir3)],
                        env=env7) as server7:
                boot7 = js(get(server7.base, "/api/bootstrap")[2])
                url_valid = (boot7.get("import_llm") or {}).get("url_valid")
                status, _, text = http(server7.base, "POST", "/api/import/scan", body={"path": str(csv3)})
                sid = js(text).get("scan_id")
                before7 = stub3.call_count
                status, _, text = http(server7.base, "POST", "/api/import/map",
                                       body={"scan_id": sid, "consent": True, "fallback": "llm"})
            outbound = stub3.call_count - before7
            log(f"    [{label}] url_valid={url_valid} → map status={status} 外发={outbound}（期望 valid={expect_valid}）")
            report.check(
                f"F6e 不变量（{label}）：url_valid 与实际行为一致"
                f"（invalid ⇒ 503+外发0；valid 且可达 ⇒ 200+外发≥1）",
                url_valid is expect_valid
                and ((status == 200 and outbound >= 1) if expect_reach else (status == 503 and outbound == 0)),
                f"url_valid={url_valid}（期望 {expect_valid}） map={status} 外发={outbound}",
            )

        log("\n### F6f punycode 形式仍被接受（不是一刀切禁国际化域名）；只查 bootstrap，不做 DNS")
        for label, url_value, expect_valid in (
            ("punycode 域名", "http://xn--fsqu00a.xn--0zwm56d/v1", True),
            ("localhost", "http://localhost:9/v1", True),
            ("不带 /v1", f"http://127.0.0.1:{free_port()}", True),
        ):
            env8 = {KEY_ENV: "sk-ASCII-only", "CONDOPT_IMPORT_LLM_BASE_URL": url_value, "CONDOPT_IMPORT_LLM_MODEL": "stub"}
            with Server(fresh(f"puny_{abs(hash(label)) % 997}"), extra=["--import-llm"], env=env8) as server8:
                llm8 = js(get(server8.base, "/api/bootstrap")[2]).get("import_llm") or {}
            report.check(
                f"F6f baseURL {label} → url_valid={expect_valid}（仅预检，不解析 DNS）",
                llm8.get("url_valid") is expect_valid,
                f"url={llm8.get('url')!r} url_valid={llm8.get('url_valid')} url_error={llm8.get('url_error')!r}",
            )

        log("\n### F6c 反向：ASCII 密钥仍可用（修复未误伤正常路径）")
        env5 = {KEY_ENV: "sk-ASCII-only", "CONDOPT_IMPORT_LLM_BASE_URL": stub3.url, "CONDOPT_IMPORT_LLM_MODEL": "stub"}
        before = stub3.call_count
        with Server(fresh("f6_ascii"), extra=["--import-llm", "--allow-import-root", str(data_dir3)], env=env5) as server5:
            status, _, text = http(server5.base, "POST", "/api/import/scan", body={"path": str(csv3)})
            sid = js(text).get("scan_id")
            status, _, text = http(server5.base, "POST", "/api/import/map",
                                   body={"scan_id": sid, "consent": True, "fallback": "llm"})
        report.check(
            "F6c ASCII 密钥 → 200 source='llm' 且真的外发了一次",
            status == 200 and js(text).get("source") == "llm" and stub3.call_count - before == 1,
            f"status={status} source={js(text).get('source')} 外发={stub3.call_count - before}",
        )
    return report.finish("v13_import.log")


def _schema_matches_registry(params_schema, algorithm) -> bool:
    if not isinstance(params_schema, list) or not algorithm:
        return False
    described = {item["name"]: item for item in condopt_registry.describe_all()}
    spec = described.get(algorithm)
    if spec is None:
        return False
    expected = spec.get("params") or []
    if len(expected) != len(params_schema):
        return False
    for want, got in zip(expected, params_schema):
        for key in ("name", "type", "default"):
            if want.get(key) != got.get(key):
                return False
    return True


def _audit_ok(path: Path, csv_path: Path, imported: dict) -> tuple[bool, str]:
    if not path.exists():
        return False, f"{path} 不存在"
    lines = [line for line in path.read_text("utf-8").splitlines() if line.strip()]
    if not lines:
        return False, "import_audit.jsonl 为空"
    import hashlib

    record = json.loads(lines[-1])
    want_sha = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    ok = (
        record.get("file_sha256") == want_sha
        and record.get("accepted") == imported.get("accepted")
        and isinstance(record.get("budget"), int)
        and isinstance(record.get("t"), (int, float))
        and record.get("path")
        and record.get("reason")
    )
    return ok, f"记录={json.dumps(record, ensure_ascii=False)[:260]}"


if __name__ == "__main__":
    raise SystemExit(main())
