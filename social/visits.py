"""串门：客卡到主卡所在的群露面，两边都记住这次会面。

- 客人用自己的角色卡 + 记忆库（social.visits[i].database，留空=与主卡同库），
  读主卡群里的最近对话窗口，以自己的人格和自己记得的事生成一条自然的串门发言；
- 发言写入两边库：客库（自己的经历，入 ingest 缓冲）+
  主库（作为"来自客人的消息"走完整 on_user_message 管线）；
- host_replies=True 时主卡会正常接话——关系不是写死的，是聊出来的；
- 两种出场方式：
  - 模拟器/GUI（虚拟群）：客人以自己的名字出现在聊天记录里；
  - QQ 顶号模式（适配器注入 social_sender）：客人发言经**同一个账号**发出
    （类似顶号），默认加【名字】前缀防身份混淆，主卡的接话同样经账号发出。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from memory.ingest import format_window
from memory.models import MessageRecord
from persona.guard import build_reply_system

if TYPE_CHECKING:
    from chat.pipeline import BotPipeline
    from config import BotConfig, SocialVisit
    from memory.store import MemoryStore
    from util.clock import Clock

log = logging.getLogger(__name__)

VISIT_HINT = ("【串门场景】你现在临时到另一个群串门，像老朋友顺路来打招呼："
              "简短（一两句话）、自然，可以寒暄、接话茬、分享你那边的事；"
              "不要自我介绍身份设定，不要喧宾夺主，没有合适的话就简单打个招呼。")


async def perform_visit(host_pipe: "BotPipeline", host_store: "MemoryStore",
                        cfg: "BotConfig", clock: "Clock", visit: "SocialVisit",
                        mock: bool = False) -> dict:
    """执行一次串门，返回 {guest, text, reply, guest_db}。"""
    from main import _build_pipeline, close_stores

    group = cfg.sim_group_id
    guest_cfg = cfg.model_copy(deep=True)
    guest_cfg.persona_file = visit.persona_file
    if visit.database:
        guest_cfg.database = visit.database
    guest_cfg.social = guest_cfg.social.model_copy(update={"enabled": False})  # 防递归

    guest_pipe, guest_store = await _build_pipeline(guest_cfg, mock)
    try:
        guest = guest_pipe.persona
        now = clock.now()
        window = await host_store.recent_messages(group, cfg.window.recent_turns)

        # 客人以自己的记忆回应眼前的话题：用主卡的窗口做查询，查的是客库
        recall = await guest_pipe.recaller.recall(group, guest, window, reinforce=False)
        system = build_reply_system(guest, recall, group, now, extra_hint=VISIT_HINT)
        user = (f"以下是这个群最近的聊天记录：\n{format_window(window) or '（还很安静）'}\n\n"
                f"请以「{guest.name}」的身份发一条串门消息。")
        # 客人连说几条：每条都能看到自己刚说的话，像真人在群里刷屏聊天
        count = max(1, int(getattr(visit, "visit_count", 3) or 3))
        said: list[str] = []
        for i in range(count):
            convo = list(said)
            prompt_user = user
            if convo:
                prompt_user += (chr(10) + chr(10) + "你刚才已经在这个群说过了："
                                + chr(10) + chr(10).join(f"- {t}" for t in convo)
                                + chr(10) + "接着说下一句（别重复上面的内容，可以补充、改口、追问）。")
            text = (await guest_pipe.chat.achat([
                {"role": "system", "content": system},
                {"role": "user", "content": prompt_user},
            ])).strip()
            if not text:
                break
            said.append(text)
            ts = clock.now()
            msg_id = await guest_store.add_message(group, guest.name, guest.name, text, ts)
            guest_pipe._pending.setdefault(group, []).append(MessageRecord(
                id=msg_id, group_id=group, user_id=guest.name,
                user_name=guest.name, content=text, ts=ts))
            await guest_pipe.flush_ingest()

        if not said:
            return {"guest": guest.name, "text": "", "reply": None,
                    "guest_db": guest_cfg.database, "host": host_pipe.persona.name,
                    "lines": []}
        text = said[-1]

        # 主库：作为"来自客人的消息"走完整管线（记录 + 可选接话 + 入库）
        reply = await host_pipe.on_user_message(
            group, guest.name, guest.name, text,
            force_reply=cfg.social.host_replies)

        # 主卡的回应也要进客库——客人得记得"那天他是怎么回我的"
        if reply:
            rts = clock.now()
            rid = await guest_store.add_message(
                group, host_pipe.persona.name, host_pipe.persona.name, reply, rts)
            guest_pipe._pending.setdefault(group, []).append(MessageRecord(
                id=rid, group_id=group, user_id=host_pipe.persona.name,
                user_name=host_pipe.persona.name, content=reply, ts=rts))
            await guest_pipe.flush_ingest()

        # QQ 顶号模式：适配器注入了群发送器 → 客人发言与主卡接话都经同一账号发出
        sender = getattr(host_pipe, "social_sender", None)
        if sender is not None:
            for line in said:
                out = (f"【{guest.name}】{line}"
                       if cfg.social.prefix_guest_name else line)
                await sender(group, out)
            if reply:
                await sender(group, reply)

        return {"guest": guest.name, "text": text, "lines": said, "reply": reply,
                "guest_db": guest_cfg.database,
                "host": host_pipe.persona.name}
    finally:
        try:
            await guest_pipe.flush_ingest()
        except Exception:
            pass
        await close_stores(guest_pipe, guest_store)


def effective_visits(cfg) -> list:
    """有效串门关系 = 各角色卡自带 social 设定 + config.social.visits 追加项。

    角色卡的 social.visit_enabled 是"这张卡想不想去串门"的人格属性；
    config.social.visits 保留为兼容/手动追加（同卡不重复）。"""
    from pathlib import Path

    from config import SocialVisit
    from persona.card import PersonaCard
    from util.appdir import base_dir

    out: list = []
    seen: set[str] = set()
    active = Path(cfg.persona_file).name
    personas_dir = base_dir() / "personas"
    for p in sorted(personas_dir.glob("*.yaml")):
        if p.name == active:
            continue          # 自己不去自己的群串门
        try:
            card = PersonaCard.load_yaml(p)
        except Exception:
            continue
        social = card.social
        if not social.visit_enabled:
            continue
        # 关系绑定：填了目标就只去那几张卡的群；留空表示谁都可以
        if social.visit_targets:
            active_rel = Path(cfg.persona_file).as_posix().lstrip("./")
            if active_rel not in [t.lstrip("./") for t in social.visit_targets]:
                continue
        # 客卡永远用自己的记忆库
        db = str(Path(cfg.database).parent / f"memory_{p.stem}.db")
        rel = f"personas/{p.name}"
        out.append(SocialVisit(persona_file=rel, database=db,
                               every_hours=social.visit_every_hours,
                               visit_count=max(1, int(social.visit_count or 3))))
        seen.add(rel)
    for v in getattr(cfg.social, "visits", []) or []:
        if v.persona_file not in seen:
            out.append(v)
            seen.add(v.persona_file)
    return out


def due_visits(cfg, clock, last_visit: dict[int, float]) -> list[int]:
    """到期的串门关系下标（首次引入的关系立即视为到期，之后按 every_hours 间隔）。"""
    if not cfg.social.enabled:
        return []
    now = clock.now()
    due = []
    for i, v in enumerate(effective_visits(cfg)):
        last = last_visit.get(i)
        if last is None or now - last >= v.every_hours * 3600:
            due.append(i)
    return due


async def run_visit_scheduler(pipe: "BotPipeline", store: "MemoryStore",
                              cfg: "BotConfig", clock: "Clock",
                              last_visit: dict[int, float],
                              mock: bool = False) -> None:
    """串门自动调度：每分钟检查到期关系并执行（GUI 与 OneBot 适配器共用）。"""
    while True:
        await asyncio.sleep(60)
        try:
            due = due_visits(cfg, clock, last_visit)
            visits = effective_visits(cfg)
        except Exception:
            continue
        for i in due:
            last_visit[i] = clock.now()
            try:
                res = await perform_visit(pipe, store, cfg, clock,
                                          visits[i], mock=mock)
                log.info("自动串门：%s 来到了 %s 的群", res["guest"], res["host"])
            except Exception:
                log.exception("自动串门失败（关系 #%s）", i)
