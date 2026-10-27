"""QQ OneBot v11 适配器（正向 WebSocket 客户端）。

对接 NapCat / LLOneBot / Lagrange 等 OneBot 11 实现的正向 WS 端口
（实现侧开启"正向 WebSocket 服务器"，本程序作为客户端连入）。

要点：
- @机器人 → 强制回复；叫名字/概率插话由 behavior 决定；
- 消息段（at/表情/图片…）转成可读文本再进管线，@自己 的段被剥离并记为 mentioned；
- 自身 QQ 号通过 get_login_info / lifecycle 事件自动获取；
- 断线自动重连；回复并发受信号量限制，防止 LLM 突发风暴。
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from pathlib import Path
from typing import TYPE_CHECKING

from adapter.base import GroupChatAdapter, GroupMessage

if TYPE_CHECKING:
    from chat.pipeline import BotPipeline
    from config import BotConfig
    from util.clock import Clock

log = logging.getLogger(__name__)

__all__ = ["GroupMessage", "OneBotV11Adapter", "build_reply_segments",
           "cq_to_text", "extract_self_id", "parse_group_message", "segments_to_text"]


def segments_to_text(segments: list[dict], self_qq: str | None) -> tuple[str, bool]:
    """消息段数组 → 纯文本；@机器人 的段剥离并返回 mentioned 标记。"""
    parts: list[str] = []
    mentioned = False
    for seg in segments:
        stype = seg.get("type", "")
        data = seg.get("data", {}) or {}
        if stype == "text":
            parts.append(str(data.get("text", "")))
        elif stype == "at":
            qq = str(data.get("qq", ""))
            if self_qq and qq == self_qq:
                mentioned = True
                parts.append("@我")
            else:
                parts.append(f"@{data.get('name') or qq}")
        elif stype == "face":
            parts.append("[表情]")
        elif stype == "image":
            parts.append("[图片]")
        elif stype == "record":
            parts.append("[语音]")
        elif stype == "video":
            parts.append("[视频]")
        elif stype == "forward":
            parts.append("[合并转发]")
        elif stype == "reply":
            continue  # 引用回复的原文已在后续段里
        else:
            parts.append(f"[{stype}]")
    return "".join(parts).strip(), mentioned


_CQ_SELF_AT = re.compile(r"\[CQ:at,qq=(\d+)[^\]]*\]")


def cq_to_text(raw: str, self_qq: str | None) -> tuple[str, bool]:
    """CQ 码字符串的降级处理（多数实现默认发数组格式，此为兼容路径）。"""
    mentioned = False

    def _sub(m: re.Match) -> str:
        nonlocal mentioned
        if self_qq and m.group(1) == self_qq:
            mentioned = True
            return "@我"
        return f"@{m.group(1)}"

    text = _CQ_SELF_AT.sub(_sub, raw)
    text = re.sub(r"\[CQ:(?:face|record|video|forward)[^\]]*\]",
                  lambda m: f"[{m.group(0).split(',')[0][4:]}]", text)
    text = re.sub(r"\[CQ:image[^\]]*\]", "[图片]", text)
    text = re.sub(r"\[CQ:reply[^\]]*\]", "", text)
    return text.strip(), mentioned


def extract_self_id(data: dict) -> str | None:
    """从 API 响应（get_login_info）或 lifecycle 事件中提取自身 QQ 号。"""
    if data.get("echo") == "login" and isinstance(data.get("data"), dict):
        uid = data["data"].get("user_id")
        return str(uid) if uid is not None else None
    if data.get("post_type") == "meta_event" and data.get("meta_event_type") == "lifecycle":
        sid = data.get("self_id")
        return str(sid) if sid is not None else None
    return None


def parse_group_message(event: dict, self_qq: str | None) -> GroupMessage | None:
    """非群消息事件返回 None。"""
    if event.get("post_type") != "message" or event.get("message_type") != "group":
        return None
    msg = event.get("message")
    if isinstance(msg, list):
        text, mentioned = segments_to_text(msg, self_qq)
    else:
        text, mentioned = cq_to_text(str(msg or ""), self_qq)
    sender = event.get("sender", {}) or {}
    user_name = (sender.get("card") or sender.get("nickname")
                 or str(event.get("user_id", "?")))
    return GroupMessage(
        group_id=str(event.get("group_id", "")),
        user_id=str(event.get("user_id", "")),
        user_name=str(user_name),
        text=text,
        mentioned_bot=mentioned,
        message_id=str(event.get("message_id", "")) or None,
    )


def parse_private_message(event: dict) -> GroupMessage | None:
    """好友私聊 → private:<QQ号> 虚拟群消息（记忆与群聊完全分开）。"""
    if event.get("post_type") != "message" or event.get("message_type") != "private":
        return None
    msg = event.get("message")
    text, _ = (segments_to_text(msg, None)
               if isinstance(msg, list) else cq_to_text(str(msg or ""), None))
    if not text:
        return None
    sender = event.get("sender", {}) or {}
    uid = str(event.get("user_id", "?"))
    user_name = (sender.get("card") or sender.get("nickname") or uid)
    return GroupMessage(
        group_id=f"private:{uid}",
        user_id=uid,
        user_name=str(user_name),
        text=text,
        mentioned_bot=True,          # 私聊 = 对方在直接跟你说话
        message_id=str(event.get("message_id", "")) or None,
        private=True,
    )


def build_reply_segments(reply_text: str, at_qq: str | None, at_sender: bool) -> list[dict]:
    segments: list[dict] = []
    if at_sender and at_qq:
        segments.append({"type": "at", "data": {"qq": at_qq}})
        segments.append({"type": "text", "data": {"text": " "}})
    segments.append({"type": "text", "data": {"text": reply_text}})
    return segments


def image_urls_from_event(event: dict) -> list[str]:
    """提取群消息里的图片段下载地址（表情包收集的入口）。"""
    msg = event.get("message")
    urls: list[str] = []
    if isinstance(msg, list):
        for seg in msg:
            if seg.get("type") == "image":
                url = (seg.get("data") or {}).get("url")
                if url:
                    urls.append(str(url))
    return urls


def split_reply(reply: str, max_parts: int = 5) -> list[str]:
    """按行拆成多条消息（角色卡常见的'一条一行'铁律）；
    超过 max_parts 时多余行合并进最后一条，防刷屏。"""
    parts = [line.strip() for line in reply.splitlines() if line.strip()]
    if len(parts) > max_parts:
        parts = parts[: max_parts - 1] + [" ".join(parts[max_parts - 1 :])]
    return parts or [reply]


class OneBotV11Adapter(GroupChatAdapter):
    def __init__(self, pipeline: "BotPipeline", cfg: "BotConfig", clock: "Clock") -> None:
        super().__init__(pipeline, cfg, clock)
        self.self_qq: str | None = None
        self._ws = None
        self._last_visit: dict[int, float] = {}   # 串门关系下标 -> 上次串门时刻
        ocfg = cfg.onebot
        self._sem = asyncio.Semaphore(max(1, ocfg.max_concurrent_replies))
        # 串门顶号模式：客人发言与主卡接话经同一账号的这条连接发出
        self.pipeline.social_sender = self.send_group_text

    async def send_group_text(self, group_id: str, text: str) -> None:
        """主动向群发纯文本（串门顶号：客卡/主卡发言都走这里）。"""
        if self._ws is None:
            log.warning("WS 未连接，串门发言无法发送：%s", text[:40])
            return
        await self._ws.send(json.dumps({
            "action": "send_group_msg",
            "params": {"group_id": int(group_id),
                       "message": [{"type": "text", "data": {"text": text}}]},
        }, ensure_ascii=False))

    async def run(self) -> None:
        import websockets

        ocfg = self.cfg.onebot
        headers = [("Authorization", f"Bearer {ocfg.access_token}")] if ocfg.access_token else []
        log.info("OneBot v11 适配器连接 %s", ocfg.ws_url)
        while True:
            try:
                connect_kwargs: dict = {}
                if headers:
                    connect_kwargs["additional_headers"] = headers
                async with websockets.connect(ocfg.ws_url, **connect_kwargs) as ws:
                    log.info("OneBot WS 已连接")
                    await self._serve(ws)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("OneBot WS 断开：%s，5 秒后重连", e)
                await asyncio.sleep(5)

    async def _serve(self, ws) -> None:
        # 询问自身身份（响应在消息流里以 echo=login 回来）
        await ws.send(json.dumps({"action": "get_login_info", "echo": "login"}))
        self._ws = ws
        from social.visits import run_visit_scheduler

        loop_task = asyncio.create_task(self._consolidation_loop())
        visit_task = asyncio.create_task(run_visit_scheduler(
            self.pipeline, self.pipeline.store, self.cfg, self.clock,
            self._last_visit, mock=self.pipeline.mock))
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(data, dict):
                    continue
                self_id = extract_self_id(data)
                if self_id:
                    self.self_qq = self_id
                    continue
                gm = parse_group_message(data, self.self_qq)
                if gm is None and self.cfg.private_chat.enabled:
                    gm = parse_private_message(data)
                if gm is not None:
                    if self.self_qq and gm.user_id == self.self_qq:
                        continue   # 自己发出的消息（串门顶号等）不再回流处理
                    if self.sticker_lib is not None:
                        # 旁观收图：表情包下载去重入库（不阻塞消息处理）
                        now = self.clock.now()
                        for url in image_urls_from_event(data):
                            asyncio.create_task(self.sticker_lib.collect(
                                url, gm.group_id, gm.user_name, now))
                    if not gm.text:
                        continue
                    asyncio.create_task(self._handle(ws, gm))
        finally:
            self._ws = None
            loop_task.cancel()
            visit_task.cancel()

    async def _handle(self, ws, gm: GroupMessage) -> None:
        from stickers.library import extract_sticker_markers

        async with self._sem:
            try:
                force_reply = gm.mentioned_bot
                if (gm.private and self.cfg.private_chat.enabled
                        and self.cfg.private_chat.always_reply):
                    force_reply = True   # 私聊每条必回（配置可关）
                reply = await self.pipeline.on_user_message(
                    gm.group_id, gm.user_id, gm.user_name, gm.text,
                    force_reply=force_reply,
                )
                if reply:
                    # 表情包标记：先发图（先图后字，与角色卡 send_sticker 规范一致）
                    markers, clean = extract_sticker_markers(reply)
                    for tag in markers[:1]:
                        sticker = None
                        if self.sticker_lib is not None:
                            sticker = await self.sticker_lib.pick_by_tag(tag, gm.group_id)
                        if sticker:
                            path = Path(sticker["file_path"]).as_posix()
                            await ws.send(json.dumps({
                                "action": "send_group_msg",
                                "params": {
                                    "group_id": int(gm.group_id),
                                    "message": [{"type": "image",
                                                 "data": {"file": f"file:///{path}"}}],
                                },
                            }, ensure_ascii=False))
                            await asyncio.sleep(random.uniform(0.5, 1.2))
                    reply = clean

                    # 按行拆成多条消息：第一条 @ 发送者，其余纯文本；
                    # 条间随机小延迟，模拟真人打字节奏（也降低风控风险）
                    for i, part in enumerate(split_reply(reply)):
                        if i:
                            await asyncio.sleep(random.uniform(0.4, 1.2))
                        if gm.private:
                            payload = {
                                "action": "send_private_msg",
                                "params": {
                                    "user_id": int(gm.private_user_id),
                                    "message": [{"type": "text",
                                                 "data": {"text": part}}],
                                },
                            }
                        else:
                            payload = {
                                "action": "send_group_msg",
                                "params": {
                                    "group_id": int(gm.group_id),
                                    "message": build_reply_segments(
                                        part, gm.user_id,
                                        self.cfg.onebot.at_sender_on_reply and i == 0,
                                    ),
                                },
                            }
                        await ws.send(json.dumps(payload, ensure_ascii=False))
            except Exception:
                log.exception("处理群 %s 消息失败：%s", gm.group_id, gm.text[:50])
