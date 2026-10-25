"""Telegram 适配器：纯解析逻辑（不触网）。"""

from adapter.telegram import parse_update


def _update(text=None, chat_type="supergroup", chat_id=-100123, key="message", **over):
    message = {
        "message_id": 42,
        "chat": {"id": chat_id, "type": chat_type, "title": "测试群"},
        "from": {"id": 777, "first_name": "小明", "username": "xiaoming"},
    }
    if text is not None:
        message["text"] = text
    message.update(over)
    return {"update_id": 99, key: message}


def test_parse_group_text():
    gm = parse_update(_update("今晚吃什么"), bot_username=None)
    assert gm is not None
    assert gm.group_id == "-100123" and gm.user_id == "777"
    assert gm.user_name == "小明"
    assert gm.text == "今晚吃什么" and not gm.mentioned_bot
    assert gm.message_id == "42"


def test_parse_mention_stripped():
    gm = parse_update(_update("@my_bot 今晚吃什么"), bot_username="my_bot")
    assert gm.mentioned_bot is True
    assert gm.text == "今晚吃什么"


def test_private_chat_ignored():
    assert parse_update(_update("hi", chat_type="private"), "my_bot") is None


def test_non_text_ignored():
    assert parse_update(_update(), "my_bot") is None  # 无 text/caption


def test_caption_used_as_text():
    gm = parse_update(_update(key="message", caption="看这张图", text=None),
                      bot_username=None)
    assert gm is not None and gm.text == "看这张图"


def test_edited_message_supported():
    gm = parse_update(_update("改一下", key="edited_message"), bot_username=None)
    assert gm is not None and gm.text == "改一下"
