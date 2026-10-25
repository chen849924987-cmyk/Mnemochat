"""LLM 客户端（OpenAI 兼容协议，DeepSeek 等均可）。

两个角色：
- achat()        回复主模型（cfg.llm）
- extract_json() 提取/巩固等结构化任务（cfg.extractor，可用便宜模型）
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from openai import AsyncOpenAI

from config import BotConfig
from util.text import parse_json_loose

log = logging.getLogger(__name__)


class ChatBackend(Protocol):
    async def achat(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str: ...
    async def extract_json(self, system: str, user: str) -> dict[str, Any]: ...


class LLMChatClient:
    def __init__(self, cfg: BotConfig) -> None:
        self.cfg = cfg
        self._chat = AsyncOpenAI(
            base_url=cfg.llm.base_url,
            api_key=cfg.llm.api_key or "EMPTY",
            max_retries=3,
            timeout=60.0,
        )
        self._extractor = AsyncOpenAI(
            base_url=cfg.extractor.base_url,
            api_key=cfg.extractor.api_key or "EMPTY",
            max_retries=3,
            timeout=120.0,
        )
        self.usage = {"chat_prompt": 0, "chat_completion": 0,
                      "extract_prompt": 0, "extract_completion": 0}

    async def achat(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
        kwargs: dict[str, Any] = dict(
            model=self.cfg.llm.model,
            messages=messages,
            temperature=self.cfg.llm.temperature,
            max_tokens=self.cfg.llm.max_tokens,
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = await self._chat.chat.completions.create(**kwargs)
        if resp.usage:
            self.usage["chat_prompt"] += resp.usage.prompt_tokens
            self.usage["chat_completion"] += resp.usage.completion_tokens
        return resp.choices[0].message.content or ""

    async def extract_json(self, system: str, user: str) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            resp = await self._extractor.chat.completions.create(
                model=self.cfg.extractor.model,
                messages=messages,
                temperature=self.cfg.extractor.temperature,
                max_tokens=self.cfg.extractor.max_tokens,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or ""
        except Exception:
            # 部分兼容端点不支持 response_format，降级重试
            resp = await self._extractor.chat.completions.create(
                model=self.cfg.extractor.model,
                messages=messages,
                temperature=self.cfg.extractor.temperature,
                max_tokens=self.cfg.extractor.max_tokens,
            )
            raw = resp.choices[0].message.content or ""
        if resp.usage:
            self.usage["extract_prompt"] += resp.usage.prompt_tokens
            self.usage["extract_completion"] += resp.usage.completion_tokens
        try:
            return parse_json_loose(raw)
        except ValueError:
            log.warning("提取输出无法解析为 JSON: %s", raw[:200])
            return {}
