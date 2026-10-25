"""离线 Mock 聊天后端：不调 API，用于演示（--mock）与测试。

规则极简但足够走通全链路：
- 记忆提取：按句式抽取（过敏/喜好/备考/养宠物…），主语"我"解析为发言人
- 矛盾判定：新内容含"现在/不再"→ 冲突
- 归纳：拼接源观察
- 回复：若上下文带回忆块则显式引用第一条，证明记忆被注入
"""

from __future__ import annotations

import re
from typing import Any

_LINE_RE = re.compile(r"^(?:\[[^\]]*\]\s*)?(\S{1,12}):\s*(.+)$", re.MULTILINE)
_PATTERNS = [
    (re.compile(r"(?:我|(\S{1,8}))对(\S{1,12})过敏"), "fact", 8.0, "过敏"),
    (re.compile(r"(?:我|(\S{1,8}))(?:不喜欢|讨厌)(\S{1,12})"), "preference", 6.0, "不喜欢"),
    (re.compile(r"(?:我|(\S{1,8}))(?:喜欢|爱吃|爱喝|迷上)(\S{1,12})"), "preference", 6.0, "喜欢"),
    (re.compile(r"(?:我|(\S{1,8}))(?:在准备|要考|开始准备)(\S{1,12})"), "event", 6.0, "在准备"),
    (re.compile(r"(?:我|(\S{1,8}))养了(?:一只|一个)?(\S{1,10})"), "fact", 5.0, "养了"),
]


class MockChatClient:
    def __init__(self) -> None:
        self.last_chat_messages: list[dict[str, str]] = []
        self.last_extract_input: str = ""

    async def achat(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
        self.last_chat_messages = messages
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        if "[你的回忆" in system:
            in_block = False
            for line in system.splitlines():
                stripped = line.strip()
                if stripped.startswith("[你的回忆"):
                    in_block = True
                    continue
                if in_block and stripped.startswith("- "):
                    return f"（mock）我记得——{stripped[2:]}。这方面我会留意的。"
            return "（mock）我好像想起点什么，但说不清。"
        tail = user.strip().splitlines()[-1] if user.strip() else ""
        return f"（mock回复）收到：{tail}"

    async def extract_json(self, system: str, user: str) -> dict[str, Any]:
        self.last_extract_input = user
        if "记忆提取" in system:
            return {"memories": self._extract_memories(user)}
        if "矛盾" in system:
            return {"conflict": bool(re.search(r"现在|不再|换了|戒了", user))}
        if "归纳" in system or "语义记忆" in system:
            items = re.findall(r"^- (.+)$", user, flags=re.MULTILINE)
            return {"summary": f"综合观察：{'；'.join(items)}", "keywords": ["综合"]}
        return {}

    @staticmethod
    def _extract_memories(window: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for speaker, content in _LINE_RE.findall(window):
            for pattern, kind, importance, verb in _PATTERNS:
                m = pattern.search(content)
                if not m:
                    continue
                subject_raw, thing = m.group(1), m.group(2)
                subject = subject_raw if subject_raw else speaker
                out.append(
                    {
                        "content": f"{subject}{verb}{thing}" if verb in ("喜欢", "养了")
                        else (f"{subject}对{thing}过敏" if verb == "过敏"
                              else f"{subject}{verb}{thing}"),
                        "kind": kind,
                        "subject": subject,
                        "importance": importance,
                        "emotion": 4.0 if verb == "过敏" else 2.0,
                        "keywords": [thing, subject],
                        "entities": [subject, thing],
                    }
                )
                break
        return out
