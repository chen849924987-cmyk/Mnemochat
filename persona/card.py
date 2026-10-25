"""角色卡：人格锚定层。

- identity/speech_style/catchphrases 构成核心人格——每次固定注入 system prompt，
  永不参与衰减与巩固（人格不漂移）；
- boundary 是知识边界：决定"这个角色被允许知道哪些记忆"，
  拦截其他群的隐私、不该注入的跨群内容。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from memory.models import MemoryRecord


class PersonaBoundary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cross_group: bool = False   # 是否允许使用在其他群习得的群内记忆
    include_self: bool = True   # 是否允许使用机器人自己的私密记忆(visibility=self)


class PersonaCard(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    identity: str = ""
    speech_style: str = ""
    catchphrases: list[str] = Field(default_factory=list)
    memory_stance: str = ""
    boundary: PersonaBoundary = PersonaBoundary()
    system_extra: str = ""

    @classmethod
    def load_yaml(cls, path: str | Path) -> "PersonaCard":
        p = Path(path)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent / p
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls.model_validate(data)

    def allows(self, mem: "MemoryRecord", current_group: str) -> bool:
        """知识边界准入过滤：检索结果注入前的最后一道闸。"""
        if mem.visibility == "global":
            return True
        if mem.visibility == "self":
            return self.boundary.include_self
        # group 可见性
        if mem.group_id == current_group:
            return True
        return self.boundary.cross_group

    def build_system_prompt(self, group_id: str, now_ts: float) -> str:
        from util.text import fmt_ts

        parts: list[str] = []
        if self.identity:
            parts.append(self.identity.strip())
        if self.speech_style:
            parts.append(f"【说话风格】{self.speech_style.strip()}")
        if self.catchphrases:
            parts.append("【口头禅】" + "、".join(self.catchphrases))
        if self.system_extra:
            parts.append(self.system_extra.strip())
        parts.append(f"【当前环境】你在群聊（{group_id}）中，现在是 {fmt_ts(now_ts)}。")
        parts.append("保持简短自然，像真人群聊发言，不要写长篇大论。")
        return "\n\n".join(parts)
