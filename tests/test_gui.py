"""图形控制台：离线端到端（mock 管线 + 临时目录配置，不碰任何 API）。"""

from __future__ import annotations

import json
import shutil
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from config import BotConfig, load_config
from gui.config_io import read_env
from gui.server import GUIApp, make_handler

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def app(tmp_path):
    cfg = BotConfig()
    cfg.database = str(tmp_path / "mem.db")
    cfg.sim_group_id = "g1"
    cfg.ingest.window_size = 2
    cfg.ingest.min_interval_seconds = 0
    # 兴趣门槛拉满：除 @必回 外一律潜水 → 可确定性断言"未回话附评分明细"
    cfg.behavior.interest_threshold = 999
    card = tmp_path / "card.yaml"
    shutil.copy(ROOT / "personas" / "default.yaml", card)
    cfg.persona_file = str(card)

    gui = GUIApp(root=ROOT, cfg=cfg, mock=True,
                 config_path=str(tmp_path / "config.yaml"),
                 env_path=str(tmp_path / ".env"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(gui))
    gui.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield gui
    server.shutdown()
    server.server_close()
    gui.close()


def _get(port: int, path: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=20) as r:
        return json.loads(r.read())


def _post(port: int, path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:          # 服务端 500 也返回 JSON 错误体
        return json.loads(e.read())


def test_pages_and_status(app):
    import urllib.request as u

    home = u.urlopen(f"http://127.0.0.1:{app.port}/", timeout=10).read().decode("utf-8")
    assert "Mnemochat" in home
    editor = u.urlopen(f"http://127.0.0.1:{app.port}/editor", timeout=10).read().decode("utf-8")
    assert "人格卡修改器" in editor

    st = _get(app.port, "/api/status")
    assert st["ok"] and st["mock"] and st["persona"] == "小满"
    assert st["group_id"] == "g1" and "greeting" in st


def test_chat_reply_silent_interest_and_history(app):
    r1 = _post(app.port, "/api/chat", {"name": "小明", "text": "@小满 在吗"})
    assert r1["ok"] and r1["reply"] and r1["persona"] == "小满"

    r2 = _post(app.port, "/api/chat", {"name": "小红", "text": "今天天气真不错啊朋友们"})
    assert r2["ok"] and r2["reply"] is None
    assert r2["interest"] is not None
    assert r2["interest"]["will_reply"] is False
    assert r2["interest"]["score"] < r2["interest"]["threshold"]
    assert "话题" in r2["interest"]["factors"]

    h = _get(app.port, "/api/history?limit=50")
    names = [m["name"] for m in h["items"]]
    assert "小明" in names and "小满" in names and "小红" in names


def test_warp_memories_and_recall(app):
    _post(app.port, "/api/chat", {"name": "小明", "text": "@小满 在吗"})

    w = _post(app.port, "/api/warp", {"duration": "1d"})
    assert w["ok"] and "promoted" in w and "superseded" in w

    mem = _get(app.port, "/api/memories?stage=all")
    assert mem["ok"] and isinstance(mem["items"], list)

    async def _seed():
        from memory.decay import initial_strength
        from memory.models import MemoryRecord

        m = MemoryRecord(stage="ltm", kind="fact", group_id="g1",
                         subject_user_id="小红", content="小红对花生过敏",
                         importance=6.0, strength=initial_strength(6.0),
                         created_at=app.pipeline.clock.now())
        mid = await app.store.add_memory(m)
        vec = (await app.pipeline.embedder.embed([m.content]))[0]
        await app.store.save_embedding(mid, app.pipeline.embedder.name, vec)

    app.bridge.run(_seed())
    rec = _post(app.port, "/api/recall", {"text": "花生过敏的人能吃花生吗"})
    assert rec["ok"] and any("花生" in it["content"] for it in rec["items"])
    assert all("factors" in it for it in rec["items"])


def test_window_reset_flush_and_bad_duration(app):
    assert _post(app.port, "/api/window_reset", {})["ok"]
    f = _post(app.port, "/api/flush", {})
    assert f["ok"] and "events" in f
    bad = _post(app.port, "/api/warp", {"duration": "abc"})
    assert not bad["ok"]


def test_config_roundtrip_and_preserve_advanced(app, tmp_path):
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"

    vals = _get(app.port, "/api/config")["values"]
    assert vals["CHAT_MODEL"] and vals["SIM_GROUP_ID"] == "g1"

    posted = dict(vals)
    posted["CHAT_MODEL"] = "test-chat-model"
    posted["CHAT_KEY"] = "sk-test-123"
    posted["INTEREST_THRESHOLD"] = 88
    try:
        resp = _post(app.port, "/api/config",
                     {"values": posted, "mode": "mock", "apply": False})
        assert resp["ok"], resp
        assert cfg_path.exists() and env_path.exists()

        cfg = load_config(cfg_path)
        assert cfg.llm.model == "test-chat-model"
        assert cfg.behavior.interest_threshold == 88
        assert cfg.llm.api_key == "sk-test-123"       # 经 os.environ 插值回来
        assert read_env(env_path)["DEEPSEEK_API_KEY"] == "sk-test-123"

        # 界面未展示的高级项：手改 yaml 后 GUI 保存不丢
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        raw["consolidation"]["min_importance"] = 9
        cfg_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        posted2 = dict(posted)
        posted2["CHAT_MODEL"] = "test-chat-model-2"
        resp2 = _post(app.port, "/api/config",
                      {"values": posted2, "mode": "mock", "apply": False})
        assert resp2["ok"], resp2
        cfg2 = load_config(cfg_path)
        assert cfg2.consolidation.min_importance == 9
        assert cfg2.llm.model == "test-chat-model-2"

        # 回读：GUI 看到的是保存后的值
        assert _get(app.port, "/api/config")["values"]["CHAT_MODEL"] == "test-chat-model-2"
    finally:
        import os

        os.environ.pop("DEEPSEEK_API_KEY", None)


def test_apply_rebuilds_pipeline(app):
    resp = _post(app.port, "/api/config",
                 {"values": {"PERSONA_FILE": "personas/dafeiyu.yaml"},
                  "mode": "mock", "apply": True})
    assert resp["ok"] and resp["applied"], resp
    assert resp["persona"] == "大肥鱼"
    assert _get(app.port, "/api/status")["persona"] == "大肥鱼"

    r = _post(app.port, "/api/chat", {"name": "小明", "text": "@大肥鱼 在吗"})
    assert r["ok"] and r["reply"] and r["persona"] == "大肥鱼"


def test_persona_endpoints(app, tmp_path):
    p = _get(app.port, "/api/persona")
    assert p["ok"] and p["persona"]["name"] == "小满"

    data = dict(p["persona"])
    data["greeting"] = "GUI 测试问候"
    assert _post(app.port, "/api/persona", data)["ok"]
    on_disk = yaml.safe_load((tmp_path / "card.yaml").read_text(encoding="utf-8"))
    assert on_disk["greeting"] == "GUI 测试问候"

    sel = _post(app.port, "/api/persona_select", {"path": "personas/dafeiyu.yaml"})
    assert sel["ok"] and sel["persona"]["name"] == "大肥鱼"
    assert _get(app.port, "/api/persona")["persona"]["name"] == "大肥鱼"

    bad = _post(app.port, "/api/persona_select", {"path": "personas/不存在.yaml"})
    assert not bad["ok"]

    lst = _get(app.port, "/api/personas")
    assert lst["ok"] and len(lst["items"]) >= 3
