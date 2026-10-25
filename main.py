"""入口。

  python main.py --init          生成 config.yaml / .env 模板
  python main.py --sim [--mock]  控制台群聊模拟器（--mock 完全离线）
  python main.py --onebot        QQ 群聊模式（OneBot v11 / NapCat 等）
  python main.py --inspect       记忆管理台
  python main.py --reembed       切换嵌入模型后补齐存量向量
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
    p.add_argument("--inspect", action="store_true", help="记忆管理台")
    p.add_argument("--reembed", action="store_true", help="为当前嵌入模型补齐存量向量")
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
    cfg = _load(args)

    if args.reembed:
        asyncio.run(_cmd_reembed(cfg))
    elif args.onebot:
        asyncio.run(_cmd_onebot(cfg, mock=args.mock))
    elif args.inspect:
        from sim.inspect import run_inspect

        asyncio.run(run_inspect(cfg, mock=args.mock))
    else:
        from sim.console import run_sim

        asyncio.run(run_sim(cfg, mock=args.mock, config_path=args.config))


async def _cmd_onebot(cfg, mock: bool) -> None:
    import logging

    from adapter.onebot import OneBotV11Adapter
    from chat.llm import LLMChatClient
    from chat.mock import MockChatClient
    from chat.pipeline import BotPipeline
    from embed.base import create_embedder
    from embed.mock import HashEmbedder
    from memory.store import MemoryStore
    from persona.card import PersonaCard
    from util.clock import Clock
    from util.text import warmup

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    warmup()
    clock = Clock()
    store = MemoryStore(cfg.database)
    await store.init()
    chat = MockChatClient() if mock else LLMChatClient(cfg)
    embedder = HashEmbedder() if mock else create_embedder(cfg)
    persona = PersonaCard.load_yaml(cfg.persona_file)
    pipeline = BotPipeline(store, chat, embedder, persona, cfg, clock)
    adapter = OneBotV11Adapter(pipeline, cfg, clock)
    print(f"QQ 模式启动：{cfg.onebot.ws_url} · 角色 {persona.name} · "
          f"{'MOCK' if mock else 'API'} · Ctrl+C 退出")
    try:
        await adapter.run()
    except KeyboardInterrupt:
        pass
    finally:
        await pipeline.flush_ingest()
        await store.close()
        print("已退出，记忆已保存。")


if __name__ == "__main__":
    main()
