"""OneBot v11 适配器：纯解析逻辑（不触网）。"""

from adapter.onebot import (
    build_reply_segments,
    cq_to_text,
    extract_self_id,
    parse_group_message,
    segments_to_text,
)


def _event(message, **over):
    event = {
        "post_type": "message",
        "message_type": "group",
        "group_id": 123456,
        "user_id": 654321,
        "message": message,
        "sender": {"card": "小明卡", "nickname": "小明", "user_id": 654321},
    }
    event.update(over)
    return event


def test_parse_group_message_at_bot():
    gm = parse_group_message(
        _event([
            {"type": "at", "data": {"qq": "10001"}},
            {"type": "text", "data": {"text": " 小满，今晚吃什么"}},
        ]),
        self_qq="10001",
    )
    assert gm is not None
    assert gm.group_id == "123456" and gm.user_id == "654321"
    assert gm.user_name == "小明卡"          # 群名片优先于昵称
    assert gm.mentioned_bot is True
    assert "@我" in gm.text and "今晚吃什么" in gm.text


def test_parse_at_others_is_not_mention():
    text, mentioned = segments_to_text(
        [{"type": "at", "data": {"qq": "8888"}},
         {"type": "text", "data": {"text": " 看看这个"}}],
        self_qq="10001",
    )
    assert mentioned is False
    assert "@8888" in text


def test_media_segments_to_placeholders():
    text, _ = segments_to_text(
        [{"type": "image", "data": {"url": "http://x"}},
         {"type": "face", "data": {"id": 4}},
         {"type": "text", "data": {"text": "哈哈"}},
         {"type": "reply", "data": {"id": 9}}],
        self_qq=None,
    )
    assert text == "[图片][表情]哈哈"


def test_private_and_non_message_events_ignored():
    assert parse_group_message(
        _event([{"type": "text", "data": {"text": "hi"}}], message_type="private"),
        self_qq=None,
    ) is None
    assert parse_group_message({"post_type": "notice", "notice_type": "poke"},
                               self_qq=None) is None
    assert parse_group_message(
        {"post_type": "meta_event", "meta_event_type": "heartbeat", "self_id": 10001},
        self_qq=None,
    ) is None


def test_cq_string_fallback():
    text, mentioned = cq_to_text("[CQ:at,qq=10001] 我对花生过敏[CQ:image,file=x.jpg]", "10001")
    assert mentioned is True
    assert "@我" in text and "花生" in text and "[图片]" in text


def test_extract_self_id():
    assert extract_self_id({"echo": "login", "data": {"user_id": 10001}}) == "10001"
    assert extract_self_id(
        {"post_type": "meta_event", "meta_event_type": "lifecycle", "self_id": 10001}
    ) == "10001"
    assert extract_self_id({"echo": "other", "data": {}}) is None
    assert extract_self_id({"post_type": "message"}) is None


def test_build_reply_segments():
    segs = build_reply_segments("记得呀", at_qq="654321", at_sender=True)
    assert segs[0]["type"] == "at" and segs[0]["data"]["qq"] == "654321"
    assert segs[-1]["data"]["text"].endswith("记得呀")
    segs2 = build_reply_segments("好", at_qq="1", at_sender=False)
    assert all(s["type"] == "text" for s in segs2)
