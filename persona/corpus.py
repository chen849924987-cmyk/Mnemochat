"""角色语料库：绑定角色的"原声台词/黑话/情绪反应"样本，按轮抽样注入。

设计（借鉴 DSH 插件 tieba-mouth 的"把语料当数据库"思路）：
- 语料是独立 JSON 文件，不压缩进角色卡——角色卡保持忠实还原，
  语料库随用随扩（这是"数据库"，不是"设定集"）；
- 每轮回复抽样 k 条注入【语感参考】，同一群在同一时间窗内抽到的固定
  （确定性抽样，避免每句话换一批导致语感跳变）；
- 语料是风格参考不是台词：模型照着语感组织句子，不照抄整条。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

_cache: dict[tuple[str, float], list[dict]] = {}


def load_entries(path: Path) -> list[dict]:
    """读取语料库 JSON（按 mtime 缓存）。文件缺失/损坏返回空列表。"""
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return []
    key = (str(p), mtime)
    if key in _cache:
        return _cache[key]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        entries = data.get("entries") or []
        assert isinstance(entries, list)
    except Exception:
        entries = []
    _cache[key] = entries
    return entries


def sample(entries: list[dict], group_id: str, now: float,
           k: int = 4, rotate_minutes: int = 3) -> list[dict]:
    """确定性抽样：同一群在同一时间窗内抽到的内容固定，不每句话换新花样。"""
    if not entries:
        return []
    window = max(1, rotate_minutes) * 60
    rng = random.Random(f"{group_id}:{int(now // window)}")
    k = max(1, min(k, len(entries)))
    return rng.sample(entries, k)


def render(items: list[dict]) -> str:
    lines = ["【语感参考·本轮】（挑 1~2 个用，别全用上，也别照抄整句）"]
    for it in items:
        phrase = str(it.get("phrase") or "").strip()
        if not phrase:
            continue
        meaning = str(it.get("meaning") or "").strip()
        example = str(it.get("example") or "").strip()
        piece = f"- 「{phrase}」"
        if meaning:
            piece += f"——{meaning}"
        if example:
            piece += f"（例：{example}）"
        lines.append(piece)
    return "\n".join(lines)
