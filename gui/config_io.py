"""配置读写：GUI 表单 ↔ config.yaml / .env，不改代码完成全部配置。

约定（与 config.py 一致）：
- 密钥放 .env（DEEPSEEK_API_KEY / SILICONFLOW_API_KEY / TELEGRAM_BOT_TOKEN），
  config.yaml 以 ${VAR} 引用；
- config.yaml 由带注释的模板渲染生成——界面上没有的高级调参项
  （ingest/consolidation/recall/decay…）同样取自当前配置一起回写，绝不丢失；
- .env 只更新已知键，其余行（注释、别的变量）原样保留。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

ENV_CHAT_KEY = "DEEPSEEK_API_KEY"
ENV_EMBED_KEY = "SILICONFLOW_API_KEY"
ENV_TELEGRAM = "TELEGRAM_BOT_TOKEN"

# 占位符 @@KEY@@ 与 values_from_config() 的键一一对应
_YAML_TEMPLATE = """\
# ============================================================
# 群聊记忆机器人配置 · 由图形控制台生成
# 密钥放 .env，此处以 ${环境变量名} 引用
# 界面未展示的高级调参项保存时从原文件重读回填，不会被覆盖
# ============================================================

# 角色卡文件（personas/ 目录下）
persona_file: @@PERSONA_FILE@@

# SQLite 数据库路径
database: @@DATABASE@@

# ---------- 聊天模型（回复用） ----------
llm:
  base_url: @@CHAT_BASE_URL@@
  api_key: ${DEEPSEEK_API_KEY}
  model: @@CHAT_MODEL@@
  temperature: @@CHAT_TEMPERATURE@@
  max_tokens: @@CHAT_MAX_TOKENS@@

# ---------- 提取模型（记忆提取/巩固用，可用便宜模型） ----------
extractor:
  base_url: @@EXT_BASE_URL@@
  api_key: ${DEEPSEEK_API_KEY}
  model: @@EXT_MODEL@@
  temperature: @@EXT_TEMPERATURE@@
  max_tokens: @@EXT_MAX_TOKENS@@

# ---------- 嵌入（provider: api | local，切换后运行 --reembed 补齐旧向量） ----------
embedding:
  provider: @@EMBED_PROVIDER@@
  api:
    base_url: @@EMBED_BASE_URL@@
    api_key: ${SILICONFLOW_API_KEY}
    model: @@EMBED_MODEL@@
    dim: @@EMBED_DIM@@
    batch_size: 32
  local:
    # 需先安装: pip install -r requirements-local-embed.txt
    model_name: @@LOCAL_MODEL@@
    dim: @@LOCAL_DIM@@
    device: @@LOCAL_DEVICE@@

# ---------- 记忆写入 ----------
ingest:
  window_size: @@INGEST_WINDOW_SIZE@@
  min_interval_seconds: @@INGEST_MIN_INTERVAL@@
  dedup_threshold: @@INGEST_DEDUP@@
  noise_importance_below: @@INGEST_NOISE_BELOW@@

# ---------- 巩固（"睡眠"） ----------
consolidation:
  interval_minutes: @@CONSOL_INTERVAL@@
  min_age_hours: @@CONSOL_MIN_AGE@@
  min_importance: @@CONSOL_MIN_IMPORTANCE@@
  cluster_similarity: @@CONSOL_CLUSTER_SIM@@
  min_cluster_size: @@CONSOL_MIN_CLUSTER@@
  supersede_check_topk: @@CONSOL_SUPERSEDE_TOPK@@

# ---------- 检索 ----------
recall:
  top_k: @@RECALL_TOP_K@@
  query_recent_messages: @@RECALL_QUERY_RECENT@@
  bm25_candidates: @@RECALL_BM25_CAND@@
  vector_candidates: @@RECALL_VEC_CAND@@
  entity_candidates: @@RECALL_ENT_CAND@@
  staging_min_age_hours: @@RECALL_STAGING_MIN_AGE@@
  weights:
    similarity: @@W_SIMILARITY@@
    bm25: @@W_BM25@@
    retention: @@W_RETENTION@@
    access: @@W_ACCESS@@
    importance_emotion: @@W_IMPORTANCE_EMOTION@@

# ---------- 衰减（艾宾浩斯） ----------
decay:
  initial_base_days: @@DECAY_INITIAL_BASE@@
  reinforce_factor: @@DECAY_REINFORCE@@
  strength_cap_days: @@DECAY_CAP@@

# ---------- 群聊行为 ----------
behavior:
  reply_on_mention: @@REPLY_ON_MENTION@@
  names: @@NAMES@@
  reply_on_question: @@REPLY_ON_QUESTION@@
  reply_probability: @@REPLY_PROBABILITY@@
  # 兴趣度评分：@/叫名字必回之外，按分数决定接不接话（纯本地计算）
  interest_enabled: @@INTEREST_ENABLED@@
  interest_threshold: @@INTEREST_THRESHOLD@@
  interest_cooldown_seconds: @@INTEREST_COOLDOWN_SECONDS@@
  interest_cooldown_penalty: @@INTEREST_COOLDOWN_PENALTY@@
  interest_question_bonus: @@INTEREST_QUESTION_BONUS@@
  interest_memory_bonus: @@INTEREST_MEMORY_BONUS@@
  interest_jitter: @@INTEREST_JITTER@@
  interest_min_length: @@INTEREST_MIN_LENGTH@@

# ---------- 对话窗口 ----------
window:
  recent_turns: @@WINDOW_RECENT_TURNS@@

# ---------- QQ 接入（OneBot v11，可选） ----------
onebot:
  ws_url: @@ONEBOT_WS_URL@@
  access_token: @@ONEBOT_TOKEN@@
  at_sender_on_reply: @@ONEBOT_AT_SENDER@@
  max_concurrent_replies: @@ONEBOT_MAX_CONCURRENT@@

# ---------- Telegram 接入（可选） ----------
telegram:
  bot_token: ${TELEGRAM_BOT_TOKEN}
  poll_timeout: @@TG_POLL_TIMEOUT@@
  reply_to_message: @@TG_REPLY_TO_MESSAGE@@

# ---------- 人格漂移检测 ----------
drift:
  enabled: @@DRIFT_ENABLED@@
  min_samples: 4
  sample_size: 12
  adherence_threshold: 6.0

# ---------- 表情包（旁观收集 → 学习分类 → 聊天时按分类发图） ----------
sticker:
  enabled: @@STICKER_ENABLED@@
  collect_max_kb: @@STICKER_MAX_KB@@
  send_cooldown_seconds: @@STICKER_SEND_COOLDOWN@@
  tag_batch: @@STICKER_TAG_BATCH@@
  vision:
    # 视觉理解可选：model 留空 = 纯语境学习（零额外成本）
    base_url: @@VISION_BASE_URL@@
    api_key: ${SILICONFLOW_API_KEY}
    model: @@VISION_MODEL@@

# 模拟器使用的群 ID
sim_group_id: @@SIM_GROUP_ID@@
"""


def _yml(value) -> str:
    """标量/列表渲染成单行合法 YAML（safe_dump 负责正确的引号与转义）。

    safe_dump 对根级标量会附加文档结束标记 "..."，需剥掉。"""
    out = yaml.safe_dump(value, allow_unicode=True, default_flow_style=True,
                         width=10**6)
    out = "\n".join(line for line in out.splitlines()
                    if line.strip() != "...").strip()
    return out


def values_from_config(cfg) -> dict:
    """当前配置 + 运行模式 → 表单值（扁平键值对，含界面未展示的高级项）。"""
    return {
        "PERSONA_FILE": cfg.persona_file,
        "DATABASE": cfg.database,
        "SIM_GROUP_ID": cfg.sim_group_id,
        "CHAT_KEY": cfg.llm.api_key,
        "CHAT_BASE_URL": cfg.llm.base_url,
        "CHAT_MODEL": cfg.llm.model,
        "CHAT_TEMPERATURE": cfg.llm.temperature,
        "CHAT_MAX_TOKENS": cfg.llm.max_tokens,
        "EXT_BASE_URL": cfg.extractor.base_url,
        "EXT_MODEL": cfg.extractor.model,
        "EXT_TEMPERATURE": cfg.extractor.temperature,
        "EXT_MAX_TOKENS": cfg.extractor.max_tokens,
        "EMBED_PROVIDER": cfg.embedding.provider,
        "EMBED_KEY": cfg.embedding.api.api_key,
        "EMBED_BASE_URL": cfg.embedding.api.base_url,
        "EMBED_MODEL": cfg.embedding.api.model,
        "EMBED_DIM": cfg.embedding.api.dim,
        "LOCAL_MODEL": cfg.embedding.local.model_name,
        "LOCAL_DIM": cfg.embedding.local.dim,
        "LOCAL_DEVICE": cfg.embedding.local.device,
        "VISION_BASE_URL": cfg.sticker.vision.base_url,
        "VISION_MODEL": cfg.sticker.vision.model,
        "REPLY_ON_MENTION": cfg.behavior.reply_on_mention,
        "NAMES": cfg.behavior.names,
        "REPLY_ON_QUESTION": cfg.behavior.reply_on_question,
        "REPLY_PROBABILITY": cfg.behavior.reply_probability,
        "INTEREST_ENABLED": cfg.behavior.interest_enabled,
        "INTEREST_THRESHOLD": cfg.behavior.interest_threshold,
        "INTEREST_COOLDOWN_SECONDS": cfg.behavior.interest_cooldown_seconds,
        "INTEREST_COOLDOWN_PENALTY": cfg.behavior.interest_cooldown_penalty,
        "INTEREST_QUESTION_BONUS": cfg.behavior.interest_question_bonus,
        "INTEREST_MEMORY_BONUS": cfg.behavior.interest_memory_bonus,
        "INTEREST_JITTER": cfg.behavior.interest_jitter,
        "INTEREST_MIN_LENGTH": cfg.behavior.interest_min_length,
        "WINDOW_RECENT_TURNS": cfg.window.recent_turns,
        "ONEBOT_WS_URL": cfg.onebot.ws_url,
        "ONEBOT_TOKEN": cfg.onebot.access_token,
        "ONEBOT_AT_SENDER": cfg.onebot.at_sender_on_reply,
        "ONEBOT_MAX_CONCURRENT": cfg.onebot.max_concurrent_replies,
        "TELEGRAM_TOKEN": cfg.telegram.bot_token,
        "TG_POLL_TIMEOUT": cfg.telegram.poll_timeout,
        "TG_REPLY_TO_MESSAGE": cfg.telegram.reply_to_message,
        "DRIFT_ENABLED": cfg.drift.enabled,
        "STICKER_ENABLED": cfg.sticker.enabled,
        "STICKER_MAX_KB": cfg.sticker.collect_max_kb,
        "STICKER_SEND_COOLDOWN": cfg.sticker.send_cooldown_seconds,
        "STICKER_TAG_BATCH": cfg.sticker.tag_batch,
        "INGEST_WINDOW_SIZE": cfg.ingest.window_size,
        "INGEST_MIN_INTERVAL": cfg.ingest.min_interval_seconds,
        "INGEST_DEDUP": cfg.ingest.dedup_threshold,
        "INGEST_NOISE_BELOW": cfg.ingest.noise_importance_below,
        "CONSOL_INTERVAL": cfg.consolidation.interval_minutes,
        "CONSOL_MIN_AGE": cfg.consolidation.min_age_hours,
        "CONSOL_MIN_IMPORTANCE": cfg.consolidation.min_importance,
        "CONSOL_CLUSTER_SIM": cfg.consolidation.cluster_similarity,
        "CONSOL_MIN_CLUSTER": cfg.consolidation.min_cluster_size,
        "CONSOL_SUPERSEDE_TOPK": cfg.consolidation.supersede_check_topk,
        "RECALL_TOP_K": cfg.recall.top_k,
        "RECALL_QUERY_RECENT": cfg.recall.query_recent_messages,
        "RECALL_BM25_CAND": cfg.recall.bm25_candidates,
        "RECALL_VEC_CAND": cfg.recall.vector_candidates,
        "RECALL_ENT_CAND": cfg.recall.entity_candidates,
        "RECALL_STAGING_MIN_AGE": cfg.recall.staging_min_age_hours,
        "W_SIMILARITY": cfg.recall.weights.similarity,
        "W_BM25": cfg.recall.weights.bm25,
        "W_RETENTION": cfg.recall.weights.retention,
        "W_ACCESS": cfg.recall.weights.access,
        "W_IMPORTANCE_EMOTION": cfg.recall.weights.importance_emotion,
        "DECAY_INITIAL_BASE": cfg.decay.initial_base_days,
        "DECAY_REINFORCE": cfg.decay.reinforce_factor,
        "DECAY_CAP": cfg.decay.strength_cap_days,
    }


def render_config(values: dict) -> str:
    """表单值 → config.yaml 文本。缺任一占位符键直接报错（防拼错键名）。"""
    out = _YAML_TEMPLATE
    for key, val in values.items():
        out = out.replace(f"@@{key}@@", _yml(val))
    if "@@" in out:
        leftover = out[out.index("@@"):out.index("@@") + 40]
        raise ValueError(f"配置值缺少字段：{leftover}")
    return out


# 界面表单实际编辑的键；保存时只有这些键以界面为准，
# 其余（ingest/consolidation/recall/decay 等高级调参）从磁盘上的最新配置重读，
# 避免"手改 yaml → GUI 保存"时把手工修改冲掉。
FORM_KEYS = frozenset({
    "PERSONA_FILE", "DATABASE", "SIM_GROUP_ID",
    "CHAT_KEY", "CHAT_BASE_URL", "CHAT_MODEL", "CHAT_TEMPERATURE", "CHAT_MAX_TOKENS",
    "EXT_BASE_URL", "EXT_MODEL", "EXT_TEMPERATURE", "EXT_MAX_TOKENS",
    "EMBED_PROVIDER", "EMBED_KEY", "EMBED_BASE_URL", "EMBED_MODEL", "EMBED_DIM",
    "LOCAL_MODEL", "LOCAL_DIM", "LOCAL_DEVICE",
    "VISION_BASE_URL", "VISION_MODEL",
    "REPLY_ON_MENTION", "NAMES", "REPLY_ON_QUESTION", "REPLY_PROBABILITY",
    "INTEREST_ENABLED", "INTEREST_THRESHOLD", "INTEREST_COOLDOWN_SECONDS",
    "INTEREST_COOLDOWN_PENALTY", "INTEREST_QUESTION_BONUS", "INTEREST_MEMORY_BONUS",
    "INTEREST_JITTER", "INTEREST_MIN_LENGTH",
    "WINDOW_RECENT_TURNS",
    "ONEBOT_WS_URL", "ONEBOT_TOKEN",
    "TELEGRAM_TOKEN", "TG_POLL_TIMEOUT", "TG_REPLY_TO_MESSAGE",
    "DRIFT_ENABLED",
    "STICKER_ENABLED", "STICKER_MAX_KB",
})


def merge_config_values(cfg, posted: dict) -> dict:
    """磁盘最新配置为底，叠加界面提交的表单键 → 完整渲染值。"""
    values = values_from_config(cfg)
    for key, val in (posted or {}).items():
        if key in FORM_KEYS:
            values[key] = val
    return values


def read_env(path: Path) -> dict[str, str]:
    """解析 .env 的 KEY=VALUE（跳过注释与空行）。"""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def write_env(path: Path, updates: dict[str, str]) -> None:
    """更新 .env 中的指定键：已有行原位替换，缺失键追加；其余内容原样保留。
    同时同步 os.environ，使随后的 load_config 立即读到新值（无需重启进程）。"""
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in updates:
                lines[i] = f"{key}={updates[key]}"
                seen.add(key)
    if len(seen) < len(updates):
        lines.append("")
        lines.append("# —— 由图形控制台写入 ——")
        for key, val in updates.items():
            if key not in seen:
                lines.append(f"{key}={val}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for key, val in updates.items():
        os.environ[key] = val
