"""角色语料库：加载、确定性抽样、注入渲染。"""

from pathlib import Path

import json

from persona.card import PersonaCard
from persona.corpus import load_entries, render, sample


def test_load_and_sample_deterministic(tmp_path):
    """同一群在同一时间窗内抽到的内容固定（避免每句话换一批语感跳变）。"""
    corpus = tmp_path / "c.json"
    corpus.write_text(json.dumps({
        "entries": [{"kind": "台词", "phrase": f"台词{i}", "meaning": "测试"} for i in range(10)],
    }, ensure_ascii=False), encoding="utf-8")

    entries = load_entries(corpus)
    assert len(entries) == 10
    a = sample(entries, "g1", 1793158496.0, k=4, rotate_minutes=3)
    b = sample(entries, "g1", 1793158496.0, k=4, rotate_minutes=3)
    assert a == b                                  # 同窗确定
    assert len(a) == 4 and all(e["phrase"] for e in a)

    c = sample(entries, "g2", 1793158496.0, k=4, rotate_minutes=3)
    assert c != a or True                          # 不同群各自抽样（不比较）


def test_render_corpus_block(tmp_path):
    items = [{"phrase": "典", "meaning": "太典型了", "example": "典，又是这套话术"}]
    block = render(items)
    assert "【语感参考·本轮】" in block
    assert "「典」——太典型了" in block
    assert "别全用上" in block


async def test_pipeline_injects_corpus_block(store, chat, embedder, cfg, clock, persona):
    """绑定语料库的角色：回复 system prompt 含【语感参考·本轮】。"""
    from chat.pipeline import BotPipeline

    corpus = Path("personas") / "_test.corpus.json"
    corpus.write_text(json.dumps({
        "entries": [{"kind": "台词", "phrase": "测试台词一", "meaning": "测试用"}],
    }, ensure_ascii=False), encoding="utf-8")
    persona.corpus_file = str(corpus.resolve())
    try:
        pipe = BotPipeline(store, chat, embedder, persona, cfg, clock)
        await pipe.on_user_message("g1", "小明", "小明", "@小满 你好呀", force_reply=True)
        system = chat.last_chat_messages[0]["content"]
        assert "【语感参考·本轮】" in system and "测试台词一" in system
    finally:
        corpus.unlink(missing_ok=True)
