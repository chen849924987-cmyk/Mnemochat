"""Telegram Bot API 适配器（getUpdates 长轮询，无需公网/无需证书）。

用法：@BotFather 创建机器人拿 token → .env 里填 TELEGRAM_BOT_TOKEN →
python main.py --telegram。把机器人拉进群即可（隐私模式需关闭，
否则收不到非 @ 消息——BotFather /setprivacy → Disable）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx

from adapter.base import GroupChatAdapter, GroupMessage

if TYPE_CHECKING:
    from chat.pipeline import BotPipeline
    from config import BotConfig
    from util.clock import Clock

log = logging.getLogger(__name__)


def parse_update(update: dict, bot_username: str | None) -> GroupMessage | None:
    """非群文本消息返回 None。@机器人的 mention 被剥离并记为 mentioned。"""
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat", {}) or {}
    if chat.get("type") not in ("group", "supergroup"):
        return None
    text = str(message.get("text") or message.get("caption") or "").strip()
    if not text:
        return None

    mentioned = False
    if bot_username:
        handle = f"@{bot_username}".lower()
        low = text.lower()
        idx = low.find(handle)
        if idx >= 0:
            mentioned = True
            text = (text[:idx] + text[idx + len(handle):]).strip()

    sender = message.get("from", {}) or {}
    user_name = (sender.get("first_name") or sender.get("username")
                 or sender.get("id", "?"))
    # 群聊里以 user id 为稳定标识（first_name 会变）
    return GroupMessage(
        group_id=str(chat.get("id", "")),
        user_id=str(sender.get("id", "")),
        user_name=str(user_name),
        text=text,
        mentioned_bot=mentioned,
        message_id=str(message.get("message_id", "")) or None,
    )


class TelegramAdapter(GroupChatAdapter):
    def __init__(self, pipeline: "BotPipeline", cfg: "BotConfig", clock: "Clock") -> None:
        super().__init__(pipeline, cfg, clock)
        token = cfg.telegram.bot_token
        if not token:
            raise ValueError("缺少 TELEGRAM_BOT_TOKEN（.env 或 config.yaml）")
        self._api = f"https://api.telegram.org/bot{token}"
        self._bot_username: str | None = None
        self._offset = 0

    async def _call(self, client: httpx.AsyncClient, method: str,
                    params: dict) -> dict | None:
        resp = await client.post(f"{self._api}/{method}", json=params)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            log.warning("Telegram %s 返回失败：%s", method, data)
            return None
        return data.get("result")

    async def run(self) -> None:
        timeout = httpx.Timeout(10.0, read=float(self.cfg.telegram.poll_timeout) + 10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            me = await self._call(client, "getMe", {})
            if me:
                self._bot_username = str(me.get("username") or "").lower()
            log.info("Telegram 适配器已启动：@%s", self._bot_username)
            loop_task = asyncio.create_task(self._consolidation_loop())
            try:
                while True:
                    try:
                        updates = await self._call(client, "getUpdates", {
                            "offset": self._offset,
                            "timeout": self.cfg.telegram.poll_timeout,
                            "allowed_updates": ["message"],
                        }) or []
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        log.warning("Telegram getUpdates 失败：%s，5 秒后重试", e)
                        await asyncio.sleep(5)
                        continue
                    for update in updates:
                        self._offset = max(self._offset, int(update["update_id"]) + 1)
                        gm = parse_update(update, self._bot_username)
                        if gm is None or not gm.text:
                            continue
                        await self._handle(client, gm)
            finally:
                loop_task.cancel()

    async def _handle(self, client: httpx.AsyncClient, gm: GroupMessage) -> None:
        from stickers.library import extract_sticker_markers

        try:
            reply = await self.pipeline.on_user_message(
                gm.group_id, gm.user_id, gm.user_name, gm.text,
                force_reply=gm.mentioned_bot,
            )
            if reply:
                # Telegram 暂不支持发图表情，剥离标记只发文字
                _, reply = extract_sticker_markers(reply)
                if not reply:
                    return
                params: dict = {"chat_id": int(gm.group_id), "text": reply}
                if self.cfg.telegram.reply_to_message and gm.message_id:
                    params["reply_to_message_id"] = int(gm.message_id)
                await self._call(client, "sendMessage", params)
        except Exception:
            log.exception("处理 Telegram 群 %s 消息失败：%s", gm.group_id, gm.text[:50])
