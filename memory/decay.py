"""艾宾浩斯衰减与显著性（salience）数学。

核心约定：R（保留率）只影响检索排序权重，不影响记忆的存在性。
记忆只会"沉底"，永不归零、永不物理删除——这是对"定期删库"的彻底否定。

- 保留率   R = exp(-Δt天 / S)，S 为该记忆的强度时间常数
- 初始强度 S0 = base + importance（重要的事忘得慢）
- 检索强化 S ← min(S × factor, cap)：每被想起一次就更牢固
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class SalienceWeights:
    similarity: float = 0.35
    bm25: float = 0.25
    retention: float = 0.20
    access: float = 0.10
    importance_emotion: float = 0.10


def initial_strength(importance: float, base_days: float = 2.0) -> float:
    return base_days + max(0.0, importance)


def retention(delta_days: float, strength: float) -> float:
    s = max(strength, 1e-6)
    return math.exp(-max(delta_days, 0.0) / s)


def strengthened(strength: float, factor: float = 1.25, cap: float = 365.0) -> float:
    return min(strength * factor, cap)


def normalize(values: list[float]) -> list[float]:
    """min-max 归一到 [0,1]；全体相等时返回全 0.5，避免排序噪声。"""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def salience(
    sim: float,
    bm25_norm: float,
    retention_value: float,
    access_count: int,
    importance: float,
    emotion: float,
    weights: SalienceWeights,
) -> float:
    """检索综合分。sim/bm25_norm/retention_value 均应在 [0,1]。"""
    access_term = min(math.log1p(access_count) / math.log1p(20.0), 1.0)
    ie_term = min((max(importance, 0.0) + max(emotion, 0.0)) / 20.0, 1.0)
    return (
        weights.similarity * max(min(sim, 1.0), 0.0)
        + weights.bm25 * max(min(bm25_norm, 1.0), 0.0)
        + weights.retention * max(min(retention_value, 1.0), 0.0)
        + weights.access * access_term
        + weights.importance_emotion * ie_term
    )
