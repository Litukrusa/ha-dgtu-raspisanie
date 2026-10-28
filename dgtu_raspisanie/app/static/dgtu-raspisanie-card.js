const DATA_URL = "/local/dgtu-raspisanie.json";

class DgtuRaspisanieCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._selected = null;
    this._data = null;
  }

  setConfig(config) {
    this.config = config || {};
  }

  set hass(hass) {
    this._hass = hass;
    if (this._started) return;
    this._started = true;
    this._load();
    this._clockTimer = setInterval(() => {
      if (this._data && this._data.ok) this._render();
    }, 30000);
  }

  disconnectedCallback() {
    clearTimeout(this._dataTimer);
    clearInterval(this._clockTimer);
  }

  getCardSize() {
    const count = this._selectedDay()?.lessons?.length || 3;
    return Math.min(8, 3 + count);
  }

  getGridOptions() {
    return { columns: 12, rows: "auto", min_columns: 6, min_rows: 3 };
  }

  async _load() {
    try {
      const response = await fetch(`${DATA_URL}?t=${Date.now()}`, { cache: "no-store" });
      if (!response.ok) throw new Error(String(response.status));
      this._data = await response.json();
      if (!this._selected || !this._days().some((day) => day.date === this._selected)) {
        this._selected = this._data.today || null;
      }
    } catch (error) {
      if (!this._data) this._data = { ok: false, error: "Расписание ещё загружается. Подождите минуту после запуска приложения." };
    }
    this._render();
    clearTimeout(this._dataTimer);
    const delay = this._data && this._data.ok ? 60000 : 4000;
    this._dataTimer = setTimeout(() => this._load(), delay);
  }

  _days() {
    return (this._data && this._data.days) || [];
  }

  _selectedDay() {
    return this._days().find((day) => day.date === this._selected) || null;
  }

  _render() {
    if (!this.shadowRoot.querySelector("ha-card")) {
      this.shadowRoot.innerHTML = `<style>${CSS}</style><ha-card><div id="root"></div></ha-card>`;
    }
    const root = this.shadowRoot.getElementById("root");
    const data = this._data;
    if (!data) {
      root.innerHTML = `<p class="muted">Загружаю расписание…</p>`;
      return;
    }
    if (!data.ok) {
      root.innerHTML = `<p class="title">Расписание</p><p class="muted">${escapeHtml(data.error || "Нет данных")}</p>${suggestions(data.suggestions)}`;
      return;
    }
    const today = data.today;
    const tomorrow = addDays(today, 1);
    const day = this._selectedDay() || this._days()[0];
    if (!day) {
      root.innerHTML = `<p class="muted">В эти дни пар нет.</p>`;
      return;
    }
    this._selected = day.date;
    const heading = day.date === today ? "Сегодня" : day.date === tomorrow ? "Завтра" : day.weekday;
    const who = [data.title, data.university].filter(Boolean).join(" · ");
    const week = [data.week_number ? `${data.week_number} неделя` : "", data.week_kind || ""].filter(Boolean).join(" · ");
    root.innerHTML = `
      <div class="top">
        <div>
          <div class="who">${escapeHtml(who)}</div>
          <div class="title">${escapeHtml(heading)}</div>
          <div class="muted">${escapeHtml(day.day + " " + day.month)}${week && day.date === today ? " · " + escapeHtml(week) : ""}</div>
        </div>
      </div>
      <p class="glance ${day.date === today ? "today" : ""}">${glance(day, day.date === today, data.person)}</p>
      <div class="controls">
        <div class="segment">
          <button type="button" data-go="${today}" aria-pressed="${day.date === today}">Сегодня</button>
          <button type="button" data-go="${tomorrow}" aria-pressed="${day.date === tomorrow}">Завтра</button>
        </div>
        <button type="button" class="arrow" data-shift="-1" aria-label="Предыдущая неделя" ${canShift(this._days(), day.date, -1) ? "" : "disabled"}>‹</button>
        <button type="button" class="arrow" data-shift="1" aria-label="Следующая неделя" ${canShift(this._days(), day.date, 1) ? "" : "disabled"}>›</button>
      </div>
      <div class="days">${this._days().filter((item) => sameWeek(item.date, day.date)).map((item) => `
        <button type="button" class="chip ${item.lessons.length ? "busy" : ""}" data-go="${item.date}" aria-pressed="${item.date === day.date}">
          <small>${escapeHtml(item.short)}</small>
          <span>${item.day}</span>
        </button>`).join("")}
      </div>
      <div class="count">${day.lessons.length ? `${day.lessons.length} ${lessonsWord(day.lessons.length)}` : ""}</div>
      <div class="lessons">${day.lessons.length ? day.lessons.map((lesson) => lessonRow(lesson, day.date === today, data.person)).join("") : `<p class="empty">Пар нет</p>`}</div>
    `;
    root.querySelectorAll("[data-go]").forEach((button) => {
      button.addEventListener("click", () => {
        this._selected = button.dataset.go;
        this._render();
      });
    });
    root.querySelectorAll("[data-shift]").forEach((button) => {
      button.addEventListener("click", () => {
        if (button.disabled) return;
        this._selected = addDays(day.date, 7 * Number(button.dataset.shift));
        this._render();
      });
    });
  }
}

function lessonRow(lesson, isToday, person) {
  const now = isToday ? moscowMinutes() : null;
  const start = clock(lesson.start);
  const end = clock(lesson.end);
  const current = now !== null && start !== null && end !== null && now >= start && now < end;
  const who = person === "teacher" ? lesson.group : lesson.teacher;
  const meta = [lesson.kind_label, who, lesson.room, lesson.subgroup ? "п/г " + lesson.subgroup : "", lesson.replaced ? "замена" : ""]
    .filter(Boolean)
    .join(" · ");
  const link = safeUrl(lesson.link);
  return `<article class="lesson ${escapeHtml(lesson.kind)} ${current ? "current" : ""}">
    <div class="when">${escapeHtml(lesson.start)}<span>${escapeHtml(lesson.end)}</span></div>
    <div>
      <div class="name">${escapeHtml(lesson.title)}${current ? `<em>Сейчас</em>` : ""}</div>
      <div class="meta">${escapeHtml(meta)}</div>
      ${lesson.topic ? `<div class="meta">${escapeHtml(lesson.topic)}</div>` : ""}
      ${link ? `<a href="${escapeHtml(link)}" target="_blank" rel="noopener">Открыть занятие</a>` : ""}
    </div>
  </article>`;
}

function glance(day, isToday, person) {
  if (!isToday) {
    const first = day.lessons[0];
    return first
      ? `Первая пара в <strong>${escapeHtml(first.start)}</strong> · ${escapeHtml(first.title)}`
      : "В этот день занятий нет";
  }
  const now = moscowMinutes();
  const current = day.lessons.find((lesson) => {
    const start = clock(lesson.start);
    const end = clock(lesson.end);
    return start !== null && end !== null && now >= start && now < end;
  });
  if (current) return `Сейчас <strong>${escapeHtml(current.title)}</strong> · до ${escapeHtml(current.end)}`;
  const next = day.lessons.find((lesson) => clock(lesson.start) > now);
  if (next) {
    const who = person === "teacher" ? next.group : next.teacher;
    return `Дальше в <strong>${escapeHtml(next.start)}</strong> · ${escapeHtml(next.title)}${who ? " · " + escapeHtml(who) : ""}`;
  }
  return day.lessons.length ? "На сегодня всё" : "На сегодня пар нет";
}

function suggestions(items) {
  if (!items || !items.length) return "";
  return `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function canShift(days, selected, delta) {
  const target = addDays(selected, 7 * delta);
  const monday = weekMonday(target);
  const sunday = addDays(monday, 6);
  return days.some((day) => day.date >= monday && day.date <= sunday);
}

function sameWeek(date, other) {
  return weekMonday(date) === weekMonday(other);
}

function weekMonday(iso) {
  const date = parseISO(iso);
  const weekday = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() - weekday + 1);
  return date.toISOString().slice(0, 10);
}

function addDays(iso, count) {
  const date = parseISO(iso);
  date.setUTCDate(date.getUTCDate() + count);
  return date.toISOString().slice(0, 10);
}

function parseISO(iso) {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day));
}

function clock(value) {
  const [hour, minute] = String(value || "").split(":");
  if (hour === undefined || minute === undefined || hour === "") return null;
  return Number(hour) * 60 + Number(minute);
}

function moscowMinutes() {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Moscow",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date());
  const map = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return Number(map.hour) * 60 + Number(map.minute);
}

function lessonsWord(count) {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return "пара";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return "пары";
  return "пар";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function safeUrl(value) {
  return typeof value === "string" && /^https?:\/\//i.test(value) ? value : "";
}

const CSS = `
  :host { display: block; }
  ha-card {
    display: block;
    height: 100%;
    box-sizing: border-box;
    padding: 16px 16px 12px;
    background: var(--ha-card-background, var(--card-background-color));
    color: var(--primary-text-color);
    border-radius: var(--ha-card-border-radius, 12px);
  }
  button, a { font: inherit; }
  button {
    color: inherit;
    background: transparent;
    border: 0;
    padding: 0;
    cursor: pointer;
  }
  button:disabled { opacity: 0.35; cursor: default; }
  .top { display: flex; justify-content: space-between; gap: 12px; }
  .who, .muted, .meta, .count {
    color: var(--secondary-text-color);
    font-size: 0.82rem;
    line-height: 1.3;
  }
  .title {
    margin-top: 2px;
    font-size: 1.55rem;
    font-weight: 500;
    letter-spacing: -0.03em;
    line-height: 1.15;
  }
  .glance { margin: 10px 0 0; font-size: 0.95rem; line-height: 1.35; }
  .glance strong { font-weight: 600; }
  .glance.today strong { color: var(--success-color, var(--primary-color)); }
  .controls { display: flex; gap: 8px; align-items: center; margin-top: 14px; }
  .segment {
    flex: 1;
    display: grid;
    grid-template-columns: 1fr 1fr;
    padding: 3px;
    border-radius: 12px;
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.12));
  }
  .segment button, .arrow, .chip { min-height: 40px; }
  .segment button { border-radius: 9px; color: var(--secondary-text-color); font-weight: 600; }
  .segment button[aria-pressed="true"] {
    background: var(--primary-color);
    color: var(--text-primary-color, #fff);
  }
  .arrow {
    width: 40px;
    border-radius: 12px;
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.12));
    font-size: 1.3rem;
  }
  .days {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    gap: 2px;
    margin-top: 12px;
    padding: 4px;
    border-radius: 16px;
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.12));
  }
  .chip {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    min-width: 0;
    border-radius: 12px;
    color: var(--secondary-text-color);
    padding: 6px 0 12px;
  }
  .chip span { color: var(--primary-text-color); font-weight: 680; font-variant-numeric: tabular-nums; line-height: 1; }
  .chip small { font-size: 0.68rem; letter-spacing: 0.04em; text-transform: uppercase; }
  .chip.busy::after {
    content: "";
    position: absolute;
    left: 50%;
    bottom: 4px;
    width: 4px;
    height: 4px;
    margin-left: -2px;
    border-radius: 50%;
    background: var(--primary-color);
  }
  .chip[aria-pressed="true"] { background: var(--primary-color); }
  .chip[aria-pressed="true"], .chip[aria-pressed="true"] span, .chip[aria-pressed="true"] small {
    color: var(--text-primary-color, #fff);
  }
  .chip[aria-pressed="true"].busy::after { background: var(--text-primary-color, #fff); }
  .count { min-height: 1rem; margin: 14px 0 0; }
  .lesson {
    display: grid;
    grid-template-columns: 3.5rem 1fr;
    gap: 10px;
    padding: 13px 2px;
    border-bottom: 1px solid var(--divider-color, rgba(127, 127, 127, 0.18));
    border-left: 0;
  }
  .lesson.lec .when { color: var(--success-color, #4caf50); }
  .lesson.lab .when { color: var(--info-color, var(--primary-color)); }
  .lesson.prac .when { color: var(--warning-color, #ff9800); }
  .lesson.sem .when { color: var(--accent-color, #9c27b0); }
  .lesson.exam .when { color: var(--error-color, #f44336); }
  .lesson.current {
    margin: 4px -8px;
    padding: 12px 10px;
    border-radius: 14px;
    border-bottom-color: transparent;
    background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.12);
  }
  .when { font-variant-numeric: tabular-nums; font-weight: 720; font-size: 0.95rem; letter-spacing: -0.03em; }
  .when span { display: block; color: var(--secondary-text-color); font-weight: 560; font-size: 0.72rem; }
  .name { font-weight: 640; letter-spacing: -0.02em; font-size: 1.02rem; }
  .name em {
    margin-left: 8px;
    font-style: normal;
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.02em;
    color: var(--success-color, var(--primary-color));
  }
  a { color: var(--primary-color); font-size: 0.82rem; text-decoration: none; }
  .empty { margin: 8px 0 12px; color: var(--secondary-text-color); }
  ul { margin: 8px 0 0; padding-left: 18px; }
`;

if (!customElements.get("dgtu-raspisanie-card")) {
  customElements.define("dgtu-raspisanie-card", DgtuRaspisanieCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "dgtu-raspisanie-card",
  name: "Расписание ДГТУ",
  description: "Пары на сегодня, завтра и любой другой день",
  preview: true,
});
