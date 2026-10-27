"""入口。

  python main.py --gui [--mock]  图形控制台：聊天模拟器+记忆+人格+配置（本地网页）
  python main.py --init          生成 config.yaml / .env 模板
  python main.py --sim [--mock]  控制台群聊模拟器（--mock 完全离线）
  python main.py --onebot        QQ 群聊模式（OneBot v11 / NapCat 等）
  python main.py --telegram      Telegram 群聊模式（长轮询）
  python main.py --check [--mock]  启动体检：角色卡/数据库/分词/API 连通性逐项验证
  python main.py --inspect       记忆管理台
  python main.py --edit-persona  人格卡修改器（本地网页：滑条调参/酒馆卡导入/热重载）
  python main.py --reembed       切换嵌入模型后补齐存量向量
  python main.py --export [文件] 导出记忆库备份 JSON（不含嵌入）
  python main.py --import 文件   从备份恢复（按 id 合并，嵌入用 --reembed 重建）
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

from util.appdir import base_dir, is_frozen, resource_dir

ROOT = base_dir()   # 源码运行=仓库根；打包成 exe 后=exe 所在目录（用户数据根）


def _seed_user_files() -> None:
    """打包模式下首次运行：把随包附带的 persona 模板与示例配置播种到 exe 目录。"""
    if not is_frozen():
        return
    res = resource_dir()
    for name in ("personas", "docs"):
        src, dst = res / name, ROOT / name
        if src.is_dir() and not dst.exists():
            shutil.copytree(src, dst)
            print(f"[初始化] 已生成 {dst}")
    for name in ("config.example.yaml", ".env.example"):
        src, dst = res / name, ROOT / name
        if src.exists() and not dst.exists():
            shutil.copy(src, dst)
            print(f"[初始化] 已生成 {dst}")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bot", description="群聊长期记忆机器人")
    p.add_argument("--config", default="config.yaml", help="配置文件路径")
    p.add_argument("--gui", action="store_true",
                   help="图形控制台：聊天模拟器/记忆管理/人格卡/配置生成，本地网页")
    p.add_argument("--sim", action="store_true", help="控制台群聊模拟器（默认动作）")
    p.add_argument("--mock", action="store_true", help="离线模式：不调用任何外部 API")
    p.add_argument("--onebot", action="store_true",
                   help="QQ 群聊模式：作为 OneBot v11 正向 WS 客户端接入（NapCat/LLOneBot/Lagrange）")
    p.add_argument("--telegram", action="store_true",
                   help="Telegram 群聊模式：getUpdates 长轮询（无需公网）")
    p.add_argument("--check", action="store_true",
                   help="启动体检：角色卡/数据库/分词/LLM/嵌入逐项验证（--mock 跳过 API 检查）")
    p.add_argument("--inspect", action="store_true", help="记忆管理台")
    p.add_argument("--reembed", action="store_true", help="为当前嵌入模型补齐存量向量")
    p.add_argument("--export", nargs="?", const="auto", metavar="文件",
                   help="导出记忆库备份 JSON（缺省为 data/export_日期.json）")
    p.add_argument("--import", dest="import_path", metavar="文件",
                   help="从备份 JSON 恢复记忆库")
    p.add_argument("--edit-persona", dest="edit_persona", nargs="?",
                   const="config", metavar="卡文件",
                   help="启动人格卡修改器（本地网页）；缺省编辑 config 指向的角色卡")
    p.add_argument("--port", type=int, default=8765,
                   help="人格卡修改器端口（默认 8765）")
    p.add_argument("--init", action="store_true", help="生成 config.yaml 与 .env 模板")
    return p


def _cmd_init() -> None:
    cfg_dst, env_dst = ROOT / "config.yaml", ROOT / ".env"
    if cfg_dst.exists() or env_dst.exists():
        print("config.yaml / .env 已存在，未覆盖。")
        return
    shutil.copy(ROOT / "config.example.yaml", cfg_dst)
    shutil.copy(ROOT / ".env.example", env_dst)
    print("已生成 config.yaml 与 .env：")
    print("  1. 编辑 .env 填入 API 密钥（或直接把密钥写进 config.yaml）")
    print("  2. python main.py --sim --mock 可先离线体验全流程")


def _load(args) -> "BotConfig":
    from config import load_config

    try:
        return load_config(ROOT / args.config if args.config == "config.yaml" else args.config)
    except FileNotFoundError:
        if args.mock and args.config == "config.yaml":
            print("[提示] 未找到 config.yaml，--mock 模式改用默认示例配置。")
            return load_config(ROOT / "config.example.yaml")
        print("未找到 config.yaml。先运行: python main.py --init")
        raise SystemExit(1)


async def _cmd_reembed(cfg) -> None:
    from embed.base import create_embedder
    from memory.store import MemoryStore

    store = MemoryStore(cfg.database)
    await store.init()
    embedder = create_embedder(cfg)
    ids = await store.memories_without_embedding(embedder.name)
    print(f"模型 {embedder.name} 需补齐 {len(ids)} 条记忆的向量")
    batch = 64
    done = 0
    for i in range(0, len(ids), batch):
        chunk = ids[i : i + batch]
        rows = await store.get_memories_by_ids(chunk)
        texts = [rows[mid].content for mid in chunk]
        vecs = await embedder.embed(texts)
        for mid, vec in zip(chunk, vecs):
            await store.save_embedding(mid, embedder.name, vec)
        done += len(chunk)
        print(f"  {done}/{len(ids)}")
    await store.close()
    print("完成。")


def main() -> None:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台中文兜底
    if is_frozen():
        # 双击 exe 即启动图形控制台（显式传参时仍可 --sim / --onebot 等）
        if len(sys.argv) <= 1:
            sys.argv += ["--gui"]
        _seed_user_files()
        import os

        os.chdir(ROOT)   # database 等相对路径统一落在 exe 目录，与启动方式无关
    args = _build_parser().parse_args()

    if args.init:
        _cmd_init()
        return
    if args.edit_persona is not None:
        path = args.edit_persona
        if path == "config":
            try:
                cfg = _load(args)
                path = cfg.persona_file
            except SystemExit:
                path = "personas/default.yaml"
                print("[提示] 未找到 config.yaml，默认编辑 personas/default.yaml")
        card_path = Path(path)
        if not card_path.is_absolute():
            card_path = ROOT / card_path
        from editor.server import run_editor

        run_editor(card_path, args.port)
        return
    if args.gui:
        from config import load_config

        cfg_path = ROOT / args.config if args.config == "config.yaml" else Path(args.config)
        if cfg_path.exists():
            cfg = load_config(cfg_path)
        else:
            print("[提示] 尚未生成 config.yaml——先用示例配置启动，"
                  "到「设置」页保存即可生成。")
            cfg = load_config(ROOT / "config.example.yaml")
        mock = args.mock
        if not mock and not cfg.llm.api_key:
            print("[提示] 未配置 API 密钥——先以离线模式运行；"
                  "在「设置」页填好密钥后点「保存并应用到运行中机器人」。")
            mock = True
        from gui.server import run_gui

        run_gui(ROOT, cfg, mock=mock, config_path=args.config, port=args.port)
        return
    cfg = _load(args)

    if args.import_path:
        asyncio.run(_cmd_import(cfg, args.import_path))
    elif args.export is not None:
        asyncio.run(_cmd_export(cfg, args.export))
    elif args.reembed:
        asyncio.run(_cmd_reembed(cfg))
    elif args.check:
        sys.exit(asyncio.run(_cmd_check(cfg, mock=args.mock)))
    elif args.onebot:
        asyncio.run(_run_adapter(cfg, mock=args.mock, kind="onebot"))
    elif args.telegram:
        asyncio.run(_run_adapter(cfg, mock=args.mock, kind="telegram"))
    elif args.inspect:
        from sim.inspect import run_inspect

        asyncio.run(run_inspect(cfg, mock=args.mock))
    else:
        from sim.console import run_sim

        asyncio.run(run_sim(cfg, mock=args.mock, config_path=args.config))


async def run_checks(cfg, mock: bool) -> list[dict]:
    """启动体检的全部检查项，返回结构化结果（CLI 与图形控制台共用）。

    每项 {name, ok, detail, warn}：ok=False 为致命问题；
    warn=True 为提示性信息（不影响启动，但值得知道）。"""
    import time as _time

    from util.text import seg

    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "", warn: bool = False) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "warn": warn})

    # 1. 角色卡
    try:
        from persona.card import PersonaCard

        persona = PersonaCard.load_yaml(cfg.persona_file)
        add("角色卡", True,
            f"{persona.name} · 性格参数 {len(persona.parameters)} 项 · "
            f"黑名单 {len(persona.ai_flavor_blocklist)} 条 · 兴趣词 {len(persona.interests)} 个")
    except Exception as e:
        add("角色卡", False, str(e))

    # 2. 数据库（含 FTS5 与完整性）
    try:
        from memory.store import MemoryStore

        store = MemoryStore(cfg.database)
        await store.init()
        try:
            stats = await store.stats()
            total_mem = sum(stats["stage"].values())
            qc_rows = await store.db.execute_fetchall("PRAGMA quick_check")
            qc = qc_rows[0][0] if qc_rows else "unknown"
            detail = (f"{cfg.database} · 记忆 {total_mem} 条 · 消息 {stats['messages']} 条 · "
                      f"完整性 {qc}")
            if cfg.sticker.enabled:
                from stickers.library import StickerLibrary

                st = await StickerLibrary(store, cfg).stats()
                detail += f" · 表情包 {st['total']} 张（已打标 {st['tagged']}）"
            add("数据库（含 FTS5 与完整性）", qc == "ok", detail)
        finally:
            await store.close()
    except Exception as e:
        add("数据库（含 FTS5 与完整性）", False, str(e))

    # 3. 数据目录可写
    try:
        from pathlib import Path

        data_dir = Path(cfg.database).parent
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("数据目录可写", True, str(data_dir))
    except Exception as e:
        add("数据目录可写", False, f"{cfg.database} 所在目录不可写：{e}")

    # 4. 中文分词
    try:
        tokens = seg("花生过敏体检测试")
        add("中文分词（jieba）", bool(tokens), f"{tokens}")
    except Exception as e:
        add("中文分词（jieba）", False, str(e))

    # 5. 记忆隔离与守口如瓶提示（提示性）
    if cfg.per_persona_memory:
        from pathlib import Path

        db = Path(cfg.database)
        derived = db.parent / f"memory_{Path(cfg.persona_file).stem}.db"
        add("记忆隔离（每卡独立库）", True,
            f"当前卡使用 {derived}；表情包库 "
            f"{cfg.sticker.database or 'data/stickers_shared.db'} 共享", warn=True)
    secrecy = cfg.private_chat.effective_secrecy if cfg.private_chat.enabled else 2
    if secrecy < 2:
        add("守口如瓶", True,
            f"保密等级 {secrecy}：私聊内容可能出现在群聊里", warn=True)

    # 6. 串门关系
    if cfg.social.visits:
        from pathlib import Path

        for i, v in enumerate(cfg.social.visits):
            try:
                from persona.card import PersonaCard

                gp = PersonaCard.load_yaml(v.persona_file)
                add(f"串门关系 #{i + 1}（{gp.name}）", True,
                    f"每 {v.every_hours:g} 小时一次 · 记忆库 {v.database or '与主卡共用'}",
                    warn=True)
            except Exception as e:
                add(f"串门关系 #{i + 1}", False,
                    f"{v.persona_file} 加载失败：{e}")
    else:
        add("串门关系", True, "未配置（到 GUI「设置 · 社交与串门」可添加）", warn=True)

    # 7. NapCat 正向 WS 端口连通（QQ 模式最常见的翻车点）
    try:
        from urllib.parse import urlparse

        u = urlparse(cfg.onebot.ws_url)
        host, port = u.hostname or "127.0.0.1", u.port or 80

        async def _probe():
            reader, writer = await asyncio.open_connection(host, port)
            writer.close()
            await writer.wait_closed()

        await asyncio.wait_for(_probe(), timeout=3)
        add("NapCat WS 端口", True, f"{host}:{port} 可连通")
    except Exception as e:
        add("NapCat WS 端口", True,
            f"{cfg.onebot.ws_url} 不可达（{type(e).__name__}）——"
            "仅影响 QQ 模式：确认 NapCat 已启动并在其网络配置里开启了正向 WebSocket",
            warn=True)

    # 8. 聊天模型 API
    if mock:
        add("聊天模型 API", True, "已跳过（离线模式）", warn=True)
    elif not cfg.llm.api_key:
        add("聊天模型 API", False,
            "未配置 api_key（.env 的 DEEPSEEK_API_KEY 或 config.yaml llm.api_key）")
    else:
        try:
            from chat.llm import LLMChatClient

            client = LLMChatClient(cfg)
            t0 = _time.time()
            reply = await client.achat(
                [{"role": "user", "content": "只回复两个字：正常"}])
            add("聊天模型 API", bool(reply.strip()),
                f"{cfg.llm.model} · {_time.time() - t0:.1f}s · 回复「{reply.strip()[:20]}」")
        except Exception as e:
            add("聊天模型 API", False, f"{type(e).__name__}: {e}")

    # 9. 嵌入 API
    if mock:
        add("嵌入 API", True, "已跳过（离线模式）", warn=True)
    else:
        try:
            if cfg.embedding.provider == "api" and not cfg.embedding.api.api_key:
                add("嵌入 API", False,
                    "未配置 api_key（.env 的 SILICONFLOW_API_KEY 或 config.yaml）")
            else:
                from embed.base import create_embedder

                embedder = create_embedder(cfg)
                vec = (await embedder.embed(["体检"]))[0]
                add("嵌入 API", True, f"{embedder.name} · dim={len(vec)}")
        except Exception as e:
            add("嵌入 API", False, f"{type(e).__name__}: {e}")

    # 10. Telegram bot token（配置了才查）
    if cfg.telegram.bot_token:
        if mock:
            add("Telegram bot token", True, "已跳过（离线模式）", warn=True)
        else:
            try:
                import httpx

                async with httpx.AsyncClient(timeout=10) as hc:
                    resp = await hc.get(
                        f"https://api.telegram.org/bot{cfg.telegram.bot_token}/getMe")
                data = resp.json()
                if data.get("ok"):
                    add("Telegram bot token", True,
                        f"@{data['result'].get('username', '?')} 验证通过")
                else:
                    add("Telegram bot token", False,
                        f"getMe 失败：{data.get('description', resp.status_code)}")
            except Exception as e:
                add("Telegram bot token", False, f"{type(e).__name__}: {e}")

    return checks


async def _cmd_check(cfg, mock: bool) -> int:
    """启动体检：返回致命问题数（0 = 一切就绪）。"""
    checks = await run_checks(cfg, mock)
    for c in checks:
        mark = "✓" if c["ok"] else ("⚠" if c["warn"] else "✗")
        suffix = f" —— {c['detail']}" if c["detail"] else ""
        print(f"  [{mark}] {c['name']}{suffix}")
    fatal = sum(1 for c in checks if not c["ok"])
    print(f"结果：{'全部通过，可以启动 ✅' if fatal == 0 else f'{fatal} 项未通过 ❌'}")
    return fatal




async def _open_stores(cfg) -> tuple["MemoryStore", "MemoryStore | None"]:
    """打开主记忆库 + 可选的独立表情包共享库。
    sticker.database 留空或与主库相同 → 表情包随主库（返回 sticker_store=None）。"""
    from memory.store import MemoryStore

    same = (not cfg.sticker.database
            or Path(cfg.sticker.database) == Path(cfg.database))
    store = MemoryStore(cfg.database)
    await store.init(with_stickers=same)
    sticker_store = None
    if not same:
        sticker_store = MemoryStore(cfg.sticker.database)
        await sticker_store.init(with_stickers=True)
    return store, sticker_store


async def close_stores(pipeline, store) -> None:
    """关闭主库与（若存在的）独立表情包库——所有入口的统一退出路径。"""
    await store.close()
    sticker_store = getattr(pipeline, "sticker_store", None)
    if sticker_store is not None and sticker_store is not store:
        await sticker_store.close()


async def _build_pipeline(cfg, mock: bool):
    from chat.llm import LLMChatClient
    from chat.mock import MockChatClient
    from chat.pipeline import BotPipeline
    from embed.base import create_embedder
    from embed.mock import HashEmbedder
    from persona.card import PersonaCard
    from stickers.library import StickerLibrary
    from stickers.tagger import create_vision
    from util.clock import Clock

    clock = Clock()
    store, sticker_store = await _open_stores(cfg)
    chat = MockChatClient() if mock else LLMChatClient(cfg)
    embedder = HashEmbedder() if mock else create_embedder(cfg)
    persona = PersonaCard.load_yaml(cfg.persona_file)
    sticker_lib = (StickerLibrary(sticker_store or store, cfg)
                   if cfg.sticker.enabled else None)
    vision = create_vision(cfg, mock) if sticker_lib else None
    pipeline = BotPipeline(store, chat, embedder, persona, cfg, clock,
                           persona_file=cfg.persona_file,
                           sticker_lib=sticker_lib, vision=vision, mock=mock)
    pipeline.sticker_store = sticker_store   # 供 close_stores 关闭（独立库时非空）
    return pipeline, store


async def _run_adapter(cfg, mock: bool, kind: str) -> None:
    import logging

    from util.text import warmup

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    warmup()
    pipeline, store = await _build_pipeline(cfg, mock)
    if kind == "onebot":
        from adapter.onebot import OneBotV11Adapter

        adapter = OneBotV11Adapter(pipeline, cfg, pipeline.clock)
        endpoint = cfg.onebot.ws_url
    else:
        from adapter.telegram import TelegramAdapter

        adapter = TelegramAdapter(pipeline, cfg, pipeline.clock)
        endpoint = "Telegram long-polling"
    print(f"{'QQ' if kind == 'onebot' else 'Telegram'} 模式启动：{endpoint} · "
          f"角色 {pipeline.persona.name} · {'MOCK' if mock else 'API'} · Ctrl+C 退出")
    try:
        await adapter.run()
    except KeyboardInterrupt:
        pass
    finally:
        await pipeline.flush_ingest()
        await close_stores(pipeline, store)
        print("已退出，记忆已保存。")


async def _cmd_export(cfg, path: str) -> None:
    import json
    from datetime import datetime

    from memory.store import MemoryStore

    if path == "auto":
        path = f"data/export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    store = MemoryStore(cfg.database)
    await store.init()
    data = await store.export_all()
    data["exported_at"] = datetime.now().isoformat()
    data["version"] = 1
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    await store.close()
    print(f"已导出 {sum(len(v) for k, v in data.items() if isinstance(v, list))} 条记录 → {path}"
          f"（不含嵌入，恢复后用 --reembed 重建）")


async def _cmd_import(cfg, path: str) -> None:
    import json

    from memory.store import MemoryStore

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    store = MemoryStore(cfg.database)
    await store.init()
    counts = await store.import_all(data)
    await store.close()
    print(f"已导入：{counts}（嵌入需用 --reembed 重建）")


if __name__ == "__main__":
    main()
