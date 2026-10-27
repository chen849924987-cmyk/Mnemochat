"""人格守卫：把召回的记忆渲染进 system prompt。

注入格式带相对时间（"约三周前"），配合记忆使用准则，营造"自然想起"而非
"查数据库"的感受。没有记忆时不注入任何块。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from util.text import fmt_relative

if TYPE_CHECKING:
    from memory.recall import RecallResult
    from persona.card import PersonaCard

MEMORY_BLOCK_HEADER = "[你的回忆·非即时感知]"

DEFAULT_STANCE = """把这些回忆当作你自己的记忆自然使用：
- 像人一样偶尔主动提起（"上次你不是说……"），不要机械罗列或全盘复述
- 时间用模糊表述（"前阵子""上个月"），不要报精确日期
- 不确定时不要假装记得，更不要编造记忆"""


def render_memory_block(recall: "RecallResult", persona: "PersonaCard", now: float) -> str:
    if not recall:
        return ""
    stance = (persona.memory_stance or DEFAULT_STANCE).strip()
    lines = [MEMORY_BLOCK_HEADER]
    for item in recall.items:
        mem = item.memory
        when = fmt_relative(mem.anchor_ts, now)
        text = getattr(item, "display", None) or mem.content
        lines.append(f"- {when}，{text}")
    return f"【记忆使用准则】\n{stance}\n\n" + "\n".join(lines)


def build_reply_system(persona: "PersonaCard", recall: "RecallResult",
                       group_id: str, now: float,
                       extra_hint: str = "") -> str:
    system = persona.build_system_prompt(group_id, now)
    block = render_memory_block(recall, persona, now)
    if block:
        system = f"{system}\n\n{block}"
    if extra_hint:
        system = f"{system}\n\n{extra_hint}"
    return system
