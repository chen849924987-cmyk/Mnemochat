"""导出/导入：备份往返保真（FTS 与实体索引在新库重建）。"""

from memory.store import MemoryStore
from util.text import fts_query


async def test_export_import_roundtrip(tmp_path, store, seed_memory, clock):
    mem = await seed_memory("小明对花生过敏", entities=["小明"],
                            keywords=["花生", "过敏"])
    await store.add_message("g1", "小明", "小明", "我对花生过敏", clock.now())

    data = await store.export_all()
    assert len(data["memories"]) == 1
    assert len(data["messages"]) == 1
    assert len(data["memory_entities"]) == 1

    store2 = MemoryStore(tmp_path / "imported.db")
    await store2.init()
    try:
        counts = await store2.import_all(data)
        assert counts["memories"] == 1 and counts["messages"] == 1

        got = await store2.get_memory(mem.id)
        assert got is not None
        assert got.content == "小明对花生过敏"
        assert got.keywords == ["花生", "过敏"]      # JSON 字段保真
        assert got.strength == mem.strength

        # FTS 与实体索引在新库可用
        hits = await store2.bm25_search(fts_query("花生 过敏"), 5)
        assert hits and hits[0][0] == mem.id
        assert "小明" in await store2.entity_names("g1")
    finally:
        await store2.close()
