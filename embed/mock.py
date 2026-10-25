"""确定性假嵌入：字符 bigram 哈希袋。

用途：离线演示（--mock）与测试。词汇级重叠 → 相似度较高，
足以验证入库/去重/聚类/召回的机制，但不具备真实语义泛化能力。
"""

from __future__ import annotations

import hashlib

import numpy as np


class HashEmbedder:
    def __init__(self, dim: int = 256) -> None:
        self.name = "mock:hash"
        self.dim = dim

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for i in range(len(text) - 1):
            gram = text[i : i + 2]
            h = int.from_bytes(hashlib.md5(gram.encode("utf-8")).digest()[:4], "little")
            v[h % self.dim] += 1.0
        n = float(np.linalg.norm(v))
        return v / n if n > 1e-9 else v

    async def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.stack([self._vec(t) for t in texts])
