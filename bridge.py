"""condopt 工作台 · 进程内桥接层（唯一执行入口）。

唯一真源契约：``CONTRACT.md``（冻结 2026-11-19）。本模块遵守：

* **进程内调用** condopt 的 Python API —— 不起 subprocess、不解析 CLI 输出文本；
* ``Bootstrap.algorithms`` 来自 :func:`condopt.registry.describe_all`，
  ``prepare_template`` 来自 :func:`condopt.prepare.template`，**绝不手写**（防「文档一套、代码一套」）；
* 所有写动作共用**一把全局** :class:`threading.RLock`，且所有写入路径都在 ``--runs-dir`` 之下；
* runs 目录是唯一真源，本层不引入第二份状态。

三个入口：

``Bridge.bootstrap()`` / ``Bridge.list_runs()`` / ``Bridge.study_snapshot()``  只读视图；
``Bridge.dispatch(action, run, payload)``                                     闭环动作（写动作加锁）；
``Bridge.stream_scenario(scenario, payload)``                                 SSE 帧生成器（观感通道）。

SSE 只是观感：``/api/run`` 走的仍是 ``dispatch``，权威结果以 ``/api/action`` 为准（契约 §4）。
"""

from __future__ import annotations

import csv
import hashlib
import http.client
import io
import json
import math
import os
import re
import shutil as _shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parent  # 本仓库根（server.py / bridge.py 就在根上）


def _ensure_condopt_importable() -> Path | None:
    """让 ``import condopt`` 成立；返回实际使用的源码目录（已安装则为 None）。

    condopt 是**外部依赖**，不是本仓库的子目录：优先用已安装的包；否则依次尝试
    ``CONDOPT_SRC`` 环境变量、同级的 ``condopt-router`` / ``Project6-skill-Opt_of_Cpndition``
    检出。这样工作台与 condopt 本体保持独立，又能在开发机上直接对着源码跑。
    """
    try:
        import condopt  # noqa: F401

        return None
    except ModuleNotFoundError:
        pass
    candidates: list[Path] = []
    env_src = os.environ.get("CONDOPT_SRC")
    if env_src:
        candidates.append(Path(env_src).expanduser())
    for sibling in ("condopt-router", "Project6-skill-Opt_of_Cpndition"):
        candidates.append(ROOT.parent / sibling / "src")
        candidates.append(ROOT.parent / sibling)
    for candidate in candidates:
        if (candidate / "condopt" / "__init__.py").is_file():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return candidate
    raise ModuleNotFoundError(
        "找不到 condopt：请先 `pip install -r requirements.txt`，或用 CONDOPT_SRC 指向 "
        "condopt 的 src 目录，或把 condopt-router 检出放在本仓库同级目录下。"
    )


CONDOPT_SRC = _ensure_condopt_importable()

import numpy as np  # noqa: E402

import condopt  # noqa: E402
from condopt import prepare as condopt_prepare  # noqa: E402
from condopt import registry as condopt_registry  # noqa: E402
from condopt import render as condopt_render  # noqa: E402
from condopt.base import Objective  # noqa: E402
from condopt.errors import CondoptError  # noqa: E402
from condopt.router import route as condopt_route  # noqa: E402
from condopt.schema import validate_answers as condopt_validate_answers  # noqa: E402
from condopt.space import SearchSpace  # noqa: E402
from condopt.study import PendingError, Study  # noqa: E402

# --------------------------------------------------------------------------- #
# 常量（Scenario 由后端定义、前端消费；stages 由 CONTRACT §4.1 表冻结）
# --------------------------------------------------------------------------- #

DEFAULT_RUNS_DIR = ROOT / "state" / "runs"

#: 契约 §4.1 的权威 Scenario 表。``/api/bootstrap`` 与 ``/api/run`` 消费同一份常量，
#: 绝不在两处各写一遍（否则 stages 漂移会让前端 renderStages() 点错灯）。
SCENARIOS: list[dict] = [
    {
        "key": "kickoff",
        "no": "01",
        "title": "开工建议",
        "tagline": "六段开工建议：算法·停止·可行率·先验·交付口径·噪声处置",
        "short": "开工",
        "action": "prepare",
        "stages": ["校验问卷答案", "解析条件空间", "路由算法", "生成六段建议"],
        "needs_run": False,
    },
    {
        "key": "sample",
        "no": "02",
        "title": "下一批取样",
        "tagline": "按当前观测给出下一批待测条件（含孔板/孔位）",
        "short": "取样",
        "action": "ask",
        "stages": ["校验 study 与待回填状态", "读取已有观测", "调用 ask 生成下一批", "写入 pending 与孔板"],
        "needs_run": True,
    },
    {
        "key": "feedback",
        "no": "03",
        "title": "结果回填",
        "tagline": "把实测结果回填进 dataset 与 rounds，清空 pending",
        "short": "回填",
        "action": "tell",
        "stages": ["解析回填结果", "校验 id 与约束", "写入 dataset 与 rounds", "确认 pending 清空"],
        "needs_run": True,
    },
    {
        "key": "delivery",
        "no": "04",
        "title": "交付自查",
        "tagline": "交付清单 + 完整性自检 + 收尾建议",
        "short": "交付",
        "action": "delivery",
        "stages": ["汇总交付清单", "跑完整性自检", "给出收尾建议", "生成可交付摘要"],
        "needs_run": True,
    },
    {
        "key": "board",
        "no": "05",
        "title": "看板",
        "tagline": "渲染离线看板 dashboard.html（收敛/覆盖/前沿）",
        "short": "看板",
        "action": "render",
        "stages": ["读取 run 目录", "渲染收敛与前沿", "写入 dashboard.html", "检查离线资源"],
        "needs_run": True,
    },
    {
        # §9.6：前端新增的导入场景。它**没有** §4 的 SSE/action 通道（三个步骤是
        # scan → map → apply），这里暴露元数据是为了 data.js ↔ /api/bootstrap 一致；
        # /api/run 收到该 scenario 会明确回 400 指向 /api/import/*（见 validate_run_request）。
        "key": "import",
        "no": "06",
        "title": "导入数据",
        "tagline": "粘路径 → 读取预览 → 确认列角色 → 让模型读表 → 作先验导入并落调参表单",
        "short": "导入",
        "action": "import",
        "stages": ["读取文件", "画像与列类型", "让模型读表", "导入为先验"],
        "needs_run": False,
    },
]

SCENARIOS_BY_KEY: dict[str, dict] = {item["key"]: item for item in SCENARIOS}

#: 契约 §2.1 的档位 → 预算（工作台侧展示用常量，不是 condopt 的事实）。
EFFORTS: dict[str, int] = {"quick": 40, "normal": 160, "thorough": 640}

# --- 导入已有数据（CONTRACT §9）------------------------------------------------ #
#: 只读这些后缀的文件（§9.2）。
IMPORT_SUFFIXES = (".csv", ".tsv", ".txt", ".json", ".jsonl")
#: 单文件大小上限默认 20 MB（CLI 可用 --max-import-bytes 覆盖）。
DEFAULT_MAX_IMPORT_BYTES = 20 * 1024 * 1024
#: 发给模型的最大样本行数（§9.4：默认 12，上限 50）——**绝不发整文件**。
DEFAULT_IMPORT_SAMPLE_ROWS = 12
MAX_IMPORT_SAMPLE_ROWS = 50
#: 本机既有网关（§9.1，复用不新建）；三个环境变量可覆盖。
DEFAULT_LLM_BASE_URL = "https://open.bohrium.com/openapi/model-gateway/v1"
DEFAULT_LLM_API_KEY_ENV = "BOHRIUM_API_KEY"
DEFAULT_LLM_MODEL = "qwen3.8-27b"
LLM_TIMEOUT_SECONDS = 60.0
#: condopt 支持的变量 kind（§9.4；非法值降级 + warning，不静默丢弃）。
SUPPORTED_KINDS = ("continuous", "integer", "log", "categorical", "bool", "ordinal")
#: 约束比较符白名单。
SUPPORTED_OPS = ("<=", ">=", "<", ">", "==")
#: 表里这些列不进入条件空间（编号/板号/备注/来源等执行元数据）。
NON_CONDITION_COLUMNS = {
    "id", "index", "no", "row", "batch", "plate", "well", "note", "notes", "comment",
    "remark", "source", "date", "timestamp", "actor", "created_at", "updated_at",
}
#: 目标列的优先猜测（按顺序匹配列名，命中即用）。
OBJECTIVE_HINTS = (
    "yield", "score", "objective", "target", "value", "result", "response", "output",
    "best", "metric", "y",
)

#: 写动作（契约 §3：写操作全局串行）；其余动作为只读。
WRITE_ACTIONS = frozenset({"init", "ask", "tell", "override", "render"})

#: 每个 action 允许的 payload 字段。未知字段**不静默忽略**（契约 §3）。
ACTION_PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    "prepare": frozenset({"answers"}),
    "init": frozenset({"id", "answers", "algorithm", "budget", "batch", "params"}),
    "ask": frozenset({"n", "plate"}),
    "tell": frozenset({"observations", "csv"}),
    "step": frozenset(),
    "recommend": frozenset(),
    "delivery": frozenset(),
    "override": frozenset({"algorithm", "reason", "params"}),
    "render": frozenset({"make_png"}),
    "report": frozenset(),
}

#: 需要 ``run`` 的 action（``init`` 自带 ``id``，``prepare`` 不落盘）。
NEEDS_RUN = frozenset({"ask", "tell", "step", "recommend", "delivery", "override", "render", "report"})

E_BAD_REQUEST = "E_BAD_REQUEST"
E_NOT_FOUND = "E_NOT_FOUND"
E_CONFLICT = "E_CONFLICT"
E_VALIDATION = "E_VALIDATION"
E_INTERNAL = "E_INTERNAL"
#: 导入功能（CONTRACT §9）的专用错误码。
E_LLM_UNAVAILABLE = "E_LLM_UNAVAILABLE"
E_LLM_BAD_RESPONSE = "E_LLM_BAD_RESPONSE"
E_CONSENT_REQUIRED = "E_CONSENT_REQUIRED"

ERROR_STATUS: dict[str, int] = {
    E_BAD_REQUEST: 400,
    E_NOT_FOUND: 404,
    E_CONFLICT: 409,
    E_VALIDATION: 422,
    E_INTERNAL: 500,
    E_LLM_UNAVAILABLE: 503,
    E_LLM_BAD_RESPONSE: 502,
    E_CONSENT_REQUIRED: 400,
}


class WorkbenchError(Exception):
    """工作台错误：错误码 + 一句话 message + 可执行 hint（契约 §2 错误模型）。

    ``extra`` 用于契约要求的**结构化**补充字段（例如 §9.5 的 ``suggested_budget``），
    会被合并进错误响应体的顶层。
    """

    def __init__(
        self,
        error: str,
        message: str,
        hint: str = "",
        status: int | None = None,
        extra: Mapping | None = None,
    ) -> None:
        super().__init__(message)
        self.error = error
        self.message = str(message)
        self.hint = str(hint or "")
        self.status = int(status if status is not None else ERROR_STATUS.get(error, 500))
        self.extra = dict(extra or {})

    def to_dict(self) -> dict:
        payload = {"ok": False, "error": self.error, "message": self.message}
        if self.hint:
            payload["hint"] = self.hint
        payload.update(_to_jsonable(self.extra))
        return payload


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #


#: run id（study 目录名）长度上限。目录名过长会撞 Windows 路径/名称限制，报成 500 不合格，
#: 因此在入口就拒绝（task-4 F2 的明确上限）。
MAX_RUN_ID_LENGTH = 64

#: Windows 上**真正无法**作为目录存在的保留设备名。
#: 判据是「实测会炸」而不是「看起来像设备名」：独立验收（task-3）逐个实测 CON / AUX /
#: COM1 / PRN / LPT1 / clock$ 在本机都能正常建目录、能出现在 /api/runs、能 GET /api/study，
#: 所以**不做整表封禁**（封了就是误伤）；只有 NUL（含 ``NUL.txt`` 这类 NTFS 设备别名形式）
#: 被证实必然失败（FileNotFoundError: ...\NUL\space.json），才在这里提前拒绝。
_UNCREATABLE_RUN_ID = re.compile(r"(?i)^nul(\..*)?$")


def _log(message: str) -> None:
    """把原始异常写到 stderr（服务日志），避免把 Python 异常文本回显给 HTTP 客户端。"""
    sys.stderr.write(f"[workbench] {message}\n")
    sys.stderr.flush()


#: 错误 message 里回显客户端输入时的展示上限（task-7）。超长 id 原样回显会把响应和日志撑大
#: （实测 5003 字符），因此展示统一截断成「前 40 字符…后 10 字符」= 51 字符：保留头尾就足以
#: 认出是哪个 id（头能认出前缀、尾能认出 .bak/后缀之类），又不会泄漏整串。**只截断展示**，
#: 判定（400 非法 / 404 不存在）用的始终是原始值。
_DISPLAY_LIMIT = 60
_DISPLAY_HEAD = 40
_DISPLAY_TAIL = 10


def _display(value: Any) -> str:
    """客户端输入的展示形态：短值原样，长值 ``前40…后10``（见 :data:`_DISPLAY_LIMIT`）。"""
    text = value if isinstance(value, str) else repr(value)
    if len(text) <= _DISPLAY_LIMIT:
        return text
    return f"{text[:_DISPLAY_HEAD]}…{text[-_DISPLAY_TAIL:]}"


def _display_list(values: Any, limit: int = 5) -> str:
    """字段名列表的展示形态：最多 ``limit`` 个，其余折成 ``…(共 N 个)``。"""
    items = [str(item) for item in values]
    if len(items) <= limit:
        return ", ".join(items)
    return ", ".join(items[:limit]) + f"…(共 {len(items)} 个)"


def _to_jsonable(value: Any) -> Any:
    """把 condopt 返回值转成严格 JSON 可序列化的结构。

    numpy 标量、``Path``、非有限浮点（NaN/Inf 不是合法 JSON，会让前端
    ``JSON.parse`` 直接抛错）都在这里归一化；缺数据一律 ``None``，不伪造。
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(item) for item in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _to_jsonable(value.to_dict())
    return str(value)


def json_dumps(payload: Any) -> str:
    """严格 JSON：UTF-8、``ensure_ascii=False``、拒绝 NaN/Infinity。"""
    return json.dumps(_to_jsonable(payload), ensure_ascii=False, allow_nan=False)


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def condopt_version() -> str:
    return str(getattr(condopt, "__version__", "0.0.0"))


def _local_condopt_error(exc: BaseException) -> WorkbenchError:
    """把 condopt 的异常映射成契约错误码（错误只在这里翻译，别处不猜）。"""
    hint = getattr(exc, "hint", "") or ""
    message = getattr(exc, "message", None) or str(exc)
    return WorkbenchError(E_VALIDATION, str(message), str(hint))


# --------------------------------------------------------------------------- #
# Bridge
# --------------------------------------------------------------------------- #


class Bridge:
    """进程内调用 condopt；写操作全局串行，写入路径只在 runs-dir 下。"""

    def __init__(
        self,
        runs_dir: str | Path | None = None,
        *,
        allow_import_roots: Iterable[str | Path] | None = None,
        max_import_bytes: int | None = None,
        import_llm: bool = False,
        llm_base_url: str | None = None,
        llm_api_key_env: str | None = None,
        llm_model: str | None = None,
    ) -> None:
        self.runs_dir = Path(runs_dir).resolve() if runs_dir else Path(DEFAULT_RUNS_DIR).resolve()
        #: 写动作专用（契约 §0：一把全局锁）。RLock 让只读视图也能安全地共享同一临界区。
        self._lock = threading.RLock()
        # --- 导入（§9.2 / §9.1）---
        roots = list(allow_import_roots) if allow_import_roots else [ROOT, self.runs_dir]
        self.allow_import_roots = tuple(Path(item).resolve() for item in roots)
        self.max_import_bytes = int(
            max_import_bytes if max_import_bytes is not None else DEFAULT_MAX_IMPORT_BYTES
        )
        #: 出网开关：只有 --import-llm 打开 **且** 环境变量有密钥时 map 才会请求网关（§9.1）。
        self.import_llm = bool(import_llm)
        env_base_url = os.environ.get("CONDOPT_IMPORT_LLM_BASE_URL")
        if llm_base_url is not None:
            raw_base_url = llm_base_url
        elif env_base_url is None:
            raw_base_url = DEFAULT_LLM_BASE_URL  # 未设置 → 默认端点
        else:
            raw_base_url = env_base_url  # 显式设置（哪怕是空串）→ 原样采用，不静默回退（F8）
        self.llm_base_url_raw = raw_base_url
        self.llm_base_url_error = _base_url_error(raw_base_url)
        #: 生效地址：合法时去掉尾斜杠；不合法时原样保留（供 bootstrap 展示，map 会 503）。
        self.llm_base_url = (
            raw_base_url.strip()
            if self.llm_base_url_error
            else raw_base_url.strip().rstrip("/")
        )
        self.llm_api_key_env = (
            llm_api_key_env or os.environ.get("CONDOPT_IMPORT_LLM_API_KEY_ENV") or DEFAULT_LLM_API_KEY_ENV
        )
        self.llm_model = llm_model or os.environ.get("CONDOPT_IMPORT_LLM_MODEL") or DEFAULT_LLM_MODEL
        #: scan_id → 扫描结果（同进程内最近几次成功 scan；§9.4 用它防「扫 A 打 B」）。
        self._scans: dict[str, dict] = {}
        #: scan_id → 最近一次 map 的来源（给 apply 的审计记录用）。
        self._maps: dict[str, dict] = {}

    # -- 只读视图 -----------------------------------------------------------
    def import_settings(self) -> dict:
        """导入功能开关/配置的可公开部分（§9.1/§2.1：只暴露「是否已配置」，绝不回显密钥）。"""
        return {
            "enabled": self.import_llm,
            "configured": bool(os.environ.get(self.llm_api_key_env)),
            "url": self.llm_base_url,
            "url_valid": self.llm_base_url_error is None,
            "url_error": self.llm_base_url_error,
            "model": self.llm_model,
            "key_env": self.llm_api_key_env,
            "max_import_bytes": self.max_import_bytes,
            "sample_rows_default": DEFAULT_IMPORT_SAMPLE_ROWS,
            "sample_rows_max": MAX_IMPORT_SAMPLE_ROWS,
            "supports": list(IMPORT_SUFFIXES),
            "allow_import_roots": [str(item) for item in self.allow_import_roots],
        }

    # -- 路径安全 -----------------------------------------------------------
    def _run_dir(
        self,
        run_id: Any,
        *,
        must_exist: bool = True,
        creating: bool = False,
    ) -> Path:
        """把 run id 解析成 runs-dir 下的目录；穿越/绝对路径/设备别名一律 400。

        ``creating=True`` 额外施加**创建期**约束（``MAX_RUN_ID_LENGTH`` 长度上限）。
        这些限制的目的是防「新建目录」时文件系统报错，所以只对 ``init`` 新目录生效；
        读取既有 study（``must_exist=True``）只做安全校验，**不套长度上限**——
        否则 condopt 本体建的 >64 字符 study 会「列表可见但 /api/study 打不开」
        （task-6 F4 裁定 (b)：工作台的核心用途就是操作既有 study）。
        """
        if not isinstance(run_id, str) or not run_id.strip():
            raise WorkbenchError(
                E_BAD_REQUEST,
                "缺少 run（study id）",
                "先用 GET /api/runs 看可用 study",
            )
        name = run_id.strip()
        if (
            name in {".", ".."}
            or "\x00" in name
            or "/" in name
            or "\\" in name
            or ":" in name
            or Path(name).name != name
        ):
            raise WorkbenchError(
                E_BAD_REQUEST,
                f"非法 run id: {_display(run_id)!r}",
                "run id 只能是 runs 目录下的目录名，不接受路径分隔符或 ..",
            )
        if _UNCREATABLE_RUN_ID.match(name):
            # task-4 F1：NUL 是 NTFS 设备别名，建目录/写文件都会失败；读路径同样拒（一定不存在）。
            # 其余“像设备名”的名字（CON/AUX/COM1/...）在本机实测可用，因此不在此列。
            raise WorkbenchError(
                E_BAD_REQUEST,
                f"非法 run id: {_display(name)!r}（Windows 保留设备名，无法作为目录）",
                "换一个普通目录名（NUL 在 NTFS 上是设备别名，不能建目录/写文件）",
            )
        if creating and len(name) > MAX_RUN_ID_LENGTH:
            # task-4 F2：超长目录名会撞文件系统限制并以 OSError 形式变成 500，这里提前拒绝并给出上限。
            raise WorkbenchError(
                E_BAD_REQUEST,
                f"run id 过长（{len(name)} 字符 > 上限 {MAX_RUN_ID_LENGTH}）",
                f"新建 run 的 id 只接受 ≤{MAX_RUN_ID_LENGTH} 字符的目录名"
                "（读取既有 study 不受此上限约束）",
            )
        target = (self.runs_dir / name).resolve()
        if target.parent != self.runs_dir:
            raise WorkbenchError(
                E_BAD_REQUEST, f"非法 run id: {_display(run_id)!r}", "路径必须落在 runs 目录下"
            )
        if must_exist:
            if not target.is_dir() or not (target / "problem.json").exists():
                raise WorkbenchError(
                    E_NOT_FOUND,
                    f"run 不存在: {_display(name)}",
                    "先用 GET /api/runs 看可用 study（或 POST /api/action {init} 建一个）",
                )
        return target

    def _ensure_runs_dir(self) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def _import_path(self, raw: Any) -> Path:
        """§9.2 路径安全：允许根内、普通文件、后缀白名单、大小上限、resolve() 后不逃逸。"""
        if not isinstance(raw, str) or not raw.strip():
            raise WorkbenchError(
                E_BAD_REQUEST,
                "缺少 path（要导入的文件路径）",
                '例如 {"path": "data/lab_log.csv"}；相对路径按第一个允许根解析',
            )
        text = raw.strip()
        if len(text) > 4096:
            raise WorkbenchError(E_BAD_REQUEST, "path 过长", "给一个真实的本地文件路径即可")
        lowered = text.lower()
        if text.startswith(("\\\\", "//")) or lowered.startswith(("\\\\.\\", "\\\\?\\")):
            raise WorkbenchError(E_BAD_REQUEST, "不接受 UNC/设备路径", "只允许本机允许根内的普通文件")
        candidate = Path(text)
        if not candidate.is_absolute():
            candidate = self.allow_import_roots[0] / candidate
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError) as exc:
            raise WorkbenchError(E_BAD_REQUEST, f"路径无法解析: {_display(text)}", str(exc)[:200]) from exc
        if not any(resolved == root or root in resolved.parents for root in self.allow_import_roots):
            raise WorkbenchError(
                E_BAD_REQUEST,
                f"路径不在允许根内: {_display(str(resolved))}",
                "允许根: " + "；".join(str(item) for item in self.allow_import_roots)
                + "（可用 --allow-import-root 增加）",
            )
        if resolved.is_dir():
            raise WorkbenchError(E_BAD_REQUEST, f"路径是目录，不是文件: {_display(str(resolved))}")
        if not resolved.is_file():
            # 设备名（NUL 等）/ 失效符号链接 / 其它特殊文件都不是普通数据文件。
            raise WorkbenchError(E_BAD_REQUEST, f"路径不是普通文件: {_display(str(resolved))}")
        suffix = resolved.suffix.lower()
        if suffix not in IMPORT_SUFFIXES:
            raise WorkbenchError(
                E_BAD_REQUEST,
                f"不支持的后缀 {_display(suffix)}",
                f"支持: {', '.join(IMPORT_SUFFIXES)}",
            )
        size = resolved.stat().st_size
        if size > self.max_import_bytes:
            raise WorkbenchError(
                E_BAD_REQUEST,
                f"文件过大（{size} 字节 > 上限 {self.max_import_bytes}）",
                "可用 --max-import-bytes 提高上限",
            )
        return resolved

    # -- 只读视图 -----------------------------------------------------------
    def bootstrap(self) -> dict:
        """首屏元数据（契约 §2.1）。algorithms / prepare_template 来自代码。"""
        return {
            "ok": True,
            "condopt": {
                "version": condopt_version(),
                "runs_dir": str(self.runs_dir),
                "algorithms": _to_jsonable(condopt_registry.describe_all()),
            },
            "prepare_template": _to_jsonable(condopt_prepare.template()),
            "scenarios": _to_jsonable(SCENARIOS),
            "efforts": dict(EFFORTS),
            # §9.1/§2.1：只暴露「开关/是否已配置/环境变量名/URL/模型/上限」，绝不回显密钥。
            "import_llm": {
                "enabled": self.import_llm,
                "configured": bool(os.environ.get(self.llm_api_key_env)),
                "url": self.llm_base_url,
                "url_valid": self.llm_base_url_error is None,
                "model": self.llm_model,
                "key_env": self.llm_api_key_env,
            },
            "import": self.import_settings(),
        }

    def _run_summary(self, study: Study) -> dict:
        dataset = study.dataset
        best = study.best()
        return {
            "id": study.dir.name,
            "n_obs": len(dataset),
            "n_rounds": len(study.rounds),
            "best": _to_jsonable(
                dataset.best_score(study.objective.name, study.objective.direction)
            ),
            "direction": study.objective.direction,
            "algorithm": study.algorithm,
            "remaining": study.remaining(),
            "budget": study.budget,
            "has_pending": bool(study.pending),
            "best_condition": None if best is None else _to_jsonable(best.x),
        }

    def list_runs(self) -> list[dict]:
        """runs-dir 下所有含 ``problem.json`` 的子目录（按名字排序）。"""
        with self._lock:
            if not self.runs_dir.is_dir():
                return []
            summaries: list[dict] = []
            for child in sorted(self.runs_dir.iterdir(), key=lambda path: path.name.lower()):
                if not child.is_dir() or not (child / "problem.json").exists():
                    continue
                try:
                    summaries.append(self._run_summary(Study.load(child)))
                except Exception as exc:  # noqa: BLE001 - 一个坏 run 不该拖垮列表
                    summaries.append(
                        {
                            "id": child.name,
                            "n_obs": None,
                            "n_rounds": None,
                            "best": None,
                            "direction": None,
                            "algorithm": None,
                            "remaining": None,
                            "budget": None,
                            "has_pending": None,
                            "best_condition": None,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
            return summaries

    def study_snapshot(self, run_id: Any) -> dict:
        """单个 study 快照（契约 §2.2；字段全在，缺数据 null/[]）。"""
        run_dir = self._run_dir(run_id)
        with self._lock:
            study = Study.load(run_dir)
            best = study.best()
            try:
                step = _to_jsonable(study.next_action())
            except Exception as exc:  # noqa: BLE001 - 快照尽量给全量字段，坏字段留给 integrity 说
                step = {"error": f"{type(exc).__name__}: {exc}"}
            try:
                recommend = None if best is None else _to_jsonable(study.recommend())
            except Exception as exc:  # noqa: BLE001
                recommend = {"error": f"{type(exc).__name__}: {exc}"}
            return {
                "id": study.dir.name,
                "run_dir": str(study.dir),
                "problem": _to_jsonable(_read_json(study.dir / "problem.json", {})),
                "config": _to_jsonable(_read_json(study.dir / "config.json", {})),
                "n_obs": len(study.dataset),
                "remaining": study.remaining(),
                "cost_spent": study.cost_spent(),
                "best": None if best is None else _to_jsonable(best.to_dict()),
                "rounds": _to_jsonable(study.rounds),
                "pending": _to_jsonable(study.pending),
                "decisions": _to_jsonable(study.decisions()),
                "step": step,
                "recommend": recommend,
                "delivery": _to_jsonable(study.delivery_checklist()),
                "integrity": _to_jsonable(study.check_integrity()),
            }

    # -- payload 校验 -------------------------------------------------------
    def _check_payload(self, action: str, payload: Any) -> dict:
        allowed = ACTION_PAYLOAD_FIELDS[action]
        if payload is None:
            payload = {}
        if not isinstance(payload, Mapping):
            raise WorkbenchError(E_BAD_REQUEST, "payload 必须是 JSON 对象", '例如 {"payload": {}}')
        unknown = sorted(str(key) for key in payload if str(key) not in allowed)
        if unknown:
            raise WorkbenchError(
                E_VALIDATION,
                f"{action} 的 payload 未知字段: {_display_list(unknown)}",
                f"允许字段: {sorted(allowed) or '（空对象）'}",
            )
        cleaned = {str(key): value for key, value in payload.items()}
        self._check_required(action, cleaned)
        return cleaned

    def _check_required(self, action: str, payload: dict) -> None:
        def need(name: str) -> Any:
            if name not in payload or payload[name] is None:
                raise WorkbenchError(E_VALIDATION, f"{action} 缺少 payload.{name}", f"必填字段: {name}")
            return payload[name]

        if action == "prepare":
            if not isinstance(need("answers"), Mapping):
                raise WorkbenchError(E_VALIDATION, "answers 必须是 JSON 对象", "形状见 prepare_template")
        elif action == "init":
            run_id = need("id")
            if not isinstance(run_id, str) or not run_id.strip():
                raise WorkbenchError(E_VALIDATION, "init 的 id 必须是非空字符串", "例如 {\"id\": \"demo\"}")
            self._run_dir(run_id, must_exist=False, creating=True)
            if not isinstance(need("answers"), Mapping):
                raise WorkbenchError(E_VALIDATION, "answers 必须是 JSON 对象", "形状见 prepare_template")
            if "algorithm" in payload and not isinstance(payload["algorithm"], str):
                raise WorkbenchError(E_VALIDATION, "algorithm 必须是字符串", "例如 \"auto\"/\"sobol\"/\"bayes\"")
            for key in ("budget", "batch"):
                if key in payload and payload[key] is not None:
                    if isinstance(payload[key], bool) or not isinstance(payload[key], int):
                        raise WorkbenchError(E_VALIDATION, f"{key} 必须是整数", f"{key} 例如 24")
        elif action == "ask":
            if "n" in payload and payload["n"] is not None:
                if isinstance(payload["n"], bool) or not isinstance(payload["n"], int) or payload["n"] < 1:
                    raise WorkbenchError(E_VALIDATION, "n 必须是 >=1 的整数", "n 例如 4")
            if "plate" in payload and payload["plate"] is not None:
                if not isinstance(payload["plate"], str):
                    raise WorkbenchError(E_VALIDATION, "plate 必须是字符串", "plate 例如 \"P1\"")
        elif action == "tell":
            has_obs, has_csv = payload.get("observations") is not None, payload.get("csv") is not None
            if has_obs == has_csv:
                raise WorkbenchError(
                    E_VALIDATION,
                    "tell 需要 observations 与 csv 二选一",
                    '例如 {"observations": [{"id": "r1-1", "y": 12.3}]}',
                )
            if has_obs and not isinstance(payload["observations"], list):
                raise WorkbenchError(E_VALIDATION, "observations 必须是数组", "每个元素至少含 id 或 x")
            if has_csv and not isinstance(payload["csv"], str):
                raise WorkbenchError(E_VALIDATION, "csv 必须是字符串", "CSV 文本，表头形如 id,x.T,y")
        elif action == "override":
            algorithm = need("algorithm")
            if not isinstance(algorithm, str):
                raise WorkbenchError(E_VALIDATION, "algorithm 必须是字符串", "用 GET /api/bootstrap 看可选算法")
            if algorithm not in condopt_registry.names():
                raise WorkbenchError(
                    E_VALIDATION,
                    f"未知算法: {_display(algorithm)}",
                    f"可用: {', '.join(condopt_registry.names())}",
                )
            reason = need("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise WorkbenchError(E_VALIDATION, "override 必须给非空 reason", "审计要求：为什么换算法")
            if payload.get("params") is not None and not isinstance(payload["params"], Mapping):
                raise WorkbenchError(E_VALIDATION, "params 必须是 JSON 对象", "例如 {\"acquisition\": \"ucb\"}")

    def validate_action(self, action: str, run: Any = None, payload: Any = None) -> dict:
        """只校验、不执行（SSE 的第 0 段用；语义与 dispatch 完全一致）。"""
        if not isinstance(action, str) or action not in ACTION_PAYLOAD_FIELDS:
            raise WorkbenchError(
                E_BAD_REQUEST,
                f"未知 action: {_display(action)!r}",
                f"可用: {', '.join(sorted(ACTION_PAYLOAD_FIELDS))}",
            )
        cleaned = self._check_payload(action, payload)
        if action in NEEDS_RUN:
            self._run_dir(run)
        return cleaned

    def validate_run_request(self, scenario_key: Any, payload: Any = None) -> dict:
        """校验 ``POST /api/run`` 的 scenario + payload（预校验，未开流前返回 4xx）。

        ``run`` 允许放在 payload 里（EduKit 请求体形状，契约 §4），这里只做拆包与白名单
        校验；真正的执行仍在 :meth:`stream_scenario` → :meth:`dispatch`。
        """
        scenario = SCENARIOS_BY_KEY.get(scenario_key) if isinstance(scenario_key, str) else None
        if scenario is None:
            raise WorkbenchError(
                E_BAD_REQUEST,
                f"未知 scenario: {_display(scenario_key)!r}",
                f"可用: {', '.join(SCENARIOS_BY_KEY)}",
            )
        if payload is None:
            payload = {}
        if not isinstance(payload, Mapping):
            raise WorkbenchError(E_BAD_REQUEST, "payload 必须是 JSON 对象", '例如 {"payload": {}}')
        inner = {str(key): value for key, value in payload.items() if str(key) != "run"}
        run_id = payload.get("run")
        if scenario["needs_run"]:
            self._run_dir(run_id)  # 缺 run / run 不存在都在开流前报错
        if scenario["action"] == "import":
            # 导入不是单动作：它由 scan → map → apply 三步组成，没有 §4 的 SSE 通道。
            # 显式报错好过让前端误以为「流式失败 → 回退 mock」。
            raise WorkbenchError(
                E_BAD_REQUEST,
                "import 场景没有 SSE 通道（它由 scan → map → apply 三步组成）",
                "用 POST /api/import/scan → /api/import/map → /api/import/apply；"
                "前端 assets/import.js 已按此实现",
            )
        self.validate_action(scenario["action"], run_id, inner)
        return dict(payload)

    # -- prepare 六段建议 ---------------------------------------------------
    def _prepare_sections(self, answers: Mapping) -> tuple[list[dict], dict, dict]:
        """契约 §3 prepare：parse_answers + 五个 *_advice + registry 路由。"""
        try:
            condopt_validate_answers(answers)
            space, objective, context = condopt_prepare.parse_answers(answers)
            recommendation = condopt_route(context)
        except CondoptError as exc:
            raise _local_condopt_error(exc) from exc
        except KeyError as exc:
            raise WorkbenchError(E_VALIDATION, f"answers 字段错误: {exc}", "对照 prepare_template 的键") from exc
        except (ValueError, TypeError) as exc:
            raise WorkbenchError(E_VALIDATION, str(exc), "对照 prepare_template 的形状修改 answers") from exc

        noise = condopt_prepare.noise_advice(answers, context)
        stopping = condopt_prepare.stopping_advice(context)
        feasibility = condopt_prepare.feasibility_advice(answers, context, objective)
        deliverable = condopt_prepare.deliverable_advice(answers, context, objective)
        prior = condopt_prepare.prior_advice(answers, context, objective)
        route_payload = _to_jsonable(recommendation.to_dict())

        sections = [
            {
                "key": "stages",
                "title": "算法与开工步骤",
                "markdown": _md_stages(route_payload, context),
            },
            {
                "key": "stopping",
                "title": "停止口径",
                "markdown": _md_stopping(stopping),
            },
            {
                "key": "feasibility",
                "title": "可行率预警",
                "markdown": _md_feasibility(feasibility),
            },
            {
                "key": "deliverable",
                "title": "交付口径",
                "markdown": _md_deliverable(deliverable),
            },
            {
                "key": "prior",
                "title": "先验与暖启动",
                "markdown": _md_prior(prior),
            },
            {
                "key": "noise",
                "title": "噪声处置",
                "markdown": _md_noise(noise),
            },
        ]
        problem = {
            "space": _to_jsonable(space.to_dict()),
            "objective": _to_jsonable(objective.to_dict()),
            "context": _to_jsonable(context.to_dict()),
            "budget": int(context.budget_total or 0),
            "batch_size": int(context.batch_size or 0),
        }
        return sections, problem, route_payload

    def preview_problem(self, answers: Mapping) -> dict:
        """SSE 第 1 段「解析条件空间」用：只解析，不出建议（不落盘）。"""
        try:
            condopt_validate_answers(answers)
            space, objective, context = condopt_prepare.parse_answers(answers)
        except CondoptError as exc:
            raise _local_condopt_error(exc) from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise WorkbenchError(E_VALIDATION, str(exc), "对照 prepare_template 的形状修改 answers") from exc
        return {
            "space": _to_jsonable(space.to_dict()),
            "objective": _to_jsonable(objective.to_dict()),
            "context": _to_jsonable(context.to_dict()),
            "n_variables": len(space.names),
            "budget": int(context.budget_total or 0),
            "batch_size": int(context.batch_size or 0),
        }

    # -- dispatch -----------------------------------------------------------
    def dispatch(self, action: str, run: Any = None, payload: Any = None) -> tuple[str, dict]:
        """执行一个闭环动作，返回 ``(message, result)``。写动作全局串行。"""
        if not isinstance(action, str) or action not in ACTION_PAYLOAD_FIELDS:
            raise WorkbenchError(
                E_BAD_REQUEST,
                f"未知 action: {_display(action)!r}",
                f"可用: {', '.join(sorted(ACTION_PAYLOAD_FIELDS))}",
            )
        cleaned = self._check_payload(action, payload)

        if action == "prepare":
            sections, problem, route_payload = self._prepare_sections(cleaned["answers"])
            message = f"已生成六段开工建议；router 推荐 {route_payload.get('algorithm')}"
            return message, {"sections": sections, "problem": problem, "route": route_payload}

        if action == "init":
            return self._do_init(cleaned)

        run_dir = self._run_dir(run)
        if action in WRITE_ACTIONS:
            with self._lock:
                study = Study.load(run_dir)  # 锁内重载：并发写之间的状态必须是最新的
                return self._execute(study, action, cleaned)
        study = Study.load(run_dir)
        return self._execute(study, action, cleaned)

    def _execute(self, study: Study, action: str, payload: dict) -> tuple[str, dict]:
        handler = getattr(self, f"_do_{action}")
        try:
            return handler(study, payload)
        except WorkbenchError:
            raise
        except CondoptError as exc:
            raise _local_condopt_error(exc) from exc

    # -- 各动作实现 ---------------------------------------------------------
    def _check_init_params(self, algorithm: str, raw: Any) -> dict | None:
        """``init.params`` 校验（CONTRACT §3 / §10.4 裁定）。

        形状是**平表** ``{参数名: 值}``（同 CLI ``--params`` / ``Study.create(params=…)``）；
        未知键与非法值都 **422 并点名**，绝不静默忽略——否则「参数页调完参却没生效」
        就是用户可见的假生效。``{}`` 视为不传（与旧行为一致）。
        """
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise WorkbenchError(
                E_VALIDATION,
                "params 必须是平表对象 {参数名: 值}",
                '例如 {"kernel": "rbf"}；不要嵌套、不要按算法名分组',
            )
        nested = sorted(str(key) for key, value in raw.items() if isinstance(value, (Mapping, list, tuple)))
        if nested:
            raise WorkbenchError(
                E_VALIDATION,
                f"params 必须是平表，这些键的值是嵌套结构: {_display_list(nested)}",
                '例如 {"kernel": "rbf"}；不要嵌套、不要按算法名分组',
            )
        params = {str(key): value for key, value in raw.items()}
        if not params:
            return None
        if algorithm == "auto":
            raise WorkbenchError(
                E_VALIDATION,
                "algorithm=auto 时不能指定算法参数（auto 路由自己决定算法，其参数由算法默认值接管）",
                "先选定算法：用具体算法名（例如 bayes），"
                "或先 POST /api/kickoff/plan 拿到推荐算法；建好后要换算法/参数可用 override 动作",
            )
        described = {
            str(item.get("name")) for item in (condopt_registry.describe(algorithm).get("params") or [])
        }
        unknown = sorted(key for key in params if key not in described)
        if unknown:
            raise WorkbenchError(
                E_VALIDATION,
                f"params 含 {algorithm} 没有的参数: {_display_list(unknown)}",
                f"{algorithm} 可调参数: {sorted(described)}",
            )
        try:
            condopt_registry.validate_params(algorithm, params)
        except (ValueError, TypeError, CondoptError) as exc:
            raise WorkbenchError(
                E_VALIDATION,
                f"params 取值不合法: {exc}",
                f"取值必须来自 {algorithm} 的 ParamSpec 元数据（见 /api/kickoff/plan 的 params）",
            ) from exc
        return params

    def _do_init(self, payload: dict) -> tuple[str, dict]:
        answers = dict(payload["answers"])
        run_id = str(payload["id"]).strip()
        algorithm = str(payload.get("algorithm") or "auto")
        run_dir = self._run_dir(run_id, must_exist=False, creating=True)
        if algorithm not in ("auto", *condopt_registry.names()):
            raise WorkbenchError(
                E_VALIDATION,
                f"未知算法: {algorithm}",
                f"可用: auto, {', '.join(condopt_registry.names())}",
            )
        params = self._check_init_params(algorithm, payload.get("params"))
        with self._lock:
            if Study.exists(run_dir):
                raise WorkbenchError(
                    E_CONFLICT,
                    f"run 已存在: {_display(run_id)}",
                    "换一个 id，或在 runs 目录里删掉旧战役（工作台不静默覆盖已有数据）",
                )
            try:
                space, objective, context = condopt_prepare.parse_answers(answers)
            except CondoptError as exc:
                raise _local_condopt_error(exc) from exc
            except (ValueError, TypeError, KeyError) as exc:
                raise WorkbenchError(E_VALIDATION, str(exc), "对照 prepare_template 的形状修改 answers") from exc
            budget = payload.get("budget")
            batch = payload.get("batch")
            if budget is not None:
                context.budget_total = int(budget)
                context.budget_remaining = int(budget)
            self._ensure_runs_dir()
            created_dir = not run_dir.exists()
            try:
                # 契约 §3：init 先落 prepare 的三份问题文件，再 Study.create 建战役。
                written = condopt_prepare.write_problem(answers, run_dir)
                study = Study.create(
                    run_dir=run_dir,
                    space=space,
                    objective=objective,
                    algorithm=algorithm,
                    params=params,
                    seed=0,
                    context=context,
                    budget=budget,
                    batch_size=batch,
                    auto_route=True,
                )
            except CondoptError as exc:
                raise _local_condopt_error(exc) from exc
            except OSError as exc:
                # 兜底：任何被本机文件系统拒绝的名字（保留设备名、路径/名称限制、权限……）
                # 都是客户端输入问题 → 4xx；原始异常只进服务日志，不回显给 HTTP 客户端。
                if created_dir:
                    _shutil.rmtree(run_dir, ignore_errors=True)
                _log(f"init {_display(run_id)!r} 失败：{type(exc).__name__}: {exc}")
                raise WorkbenchError(
                    E_BAD_REQUEST,
                    f"无法创建 run {_display(run_id)!r}：本机文件系统拒绝了这个名称",
                    f"用 ≤{MAX_RUN_ID_LENGTH} 字符的普通目录名（不含 / \\ : 与设备名如 NUL）",
                ) from exc
            except (ValueError, TypeError, KeyError) as exc:
                raise WorkbenchError(E_VALIDATION, str(exc), "对照 prepare_template 的形状修改 answers") from exc
            summary = self._run_summary(study)
        message = f"已创建 study {run_id}（算法 {summary['algorithm']}，预算 {summary['budget']}）"
        return message, {
            "run": summary,
            "problem_files": {
                "space": written.get("space"),
                "context": written.get("context"),
                "objective": written.get("objective"),
            },
            "route": _to_jsonable((written.get("recommendation") or {}).get("algorithm")),
        }

    def _do_ask(self, study: Study, payload: dict) -> tuple[str, dict]:
        try:
            pending = study.ask(payload.get("n"), plate=payload.get("plate"))
        except PendingError as exc:
            raise WorkbenchError(
                E_CONFLICT,
                str(exc),
                "先 POST /api/action {tell} 回填当前批次，或看 study.pending",
            ) from exc
        except ValueError as exc:
            raise WorkbenchError(E_VALIDATION, str(exc), "检查 n / plate 与剩余预算") from exc
        summary = self._run_summary(study)
        first = pending[0] if pending else {}
        message = (
            f"已生成 {len(pending)} 条待测建议（算法 {first.get('algorithm', study.algorithm)}，"
            f"孔板 {first.get('plate', '-')}）；剩余预算 {summary['remaining']}"
        )
        return message, {"run": summary, "pending": _to_jsonable(pending)}

    def _do_tell(self, study: Study, payload: dict) -> tuple[str, dict]:
        if payload.get("csv") is not None:
            try:
                rows = _observations_from_csv(str(payload["csv"]))
            except (ValueError, json.JSONDecodeError) as exc:
                raise WorkbenchError(E_VALIDATION, f"CSV 解析失败: {exc}", "表头形如 id,x.T,y") from exc
            rows = _normalize_results(rows, study)
        else:
            rows = list(payload["observations"])
            for index, row in enumerate(rows):
                if not isinstance(row, Mapping):
                    raise WorkbenchError(
                        E_VALIDATION,
                        f"observations[{index}] 必须是对象",
                        '例如 {"id": "r1-1", "y": 12.3}',
                    )
        if not rows:
            raise WorkbenchError(E_VALIDATION, "tell 的 observations 为空", "至少回填一条实测结果")
        try:
            observations = study.tell(rows)
        except ValueError as exc:
            if "unknown pending id" in str(exc):
                raise WorkbenchError(
                    E_CONFLICT,
                    str(exc),
                    "用 GET /api/study?id=<run> 看当前 pending id，或先 ask 生成建议",
                ) from exc
            raise WorkbenchError(E_VALIDATION, str(exc), "检查 x 是否在条件空间内 / id 是否正确") from exc
        still_pending = [item["id"] for item in study.pending]
        if still_pending:
            study.log_decision(
                action="execution_partial",
                algorithm=study.algorithm,
                reason=(
                    f"提交了 {len(rows)} 条结果，仍有 {len(still_pending)} 个 pending"
                    "（本轮未提交/结果缺失/被幂等跳过）；未测的孔位可补测后重跑 tell"
                ),
                payload={"requested": len(rows), "still_pending": still_pending},
            )
        summary = self._run_summary(study)
        message = f"已记录 {len(observations)} 条实测结果；剩余预算 {summary['remaining']}"
        if still_pending:
            message += f"；仍有 {len(still_pending)} 条 pending 未回填"
        return message, {
            "run": summary,
            "accepted": len(observations),
            "still_pending": still_pending,
        }

    def _do_step(self, study: Study, payload: dict) -> tuple[str, dict]:
        try:
            step = study.next_action()
        except (ValueError, KeyError) as exc:
            raise WorkbenchError(E_CONFLICT, str(exc), "先 ask + tell 至少一轮") from exc
        step = _to_jsonable(step)
        return f"下一步：{step.get('action')}（{step.get('reason')}）", {"step": step}

    def _do_recommend(self, study: Study, payload: dict) -> tuple[str, dict]:
        try:
            recommendation = study.recommend()
        except ValueError as exc:
            raise WorkbenchError(E_CONFLICT, str(exc), "先 ask + tell 至少一条观测") from exc
        recommendation = _to_jsonable(recommendation)
        condition = ", ".join(f"{key}={value}" for key, value in (recommendation.get("condition") or {}).items())
        return f"推荐条件 {condition}（分数 {recommendation.get('score')}）", {"recommend": recommendation}

    def _do_delivery(self, study: Study, payload: dict) -> tuple[str, dict]:
        delivery = _to_jsonable(study.delivery_checklist())
        integrity = _to_jsonable(study.check_integrity())
        failed = [item for item in delivery if not item.get("ok")]
        if failed:
            message = f"交付自查未通过 {len(failed)}/{len(delivery)} 项；数据完整性 {'OK' if integrity.get('ok') else '有 issue'}"
        else:
            message = f"交付自查 {len(delivery)} 项全部通过；数据完整性 {'OK' if integrity.get('ok') else '有 issue'}"
        return message, {"delivery": delivery, "integrity": integrity}

    def _do_override(self, study: Study, payload: dict) -> tuple[str, dict]:
        try:
            decision = study.override(
                str(payload["algorithm"]),
                payload.get("params"),
                str(payload["reason"]),
            )
        except KeyError as exc:
            raise WorkbenchError(
                E_VALIDATION, f"未知算法: {_display(str(exc))}", "用 GET /api/bootstrap 看可选算法"
            ) from exc
        except ValueError as exc:
            raise WorkbenchError(E_VALIDATION, str(exc), "检查 params 是否满足该算法的 ParamSpec") from exc
        decision = _to_jsonable(decision)
        message = f"已覆盖为 {decision.get('algorithm')}（含 reason，已写入 decisions.jsonl）"
        return message, {"decision": decision}

    def _do_render(self, study: Study, payload: dict) -> tuple[str, dict]:
        make_png = bool(payload.get("make_png", True))
        try:
            result = condopt_render.render_study(study.dir, out_dir=study.dir, make_png=make_png)
        except FileNotFoundError as exc:
            raise WorkbenchError(E_NOT_FOUND, str(exc), "先 ask/tell 产生数据再渲染") from exc
        dashboard = Path(str(result.get("dashboard"))).resolve()
        png = result.get("convergence_png")
        return (
            f"看板已生成：{dashboard.name}（观测 {result.get('n_observations')} 条）",
            {
                "dashboard": str(dashboard),
                "png": None if not png else str(Path(str(png)).resolve()),
                "warnings": _to_jsonable(result.get("warnings") or []),
            },
        )

    def _do_report(self, study: Study, payload: dict) -> tuple[str, dict]:
        markdown = study.report()
        return "已生成 Markdown 报告（展示用；决策信息请看 step/recommend 结构化字段）", {"markdown": markdown}

    # -- 导入已有数据（CONTRACT §9）------------------------------------------
    def scan_import(self, path: Any, sample_rows: Any = None, model: Any = None) -> dict:
        """§9.3：本地只读 —— 读文件、嗅探编码/分隔符、逐列画像、规则推断映射。**不出网。**"""
        resolved = self._import_path(path)
        rows_requested = _import_sample_rows(sample_rows)
        preview_model = model if model is not None else self.llm_model
        if not isinstance(preview_model, str) or not preview_model.strip():
            raise WorkbenchError(E_VALIDATION, "model 必须是非空字符串", "留空 = 用后端默认模型")
        table = _read_import_table(resolved)
        columns = _profile_columns(table)
        rule_mapping, warnings = _rule_mapping(columns, table)
        for message in table["warnings"]:
            if message not in warnings:
                warnings.append(message)
        stat = resolved.stat()
        scan_id = _scan_id(resolved, stat.st_size, stat.st_mtime)
        # 同进程内保留最近几次 scan，供 map 用（§9.4 防「扫 A 打 B」）。
        self._scans[scan_id] = {
            "scan_id": scan_id,
            "path": str(resolved),
            "bytes": int(stat.st_size),
            "mtime": float(stat.st_mtime),
            "columns": columns,
            "n_rows": len(table["rows"]),
            "header": table["header"],
            "samples": table["rows"][:MAX_IMPORT_SAMPLE_ROWS],
            "rule_mapping": rule_mapping,
            "warnings": warnings,
        }
        while len(self._scans) > 8:  # 有界缓存：只留最近 8 次
            self._scans.pop(next(iter(self._scans)))
        # §9.3 sample_preview：用与 map **完全相同**的请求体构造路径算出「真值」，
        # 只是不发出去。构造提示词不需要密钥，所以未开开关/未配密钥时也要能给。
        preview_body = _llm_request_body(preview_model, self._scans[scan_id], rows_requested)
        preview_bytes = _request_body_bytes(preview_body)
        if self.llm_base_url_error:
            # 配置错在 scan 阶段就说清楚，别等用户点「同意」之后才 503（F7/F8）。
            warnings.append(
                f"模型网关地址配置不合法：{self.llm_base_url_error}（模型读表会返回 503）"
            )
        self._scans[scan_id].update(
            {
                "preview_model": preview_model,
                "preview_rows": rows_requested,
                "preview_bytes": preview_bytes,
            }
        )
        profile = {
            "ok": True,
            "scan_id": scan_id,
            "file": {
                "path": str(resolved),
                "bytes": int(stat.st_size),
                "mtime": float(stat.st_mtime),
                "encoding": table["encoding"],
                "delimiter": table["delimiter"],
                "has_header": table["has_header"],
                "format": table["format"],
            },
            "n_rows": len(table["rows"]),
            "columns": columns,
            "rule_mapping": rule_mapping,
            "sample_preview": {
                "rows": len(self._scans[scan_id]["samples"][:rows_requested]),
                "bytes": preview_bytes,
                # 地址不合法时给空串，不要拼出一个看起来能用、实际 503 的假 URL。
                "endpoint": (
                    "" if self.llm_base_url_error else f"{self.llm_base_url}/chat/completions"
                ),
            },
            "warnings": warnings,
        }
        return profile

    def map_import(self, payload: Any) -> dict:
        """§9.4：模型读表（或规则回退）。只有这里会出网，且必须开关+密钥+consent 三者齐备。"""
        if not isinstance(payload, Mapping):
            raise WorkbenchError(E_BAD_REQUEST, "payload 必须是 JSON 对象", '例如 {"scan_id": "…", "consent": true}')
        known = {"scan_id", "consent", "sample_rows", "model", "fallback"}
        unknown = sorted(str(key) for key in payload if str(key) not in known)
        if unknown:
            raise WorkbenchError(
                E_VALIDATION,
                f"map 的 payload 未知字段: {_display_list(unknown)}",
                f"允许字段: {sorted(known)}",
            )
        scan_id = payload.get("scan_id")
        scan = self._scans.get(scan_id) if isinstance(scan_id, str) else None
        if scan is None:
            raise WorkbenchError(
                E_BAD_REQUEST,
                f"scan_id 无效或已过期: {_display(scan_id)!r}",
                "先 POST /api/import/scan 拿 scan_id（只认本进程最近几次成功 scan）",
            )
        sample_rows = _import_sample_rows(payload.get("sample_rows"))
        fallback = payload.get("fallback")
        # 前端「让模型读表」按钮显式发 fallback:"llm"（把默认写死比留空更好审计）。
        if fallback not in (None, "llm", "rules"):
            raise WorkbenchError(E_VALIDATION, f"fallback 只支持 'llm' / 'rules'，收到 {_display(fallback)!r}")
        model = payload.get("model") or self.llm_model
        if not isinstance(model, str) or not model.strip():
            raise WorkbenchError(E_VALIDATION, "model 必须是非空字符串")

        if fallback == "rules":
            # 离线显式回退（§9.1：前端可让用户改走规则映射，不静默降级；规则路径不需要 consent）。
            self._maps[scan["scan_id"]] = {"source": "rules", "model": None, "consent": False}
            return {
                "ok": True,
                "source": "rules",
                "model": None,
                "sent": None,
                "mapping": scan["rule_mapping"],
                "confidence": None,
                "rationale": "用本地规则从列名/类型/取值范围推断（未出网）。",
                "warnings": list(scan["warnings"]),
            }

        # 顺序很关键（§9.4，lead 2026-11-20 裁定）：**「即将真的调模型」时才要求 consent**。
        # 先判可用性：没开开关/没密钥时答案是 E_LLM_UNAVAILABLE（不是 consent 缺失）——
        # 否则前端会把「后端没配置」误显示成「用户没同意」。
        if not self.import_llm:
            raise WorkbenchError(
                E_LLM_UNAVAILABLE,
                "模型读表未启用（服务端 --import-llm 关闭）",
                "用 --import-llm 启动服务；或让前端改走 fallback:\"rules\"（离线规则映射）",
            )
        api_key = os.environ.get(self.llm_api_key_env)
        if not api_key:
            raise WorkbenchError(
                E_LLM_UNAVAILABLE,
                f"模型读表缺少密钥（环境变量 {self.llm_api_key_env} 未设置）",
                f"设置环境变量 {self.llm_api_key_env} 后重启服务；密钥只从环境变量读，不写盘、不回显",
            )
        if payload.get("consent") is not True:
            raise WorkbenchError(
                E_CONSENT_REQUIRED,
                "调用模型前必须显式同意（consent:true）",
                "前端需二次确认并列出：目标 URL、模型 id、行数、字节数；未同意不得外发",
            )

        request_body = _llm_request_body(model, scan, sample_rows)
        raw = self._llm_chat(api_key, request_body)
        parsed = _extract_json_object(raw)
        retried = False
        if parsed is None:
            # 契约 §9.4：先做一次「只修格式」的重试，仍失败才 502（附原始响应前 500 字符）。
            raw = self._llm_chat(api_key, _llm_repair_body(model, raw))
            parsed = _extract_json_object(raw)
            retried = True
        if parsed is None:
            raise WorkbenchError(
                E_LLM_BAD_RESPONSE,
                "模型两次都未返回合法 JSON",
                f"原始响应前 500 字符: {raw[:500]}",
            )
        mapping, confidence, rationale, parse_warnings = _mapping_from_llm_json(parsed)
        normalised, kind_warnings = _normalise_mapping(mapping, scan["columns"], scan["header"])
        sent_bytes = _request_body_bytes(request_body)
        self._maps[scan["scan_id"]] = {
            "source": "llm",
            "model": model,
            "consent": True,
            "confidence": confidence,
        }
        warnings = [*scan["warnings"], *parse_warnings, *kind_warnings]
        if retried:
            warnings.append("模型首次返回不是合法 JSON，已做一次「只修格式」重试。")
        preview_bytes = scan.get("preview_bytes")
        if isinstance(preview_bytes, int) and preview_bytes != sent_bytes:
            # §9.3 的硬不变量在运行时的守卫：用户是同意的「scan 预告数字」，一旦实际发送量
            # 与预告不同（换了模型 / 改了 sample_rows 却没重扫），必须显式说出来。
            warnings.append(
                f"本次实际发送 {sent_bytes} 字节，与 scan 预告的 {preview_bytes} 字节不同"
                f"（模型或 sample_rows 变了）；同意界面应基于本次数字重新确认。"
            )
        return {
            "ok": True,
            "source": "llm",
            "model": model,
            "sent": {
                "url": f"{self.llm_base_url}/chat/completions",
                "rows": len(scan["samples"][:sample_rows]),
                "bytes": sent_bytes,
                "redacted": False,
            },
            "mapping": normalised,
            "confidence": confidence,
            "rationale": rationale,
            "warnings": warnings,
        }

    def apply_import(self, payload: Any) -> dict:
        """§9.5：按确认后的 mapping 建 study + 先验导入；返回调参 schema。"""
        if not isinstance(payload, Mapping):
            raise WorkbenchError(E_BAD_REQUEST, "payload 必须是 JSON 对象")
        known = {"id", "path", "scan_id", "mapping", "algorithm", "params", "budget", "batch", "reason",
                 "count_toward_budget"}
        unknown = sorted(str(key) for key in payload if str(key) not in known)
        if unknown:
            raise WorkbenchError(
                E_VALIDATION,
                f"apply 的 payload 未知字段: {_display_list(unknown)}",
                f"允许字段: {sorted(known)}",
            )
        run_id = payload.get("id")
        if not isinstance(run_id, str) or not run_id.strip():
            raise WorkbenchError(E_VALIDATION, "apply 需要 id（study 目录名）", '例如 {"id": "campaign_from_csv"}')
        run_dir = self._run_dir(run_id, must_exist=False, creating=True)
        resolved = self._import_path(payload.get("path"))
        expected_scan = payload.get("scan_id")
        stat = resolved.stat()
        current_scan_id = _scan_id(resolved, stat.st_size, stat.st_mtime)
        if expected_scan is not None and expected_scan != current_scan_id:
            raise WorkbenchError(
                E_BAD_REQUEST,
                "scan_id 与当前文件不符（扫描后被改动过）",
                "重新 POST /api/import/scan 后再 apply，避免把 A 的映射用到 B 的数据上",
            )
        # condopt 的 import_observations 语义是「先验计入 budget」，没有不计预算的导入路径；
        # 前端确认表有这个开关（默认 true），关掉时**明确留痕**而不是静默忽略。
        count_toward_budget = payload.get("count_toward_budget") is not False
        table = _read_import_table(resolved)
        # 用户确认过的 mapping 是 apply 的真源；仍按真实表头校验列名（拼错的列必须 422 而不是静默丢）。
        mapping, mapping_warnings = _normalise_mapping(
            payload.get("mapping"), None, table["header"], strict=True
        )
        if not mapping["variables"]:
            raise WorkbenchError(E_VALIDATION, "mapping.variables 为空：至少需要一个条件变量", "在确认表里指定条件列")
        if not mapping["objective"]:
            raise WorkbenchError(E_VALIDATION, "mapping.objective 缺失：必须指定目标列", "在确认表里指定目标列")
        mapping_warnings = [*mapping_warnings, *_fill_missing_choices(table, mapping)]
        # 补齐成功后就不再重复「没有 choices，将按数据补齐」这条预告性提醒。
        if any("choices 由数据补齐" in item for item in mapping_warnings):
            mapping_warnings = [
                item for item in mapping_warnings if "将按数据里出现的取值补齐" not in item
            ]

        try:
            mapping_space = _space_from_mapping(mapping)
            mapping_objective = _objective_from_mapping(mapping)
        except (ValueError, KeyError, TypeError) as exc:
            raise WorkbenchError(
                E_VALIDATION, f"mapping 无法构成条件空间: {exc}", "检查 variables 的 kind/low/high/choices"
            ) from exc
        observations, rejected = _rows_to_observations(table, mapping, mapping_space)
        valid_total = len(observations)

        algorithm = payload.get("algorithm") or "auto"
        if not isinstance(algorithm, str) or algorithm not in ("auto", *condopt_registry.names()):
            raise WorkbenchError(
                E_VALIDATION,
                f"未知算法: {_display(algorithm)}",
                f"可用: auto, {', '.join(condopt_registry.names())}",
            )
        params = payload.get("params")
        if params is not None and not isinstance(params, Mapping):
            raise WorkbenchError(E_VALIDATION, "params 必须是 JSON 对象", "形状见 params_schema")
        batch = payload.get("batch")
        if batch is not None and (isinstance(batch, bool) or not isinstance(batch, int) or batch < 1):
            raise WorkbenchError(E_VALIDATION, "batch 必须是 >=1 的整数")
        budget = payload.get("budget")
        reason = payload.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise WorkbenchError(E_VALIDATION, "reason 必须是字符串")

        warnings = list(mapping_warnings)
        if not count_toward_budget:
            warnings.append(
                "condopt 的 import_observations 语义是「先验计入 budget」，无法关闭；"
                "本次已按计入处理（count_toward_budget=false 只影响你的后续规划，不影响导入结果）。"
            )
        with self._lock:
            existing_study = Study.exists(run_dir)
            active_space = mapping_space
            if existing_study:
                study = Study.load(run_dir)
                active_space = study.space
                if study.space.to_dict() != mapping_space.to_dict():
                    warnings.append(
                        "run 已存在，按它自己的条件空间校验导入行（mapping 的空间只用于解释列语义）"
                    )
                observations, rejected = _rows_to_observations(table, mapping, active_space)
                valid_total = len(observations)
            unique: list[dict] = []
            seen: set = set()
            for observation in observations:
                key = _dedupe_key(observation, active_space)
                if key in seen:
                    continue
                seen.add(key)
                unique.append(observation)
            estimated_accepted = len(unique)

            if budget is None:
                suggested = _suggested_budget(estimated_accepted)
                raise WorkbenchError(
                    E_VALIDATION,
                    f"apply 需要 budget（先验计入预算）：导入 {estimated_accepted} 行时 budget 至少 {max(1, estimated_accepted)}",
                    f"给一个 ≥ 导入行数的 budget（建议 {suggested}）；工作台不会替你放大预算",
                    extra={"suggested_budget": suggested},
                )
            if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
                raise WorkbenchError(E_VALIDATION, "budget 必须是 >=1 的整数")

            if existing_study:
                existing = len(study.dataset)
                needed = existing + estimated_accepted
                if study.budget < needed:
                    suggested = _suggested_budget(needed)
                    raise WorkbenchError(
                        E_VALIDATION,
                        f"既有 study 的 budget={study.budget} 不足以容纳导入：已有 {existing} 条 + 待导入 {estimated_accepted} 条 = {needed}",
                        f"建议 budget ≥ {suggested}；工作台不会静默放大预算",
                        extra={"suggested_budget": suggested},
                    )
                if budget != study.budget:
                    warnings.append(
                        f"run 已存在，沿用其 budget={study.budget}（请求里的 budget={budget} 未生效）"
                    )
            else:
                if budget < estimated_accepted:
                    suggested = _suggested_budget(estimated_accepted)
                    raise WorkbenchError(
                        E_VALIDATION,
                        f"budget={budget} 小于导入行数 {estimated_accepted}：先验数据计入预算，不允许静默放大",
                        f"建议 budget={suggested}（≥ 导入行数，并留出后续探索轮次）",
                        extra={"suggested_budget": suggested},
                    )
                try:
                    study = Study.create(
                        run_dir=run_dir,
                        space=mapping_space,
                        objective=mapping_objective,
                        algorithm=algorithm,
                        params=dict(params) if params else None,
                        budget=int(budget),
                        batch_size=int(batch) if batch else None,
                    )
                except KeyError as exc:
                    raise WorkbenchError(E_VALIDATION, f"算法参数不合法: {_display(str(exc))}") from exc
                except (ValueError, TypeError) as exc:
                    raise WorkbenchError(E_VALIDATION, f"mapping/算法参数不合法: {exc}") from exc
            try:
                imported = study.import_observations(unique)
            except ValueError as exc:
                raise WorkbenchError(E_VALIDATION, str(exc), "检查 mapping 的列/类型是否与数据一致") from exc
            accepted = len(imported)
            # duplicate = import_observations 的去重结果（批内重复 + 与既有 dataset 重复）。
            duplicate_count = max(0, valid_total - accepted)
            summary = self._run_summary(study)
            remaining = summary["remaining"]
            warnings.append(
                f"导入 {accepted} 条后剩余预算 {remaining}；如需更多轮次请调大 budget"
            )
            map_info = None
            for key in (current_scan_id, expected_scan):
                if isinstance(key, str) and key in self._maps:
                    map_info = self._maps[key]
                    break
            source = (map_info or {}).get("source") or "rules"
            model = (map_info or {}).get("model")
            consent = bool((map_info or {}).get("consent"))
            audit = {
                "t": time.time(),
                "path": str(resolved),
                "file_sha256": _file_sha256(resolved),
                "source": source,
                "model": model,
                "consent": consent,
                "accepted": accepted,
                "rejected": len(rejected),
                "duplicate": duplicate_count,
                "budget": study.budget,
                "count_toward_budget_requested": payload.get("count_toward_budget") is not False,
                "reason": reason or f"导入 {len(table['rows'])} 行历史数据作为先验",
            }
            with (run_dir / "import_audit.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json_dumps(audit) + "\n")

        params_schema = _to_jsonable(condopt_registry.describe(study.algorithm)["params"])
        message = (
            f"已导入 {accepted} 条先验观测（重复 {duplicate_count}、拒绝 {len(rejected)}）"
            f"，算法 {study.algorithm}，剩余预算 {remaining}"
        )
        # 契约 §9.5 的响应是**顶层平铺**（run/imported/warnings/params_schema/next）；
        # 同时镜像到 result 里，兼容 §3 的 {"ok","message","result"} 信封习惯。
        flat = {
            "run": summary,
            "imported": {"accepted": accepted, "duplicate": duplicate_count, "rejected": rejected},
            "warnings": warnings,
            "params_schema": params_schema,
            "next": "ask",
        }
        return {"ok": True, "message": message, **flat, "result": dict(flat)}

    # -- 模型读表（§9.4）----------------------------------------------------
    def _llm_chat(self, api_key: str, request_body: dict) -> str:
        """OpenAI 兼容调用：``POST {baseURL}/chat/completions`` + ``Authorization: Bearer``。"""
        _check_api_key_for_header(api_key, self.llm_api_key_env)
        if self.llm_base_url_error:
            # F7/F8：地址不合法是配置错误，必须在构造请求前就报 503（InvalidURL 不是
            # UnicodeEncodeError 的子类，漏到外层就成了 500 + 异常类名）。
            raise WorkbenchError(
                E_LLM_UNAVAILABLE,
                self.llm_base_url_error,
                "检查环境变量 CONDOPT_IMPORT_LLM_BASE_URL（例如 https://open.bohrium.com/openapi/model-gateway/v1）",
            )
        url = f"{self.llm_base_url}/chat/completions"
        payload = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
        # 任何「地址/请求头」类构造错误（UnicodeEncodeError、InvalidURL、头里有换行…）
        # 都归一到「配置类错误 503」，不露成 500（task-11 要求 2 / task-3 F7）。
        try:
            request = urllib.request.Request(url, data=payload, method="POST")
            request.add_header("Content-Type", "application/json")
            request.add_header("Authorization", f"Bearer {api_key}")
        except (UnicodeEncodeError, http.client.InvalidURL, ValueError) as exc:
            _log(f"模型网关地址/请求头不合法：{type(exc).__name__}")
            raise WorkbenchError(
                E_LLM_UNAVAILABLE,
                "模型网关地址或请求头不合法（无法构造 HTTP 请求）",
                f"检查 {self.llm_api_key_env} 与 CONDOPT_IMPORT_LLM_BASE_URL 是否都是 ASCII/URL 安全字符",
            ) from exc
        try:
            with urllib.request.urlopen(request, timeout=LLM_TIMEOUT_SECONDS) as response:
                body = response.read().decode("utf-8", errors="replace")
        except (UnicodeEncodeError, http.client.InvalidURL, ValueError) as exc:
            _log(f"模型网关请求失败（配置类）：{type(exc).__name__}")
            raise WorkbenchError(
                E_LLM_UNAVAILABLE,
                "模型网关地址或请求头不合法（无法发起 HTTP 请求）",
                f"检查 {self.llm_api_key_env} 与 CONDOPT_IMPORT_LLM_BASE_URL 是否都是 ASCII/URL 安全字符",
            ) from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            # 只记录状态与长度，绝不记录请求头（里面有密钥）。
            _log(f"LLM HTTP {exc.code}（响应 {len(detail)} 字符）")
            raise WorkbenchError(
                E_LLM_BAD_RESPONSE,
                f"网关返回 HTTP {exc.code}",
                f"检查 baseURL/模型 id；响应片段: {detail}",
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _log(f"LLM 连接失败：{type(exc).__name__}")
            raise WorkbenchError(
                E_LLM_UNAVAILABLE,
                f"无法连接模型网关（{type(exc).__name__}）",
                f"检查网络与 {self.llm_base_url}；工作台其余功能仍可离线使用",
            ) from exc
        try:
            parsed = json.loads(body)
            content = parsed["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise WorkbenchError(
                E_LLM_BAD_RESPONSE,
                "网关响应不是 OpenAI 兼容的 chat.completions 形状",
                f"原始响应前 500 字符: {body[:500]}",
            ) from exc
        return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)

    # -- 对话式开工（CONTRACT §10）------------------------------------------
    def _kickoff_context_from_answers(self, answers: Mapping) -> tuple:
        """answers → (space, objective, context, route 结果)；沿用 prepare 的解析路径。"""
        try:
            condopt_validate_answers(answers)
            space, objective, context = condopt_prepare.parse_answers(answers)
            recommendation = condopt_route(context)
        except CondoptError as exc:
            raise _local_condopt_error(exc) from exc
        except KeyError as exc:
            raise WorkbenchError(E_VALIDATION, f"answers 字段错误: {exc}", "先用 /api/kickoff/chat 攒出 answers") from exc
        except (ValueError, TypeError) as exc:
            raise WorkbenchError(E_VALIDATION, str(exc), "先用 /api/kickoff/chat 攒出 answers") from exc
        return space, objective, context, recommendation

    def _kickoff_answers_for_init(self, answers: Mapping) -> tuple[dict, list[str]]:
        """把 state.answers 收敛成「可直接喂给 ``POST /api/action {init}``」的形状。

        带硬约束时 condopt 只能走 ``spec`` 路径（prepare 的 ``constraints`` 只是**数量**），
        这里显式转入 spec 并在 warnings 里说清目标名会变成 ``spec_score``。
        """
        cleaned = json.loads(json.dumps(_to_jsonable(answers), ensure_ascii=False))
        warnings: list[str] = []
        cleaned.pop("constraint_items", None)
        cleaned.pop("defer_to_import", None)
        cleaned["batch"] = int(cleaned.get("batch") or KICKOFF_DEFAULT_BATCH)
        cleaned.setdefault("noise", "medium")
        cleaned.setdefault("smoothness", "mixed")
        cleaned.setdefault("domain", "generic")
        cleaned.setdefault("prior", 0)
        cleaned.setdefault("notes", "由对话式开工生成（CONTRACT §10）")

        items = [item for item in (answers.get("constraint_items") or []) if isinstance(item, Mapping)]
        if items:
            objective = cleaned.get("objective") or {}
            # 硬约束只能由 spec.constraints 表达；目标**不**复制成额外 goal——
            # 那会悄悄改变标量化分数（把约束指标也当成要最大化的目标）。
            metrics = [{"metric": str(objective.get("name")), "mode": str(objective.get("direction") or "max")}]
            limits = {
                str(item["metric"]): {"op": str(item.get("op") or ">="), "value": float(item.get("value") or 0.0)}
                for item in items
                if item.get("metric")
            }
            cleaned["spec"] = {"goals": metrics, "constraints": limits}
            cleaned["constraints"] = len(limits)
            warnings.append(
                "你给了硬约束：condopt 只有 spec 路径能表达「指标 + 比较符 + 阈值的硬约束」，"
                "所以 answers 里会带 spec；代价是 study 的目标列名为 spec_score"
                "（真实目标与约束都写在 spec.goals / spec.constraints 里）。"
            )
        else:
            cleaned.pop("spec", None)
            cleaned["constraints"] = int(cleaned.get("constraints") or 0)
        warnings.append(
            f"对话没有问 batch，默认 batch={KICKOFF_DEFAULT_BATCH}（可在参数页的「高级 answers JSON」里改）。"
        )
        return cleaned, warnings

    def _kickoff_options(self, answers: Mapping) -> tuple[list[dict], str, list, dict]:
        """选项生成：用 router 的**真实打分**排序；recommended 必须是 router 选中的那个。"""
        # 先收敛成 init 会收到的同一份 answers（去掉 constraint_items 这类内部键、
        # 带约束时转入 spec），保证「路由看到的上下文 == init 会建的 study」。
        routable, _ = self._kickoff_answers_for_init(answers)
        space, objective, context, recommendation = self._kickoff_context_from_answers(routable)
        route_payload = _to_jsonable(recommendation.to_dict())
        scores = route_payload.get("candidate_scores") or {}
        picked = str(route_payload.get("algorithm") or "")
        alternatives = {
            str(item.get("algorithm")): item for item in (route_payload.get("alternatives") or [])
        }
        # -165 这类是被规则判死的档位（不是「第二好」），别拿来当可选项。
        qualified = [
            str(name) for name, score in scores.items()
            if isinstance(score, (int, float)) and float(score) > -100 and str(name) != picked
        ]
        qualified.sort(key=lambda name: float(scores.get(name, 0.0)), reverse=True)
        order = [picked, *qualified]
        for name in alternatives:
            if name not in order:
                order.append(name)
        order = [name for name in order if name in condopt_registry.names()]
        order = order[:4] if len(order) >= 2 else order
        if len(order) < 2:
            raise WorkbenchError(
                E_VALIDATION,
                "当前 answers 只能路由出一个可用算法，给不出 2 个以上选项",
                "回到对话补全条件变量/预算后再试",
            )
        options: list[dict] = []
        for name in order:
            display = _algorithm_display(name)
            if name == picked:
                why = list(route_payload.get("rationale") or [])
            else:
                alternative = alternatives.get(name) or {}
                why = [str(alternative.get("why"))] if alternative.get("why") else []
            if not why:
                described = {item["name"]: item for item in condopt_registry.describe_all()}.get(name, {})
                when = str(described.get("when") or "")
                why = [when] if when else []
            options.append(
                {
                    "key": name,
                    "title": display["title"],
                    "one_line": display["one_line"],
                    "why": why,
                    "tradeoff": display["tradeoff"],
                    "recommended": name == picked,
                    "risk": display["risk"],
                    "score": scores.get(name),
                    "disqualified": bool(
                        isinstance(scores.get(name), (int, float)) and float(scores.get(name)) <= -100
                    ),
                    "text_source": display["text_source"],
                }
            )
        return options, picked, [name for name in order if name != picked], route_payload

    def kickoff_chat(self, payload: Any) -> dict:
        """§10.2 的对外入口：规则状态机 + （预留的）reply 润色开关。"""
        return self._kickoff_finish(self._kickoff_chat(payload))

    def _kickoff_finish(self, response: dict) -> dict:
        """CONTRACT §10.4 的**模型通道开关位**。

        默认（未开 ``--import-llm``）绝不进入；即使开了，本版本也只把 ``reply`` 交给
        :meth:`_kickoff_polish_reply`，而它现在**原样返回**——「宁可不变，也不许改
        JSON 结构或路由结论」。将来接模型时只改这一个方法。
        """
        if self.import_llm:
            response["reply"] = self._kickoff_polish_reply(dict(response["reply"]))
        return response

    def _kickoff_polish_reply(self, reply: dict) -> dict:
        """§10.4 占位：可选地用模型润色 ``reply.text``（尚未实现，当前为恒等函数）。"""
        return reply

    def _kickoff_chat(self, payload: Any) -> dict:
        """§10.2：五步收敛的对话状态机。**默认纯规则，不出网。**"""
        if not isinstance(payload, Mapping):
            raise WorkbenchError(E_BAD_REQUEST, "payload 必须是 JSON 对象")
        known = {"messages", "state", "free_text"}
        unknown = sorted(str(key) for key in payload if str(key) not in known)
        if unknown:
            raise WorkbenchError(
                E_VALIDATION,
                f"chat 的 payload 未知字段: {_display_list(unknown)}",
                f"允许字段: {sorted(known)}",
            )
        messages = payload.get("messages") or []
        if not isinstance(messages, list):
            raise WorkbenchError(E_VALIDATION, "messages 必须是数组", '例如 [{"role":"user","content":"…"}]')
        free_text = bool(payload.get("free_text"))
        raw_state = payload.get("state")
        if raw_state is None:
            state = kickoff_empty_state()
        elif isinstance(raw_state, Mapping):
            if raw_state.get("step") not in KICKOFF_STEPS:
                raise WorkbenchError(
                    E_VALIDATION,
                    f"state.step 非法: {_display(raw_state.get('step'))!r}",
                    f"允许: {', '.join(KICKOFF_STEPS)}",
                )
            answers = raw_state.get("answers")
            if answers is not None and not isinstance(answers, Mapping):
                raise WorkbenchError(E_VALIDATION, "state.answers 必须是对象")
            state = {
                "step": raw_state.get("step"),
                "answers": json.loads(json.dumps(_to_jsonable(answers), ensure_ascii=False))
                if isinstance(answers, Mapping)
                else kickoff_empty_state()["answers"],
            }
        else:
            raise WorkbenchError(E_VALIDATION, "state 必须是对象（原样回传上一轮的 state）")
        state["missing"] = _kickoff_missing(state["answers"])

        step = state["step"]
        if step in ("options", "done"):
            # 已到选项步：再喂消息就是「重新选择」，保持选项不动，避免状态倒流。
            options, picked, others, _ = self._kickoff_options(state["answers"])
            return {
                "ok": True,
                "ready": True,
                "reply": {
                    "text": "已经可以选算法了：下面是 condopt 路由器按你刚才的答案给出的候选。",
                    "quick_replies": [],
                },
                "state": state,
                "options": options,
                "candidates": {"recommended": picked, "others": others},
            }

        # 取最后一条 user 消息作为这一轮的回答
        answer_text = ""
        answer_value: Mapping | None = None
        for message in reversed(messages):
            if not isinstance(message, Mapping):
                continue
            if str(message.get("role") or "") != "user":
                continue
            content = message.get("content")
            answer_text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            if isinstance(content, Mapping):
                answer_value = content
            else:
                try:
                    parsed = json.loads(answer_text)
                    if isinstance(parsed, Mapping):
                        answer_value = parsed
                except (json.JSONDecodeError, TypeError):
                    answer_value = None
            break

        if answer_value is None and answer_text:
            answer_value = _match_chip(step, answer_text)
        if answer_value is None and answer_text and free_text:
            parser = {
                "objective": _parse_objective_text,
                "budget": _parse_budget_text,
                "variables": _parse_variables_text,
                "noise": _parse_noise_text,
                "constraints": _parse_constraints_text,
            }[step]
            answer_value = parser(answer_text)

        if answer_value is None:
            # 解析不了就**再问一次**，不瞎猜（§10.2）。
            if not answer_text:
                reply = _kickoff_question_reply(step)
            elif free_text:
                reply = _kickoff_question_reply(
                    step, "这句我没解析出需要的信息，所以不敢替你猜——请换个说法，或直接点下面的选项。"
                )
            else:
                reply = _kickoff_question_reply(
                    step, "没看懂这条回答，请直接点下面的选项（或带 free_text:true 让我按规则解析）。"
                )
            return {"ok": True, "ready": False, "reply": reply, "state": state, "options": None, "candidates": None}

        checked = _kickoff_chip_for_value(step, answer_value) or dict(answer_value)
        if step == "variables" and checked.get("defer_to_import"):
            state["answers"]["defer_to_import"] = True
            return {
                "ok": True,
                "ready": False,
                "reply": {
                    "text": (
                        "好，那这条不走对话：去 **06 导入数据** 把历史表读进来，它会用同一套 run 语义"
                        "直接建 study 并落到算法子参数页。导入完成后回到这里，也能在同一个 study 上继续。\n\n"
                        "**为什么这样分**：条件空间要从你的数据里推断，靠猜区间会毁掉整场实验。"
                    ),
                    "quick_replies": _kickoff_quick_replies("variables")[:2],
                },
                "state": state,
                "options": None,
                "candidates": None,
            }

        answers = _kickoff_merge(state["answers"], checked)
        state["answers"] = answers
        state["missing"] = _kickoff_missing(answers)

        if step == "constraints":
            if state["missing"]:
                # 信息不足：停住并说明缺什么，options 必须是 null（§10.5.3）。
                gap = "、".join(_KICKOFF_MISSING_TEXT[item] for item in state["missing"])
                target = state["missing"][0]
                state["step"] = target
                return {
                    "ok": True,
                    "ready": False,
                    "reply": _kickoff_question_reply(
                        target,
                        f"还差 **{gap}** 才能给你算法选项，所以先停在「{target}」这一步继续问——"
                        "不会用一个凑出来的选项糊过去。",
                    ),
                    "state": state,
                    "options": None,
                    "candidates": None,
                }
            options, picked, others, _ = self._kickoff_options(answers)
            state["step"] = "options"
            return {
                "ok": True,
                "ready": True,
                "reply": {
                    "text": (
                        f"信息够了。condopt 的路由器在你这组答案上的**首选是 {picked}**；"
                        "下面是它按真实打分给出的候选，点一张卡进去就能调该算法的子参数。"
                    ),
                    "quick_replies": [],
                },
                "state": state,
                "options": options,
                "candidates": {"recommended": picked, "others": others},
            }

        next_step = _KICKOFF_NEXT_STEP[step]
        state["step"] = next_step
        if next_step == "options":
            options, picked, others, _ = self._kickoff_options(answers)
            return {
                "ok": True,
                "ready": True,
                "reply": {
                    "text": (
                        f"信息够了。condopt 的路由器在你这组答案上的**首选是 {picked}**；"
                        "下面是候选算法，点进去调子参数。"
                    ),
                    "quick_replies": [],
                },
                "state": state,
                "options": options,
                "candidates": {"recommended": picked, "others": others},
            }
        return {
            "ok": True,
            "ready": False,
            "reply": _kickoff_question_reply(next_step),
            "state": state,
            "options": None,
            "candidates": None,
        }

    def kickoff_plan(self, payload: Any) -> dict:
        """§10.3：参数页数据 —— params 取自 registry，why 取自 router，answers 可直喂 init。"""
        if not isinstance(payload, Mapping):
            raise WorkbenchError(E_BAD_REQUEST, "payload 必须是 JSON 对象")
        known = {"state", "algorithm"}
        unknown = sorted(str(key) for key in payload if str(key) not in known)
        if unknown:
            raise WorkbenchError(
                E_VALIDATION,
                f"plan 的 payload 未知字段: {_display_list(unknown)}",
                f"允许字段: {sorted(known)}",
            )
        state = payload.get("state")
        if not isinstance(state, Mapping):
            raise WorkbenchError(E_VALIDATION, "plan 需要 state（chat 返回的那个）")
        answers_in = state.get("answers")
        if not isinstance(answers_in, Mapping):
            raise WorkbenchError(E_VALIDATION, "state.answers 必须是对象")
        missing = _kickoff_missing(answers_in)
        if missing:
            gap = "、".join(_KICKOFF_MISSING_TEXT[item] for item in missing)
            raise WorkbenchError(
                E_VALIDATION,
                f"还不能生成参数页：缺 {gap}",
                "先把 /api/kickoff/chat 走到 options，再调 plan",
            )
        answers, warnings = self._kickoff_answers_for_init(answers_in)
        algorithm = payload.get("algorithm")
        if algorithm is None:
            algorithm = "auto"
        if not isinstance(algorithm, str) or not algorithm.strip():
            raise WorkbenchError(E_VALIDATION, "algorithm 必须是非空字符串", "例如 bayes / auto")
        _, _, _, recommendation = self._kickoff_context_from_answers(answers)
        route_payload = _to_jsonable(recommendation.to_dict())
        picked = str(route_payload.get("algorithm") or "")
        if algorithm == "auto":
            algorithm = picked
        elif algorithm not in condopt_registry.names():
            raise WorkbenchError(
                E_VALIDATION,
                f"未知算法: {_display(algorithm)}",
                f"可用: auto, {', '.join(condopt_registry.names())}",
            )
        if algorithm != picked:
            warnings.append(
                f"condopt 路由器在你的答案上首选 {picked}（评分 {route_payload.get('candidate_scores', {}).get(picked)}），"
                f"你选了 {algorithm}；why 仍是 router 的选择依据，供对照。"
            )
        described = condopt_registry.describe(algorithm)
        params = [_to_jsonable(_param_display(algorithm, item)) for item in (described.get("params") or [])]
        why = [str(item) for item in (route_payload.get("rationale") or [])]
        if not why:
            described_route = {item["name"]: item for item in condopt_registry.describe_all()}
            why = [str(described_route.get(algorithm, {}).get("when") or "")]
        warnings.extend(str(item) for item in (route_payload.get("warnings") or []))
        return {
            "ok": True,
            "algorithm": algorithm,
            "why": why,
            "params": params,
            "answers": _to_jsonable(answers),
            "warnings": warnings,
            "params_source": "registry.describe_all()（name/type/default/choices 全部代码取值）",
            "display_source_note": (
                "label/plain/impact/advanced 是工作台人工文案（display_source=workbench）；"
                "未覆盖到的参数回落 registry 的 description（display_source=registry）。"
            ),
            "recommended_algorithm": picked,
        }

    # -- SSE -----------------------------------------------------------------
    def stream_scenario(self, scenario_key: str, payload: Any = None) -> Iterable[tuple[str, dict]]:
        """按 Scenario 的 4 段生成 SSE 帧：meta → stage×4 → delta → done。

        ``stage.index`` 严格落在 ``SCENARIOS_BY_KEY[key]["stages"]`` 的下标范围内；
        执行失败时插一帧 ``error``，但**最后仍然发 done**（契约 §4：页面不崩）。
        """
        scenario = SCENARIOS_BY_KEY.get(scenario_key) if isinstance(scenario_key, str) else None
        if scenario is None:
            raise WorkbenchError(
                E_BAD_REQUEST,
                f"未知 scenario: {_display(scenario_key)!r}",
                f"可用: {', '.join(SCENARIOS_BY_KEY)}",
            )
        if payload is None:
            payload = {}
        if not isinstance(payload, Mapping):
            raise WorkbenchError(E_BAD_REQUEST, "payload 必须是 JSON 对象", '例如 {"payload": {}}')
        payload = dict(payload)

        action = scenario["action"]
        run_id = payload.get("run") if isinstance(payload.get("run"), str) else None
        # meta 与 done 必须报同一个 session id（前端拿它做关联）；未给 run 时生成一个。
        session_id = run_id or f"{scenario_key}-{uuid.uuid4().hex[:8]}"
        started = time.monotonic()
        yield ("meta", {"run_id": session_id, "mode": "live",
                        "model": f"condopt-{condopt_version()}"})

        inner = dict(payload)
        inner.pop("run", None)
        stages = scenario["stages"]
        status = "running"
        try:
            # 0 段：校验（不执行）
            yield ("stage", {"index": 0, "label": stages[0], "status": "running"})
            if action in NEEDS_RUN and not run_id:
                raise WorkbenchError(E_VALIDATION, f"{scenario_key} 需要 payload.run", "先 GET /api/runs 选一个 study")
            if action == "prepare":
                self.validate_action("prepare", None, inner)
            else:
                self.validate_action(action, run_id, inner)
            yield ("stage", {"index": 0, "label": stages[0], "status": "done"})

            # 1 段：解析/读取
            yield ("stage", {"index": 1, "label": stages[1], "status": "running"})
            preview = None
            if action == "prepare":
                preview = self.preview_problem(inner["answers"])
            elif action in NEEDS_RUN:
                preview = self.study_snapshot(run_id)
            yield ("stage", {"index": 1, "label": stages[1], "status": "done"})

            # 2 段：执行动作（唯一的重活；写动作在这里加锁）
            yield ("stage", {"index": 2, "label": stages[2], "status": "running"})
            message, result = self.dispatch(action, run_id, inner)
            yield ("stage", {"index": 2, "label": stages[2], "status": "done"})

            # 3 段：组织 Markdown 展示
            yield ("stage", {"index": 3, "label": stages[3], "status": "running"})
            chunks = md_chunks(action, result)
            yield ("stage", {"index": 3, "label": stages[3], "status": "done"})

            for chunk in [f"> {message}\n", *chunks]:
                yield ("delta", {"text": chunk})
            if action == "prepare" and isinstance(preview, Mapping):
                yield ("notice", {
                    "message": f"条件空间：{preview.get('n_variables')} 个变量，"
                               f"预算 {preview.get('budget')}，批量 {preview.get('batch_size')}"
                })
            for note in _notices(action, result):
                yield ("notice", {"message": note})
            status = "ok"
        except WorkbenchError as exc:
            status = "error"
            yield ("error", {"message": exc.message, **({"hint": exc.hint} if exc.hint else {})})
        except Exception as exc:  # noqa: BLE001 - SSE 里任何异常都变成 error 帧，不掀翻页面
            status = "error"
            _log(f"SSE {scenario_key} 异常：{type(exc).__name__}: {exc}")
            yield ("error", {"message": f"服务端异常（{type(exc).__name__}）", "hint": "看服务端日志"})
        finally:
            yield ("done", {
                "run_id": session_id,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "mode": "live",
                "status": status,
            })


# --------------------------------------------------------------------------- #
# 导入已有数据：读表 / 画像 / 规则推断 / 模型读表（CONTRACT §9）
# --------------------------------------------------------------------------- #


class _RowError(ValueError):
    """单行数据不可用（带列名原因），只用于收集 rejected，不中断导入。"""


def _scan_id(path: Path, size: int, mtime: float) -> str:
    """§9.3：``sha256(path+size+mtime)`` 前 16 位（只认元数据，不读内容）。"""
    return hashlib.sha256(f"{path}|{size}|{mtime}".encode("utf-8")).hexdigest()[:16]


def _file_sha256(path: Path) -> str:
    """文件内容 sha256（审计用；分块读，避免整文件进内存两遍）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and value.strip() == ""


def _to_number(value: Any) -> float | None:
    """数值化：bool 不算数值；字符串只接受纯十进制（不做千分位猜测）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "yes", "y", "1", "是", "t"):
            return True
        if text in ("false", "no", "n", "0", "否", "f"):
            return False
    return None


def _decode_text(path: Path) -> tuple[str, str]:
    """按 utf-8-sig → utf-8 → gbk → latin-1 顺序解码；返回 (文本, 报告的编码名)。"""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        return text, ("utf-8" if encoding == "utf-8-sig" else encoding)
    return raw.decode("utf-8", errors="replace"), "utf-8"


#: 展开 condopt 观测行时保留的顶层键（``x``/``objectives``/``constraints`` 会展开成前缀列）。
_OBSERVATION_KEYS = ("x", "objectives", "constraints", "y", "fidelity", "cost", "source")
_OBSERVATION_PREFIX = {"x": "x.", "objectives": "obj.", "constraints": "con."}


def _flatten_row(obj: Mapping) -> dict:
    """把 condopt 观测行拍平成宽表行（``x.T`` / ``obj.yield`` / ``con.purity`` …）。

    JSON/JSONL 若已经是 condopt 观测（带 ``x``），拍平后才能与 CSV 走同一条映射管线；
    ``meta``/``schema_version``/``actor`` 是执行元数据，不参与映射。
    """
    row: dict = {}
    for key, value in obj.items():
        name = str(key)
        if name in ("meta", "schema_version", "actor"):
            continue
        if name in _OBSERVATION_PREFIX and isinstance(value, Mapping):
            prefix = _OBSERVATION_PREFIX[name]
            for inner, inner_value in value.items():
                row[f"{prefix}{inner}"] = inner_value
        elif isinstance(value, Mapping):
            row[name] = json.dumps(value, ensure_ascii=False)
        else:
            row[name] = value
    return row


def _read_import_table(path: Path) -> dict:
    """读文件 → ``{header, rows, encoding, delimiter, has_header, format, warnings}``。"""
    suffix = path.suffix.lower()
    text, encoding = _decode_text(path)
    warnings: list[str] = []
    if suffix in (".json", ".jsonl"):
        if suffix == ".jsonl":
            objects = []
            for index, line in enumerate(text.splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise WorkbenchError(
                        E_VALIDATION, f"JSONL 第 {index} 行不是合法 JSON: {exc}", "每行一个 JSON 对象"
                    ) from exc
                if not isinstance(item, Mapping):
                    raise WorkbenchError(E_VALIDATION, f"JSONL 第 {index} 行不是对象")
                objects.append(item)
        else:
            try:
                loaded = json.loads(text)
            except json.JSONDecodeError as exc:
                raise WorkbenchError(E_VALIDATION, f"JSON 解析失败: {exc}", "应为对象数组") from exc
            if isinstance(loaded, Mapping) and isinstance(loaded.get("observations"), list):
                loaded = loaded["observations"]
            if not isinstance(loaded, list) or not all(isinstance(item, Mapping) for item in loaded):
                raise WorkbenchError(E_VALIDATION, "JSON 必须是对象数组（或 {\"observations\": [...]}）")
            objects = loaded
        rows = [_flatten_row(item) for item in objects]
        header: list[str] = []
        for row in rows:
            for key in row:
                if key not in header:
                    header.append(key)
        return {
            "header": header,
            "rows": rows,
            "encoding": encoding,
            "delimiter": None,
            "has_header": True,
            "format": "observations" if any("x." in row for row in rows[:1]) else "json",
            "warnings": warnings,
        }

    delimiter = "\t" if suffix == ".tsv" else ","
    hard_warnings: list[str] = []
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        if suffix == ".txt":
            hard_warnings.append("无法嗅探分隔符，按逗号处理。")
    try:
        has_header = bool(csv.Sniffer().has_header(text[:8192]))
    except csv.Error:
        has_header = True
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    all_rows = [row for row in reader if any(str(cell).strip() for cell in row)]
    if not all_rows:
        raise WorkbenchError(E_VALIDATION, "文件没有数据行", "至少需要一行表头 + 一行数据")
    if has_header:
        header = [str(cell).strip() for cell in all_rows[0]]
        data_rows = all_rows[1:]
    else:
        header = [f"col{index + 1}" for index in range(len(all_rows[0]))]
        data_rows = all_rows
        hard_warnings.append("未检测到表头，列名按 col1/col2… 生成。")
    width = len(header)
    rows = []
    ragged = 0
    for raw_row in data_rows:
        if len(raw_row) != width:
            ragged += 1
        row = {}
        for index, name in enumerate(header):
            row[name] = raw_row[index] if index < len(raw_row) else None
        rows.append(row)
    if ragged:
        hard_warnings.append(f"{ragged} 行列数与表头不一致，缺失单元格按空值处理。")
    return {
        "header": header,
        "rows": rows,
        "encoding": encoding,
        "delimiter": delimiter,
        "has_header": has_header,
        "format": "wide",
        "warnings": hard_warnings,
    }


def _profile_column(name: str, values: list) -> dict:
    """单列画像：全部字段来自真实统计，缺就 None（§9.3：不许编造）。"""
    present = [value for value in values if not _is_missing(value)]
    missing = len(values) - len(present)
    numbers = [_to_number(value) for value in present]
    numeric_values = [number for number in numbers if number is not None]
    numeric_total = len(numbers)
    non_numeric = numeric_total - len(numeric_values)
    # 80% 阈值：实验表里偶尔的 'n/a' 不该把整列判成 text——否则规则映射会把数值列当分类列，
    # 而契约 §9.5 的 rejected 例子（"'T' 不是数值：'n/a'"）正是「列是数值、个别行坏」这一情形。
    numeric = numeric_total > 0 and len(numeric_values) / numeric_total >= 0.8
    bools = [_to_bool(value) for value in present]
    boolean = bool(present) and all(item is not None for item in bools)
    unique_values: list = []
    for value in present:
        if value not in unique_values:
            unique_values.append(value)
    if numeric:
        inferred = "integer" if all(float(number).is_integer() for number in numeric_values) else "number"
    elif boolean and len(unique_values) <= 2:
        inferred = "bool"
    else:
        inferred = "text"
    profile = {
        "name": name,
        "inferred": inferred,
        "missing": missing,
        "unique": len(unique_values),
        "min": min(numeric_values) if numeric else None,
        "max": max(numeric_values) if numeric else None,
        "non_numeric": non_numeric if numeric else None,
        "choices": (
            [str(value) for value in unique_values]
            if (not numeric and len(unique_values) <= 50 and unique_values)
            else None
        ),
        "samples": [str(value) for value in unique_values[:3]],
    }
    return profile


def _profile_columns(table: dict) -> list[dict]:
    header = table["header"]
    rows = table["rows"]
    return [_profile_column(name, [row.get(name) for row in rows]) for name in header]


def _default_kind(profile: dict) -> str:
    inferred = profile.get("inferred")
    if inferred in ("integer", "number"):
        return "integer" if inferred == "integer" and (profile.get("unique") or 0) > 1 else "continuous"
    if inferred == "bool":
        return "bool"
    return "categorical"


def _rule_mapping(columns: list[dict], table: dict) -> tuple[dict, list[str]]:
    """离线规则推断（§9.3 的 ``rule_mapping``）：列名 + 类型 + 取值范围。"""
    warnings: list[str] = []
    by_name = {profile["name"]: profile for profile in columns}
    lower = {profile["name"].lower(): profile for profile in columns}

    objective: dict | None = None
    for hint in OBJECTIVE_HINTS:
        for name, profile in lower.items():
            if name == hint and profile.get("inferred") in ("number", "integer"):
                objective = {"column": profile["name"], "name": profile["name"], "direction": "max"}
                break
        if objective:
            break
    if objective is None:
        for hint in OBJECTIVE_HINTS:
            for name, profile in lower.items():
                if hint in name and profile.get("inferred") in ("number", "integer"):
                    objective = {"column": profile["name"], "name": profile["name"], "direction": "max"}
                    break
            if objective:
                break
    if objective is None:
        numeric = [profile for profile in columns if profile.get("inferred") in ("number", "integer")]
        if numeric:
            profile = numeric[-1]
            objective = {"column": profile["name"], "name": profile["name"], "direction": "max"}
            warnings.append(
                f"未找到明显的目标列，暂把最后一个数值列 {profile['name']!r} 当目标；请核对。"
            )

    variables: list[dict] = []
    excluded: list[str] = []
    for profile in columns:
        name = profile["name"]
        if _is_missing(profile.get("inferred")) or not profile["samples"]:
            warnings.append(f"列 {name!r} 全部为空，未参与映射。")
            continue
        if objective and name == objective["column"]:
            continue
        lowered = name.lower()
        is_condition_like = lowered.startswith(("x.", "x:")) or lowered.startswith("con.")
        if not is_condition_like and (
            lowered in NON_CONDITION_COLUMNS
            or lowered.endswith(("_id", "id"))
            or lowered in ("fidelity", "cost")
        ):
            excluded.append(name)
            continue
        kind = _default_kind(profile)
        entry = {"column": name, "name": name, "kind": kind}
        if profile.get("non_numeric"):
            warnings.append(
                f"列 {name!r} 有 {profile['non_numeric']} 个非数值单元格，导入时这些行会被拒绝。"
            )
        if kind in ("continuous", "integer", "log"):
            entry["low"] = profile.get("min")
            entry["high"] = profile.get("max")
            if entry["low"] is None or entry["high"] is None:
                warnings.append(f"列 {name!r} 缺少数值范围，已降级为 categorical。")
                entry = {"column": name, "name": name, "kind": "categorical",
                         "choices": profile.get("choices") or []}
        elif kind in ("categorical", "ordinal"):
            entry["choices"] = profile.get("choices") or []
        variables.append(entry)
    if excluded:
        for name in excluded:
            lowered = name.lower()
            if lowered in ("batch", "plate", "well"):
                warnings.append(f"{name!r} 列疑似板号/批次，未放入条件空间（执行元数据）。")
            elif lowered in ("fidelity", "cost"):
                warnings.append(f"{name!r} 列疑似保真度/成本，未放入条件空间（可单独映射）。")
            else:
                warnings.append(f"{name!r} 列疑似标识/备注，未放入条件空间。")

    fidelity = None
    cost = None
    for profile in columns:
        lowered = profile["name"].lower()
        if lowered in ("fidelity", "f."):
            fidelity = {"column": profile["name"], "name": profile["name"]}
        elif lowered == "cost":
            cost = {"column": profile["name"], "name": profile["name"]}

    mapping = {
        "variables": variables,
        "objective": objective,
        "constraints": [],
        "fidelity": fidelity,
        "cost": cost,
    }
    if objective:
        warnings.append("目标方向默认按 max 处理，请在确认表里核对 min/max。")
    else:
        warnings.append("未能推断目标列，请在确认表里手动指定。")
    return mapping, warnings


#: 发给模型的系统提示（§9.4：要求严格 JSON 输出；只发解析结果，不发解释）。
_LLM_MAPPING_SYSTEM_PROMPT = (
    "你是实验数据列语义分析器。输入是【表头 + 每列画像 + 前若干行样本】，"
    "你要判断哪些列是可控实验条件、哪一列是优化目标、哪些列是数值约束。"
    "只输出一个 JSON 对象，不要解释文字、不要 markdown 代码围栏。字段形状：\n"
    '{"variables":[{"column":"列名","name":"变量名","kind":"continuous|integer|log|categorical|bool|ordinal",'
    '"low":数值,"high":数值,"unit":"单位或省略","choices":["分类取值"]}],'
    '"objective":{"column":"列名","name":"指标名","direction":"max|min"},'
    '"constraints":[{"column":"列名","op":">=|<=|>|<|==","value":数值}],'
    '"fidelity":"列名或 null","cost":"列名或 null",'
    '"confidence":0到1,"rationale":"一句话理由","warnings":["不确定之处"]}'
)


def _base_url_error(raw: str) -> str | None:
    """校验 ``CONDOPT_IMPORT_LLM_BASE_URL`` 的实际生效值；返回错误说明或 ``None``。

    这些配置错必须在**发起调用前**判掉：含空白/坏端口的 URL 会让 ``urllib`` 抛
    ``http.client.InvalidURL``（它不是 ``UnicodeEncodeError`` 的子类），不判就会变成
    500 + 异常类名（task-3 F7）。显式置空也不许静默回退到默认端点（F8）。
    非 ASCII 主机同样判为错（D5）：HTTP 请求行/Host 头只能 ASCII，若这里放行，
    ``bootstrap.url_valid`` 就会与实际 ``map`` 503 矛盾——预检信号不许说谎。
    每条 message 自带环境变量名，方便前端直接展示 ``url_error``。
    """
    env = "环境变量 CONDOPT_IMPORT_LLM_BASE_URL"
    text = (raw or "").strip()
    if not text:
        return f"{env} 为空/纯空白（显式置空不会回退到默认端点）"
    if any(char.isspace() for char in text):
        return f"{env} 含空白字符"
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        # 需要国际化域名请自己写 punycode（xn--…）；两条路径判定保持一致。
        return f"{env} 含非 ASCII 字符（主机名请用 punycode，即 xn--… 形式）"
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme not in ("http", "https"):
        return f"{env} 必须以 http:// 或 https:// 开头"
    if not parsed.netloc or not parsed.hostname:
        return f"{env} 缺少主机名"
    try:
        parsed.port  # 非法端口在取值时抛 ValueError
    except ValueError:
        return f"{env} 的端口不合法"
    if not parsed.path.rstrip("/").endswith("/v1") and "/chat/completions" in parsed.path:
        return f"{env} 应填到 /v1 为止（不要带 /chat/completions，后端会自己拼）"
    return None


def _check_api_key_for_header(api_key: str, env_name: str) -> None:
    """HTTP 头只能装 latin-1 且不得含控制字符（含 ``\\r``/``\\n`` 会造成头注入）。

    非 ASCII / 控制字符的密钥是**配置错误**：必须报 ``E_LLM_UNAVAILABLE``，
    不能让它以 ``UnicodeEncodeError`` 的形式变成 500（task-3 F6）。
    错误信息只点名环境变量名，**绝不回显密钥内容**。
    """
    has_control = any(ord(char) < 0x20 or ord(char) == 0x7F for char in api_key)
    try:
        api_key.encode("latin-1")
        latin1_ok = True
    except UnicodeEncodeError:
        latin1_ok = False
    if has_control or not latin1_ok:
        detail = "含非 ASCII 字符" if not latin1_ok else "含控制字符（换行等）"
        raise WorkbenchError(
            E_LLM_UNAVAILABLE,
            f"环境变量 {env_name} 的值{detail}，无法作为 HTTP 头发送（Authorization 头只能是 latin-1）",
            f"检查环境变量 {env_name}：换成正常 API key（纯 ASCII、无换行），"
            "或让前端改走 fallback:\"rules\"（离线规则映射）",
        )


def _import_sample_rows(value: Any) -> int:
    """``sample_rows`` 校验（§9.3）：缺省 12，允许 1..50，越界 **422**（不静默夹取）。"""
    if value is None:
        return DEFAULT_IMPORT_SAMPLE_ROWS
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkbenchError(
            E_VALIDATION,
            "sample_rows 必须是整数",
            f"默认 {DEFAULT_IMPORT_SAMPLE_ROWS}，上限 {MAX_IMPORT_SAMPLE_ROWS}",
        )
    if value < 1 or value > MAX_IMPORT_SAMPLE_ROWS:
        raise WorkbenchError(
            E_VALIDATION,
            f"sample_rows 越界：{value}（允许 1..{MAX_IMPORT_SAMPLE_ROWS}）",
            f"默认 {DEFAULT_IMPORT_SAMPLE_ROWS}；外发内容只能是表头 + 画像 + 前 N 行样本",
        )
    return int(value)


def _request_body_bytes(body: Mapping) -> int:
    """请求体的**真实字节数**（UTF-8）——与 ``_llm_chat`` 实际编码方式完全一致。

    提示词里有中文，字符数 ≠ 字节数；``scan.sample_preview.bytes`` 与 ``map.sent.bytes``
    必须共用这一条构造路径，否则契约 §9.3 的硬不变量就被破坏。
    """
    return len(json.dumps(body, ensure_ascii=False).encode("utf-8"))


def _llm_request_body(model: str, scan: dict, sample_rows: int) -> dict:
    """构造 OpenAI 兼容请求体：**只含表头 + 列画像 + 前 N 行样本**（§9.1 最小外发）。"""
    samples = scan["samples"][:sample_rows]
    user_payload = {
        "columns": scan["columns"],
        "sample_rows": samples,
        "n_rows": scan["n_rows"],
        "supported_kinds": list(SUPPORTED_KINDS),
        "supported_ops": list(SUPPORTED_OPS),
        "note": "只根据表头/画像/样本判断列角色；不确定的写进 warnings。",
    }
    return {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": _LLM_MAPPING_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
    }


def _llm_repair_body(model: str, raw: str) -> dict:
    """「只修格式」的第二次请求（§9.4）：保持语义不变，只把内容变成合法 JSON。"""
    return {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": "把用户给的文本转换成合法 JSON 对象（只修格式，不要新增/删除语义）。"
                           "只输出 JSON，不要解释、不要代码围栏。",
            },
            {"role": "user", "content": raw[:4000]},
        ],
    }


def _extract_json_object(text: str) -> dict | None:
    """从模型输出里提取 JSON 对象（容忍代码围栏与前后废话）。"""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped).strip()
    candidates = [stripped]
    start, end = stripped.find("{"), stripped.rfind("}")
    if start >= 0 and end > start:
        candidates.append(stripped[start:end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return None


def _mapping_from_llm_json(parsed: Mapping) -> tuple[dict, Any, str, list[str]]:
    """把模型返回的 JSON 拆成 (mapping, confidence, rationale, warnings)。"""
    warnings: list[str] = []
    raw_warnings = parsed.get("warnings")
    if isinstance(raw_warnings, list):
        warnings.extend(str(item) for item in raw_warnings)
    mapping = parsed.get("mapping") if isinstance(parsed.get("mapping"), Mapping) else parsed
    confidence = parsed.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = None
    rationale = str(parsed.get("rationale") or "").strip()
    return dict(mapping), confidence, rationale, warnings


def _normalise_mapping(
    raw: Any,
    columns: list[dict] | None,
    header: list[str] | None,
    *,
    strict: bool = False,
) -> tuple[dict, list[str]]:
    """校验/归一化 mapping（§9.4）。

    ``strict=True``（apply，用户确认过的 mapping）→ 结构问题直接 422；
    ``strict=False``（模型建议）→ 非法 kind 按画像降级并写 warning，不静默丢弃。
    """
    if strict and not isinstance(raw, Mapping):
        raise WorkbenchError(E_VALIDATION, "mapping 必须是 JSON 对象", "形状见 §9.4")

    def fail(message: str, hint: str = "") -> None:
        if strict:
            raise WorkbenchError(E_VALIDATION, message, hint)
        warnings.append(message)

    warnings: list[str] = []
    raw = raw if isinstance(raw, Mapping) else {}
    by_column = {profile["name"]: profile for profile in (columns or [])}
    known = set(header) if header is not None else None

    if strict:
        allowed = {"variables", "objective", "constraints", "fidelity", "cost"}
        unknown = sorted(str(key) for key in raw if str(key) not in allowed)
        if unknown:
            raise WorkbenchError(
                E_VALIDATION,
                f"mapping 未知字段: {_display_list(unknown)}",
                f"允许字段: {sorted(allowed)}",
            )

    def resolve_column(value: Any, where: str) -> str | None:
        if not isinstance(value, str) or not value.strip():
            fail(f"{where} 缺少 column")
            return None
        name = value.strip()
        if known is not None and name not in known:
            fail(f"{where} 引用了表里没有的列 {_display(name)!r}")
            return None
        return name

    variables: list[dict] = []
    seen_columns: set[str] = set()
    raw_variables = raw.get("variables")
    if raw_variables is None:
        raw_variables = []
    if not isinstance(raw_variables, list):
        fail("mapping.variables 必须是数组")
        raw_variables = []
    for index, item in enumerate(raw_variables):
        where = f"mapping.variables[{index}]"
        if not isinstance(item, Mapping):
            fail(f"{where} 必须是对象")
            continue
        column = resolve_column(item.get("column"), where)
        if column is None:
            continue
        if column in seen_columns:
            fail(f"列 {column!r} 在 variables 里重复出现，已忽略重复项。")
            continue
        seen_columns.add(column)
        name = str(item.get("name") or column)
        profile = by_column.get(column)
        kind = item.get("kind")
        if kind not in SUPPORTED_KINDS:
            fallback = _default_kind(profile) if profile else "categorical"
            warnings.append(f"列 {column!r} 的 kind={_display(kind)!r} 不受支持，已按 {fallback} 处理。")
            kind = fallback
        entry: dict = {"column": column, "name": name, "kind": kind}
        if kind in ("continuous", "integer", "log"):
            low = item.get("low", profile.get("min") if profile else None)
            high = item.get("high", profile.get("max") if profile else None)
            low_number = _to_number(low)
            high_number = _to_number(high)
            if low_number is None or high_number is None:
                if strict:
                    raise WorkbenchError(
                        E_VALIDATION,
                        f"变量 {name!r}（{kind}）缺少 low/high",
                        "数值/对数变量需要 low/high（可参考 scan 的 min/max）",
                    )
                kind = "categorical"
                entry = {"column": column, "name": name, "kind": "categorical",
                         "choices": (profile or {}).get("choices") or []}
                warnings.append(f"列 {column!r} 缺少数值范围，已降级为 categorical。")
            elif low_number > high_number:
                raise WorkbenchError(E_VALIDATION, f"变量 {name!r} 的 low({low_number}) > high({high_number})")
            else:
                entry["low"] = low_number
                entry["high"] = high_number
                if item.get("unit") is not None:
                    entry["unit"] = str(item["unit"])
        elif kind in ("categorical", "ordinal"):
            choices = item.get("choices") or (profile or {}).get("choices") or []
            if not isinstance(choices, list) or not choices:
                # 前端确认表目前不带 choices（§9.4 冻结形状里它是可选的）；apply 会从真实数据
                # 取该列的 distinct 值补齐（见 `_fill_missing_choices`），所以这里只提醒不拦。
                warnings.append(f"分类变量 {name!r} 没有 choices，将按数据里出现的取值补齐。")
                choices = []
            entry["choices"] = [str(choice) for choice in choices]
        variables.append(entry)

    objective = None
    raw_objective = raw.get("objective")
    if raw_objective is not None:
        if not isinstance(raw_objective, Mapping):
            fail("mapping.objective 必须是对象")
        else:
            column = resolve_column(raw_objective.get("column"), "mapping.objective")
            if column:
                direction = raw_objective.get("direction")
                if direction not in ("max", "min"):
                    warnings.append(f"目标方向 {_display(direction)!r} 非法，已按 max 处理。")
                    direction = "max"
                objective = {
                    "column": column,
                    "name": str(raw_objective.get("name") or column),
                    "direction": direction,
                }

    constraints: list[dict] = []
    raw_constraints = raw.get("constraints")
    if raw_constraints is None:
        raw_constraints = []
    if not isinstance(raw_constraints, list):
        fail("mapping.constraints 必须是数组")
        raw_constraints = []
    for index, item in enumerate(raw_constraints):
        where = f"mapping.constraints[{index}]"
        if not isinstance(item, Mapping):
            fail(f"{where} 必须是对象")
            continue
        column = resolve_column(item.get("column"), where)
        if column is None:
            continue
        op = item.get("op")
        if op not in SUPPORTED_OPS:
            fail(f"{where} 的 op={_display(op)!r} 不支持（{', '.join(SUPPORTED_OPS)}）")
            continue
        value = _to_number(item.get("value"))
        if value is None:
            fail(f"{where} 的 value 必须是数值")
            continue
        constraints.append({"column": column, "op": op, "value": value})

    def _column_ref(value: Any, where: str) -> dict | None:
        """§9.4 冻结形状：``{"column": str, "name": str}``（也容忍裸字符串写法）。"""
        if value is None:
            return None
        if isinstance(value, str):
            raw_column, raw_name, levels = value, value, None
        elif isinstance(value, Mapping):
            raw_column = value.get("column") or value.get("name")
            raw_name = value.get("name") or raw_column
            levels = value.get("levels")
        else:
            fail(f"{where} 必须是 {{\"column\": …}} 或列名字符串")
            return None
        column = resolve_column(raw_column, where)
        if column is None:
            return None
        ref = {"column": column, "name": str(raw_name or column)}
        if where.endswith("fidelity") and levels is not None:
            if not isinstance(levels, Mapping) or not levels:
                fail(f"{where}.levels 必须是「原值 → 数值」的对象")
            else:
                cleaned_levels = {}
                for key, item in levels.items():
                    number = _to_number(item)
                    if number is None:
                        fail(f"{where}.levels[{_display(key)!r}] 必须是数值")
                        continue
                    cleaned_levels[str(key)] = number
                if cleaned_levels:
                    ref["levels"] = cleaned_levels
        return ref

    fidelity = _column_ref(raw.get("fidelity"), "mapping.fidelity")
    cost = _column_ref(raw.get("cost"), "mapping.cost")

    mapping = {
        "variables": variables,
        "objective": objective,
        "constraints": constraints,
        "fidelity": fidelity,
        "cost": cost,
    }
    return mapping, warnings


def _coerce_import_cell(value: Any, kind: str, column: str) -> Any:
    """按变量 kind 把单元格转成 condopt 需要的类型；失败抛 :class:`_RowError`。"""
    if _is_missing(value):
        raise _RowError(f"'{column}' 为空")
    if kind in ("continuous", "log"):
        number = _to_number(value)
        if number is None:
            raise _RowError(f"'{column}' 不是数值：{value!r}")
        return number
    if kind == "integer":
        number = _to_number(value)
        if number is None or abs(number - round(number)) > 1e-9:
            raise _RowError(f"'{column}' 不是整数：{value!r}")
        return int(round(number))
    if kind == "bool":
        parsed = _to_bool(value)
        if parsed is None:
            raise _RowError(f"'{column}' 不是布尔值：{value!r}")
        return parsed
    return str(value)


def _space_from_mapping(mapping: dict) -> SearchSpace:
    """mapping.variables → ``SearchSpace``（去掉 mapping 专用字段 ``column``）。"""
    variables = [
        {key: value for key, value in item.items() if key != "column"} for item in mapping["variables"]
    ]
    return SearchSpace.from_dict({"variables": variables})


def _objective_from_mapping(mapping: dict) -> Objective:
    """mapping.objective + constraints → ``Objective``（约束名用列名）。"""
    objective = mapping["objective"]
    constraints = [item["column"] for item in mapping.get("constraints") or []]
    limits = {
        item["column"]: {"op": item["op"], "value": item["value"]}
        for item in mapping.get("constraints") or []
    }
    return Objective.from_dict(
        {
            "name": str(objective.get("name") or objective["column"]),
            "direction": objective.get("direction") or "max",
            "constraints": constraints,
            "constraint_limits": limits,
        }
    )


def _fidelity_value(ref: Mapping, row: Mapping) -> float:
    """把 fidelity 列的原值转成数值；``levels`` 给了「原值 → 数值」换算表就优先用它。"""
    column = str(ref["column"])
    raw = row.get(column)
    levels = ref.get("levels")
    if isinstance(levels, Mapping):
        key = str(raw).strip() if raw is not None else ""
        if key in levels:
            return float(levels[key])
        if _to_number(raw) is not None:
            return float(_to_number(raw))
        raise _RowError(
            f"'{column}' 的保真档位 {raw!r} 不在 levels 表里（可用：{', '.join(str(k) for k in levels)}）"
        )
    return float(_coerce_import_cell(raw, "continuous", column))


def _suggested_budget(accepted: int) -> int:
    """budget 不足时给前端的建议值（§9.5 的 ``suggested_budget``）。

    口径：``accepted + max(10, ceil(accepted/3))`` —— 留出约 1/3 的后续探索轮次，
    对 90 行先验恰好给出契约示例里的 120。
    """
    accepted = max(0, int(accepted))
    return max(1, accepted + max(10, math.ceil(accepted / 3)))


def _fill_missing_choices(table: dict, mapping: dict) -> list[str]:
    """分类/序数变量缺 ``choices`` 时，用真实数据里的 distinct 值补齐（不伪造取值）。"""
    warnings: list[str] = []
    for variable in mapping["variables"]:
        if variable["kind"] not in ("categorical", "ordinal") or variable.get("choices"):
            continue
        values: list[str] = []
        for row in table["rows"]:
            cell = row.get(variable["column"])
            if _is_missing(cell):
                continue
            text = str(cell)
            if text not in values:
                values.append(text)
        if not values:
            raise WorkbenchError(
                E_VALIDATION,
                f"分类变量 {variable['name']!r} 没有 choices，且列 {variable['column']!r} 没有任何取值",
                "在确认表里改成数值列，或补上 choices",
            )
        variable["choices"] = values
        warnings.append(
            f"分类变量 {variable['name']!r} 的 choices 由数据补齐（{len(values)} 个取值）。"
        )
    return warnings


def _rows_to_observations(table: dict, mapping: dict, space: SearchSpace) -> tuple[list[dict], list[dict]]:
    """按 mapping 把每行转成观测 dict；坏行逐行给原因（§9.5：不静默丢）。"""
    observations: list[dict] = []
    rejected: list[dict] = []
    objective = mapping["objective"]
    constraints = mapping.get("constraints") or []
    fidelity_ref = mapping.get("fidelity")
    cost_ref = mapping.get("cost")
    for index, row in enumerate(table["rows"], start=1):
        try:
            x = {
                variable["name"]: _coerce_import_cell(row.get(variable["column"]), variable["kind"], variable["column"])
                for variable in mapping["variables"]
            }
            y_value = _coerce_import_cell(row.get(objective["column"]), "continuous", objective["column"])
            values = {}
            for item in constraints:
                values[item["column"]] = _coerce_import_cell(row.get(item["column"]), "continuous", item["column"])
        except _RowError as exc:
            rejected.append({"row": index, "reason": str(exc)})
            continue
        fidelity = 1.0
        if fidelity_ref:
            try:
                fidelity = _fidelity_value(fidelity_ref, row)
            except _RowError as exc:
                rejected.append({"row": index, "reason": str(exc)})
                continue
        cost = None
        if cost_ref:
            try:
                cost = _coerce_import_cell(row.get(cost_ref["column"]), "continuous", cost_ref["column"])
            except _RowError as exc:
                rejected.append({"row": index, "reason": str(exc)})
                continue
        try:
            validated = space.validate_config(x)
        except ValueError as exc:
            rejected.append({"row": index, "reason": f"条件不在空间内：{exc}"})
            continue
        observation: dict = {
            "x": validated,
            "y": y_value,
            "fidelity": float(fidelity),
            "source": "import",
            "meta": {"import_row": index},
        }
        if values:
            observation["constraints"] = values
        if cost is not None:
            observation["cost"] = float(cost)
        observations.append(observation)
    return observations, rejected


def _dedupe_key(observation: Mapping, space: SearchSpace) -> tuple:
    """与 ``Study.import_observations`` 完全同口径的去重键（space.encode + fidelity）。"""
    validated = space.validate_config(observation["x"])
    encoded = tuple(float(item) for item in space.encode(validated).round(9))
    return (encoded, round(float(observation.get("fidelity", 1.0)), 6))


# --------------------------------------------------------------------------- #
# CSV 回填（与 condopt.cli._results_from_csv 同一套列约定，避免两套语义）
# --------------------------------------------------------------------------- #


def _coerce_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text == "":
        return None
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        number = float(text)
    except ValueError:
        return text
    return int(number) if number.is_integer() and "." not in text and "e" not in lowered else number


def _observations_from_csv(text: str) -> list[dict]:
    """解析回填 CSV（列约定同 `condopt tell --results`，见 cli 模块 docstring）。"""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if reader.fieldnames is None:
        raise ValueError("CSV 没有表头")
    rows: list[dict] = []
    for raw in reader:
        record: dict = {}
        config: dict = {}
        objectives: dict = {}
        constraints: dict = {}
        prefixed = any(
            str(key).lower().startswith(("x.", "x:", "obj.", "obj:", "con.", "con:"))
            for key in raw
            if key is not None
        )
        for key, value in raw.items():
            if key is None:
                continue
            name = str(key).strip()
            lower = name.lower()
            cooked = _coerce_cell(value)
            if lower in ("x.", "x:"):
                continue
            if lower.startswith(("x.", "x:")):
                config[name[2:]] = cooked
            elif lower.startswith(("obj.", "obj:")):
                objectives[name[4:]] = cooked
            elif lower.startswith(("con.", "con:")):
                constraints[name[4:]] = cooked
            elif lower in ("objectives", "objective"):
                parsed = cooked if isinstance(cooked, dict) else json.loads(str(value))
                objectives.update(parsed)
            elif lower in ("constraints", "constraint"):
                parsed = cooked if isinstance(cooked, dict) else json.loads(str(value))
                constraints.update(parsed)
            elif lower in ("y", "score", "value", "target"):
                record["y"] = cooked
            elif lower in ("fidelity", "f"):
                record["fidelity"] = cooked
            elif lower == "plate":
                record["plate"] = cooked
            elif lower == "well":
                record["well"] = cooked
            elif lower in ("id", "suggestion_id"):
                record["id"] = cooked
            elif lower == "source":
                record["source"] = cooked
            elif lower == "cost":
                record["cost"] = cooked
            elif not prefixed:
                config[name] = cooked
        record["x"] = config
        if objectives:
            record["objectives"] = objectives
        if constraints:
            record["constraints"] = constraints
        if (record.get("plate") or record.get("well")) and "meta" not in record:
            meta = {}
            if record.get("plate"):
                meta["plate"] = record["plate"]
            if record.get("well"):
                meta["plate_well"] = record["well"]
            record["meta"] = meta
        record.pop("plate", None)
        record.pop("well", None)
        rows.append(record)
    if not rows:
        raise ValueError("CSV 没有数据行")
    return rows


def _normalize_results(rows: list[dict], study: Study) -> list[dict]:
    """把朴素的目标/约束列从条件字典里挪出来（同 condopt.cli._normalize_results）。"""
    objective = study.objective
    metric_names = list(objective.objectives) or [objective.name]
    if study.spec is not None:
        metric_names = list(study.spec.metrics)
    constraint_names = list(objective.constraints)
    if study.spec is not None and study.spec.constraints:
        constraint_names = list(study.spec.constraints)
    for row in rows:
        config = row.get("x") or {}
        if row.get("y") is None and objective.name in config:
            row["y"] = config.pop(objective.name)
        for name in metric_names:
            if name in config:
                row.setdefault("objectives", {})[name] = config.pop(name)
        for name in constraint_names:
            if name in config:
                row.setdefault("constraints", {})[name] = config.pop(name)
    return rows


# --------------------------------------------------------------------------- #
# 结构化结果 → Markdown（只做展示，不参与判定）
# --------------------------------------------------------------------------- #


def _json_block(payload: Any) -> str:
    return "```json\n" + json_dumps(payload) + "\n```"


def _md_stages(route_payload: dict, context: Any) -> str:
    lines = [
        "### 算法与开工步骤",
        "",
        f"- 路由结论：**{route_payload.get('algorithm')}**（置信度 {route_payload.get('confidence')}）",
    ]
    rationale = route_payload.get("rationale") or []
    if rationale:
        lines += ["- 理由：", *[f"  - {item}" for item in rationale[:5]]]
    params = route_payload.get("params") or {}
    if params:
        lines += ["- 推荐参数：", _json_block(params)]
    alternatives = route_payload.get("alternatives") or []
    if alternatives:
        lines.append("- 备选：")
        for item in alternatives[:3]:
            lines.append(
                f"  - `{item.get('algorithm')}`（{item.get('score')}）：{item.get('why')}"
            )
    warnings = route_payload.get("warnings") or []
    for warning in warnings:
        lines.append(f"- ⚠ {warning}")
    lines += [
        "",
        "**开工步骤**：确认条件空间 → `init` 建 study → `ask` 生成首批 → 实验后 `tell` 回填 "
        "→ 循环至停止口径触发 → `delivery` 自查 → `render` 看板。",
        f"- 预算 {int(getattr(context, 'budget_total', 0) or 0)} 次，批量 "
        f"{int(getattr(context, 'batch_size', 0) or 0)}；维度 {int(getattr(context, 'n_dims', 0) or 0)}。",
    ]
    return "\n".join(lines)


def _md_stopping(stopping: dict) -> str:
    lines = [
        "### 停止口径",
        "",
        f"- 推荐：**{stopping.get('recommended')}**",
        f"- 为什么：{stopping.get('why')}",
        f"- 交付槽位：{stopping.get('delivery_slots')} 次；可探索约 "
        f"{stopping.get('effective_exploration_budget')} 次",
        f"- 怎么开：{stopping.get('how_to_enable')}",
    ]
    if stopping.get("delivery_note"):
        lines.append(f"- 交付说明：{stopping['delivery_note']}")
    return "\n".join(lines)


def _md_feasibility(feasibility: dict) -> str:
    fraction = feasibility.get("estimated_feasible_fraction")
    lines = [
        "### 可行率预警",
        "",
        f"- 风险：**{feasibility.get('risk')}**",
        f"- 估计可行率：{'—' if fraction is None else f'{float(fraction):.1%}'}",
        f"- 依据：{feasibility.get('basis')}",
        f"- 建议：{feasibility.get('advice')}",
    ]
    if feasibility.get("reference"):
        lines.append(f"- 参考：{feasibility['reference']}")
    return "\n".join(lines)


def _md_deliverable(deliverable: dict) -> str:
    lines = [
        "### 交付口径",
        "",
        f"- 交付指标：**{deliverable.get('metric')}** — {deliverable.get('definition')}",
    ]
    must_include = deliverable.get("must_include") or []
    if must_include:
        lines += ["- 必须包含：", *[f"  - {item}" for item in must_include]]
    plan = deliverable.get("confirmation_plan") or {}
    if plan:
        lines.append(f"- 确认计划：{plan.get('advice')}")
    lines.append(f"- 自查方式：{deliverable.get('how_to_check')}")
    return "\n".join(lines)


def _md_prior(prior: dict) -> str:
    lines = ["### 先验与暖启动", ""]
    if not prior.get("has_prior"):
        lines.append(f"- {prior.get('advice') or '无先验数据；正常从零开始采样'}")
        return "\n".join(lines)
    lines.append(f"- 先验条数：{prior.get('n_prior')}（模式 {prior.get('mode', 'default')}）")
    lines.append(f"- 建议：{prior.get('advice')}")
    if prior.get("how_to_enable"):
        lines.append(f"- 怎么开：{prior['how_to_enable']}")
    if prior.get("evidence"):
        lines.append(f"- 证据：{prior['evidence']}")
    return "\n".join(lines)


def _md_noise(noise: dict) -> str:
    lines = [
        "### 噪声处置",
        "",
        f"- 噪声等级：**{noise.get('level')}**",
        f"- 建议：{noise.get('advice')}",
        f"- 建议复孔：{noise.get('replicates')}；基线臂：{'是' if noise.get('baseline_arm') else '否'}",
    ]
    if noise.get("params"):
        lines += ["- 参数建议：", _json_block(noise["params"])]
    return "\n".join(lines)


def _md_ask(result: dict) -> list[str]:
    pending = result.get("pending") or []
    lines = ["## 下一批取样", ""]
    if not pending:
        lines.append("没有生成建议。")
        return ["\n".join(lines)]
    lines.append("| id | 孔板/孔位 | 算法 | 条件 |")
    lines.append("|---|---|---|---|")
    for item in pending:
        condition = ", ".join(f"{key}={value}" for key, value in (item.get("x") or {}).items())
        lines.append(
            f"| `{item.get('id')}` | {item.get('plate', '-')}/{item.get('well', '-')} "
            f"| {item.get('algorithm')} | {condition} |"
        )
    lines.append("")
    lines.append("测完后用 `feedback` 场景回填（`observations` 或 `csv`）。")
    return ["\n".join(lines)]


def _md_tell(result: dict) -> list[str]:
    lines = [
        "## 结果回填",
        "",
        f"- 本次记录：{result.get('accepted')} 条",
        f"- 剩余预算：{(result.get('run') or {}).get('remaining')}",
    ]
    still = result.get("still_pending") or []
    if still:
        lines.append(f"- ⚠ 仍有 {len(still)} 条 pending 未回填：{', '.join(str(item) for item in still[:5])}")
    else:
        lines.append("- pending 已清空。")
    best = (result.get("run") or {}).get("best")
    lines.append(f"- 当前最优分数：{best}")
    return ["\n".join(lines)]


def _md_delivery(result: dict) -> list[str]:
    delivery = result.get("delivery") or []
    integrity = result.get("integrity") or {}
    lines = ["## 交付自查", "", "| 检查项 | 结果 | 说明 |", "|---|---|---|"]
    for item in delivery:
        lines.append(f"| {item.get('item')} | {'✅' if item.get('ok') else '❌'} | {item.get('detail')} |")
    lines += [
        "",
        f"- 数据完整性：{'OK' if integrity.get('ok') else '有 issue'}",
        f"- 计数：{json_dumps(integrity.get('counts'))}",
    ]
    for issue in integrity.get("issues") or []:
        lines.append(f"- ⚠ {issue}")
    return ["\n".join(lines)]


def _md_board(result: dict) -> list[str]:
    lines = [
        "## 看板",
        "",
        f"- dashboard：`{result.get('dashboard')}`",
        f"- PNG：{result.get('png') or '—'}",
    ]
    warnings = result.get("warnings") or []
    if warnings:
        lines += ["- 警告：", *[f"  - {item}" for item in warnings]]
    lines.append("")
    lines.append("看板为单文件离线产物（无 CDN / 无外网请求），可直接双击打开。")
    return ["\n".join(lines)]


def _md_step(result: dict) -> list[str]:
    step = result.get("step") or {}
    lines = [
        "## 下一步",
        "",
        f"- action：**{step.get('action')}**",
        f"- reason：{step.get('reason')}",
        f"- 命令：`{step.get('next_command')}`",
    ]
    for code in step.get("suggested_actions") or []:
        reason = (step.get("suggested_action_reasons") or {}).get(code)
        lines.append(f"- `{code}`" + (f" — {reason}" if reason else ""))
    return ["\n".join(lines)]


def _md_recommend(result: dict) -> list[str]:
    recommendation = result.get("recommend") or {}
    condition = ", ".join(f"{key}={value}" for key, value in (recommendation.get("condition") or {}).items())
    lines = [
        "## 推荐条件",
        "",
        f"- 条件：{condition}",
        f"- 分数：{recommendation.get('score')}",
        f"- 停止：{recommendation.get('should_stop')}（{recommendation.get('stop_reason')}）",
    ]
    return ["\n".join(lines)]


def _md_prepare(result: dict) -> list[str]:
    chunks = ["## 开工建议\n"]
    for section in result.get("sections") or []:
        chunks.append(str(section.get("markdown") or ""))
    return chunks


def _md_report(result: dict) -> list[str]:
    markdown = str(result.get("markdown") or "")
    return [markdown if markdown.strip() else "（报告为空：还没有观测数据）"]


def md_chunks(action: str, result: dict) -> list[str]:
    """把结构化结果渲染成 Markdown 片段（可含 KaTeX ``$…$``）。"""
    builder = {
        "prepare": _md_prepare,
        "ask": _md_ask,
        "tell": _md_tell,
        "delivery": _md_delivery,
        "render": _md_board,
        "step": _md_step,
        "recommend": _md_recommend,
        "report": _md_report,
    }.get(action)
    if builder is None:
        return [f"```json\n{json_dumps(result)}\n```"]
    return builder(result)


def _notices(action: str, result: dict) -> list[str]:
    notices: list[str] = []
    if action == "delivery":
        failed = [item for item in result.get("delivery") or [] if not item.get("ok")]
        for item in failed:
            notices.append(f"交付项未通过：{item.get('item')}（{item.get('detail')}）")
    if action == "render":
        notices.extend(str(item) for item in (result.get("warnings") or []))
    return notices


# --------------------------------------------------------------------------- #
# 对话式开工（CONTRACT §10）：确定性规则，默认不出网
#
# 文案分成两类，响应里逐条标明来源，绝不把人工文案伪装成算法库元数据：
#   * ``KICKOFF_*_CHIPS`` / ``ALGO_DISPLAY`` / ``PARAM_DISPLAY`` = **人工文案**（工作台维护的中文）；
#   * ``registry.describe_all()`` / ``router.route()`` = **代码取值**（params、why、评分、summary/when 回落）。
# --------------------------------------------------------------------------- #

#: 五步 + options + done（§10.2 的 step 取值）。
KICKOFF_STEPS = ("objective", "budget", "variables", "noise", "constraints", "options", "done")

#: condopt 允许的枚举值（来自 schema，不是猜的；解析结果只允许落在这两个集合里）。
KICKOFF_SMOOTHNESS = ("smooth", "mixed", "rough")
KICKOFF_NOISE = ("low", "medium", "high")

#: 变量声明：名称 + （方括号/圆括号取值列表 | 低-高区间）。一个 token 一个变量。
#: 用「扫描式」而不是「先 split 再逐段匹配」——否则 ``溶剂[MeCN, EtOH]`` 会被逗号切开。
_KICKOFF_VAR_TOKEN = re.compile(
    r"(?P<name>[^0-9\[\]（）(),，、;；\s]+?)\s*"
    r"(?:"
    r"\[(?P<choices_b>[^\]]+)\]"
    r"|（(?P<choices_c>[^）]+)）"
    r"|\((?P<choices_d>[^)]+)\)"
    r"|[:：]?\s*(?P<low>-?\d+(?:\.\d+)?)\s*[-~–—]\s*(?P<high>-?\d+(?:\.\d+)?)"
    r"\s*(?P<unit>[A-Za-z°℃℉%/^]{0,8})"
    r")"
)
#: 两个变量声明之间只允许出现这些分隔（否则视为「没读懂」→ 再问一次）。
_KICKOFF_VAR_GAP = re.compile(r"^[\s,，、;；和与]*$")
#: 约束：「纯度 >= 95」「纯度≥95」「纯度 不低于 95」。
_KICKOFF_CONSTRAINT = re.compile(
    r"(?P<name>[^\d<>≤≥=]+?)\s*(?P<op>>=|<=|≥|≤|>|<|==|=|不低于|不超过|至少|至多)\s*(?P<value>-?\d+(?:\.\d+)?)"
)
_KICKOFF_CONSTRAINT_NONE = ("没有", "无约束", "不需要", "暂无", "否", "none", "no")
_KICKOFF_NEGATIVE = ("越小", "越低", "最小", "最低", "最少", "省", "降低", "min", "reduce", "decrease")
_KICKOFF_POSITIVE = ("越大", "越高", "最大", "最高", "最多", "提升", "提高", "max", "increase")

#: 人工文案：目标方向芯片。value 直接是 prepare 的 objective 片段。
KICKOFF_OBJECTIVE_CHIPS = (
    {"label": "收率越大越好", "value": {"objective": {"name": "yield", "direction": "max"}}},
    {"label": "纯度越大越好", "value": {"objective": {"name": "purity", "direction": "max"}}},
    {"label": "成本越小越好", "value": {"objective": {"name": "cost", "direction": "min"}}},
)

#: 人工文案：预算芯片（数字即 answers.budget；effort 只作展示层标签）。
KICKOFF_BUDGET_CHIPS = (
    {"label": "便宜 · 40 次", "value": {"budget": 40, "effort": "quick"}},
    {"label": "一般 · 160 次", "value": {"budget": 160, "effort": "normal"}},
    {"label": "很贵 · 24 次", "value": {"budget": 24, "effort": "thorough"}},
)

#: 人工文案：条件变量芯片。**低/高范围是工作台给的默认值**（回复里会说明可在参数页改），
#: 不是从用户数据里读出来的，所以 chip 的 label 明确写「用一组常见默认区间」。
KICKOFF_VARIABLE_CHIPS = (
    {
        "label": "温度 + 压力（用 20–150 / 1–10 的默认区间）",
        "value": {
            "variables": [
                {"name": "温度", "kind": "continuous", "low": 20.0, "high": 150.0, "unit": "degC"},
                {"name": "压力", "kind": "continuous", "low": 1.0, "high": 10.0, "unit": "bar"},
            ]
        },
    },
    {
        "label": "只有温度（20–150 degC）",
        "value": {
            "variables": [
                {"name": "温度", "kind": "continuous", "low": 20.0, "high": 150.0, "unit": "degC"}
            ]
        },
    },
    {"label": "我贴一份历史数据（走 06 导入）", "value": {"defer_to_import": True}},
)

#: 人工文案：噪声/光滑度芯片（middle 档的 smoothness 值必须是 condopt 的 "mixed"）。
KICKOFF_NOISE_CHIPS = (
    {"label": "噪声小 · 光滑", "value": {"noise": "low", "smoothness": "smooth"}},
    {"label": "噪声中 · 一般", "value": {"noise": "medium", "smoothness": "mixed"}},
    {"label": "噪声大 · 跳变", "value": {"noise": "high", "smoothness": "rough"}},
)

#: 人工文案：约束芯片。
KICKOFF_CONSTRAINT_CHIPS = (
    {"label": "没有硬约束", "value": {"constraints": 0, "constraint_items": []}},
    {
        "label": "纯度 ≥ 95",
        "value": {
            "constraints": 1,
            "constraint_items": [{"metric": "purity", "op": ">=", "value": 95.0}],
        },
    },
)

#: 每一步的「问什么 + 为什么问」（人工文案）。
KICKOFF_QUESTIONS: dict[str, dict] = {
    "objective": {
        "text": (
            "先确认要优化的**结果**：你想让哪个指标变好？是越大越好还是越小越好？\n\n"
            "**为什么问这个**：目标名和方向决定了后面所有采样的排序方式——方向填反了，"
            "整场实验都会朝着错的方向走。"
        ),
    },
    "budget": {
        "text": (
            "接下来是**预算**：一次实验大概多贵，总共准备做多少次？\n\n"
            "**为什么问这个**：预算决定该用「样本效率高但假设多」的模型类算法，"
            "还是「稳健但吃次数」的采样类算法；次数不够时模型类更划算。"
        ),
    },
    "variables": {
        "text": (
            "**有哪些条件可以调？**可以说名字，也可以直接给范围，例如 `温度 20-150、溶剂[MeCN, EtOH]`。\n\n"
            "**为什么问这个**：条件空间是优化的搜索范围。只报名字而不给范围我没法确定边界，"
            "所以那样我会再问一次——不会替你猜。"
        ),
    },
    "noise": {
        "text": (
            "测量**噪声**大吗？响应面**光滑**吗？\n\n"
            "**为什么问这个**：噪声大就要抑制过拟合、多半还得做复孔；响应面有跳变时"
            "高斯过程这类「假设光滑」的算法会退化，得换更稳健的。"
        ),
    },
    "constraints": {
        "text": (
            "最后：有没有**必须满足的硬约束**（例如纯度 ≥ 95）？\n\n"
            "**为什么问这个**：硬约束会改变「什么算好结果」——没有约束的算法可能一直给你"
            "不可行的最优解。"
        ),
    },
}

#: 人工文案：算法卡片（title/one_line/tradeoff/risk）+ 来源标记。
#: 未列出的算法回落到 `registry.describe_all()` 的 summary/when（`text_source="registry"`）。
ALGO_DISPLAY: dict[str, dict] = {
    "bayes": {
        "title": "贝叶斯优化（bayes）",
        "one_line": "样本效率最高，预算有限时最可能逼近最优。",
        "tradeoff": "代理模型假设响应面光滑；有跳变或强噪声时会退化。",
        "risk": "low",
    },
    "tpe": {
        "title": "TPE（tpe）",
        "one_line": "对分类/混合空间友好，小样本也能用。",
        "tradeoff": "不如高斯过程擅长精细的连续极值。",
        "risk": "low",
    },
    "constrained": {
        "title": "约束贝叶斯（constrained）",
        "one_line": "先找可行点，再在可行域里做带约束的改进。",
        "tradeoff": "可行域极小时前几轮几乎都在找可行点，探索效率下降。",
        "risk": "medium",
    },
    "sobol": {
        "title": "Sobol 序列（sobol）",
        "one_line": "空间填充均匀，适合做初始设计或中等维度。",
        "tradeoff": "不利用已有观测，预算大时样本效率一般。",
        "risk": "low",
    },
    "lhs": {
        "title": "拉丁超立方（lhs）",
        "one_line": "单批内每维分层均匀，适合一次性批量实验。",
        "tradeoff": "同样是空间填充，不做模型引导。",
        "risk": "low",
    },
    "random": {
        "title": "随机搜索（random）",
        "one_line": "无模型、极稳健，高维或极小预算的兜底。",
        "tradeoff": "样本效率最低，别指望它精细收敛。",
        "risk": "low",
    },
    "cmaes": {
        "title": "CMA-ES（cmaes）",
        "one_line": "连续、中等维度上的进化策略，抗噪。",
        "tradeoff": "需要较多评估次数，且对分类变量不友好。",
        "risk": "medium",
    },
    "grid": {
        "title": "网格搜索（grid）",
        "one_line": "穷举等距网格，最直观、最容易解释。",
        "tradeoff": "维度一高就爆炸，只适合低维小空间。",
        "risk": "high",
    },
    "parego": {
        "title": "ParEGO（parego）",
        "one_line": "多目标标量化，单次只跑一个标量目标。",
        "tradeoff": "需要多目标问题才划算；权重标量化会丢一部分前沿覆盖。",
        "risk": "medium",
    },
    "qehvi": {
        "title": "qEHVI（qehvi）",
        "one_line": "批量多目标期望超体积改进，专为 2 目标设计。",
        "tradeoff": "目标数 > 2 时不可用；计算比标量化重。",
        "risk": "medium",
    },
    "qehvi_nd": {
        "title": "qEHVI-ND（qehvi_nd）",
        "one_line": "多目标（>2）的批量超体积改进。",
        "tradeoff": "计算最重，预算很少时前几轮都在建模型。",
        "risk": "medium",
    },
    "qnehvi": {
        "title": "qNEHVI（qnehvi）",
        "one_line": "噪声下的批量多目标改进，抗噪更强。",
        "tradeoff": "比 qEHVI 更慢，且需要噪声估计。",
        "risk": "medium",
    },
    "cqehvi": {
        "title": "约束 qEHVI（cqehvi）",
        "one_line": "2 目标 + 硬约束的批量前沿搜索。",
        "tradeoff": "只支持 2 目标；可行域太小时起步慢。",
        "risk": "medium",
    },
    "cqnehvi": {
        "title": "约束 qNEHVI（cqnehvi）",
        "one_line": "噪声 + 约束下的多目标批量搜索。",
        "tradeoff": "计算最重的一档，适合预算充足的多目标问题。",
        "risk": "medium",
    },
    "mfbo": {
        "title": "多保真贝叶斯（mfbo）",
        "one_line": "低保真先筛、高保真确认，省真实实验。",
        "tradeoff": "需要你提供保真度与成本；低保真偏差大时会带偏。",
        "risk": "medium",
    },
    "mfparego": {
        "title": "多保真 ParEGO（mfparego）",
        "one_line": "多目标 + 多保真，先低保真筛前沿。",
        "tradeoff": "需要保真度/成本定义，设置成本高于单保真。",
        "risk": "medium",
    },
    "momfbo": {
        "title": "多目标多保真（momfbo）",
        "one_line": "多目标 + 多保真 + 噪声的综合档。",
        "tradeoff": "参数最多、最依赖保真度定义，配置不对收益会反噬。",
        "risk": "high",
    },
    "mqehvi_nd": {
        "title": "多目标多保真 EHVI（mqehvi_nd）",
        "one_line": "多目标超体积改进 + 多保真采样。",
        "tradeoff": "计算重，且需要成对的保真度数据。",
        "risk": "high",
    },
}

#: 人工文案：参数展示层（label/plain/impact/advanced）。
#:
#: **索引规则**：先查 ``(algorithm, name)`` 精确条目，再回落 ``name`` 通用条目
#: （见 :func:`_param_copy`）。只有在**跨算法签名不同**时才需要精确条目——例如
#: ``acquisition`` 在 10 个算法里有 3 种 choices（``mfbo`` 只有 ei/logei/ucb），
#: 通用文案里列举 pi/ts 就会对部分算法说错（task-17 / 验收 F9）。
#: 未列出的参数回落 registry ``description``，并标 ``display_source="registry"``。
PARAM_DISPLAY: dict[tuple[str, str] | str, dict] = {
    # —— acquisition：choices 随算法不同，逐算法给文案 ——
    **{
        (algorithm, "acquisition"): {
            "label": "采集函数",
            "plain": "ei/logei 平衡探索与利用；ucb 更偏探索；pi 更偏利用；ts 用后验采样。",
            "impact": "high",
            "advanced": False,
        }
        for algorithm in ("bayes", "constrained", "cqehvi", "cqnehvi", "qehvi", "qnehvi")
    },
    "acquisition": {
        "label": "采集函数",
        "plain": "ei/logei 平衡探索与利用；ucb 更偏探索。其余取值以控件列出的为准。",
        "impact": "high",
        "advanced": False,
    },
    "kernel": {"label": "核函数", "plain": "越光滑的响应面用 rbf，一般用 matern52，粗糙/不连续用 matern32。", "impact": "high", "advanced": False},
    "length_scale": {"label": "特征长度尺度", "plain": "初始长度尺度；越大越平滑、越小越允许局部起伏。一般不用动。", "impact": "medium", "advanced": True},
    "ard": {"label": "逐维长度尺度（ARD）", "plain": "让每个条件变量有自己的尺度。混合空间建议开启。", "impact": "medium", "advanced": False},
    "alpha": {"label": "观测噪声方差", "plain": "测量越不稳就越该调大，复孔越多可调小（工作台建议区间 0.01–0.1）；各算法的默认不同，以控件显示的默认值为准。", "impact": "high", "advanced": False},
    "n_initial": {"label": "初始设计点数", "plain": "先用多少个空间填充点起步；不填（0）则由后端按维度自动决定。", "impact": "medium", "advanced": False},
    "n_candidates": {"label": "候选点数", "plain": "每轮评估多少个候选点；越大越准也越慢。", "impact": "low", "advanced": True},
    "kappa": {"label": "UCB 探索系数", "plain": "只在 acquisition=ucb 时生效；越大越敢往未知区域走。", "impact": "medium", "advanced": True},
    "xi": {"label": "改进阈值 ξ", "plain": "只在 ei/pi 时生效；调大更偏探索。", "impact": "low", "advanced": True},
    "batch_strategy": {"label": "批量策略", "plain": "一次建议多点时怎么避免点到重复位置；可选值与默认值随算法不同，以控件列出的为准。", "impact": "medium", "advanced": True},
    "local_frac": {"label": "批量分散半径", "plain": "批量点的排斥强度，越大点铺得越开。", "impact": "low", "advanced": True},
    "local_sigma": {"label": "批量分散宽度", "plain": "配合批量分散半径使用，一般默认即可。", "impact": "low", "advanced": True},
    "mc_draws": {"label": "蒙特卡洛样本数", "plain": "约束/多目标采集的 MC 积分精度；越大越准越慢。", "impact": "low", "advanced": True},
    "constraint_margin": {"label": "约束余量", "plain": "把约束边界往里收一点，给噪声留余量。", "impact": "medium", "advanced": True},
    "feasibility_threshold": {"label": "可行概率阈值", "plain": "可行概率低于它就优先找可行点。", "impact": "medium", "advanced": True},
    "rho": {"label": "ParEGO 标量化权重", "plain": "多目标随机加权的采样强度，一般默认即可。", "impact": "low", "advanced": True},
    "weight_alpha": {"label": "权重分布参数", "plain": "控制随机权重的分布：数值越大权重越集中，默认即均匀分布。", "impact": "low", "advanced": True},
    "sigma0": {"label": "初始步长 σ0", "plain": "初始搜索步长：条件空间越大就该越大，否则容易只在起点附近打转。", "impact": "high", "advanced": False},
    "population_size": {"label": "种群大小", "plain": "每代评估点数；批量实验时按批大小设。", "impact": "medium", "advanced": False},
    "gamma": {"label": "TPE 分裂分位", "plain": "把已有观测分成「好/差」两组的分位点。", "impact": "medium", "advanced": True},
    "exploration_frac": {"label": "TPE 探索比例", "plain": "多少比例的建议来自随机探索。", "impact": "medium", "advanced": True},
    "dedupe": {"label": "避开重复点", "plain": "是否避开已经评价过的条件；断点续跑时建议开启。", "impact": "low", "advanced": False},
    "scramble": {"label": "Sobol 随机化", "plain": "随机化 Sobol 可复现又有随机性质，一般保持开启。", "impact": "low", "advanced": False},
    "n_base": {"label": "基础样本数", "plain": "qNEHVI 的基底采样数；越大越稳越慢。", "impact": "low", "advanced": True},
    "n_y": {"label": "后验样本数", "plain": "噪声下的后验抽样数量。", "impact": "low", "advanced": True},
    "noise_std": {"label": "噪声标准差", "plain": "已知噪声水平时填它，比让模型自己估更稳。", "impact": "medium", "advanced": True},
    "fidelity_levels": {"label": "保真度档位", "plain": "多保真实验各档的保真度数值；最高的一档对应真实实验，其余按相对保真度填。", "impact": "high", "advanced": False},
    "fidelity_costs": {"label": "各档成本", "plain": "每档保真度的相对成本，决定值不值得先跑低保真。", "impact": "high", "advanced": False},
    "cost_exponent": {"label": "成本指数", "plain": "成本随保真度上升的陡峭程度。", "impact": "low", "advanced": True},
    "min_full_fraction": {"label": "全保真最低占比", "plain": "保证交付时有足够的真实实验证据。", "impact": "medium", "advanced": True},
    "optimization": {"label": "LHS 优化轮数", "plain": "是否对拉丁超立方做最大化最小距离的优化。", "impact": "low", "advanced": False},
}

#: 人工文案：哪一步答完之后该问哪一步（§10.1 的五轮顺序）。
_KICKOFF_NEXT_STEP = {
    "objective": "budget",
    "budget": "variables",
    "variables": "noise",
    "noise": "constraints",
    "constraints": "options",
}

#: 缺字段时的人话说明（人工文案）。
_KICKOFF_MISSING_TEXT = {
    "objective": "目标（要优化的指标名 + 越大越好/越小越好）",
    "budget": "总实验次数（budget）",
    "variables": "至少一个可调条件及其范围",
}


def kickoff_empty_state() -> dict:
    """空状态：step=objective，answers 用 condopt 的枚举默认值（不是编造的实验结果）。"""
    return {
        "step": "objective",
        "answers": {
            "objective": {"name": "", "direction": "max"},
            "variables": [],
            "budget": 0,
            "batch": KICKOFF_DEFAULT_BATCH,
            "noise": "medium",
            "smoothness": "mixed",
            "domain": "generic",
            "prior": 0,
            "notes": "由对话式开工生成（CONTRACT §10）",
        },
        "missing": ["objective"],
    }


#: 对话没问 batch（§10.1 只问五件事），给一个明确默认值并写进 plan 的 warnings。
KICKOFF_DEFAULT_BATCH = 4


def _kickoff_missing(answers: Mapping) -> list[str]:
    """哪些字段还不足以路由（顺序即提示顺序）。**不足就停住，不凑选项。**"""
    missing: list[str] = []
    objective = answers.get("objective") or {}
    if not isinstance(objective, Mapping) or not str(objective.get("name") or "").strip():
        missing.append("objective")
    if objective.get("direction") not in ("max", "min"):
        missing.append("objective")
    variables = answers.get("variables") or []
    if not isinstance(variables, list) or not variables:
        missing.append("variables")
    budget = answers.get("budget")
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        missing.append("budget")
    # 去重且保持顺序
    seen: list[str] = []
    for item in missing:
        if item not in seen:
            seen.append(item)
    return seen


def _kickoff_chip_for_value(step: str, value: Mapping) -> dict | None:
    """把解析出来的答案并进 answers（chip 已给出完整字段）。返回合并后的 answers 或 None。"""
    chips = {
        "objective": KICKOFF_OBJECTIVE_CHIPS,
        "budget": KICKOFF_BUDGET_CHIPS,
        "variables": KICKOFF_VARIABLE_CHIPS,
        "noise": KICKOFF_NOISE_CHIPS,
        "constraints": KICKOFF_CONSTRAINT_CHIPS,
    }.get(step, ())
    allowed = {"objective", "budget", "effort", "variables", "noise", "smoothness", "constraints",
               "constraint_items", "defer_to_import"}
    unknown = sorted(str(key) for key in value if str(key) not in allowed)
    if unknown:
        raise WorkbenchError(
            E_VALIDATION,
            f"{step} 的答案未知字段: {_display_list(unknown)}",
            f"允许字段: {sorted(allowed)}",
        )
    return None if not chips else dict(value)


def _match_chip(step: str, text: str) -> dict | None:
    """用**我们自己发出去的 chip 标签**做精确匹配（前端把 label 当文本回传也能work）。"""
    if not text:
        return None
    normalized = re.sub(r"\s+", "", text)
    chips = {
        "objective": KICKOFF_OBJECTIVE_CHIPS,
        "budget": KICKOFF_BUDGET_CHIPS,
        "variables": KICKOFF_VARIABLE_CHIPS,
        "noise": KICKOFF_NOISE_CHIPS,
        "constraints": KICKOFF_CONSTRAINT_CHIPS,
    }.get(step, ())
    for chip in chips:
        if re.sub(r"\s+", "", str(chip["label"])) == normalized:
            return dict(chip["value"])
    return None


def _parse_objective_text(text: str) -> dict | None:
    """规则解析目标；**方向不明就返回 None（再问一次），绝不猜方向**。

    名称取「方向词之前的那截」——比「把方向词删掉」稳，因为中文里
    ``收率越大越好`` 删 ``越大`` 会留下 ``越好``（切成 ``收率越``）。
    """
    cleaned = re.sub(r"\s+", "", str(text or ""))
    if not cleaned:
        return None
    direction = None
    cut = len(cleaned)
    for keywords, value in ((_KICKOFF_NEGATIVE, "min"), (_KICKOFF_POSITIVE, "max")):
        for keyword in keywords:
            index = cleaned.find(keyword)
            if index != -1 and index < cut:
                cut, direction = index, value
    if direction is None:
        return None
    name = cleaned[:cut].strip()
    # 去掉「让/把/使/要/想」这类动词与标点（只剥两端，不动词中间的字）
    name = name.strip("：:，,。.、的好了要想要把让使请帮我看下")
    if not name:
        return None
    return {"objective": {"name": name, "direction": direction}}


def _parse_budget_text(text: str) -> dict | None:
    """规则解析预算：取第一个整数；没有数字就返回 None。"""
    match = re.search(r"(?<!\d)(\d{1,7})(?!\d)", str(text or ""))
    if match is None:
        return None
    value = int(match.group(1))
    if value < 1:
        return None
    return {"budget": value}


def _parse_variables_text(text: str) -> dict | None:
    """规则解析条件变量。

    **只认明确写出来的边界**：``温度 20-150``（连续）或 ``溶剂[MeCN, EtOH]``（分类），
    多个用顿号/逗号/空格分隔。只报名字（没有范围/取值）→ 返回 None 再问一次，
    绝不替用户猜 low/high（§10.2「解析不了就再问，不许瞎猜」）。
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    variables: list[dict] = []
    position = 0
    for match in _KICKOFF_VAR_TOKEN.finditer(raw):
        gap = raw[position : match.start()]
        if not _KICKOFF_VAR_GAP.match(gap):
            return None  # 中间夹着看不懂的内容 → 不猜
        position = match.end()
        name = match.group("name").strip()
        choices_body = match.group("choices_b") or match.group("choices_c") or match.group("choices_d")
        if choices_body is not None:
            body = [item.strip() for item in re.split(r"[/|、,，;；\s]+", choices_body) if item.strip()]
            if len(body) < 2:
                return None
            variables.append({"name": name, "kind": "categorical", "choices": body})
            continue
        low = float(match.group("low"))
        high = float(match.group("high"))
        if low >= high:
            return None
        entry: dict[str, Any] = {"name": name, "kind": "continuous", "low": low, "high": high}
        unit = (match.group("unit") or "").strip()
        if unit:
            entry["unit"] = unit
        variables.append(entry)
    if not variables:
        return None
    if not _KICKOFF_VAR_GAP.match(raw[position:]):
        return None
    return {"variables": variables}


def _parse_noise_text(text: str) -> dict | None:
    cleaned = re.sub(r"\s+", "", str(text or ""))
    if not cleaned:
        return None
    noise = None
    for keyword in ("噪声大", "噪声高", "跳变", "粗糙", "不稳", "波动大"):
        if keyword in cleaned:
            noise = "high"
            break
    if noise is None:
        for keyword in ("噪声小", "噪声低", "很稳", "平稳", "光滑", "平滑"):
            if keyword in cleaned:
                noise = "low"
                break
    if noise is None and ("中" in cleaned or "一般" in cleaned or "medium" in cleaned):
        noise = "medium"
    if noise is None:
        return None
    smoothness = {"low": "smooth", "high": "rough", "medium": "mixed"}[noise]
    if noise not in KICKOFF_NOISE or smoothness not in KICKOFF_SMOOTHNESS:
        return None  # 与 condopt 的枚举对齐（防手滑写出 schema 不认的值）
    return {"noise": noise, "smoothness": smoothness}


def _normalize_op(op: str) -> str:
    return {"≥": ">=", "≤": "<=", "=": "==", "不低于": ">=", "至少": ">=",
            "不超过": "<=", "至多": "<="}.get(op, op)


def _parse_constraints_text(text: str) -> dict | None:
    """规则解析硬约束；提到约束但读不出「指标 + 比较符 + 数值」→ None（再问一次）。"""
    raw = str(text or "").strip()
    cleaned = re.sub(r"\s+", "", raw)
    if not cleaned:
        return None
    if any(keyword in cleaned.lower() for keyword in _KICKOFF_CONSTRAINT_NONE):
        return {"constraints": 0, "constraint_items": []}
    items: list[dict] = []
    for match in _KICKOFF_CONSTRAINT.finditer(cleaned):
        metric = match.group("name").strip("，,。.、的：:和与")
        if not metric:
            continue
        items.append(
            {
                "metric": metric,
                "op": _normalize_op(match.group("op")),
                "value": float(match.group("value")),
            }
        )
    if not items:
        return None
    return {"constraints": len(items), "constraint_items": items}


def _kickoff_merge(answers: Mapping, parsed: Mapping) -> dict:
    """把一步的解析结果并进 answers（只覆盖它管的那几个字段）。"""
    merged = json.loads(json.dumps(_to_jsonable(answers), ensure_ascii=False))
    for key, value in parsed.items():
        if key == "constraint_items":
            merged["constraint_items"] = value
            continue
        if key == "defer_to_import":
            merged["defer_to_import"] = bool(value)
            continue
        if key == "effort":
            merged["effort"] = value  # 展示层标签，init 会忽略未知键（不写进 answers 就安全）
            merged.pop("effort", None)
            continue
        merged[key] = value
    return merged


def _kickoff_quick_replies(step: str) -> list[dict]:
    chips = {
        "objective": KICKOFF_OBJECTIVE_CHIPS,
        "budget": KICKOFF_BUDGET_CHIPS,
        "variables": KICKOFF_VARIABLE_CHIPS,
        "noise": KICKOFF_NOISE_CHIPS,
        "constraints": KICKOFF_CONSTRAINT_CHIPS,
    }.get(step, ())
    return [{"label": chip["label"], "value": _to_jsonable(chip["value"])} for chip in chips]


def _kickoff_question_reply(step: str, extra: str = "") -> dict:
    question = KICKOFF_QUESTIONS[step]
    text = question["text"] if not extra else f"{extra}\n\n{question['text']}"
    return {"text": text, "quick_replies": _kickoff_quick_replies(step)}


def _algorithm_display(algorithm: str) -> dict:
    """算法卡片的文案：人工表优先，缺则回落 registry（并把来源标出来）。"""
    described = {item["name"]: item for item in condopt_registry.describe_all()}.get(algorithm, {})
    hand = ALGO_DISPLAY.get(algorithm)
    if hand:
        return {
            "title": hand["title"],
            "one_line": hand["one_line"],
            "tradeoff": hand["tradeoff"],
            "risk": hand["risk"],
            "text_source": "workbench",
        }
    summary = str(described.get("summary") or "").split("。")[0]
    when = str(described.get("when") or "")
    return {
        "title": f"{algorithm}",
        "one_line": summary or f"{algorithm}（registry 未提供摘要）",
        "tradeoff": when or "registry 未提供适用场景说明",
        "risk": "medium",
        "text_source": "registry",
    }


def _param_copy(algorithm: str, name: str) -> dict | None:
    """取参数的人工文案：**先精确 ``(algorithm, name)``、再通用 ``name``**。

    精确条目是给「跨算法签名不同」的参数（如 ``acquisition`` 的 choices 有 3 种）用的，
    避免通用文案列举了某个算法不存在的选项（task-17 / F9）。
    ``test_param_copy_never_lies`` 会把这条索引规则当成契约来验。
    """
    exact = PARAM_DISPLAY.get((algorithm, name))
    if exact is not None:
        return exact
    return PARAM_DISPLAY.get(name)


def _param_display(algorithm: str, param: Mapping) -> dict:
    """参数展示层：人工文案优先（``display_source="workbench"``），缺则回落 description。"""
    name = str(param.get("name"))
    hand = _param_copy(algorithm, name)
    entry = dict(param)
    if hand:
        entry.update(
            {
                "label": hand["label"],
                "plain": hand["plain"],
                "impact": hand["impact"],
                "advanced": bool(hand["advanced"]),
                "display_source": "workbench",
            }
        )
    else:
        entry.update(
            {
                "label": name,
                "plain": str(param.get("description") or "（registry 未提供说明）"),
                "impact": "medium",
                "advanced": True,
                "display_source": "registry",
            }
        )
    return entry


def _kickoff_options(state: Mapping, algorithm: str) -> dict:
    """占位：真正的选项生成在 :meth:`Bridge._kickoff_options`（需要 parse_answers + route）。"""
    raise NotImplementedError


__all__ = [
    "Bridge",
    "WorkbenchError",
    "SCENARIOS",
    "SCENARIOS_BY_KEY",
    "EFFORTS",
    "ACTION_PAYLOAD_FIELDS",
    "WRITE_ACTIONS",
    "NEEDS_RUN",
    "MAX_RUN_ID_LENGTH",
    "E_BAD_REQUEST",
    "E_NOT_FOUND",
    "E_CONFLICT",
    "E_VALIDATION",
    "E_INTERNAL",
    "ERROR_STATUS",
    "condopt_version",
    "json_dumps",
    "md_chunks",
    "_display",
    "_display_list",
]
