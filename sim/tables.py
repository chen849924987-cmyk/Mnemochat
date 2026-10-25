"""rich 表格渲染：模拟器与管理台共用。"""

from __future__ import annotations

from rich.table import Table

from memory import decay
from memory.models import MemoryRecord
from memory.recall import RecallResult
from util.text import fmt_relative, fmt_ts


def memories_table(rows: list[MemoryRecord], now: float, title: str = "记忆库") -> Table:
    table = Table(title=title, show_lines=False)
    for col in ("id", "阶段", "类型", "状态", "主体", "重要", "R", "S(天)", "召回", "复现", "内容"):
        table.add_column(col, overflow="fold")
    for m in rows:
        ret = decay.retention((now - m.anchor_ts) / 86400.0, m.strength)
        table.add_row(
            str(m.id), m.stage, m.kind, m.status,
            m.subject_user_id or "-", f"{m.importance:.0f}",
            f"{ret:.2f}", f"{m.strength:.1f}",
            str(m.access_count), str(m.recurrence_count),
            m.content[:48],
        )
    return table


def recall_table(recall: RecallResult, now: float) -> Table:
    table = Table(title=f"召回预演（{len(recall.items)} 条）")
    for col in ("id", "总分", "语义", "BM25", "保留率", "召回数", "时间", "内容"):
        table.add_column(col, overflow="fold")
    for item in recall.items:
        f = item.factors
        table.add_row(
            str(item.memory.id), f"{item.score:.3f}",
            f"{f.get('similarity', 0):.2f}", f"{f.get('bm25', 0):.2f}",
            f"{f.get('retention', 0):.2f}", str(int(f.get('access_count', 0))),
            fmt_relative(item.memory.anchor_ts, now),
            item.memory.content[:48],
        )
    return table


def detail_panel(m: MemoryRecord, now: float) -> Table:
    table = Table(title=f"记忆 #{m.id}", show_header=False)
    table.add_column("字段", style="bold")
    table.add_column("值", overflow="fold")
    rows = [
        ("阶段/类型", f"{m.stage} / {m.kind}"),
        ("状态", m.status + (f" → 取代于 #{m.superseded_by}" if m.superseded_by
                             else (f" → 归纳于 #{m.archived_into}" if m.archived_into else ""))),
        ("群/主体/说话人", f"{m.group_id} / {m.subject_user_id or '-'} / {m.actor_user_id or '-'}"),
        ("内容", m.content),
        ("关键词", "、".join(m.keywords) or "-"),
        ("实体", "、".join(m.entities) or "-"),
        ("重要性/情感", f"{m.importance:.1f} / {m.emotion:.1f}"),
        ("创建/最后召回", f"{fmt_ts(m.created_at)} / "
                       + (fmt_ts(m.last_accessed_at) if m.last_accessed_at else "从未")),
        ("强度 S", f"{m.strength:.2f} 天"),
        ("保留率 R(当前)", f"{decay.retention((now - m.anchor_ts) / 86400.0, m.strength):.3f}"),
        ("召回次数/复现次数", f"{m.access_count} / {m.recurrence_count}"),
        ("溯源消息", ", ".join(str(i) for i in m.source_msg_ids) or "-"),
    ]
    for k, v in rows:
        table.add_row(k, v)
    return table


def stats_table(stats: dict) -> Table:
    table = Table(title="统计")
    table.add_column("维度")
    table.add_column("分布")
    for field in ("stage", "kind", "status"):
        table.add_row(field, "  ".join(f"{k}:{v}" for k, v in stats.get(field, {}).items())
                      or "空")
    table.add_row("messages", str(stats.get("messages", 0)))
    table.add_row("embeddings", "  ".join(f"{k}:{v}" for k, v in stats.get("embeddings", {}).items())
                  or "空")
    return table
