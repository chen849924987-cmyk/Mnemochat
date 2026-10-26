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
    reply_on_question: bool = False
    reply_probability: float = 0.15
    # 兴趣度（@/叫名字必回之外，按兴趣分决定是否接话；纯本地评分）
    interest_enabled: bool = True
    interest_threshold: float = 45          # 0-100，达到才回
    interest_cooldown_seconds: float = 180  # 刚回复过则兴致下降
    interest_cooldown_penalty: float = 40
    interest_question_bonus: float = 20
    interest_memory_bonus: float = 25       # 提到机器人记得的人/事
    interest_jitter: float = 15             # 情绪抖动上限（时接时不接）
    interest_min_length: int = 6            # 过短消息（哈哈/6）不构成话题


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


class StickerVisionConfig(BaseModel):
    """视觉理解（可选）：给表情包打画面描述。

    use_chat=True 时直接用聊天模型看图（聊天模型本身多模态时开启），
    base_url/api_key/model 三项忽略；聊天模型实际不支持图片时
    打标自动退回纯语境学习（describe 异常被 tagger 捕获）。
    use_chat=False 且 model 留空 = 纯语境学习（零额外成本）。
    """

    model_config = ConfigDict(extra="ignore")

    use_chat: bool = False
    base_url: str = "https://api.siliconflow.cn/v1"
    api_key: str = ""
    model: str = ""    # 例：Qwen/Qwen2.5-VL-32B-Instruct；use_chat=False 且留空 = 不用视觉


class StickerConfig(BaseModel):
    """表情包：旁观收集群友的表情包，学习分类，聊天时按分类发图。"""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    database: str = ""                # 共享表情包库路径；留空=随记忆库
    collect_max_kb: int = 800         # 超过该大小的图视为照片，不入表情库
    retain_days: float = 90           # 遗忘：群里 N 天没出现的梗删除文件+记录；0=永不清理
    send_cooldown_seconds: float = 300  # 发图冷却，防刷屏
    tag_batch: int = 8                # 每轮巩固打标的表情包数量
    vision: StickerVisionConfig = StickerVisionConfig()


class SocialVisit(BaseModel):
    """一条串门关系：guest 角色卡定期到主卡所在的模拟群露面。

    database 留空 = 客人与主卡共用记忆库（只是换人格出场）；
    填路径 = 客人有自己的记忆宇宙，串门经历记在它自己的库里。"""

    model_config = ConfigDict(extra="ignore")

    persona_file: str
    database: str = ""
    every_hours: float = 8


class SocialConfig(BaseModel):
    """社交与串门：多角色卡互访，长出"活人交际"。"""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    visits: list[SocialVisit] = Field(default_factory=list)
    host_replies: bool = True   # 客人发言后，主卡是否接话（产生真实互动）


class BotConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    persona_file: str = "personas/default.yaml"
    database: str = "data/memory.db"
    # 每张角色卡独立记忆库：开启后 database 自动变为 data/memory_<卡名>.db，
    # 表情包库仍共享（sticker.database 留空时自动 data/stickers_shared.db）——
    # "人格是它自己长的，记忆是它自己过的日子，梗是大家共有的"
    per_persona_memory: bool = False
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
    sticker: StickerConfig = StickerConfig()
    social: SocialConfig = SocialConfig()
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
    return _apply_per_persona_memory(BotConfig.model_validate(_interpolate_env(raw)))


def _apply_per_persona_memory(cfg: BotConfig) -> BotConfig:
    """记忆隔离派生：per_persona_memory 开启时，每个角色卡自动使用
    data/memory_<卡名>.db，表情包集中到共享库（未显式配置时 data/stickers_shared.db）。"""
    if not cfg.per_persona_memory:
        return cfg
    db = Path(cfg.database)
    stem = Path(cfg.persona_file).stem
    cfg.database = str(db.parent / f"memory_{stem}.db")
    if not cfg.sticker.database:
        cfg.sticker.database = str(db.parent / "stickers_shared.db")
    return cfg
