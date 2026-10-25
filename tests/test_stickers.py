"""表情包子系统：收集去重、打标、发送选择、prompt 注入、标记解析。"""

from pathlib import Path

from chat.pipeline import BotPipeline
from stickers.library import StickerLibrary, extract_sticker_markers
from stickers.tagger import MockVisionDescriber, StickerTagger

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _lib(store, cfg, data: bytes = PNG):
    async def fake_download(url: str) -> bytes | None:
        return data
    return StickerLibrary(store, cfg, downloader=fake_download)


def test_extract_markers():
    markers, clean = extract_sticker_markers("看这个 [[sticker:搞笑]] 哈哈\n[[sticker:猫猫]]")
    assert markers == ["搞笑", "猫猫"]
    assert clean == "看这个 哈哈"
    assert extract_sticker_markers("没有标记的普通回复") == ([], "没有标记的普通回复")


async def test_collect_dedup_and_size_gate(store, cfg, clock):
    cfg.sticker.collect_max_kb = 1   # 1KB 上限便于测试
    lib = _lib(store, cfg)

    sid1 = await lib.collect("http://x/1.png", "g1", "小明", clock.now())
    assert sid1 is not None
    # 同一内容再次出现：去重 + 计数 + 补语境
    sid2 = await lib.collect("http://x/again.png", "g1", "小红", clock.now())
    assert sid2 == sid1
    stickers = await lib.list_stickers()
    assert len(stickers) == 1 and stickers[0]["use_count"] == 2

    # 超大图（照片）不入库
    big = _lib(store, cfg, b"\x89PNG\r\n\x1a\n" + b"\x00" * 4096)
    assert await big.collect("http://x/big.png", "g1", "小明", clock.now()) is None
    assert len(await lib.list_stickers()) == 1


async def test_tagger_learns_from_context(store, cfg, clock, chat):
    lib = _lib(store, cfg)
    sid = await lib.collect("http://x/1.png", "g1", "小明", clock.now())
    # 出现语境：大家在笑
    await store.add_message("g1", "小明", "小明", "哈哈哈哈笑死我了", clock.now() - 5)
    await lib.collect("http://x/1.png", "g1", "小明", clock.now())

    tagger = StickerTagger(lib, chat, MockVisionDescriber(), cfg, clock)
    tagged = await tagger.run()
    assert tagged == 1
    stickers = await lib.list_stickers()
    assert "搞笑" in stickers[0]["tags"]
    assert "猫猫" in stickers[0]["description"]     # mock 视觉描述已保存
    # 幂等：打过的标不再处理
    assert await tagger.run() == 0


async def test_pick_by_tag_and_cooldown(store, cfg, clock):
    lib = _lib(store, cfg)
    sid = await lib.collect("http://x/1.png", "g1", "小明", clock.now())
    await lib.set_tags(sid, ["搞笑"])

    pick = await lib.pick_by_tag("搞笑", "g1")
    assert pick is not None and Path(pick["file_path"]).exists()
    # 冷却期内不发
    assert await lib.pick_by_tag("搞笑", "g1") is None
    # 无匹配标签
    assert await lib.pick_by_tag("不存在的标签", "g2") is None


async def test_pipeline_injects_sticker_block(store, chat, embedder, cfg, clock,
                                              persona, make_msg):
    lib = _lib(store, cfg)
    sid = await lib.collect("http://x/1.png", "g1", "小明", clock.now())
    await lib.set_tags(sid, ["搞笑", "猫猫"])
    pipe = BotPipeline(store, chat, embedder, persona, cfg, clock,
                       sticker_lib=lib, vision=MockVisionDescriber())

    await pipe.on_user_message("g1", "小明", "小明", "在吗", force_reply=True)
    system = chat.last_chat_messages[0]["content"]
    assert "【表情包】" in system
    assert "搞笑(1)" in system and "[[sticker:" in system


async def test_untagged_list_has_contexts(store, cfg, clock):
    lib = _lib(store, cfg)
    await store.add_message("g1", "小明", "小明", "笑死", clock.now())
    sid = await lib.collect("http://x/1.png", "g1", "小明", clock.now())
    batch = await lib.untagged(5)
    assert len(batch) == 1
    assert batch[0]["id"] == sid
    assert any("小明" in c for c in batch[0]["contexts"])
