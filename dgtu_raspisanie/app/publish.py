"""Install the dashboard card and keep its data file up to date."""

import base64
import json
import os
import socket
import struct
import threading
import time
import traceback
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CARD_VERSION = "1.3.0"
RESOURCE_URL = f"/local/dgtu-raspisanie-card.js?v={CARD_VERSION}"
CARD_TYPE = "custom:dgtu-raspisanie-card"
APP_DIR = Path(__file__).resolve().parent
CARD_SOURCE = APP_DIR / "static" / "dgtu-raspisanie-card.js"


def config_dir():
    return Path(os.environ.get("HA_CONFIG", "/homeassistant"))


def www_dir():
    return config_dir() / "www"


def start_background(collect):
    threading.Thread(target=_loop, args=(collect,), name="dgtu-dashboard", daemon=True).start()


def _loop(collect):
    deadline = time.monotonic() + 12 * 60
    placed = Path("/data/card_placed").is_file()
    next_publish = 0.0
    announced = False
    while True:
        if time.monotonic() >= next_publish:
            payload = None
            try:
                install_card()
                payload = collect()
                write_json(payload)
                push_state(payload)
            except Exception:
                traceback.print_exc()
            if payload and payload.get("ok"):
                minutes = int(payload.get("refresh_minutes") or 20)
            else:
                minutes = 2
            next_publish = time.monotonic() + max(1, minutes) * 60
        if not placed and time.monotonic() < deadline:
            try:
                outcome = ensure_dashboard()
                if outcome in ("placed", "exists", "manual"):
                    _mark_placed()
                    placed = True
                    print(f"Карточка на обзоре: {outcome}", flush=True)
            except Exception as exc:
                if not announced:
                    print(f"Сам поставить карточку не удалось ({exc}). Откройте «Расписание» и нажмите «На главный экран».", flush=True)
                    announced = True
        time.sleep(15)


def install_card():
    target_dir = www_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "dgtu-raspisanie-card.js"
    data = CARD_SOURCE.read_bytes()
    if not target.is_file() or target.read_bytes() != data:
        temporary = target.with_suffix(".js.tmp")
        temporary.write_bytes(data)
        temporary.replace(target)
        print(f"Карточка записана в {target}", flush=True)


def write_json(payload):
    target_dir = www_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "dgtu-raspisanie.json"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if target.is_file() and target.read_bytes() == body:
        return
    temporary = target.with_suffix(".json.tmp")
    temporary.write_bytes(body)
    temporary.replace(target)


def push_state(payload):
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        return
    state = _state_text(payload)
    attributes = {
        "friendly_name": "Расписание ДГТУ",
        "icon": "mdi:calendar-clock",
        "title": payload.get("title") or "",
        "university": payload.get("university") or "",
        "today": payload.get("today") or "",
        "status": payload.get("error") or "",
    }
    request = Request(
        "http://supervisor/core/api/states/sensor.dgtu_raspisanie",
        data=json.dumps({"state": state, "attributes": attributes}, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        print(f"Не удалось обновить sensor.dgtu_raspisanie: {exc}", flush=True)


def _state_text(payload):
    if not payload.get("ok"):
        return "нет данных"
    today = payload.get("today")
    day = next((item for item in payload.get("days") or [] if item.get("date") == today), None)
    lessons = (day or {}).get("lessons") or []
    if not lessons:
        return "нет пар"
    return f"{len(lessons)} {_lessons_word(len(lessons))}"[:255]


def _lessons_word(count):
    mod10 = count % 10
    mod100 = count % 100
    if mod10 == 1 and mod100 != 11:
        return "пара"
    if 2 <= mod10 <= 4 and not 12 <= mod100 <= 14:
        return "пары"
    return "пар"


def _mark_placed():
    path = Path("/data/card_placed")
    try:
        path.write_text(CARD_VERSION, encoding="utf-8")
    except OSError:
        pass


def ensure_dashboard():
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        raise RuntimeError("нет токена Home Assistant")
    ws = _connect()
    try:
        hello = json.loads(ws.recv())
        if hello.get("type") != "auth_required":
            raise RuntimeError(f"неожиданный ответ {hello.get('type')}")
        ws.send({"type": "auth", "access_token": token})
        auth = json.loads(ws.recv())
        if auth.get("type") != "auth_ok":
            raise RuntimeError(auth.get("message") or "авторизация не принята")
        _sync_resource(ws)
        result = ws.call({"type": "lovelace/config", "url_path": None})
        config = result.get("result") or {}
        outcome = _insert_card(config)
        if outcome == "placed":
            ws.call({"type": "lovelace/config/save", "url_path": None, "config": config})
        return outcome
    finally:
        ws.close()


def _sync_resource(ws):
    listed = ws.call({"type": "lovelace/resources"})
    resources = listed.get("result") or []
    current = next((item for item in resources if "dgtu-raspisanie-card.js" in (item.get("url") or "")), None)
    if current is None:
        ws.call({"type": "lovelace/resources/create", "url": RESOURCE_URL, "res_type": "module"})
        return
    if current.get("url") != RESOURCE_URL:
        ws.call({
            "type": "lovelace/resources/update",
            "resource_id": current.get("id"),
            "url": RESOURCE_URL,
            "res_type": "module",
        })


def _insert_card(config):
    views = config.get("views") or []
    if not views or config.get("strategy") or views[0].get("strategy"):
        return "manual"
    if _contains(views[0]):
        return "exists"
    card = {"type": CARD_TYPE, "grid_options": {"columns": 12, "rows": "auto"}}
    view = views[0]
    if isinstance(view.get("sections"), list):
        if not view["sections"]:
            view["sections"].append({"type": "grid", "cards": []})
        cards = view["sections"][0].setdefault("cards", [])
        index = 1 if cards and cards[0].get("type") == "heading" else 0
        cards.insert(index, card)
        return "placed"
    cards = view.setdefault("cards", [])
    cards.insert(0, card)
    return "placed"


def _contains(node):
    if not isinstance(node, dict):
        return False
    if node.get("type") == CARD_TYPE:
        return True
    for key in ("cards", "sections", "views"):
        children = node.get(key)
        if isinstance(children, list) and any(_contains(child) for child in children):
            return True
    return False


class _Socket:
    def __init__(self, sock, leftover):
        self.sock = sock
        self.buf = leftover
        self._id = 0

    def send(self, payload):
        _send_frame(self.sock, json.dumps(payload))

    def recv(self):
        return _recv_frame(self)

    def call(self, payload):
        self._id += 1
        payload = dict(payload)
        payload["id"] = self._id
        self.send(payload)
        while True:
            message = json.loads(self.recv())
            if message.get("id") != self._id:
                continue
            if not message.get("success", True):
                error = message.get("error") or {}
                raise RuntimeError(error.get("message") or "запрос отклонён")
            return message

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _connect():
    sock = socket.create_connection(("supervisor", 80), timeout=15)
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        "GET /core/api/websocket HTTP/1.1\r\n"
        "Host: supervisor\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(request.encode())
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > 65536:
            break
    head, _, leftover = data.partition(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0]
    if b" 101 " not in status:
        raise ConnectionError(status.decode("utf-8", "replace"))
    return _Socket(sock, leftover)


def _send_frame(sock, text):
    payload = text.encode("utf-8")
    mask = os.urandom(4)
    header = bytearray([0x81])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    header.extend(mask)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(bytes(header) + masked)


def _recv_exact(ws, size):
    while len(ws.buf) < size:
        chunk = ws.sock.recv(4096)
        if not chunk:
            raise ConnectionError("соединение закрыто")
        ws.buf += chunk
    output, ws.buf = ws.buf[:size], ws.buf[size:]
    return output


def _recv_frame(ws):
    while True:
        first, second = _recv_exact(ws, 2)
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", _recv_exact(ws, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _recv_exact(ws, 8))[0]
        if second & 0x80:
            mask = _recv_exact(ws, 4)
            data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(_recv_exact(ws, length)))
        else:
            data = _recv_exact(ws, length)
        if opcode == 0x9:
            _send_frame(ws.sock, "")
            continue
        if opcode == 0x8:
            raise ConnectionError("сокет закрыт")
        if opcode == 0x1:
            return data.decode("utf-8")
