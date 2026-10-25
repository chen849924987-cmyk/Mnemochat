"""测试夹具：全部离线（HashEmbedder + MockChatClient），不触碰任何 API。"""

from __future__ import annotations

from pathlib import Path

import pytest

from chat.mock import MockChatClient
from config import BotConfig
from embed.mock import HashEmbedder
from memory.decay import initial_strength
from memory.models import MemoryRecord, MessageRecord
from memory.store import MemoryStore
from persona.card import PersonaCard
from util.clock import Clock

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg() -> BotConfig:
    c = BotConfig()
    c.database = "data/test.db"  # 会被 store fixture 覆盖为 tmp_path
    c.ingest.window_size = 2
    c.ingest.min_interval_seconds = 0
    c.recall.top_k = 5
    return c


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(tmp_path / "test.db")
    await s.init()
    yield s
    await s.close()


@pytest.fixture
def embedder() -> HashEmbedder:
    return HashEmbedder(64)


@pytest.fixture
def chat() -> MockChatClient:
    return MockChatClient()


@pytest.fixture
def persona() -> PersonaCard:
    return PersonaCard.load_yaml(ROOT / "personas" / "default.yaml")


@pytest.fixture
def make_msg():
    def _make(group_id: str, name: str, content: str, ts: float = 1_000_000.0) -> MessageRecord:
        return MessageRecord(group_id=group_id, user_id=name, user_name=name,
                             content=content, ts=ts)
    return _make


@pytest.fixture
def seed_memory(store, embedder, clock):
    """直接向库中植入一条记忆（带嵌入），绕过 ingest 以精确控制状态。"""

    async def _seed(content: str, **kw) -> MemoryRecord:
        params: dict = dict(
            stage="ltm", kind="fact", group_id="g1", subject_user_id="小明",
            actor_user_id="小明", importance=6.0, emotion=0.0, visibility="group",
            keywords=[], entities=[], created_at=clock.now(),
        )
        params.update(kw)
        mem = MemoryRecord(
            content=content,
            strength=initial_strength(params["importance"]),
            **params,
        )
        mem.id = await store.add_memory(mem)
        vec = (await embedder.embed([content]))[0]
        await store.save_embedding(mem.id, embedder.name, vec)
        return mem

    return _seed
