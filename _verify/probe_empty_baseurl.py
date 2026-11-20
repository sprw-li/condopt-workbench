"""F8 探针：CONDOPT_IMPORT_LLM_BASE_URL 显式置空时的实际生效地址。

运行：py -3 _verify/probe_empty_baseurl.py
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPO, Server, fresh, get, js, log  # noqa: E402

sys.path.insert(0, str(REPO / "workbench"))
import bridge as bridge_module  # noqa: E402


def main() -> None:
    log(f"DEFAULT_LLM_BASE_URL = {getattr(bridge_module, 'DEFAULT_LLM_BASE_URL', '?')}")
    for label, value in (("未设置（键不存在）", None), ("显式空串 ''", ""), ("显式空白串 '   '", "   ")):
        env = {"BOHRIUM_API_KEY": "sk-ASCII"}
        # 只有在「测试未设置」时才把键删掉；否则要**真的**把空串/空白传进去
        # （第一版我无条件 drop，把要测的值自己删掉了，于是三行输出一样 —— 那是探针的 bug）
        drop = ["CONDOPT_IMPORT_LLM_BASE_URL"] if value is None else []
        if value is not None:
            env["CONDOPT_IMPORT_LLM_BASE_URL"] = value
        with Server(fresh(f"baseurl_{abs(hash(label)) % 1000}"), extra=["--import-llm"], env=env, drop_env=drop) as server:
            status, _, text = get(server.base, "/api/bootstrap")
            llm = js(text).get("import_llm") or {}
            log(f"{label:22} → bootstrap.import_llm.url = {llm.get('url')!r}  "
                f"url_valid={llm.get('url_valid')}  enabled={llm.get('enabled')}")


if __name__ == "__main__":
    main()
