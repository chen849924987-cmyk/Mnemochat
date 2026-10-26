"""表情包图库：收（下载去重）、学（语境/视觉打标）、发（按分类取图）。

- 收集是旁观学习在图像上的延伸：群友斗图时机器人默默攒图（按内容 md5 去重），
  每次出现都记录语境（前后几条消息），作为标签学习的数据源；
- 发送走回复标记 [[sticker:分类]]：适配器先发图、再发文字（与角色卡的
  send_sticker 规范一致）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

import httpx

from config import BotConfig

if TYPE_CHECKING:
    from memory.store import MemoryStore

log = logging.getLogger(__name__)

MARKER_RE = re.compile(r"\[\[sticker:([^\[\]:]{1,24})\]\]")


def extract_sticker_markers(text: str) -> tuple[list[str], str]:
    """提取回复中的 [[sticker:分类]] 标记，返回 (标记列表, 清理后的文本)。"""
    markers = MARKER_RE.findall(text)
    clean = re.sub(r" {2,}", " ", MARKER_RE.sub("", text)).strip()
    return markers, clean


def _ext_from_bytes(data: bytes, url: str = "") -> str:
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"GIF":
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    for ext in (".jpg", ".png", ".gif", ".webp"):
        if url.lower().split("?")[0].endswith(ext):
            return ext
    return ".jpg"


async def _http_download(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
    except Exception:
        log.debug("表情包下载失败：%s", url)
        return None


class StickerLibrary:
    def __init__(self, store: "MemoryStore", cfg: BotConfig,
                 downloader: Callable[[str], Awaitable[bytes | None]] | None = None) -> None:
        self.store = store
        self.cfg = cfg
        # 表情图存在数据库旁边：<db目录>/stickers/
        self.dir = Path(store.db_path).resolve().parent / "stickers"
        self._download = downloader or _http_download
        self._sem = asyncio.Semaphore(4)
        self._last_sent: dict[str, float] = {}   # group_id -> monotonic

    # ---------- 收集 ----------

    async def collect(self, url: str, group_id: str, sender: str, now: float) -> int | None:
        """下载表情包入库；重复出现则计数并补语境。返回 sticker id。"""
        if not url:
            return None
        async with self._sem:
            data = await self._download(url)
        if not data:
            return None
        if len(data) > self.cfg.sticker.collect_max_kb * 1024:
            return None  # 大图是照片，不是表情包

        md5 = hashlib.md5(data).hexdigest()
        context = await self._recent_context(group_id)
        rows = await self.store.db.execute_fetchall(
            "SELECT id FROM stickers WHERE md5=?", (md5,)
        )
        if rows:
            sid = rows[0]["id"]
            await self.store.db.execute(
                "UPDATE stickers SET use_count=use_count+1, last_seen=? WHERE id=?",
                (now, sid),
            )
            await self._add_context(sid, now, group_id, sender, context)
            await self.store.db.commit()
            return sid

        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{md5}{_ext_from_bytes(data, url)}"
        path.write_bytes(data)
        cur = await self.store.db.execute(
            "INSERT INTO stickers(md5, file_path, source_url, group_id, first_sender, "
            "first_seen, last_seen, use_count) VALUES(?,?,?,?,?,?,?,1)",
            (md5, str(path), url, group_id, sender, now, now),
        )
        sid = cur.lastrowid
        await self._add_context(sid, now, group_id, sender, context)
        await self.store.db.commit()
        log.info("收集表情包 #%s（%s，%d 字节）", sid, group_id, len(data))
        return sid

    async def _recent_context(self, group_id: str) -> str:
        msgs = await self.store.recent_messages(group_id, limit=3)
        return " / ".join(f"{m.user_name}:{m.content[:30]}" for m in msgs)

    async def _add_context(self, sid: int, ts: float, group_id: str,
                           sender: str, context: str) -> None:
        await self.store.db.execute(
            "INSERT INTO sticker_contexts(sticker_id, ts, group_id, sender, context_text) "
            "VALUES(?,?,?,?,?)",
            (sid, ts, group_id, sender, context[:200]),
        )

    # ---------- 打标数据 ----------

    async def untagged(self, limit: int = 8) -> list[dict]:
        rows = await self.store.db.execute_fetchall(
            "SELECT id, description FROM stickers WHERE tags='[]' LIMIT ?", (limit,)
        )
        out: list[dict] = []
        for r in rows:
            ctxs = await self.store.db.execute_fetchall(
                "SELECT context_text FROM sticker_contexts WHERE sticker_id=? "
                "ORDER BY ts DESC LIMIT 3", (r["id"],)
            )
            out.append({"id": r["id"], "description": r["description"] or "",
                        "contexts": [c["context_text"] for c in ctxs]})
        return out

    async def set_tags(self, sid: int, tags: list[str],
                       description: str | None = None) -> None:
        if description is not None:
            await self.store.db.execute(
                "UPDATE stickers SET tags=?, description=? WHERE id=?",
                (json.dumps(tags, ensure_ascii=False), description, sid),
            )
        else:
            await self.store.db.execute(
                "UPDATE stickers SET tags=? WHERE id=?",
                (json.dumps(tags, ensure_ascii=False), sid),
            )
        await self.store.db.commit()

    async def file_path(self, sid: int) -> str | None:
        rows = await self.store.db.execute_fetchall(
            "SELECT file_path FROM stickers WHERE id=?", (sid,)
        )
        return rows[0]["file_path"] if rows else None

    # ---------- 发送 ----------

    async def pick_by_tag(self, tag: str, group_id: str) -> dict | None:
        """按分类选图：本群的梗优先；热度按"使用次数 / 时间距离"衰减——
        久未出现的冷梗自然沉底（软遗忘），冷却期内不发。"""
        now = time.monotonic()
        if now - self._last_sent.get(group_id, 0.0) < self.cfg.sticker.send_cooldown_seconds:
            return None
        rows = await self.store.db.execute_fetchall(
            "SELECT * FROM stickers WHERE tags LIKE ? "
            "ORDER BY CASE WHEN group_id=? THEN 0 ELSE 1 END, "
            "use_count / (1.0 + MAX(0.0, ? - last_seen) / 86400.0) DESC LIMIT 5",
            (f'%"{tag}"%', group_id, time.time()),
        )
        if not rows:
            return None
        pick = random.choice(rows[:3])
        self._last_sent[group_id] = now
        return dict(pick)

    # ---------- 遗忘 ----------

    async def prune(self, now: float, retain_days: float) -> int:
        """硬遗忘：群里 retain_days 天没出现过的梗删除行、语境与图片文件。
        表情包没有记忆那种溯源价值，这里破例做物理删除；retain_days<=0 由调用方短路。
        返回清理张数。"""
        cutoff = now - retain_days * 86400.0
        rows = await self.store.db.execute_fetchall(
            "SELECT id, file_path FROM stickers WHERE last_seen < ?", (cutoff,))
        if not rows:
            return 0
        root = self.dir.resolve()
        removed = 0
        for r in rows:
            p = Path(r["file_path"])
            try:   # 防御：只删图库目录内的文件
                if p.resolve().parent == root and p.exists():
                    p.unlink()
            except OSError:
                log.warning("表情包文件清理失败：%s", p)
            await self.store.db.execute(
                "DELETE FROM sticker_contexts WHERE sticker_id=?", (r["id"],))
            await self.store.db.execute("DELETE FROM stickers WHERE id=?", (r["id"],))
            removed += 1
        await self.store.db.commit()
        if removed:
            log.info("表情包遗忘：%d 张（%d 天未出现）", removed, int(retain_days))
        return removed

    # ---------- prompt 注入 ----------

    async def prompt_block(self, group_id: str) -> str:
        """把可用分类清单注入 system prompt（本群的梗优先展示）。"""
        rows = await self.store.db.execute_fetchall(
            "SELECT tags FROM stickers WHERE tags != '[]' AND group_id=?", (group_id,)
        )
        if not rows:
            rows = await self.store.db.execute_fetchall(
                "SELECT tags FROM stickers WHERE tags != '[]'"
            )
        counts: dict[str, int] = {}
        for r in rows:
            try:
                tags = json.loads(r["tags"])
            except (ValueError, TypeError):
                continue
            for t in tags:
                counts[t] = counts.get(t, 0) + 1
        if not counts:
            return ""
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:6]
        cats = "、".join(f"{t}({c})" for t, c in top)
        return (
            f"【表情包】群里攒了表情包，可用分类：{cats}。"
            "想发就在回复文本中嵌入 [[sticker:分类]] 标记（标记不是文字，单独嵌入即可），"
            "系统会先发图再发你的话；没有贴切分类就不发，别硬凑。"
        )

    async def stats(self) -> dict:
        total = (await self.store.db.execute_fetchall(
            "SELECT COUNT(*) AS c FROM stickers"))[0]["c"]
        tagged = (await self.store.db.execute_fetchall(
            "SELECT COUNT(*) AS c FROM stickers WHERE tags != '[]'"))[0]["c"]
        return {"total": total, "tagged": tagged}

    async def list_stickers(self, limit: int = 50) -> list[dict]:
        rows = await self.store.db.execute_fetchall(
            "SELECT id, tags, use_count, group_id, description, file_path "
            "FROM stickers ORDER BY last_seen DESC LIMIT ?", (limit,)
        )
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["tags"] = json.loads(d["tags"])
            except (ValueError, TypeError):
                d["tags"] = []
            out.append(d)
        return out
