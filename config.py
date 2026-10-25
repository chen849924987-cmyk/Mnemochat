"""配置加载：config.yaml + .env 密钥插值。

约定：
- 仓库里提交 config.example.yaml，真实配置 config.yaml 被 gitignore；
- yaml 中 `${VAR}` 引用环境变量（.env 自动加载），适合放密钥。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    temperature: float = 0.8
    max_tokens: int = 1024


class ExtractorConfig(LLMConfig):
    temperature: float = 0.2
    max_tokens: int = 2048


class EmbedAPIConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    base_url: str = "https://api.siliconflow.cn/v1"
    api_key: str = ""
    model: str = "BAAI/bge-m3"
    dim: int = 1024
    batch_size: int = 32


class EmbedLocalConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model_name: str = "BAAI/bge-small-zh-v1.5"
    dim: int = 512
    device: str = "cpu"


class EmbeddingConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: Literal["api", "local"] = "api"
    api: EmbedAPIConfig = EmbedAPIConfig()
    local: EmbedLocalConfig = EmbedLocalConfig()


class IngestConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    window_size: int = 8
    min_interval_seconds: float = 20
    dedup_threshold: float = 0.92
    noise_importance_below: int = 3


class ConsolidationConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    interval_minutes: float = 30
    min_age_hours: float = 24
    min_importance: float = 5
    cluster_similarity: float = 0.80
    min_cluster_size: int = 2
    supersede_check_topk: int = 3


class RecallWeights(BaseModel):
    model_config = ConfigDict(extra="ignore")

    similarity: float = 0.35
    bm25: float = 0.25
    retention: float = 0.20
    access: float = 0.10
    importance_emotion: float = 0.10


class RecallConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    top_k: int = 12
    query_recent_messages: int = 5
    bm25_candidates: int = 50
    vector_candidates: int = 50
    entity_candidates: int = 20
    staging_min_age_hours: float = 6
    weights: RecallWeights = RecallWeights()


class DecayConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    initial_base_days: float = 2.0
    reinforce_factor: float = 1.25
    strength_cap_days: float = 365.0


class BehaviorConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reply_on_mention: bool = True
    names: list[str] = Field(default_factory=list)
    reply_probability: float = 0.15
    reply_on_question: bool = False


class WindowConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    recent_turns: int = 20


class OnebotConfig(BaseModel):
    """QQ OneBot v11（NapCat / LLOneBot / Lagrange）正向 WebSocket。"""

    model_config = ConfigDict(extra="ignore")

    ws_url: str = "ws://127.0.0.1:3001"
    access_token: str = ""
    at_sender_on_reply: bool = True
    max_concurrent_replies: int = 4


class TelegramConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    bot_token: str = ""
    poll_timeout: int = 30          # getUpdates 长轮询秒数
    reply_to_message: bool = False  # 回复时是否引用原消息


class DriftConfig(BaseModel):
    """人格漂移检测：巩固时抽样评审机器人近期发言与角色卡的一致性。"""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    min_samples: int = 4            # 样本不足则跳过本轮检测
    sample_size: int = 12           # 每次抽样的机器人发言条数
    adherence_threshold: float = 6.0  # 低于该贴合度判为漂移


class BotConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    persona_file: str = "personas/default.yaml"
    database: str = "data/memory.db"
    llm: LLMConfig = LLMConfig()
    extractor: ExtractorConfig = ExtractorConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    ingest: IngestConfig = IngestConfig()
    consolidation: ConsolidationConfig = ConsolidationConfig()
    recall: RecallConfig = RecallConfig()
    decay: DecayConfig = DecayConfig()
    behavior: BehaviorConfig = BehaviorConfig()
    window: WindowConfig = WindowConfig()
    onebot: OnebotConfig = OnebotConfig()
    telegram: TelegramConfig = TelegramConfig()
    drift: DriftConfig = DriftConfig()
    sim_group_id: str = "sim-main"


def _interpolate_env(value):
    """递归把 ${VAR} 替换为环境变量值；未定义则替换为空串。"""
    if isinstance(value, str):
        return _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(v) for v in value]
    return value


def load_config(path: str | Path = "config.yaml", root: Path | None = None) -> BotConfig:
    """加载配置。path 相对 root（默认仓库根目录）。"""
    root = root or Path(__file__).resolve().parent
    load_dotenv(root / ".env")

    cfg_path = Path(path)
    if not cfg_path.is_absolute():
        cfg_path = root / cfg_path
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"配置文件不存在：{cfg_path}\n请先复制 config.example.yaml 为 config.yaml 并填写"
        )
    with open(cfg_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return BotConfig.model_validate(_interpolate_env(raw))
