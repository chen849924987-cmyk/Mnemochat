"""人格参数渲染与角色卡热重载。"""

import pytest
from pydantic import ValidationError

from chat.pipeline import BotPipeline
from persona.card import PersonaCard


def test_parameters_rendered_into_system_prompt(tmp_path):
    card = PersonaCard(name="大肥鱼", identity="你是大肥鱼",
                       parameters={"傲娇": 8, "摸鱼": 12.5})
    prompt = card.build_system_prompt("g1", 1_000_000.0)
    assert "【性格参数】" in prompt
    assert "傲娇：8/10" in prompt
    assert "摸鱼：10/10" in prompt        # 超界钳制
    assert "绝不向用户报数字" in prompt


def test_bad_parameter_value_rejected_at_load():
    # 坏值在加载时就被 pydantic 拒绝，进不了系统
    with pytest.raises(ValidationError):
        PersonaCard(name="x", parameters={"毒舌": "坏值"})


def test_greeting_and_default_card_load():
    card = PersonaCard.load_yaml("personas/dafeiyu.yaml")
    assert card.name == "大肥鱼"
    assert "呜哇" in card.greeting
    assert card.parameters["摸鱼"] >= 9
    prompt = card.build_system_prompt("g1", 0.0)
    assert "是蓝鲸" in prompt or "蓝鲸" in prompt


def test_deepseek_whale_card_loads():
    card = PersonaCard.load_yaml("personas/deepseek_whale.yaml")
    assert card.name == "小鲸鱼"
    prompt = card.build_system_prompt("g1", 0.0)
    # 核心要素齐全：人格锚定 + 去AI味黑名单 + 滑条参数
    assert "不是客服" in prompt
    assert "AI味黑名单" in prompt
    assert "傲娇：9/10" in prompt
    assert "一行" in prompt
    # 漂移检测用的黑名单也在卡上
    assert "首先" in card.ai_flavor_blocklist and len(card.ai_flavor_blocklist) >= 5


async def test_check_command_offline(tmp_path, cfg, capsys):
    """--check 体检：离线项全部通过。"""
    from main import _cmd_check

    cfg.database = str(tmp_path / "check.db")
    failures = await _cmd_check(cfg, mock=True)
    assert failures == 0
    out = capsys.readouterr().out
    assert "角色卡" in out and "数据库" in out and "全部通过" in out


async def test_persona_hot_reload(store, chat, embedder, cfg, clock, tmp_path):
    """修改器保存角色卡后，运行中的机器人下一条消息即生效。"""
    card_file = tmp_path / "persona.yaml"
    card_file.write_text(
        "name: 甲\nidentity: 你是甲，口头禅是 pineapple\n", encoding="utf-8"
    )
    persona = PersonaCard.load_yaml(card_file)
    pipe = BotPipeline(store, chat, embedder, persona, cfg, clock,
                       persona_file=card_file)

    await pipe.on_user_message("g1", "小明", "小明", "你好呀", force_reply=True)
    assert "pineapple" in chat.last_chat_messages[0]["content"]

    # 修改器保存了新卡
    card_file.write_text(
        "name: 乙\nidentity: 你是乙，口头禅是 coconut\n", encoding="utf-8"
    )
    await pipe.on_user_message("g1", "小明", "小明", "又见面了", force_reply=True)
    assert "coconut" in chat.last_chat_messages[0]["content"]
    assert "pineapple" not in chat.last_chat_messages[0]["content"]
    assert pipe.persona.name == "乙"
