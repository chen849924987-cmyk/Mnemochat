"""路径解析：源码运行以仓库根为基准；打包成 exe 后以 exe 所在目录为用户数据根。

- base_dir()：用户数据根——config.yaml / personas / data 都放这里（打包后可写、可持久）；
- resource_dir()：只读资源根——打包后是解包目录 _internal（schema.sql、page.html、
  打包时附带进去的 personas 模板等）；源码运行时两者相同。
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def base_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", "")
    if is_frozen() and meipass:
        return Path(meipass)
    return base_dir()
