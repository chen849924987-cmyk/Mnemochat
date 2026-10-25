"""本地嵌入通道：sentence-transformers（需 pip install -r requirements-local-embed.txt）。

模型懒加载；同步 encode 放线程池，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio

import numpy as np

from config import EmbedLocalConfig


class LocalEmbedder:
    def __init__(self, cfg: EmbedLocalConfig) -> None:
        self.cfg = cfg
        self.name = f"local:{cfg.model_name}"
        self.dim = cfg.dim
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:  # pragma: no cover
                raise RuntimeError(
                    "本地嵌入需要先安装: pip install -r requirements-local-embed.txt"
                ) from e
            self._model = SentenceTransformer(self.cfg.model_name, device=self.cfg.device)
        return self._model

    async def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        model = await asyncio.to_thread(self._ensure_model)
        mat = await asyncio.to_thread(
            lambda: model.encode(texts, normalize_embeddings=True)
        )
        mat = np.asarray(mat, dtype=np.float32)
        if mat.shape[1] != self.dim:
            self.dim = int(mat.shape[1])  # 实际维度以模型为准
        return mat
