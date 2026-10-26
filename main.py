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

ROOT = Path(__file__).resolve().parent


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


async def _cmd_check(cfg, mock: bool) -> int:
    """启动体检：返回失败项数（0 = 一切就绪）。"""
    import time as _time

    from chat.llm import LLMChatClient
    from embed.base import create_embedder
    from memory.store import MemoryStore
    from persona.card import PersonaCard
    from util.text import seg

    failures = 0

    def report(name: str, passed: bool, detail: str = "") -> None:
        nonlocal failures
        mark = "✓" if passed else "✗"
        suffix = f" —— {detail}" if detail else ""
        print(f"  [{mark}] {name}{suffix}")
        if not passed:
            failures += 1

    print(f"Mnemochat 启动体检{'' if mock else '（含 API 连通性检查）'}")

    try:
        persona = PersonaCard.load_yaml(cfg.persona_file)
        report("角色卡", True,
               f"{persona.name} · 性格参数 {len(persona.parameters)} 项 · "
               f"黑名单 {len(persona.ai_flavor_blocklist)} 条")
    except Exception as e:
        report("角色卡", False, str(e))

    try:
        store = MemoryStore(cfg.database)
        await store.init()
        stats = await store.stats()
        total_mem = sum(stats["stage"].values())
        detail = (f"{cfg.database} · 记忆 {total_mem} 条 · 消息 {stats['messages']} 条 · "
                  f"嵌入模型 {list(stats['embeddings']) or '无'}")
        if cfg.sticker.enabled:
            from stickers.library import StickerLibrary

            s = await StickerLibrary(store, cfg).stats()
            detail += f" · 表情包 {s['total']} 张（已打标 {s['tagged']}）"
        report("数据库（含 FTS5）", True, detail)
        await store.close()
    except Exception as e:
        report("数据库（含 FTS5）", False, str(e))

    try:
        tokens = seg("花生过敏体检测试")
        report("中文分词（jieba）", bool(tokens), f"{tokens}")
    except Exception as e:
        report("中文分词（jieba）", False, str(e))

    if not mock:
        if not cfg.llm.api_key:
            report("聊天模型 API", False,
                   "未配置 api_key（.env 的 DEEPSEEK_API_KEY 或 config.yaml llm.api_key）")
        else:
            try:
                t0 = _time.time()
                client = LLMChatClient(cfg)
                reply = await client.achat(
                    [{"role": "user", "content": "只回复两个字：正常"}])
                report("聊天模型 API", bool(reply.strip()),
                       f"{cfg.llm.model} · {_time.time() - t0:.1f}s · 回复「{reply.strip()[:20]}」")
            except Exception as e:
                report("聊天模型 API", False, f"{type(e).__name__}: {e}")

        try:
            if cfg.embedding.provider == "api" and not cfg.embedding.api.api_key:
                report("嵌入 API", False,
                       "未配置 api_key（.env 的 SILICONFLOW_API_KEY 或 config.yaml）")
            else:
                embedder = create_embedder(cfg)
                vec = (await embedder.embed(["体检"]))[0]
                report(f"嵌入（{cfg.embedding.provider}）", True,
                       f"{embedder.name} · dim={len(vec)}")
        except Exception as e:
            report("嵌入", False, f"{type(e).__name__}: {e}")

    print(f"结果：{'全部通过，可以启动 ✅' if failures == 0 else f'{failures} 项未通过 ❌'}")
    return failures


async def _build_pipeline(cfg, mock: bool):
    from chat.llm import LLMChatClient
    from chat.mock import MockChatClient
    from chat.pipeline import BotPipeline
    from embed.base import create_embedder
    from embed.mock import HashEmbedder
    from memory.store import MemoryStore
    from persona.card import PersonaCard
    from stickers.library import StickerLibrary
    from stickers.tagger import create_vision
    from util.clock import Clock

    clock = Clock()
    store = MemoryStore(cfg.database)
    await store.init()
    chat = MockChatClient() if mock else LLMChatClient(cfg)
    embedder = HashEmbedder() if mock else create_embedder(cfg)
    persona = PersonaCard.load_yaml(cfg.persona_file)
    sticker_lib = StickerLibrary(store, cfg) if cfg.sticker.enabled else None
    vision = create_vision(cfg, mock) if sticker_lib else None
    pipeline = BotPipeline(store, chat, embedder, persona, cfg, clock,
                           persona_file=cfg.persona_file,
                           sticker_lib=sticker_lib, vision=vision)
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
        await store.close()
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
