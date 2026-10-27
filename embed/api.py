"""OpenAI 兼容嵌入 API 通道（硅基流动 / 智谱 / OpenAI 等均可）。"""

from __future__ import annotations

import numpy as np
from openai import AsyncOpenAI

from config import EmbedAPIConfig


class APIEmbedder:
    def __init__(self, cfg: EmbedAPIConfig) -> None:
        self.cfg = cfg
        self.name = f"api:{cfg.model}"
        self.dim = cfg.dim
        self._client = AsyncOpenAI(base_url=cfg.base_url,
                                   api_key=cfg.api_key or "EMPTY")

    async def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        out: list[list[float]] = []
        bs = max(1, self.cfg.batch_size)
        for i in range(0, len(texts), bs):
            batch = texts[i : i + bs]
            resp = await self._client.embeddings.create(model=self.cfg.model, input=batch)
            out.extend(d.embedding for d in resp.data)
        mat = np.asarray(out, dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms < 1e-9] = 1.0
        return (mat / norms).astype(np.float32)
