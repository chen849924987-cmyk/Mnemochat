"""串门：客卡到主卡所在的模拟群露面，两边都记住这次会面。

- 客人用自己的角色卡 + 记忆库（social.visits[i].database，留空=与主卡同库），
  读主卡群里的最近对话窗口，以自己的人格和自己记得的事生成一条自然的串门发言；
- 发言写入两边库：客库（自己的经历，入 ingest 缓冲）+
  主库（作为"来自客人的消息"走完整 on_user_message 管线）；
- host_replies=True 时主卡会正常接话——关系不是写死的，是聊出来的；
- 身份边界：模拟器/GUI 的群是虚拟的，客人以自己的名字出现，效果最佳；
  QQ 模式下单个账号无法分身，跨号串门请用"双开双卡同群"方案（各跑一个实例）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from memory.ingest import format_window
from memory.models import MessageRecord
from persona.guard import build_reply_system

if TYPE_CHECKING:
    from chat.pipeline import BotPipeline
    from config import BotConfig, SocialVisit
    from memory.store import MemoryStore
    from util.clock import Clock

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
        text = (await guest_pipe.chat.achat([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ])).strip()

        # 客库：这是客人自己的经历（进 ingest 缓冲，巩固时成为它的记忆）
        ts = clock.now()
        msg_id = await guest_store.add_message(group, guest.name, guest.name, text, ts)
        guest_pipe._pending.setdefault(group, []).append(MessageRecord(
            id=msg_id, group_id=group, user_id=guest.name,
            user_name=guest.name, content=text, ts=ts))
        await guest_pipe.flush_ingest()

        # 主库：作为"来自客人的消息"走完整管线（记录 + 可选接话 + 入库）
        reply = await host_pipe.on_user_message(
            group, guest.name, guest.name, text,
            force_reply=cfg.social.host_replies)

        return {"guest": guest.name, "text": text, "reply": reply,
                "guest_db": guest_cfg.database,
                "host": host_pipe.persona.name}
    finally:
        try:
            await guest_pipe.flush_ingest()
        except Exception:
            pass
        await close_stores(guest_pipe, guest_store)


def due_visits(cfg, clock, last_visit: dict[int, float]) -> list[int]:
    """到期的串门关系下标（首次引入的关系立即视为到期，之后按 every_hours 间隔）。"""
    if not cfg.social.enabled:
        return []
    now = clock.now()
    due = []
    for i, v in enumerate(cfg.social.visits):
        last = last_visit.get(i)
        if last is None or now - last >= v.every_hours * 3600:
            due.append(i)
    return due
