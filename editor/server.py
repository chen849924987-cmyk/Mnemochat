"""人格卡修改器：本地网页 UI（Python 标准库实现，零额外依赖）。

  python main.py --edit-persona                 # 编辑 config 指向的角色卡
  python main.py --edit-persona personas/大肥鱼.yaml --port 8766

能力：
- 编辑人格卡全部字段；性格参数以 0-10 滑条调节
- 导入 SillyTavern（酒馆）V1/V2/V3 角色卡（PNG 或 JSON）
- 实时预览最终注入的 system prompt
- 保存即写入 yaml（首次自动备份 .bak）；运行中的机器人下一条消息热重载生效
"""

from __future__ import annotations

import base64
import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from persona.card import PersonaCard
from persona.convert import extract_card_from_png, st_card_to_persona

_PAGE = Path(__file__).resolve().parent / "page.html"


class _State:
    def __init__(self, card_path: Path) -> None:
        self.card_path = card_path
        self.backed_up = False


def _load_persona(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_persona(state: _State, data: dict) -> None:
    if not state.backed_up and state.card_path.exists():
        bak = state.card_path.with_suffix(state.card_path.suffix + ".bak")
        bak.write_bytes(state.card_path.read_bytes())
        state.backed_up = True
    state.card_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state.card_path.with_suffix(state.card_path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    tmp.replace(state.card_path)


def make_handler(state: _State) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # 静默访问日志
            pass

        def _json(self, obj: dict, code: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/index.html"):
                body = _PAGE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/persona":
                try:
                    self._json({"ok": True, "path": str(state.card_path),
                                "persona": _load_persona(state.card_path)})
                except FileNotFoundError:
                    self._json({"ok": False,
                                "error": f"角色卡不存在：{state.card_path}"}, 404)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._json({"ok": False, "error": "请求体不是合法 JSON"}, 400)

            if self.path == "/api/persona":
                try:
                    PersonaCard.model_validate(payload)   # 保存前校验
                    _save_persona(state, payload)
                    self._json({"ok": True})
                except Exception as e:
                    self._json({"ok": False, "error": f"校验/保存失败：{e}"}, 400)
            elif self.path == "/api/preview":
                try:
                    card = PersonaCard.model_validate(payload)
                    self._json({"ok": True, "system_prompt":
                                card.build_system_prompt("预览群", time.time())})
                except Exception as e:
                    self._json({"ok": False, "error": str(e)}, 400)
            elif self.path == "/api/import_card":
                card = None
                try:
                    if payload.get("png_base64"):
                        card = extract_card_from_png(
                            base64.b64decode(payload["png_base64"]))
                    elif isinstance(payload.get("json"), dict):
                        card = payload["json"]
                except Exception:
                    card = None
                if not card:
                    return self._json({"ok": False, "error":
                                       "PNG 中未找到内嵌角色卡（缺 chara/ccv3 块），或 JSON 无效"}, 400)
                self._json({"ok": True, "persona": st_card_to_persona(card)})
            else:
                self.send_response(404)
                self.end_headers()

    return Handler


def run_editor(card_path: Path, port: int = 8765) -> None:
    state = _State(card_path)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    url = f"http://127.0.0.1:{port}"
    print(f"人格卡修改器已启动：{url}")
    print(f"  正在编辑：{card_path}（首次保存自动备份 .bak）")
    print("  保存后运行中的机器人自动热重载；Ctrl+C 退出")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("修改器已关闭。")
