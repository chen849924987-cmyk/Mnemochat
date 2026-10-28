"""唯一数据访问层：所有 SQL 集中在这里。

职责：
- messages / memories 的增查（append-only，永不物理删除）
- FTS5 全文索引维护（jieba 分词后入库，与查询侧一致）
- 嵌入存储与内存向量缓存（按模型名分行；只缓存 active 记忆）
- 实体索引（轻量图：实体名 → 记忆）
- 召回强化（access_count / last_accessed / 强度重置）

缓存策略：写入时递增 _version，向量/实体缓存按版本失效重载。
"""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import numpy as np

from util.text import seg
from .models import MemoryRecord, MessageRecord, _loads_or_default, memory_from_row, message_from_row

_SCHEMA = Path(__file__).resolve().parent / "schema.sql"
_STICKER_SCHEMA = Path(__file__).resolve().parent / "schema_stickers.sql"


class MemoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None
        self._version = 0
        self._vec_cache: dict[str, tuple[list[int], np.ndarray, dict[int, str]]] = {}
        self._entity_cache: dict[str, list[str]] = {}

    # ---------- 生命周期 ----------

    async def init(self, with_stickers: bool = True) -> None:
        """建表。with_stickers=False 时不建表情包表——用于"每卡独立记忆库 +
        共享表情包库"的部署：各卡记忆库互相独立，表情包集中在共享库。"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.executescript(_SCHEMA.read_text(encoding="utf-8"))
        if with_stickers:
            await self._db.executescript(_STICKER_SCHEMA.read_text(encoding="utf-8"))
        await self._db.commit()
        # 旧库迁移：messages 补 reasoning 列（思维链留存，v0.11 前建的库没有）
        cols = {r[1] for r in await self._db.execute_fetchall(
            "PRAGMA table_info(messages)")}
        if "reasoning" not in cols:
            await self._db.execute(
                "ALTER TABLE messages ADD COLUMN reasoning TEXT NOT NULL DEFAULT ''")
            await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "MemoryStore 未 init()"
        return self._db

    def _touch(self) -> None:
        self._version += 1
        self._vec_cache.clear()
        self._entity_cache.clear()

    # ---------- messages ----------

    async def add_message(
        self, group_id: str, user_id: str, user_name: str, content: str, ts: float,
        reasoning: str = "",
    ) -> int:
        cur = await self.db.execute(
            "INSERT INTO messages(group_id, user_id, user_name, content, ts, replied, reasoning) "
            "VALUES(?,?,?,?,?,0,?)",
            (group_id, user_id, user_name, content, ts, reasoning),
        )
        await self.db.commit()
        return cur.lastrowid

    async def recent_messages(
        self, group_id: str, limit: int = 20, after_ts: float | None = None
    ) -> list[MessageRecord]:
        sql = "SELECT * FROM messages WHERE group_id=?"
        params: list = [group_id]
        if after_ts is not None:
            sql += " AND ts>?"
            params.append(after_ts)
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = await self.db.execute_fetchall(sql, params)
        return [message_from_row(r) for r in reversed(rows)]

    async def recent_messages_by_user(self, user_id: str,
                                      limit: int = 50) -> list[MessageRecord]:
        rows = await self.db.execute_fetchall(
            "SELECT * FROM messages WHERE user_id=? ORDER BY ts DESC, id DESC LIMIT ?",
            (user_id, limit),
        )
        return [message_from_row(r) for r in rows]

    async def message_count(self, group_id: str | None = None) -> int:
        if group_id:
            row = await self.db.execute_fetchall(
                "SELECT COUNT(*) AS c FROM messages WHERE group_id=?", (group_id,)
            )
        else:
            row = await self.db.execute_fetchall("SELECT COUNT(*) AS c FROM messages")
        return row[0]["c"]

    # ---------- memories ----------

    async def add_memory(self, mem: MemoryRecord) -> int:
        cur = await self.db.execute(
            "INSERT INTO memories(stage, kind, group_id, subject_user_id, actor_user_id, "
            "content, keywords, entities, importance, emotion, visibility, "
            "access_count, recurrence_count, last_accessed_at, created_at, strength, "
            "status, superseded_by, archived_into, source_msg_ids) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                mem.stage, mem.kind, mem.group_id, mem.subject_user_id, mem.actor_user_id,
                mem.content, json.dumps(mem.keywords, ensure_ascii=False),
                json.dumps(mem.entities, ensure_ascii=False),
                mem.importance, mem.emotion, mem.visibility,
                mem.access_count, mem.recurrence_count, mem.last_accessed_at,
                mem.created_at, mem.strength, mem.status,
                mem.superseded_by, mem.archived_into,
                json.dumps(mem.source_msg_ids),
            ),
        )
        mem_id = cur.lastrowid
        await self.db.execute(
            "INSERT INTO fts_memories(content_seg, keywords_seg, mem_id) VALUES(?,?,?)",
            (seg(mem.content), " ".join(mem.keywords), mem_id),
        )
        if mem.entities:
            await self.add_entities(mem_id, mem.group_id, mem.entities)
        await self.db.commit()
        self._touch()
        return mem_id

    async def get_memory(self, mem_id: int) -> MemoryRecord | None:
        rows = await self.db.execute_fetchall("SELECT * FROM memories WHERE id=?", (mem_id,))
        return memory_from_row(rows[0]) if rows else None

    async def get_memories_by_ids(self, ids: list[int]) -> dict[int, MemoryRecord]:
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = await self.db.execute_fetchall(
            f"SELECT * FROM memories WHERE id IN ({placeholders})", ids
        )
        return {r["id"]: memory_from_row(r) for r in rows}

    async def list_memories(
        self,
        group_id: str | None = None,
        stage: str | None = None,
        status: str | None = "active",
        kind: str | None = None,
        subject_user_id: str | None = None,
        limit: int = 200,
    ) -> list[MemoryRecord]:
        sql, params = "SELECT * FROM memories WHERE 1=1", []
        if group_id:
            sql += " AND group_id=?"
            params.append(group_id)
        if stage:
            sql += " AND stage=?"
            params.append(stage)
        if status:
            sql += " AND status=?"
            params.append(status)
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        if subject_user_id:
            sql += " AND subject_user_id=?"
            params.append(subject_user_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = await self.db.execute_fetchall(sql, params)
        return [memory_from_row(r) for r in rows]

    _UPDATABLE = {
        "stage", "status", "superseded_by", "archived_into", "importance",
        "recurrence_count", "access_count", "last_accessed_at", "strength", "emotion",
    }

    async def update_memory(self, mem_id: int, **fields) -> None:
        for key in fields:
            if key not in self._UPDATABLE:
                raise ValueError(f"字段不可更新: {key}")
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        await self.db.execute(
            f"UPDATE memories SET {sets} WHERE id=?", (*fields.values(), mem_id)
        )
        await self.db.commit()
        self._touch()

    # ---------- 嵌入 ----------

    async def save_embedding(self, memory_id: int, model: str, vec: np.ndarray) -> None:
        v = np.asarray(vec, dtype=np.float32).ravel()
        await self.db.execute(
            "INSERT OR REPLACE INTO embeddings(memory_id, model, dim, vec) VALUES(?,?,?,?)",
            (memory_id, model, v.shape[0], v.tobytes()),
        )
        await self.db.commit()
        self._touch()

    async def get_embedding(self, memory_id: int, model: str) -> np.ndarray | None:
        rows = await self.db.execute_fetchall(
            "SELECT vec FROM embeddings WHERE memory_id=? AND model=?", (memory_id, model)
        )
        return np.frombuffer(rows[0]["vec"], dtype=np.float32) if rows else None

    async def ensure_vec_cache(self, model: str) -> tuple[list[int], np.ndarray, dict[int, str]]:
        """加载该模型下所有 active 记忆的归一化向量。返回 (ids, matrix, id→group)。"""
        cached = self._vec_cache.get(model)
        if cached is not None:
            return cached
        rows = await self.db.execute_fetchall(
            "SELECT e.memory_id AS mid, e.vec AS vec, m.group_id AS gid "
            "FROM embeddings e JOIN memories m ON m.id = e.memory_id "
            "WHERE e.model=? AND m.status='active'",
            (model,),
        )
        ids: list[int] = []
        id2group: dict[int, str] = {}
        vecs: list[np.ndarray] = []
        ref_dim: int | None = None
        for r in rows:
            v = np.frombuffer(r["vec"], dtype=np.float32)
            if ref_dim is None:
                ref_dim = v.shape[0]
            elif v.shape[0] != ref_dim:
                continue  # 同名模型下维度不一致的脏数据，跳过
            n = float(np.linalg.norm(v))
            if n < 1e-9:
                continue
            ids.append(r["mid"])
            id2group[r["mid"]] = r["gid"]
            vecs.append(v / n)
        matrix = (
            np.stack(vecs).astype(np.float32) if vecs else np.zeros((0, 1), dtype=np.float32)
        )
        self._vec_cache[model] = (ids, matrix, id2group)
        return self._vec_cache[model]

    async def vector_topk(
        self, model: str, qvec: np.ndarray, k: int, group_id: str | None = None
    ) -> list[tuple[int, float]]:
        ids, matrix, id2group = await self.ensure_vec_cache(model)
        if len(ids) == 0:
            return []
        q = np.asarray(qvec, dtype=np.float32).ravel()
        qn = np.linalg.norm(q)
        if qn < 1e-9:
            return []
        sims = matrix @ (q / qn)
        order = np.argsort(-sims)
        out: list[tuple[int, float]] = []
        for idx in order:
            mid = ids[idx]
            if group_id is not None and id2group.get(mid) != group_id:
                continue
            out.append((mid, float(sims[idx])))
            if len(out) >= k:
                break
        return out

    async def vector_sims(self, model: str, qvec: np.ndarray, ids: list[int]) -> dict[int, float]:
        """给定候选 id 集合，返回各自与查询向量的相似度（无向量则 0）。"""
        all_ids, matrix, _ = await self.ensure_vec_cache(model)
        result = {mid: 0.0 for mid in ids}
        if len(all_ids) == 0:
            return result
        pos = {mid: i for i, mid in enumerate(all_ids)}
        q = np.asarray(qvec, dtype=np.float32).ravel()
        qn = np.linalg.norm(q)
        if qn < 1e-9:
            return result
        sims = matrix @ (q / qn)
        for mid in ids:
            i = pos.get(mid)
            if i is not None:
                result[mid] = max(0.0, float(sims[i]))
        return result

    async def memories_without_embedding(self, model: str) -> list[int]:
        rows = await self.db.execute_fetchall(
            "SELECT m.id FROM memories m "
            "LEFT JOIN embeddings e ON e.memory_id=m.id AND e.model=? "
            "WHERE e.memory_id IS NULL AND m.status IN ('active','superseded','archived')",
            (model,),
        )
        return [r["id"] for r in rows]

    # ---------- BM25 ----------

    async def bm25_search(self, match_expr: str, limit: int = 50) -> list[tuple[int, float]]:
        """FTS5 BM25。返回 (mem_id, 原始bm25分)，bm25() 越接近 0 越相关，按最相关在前。"""
        if not match_expr.strip():
            return []
        try:
            rows = await self.db.execute_fetchall(
                "SELECT f.mem_id AS mid, bm25(fts_memories) AS s "
                "FROM fts_memories f JOIN memories m ON m.id=f.mem_id "
                "WHERE fts_memories MATCH ? AND m.status='active' "
                "ORDER BY s LIMIT ?",
                (match_expr, limit),
            )
        except aiosqlite.OperationalError:
            return []
        return [(r["mid"], float(r["s"])) for r in rows]

    # ---------- 实体索引 ----------

    async def add_entities(self, memory_id: int, group_id: str, names: list[str]) -> None:
        for name in {n.strip() for n in names if n and n.strip()}:
            await self.db.execute(
                "INSERT OR IGNORE INTO memory_entities(name, group_id, memory_id) VALUES(?,?,?)",
                (name, group_id, memory_id),
            )
        await self.db.commit()
        self._touch()

    async def entity_names(self, group_id: str) -> list[str]:
        cached = self._entity_cache.get(group_id)
        if cached is not None:
            return cached
        rows = await self.db.execute_fetchall(
            "SELECT DISTINCT name FROM memory_entities WHERE group_id=?", (group_id,)
        )
        names = [r["name"] for r in rows]
        self._entity_cache[group_id] = names
        return names

    async def memories_by_entities(
        self, group_id: str, names: list[str], limit: int = 20
    ) -> list[int]:
        if not names:
            return []
        placeholders = ",".join("?" * len(names))
        rows = await self.db.execute_fetchall(
            f"SELECT DISTINCT memory_id FROM memory_entities "
            f"WHERE group_id=? AND name IN ({placeholders}) LIMIT ?",
            (group_id, *names, limit),
        )
        return [r["memory_id"] for r in rows]

    # ---------- 强化 ----------

    async def apply_reinforcement(
        self, mem_ids: list[int], now: float, factor: float, cap: float
    ) -> None:
        """检索即强化：access_count+1、刷新 last_accessed、强度×factor（封顶 cap）。"""
        if not mem_ids:
            return
        await self.db.executemany(
            "UPDATE memories SET access_count=access_count+1, last_accessed_at=?, "
            "strength=min(strength*?, ?) WHERE id=?",
            [(now, factor, cap, mid) for mid in mem_ids],
        )
        await self.db.commit()
        self._touch()

    # ---------- 巩固审计 / meta / 统计 ----------

    async def begin_consolidation_run(self, started_at: float) -> int:
        cur = await self.db.execute(
            "INSERT INTO consolidation_runs(started_at) VALUES(?)", (started_at,)
        )
        await self.db.commit()
        return cur.lastrowid

    async def finish_consolidation_run(
        self, run_id: int, finished_at: float, promoted: int, reinforced: int,
        superseded: int, semantic_created: int, notes: str = "",
    ) -> None:
        await self.db.execute(
            "UPDATE consolidation_runs SET finished_at=?, promoted=?, reinforced=?, "
            "superseded=?, semantic_created=?, notes=? WHERE id=?",
            (finished_at, promoted, reinforced, superseded, semantic_created, notes, run_id),
        )
        await self.db.commit()

    async def set_meta(self, key: str, value: str) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)", (key, value)
        )
        await self.db.commit()

    async def get_meta(self, key: str) -> str | None:
        rows = await self.db.execute_fetchall("SELECT value FROM meta WHERE key=?", (key,))
        return rows[0]["value"] if rows else None

    # ---------- 人格漂移报告 ----------

    async def add_drift_report(self, ts: float, adherence: float, drifted: bool,
                               issues: list[str], suggestion: str,
                               sample_size: int) -> int:
        cur = await self.db.execute(
            "INSERT INTO drift_reports(ts, adherence, drifted, issues, suggestion, sample_size) "
            "VALUES(?,?,?,?,?,?)",
            (ts, adherence, int(drifted),
             json.dumps(issues, ensure_ascii=False), suggestion, sample_size),
        )
        await self.db.commit()
        return cur.lastrowid

    async def latest_drift_report(self) -> dict | None:
        rows = await self.db.execute_fetchall(
            "SELECT * FROM drift_reports ORDER BY id DESC LIMIT 1"
        )
        if not rows:
            return None
        row = rows[0]
        row = dict(row)
        row["issues"] = _loads_or_default(row["issues"], [])
        row["drifted"] = bool(row["drifted"])
        return row

    # ---------- 导出 / 导入（备份迁移，不含嵌入——用 --reembed 重建） ----------

    async def export_all(self) -> dict:
        async def rows(table: str) -> list[dict]:
            result = await self.db.execute_fetchall(f"SELECT * FROM {table}")
            return [dict(r) for r in result]
        return {
            "messages": await rows("messages"),
            "memories": await rows("memories"),
            "memory_entities": await rows("memory_entities"),
        }

    async def import_all(self, data: dict) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in data.get("messages", []):
            await self.db.execute(
                "INSERT OR REPLACE INTO messages(id, group_id, user_id, user_name, content, ts, replied) "
                "VALUES(:id, :group_id, :user_id, :user_name, :content, :ts, :replied)",
                row,
            )
            counts["messages"] = counts.get("messages", 0) + 1
        for row in data.get("memories", []):
            await self.db.execute(
                "INSERT OR REPLACE INTO memories(id, stage, kind, group_id, subject_user_id, "
                "actor_user_id, content, keywords, entities, importance, emotion, visibility, "
                "access_count, recurrence_count, last_accessed_at, created_at, strength, status, "
                "superseded_by, archived_into, source_msg_ids) "
                "VALUES(:id, :stage, :kind, :group_id, :subject_user_id, :actor_user_id, :content, "
                ":keywords, :entities, :importance, :emotion, :visibility, :access_count, "
                ":recurrence_count, :last_accessed_at, :created_at, :strength, :status, "
                ":superseded_by, :archived_into, :source_msg_ids)",
                row,
            )
            await self.db.execute(
                "INSERT OR IGNORE INTO fts_memories(content_seg, keywords_seg, mem_id) VALUES(?,?,?)",
                (seg(row["content"]), " ".join(_loads_or_default(row["keywords"], [])), row["id"]),
            )
            counts["memories"] = counts.get("memories", 0) + 1
        for row in data.get("memory_entities", []):
            await self.db.execute(
                "INSERT OR IGNORE INTO memory_entities(name, group_id, memory_id) "
                "VALUES(:name, :group_id, :memory_id)",
                row,
            )
            counts["memory_entities"] = counts.get("memory_entities", 0) + 1
        await self.db.commit()
        self._touch()
        return counts

    async def stats(self) -> dict:
        out: dict = {}
        for field in ("stage", "kind", "status"):
            rows = await self.db.execute_fetchall(
                f"SELECT {field} AS k, COUNT(*) AS c FROM memories GROUP BY {field}"
            )
            out[field] = {r["k"]: r["c"] for r in rows}
        out["messages"] = await self.message_count()
        out["embeddings"] = (await self.db.execute_fetchall(
            "SELECT model, COUNT(*) AS c FROM embeddings GROUP BY model"
        ))
        out["embeddings"] = {r["model"]: r["c"] for r in out["embeddings"]}
        return out
