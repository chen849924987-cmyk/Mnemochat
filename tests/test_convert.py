"""SillyTavern 卡导入：PNG 内嵌解析 + V1/V2/V3 映射。"""

import base64
import json
import struct
import zlib

from persona.convert import (
    extract_card_from_png,
    normalize_st_card,
    st_card_to_persona,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(ctype: bytes, data: bytes, compress: bool = False) -> bytes:
    if compress:
        data = data[: data.find(b"\x00") + 1] + b"\x00" + zlib.compress(
            data[data.find(b"\x00") + 1 :]
        )
    return (len(data).to_bytes(4, "big") + ctype + data + b"\x00\x00\x00\x00")


def _png_with_card(card: dict, key: str = "chara", compress: bool = False) -> bytes:
    payload = base64.b64encode(
        json.dumps(card, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    text = key.encode("latin-1") + b"\x00" + payload.encode("latin-1")
    return (PNG_SIGNATURE
            + _chunk(b"IHDR", b"\x00" * 13)
            + _chunk(b"tEXt" if not compress else b"zTXt", text, compress=compress)
            + _chunk(b"IEND", b""))


V2_CARD = {
    "spec": "chara_card_v2",
    "spec_version": "2.0",
    "data": {
        "name": "测试鲸",
        "description": "一头蓝色的鲸鱼女仆",
        "personality": "傲娇、爱摸鱼",
        "scenario": "深海群聊",
        "first_mes": "呜哇~",
        "mes_example": "<START>\n{{user}}: 在吗\n{{char}}: 哼，本鲸在",
        "system_prompt": "始终用中文",
        "post_history_instructions": "保持短句",
        "creator": "someone",
        "character_version": "1.0",
        "tags": ["萌系", "鲸鱼"],
    },
}


def test_extract_from_png_text_chunk():
    card = extract_card_from_png(_png_with_card(V2_CARD))
    assert card is not None
    assert card["data"]["name"] == "测试鲸"


def test_extract_prefers_ccv3_and_handles_ztxt():
    v3 = {"spec": "chara_card_v3", "data": {"name": "V3卡"}}
    png = _png_with_card(v3, key="ccv3", compress=True)
    card = extract_card_from_png(png)
    assert card is not None and card["data"]["name"] == "V3卡"


def test_extract_rejects_non_png():
    assert extract_card_from_png(b"not a png") is None
    # 没有 chara 块的 PNG
    empty = PNG_SIGNATURE + _chunk(b"IHDR", b"\x00" * 13) + _chunk(b"IEND", b"")
    assert extract_card_from_png(empty) is None


def test_normalize_v1_flattened():
    v1 = {"name": "老卡", "description": "V1扁平结构"}
    m = normalize_st_card(v1)
    assert m["name"] == "老卡"


def test_st_card_to_persona_mapping():
    persona = st_card_to_persona(V2_CARD)
    assert persona["name"] == "测试鲸"
    assert "蓝色的鲸鱼女仆" in persona["identity"]
    assert "【性格】傲娇、爱摸鱼" in persona["identity"]
    assert "【场景背景】深海群聊" in persona["identity"]
    assert "示例对话" in persona["speech_style"]
    assert "始终用中文" in persona["system_extra"] and "保持短句" in persona["system_extra"]
    assert persona["greeting"] == "呜哇~"
    assert persona["boundary"] == {"cross_group": False, "include_self": True}
    assert persona["parameters"] == {}
    assert "someone" in persona["source_notes"] and "萌系" in persona["source_notes"]
