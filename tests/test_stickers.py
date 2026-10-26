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


def test_create_vision_channels(cfg):
    """看图通道选择：mock > 独立视觉端点 > 纯语境学习；use_chat 时聊天模型优先。"""
    from stickers.tagger import APIVisionDescriber, create_vision

    assert isinstance(create_vision(cfg, mock=True), MockVisionDescriber)

    cfg.sticker.vision.use_chat = False
    cfg.sticker.vision.model = ""
    assert create_vision(cfg, mock=False) is None          # 纯语境学习

    cfg.sticker.vision.base_url = "https://vl.example.com/v1"
    cfg.sticker.vision.model = "Qwen/Qwen2.5-VL-32B-Instruct"
    d = create_vision(cfg, mock=False)
    assert isinstance(d, APIVisionDescriber)
    assert d.model == cfg.sticker.vision.model             # 独立 VL 端点

    cfg.sticker.vision.use_chat = True
    d = create_vision(cfg, mock=False)
    assert isinstance(d, APIVisionDescriber)
    assert d.model == cfg.llm.model                        # 多模态聊天模型接管


def _lib_varied(store, cfg):
    """每个 URL 一份不同字节的数据（同图重发才共享 md5，测遗忘/排序需要多张不同的图）。"""
    async def fake_download(url: str) -> bytes | None:
        return PNG + url.encode()
    return StickerLibrary(store, cfg, downloader=fake_download)


async def test_prune_forgets_stale_stickers(store, cfg, clock):
    """遗忘：群里 retain_days 天没出现的梗删除行、语境与文件；近期出现的保留。"""
    lib = _lib_varied(store, cfg)
    now = clock.now()
    sid_old = await lib.collect("http://x/old.png", "g1", "老王", now - 120 * 86400)
    sid_new = await lib.collect("http://x/new.png", "g1", "小明", now)
    assert sid_old != sid_new
    old_path = await lib.file_path(sid_old)
    assert Path(old_path).exists()

    pruned = await lib.prune(now, retain_days=90)
    assert pruned == 1
    assert not Path(old_path).exists()                       # 文件已删
    assert await lib.file_path(sid_old) is None              # 行已删
    rows = await store.db.execute_fetchall(
        "SELECT COUNT(*) AS c FROM sticker_contexts WHERE sticker_id=?", (sid_old,))
    assert rows[0]["c"] == 0                                 # 语境已删
    assert [r["id"] for r in await lib.list_stickers(10)] == [sid_new]

    # 无过期内容时清理是空操作
    assert await lib.prune(now, retain_days=90) == 0


async def test_pick_prefers_recent_over_old_hot(store, cfg, clock):
    """软遗忘：选取热度按时间距离衰减——老的高频梗排不进候选，新梗优先。"""
    cfg.sticker.send_cooldown_seconds = 0
    lib = _lib_varied(store, cfg)
    now = clock.now()
    stale_ids, fresh_ids = [], []
    for i in range(3):
        stale = await lib.collect(f"http://x/old{i}.png", "g1", "老王", now - 120 * 86400)
        await lib.set_tags(stale, ["搞笑"])
        await store.db.execute("UPDATE stickers SET use_count=10 WHERE id=?", (stale,))
        fresh = await lib.collect(f"http://x/new{i}.png", "g1", "小明", now)
        await lib.set_tags(fresh, ["搞笑"])
        stale_ids.append(stale)
        fresh_ids.append(fresh)
    await store.db.commit()

    picks = {(await lib.pick_by_tag("搞笑", "g1"))["id"] for _ in range(15)}
    assert picks <= set(fresh_ids)   # use_count=10 的 120 天前冷梗全部沉底


async def test_sticker_store_can_be_shared(tmp_path, cfg):
    """记忆隔离部署：主库不建表情包表，表情包集中在独立共享库。"""
    from memory.store import MemoryStore

    core = MemoryStore(tmp_path / "core.db")
    await core.init(with_stickers=False)
    shared = MemoryStore(tmp_path / "shared.db")
    await shared.init(with_stickers=True)

    async def fake_download(url: str) -> bytes | None:
        return PNG
    lib = StickerLibrary(shared, cfg, downloader=fake_download)
    sid = await lib.collect("http://x/1.png", "g1", "小明", 1_000.0)
    assert sid is not None

    core_tables = {r["name"] for r in await core.db.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    shared_tables = {r["name"] for r in await shared.db.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "stickers" not in core_tables and "messages" in core_tables
    assert "stickers" in shared_tables
    await core.close()
    await shared.close()
