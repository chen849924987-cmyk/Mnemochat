"""控制台群聊模拟器：无需任何聊天平台即可驱动完整记忆管线。

用法：
  python main.py --sim            # 真实 API
  python main.py --sim --mock     # 完全离线（规则模拟，无 API 费用）

命令：
  名字: 消息          以该成员身份发言；机器人按行为策略决定是否回复
  /warp 7d|12h|30m    时间快进（触发遗忘衰减与巩固）
  /consolidate        手动触发巩固（晋升/矛盾取代/睡眠归纳）
  /ingest             立即处理未入库的消息缓冲
  /memories [stage]   查看记忆库（stage: staging/ltm/all，默认 ltm）
  /recall <文本>      召回预演：看会想起什么、各因子得分
  /window reset       清空对话窗口（此后只能靠长期记忆回答）
  /stats              统计
  /help               帮助
  /quit               退出
"""

from __future__ import annotations

import asyncio
import logging
import re

from rich.console import Console

from chat.llm import LLMChatClient
from chat.mock import MockChatClient
from chat.pipeline import BotPipeline
from config import BotConfig
from embed.base import create_embedder
from embed.mock import HashEmbedder
from persona.card import PersonaCard
from sim.tables import memories_table, recall_table, stats_table
from stickers.library import StickerLibrary
from stickers.tagger import create_vision
from util.clock import Clock
from util.text import fmt_ts, warmup

log = logging.getLogger(__name__)

_MSG_RE = re.compile(r"^(\S{1,24}):\s*(.+)$", re.S)
_DUR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([smhdw])", re.IGNORECASE)
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(s: str) -> float:
    total = 0.0
    for num, unit in _DUR_RE.findall(s):
        total += float(num) * _UNIT[unit.lower()]
    if total <= 0:
        raise ValueError(f"无法解析时长：{s!r}（示例：7d / 12h / 30m）")
    return total


async def run_sim(cfg: BotConfig, mock: bool = False, config_path: str = "config.yaml") -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    warmup()

    clock = Clock()
    from main import _open_stores, close_stores

    store, sticker_store = await _open_stores(cfg)
    chat = MockChatClient() if mock else LLMChatClient(cfg)
    embedder = HashEmbedder() if mock else create_embedder(cfg)
    persona = PersonaCard.load_yaml(cfg.persona_file)
    pipe = BotPipeline(store, chat, embedder, persona, cfg, clock,
                       persona_file=cfg.persona_file,
                       sticker_lib=(StickerLibrary(sticker_store or store, cfg)
                                    if cfg.sticker.enabled else None),
                       vision=(create_vision(cfg, mock)
                               if cfg.sticker.enabled else None))
    pipe.sticker_store = sticker_store

    console = Console()
    mode = "[yellow]MOCK 离线[/]" if mock else "[green]API[/]"
    console.print(f"\n[bold]群聊记忆机器人[/] · {mode} · 群 [cyan]{cfg.sim_group_id}[/] "
                  f"· 角色 [magenta]{persona.name}[/] · 嵌入 {embedder.name} · 库 {cfg.database}")
    console.print("输入 [bold]名字: 消息[/] 发言，[bold]/help[/] 查看命令。")
    if persona.greeting:
        console.print(f"[magenta]{persona.name}[/]: {persona.greeting}")
    console.print("")

    shown_mem_ids: set[int] = set()

    def report_ingest() -> None:
        res = pipe.last_ingest_result
        if not res:
            return
        for mid, content in res.new_memories:
            if mid not in shown_mem_ids:
                shown_mem_ids.add(mid)
                console.print(f"  [dim]📖 记住了（staging）：{content}[/]")
        for content in res.reinforced_contents:
            console.print(f"  [dim]🔁 复现强化：{content}[/]")

    try:
        while True:
            try:
                line = (await asyncio.to_thread(console.input, "[bold cyan]群聊>[/] ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue

            if line in ("/quit", "/exit", "/q"):
                break
            if line == "/help":
                console.print(__doc__)
                continue
            if line.startswith("/warp"):
                try:
                    secs = parse_duration(line.split(maxsplit=1)[1])
                except (IndexError, ValueError) as e:
                    console.print(f"[red]{e}[/]")
                    continue
                # 先以"过去的时间"处理完未入库消息，再让时间流逝
                await pipe.flush_ingest()
                report_ingest()
                new_now = clock.warp(secs)
                console.print(f"⏩ 时间快进 → [bold]{fmt_ts(new_now)}[/]")
                c = await pipe.consolidate_now()
                console.print(f"  😴 巩固：晋升 {c.promoted} 条 · 取代 {c.superseded} 条 "
                              f"· 睡眠归纳 {c.semantic_created} 条（归档 {c.archived} 条源记忆）")
                for content in c.promoted_contents:
                    console.print(f"  [dim]⬆ 晋升 LTM：{content}[/]")
                for content in c.superseded_contents:
                    console.print(f"  [dim]✖ 被取代：{content}[/]")
                for content in c.semantic_contents:
                    console.print(f"  [dim]✦ 语义归纳：{content}[/]")
                continue
            if line == "/consolidate":
                await pipe.flush_ingest()
                c = await pipe.consolidate_now()
                console.print(f"😴 晋升 {c.promoted} · 取代 {c.superseded} "
                              f"· 归纳 {c.semantic_created}（归档 {c.archived}）")
                if pipe.last_stickers_pruned:
                    console.print(f"  🧹 忘记表情包 {pipe.last_stickers_pruned} 张")
                continue
            if line == "/ingest":
                await pipe.flush_ingest()
                report_ingest()
                if not pipe.last_ingest_result:
                    console.print("[dim]（缓冲为空）[/]")
                continue
            if line == "/stats":
                console.print(stats_table(await store.stats()))
                continue
            if line.startswith("/memories"):
                args = line.split()[1:]
                stage = args[0] if args and args[0] in ("staging", "ltm", "all") else "ltm"
                rows = await store.list_memories(
                    group_id=cfg.sim_group_id,
                    stage=None if stage == "all" else stage,
                    status=None, limit=100,
                )
                console.print(memories_table(rows, clock.now(), title=f"记忆库·{stage}"))
                continue
            if line == "/stickers":
                if pipe.sticker_lib is None:
                    console.print("[dim]sticker.enabled 未开启[/]")
                else:
                    from rich.table import Table as _T

                    rows = await pipe.sticker_lib.list_stickers(30)
                    t = _T(title=f"表情包库（{len(rows)} 张）")
                    for col in ("id", "标签", "次数", "来源群", "描述"):
                        t.add_column(col, overflow="fold")
                    for r in rows:
                        t.add_row(str(r["id"]), "、".join(r["tags"]) or "未打标",
                                  str(r["use_count"]), r["group_id"],
                                  (r["description"] or "")[:24])
                    console.print(t)
                continue
            if line == "/window reset":
                pipe.reset_window(cfg.sim_group_id)
                console.print("🧹 对话窗口已清空——接下来只能靠长期记忆了。")
                continue
            if line.startswith("/recall "):
                text = line[len("/recall "):].strip()
                fake_group = cfg.sim_group_id
                from memory.models import MessageRecord
                probe = MessageRecord(group_id=fake_group, user_id="探针", user_name="探针",
                                      content=text, ts=clock.now())
                rec = await pipe.recaller.recall(fake_group, persona, [probe], reinforce=False)
                console.print(recall_table(rec, clock.now()))
                continue

            m = _MSG_RE.match(line)
            if not m:
                console.print("[dim]格式：名字: 消息　|　命令：/help[/]")
                continue
            name, content = m.group(1), m.group(2)
            reply = await pipe.on_user_message(cfg.sim_group_id, name, name, content)
            if reply:
                console.print(f"[bold green]{pipe.persona.name}[/]: {reply}")
            else:
                v = pipe.last_interest
                if v is not None:
                    parts = " ".join(f"{k}{'+' if val >= 0 else ''}{val:.0f}"
                                     for k, val in v.factors.items())
                    console.print(f"[dim]（兴致 {v.score:.0f}＜{cfg.behavior.interest_threshold}"
                                  f"｜{parts} → 潜水中）[/]")
                else:
                    console.print(f"[dim]（{pipe.persona.name}在旁边看着，没有说话……）[/]")
            if pipe._tasks:
                await asyncio.gather(*pipe._tasks, return_exceptions=True)
            report_ingest()
    finally:
        await pipe.flush_ingest()
        await close_stores(pipe, store)
        console.print("\n记忆已保存在本地库，随时回来继续。再见 👋")
