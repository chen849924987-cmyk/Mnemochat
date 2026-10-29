"""SillyTavern 角色卡（V1/V2/V3）导入转换。

SillyTavern 生态的角色卡是内嵌 JSON 的 PNG 图片（tEXt chunk），
V2/V3 规范见：
  https://github.com/malfoyslastname/character-card-spec-v2
  https://github.com/kwaroran/character-card-spec-v3
导入 chub.ai 等站点下载的卡即可直接获得海量现成人格。
"""

from __future__ import annotations

import re

import base64
import json
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _iter_chunks(data: bytes):
    i = len(PNG_SIGNATURE)
    while i + 8 <= len(data):
        length = int.from_bytes(data[i : i + 4], "big")
        ctype = data[i + 4 : i + 8]
        cdata = data[i + 8 : i + 8 + length]
        if len(cdata) < length:
            break
        yield ctype, cdata
        i += 12 + length  # 跳过 CRC


def extract_card_from_png(data: bytes) -> dict | None:
    """读取 PNG 内嵌角色卡：优先 ccv3（V3），其次 chara（V1/V2），base64 JSON。"""
    if not data.startswith(PNG_SIGNATURE):
        return None
    found: dict[str, str] = {}
    for ctype, cdata in _iter_chunks(data):
        try:
            if ctype == b"tEXt":
                null = cdata.find(b"\x00")
                if null < 0:
                    continue
                key = cdata[:null].decode("latin-1")
                found[key] = cdata[null + 1 :].decode("latin-1")
            elif ctype == b"zTXt":
                null = cdata.find(b"\x00")
                if null < 0 or null + 2 > len(cdata):
                    continue
                key = cdata[:null].decode("latin-1")
                found[key] = zlib.decompress(cdata[null + 2 :]).decode("latin-1")
        except (UnicodeDecodeError, zlib.error):
            continue
    for key in ("ccv3", "chara"):
        raw = found.get(key)
        if raw:
            try:
                card = json.loads(base64.b64decode(raw).decode("utf-8"))
                if isinstance(card, dict):
                    return card
            except (ValueError, UnicodeDecodeError):
                continue
    return None


def normalize_st_card(card: dict) -> dict:
    """V1 扁平 / V2 / V3 结构 → 统一扁平字段（data 内字段覆盖顶层）。"""
    data = card.get("data")
    if isinstance(data, dict):
        merged = {k: v for k, v in card.items() if k != "data"}
        merged.update(data)
        return merged
    return dict(card)


def st_card_to_persona(card: dict) -> dict:
    """SillyTavern 卡 → Mnemochat 人格卡（yaml dict 结构）。"""
    m = normalize_st_card(card)

    def g(key: str) -> str:
        return str(m.get(key) or "").strip()

    identity_parts = []
    if g("description"):
        identity_parts.append(g("description"))
    if g("personality"):
        identity_parts.append(f"【性格】{g('personality')}")
    if g("scenario"):
        identity_parts.append(f"【场景背景】{g('scenario')}")

    extra_parts = []
    if g("system_prompt"):
        extra_parts.append(g("system_prompt"))
    if g("post_history_instructions"):
        extra_parts.append(g("post_history_instructions"))

    speech_style = ""
    examples: list[str] = []
    if g("mes_example"):
        raw = str(g("mes_example"))
        # 酒馆卡示例对话按 <START> 或空行分段，取前 6 段作参考对话
        segs = [
            seg.strip().lstrip("<START>").strip()
            for seg in re.split(r"<START>|\n\s*\n", raw)
            if seg.strip()
        ]
        examples = [seg[:300] for seg in segs if seg][:6]
        speech_style = f"参考示例对话的语气说话。示例：{raw[:300]}"

    tags = [str(t) for t in (m.get("tags") or [])][:10]
    notes = "、".join(filter(None, [
        f"导入自 SillyTavern 角色卡" if g("name") else "",
        f"creator: {g('creator')}" if g("creator") else "",
        f"版本: {g('character_version')}" if g("character_version") else "",
        f"标签: {'/'.join(tags)}" if tags else "",
    ]))

    return {
        "name": g("name") or "未命名",
        "identity": "\n\n".join(identity_parts) or "（导入卡未提供描述）",
        "speech_style": speech_style,
            "examples": examples,
        "catchphrases": [],
        "memory_stance": "",   # 留空 → 使用默认记忆使用准则
        "boundary": {"cross_group": False, "include_self": True},
        "system_extra": "\n\n".join(extra_parts),
        "parameters": {},      # 数值参数导入后由修改器滑条自行设定
        "greeting": g("first_mes"),
        "source_notes": notes,
    }
