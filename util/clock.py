"""系统时钟抽象。

所有时间戳一律通过 Clock 获取（unix 秒，float）。模拟器通过 warp() 快进时间，
用于验证遗忘曲线与巩固触发；生产环境就是一个不走样的真实时钟。
"""

from __future__ import annotations

import time


class Clock:
    def __init__(self) -> None:
        self._offset: float = 0.0

    def now(self) -> float:
        return time.time() + self._offset

    def warp(self, seconds: float) -> float:
        """时间快进 seconds 秒，返回快进后的当前时刻。"""
        self._offset += seconds
        return self.now()

    @property
    def offset(self) -> float:
        return self._offset
