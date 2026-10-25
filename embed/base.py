"""嵌入提供者抽象：api / local / mock 三种实现，配置切换。

向量一律归一化后返回，相似度即点积。
name 是嵌入身份（进 embeddings 表主键）：切换 provider/model 即换命名空间，
旧向量保留，由 --reembed 增量补齐。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

from config import BotConfig


@runtime_checkable
class EmbeddingProvider(Protocol):
    name: str
    dim: int

    async def embed(self, texts: list[str]) -> np.ndarray:
        """返回 (n, dim) 的 float32 归一化矩阵。"""
        ...


def create_embedder(cfg: BotConfig) -> EmbeddingProvider:
    from .api import APIEmbedder
    from .local import LocalEmbedder

    if cfg.embedding.provider == "api":
        return APIEmbedder(cfg.embedding.api)
    if cfg.embedding.provider == "local":
        return LocalEmbedder(cfg.embedding.local)
    raise ValueError(f"未知 embedding provider: {cfg.embedding.provider}")
