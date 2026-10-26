"""表情包打标：语境学习 + 可选视觉理解。

- 语境学习（默认）：根据表情包每次出现时的前后消息推断语义标签
  （发三次"哈哈"时出现的图，自动获得"搞笑"标签）；
- 视觉理解（可选，两种接法）：
  - vision.use_chat: true —— 聊天模型本身是多模态 → 直接用它看图（零额外配置）；
  - vision.model 填独立 VL 端点 —— 主模型是纯文本时另接一个视觉 API；
  看图得到的画面描述与语境一起参与打标（标签判定始终由提取模型完成），标签更准。
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from config import BotConfig

if TYPE_CHECKING:
    from chat.llm import ChatBackend
    from util.clock import Clock

    from .library import StickerLibrary

log = logging.getLogger(__name__)

TAG_SYSTEM = """你是表情包标签器。根据一张群聊表情包的画面描述（可能为空）和它在群聊中出现的上下文，给它打 1-4 个标签。

优先使用这些通用分类：搞笑、无语、开心、震惊、赞同、感谢、疑问、猫猫、熊猫头、吃瓜、加油、晚安；有更贴切的梗名也可以用。
标签用途：机器人之后按分类把这张图发回群里。

输出 JSON：{"tags": ["搞笑", "猫猫"]}"""


class VisionDescriber(Protocol):
    async def describe(self, image_bytes: bytes) -> str: ...


def _mime_of(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"GIF":
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


class APIVisionDescriber:
    """OpenAI 兼容视觉端点：可以是独立 VL 模型（硅基流动 Qwen-VL 等），
    也可以是聊天模型本身（多模态模型开启 vision.use_chat 时，端点/密钥/模型名取自 llm 配置）。"""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        from openai import AsyncOpenAI

        self.model = model
        self._client = AsyncOpenAI(base_url=base_url,
                                   api_key=api_key or "EMPTY",
                                   max_retries=2, timeout=60.0)

    async def describe(self, image_bytes: bytes) -> str:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "一句话描述这张群聊表情包的画面内容与情绪倾向"},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{_mime_of(image_bytes)};base64,{b64}"}},
                ],
            }],
            max_tokens=100,
        )
        return (resp.choices[0].message.content or "").strip()


class MockVisionDescriber:
    async def describe(self, image_bytes: bytes) -> str:
        return "一只表情夸张的猫猫（mock 描述）"


def create_vision(cfg: BotConfig, mock: bool) -> VisionDescriber | None:
    """视觉通道选择：mock > 聊天模型本身（use_chat，多模态）> 独立视觉端点 > 纯语境学习。"""
    if mock:
        return MockVisionDescriber()
    v = cfg.sticker.vision
    if v.use_chat:
        # 聊天模型自己看图；若它实际是纯文本模型，describe 会抛错，
        # StickerTagger 捕获后退回"语境描述 + 语境学习"，功能不中断
        return APIVisionDescriber(cfg.llm.base_url, cfg.llm.api_key, cfg.llm.model)
    if not v.model:
        return None   # 纯语境学习模式
    return APIVisionDescriber(v.base_url, v.api_key, v.model)


class StickerTagger:
    def __init__(self, lib: "StickerLibrary", chat: "ChatBackend",
                 vision: VisionDescriber | None, cfg: BotConfig, clock: "Clock") -> None:
        self.lib = lib
        self.chat = chat
        self.vision = vision
        self.cfg = cfg
        self.clock = clock

    async def run(self) -> int:
        """给未打标的表情包批量打标（每轮最多 tag_batch 张）。返回打标数。"""
        batch = await self.lib.untagged(self.cfg.sticker.tag_batch)
        tagged = 0
        for item in batch:
            description = item["description"]
            if self.vision is not None:
                path = await self.lib.file_path(item["id"])
                if path and Path(path).exists():
                    try:
                        description = (await self.vision.describe(
                            Path(path).read_bytes())) or description
                    except Exception:
                        log.exception("视觉描述失败（sticker #%s）", item["id"])
            user = (
                f"【画面描述】{description or '（无视觉信息）'}\n"
                f"【出现语境】\n"
                + "\n".join(f"- {c}" for c in item["contexts"])
            )
            try:
                verdict = await self.chat.extract_json(TAG_SYSTEM, user)
            except Exception:
                log.exception("表情包打标失败（sticker #%s）", item["id"])
                continue
            tags = [str(t).strip() for t in (verdict.get("tags") or [])
                    if str(t).strip()][:4]
            if not tags:
                tags = ["通用"]
            await self.lib.set_tags(item["id"], tags,
                                    description or None)
            tagged += 1
        if tagged:
            log.info("表情包打标完成：%d 张", tagged)
        return tagged
