"""记忆管理台（--inspect）：查看/审计记忆库，不产生对话。

命令：
  list [staging|ltm|all] [n]    列出记忆（默认 ltm，n 默认 50）
  show <id>                     查看单条记忆详情（含 R/S/召回轨迹/溯源）
  recall <文本>                 召回预演（不强化）
  consolidate                   手动巩固
  stats                         统计
  messages [n]                  最近消息日志
  q                             退出
"""

from __future__ import annotations

import asyncio
import logging

from rich.console import Console

from chat.llm import LLMChatClient
from chat.mock import MockChatClient
from chat.pipeline import BotPipeline
from config import BotConfig
from embed.base import create_embedder
from embed.mock import HashEmbedder
from memory.models import MessageRecord
from memory.store import MemoryStore
from persona.card import PersonaCard
from sim.tables import detail_panel, memories_table, recall_table, stats_table
from util.clock import Clock
from util.text import fmt_relative, fmt_ts, warmup


async def run_inspect(cfg: BotConfig, mock: bool = False) -> None:
    logging.basicConfig(level=logging.WARNING)
    warmup()

    clock = Clock()
    store = MemoryStore(cfg.database)
    await store.init()
    chat = MockChatClient() if mock else LLMChatClient(cfg)
    embedder = HashEmbedder() if mock else create_embedder(cfg)
    persona = PersonaCard.load_yaml(cfg.persona_file)
    pipe = BotPipeline(store, chat, embedder, persona, cfg, clock, cfg.sim_group_id)

    console = Console()
    console.print(f"\n[bold]记忆管理台[/] · 库 {cfg.database} · 嵌入 {embedder.name}")
    console.print("命令：list / show <id> / recall <文本> / consolidate / stats / messages / q\n")

    try:
        while True:
            try:
                line = (await asyncio.to_thread(console.input, "[bold cyan]inspect>[/] ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            cmd, *args = line.split()
            if cmd in ("q", "quit", "exit"):
                break
            if cmd == "help":
                console.print(__doc__)
            elif cmd == "list":
                stage = args[0] if args and args[0] in ("staging", "ltm", "all") else "ltm"
                try:
                    n = int(args[1]) if len(args) > 1 else 50
                except ValueError:
                    n = 50
                rows = await store.list_memories(
                    stage=None if stage == "all" else stage, status=None, limit=n
                )
                console.print(memories_table(rows, clock.now(), title=f"记忆·{stage}"))
            elif cmd == "show":
                try:
                    mid = int(args[0])
                except (IndexError, ValueError):
                    console.print("[red]用法：show <id>[/]")
                    continue
                mem = await store.get_memory(mid)
                console.print(detail_panel(mem, clock.now()) if mem else "[red]不存在[/]")
            elif cmd == "recall":
                text = line[len("recall"):].strip()
                if not text:
                    console.print("[red]用法：recall <文本>[/]")
                    continue
                probe = MessageRecord(group_id=cfg.sim_group_id, user_id="探针",
                                      user_name="探针", content=text, ts=clock.now())
                rec = await pipe.recaller.recall(cfg.sim_group_id, persona, [probe],
                                                  reinforce=False)
                console.print(recall_table(rec, clock.now()))
            elif cmd == "consolidate":
                c = await pipe.consolidate_now()
                console.print(f"晋升 {c.promoted} · 取代 {c.superseded} · "
                              f"归纳 {c.semantic_created}（归档 {c.archived}）")
            elif cmd == "stats":
                console.print(stats_table(await store.stats()))
            elif cmd == "messages":
                try:
                    n = int(args[0]) if args else 20
                except ValueError:
                    n = 20
                msgs = await store.recent_messages(cfg.sim_group_id, limit=n)
                for msg in reversed(msgs):
                    console.print(f"[dim]{fmt_ts(msg.ts)} ({fmt_relative(msg.ts, clock.now())})[/] "
                                  f"[bold]{msg.user_name}[/]: {msg.content}")
            else:
                console.print("[dim]未知命令，help 查看用法[/]")
    finally:
        await store.close()
        console.print("再见。")
