"""写入路径：对话窗口 → LLM 提取 → 噪声门控 → 复现去重 → staging（附嵌入）。

惊奇门控的工程化简化：
- 提取时就打多维分（重要性/情感/实体），低信息量噪声直接丢弃；
- 语义去重：与既有记忆相似度 ≥ 阈值 → 不新建，复现强化旧条目（recurrence_count+1）。
嵌入在入库时生成（而非晋升时），因为去重与聚类都依赖向量。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from config import BotConfig
from memory.decay import initial_strength
from memory.models import ExtractedMemory, MemoryRecord, MessageRecord
from util.text import fmt_ts

if TYPE_CHECKING:
    from chat.llm import ChatBackend
    from embed.base import EmbeddingProvider
    from memory.store import MemoryStore
    from util.clock import Clock

log = logging.getLogger(__name__)

EXTRACT_SYSTEM = """你是群聊长期记忆提取器。从群聊片段中提取"值得机器人长期记住"的信息。

优先提取：
- 群成员的稳定偏好（喜欢/讨厌什么）
- 事实（过敏、职业、日程、身体状况）
- 重要事件与约定（备考、搬家、约饭、答应过的事）
- 情绪强烈的时刻
- 与机器人自身的互动（起的外号、约定、态度变化）

不要提取：寒暄、灌水、表情包闲聊、与群成员无关的时事。

要求：
- content 必须第三人称、自包含、含主语（如"小明对花生过敏"），脱离上下文也能理解
- subject 填该记忆关于谁的昵称（机器人自己填 "self"），与具体人无关则留空字符串
- kind 取值：episodic(情节)/semantic(语义)/preference(偏好)/fact(事实)/event(事件)/bond(情感联结)
- importance 0-10（对维持长期关系越重要分越高），emotion 0-10（情绪强度）
- keywords 2-5 个检索关键词，entities 列出涉及的人名/事物名

输出 JSON：{"memories": [{"content": str, "kind": str, "subject": str, "importance": number, "emotion": number, "keywords": [str], "entities": [str]}]}
没有值得记的就输出 {"memories": []}。"""

_KINDS = {"episodic", "semantic", "preference", "fact", "event", "bond"}


def format_window(msgs: list[MessageRecord]) -> str:
    return "\n".join(f"[{fmt_ts(m.ts)}] {m.user_name}: {m.content}" for m in msgs)


def passes_noise_gate(cand: ExtractedMemory, noise_importance_below: int) -> bool:
    """低重要性且无实体且非稳定类型 → 噪声丢弃。"""
    if cand.importance >= noise_importance_below:
        return True
    if cand.entities or cand.subject:
        return True
    return cand.kind in ("preference", "bond", "fact")


def _clamp(value, lo: float, hi: float, default: float) -> float:
    try:
        return min(hi, max(lo, float(value)))
    except (TypeError, ValueError):
        return default


def parse_candidates(data: dict) -> list[ExtractedMemory]:
    out: list[ExtractedMemory] = []
    for item in data.get("memories", []) or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "")).strip()
        if not content or len(content) > 500:
            continue
        kind = str(item.get("kind", "episodic"))
        if kind not in _KINDS:
            kind = "episodic"
        keywords = [str(k).strip() for k in (item.get("keywords") or []) if str(k).strip()][:8]
        entities = [str(e).strip() for e in (item.get("entities") or []) if str(e).strip()][:8]
        out.append(
            ExtractedMemory(
                content=content,
                kind=kind,  # type: ignore[arg-type]
                subject=str(item.get("subject", "")).strip(),
                importance=_clamp(item.get("importance", 5), 0, 10, 5),
                emotion=_clamp(item.get("emotion", 0), 0, 10, 0),
                keywords=keywords,
                entities=entities,
            )
        )
    return out


@dataclass
class IngestResult:
    extracted: int = 0
    inserted: int = 0
    merged: int = 0     # 复现强化（未新建）
    dropped: int = 0    # 噪声丢弃
    group_id: str = ""
    new_memories: list[tuple[int, str]] = field(default_factory=list)
    reinforced_contents: list[str] = field(default_factory=list)


class Ingestor:
    def __init__(self, store: "MemoryStore", chat: "ChatBackend",
                 embedder: "EmbeddingProvider", cfg: BotConfig, clock: "Clock") -> None:
        self.store = store
        self.chat = chat
        self.embedder = embedder
        self.cfg = cfg
        self.clock = clock

    async def ingest_messages(self, msgs: list[MessageRecord]) -> IngestResult:
        if not msgs:
            return IngestResult()
        group_id = msgs[0].group_id
        now = self.clock.now()
        result = IngestResult(group_id=group_id)

        try:
            data = await self.chat.extract_json(EXTRACT_SYSTEM, format_window(msgs))
        except Exception:
            log.exception("记忆提取失败，本窗口跳过：%s", [m.content[:30] for m in msgs])
            return result

        candidates = parse_candidates(data)
        result.extracted = len(candidates)
        if not candidates:
            return result

        name_to_id = {m.user_name: m.user_id for m in msgs}
        vecs = await self.embedder.embed([c.content for c in candidates])
        source_ids = [m.id for m in msgs if m.id]

        for cand, vec in zip(candidates, vecs):
            if not passes_noise_gate(cand, self.cfg.ingest.noise_importance_below):
                result.dropped += 1
                continue

            entities = list(dict.fromkeys(
                ([cand.subject] if cand.subject else []) + cand.entities
            ))
            dup_id = await self._find_duplicate(vec, group_id)
            if dup_id is not None:
                old = await self.store.get_memory(dup_id)
                if old is not None:
                    await self.store.update_memory(
                        dup_id,
                        recurrence_count=old.recurrence_count + 1,
                        importance=max(old.importance, cand.importance),
                    )
                    result.merged += 1
                    result.reinforced_contents.append(old.content)
                    continue

            if cand.subject == "self":
                subject_id: str | None = "self"
            else:
                subject_id = name_to_id.get(cand.subject, cand.subject or None)
            mem = MemoryRecord(
                stage="staging",
                kind=cand.kind,
                group_id=group_id,
                subject_user_id=subject_id,
                actor_user_id=subject_id,
                content=cand.content,
                keywords=cand.keywords,
                entities=entities,
                importance=cand.importance,
                emotion=cand.emotion,
                visibility="group",
                created_at=now,
                strength=initial_strength(cand.importance, self.cfg.decay.initial_base_days),
                source_msg_ids=source_ids,
            )
            mem_id = await self.store.add_memory(mem)
            await self.store.save_embedding(mem_id, self.embedder.name, vec)
            result.inserted += 1
            result.new_memories.append((mem_id, cand.content))
        return result

    async def _find_duplicate(self, vec, group_id: str) -> int | None:
        """同群 active 记忆中语义最近邻超过阈值 → 返回其 id。"""
        hits = await self.store.vector_topk(self.embedder.name, vec, 1, group_id=group_id)
        if hits and hits[0][1] >= self.cfg.ingest.dedup_threshold:
            return hits[0][0]
        return None
