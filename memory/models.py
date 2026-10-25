"""记忆与消息的数据模型。"""

from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Stage = Literal["staging", "ltm"]
Kind = Literal["episodic", "semantic", "preference", "fact", "event", "bond"]
Visibility = Literal["group", "global", "self"]
Status = Literal["active", "superseded", "archived"]


class MessageRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: Optional[int] = None
    group_id: str
    user_id: str
    user_name: str
    content: str
    ts: float
    replied: bool = False


class MemoryRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: Optional[int] = None
    stage: Stage = "staging"
    kind: Kind = "episodic"
    group_id: str
    subject_user_id: Optional[str] = None
    actor_user_id: Optional[str] = None
    content: str
    keywords: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    importance: float = 5.0
    emotion: float = 0.0
    visibility: Visibility = "group"
    access_count: int = 0
    recurrence_count: int = 0
    last_accessed_at: Optional[float] = None
    created_at: float = 0.0
    strength: float = 1.0
    status: Status = "active"
    superseded_by: Optional[int] = None
    archived_into: Optional[int] = None
    source_msg_ids: list[int] = Field(default_factory=list)

    @property
    def anchor_ts(self) -> float:
        """衰减锚点：最近一次被强化（召回/复现）的时刻，从未被访问过则取创建时刻。"""
        return self.last_accessed_at if self.last_accessed_at else self.created_at


class ExtractedMemory(BaseModel):
    """LLM 提取阶段的候选记忆。"""

    model_config = ConfigDict(extra="ignore")

    content: str
    kind: Kind = "episodic"
    subject: str = ""          # 关于谁（显示名），空=群/世界
    importance: float = 5.0
    emotion: float = 0.0
    keywords: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)


def _loads_or_default(raw: str, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def memory_from_row(row) -> MemoryRecord:
    return MemoryRecord(
        id=row["id"],
        stage=row["stage"],
        kind=row["kind"],
        group_id=row["group_id"],
        subject_user_id=row["subject_user_id"],
        actor_user_id=row["actor_user_id"],
        content=row["content"],
        keywords=_loads_or_default(row["keywords"], []),
        entities=_loads_or_default(row["entities"], []),
        importance=row["importance"],
        emotion=row["emotion"],
        visibility=row["visibility"],
        access_count=row["access_count"],
        recurrence_count=row["recurrence_count"],
        last_accessed_at=row["last_accessed_at"],
        created_at=row["created_at"],
        strength=row["strength"],
        status=row["status"],
        superseded_by=row["superseded_by"],
        archived_into=row["archived_into"],
        source_msg_ids=_loads_or_default(row["source_msg_ids"], []),
    )


def message_from_row(row) -> MessageRecord:
    return MessageRecord(
        id=row["id"],
        group_id=row["group_id"],
        user_id=row["user_id"],
        user_name=row["user_name"],
        content=row["content"],
        ts=row["ts"],
        replied=bool(row["replied"]),
    )
