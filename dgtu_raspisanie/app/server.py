#!/usr/bin/env python3
"""Timetable server for the DGTU Home Assistant app."""

import json
import os
import ssl
import threading
import time
import traceback
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

APP_DIR = Path(__file__).resolve().parent
INDEX_PATH = APP_DIR / "static" / "index.html"
CARD_PATH = APP_DIR / "static" / "dgtu-raspisanie-card.js"
PREVIEW_PATH = APP_DIR / "static" / "preview.html"
OPTIONS_PATH = Path(os.environ.get("OPTIONS_PATH", "/data/options.json"))
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8099"))
TZ = ZoneInfo("Europe/Moscow")
USER_AGENT = "HomeAssistant-DGTU-Raspisanie/1.0"
LIST_TTL = 6 * 60 * 60

UNIVERSITIES = {
    "ДГТУ": ("https://edu.donstu.ru/api", "ДГТУ"),
    "ПИ": ("https://edu-tpi.donstu.ru/api", "ПИ ДГТУ"),
    "ПИ ДГТУ": ("https://edu-tpi.donstu.ru/api", "ПИ ДГТУ"),
}
TARGETS = {
    "Группа": "group",
    "группа": "group",
    "group": "group",
    "Преподаватель": "teacher",
    "преподаватель": "teacher",
    "teacher": "teacher",
}
WEEKDAYS = (
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
)
WEEKDAYS_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
KIND_RULES = (
    ("лекция", "lec", "Лекция"),
    ("лек.", "lec", "Лекция"),
    ("лек ", "lec", "Лекция"),
    ("лабораторная", "lab", "Лабораторная"),
    ("лаб.", "lab", "Лабораторная"),
    ("лаб ", "lab", "Лабораторная"),
    ("практика", "prac", "Практика"),
    ("пр.", "prac", "Практика"),
    ("пр ", "prac", "Практика"),
    ("семинар", "sem", "Семинар"),
    ("сем.", "sem", "Семинар"),
    ("сем ", "sem", "Семинар"),
    ("консультация", "cons", "Консультация"),
    ("конс.", "cons", "Консультация"),
    ("экзамен", "exam", "Экзамен"),
    ("экз.", "exam", "Экзамен"),
    ("зачет", "exam", "Зачёт"),
    ("зачёт", "exam", "Зачёт"),
    ("зач.", "exam", "Зачёт"),
)
HOMOGLYPHS = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "х": "x",
        "к": "k",
        "м": "m",
        "т": "t",
        "в": "b",
        "н": "h",
        "у": "y",
        "і": "i",
        "ё": "e",
    }
)

_lock = threading.Lock()
_lists = {}
_weeks = {}
_options_cache = (0.0, {})


class ScheduleError(Exception):
    def __init__(self, message, suggestions=None):
        super().__init__(message)
        self.suggestions = suggestions or []


def now_moscow():
    return datetime.now(TZ)


def today_moscow():
    return now_moscow().date()


def academic_year(day):
    if day.month >= 9:
        return f"{day.year}-{day.year + 1}"
    return f"{day.year - 1}-{day.year}"


def week_monday(day):
    return day - timedelta(days=day.weekday())


def fold(value):
    return " ".join(value.replace("ё", "е").replace("Ё", "Е").split()).casefold()


def compact(value):
    return fold(value).replace(" ", "").replace("-", "").replace("–", "").translate(HOMOGLYPHS)


def load_options():
    global _options_cache
    now = time.monotonic()
    cached_at, cached = _options_cache
    if cached and now - cached_at < 2:
        return cached
    defaults = {
        "university": "ДГТУ",
        "target": "Группа",
        "name": "",
        "refresh_minutes": 20,
        "subgroup": 0,
    }
    try:
        data = json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    if isinstance(data, dict):
        for key in defaults:
            if key in data and data[key] is not None:
                defaults[key] = data[key]
    try:
        defaults["refresh_minutes"] = min(180, max(5, int(defaults["refresh_minutes"])))
    except (TypeError, ValueError):
        defaults["refresh_minutes"] = 20
    try:
        defaults["subgroup"] = min(20, max(0, int(defaults["subgroup"])))
    except (TypeError, ValueError):
        defaults["subgroup"] = 0
    defaults["name"] = str(defaults["name"]).strip()
    defaults["university"] = str(defaults["university"]).strip() or "ДГТУ"
    defaults["target"] = str(defaults["target"]).strip() or "Группа"
    _options_cache = (now, defaults)
    return defaults


def ssl_context():
    candidates = [
        os.environ.get("SSL_CERT_FILE"),
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/ssl/cert.pem",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return ssl.create_default_context(cafile=path)
    return ssl.create_default_context()


SSL_CONTEXT = ssl_context()


def api_get(url, params):
    full = f"{url}?{urlencode(params)}"
    request = Request(full, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last_error = None
    for attempt in range(2):
        try:
            with urlopen(request, timeout=20, context=SSL_CONTEXT) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.4)
    print(f"Запрос расписания не удался: {last_error}", flush=True)
    raise ScheduleError("Сайт расписания университета сейчас не отвечает. Попробуйте через минуту.")


def university_of(options):
    base, label = UNIVERSITIES.get(options["university"], UNIVERSITIES["ДГТУ"])
    return base, label


def target_of(options):
    return TARGETS.get(options["target"], "group")


def list_items(base, year, kind):
    key = (base, year, kind)
    now = time.monotonic()
    with _lock:
        cached = _lists.get(key)
        if cached and now - cached[0] < LIST_TTL:
            return cached[1]
    path = "/raspGrouplist" if kind == "group" else "/raspTeacherlist"
    payload = api_get(base + path, {"year": year})
    items = payload.get("data") or []
    if not isinstance(items, list):
        raise ScheduleError("Сайт расписания вернул неожиданный список.")
    with _lock:
        _lists[key] = (now, items)
    return items


def resolve_group(items, query):
    wanted = fold(query)
    wanted_compact = compact(query)
    exact = [item for item in items if fold(item.get("name") or "") == wanted]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ScheduleError(
            f"Несколько групп с именем «{query}». Укажите имя точнее.",
            [item["name"] for item in exact[:8]],
        )
    compact_hits = [item for item in items if compact(item.get("name") or "") == wanted_compact]
    if len(compact_hits) == 1:
        return compact_hits[0]
    starts = [
        item
        for item in items
        if fold(item.get("name") or "").startswith(wanted)
        or compact(item.get("name") or "").startswith(wanted_compact)
    ]
    names = []
    for item in starts:
        name = item.get("name") or ""
        if name and name not in names:
            names.append(name)
        if len(names) == 8:
            break
    if names:
        raise ScheduleError(
            f"Группа «{query}» не найдена. Похожие имена:",
            names,
        )
    raise ScheduleError(f"Группа «{query}» не найдена в этом учебном году.")


def resolve_teacher(items, query):
    wanted = fold(query)
    exact = [item for item in items if fold(item.get("name") or "") == wanted]
    if len(exact) == 1:
        return exact[0]
    surname_hits = []
    contains = []
    for item in items:
        name = fold(item.get("name") or "")
        if not name:
            continue
        if name.split()[0] == wanted or name.startswith(wanted + " "):
            surname_hits.append(item)
        elif wanted in name:
            contains.append(item)
    chosen = surname_hits or contains
    if len(chosen) == 1:
        return chosen[0]
    if len(exact) > 1:
        chosen = exact
    if len(chosen) > 1:
        names = []
        for item in chosen:
            name = item.get("name") or ""
            if name and name not in names:
                names.append(name)
            if len(names) == 8:
                break
        raise ScheduleError(
            f"Найдено несколько преподавателей по запросу «{query}». Укажите фамилию и имя.",
            names,
        )
    raise ScheduleError(f"Преподаватель «{query}» не найден в этом учебном году.")


def split_kind(discipline):
    text = (discipline or "").strip()
    lowered = text.casefold().replace("ё", "е")
    for prefix, kind, label in KIND_RULES:
        if lowered.startswith(prefix):
            title = text[len(prefix) :].lstrip(" .-–—")
            return kind, label, title or text
    return "other", "Занятие", text


def lesson_from(item, subgroup_filter):
    try:
        subgroup = int(item.get("номерПодгруппы") or 0)
    except (TypeError, ValueError):
        subgroup = 0
    if subgroup_filter and subgroup not in (0, subgroup_filter):
        return None
    raw_date = str(item.get("дата") or "")
    if len(raw_date) < 10:
        return None
    kind, kind_label, title = split_kind(item.get("дисциплина") or "")
    link = item.get("ссылка") or None
    if isinstance(link, str):
        link = link.strip() or None
    topic = item.get("тема") or None
    if isinstance(topic, str):
        topic = topic.strip() or None
    try:
        number = int(item.get("номерЗанятия") or 0)
    except (TypeError, ValueError):
        number = 0
    return {
        "date": raw_date[:10],
        "start": item.get("начало") or "",
        "end": item.get("конец") or "",
        "number": number,
        "title": title,
        "kind": kind,
        "kind_label": kind_label,
        "teacher": item.get("фиоПреподавателя") or item.get("преподаватель") or "",
        "group": item.get("группа") or "",
        "room": item.get("аудитория") or "",
        "subgroup": subgroup,
        "replaced": bool(item.get("замена")),
        "link": link,
        "topic": topic,
    }


def fetch_week(base, kind, entity_id, monday, subgroup):
    key = (base, kind, str(entity_id), monday.isoformat(), subgroup)
    options = load_options()
    ttl = options["refresh_minutes"] * 60
    now = time.monotonic()
    with _lock:
        cached = _weeks.get(key)
        if cached and now - cached[0] < ttl:
            return cached[1], False
    param = "idGroup" if kind == "group" else "idTeacher"
    payload = api_get(f"{base}/Rasp", {param: entity_id, "sdate": monday.isoformat()})
    data = payload.get("data") or {}
    lessons = []
    seen = set()
    for item in data.get("rasp") or []:
        lesson = lesson_from(item, subgroup)
        if lesson is None:
            continue
        identity = (
            lesson["date"],
            lesson["start"],
            lesson["end"],
            lesson["title"],
            lesson["teacher"],
            lesson["group"],
            lesson["room"],
            lesson["subgroup"],
        )
        if identity in seen:
            continue
        seen.add(identity)
        lessons.append(lesson)
    lessons.sort(key=lambda row: (row["date"], row["start"], row["subgroup"], row["title"]))
    info = data.get("info") if isinstance(data.get("info"), dict) else {}
    packed = {"lessons": lessons, "info": info}
    with _lock:
        _weeks[key] = (now, packed)
    return packed, False


def stale_week(base, kind, entity_id, monday, subgroup):
    key = (base, kind, str(entity_id), monday.isoformat(), subgroup)
    with _lock:
        cached = _weeks.get(key)
    if not cached:
        return None
    return cached[1]


def build_days(monday, lessons):
    by_date = {}
    for lesson in lessons:
        by_date.setdefault(lesson["date"], []).append(
            {key: value for key, value in lesson.items() if key != "date"}
        )
    days = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        iso = day.isoformat()
        days.append(
            {
                "date": iso,
                "weekday": WEEKDAYS[offset],
                "short": WEEKDAYS_SHORT[offset],
                "day": day.day,
                "month": MONTHS[day.month - 1],
                "lessons": by_date.get(iso, []),
            }
        )
    return days


def schedule_for(requested):
    options = load_options()
    today = today_moscow()
    if not options["name"]:
        raise ScheduleError(
            "Укажите группу или фамилию в конфигурации приложения «Расписание ДГТУ» и перезапустите его."
        )
    base, university_label = university_of(options)
    kind = target_of(options)
    year = academic_year(requested)
    items = list_items(base, year, kind)
    if kind == "group":
        entity = resolve_group(items, options["name"])
        title = entity.get("name") or options["name"]
        entity_id = entity.get("id")
    else:
        entity = resolve_teacher(items, options["name"])
        title = entity.get("name") or options["name"]
        entity_id = entity.get("id")
    if entity_id is None:
        raise ScheduleError("Сайт расписания не вернул идентификатор.")
    monday = week_monday(requested)
    try:
        packed, _stale = fetch_week(base, kind, entity_id, monday, options["subgroup"])
        stale = False
    except ScheduleError:
        packed = stale_week(base, kind, entity_id, monday, options["subgroup"])
        if packed is None:
            raise
        stale = True
    info = packed["info"]
    current_week = week_monday(requested) == week_monday(today)
    week_number = info.get("curWeekNumber") if current_week else None
    week_kind = None
    if current_week:
        num = info.get("selectedNumNed")
        if num == 1:
            week_kind = "верхняя"
        elif num == 2:
            week_kind = "нижняя"
    updated = info.get("dateUploadingRasp")
    return {
        "ok": True,
        "stale": stale,
        "today": today.isoformat(),
        "selected": requested.isoformat(),
        "title": title,
        "person": kind,
        "university": university_label,
        "week_number": week_number,
        "week_kind": week_kind,
        "updated": updated,
        "refresh_minutes": options["refresh_minutes"],
        "timezone": "Europe/Moscow",
        "days": build_days(monday, packed["lessons"]),
    }


def collect_dashboard():
    options = load_options()
    today = today_moscow()
    start = week_monday(today) - timedelta(days=7)
    merged = {}
    meta = None
    error = None
    for index in range(4):
        monday = start + timedelta(days=7 * index)
        try:
            payload = schedule_for(monday)
        except ScheduleError as exc:
            error = exc
            message = str(exc)
            if any(part in message for part in ("не найдена", "Укажите", "Несколько", "несколько")):
                break
            continue
        if meta is None or monday == week_monday(today):
            meta = payload
        for day in payload["days"]:
            merged[day["date"]] = day
    if meta is None:
        return {
            "ok": False,
            "error": str(error) if error else "Нет данных",
            "suggestions": error.suggestions if error else [],
            "today": today.isoformat(),
            "refresh_minutes": options["refresh_minutes"],
            "days": [],
        }
    return {
        "ok": True,
        "stale": meta.get("stale", False),
        "today": today.isoformat(),
        "title": meta["title"],
        "person": meta["person"],
        "university": meta["university"],
        "week_number": meta.get("week_number"),
        "week_kind": meta.get("week_kind"),
        "updated": meta.get("updated"),
        "refresh_minutes": options["refresh_minutes"],
        "timezone": "Europe/Moscow",
        "days": [merged[key] for key in sorted(merged)],
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                self._send(200, INDEX_PATH.read_bytes(), "text/html; charset=utf-8")
            elif parsed.path == "/preview":
                self._send(200, PREVIEW_PATH.read_bytes(), "text/html; charset=utf-8")
            elif parsed.path == "/dgtu-raspisanie-card.js":
                self._send(200, CARD_PATH.read_bytes(), "text/javascript; charset=utf-8")
            elif parsed.path == "/local/dgtu-raspisanie.json":
                self._dashboard_file()
            elif parsed.path == "/api/health":
                self._send_json(200, {"ok": True})
            elif parsed.path == "/api/schedule":
                self._schedule(parse_qs(parsed.query))
            else:
                self._send_json(404, {"ok": False, "error": "Не найдено"})
        except Exception:
            traceback.print_exc()
            self._send_json(500, {"ok": False, "error": "Внутренняя ошибка приложения"})

    def _dashboard_file(self):
        from publish import www_dir

        file = www_dir() / "dgtu-raspisanie.json"
        if not file.is_file():
            self._send_json(404, {"ok": False, "error": "Расписание ещё загружается"})
            return
        self._send(200, file.read_bytes(), "application/json; charset=utf-8")

    def _schedule(self, query):
        raw = (query.get("date") or [None])[0]
        if raw:
            try:
                requested = date.fromisoformat(raw)
            except ValueError:
                self._send_json(400, {"ok": False, "error": "Дата должна быть в формате ГГГГ-ММ-ДД"})
                return
        else:
            requested = today_moscow()
        try:
            payload = schedule_for(requested)
        except ScheduleError as exc:
            self._send_json(200, {"ok": False, "error": str(exc), "suggestions": exc.suggestions})
            return
        self._send_json(200, payload)

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[{now_moscow():%H:%M:%S}] {self.address_string()} {fmt % args}", flush=True)


def main():
    from publish import start_background

    start_background(collect_dashboard)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Расписание ДГТУ слушает {HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
