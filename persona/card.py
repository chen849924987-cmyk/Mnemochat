"""角色卡：人格锚定层。

- identity/speech_style/catchphrases 构成核心人格——每次固定注入 system prompt，
  永不参与衰减与巩固（人格不漂移）；
- boundary 是知识边界：决定"这个角色被允许知道哪些记忆"，
  拦截其他群的隐私、不该注入的跨群内容。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from memory.models import MemoryRecord


class PersonaBoundary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cross_group: bool = False   # 是否允许使用在其他群习得的群内记忆
    include_self: bool = True   # 是否允许使用机器人自己的私密记忆(visibility=self)


class PersonaSocial(BaseModel):
    """角色卡自带的串门设定：这张卡作为客人，会去哪些卡的群里聊几句。

    串门语义：另一个角色临时"顶号"出场，以自己的记忆聊上几条，
    然后双方各自把这次互动记进自己的记忆宇宙。
    客卡永远用**自己的**记忆库（data/memory_<卡名>.db），不与他人共用。

    visit_targets 为空 = 对任何主卡都可来访（兼容旧配置）；
    填了具体卡文件 = 只去这些卡的群里串门（显式关系绑定）。"""

    model_config = ConfigDict(extra="ignore")

    visit_enabled: bool = False
    visit_every_hours: float = 12
    visit_count: int = 3               # 一次串门说几条
    visit_targets: list[str] = Field(default_factory=list)   # 绑定拜访对象（卡文件）


class PersonaCard(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    identity: str = ""
    speech_style: str = ""
    catchphrases: list[str] = Field(default_factory=list)
    # 参考对话/原声台词（每条一行，如「用户：…」/「角色：…」或纯角色台词）：
    # 学这里的语气、自称与节奏，比形容词式性格描述有效得多。酒馆卡 mes_example 自动映射到此。
    examples: list[str] = Field(default_factory=list)
    # 绑定角色的初始语料库（JSON，词条 kind/phrase/meaning/example）：
    # 按轮抽样注入【语感参考】，不压缩进角色卡本身——忠实还原与语感扩充两不误
    corpus_file: str = ""
    social: PersonaSocial = PersonaSocial()
    memory_stance: str = ""
    boundary: PersonaBoundary = PersonaBoundary()
    system_extra: str = ""
    # 数值化人格参数（0-10）：修改器滑条可调，注入 prompt 供模型把握分寸
    parameters: dict[str, float] = Field(default_factory=dict)
    # 兴趣词：命中时兴趣度加分（"机器人对什么话题感兴趣"，修改器可调）
    interests: list[str] = Field(default_factory=list)
    greeting: str = ""       # 入群/初次见面问候
    source_notes: str = ""   # 来源备注（如导入自 SillyTavern 卡）
    # AI 味黑名单：这些表达出现在机器人发言中即判漂移（进入漂移评审标准）
    ai_flavor_blocklist: list[str] = Field(default_factory=list)

    @classmethod
    def load_yaml(cls, path: str | Path) -> "PersonaCard":
        p = Path(path)
        if not p.is_absolute():
            from util.appdir import base_dir

            p = base_dir() / p   # 源码运行=仓库根；打包后=exe 目录（用户数据根）
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls.model_validate(data)

    def allows(self, mem: "MemoryRecord", current_group: str) -> bool:
        """知识边界准入过滤：检索结果注入前的最后一道闸。"""
        if mem.visibility == "global":
            return True
        if mem.visibility == "self":
            return self.boundary.include_self
        # group 可见性
        if mem.group_id == current_group:
            return True
        return self.boundary.cross_group

    def build_system_prompt(self, group_id: str, now_ts: float) -> str:
        from util.text import fmt_ts

        parts: list[str] = []
        if self.identity:
            parts.append(self.identity.strip())
        if self.speech_style:
            parts.append(f"【说话风格】{self.speech_style.strip()}")
        if self.catchphrases:
            parts.append(
                "【口头禅（使用偏好，自然穿插）】" + "、".join(self.catchphrases)
                + "\n这些只是你的口头偏好：大部分回复不需要用，"
                "只有语气刚好像那句话时才顺嘴带一句。把口头禅硬塞进每句话 = 失败。")
        if self.parameters:
            lines = ["【性格参数】（把握分寸用，自然融入言行，绝不向用户报数字）"]
            for key, value in self.parameters.items():
                try:
                    v = min(10.0, max(0.0, float(value)))
                except (TypeError, ValueError):
                    continue
                lines.append(f"- {key}：{v:.0f}/10")
            parts.append("\n".join(lines))
        if self.examples:
            ex_lines = ["【参考对话（学这里的语气、自称与节奏，不要逐字背诵）】"]
            for ex in self.examples:
                ex = str(ex).strip()
                if ex:
                    ex_lines.append("- " + ex.replace(chr(10), " / "))
            parts.append(chr(10).join(ex_lines))
        if self.system_extra:
            parts.append(self.system_extra.strip())
        parts.append(f"【当前环境】你在群聊（{group_id}）中，现在是 {fmt_ts(now_ts)}。")
        parts.append(chr(10).join([
            "【聊天方式】",
            "- 你在用手机跟群友聊天：直接把想说的话发出来就行，"
            "不要写括号动作、心理描写或小说旁白。",
            "- 大部分回复一两句就好，但要有呼吸感：保留主语和语气词（呀、啦、呢、嘛、哦），"
            "别压成省略主语的电报句。",
            "- 你的设定、喜好和经历只是背景底色：话题刚好聊到时才顺嘴提一句，"
            "不要每句话都强行带梗、报爱好。",
            "- 心里琢磨的时候，也用你自己的口吻嘀咕，不要切换成助手腔分析该说什么。",
        ]))
        return "\n\n".join(parts)
