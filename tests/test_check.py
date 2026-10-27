"""启动体检 run_checks 与 GUI 的 /api/check、/api/mode。"""

import shutil
import threading
import json
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from config import BotConfig
from gui.server import GUIApp, make_handler
from main import run_checks

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def app(tmp_path):
    """最小 GUI 实例（mock 管线 + 临时目录配置），供端点测试。"""
    cfg = BotConfig()
    cfg.database = str(tmp_path / "mem.db")
    cfg.sim_group_id = "g1"
    cfg.ingest.window_size = 2
    cfg.ingest.min_interval_seconds = 0
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
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


async def test_run_checks_mock_offline(tmp_path):
    """离线模式：不触碰网络，全部检查项结构完整且无致命问题。"""
    cfg = BotConfig()
    cfg.database = str(tmp_path / "mem.db")
    cfg.persona_file = "personas/default.yaml"
    checks = await run_checks(cfg, mock=True)

    names = [c["name"] for c in checks]
    assert "角色卡" in names and "数据库（含 FTS5 与完整性）" in names
    assert "中文分词（jieba）" in names and "NapCat WS 端口" in names
    for c in checks:
        assert isinstance(c["ok"], bool) and isinstance(c["warn"], bool)
    skipped = next(c for c in checks if c["name"] == "聊天模型 API")
    assert skipped["warn"] and "跳过" in skipped["detail"]
    assert not [c for c in checks if not c["ok"]]


async def test_run_checks_detects_broken_persona_and_dead_ws(tmp_path):
    """体检能提前发现：角色卡路径错误、NapCat 端口不通（警告级）。"""
    cfg = BotConfig()
    cfg.database = str(tmp_path / "mem.db")
    cfg.persona_file = str(tmp_path / "不存在.yaml")
    cfg.onebot.ws_url = "ws://127.0.0.1:1"    # 保留端口，几乎必然关闭
    checks = await run_checks(cfg, mock=True)

    persona = next(c for c in checks if c["name"] == "角色卡")
    assert not persona["ok"]
    ws = next(c for c in checks if c["name"] == "NapCat WS 端口")
    assert ws["ok"] and ws["warn"]            # 警告级：只影响 QQ 模式
    assert "仅影响 QQ 模式" in ws["detail"]


def test_gui_check_endpoint(app):
    """/api/check：返回结构化体检，角色卡等检查项齐备。"""
    resp = _post(app.port, "/api/check", {})   # 与前端一致：POST 触发体检
    assert resp["ok"] and "fatal" in resp
    names = {c["name"] for c in resp["checks"]}
    assert "角色卡" in names and "NapCat WS 端口" in names
    assert not resp["fatal"]                  # 临时库 fresh，应无致命项


def test_gui_mode_endpoint_requires_and_uses_key(app):
    """/api/mode：未配置密钥拒绝切真实 API；配置后热切换、可切回。"""
    r = _post(app.port, "/api/mode", {"mock": False})
    assert not r["ok"] and "密钥" in r["error"]
    assert app.mock is True                   # 拒绝时不重建

    from chat.llm import LLMChatClient
    from chat.mock import MockChatClient

    assert isinstance(app.pipeline.chat, MockChatClient)
    app.cfg.llm.api_key = "sk-test"           # 仅内存：构造客户端不触发网络
    r2 = _post(app.port, "/api/mode", {"mock": False})
    assert r2["ok"] and r2["mock"] is False
    assert isinstance(app.pipeline.chat, LLMChatClient)

    r3 = _post(app.port, "/api/mode", {"mock": True})
    assert r3["ok"] and r3["mock"] is True
    assert isinstance(app.pipeline.chat, MockChatClient)
