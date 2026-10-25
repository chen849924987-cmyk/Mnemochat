"""文本工具：中文分词（供 FTS5 BM25 使用）、宽松 JSON 解析、相对时间。

BM25 的关键词检索依赖词级切分——SQLite 自带的 unicode61 分词器不切中文，
所以入库与查询两侧统一用 jieba 切词后以空格拼接，再交给 FTS5。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import jieba

_jieba_ready = False

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+")


def warmup() -> None:
    """预加载 jieba 词典（约 1 秒），在启动时调用一次，避免首条消息卡顿。"""
    global _jieba_ready
    if not _jieba_ready:
        list(jieba.cut("预热"))
        _jieba_ready = True


def tokenize(text: str) -> list[str]:
    """切词并过滤噪声 token：去标点、去单字汉字、去纯数字。"""
    warmup()
    tokens: list[str] = []
    for tok in jieba.cut(text):
        tok = tok.strip()
        if not tok:
            continue
        for piece in _TOKEN_RE.findall(tok):
            # 单个汉字（的/了/是…）信息量太低，会污染 BM25；英文/数字保留
            if len(piece) == 1 and "\u4e00" <= piece <= "\u9fff":
                continue
            tokens.append(piece)
    return tokens


def seg(text: str) -> str:
    """空格拼接的分词结果，直接喂给 FTS5。"""
    return " ".join(tokenize(text))


def fts_query(text: str) -> str:
    """构造 FTS5 MATCH 表达式：词间 OR，每词加引号防语法注入。"""
    terms = tokenize(text)
    return " OR ".join(f'"{t}"' for t in terms)


def parse_json_loose(raw: str) -> dict:
    """宽松解析 LLM 输出的 JSON：剥代码围栏、截取首尾花括号。"""
    if not raw:
        raise ValueError("empty json")
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError(f"cannot parse json: {raw[:200]!r}")


def fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def fmt_relative(ts: float, now: float) -> str:
    """相对时间：给人格'活着'的感受（约三周前 / 上个月…）。"""
    days = max(0.0, (now - ts) / 86400.0)
    if days < 1 / 24:
        return "刚刚"
    if days < 1:
        return "今天"
    if days < 2:
        return "昨天"
    if days < 7:
        return f"约{int(days)}天前"
    if days < 14:
        return "上周"
    if days < 30:
        return f"约{days / 7:.0f}周前" if days >= 21 else "约两周前"
    if days < 60:
        return "上个月"
    if days < 365:
        return f"约{days / 30:.0f}个月前"
    return f"约{days / 365:.0f}年前"
