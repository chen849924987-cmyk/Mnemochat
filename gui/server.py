"""Mnemochat 图形控制台：本地网页一体化入口（Python 标准库实现，零额外依赖）。

  python main.py --gui [--mock] [--port 8765]

一个页面四个标签页：
- 聊天：群聊模拟器（发言/兴趣评分明细/时间快进/巩固/清窗口/串门，含表情包发图）
- 记忆：多记忆宇宙总览与切换（主卡 + 各串门客卡）、召回预演、清空（先备份）
- 人格：角色卡编辑（复用人格卡修改器，滑条/酒馆卡导入/热重载）
- 设置：聊天/提取/嵌入模型与密钥、群聊行为、串门关系、平台接入——保存即生成
  config.yaml 与 .env，可一键应用到运行中的机器人（无需改代码、无需重启）

架构：HTTP 线程 + 常驻 asyncio 循环（桥接 BotPipeline）；仅监听 127.0.0.1。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from editor.server import EditorState, load_persona, save_persona
from gui.config_io import (ENV_CHAT_KEY, ENV_EMBED_KEY, ENV_TELEGRAM,
                           merge_config_values, read_env, render_config,
                           values_from_config, write_env)
from persona.card import PersonaCard
from persona.convert import extract_card_from_png, st_card_to_persona

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_PAGE = Path(__file__).resolve().parent / "page.html"
_EDITOR_PAGE = _ROOT / "editor" / "page.html"


class _Bridge:
    """把同步 HTTP 线程的调用转发到常驻 asyncio 循环（管线与 SQLite 都住在那里）。"""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._thread.start()

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)
        self.loop.close()


class GUIApp:
    def __init__(self, root: Path, cfg, mock: bool = False,
                 config_path: str = "config.yaml", env_path: str | None = None,
                 port: int = 8765) -> None:
        self.root = Path(root)
        self.cfg = cfg
        self.mock = bool(mock)
        self.config_path = config_path
        self.cfg_path = Path(config_path) \
            if Path(config_path).is_absolute() else self.root / config_path
        self.env_path = Path(env_path) if env_path else self.root / ".env"
        self.port = port
        self.bridge = _Bridge()
        self.pipeline = None
        self.store = None
        self.shown_mem: set[int] = set()      # 已在事件流里播报过的新记忆
        self.editor_state = EditorState(self._resolve(cfg.persona_file))
        try:
            from util.text import warmup

            warmup()
        except Exception:
            pass
        self.bridge.run(self._build(cfg, self.mock))
        self._last_visit: dict[int, float] = {}     # 串门关系下标 -> 上次串门时刻
        self._visit_task = asyncio.run_coroutine_threadsafe(
            self._visit_loop(), self.bridge.loop)

    async def _visit_loop(self) -> None:
        """串门自动调度：共享引擎（GUI 虚拟群无发送器，客人以名字出场）。"""
        from social.visits import run_visit_scheduler

        await run_visit_scheduler(self.pipeline, self.store, self.cfg,
                                  self.pipeline.clock, self._last_visit,
                                  mock=self.mock)

    def _resolve(self, p: str | Path) -> Path:
        path = Path(p)
        return path if path.is_absolute() else self.root / path

    # ---------- 管线 ----------

    async def _build(self, cfg, mock: bool) -> None:
        from main import _build_pipeline

        self.pipeline, self.store = await _build_pipeline(cfg, mock)

    async def _rebuild(self, new_cfg, mock: bool) -> None:
        """按新配置重建管线（换模型/密钥/角色卡后无需重启进程）。"""
        try:
            await self.pipeline.flush_ingest()
        except Exception:
            pass
        old_pipeline, old_store = self.pipeline, self.store
        await self._build(new_cfg, mock)
        self.cfg, self.mock = new_cfg, mock
        self.shown_mem.clear()
        # 新管线就绪后再关旧库（新旧可能指向同一个 SQLite 文件）
        from main import close_stores

        await close_stores(old_pipeline, old_store)

    def close(self) -> None:
        async def _fin() -> None:
            try:
                await self.pipeline.flush_ingest()
            except Exception:
                pass
            from main import close_stores

            await close_stores(self.pipeline, self.store)

        try:
            self._visit_task.cancel()
        except Exception:
            pass
        try:
            self.bridge.run(_fin())
        finally:
            self.bridge.stop()

    # ---------- 聊天 ----------

    def _ingest_events(self) -> dict:
        res = self.pipeline.last_ingest_result
        if not res:
            return {"new": [], "reinforced": []}
        new = []
        for mid, content in res.new_memories:
            if mid not in self.shown_mem:
                self.shown_mem.add(mid)
                new.append(content)
        return {"new": new, "reinforced": list(res.reinforced_contents)}

    async def api_chat(self, name: str, text: str, force: bool) -> dict:
        pipe = self.pipeline
        gid = self.cfg.sim_group_id
        reply = await pipe.on_user_message(gid, name, name, text, force_reply=force)
        if pipe._tasks:
            await asyncio.gather(*pipe._tasks, return_exceptions=True)
        interest = None
        if reply is None and pipe.last_interest is not None:
            v = pipe.last_interest
            interest = {"score": round(v.score, 1),
                        "threshold": self.cfg.behavior.interest_threshold,
                        "factors": v.factors, "will_reply": v.will_reply}
        return {"reply": reply, "interest": interest,
                "events": self._ingest_events(), "persona": pipe.persona.name,
                "ts": pipe.clock.now()}

    async def api_warp(self, seconds: float) -> dict:
        pipe = self.pipeline
        await pipe.flush_ingest()
        new_now = pipe.clock.warp(seconds)
        c = await pipe.consolidate_now()
        return {"ok": True, "now": new_now,
                "promoted": c.promoted, "superseded": c.superseded,
                "semantic": c.semantic_created, "archived": c.archived,
                "stickers_pruned": pipe.last_stickers_pruned,
                "promoted_contents": list(c.promoted_contents),
                "superseded_contents": list(c.superseded_contents),
                "semantic_contents": list(c.semantic_contents),
                "events": self._ingest_events()}

    async def api_consolidate(self) -> dict:
        pipe = self.pipeline
        await pipe.flush_ingest()
        c = await pipe.consolidate_now()
        return {"ok": True, "promoted": c.promoted, "superseded": c.superseded,
                "semantic": c.semantic_created, "archived": c.archived,
                "stickers_pruned": pipe.last_stickers_pruned,
                "promoted_contents": list(c.promoted_contents),
                "superseded_contents": list(c.superseded_contents),
                "semantic_contents": list(c.semantic_contents),
                "drift": (None if pipe.last_drift_report is None else {
                    "drifted": pipe.last_drift_report.drifted,
                    "adherence": pipe.last_drift_report.adherence,
                    "skipped": pipe.last_drift_report.skipped})}

    async def api_flush(self) -> dict:
        await self.pipeline.flush_ingest()
        return {"ok": True, "events": self._ingest_events()}

    async def api_recall(self, text: str) -> dict:
        from memory.models import MessageRecord

        pipe = self.pipeline
        gid = self.cfg.sim_group_id
        probe = MessageRecord(group_id=gid, user_id="探针", user_name="探针",
                              content=text, ts=pipe.clock.now())
        rec = await pipe.recaller.recall(gid, pipe.persona, [probe], reinforce=False)
        items = []
        for it in rec.items:
            m = it.memory
            items.append({"id": m.id, "score": round(it.score, 3),
                          "factors": {k: round(v, 3) for k, v in it.factors.items()},
                          "content": m.content, "stage": m.stage, "kind": m.kind,
                          "strength": round(m.strength, 2),
                          "access": m.access_count, "anchor_ts": m.anchor_ts})
        return {"ok": True, "items": items, "ts": pipe.clock.now()}

    async def api_memories(self, stage: str, q: str, limit: int,
                           universe: str = "") -> dict:
        return await self.api_memories_of(universe, stage, q, limit)

    async def api_visit(self, index: int | None) -> dict:
        """手动触发一次串门（缺省选第一条关系）；自动调度共用同一引擎。"""
        from social.visits import perform_visit

        visits = self.cfg.social.visits
        if not visits:
            return {"ok": False, "error": "还没有配置串门关系——到「设置 · 社交与串门」添加"}
        if index is None:
            index = next((i for i, v in enumerate(visits)), 0)
        if not (0 <= index < len(visits)):
            return {"ok": False, "error": f"串门关系下标越界：{index}"}
        res = await perform_visit(self.pipeline, self.store, self.cfg,
                                  self.pipeline.clock, visits[index],
                                  mock=self.mock)
        self._last_visit[index] = self.pipeline.clock.now()
        return {"ok": True, **res}

    async def api_history(self, limit: int) -> dict:
        rows = await self.store.recent_messages(self.cfg.sim_group_id, limit)
        return {"ok": True, "items": [
            {"name": r.user_name, "user_id": r.user_id, "content": r.content,
             "ts": r.ts, "replied": r.replied} for r in rows]}

    async def api_status(self) -> dict:
        pipe = self.pipeline
        stats = await self.store.stats()
        stage = stats.get("stage", {})
        env = read_env(self.env_path)
        guests = []
        for v in self.cfg.social.visits:
            try:
                guests.append((load_persona(self._resolve(v.persona_file))
                               or {}).get("name") or Path(v.persona_file).stem)
            except Exception:
                guests.append(Path(v.persona_file).stem)
        return {"ok": True, "mock": self.mock,
                "persona": pipe.persona.name, "greeting": pipe.persona.greeting,
                "guests": guests,
                "persona_file": self.cfg.persona_file,
                "group_id": self.cfg.sim_group_id, "database": self.cfg.database,
                "sticker_database": (self.cfg.sticker.database
                                     or f"{self.cfg.database}（同库）"),
                "per_persona_memory": self.cfg.per_persona_memory,
                "messages": stats.get("messages", 0),
                "staging": stage.get("staging", 0), "ltm": stage.get("ltm", 0),
                "chat_key_set": bool(self.cfg.llm.api_key),
                "embed_provider": self.cfg.embedding.provider,
                "env_chat_key_set": bool(env.get(ENV_CHAT_KEY)),
                "config_saved": self.cfg_path.exists(),
                "config_path": str(self.cfg_path)}

    # ---------- 配置 ----------

    async def api_config_save(self, posted: dict, mode: str, apply: bool) -> dict:
        try:
            # 以磁盘最新配置为底（保留界面没展示的手工调参），再叠加表单字段
            disk_cfg = load_config_safe(self.cfg_path, fallback=self.cfg)
            values = merge_config_values(disk_cfg, posted)
            text = render_config(values)
            self.cfg_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cfg_path.with_suffix(self.cfg_path.suffix + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self.cfg_path)
            write_env(self.env_path, {
                ENV_CHAT_KEY: str(values.get("CHAT_KEY", "") or ""),
                ENV_EMBED_KEY: str(values.get("EMBED_KEY", "") or ""),
                ENV_TELEGRAM: str(values.get("TELEGRAM_TOKEN", "") or ""),
            })
            from config import load_config

            new_cfg = load_config(self.cfg_path)
            old_persona = self.cfg.persona_file
            self.cfg = new_cfg
            if new_cfg.persona_file != old_persona:
                self.editor_state = EditorState(self._resolve(new_cfg.persona_file))
            result = {"ok": True, "config_path": str(self.cfg_path)}
            if apply:
                mock = (mode != "api")
                await self._rebuild(new_cfg, mock)
                result["applied"] = True
                result["mock"] = mock
                result["persona"] = self.pipeline.persona.name
            return result
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def api_test_chat(self) -> dict:
        if not self.cfg.llm.api_key:
            return {"ok": False, "error": "未配置聊天 API 密钥——在设置页填入并保存后重试"}
        from chat.llm import LLMChatClient

        t0 = time.time()
        try:
            reply = await LLMChatClient(self.cfg).achat(
                [{"role": "user", "content": "只回复两个字：正常"}])
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        dt = time.time() - t0
        text = reply.strip()
        return {"ok": bool(text),
                "detail": f"{self.cfg.llm.model} · {dt:.1f}s · 「{text[:20]}」"}

    async def api_test_embed(self) -> dict:
        if (self.cfg.embedding.provider == "api"
                and not self.cfg.embedding.api.api_key):
            return {"ok": False, "error": "未配置嵌入 API 密钥——在设置页填入并保存后重试"}
        from embed.base import create_embedder

        embedder = create_embedder(self.cfg)
        t0 = time.time()
        try:
            vec = (await embedder.embed(["体检"]))[0]
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        dt = time.time() - t0
        return {"ok": True, "detail": f"{embedder.name} · dim={len(vec)} · {dt:.1f}s"}

    # ---------- 人格卡 ----------

    def api_personas(self) -> dict:
        active = str(Path(self._resolve(self.cfg.persona_file)))
        out = []
        for p in sorted((self.root / "personas").glob("*.yaml")):
            try:
                name = (load_persona(p) or {}).get("name") or p.stem
            except Exception:
                name = p.stem
            out.append({"file": f"personas/{p.name}", "name": name,
                        "is_active": Path(self._resolve(f"personas/{p.name}")) == Path(active),
                        "db": self._persona_db_path(f"personas/{p.name}")})
        return {"ok": True, "items": out,
                "current": str(self.editor_state.card_path),
                "per_persona_memory": self.cfg.per_persona_memory}

    def api_persona_select(self, path: str) -> dict:
        card = self._resolve(path)
        if not card.exists():
            return {"ok": False, "error": f"角色卡不存在：{card}"}
        try:
            data = load_persona(card)
            PersonaCard.model_validate(data)
        except Exception as e:
            return {"ok": False, "error": f"角色卡校验失败：{e}"}
        self.editor_state = EditorState(card)
        return {"ok": True, "path": str(card), "persona": data}

    # ---------- 多记忆宇宙（全部角色卡 + 串门客卡显式库） ----------

    def _persona_db_path(self, persona_file: str) -> str:
        """一张卡在当前配置下使用的记忆库路径（与 per_persona_memory 派生规则一致）。"""
        if self.cfg.per_persona_memory:
            db = Path(self.cfg.database)
            return str(db.parent / f"memory_{Path(persona_file).stem}.db")
        return str(Path(self.cfg.database))

    def _universe_defs(self) -> list[tuple[str, str]]:
        """[(标签, 记忆库路径)]：主卡 + personas/ 下全部角色卡 + 串门客卡显式库。

        未开启记忆隔离时所有卡派生到同一个库，去重后自然合并为一条。"""
        entries = [(f"{self.pipeline.persona.name}（当前主卡）",
                    str(Path(self.cfg.database)))]
        active_file = str(Path(self._resolve(self.cfg.persona_file)))
        for p in sorted((self.root / "personas").glob("*.yaml")):
            if Path(self._resolve(str(p))) == Path(active_file):
                continue   # 主卡已在上面
            try:
                name = (load_persona(p) or {}).get("name") or p.stem
            except Exception:
                name = p.stem
            entries.append((name, self._persona_db_path(f"personas/{p.name}")))
        for v in self.cfg.social.visits:
            path = str(Path(v.database or self._persona_db_path(v.persona_file)))
            try:
                name = (load_persona(self._resolve(v.persona_file)) or {}).get(
                    "name") or Path(v.persona_file).stem
            except Exception:
                name = Path(v.persona_file).stem
            entries.append((f"{name}（串门客卡）", path))
        seen: set[str] = set()
        out = []
        for label, path in entries:
            if path in seen:
                continue
            seen.add(path)
            out.append((label, path))
        return out

    async def api_universes(self) -> dict:
        from memory.store import MemoryStore

        items = []
        for label, path in self._universe_defs():
            exists = Path(path).exists()
            stats = None
            if exists:
                s = MemoryStore(path)
                await s.init(with_stickers=False)
                try:
                    st = await s.stats()
                    stats = {"messages": st.get("messages", 0),
                             "staging": st.get("stage", {}).get("staging", 0),
                             "ltm": st.get("stage", {}).get("ltm", 0)}
                finally:
                    await s.close()
            items.append({"label": label, "path": path, "exists": exists,
                          "active": path == str(Path(self.cfg.database)),
                          "stats": stats})
        stickers = None
        if self.pipeline.sticker_lib is not None:
            stickers = await self.pipeline.sticker_lib.stats()
        return {"ok": True, "items": items, "stickers": stickers}

    def _known_db_paths(self) -> set[str]:
        return {str(Path(p)) for _, p in self._universe_defs()}

    async def api_memories_of(self, path: str, stage: str, q: str,
                              limit: int) -> dict:
        """查看指定记忆宇宙（须为已知库路径）；默认当前主卡。"""
        from memory import decay
        from memory.store import MemoryStore

        if path:
            path = str(Path(path))
            if path not in self._known_db_paths():
                return {"ok": False, "error": "未知的记忆库路径"}
        gid = self.cfg.sim_group_id
        s = self.store
        transient = bool(path and path != str(Path(self.cfg.database)))
        if transient:
            s = MemoryStore(path)
            await s.init(with_stickers=False)
        try:
            rows = await s.list_memories(
                group_id=gid, stage=None if stage == "all" else stage,
                status=None, limit=limit)
        finally:
            if transient:
                await s.close()
        now = self.pipeline.clock.now()
        out = []
        for m in rows:
            if q and q not in m.content:
                continue
            ret = decay.retention((now - m.anchor_ts) / 86400.0, m.strength)
            out.append({"id": m.id, "stage": m.stage, "kind": m.kind,
                        "status": m.status, "content": m.content,
                        "subject": m.subject_user_id, "actor": m.actor_user_id,
                        "group_id": m.group_id,
                        "importance": m.importance, "emotion": m.emotion,
                        "retention": round(ret, 3), "strength": round(m.strength, 1),
                        "access": m.access_count, "recurrence": m.recurrence_count,
                        "created_at": m.created_at})
        return {"ok": True, "items": out, "now": now}

    async def api_memory_clear(self, path: str) -> dict:
        """清空一个记忆宇宙（先自动备份 JSON）。只接受已知库路径。"""
        import json as _json
        from datetime import datetime

        from memory.store import MemoryStore

        path = str(Path(path))
        if path not in self._known_db_paths():
            return {"ok": False, "error": "未知的记忆库路径"}
        if not Path(path).exists():
            return {"ok": False, "error": "记忆库不存在"}
        s = MemoryStore(path)
        await s.init(with_stickers=False)
        try:
            data = await s.export_all()
            n = sum(len(v) for k, v in data.items() if isinstance(v, list))
            backup = Path(path).parent / (
                f"backup_{Path(path).stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
            backup.write_text(_json.dumps(data, ensure_ascii=False, indent=1),
                              encoding="utf-8")
            counts = {}
            for table in ("messages", "memories", "embeddings", "memory_entities",
                          "fts_memories", "drift_reports"):
                cur = await s.db.execute(f"DELETE FROM {table}")
                counts[table] = cur.rowcount or 0
            await s.db.commit()
        finally:
            await s.close()
        return {"ok": True, "backup": str(backup), "backup_records": n,
                "cleared": counts}


def load_config_safe(path: Path, fallback):
    """读取磁盘上的最新配置；文件不存在或损坏时回退到当前内存配置。"""
    if not path.exists():
        return fallback
    try:
        from config import load_config

        return load_config(path)
    except Exception:
        return fallback


def make_handler(app: GUIApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # 静默访问日志
            pass

        def _json(self, obj: dict, code: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _page(self, path: Path) -> None:
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_payload(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return {"__bad_json__": True}

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            try:
                if url.path in ("/", "/index.html"):
                    return self._page(_PAGE)
                if url.path == "/editor":
                    return self._page(_EDITOR_PAGE)
                if url.path == "/api/status":
                    return self._json(app.bridge.run(app.api_status()))
                if url.path == "/api/stats":
                    return self._json({"ok": True,
                                       "stats": app.bridge.run(app.store.stats())})
                if url.path == "/api/config":
                    values = values_from_config(app.cfg)
                    return self._json({"ok": True, "values": values,
                                       "mode": "mock" if app.mock else "api",
                                       "config_path": str(app.cfg_path),
                                       "env_path": str(app.env_path),
                                       "exists": app.cfg_path.exists()})
                if url.path == "/api/history":
                    limit = int(qs.get("limit", ["200"])[0])
                    return self._json(app.bridge.run(app.api_history(limit)))
                if url.path == "/api/memories":
                    stage = qs.get("stage", ["all"])[0]
                    q = qs.get("q", [""])[0]
                    limit = int(qs.get("limit", ["300"])[0])
                    universe = qs.get("universe", [""])[0]
                    return self._json(app.bridge.run(
                        app.api_memories(stage, q, limit, universe)))
                if url.path == "/api/universes":
                    return self._json(app.bridge.run(app.api_universes()))
                if url.path == "/api/persona":
                    try:
                        return self._json({
                            "ok": True,
                            "path": str(app.editor_state.card_path),
                            "persona": load_persona(app.editor_state.card_path)})
                    except FileNotFoundError:
                        return self._json({"ok": False, "error":
                                           f"角色卡不存在：{app.editor_state.card_path}"}, 404)
                if url.path == "/api/personas":
                    return self._json(app.api_personas())
                return self._json({"ok": False, "error": "未知路径"}, 404)
            except Exception as e:  # 防止单个请求拖垮服务
                import traceback

                traceback.print_exc()
                return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)

        def do_POST(self) -> None:  # noqa: N802
            payload = self._read_payload()
            if payload.get("__bad_json__"):
                return self._json({"ok": False, "error": "请求体不是合法 JSON"}, 400)
            try:
                if self.path == "/api/chat":
                    name = str(payload.get("name") or "路人").strip()[:24] or "路人"
                    text = str(payload.get("text") or "").strip()
                    if not text:
                        return self._json({"ok": False, "error": "消息为空"}, 400)
                    return self._json({"ok": True, **app.bridge.run(
                        app.api_chat(name, text, bool(payload.get("force"))))})
                if self.path == "/api/warp":
                    from sim.console import parse_duration

                    secs = parse_duration(str(payload.get("duration") or "1d"))
                    return self._json(app.bridge.run(app.api_warp(secs)))
                if self.path == "/api/consolidate":
                    return self._json(app.bridge.run(app.api_consolidate()))
                if self.path == "/api/flush":
                    return self._json(app.bridge.run(app.api_flush()))
                if self.path == "/api/window_reset":
                    app.pipeline.reset_window(app.cfg.sim_group_id)
                    return self._json({"ok": True})
                if self.path == "/api/recall":
                    text = str(payload.get("text") or "").strip()
                    if not text:
                        return self._json({"ok": False, "error": "召回文本为空"}, 400)
                    return self._json(app.bridge.run(app.api_recall(text)))
                if self.path == "/api/visit":
                    index = payload.get("index")
                    return self._json(app.bridge.run(app.api_visit(
                        None if index is None else int(index))))
                if self.path == "/api/memory/clear":
                    return self._json(app.bridge.run(app.api_memory_clear(
                        str(payload.get("path") or ""))))
                if self.path == "/api/config":
                    return self._json(app.bridge.run(app.api_config_save(
                        payload.get("values") or {},
                        str(payload.get("mode") or "mock"),
                        bool(payload.get("apply")))))
                if self.path == "/api/config/test_chat":
                    return self._json(app.bridge.run(app.api_test_chat()))
                if self.path == "/api/config/test_embed":
                    return self._json(app.bridge.run(app.api_test_embed()))
                if self.path == "/api/persona":
                    try:
                        PersonaCard.model_validate(payload)   # 保存前校验
                        save_persona(app.editor_state, payload)
                        return self._json({"ok": True})
                    except Exception as e:
                        return self._json({"ok": False,
                                           "error": f"校验/保存失败：{e}"}, 400)
                if self.path == "/api/persona_select":
                    return self._json(app.api_persona_select(
                        str(payload.get("path") or "")))
                if self.path == "/api/preview":
                    try:
                        card = PersonaCard.model_validate(payload)
                        return self._json({"ok": True, "system_prompt":
                                           card.build_system_prompt("预览群", time.time())})
                    except Exception as e:
                        return self._json({"ok": False, "error": str(e)}, 400)
                if self.path == "/api/import_card":
                    card = None
                    try:
                        if payload.get("png_base64"):
                            card = extract_card_from_png(
                                base64.b64decode(payload["png_base64"]))
                        elif isinstance(payload.get("json"), dict):
                            card = payload["json"]
                    except Exception:
                        card = None
                    if not card:
                        return self._json({"ok": False, "error":
                                           "PNG 中未找到内嵌角色卡（缺 chara/ccv3 块），或 JSON 无效"}, 400)
                    return self._json({"ok": True, "persona": st_card_to_persona(card)})
                return self._json({"ok": False, "error": "未知路径"}, 404)
            except Exception as e:
                import traceback

                traceback.print_exc()
                return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)

    return Handler


def run_gui(root: Path, cfg, mock: bool, config_path: str = "config.yaml",
            port: int = 8765) -> None:
    app = GUIApp(root=root, cfg=cfg, mock=mock, config_path=config_path, port=port)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    app.port = server.server_address[1]
    url = f"http://127.0.0.1:{app.port}"
    mode = "MOCK 离线" if mock else "API"
    print(f"Mnemochat 图形控制台已启动：{url}")
    print(f"  模式 {mode} · 角色 {app.pipeline.persona.name} · 库 {cfg.database}")
    print(f"  配置 {app.cfg_path}（在「设置」页修改，保存即生成/更新）")
    print("  Ctrl+C 退出")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()
        print("控制台已关闭，记忆已保存。")
