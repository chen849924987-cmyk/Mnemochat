"""入口。

  python main.py --init          生成 config.yaml / .env 模板
  python main.py --sim [--mock]  控制台群聊模拟器（--mock 完全离线）
  python main.py --onebot        QQ 群聊模式（OneBot v11 / NapCat 等）
  python main.py --telegram      Telegram 群聊模式（长轮询）
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
    p.add_argument("--sim", action="store_true", help="控制台群聊模拟器（默认动作）")
    p.add_argument("--mock", action="store_true", help="离线模式：不调用任何外部 API")
    p.add_argument("--onebot", action="store_true",
                   help="QQ 群聊模式：作为 OneBot v11 正向 WS 客户端接入（NapCat/LLOneBot/Lagrange）")
    p.add_argument("--telegram", action="store_true",
                   help="Telegram 群聊模式：getUpdates 长轮询（无需公网）")
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
    cfg = _load(args)

    if args.import_path:
        asyncio.run(_cmd_import(cfg, args.import_path))
    elif args.export is not None:
        asyncio.run(_cmd_export(cfg, args.export))
    elif args.reembed:
        asyncio.run(_cmd_reembed(cfg))
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


async def _build_pipeline(cfg, mock: bool):
    from chat.llm import LLMChatClient
    from chat.mock import MockChatClient
    from chat.pipeline import BotPipeline
    from embed.base import create_embedder
    from embed.mock import HashEmbedder
    from memory.store import MemoryStore
    from persona.card import PersonaCard
    from util.clock import Clock

    clock = Clock()
    store = MemoryStore(cfg.database)
    await store.init()
    chat = MockChatClient() if mock else LLMChatClient(cfg)
    embedder = HashEmbedder() if mock else create_embedder(cfg)
    persona = PersonaCard.load_yaml(cfg.persona_file)
    pipeline = BotPipeline(store, chat, embedder, persona, cfg, clock,
                           persona_file=cfg.persona_file)
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
