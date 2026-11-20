"""pytest 入口：让 ``import condopt``（外部依赖）与 ``import bridge``（本仓库）都成立。

condopt 不在本仓库里——定位逻辑统一在 bridge.py 的 ``_ensure_condopt_importable()``：
已安装的包 → ``CONDOPT_SRC`` → 同级检出。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bridge  # noqa: E402,F401  （导入即完成 condopt 定位，失败会给出可操作的报错）
