"""表情包打标：语境学习 + 可选视觉理解。

- 语境学习（默认）：根据表情包每次出现时的前后消息推断语义标签
  （发三次"哈哈"时出现的图，自动获得"搞笑"标签）；
- 视觉理解（可选）：配置 OpenAI 兼容视觉端点后，先让视觉模型描述画面，
  描述与语境一起参与打标，标签更准。
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from config import BotConfig, StickerVisionConfig

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
    """OpenAI 兼容视觉端点（硅基流动 Qwen-VL / 任何 VL 模型均可）。"""

    def __init__(self, vcfg: StickerVisionConfig) -> None:
        from openai import AsyncOpenAI

        self.cfg = vcfg
        self._client = AsyncOpenAI(base_url=vcfg.base_url,
                                   api_key=vcfg.api_key or "EMPTY",
                                   max_retries=2, timeout=60.0)

    async def describe(self, image_bytes: bytes) -> str:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        resp = await self._client.chat.completions.create(
            model=self.cfg.model,
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
    if mock:
        return MockVisionDescriber()
    if not cfg.sticker.vision.model:
        return None   # 纯语境学习模式
    return APIVisionDescriber(cfg.sticker.vision)


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
