"use strict";

(function () {
  const NBSP = " ";
  const WD = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
  const MON = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
  const MSK = 3 * 3600e3;
  const DAY = 864e5;

  const app = document.getElementById("app");
  const updatedEl = document.getElementById("updated");

  let DATA = null;
  let BEST = [];
  const TRIPS_CSV = {};
  const UI = { range: "30", nights: {}, sort: "date", showLow: false };
  let hiders = [];
  let lastWidth = 0;
  let offline = false;
  let loadedAt = 0;

  // ---------- мелочи ------------------------------------------------------------
  function el(tag, attrs, ...kids) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return node;
  }

  function sv(tag, attrs, text) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value !== null && value !== undefined) node.setAttribute(key, value);
    }
    if (text !== undefined) node.textContent = text;
    return node;
  }

  const pad = (n) => String(n).padStart(2, "0");
  const num = (n) => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
  const rub = (n) => num(n) + NBSP + "₽";
  const dayMs = (iso) => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10));
  const parseUtc = (s) => Date.parse(s);

  function dayLabel(iso) {
    const d = new Date(dayMs(iso));
    return WD[d.getUTCDay()] + " " + pad(d.getUTCDate()) + "." + pad(d.getUTCMonth() + 1);
  }

  function ddmm(iso) {
    return iso.slice(8, 10) + "." + iso.slice(5, 7);
  }

  function moment(ms) {
    const d = new Date(ms + MSK);
    return d.getUTCDate() + " " + MON[d.getUTCMonth()] + ", " + pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes());
  }

  function nightsWord(n) {
    if (n % 10 === 1 && n % 100 !== 11) return n + " ночь";
    if (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14)) return n + " ночи";
    return n + " ночей";
  }

  const airlineName = (code) => (DATA.names.airlines || {})[code] || code || "?";
  const airportName = (code) => (DATA.names.airports || {})[code] || code;

  function flightLabel(leg) {
    const name = airlineName(leg.airline);
    if (!leg.flight) return name;
    return name === leg.airline ? leg.flight : name + " " + leg.flight;
  }

  function airlinesText(trip) {
    const names = [];
    for (const leg of trip.legs || []) {
      const name = airlineName(leg.airline);
      if (!names.includes(name)) names.push(name);
    }
    return names.join(" / ");
  }

  function routeText(trip, resort) {
    if (resort.airports.length < 2 || !trip.legs) return "";
    const there = trip.legs[0].to;
    const back = trip.legs[1].from;
    return there === back ? airportName(there) : airportName(there) + " / " + airportName(back);
  }

  function nightsText(list) {
    return list.length > 1 ? list.slice(0, -1).join(", ") + " или " + nightsWord(list[list.length - 1]) : nightsWord(list[0]);
  }

  function bestTrip(resort) {
    return resort.best ? resort.trips.find((t) => t.key === resort.best) || null : null;
  }

  function debounce(fn, wait) {
    let timer = 0;
    return () => {
      clearTimeout(timer);
      timer = setTimeout(fn, wait);
    };
  }

  async function fetchText(url) {
    const resp = await fetch(url + "?v=" + Date.now(), { cache: "no-store" });
    if (!resp.ok) throw new Error(url + ": " + resp.status);
    if (resp.headers.get("X-Offline")) offline = true;
    return resp.text();
  }

  function parseCSV(text) {
    const lines = text.trim().split(/\r?\n/);
    const head = lines.shift().split(",");
    return lines.filter(Boolean).map((line) => {
      const cells = line.split(",");
      const row = {};
      head.forEach((name, i) => (row[name] = cells[i] === undefined ? "" : cells[i]));
      return row;
    });
  }

  async function tripHistory(resortId) {
    if (!TRIPS_CSV[resortId]) {
      TRIPS_CSV[resortId] = fetchText("data/trips/" + resortId + ".csv")
        .then(parseCSV)
        .catch(() => []);
    }
    return TRIPS_CSV[resortId];
  }

  // ---------- графики (SVG) ------------------------------------------------------
  function niceTicks(lo, hi, count) {
    if (hi === lo) {
      const extra = Math.max(500, Math.abs(hi) * 0.05);
      lo -= extra;
      hi += extra;
    }
    const raw = (hi - lo) / count;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 5, 10].map((s) => s * mag).find((s) => s >= raw);
    const ticks = [];
    for (let v = Math.floor(lo / step) * step; v <= Math.ceil(hi / step) * step + step / 2; v += step) {
      ticks.push(Math.round(v));
    }
    return ticks;
  }

  function tickLabel(v) {
    if (Math.abs(v) >= 10000) return String(+(v / 1000).toFixed(1)).replace(".", ",") + " тыс.";
    return num(v);
  }

  function xTicks(x0, x1, kind, width) {
    const most = Math.max(2, Math.floor(width / 74));
    const out = [];
    if (kind === "time" && x1 - x0 < 2 * DAY) {
      const hours = [3, 6, 12, 24].find((h) => (x1 - x0) / (h * 3600e3) <= most) || 24;
      for (let t = Math.ceil((x0 + MSK) / (hours * 3600e3)) * hours * 3600e3 - MSK; t <= x1; t += hours * 3600e3) {
        out.push({ x: t, label: pad(new Date(t + MSK).getUTCHours()) + ":00" });
      }
      return out;
    }
    const offset = kind === "time" ? MSK : 0;
    const days = [1, 2, 3, 7, 14, 28, 56].find((s) => (x1 - x0) / DAY / s <= most) || 56;
    for (let t = Math.ceil((x0 + offset) / DAY) * DAY - offset; t <= x1; t += days * DAY) {
      const d = new Date(t + offset);
      out.push({ x: t, label: d.getUTCDate() + " " + MON[d.getUTCMonth()] });
    }
    return out;
  }

  // opt: points [{x, y, gray, key, tip: [строка-значение, подписи...]}], mode "step" | "dots",
  //      height, threshold, kind "time" | "day", xDomain, extendTo, bands, onPick, mark, aria, empty
  function drawChart(box, opt) {
    box.replaceChildren();
    box.className = "chart";
    const W = Math.max(260, Math.round(box.clientWidth || 320));
    const H = opt.height || 200;
    const m = { l: 56, r: 14, t: 18, b: 26 };
    const pts = opt.points.filter((p) => p.y !== null && p.y !== undefined);
    if (!pts.length) {
      box.append(el("p", { class: "muted small", text: opt.empty || "Пока нет данных" }));
      return;
    }
    let x0 = Math.min(...pts.map((p) => p.x));
    let x1 = Math.max(...pts.map((p) => p.x), opt.extendTo || -Infinity);
    if (opt.xDomain) {
      x0 = Math.min(x0, opt.xDomain[0]);
      x1 = Math.max(x1, opt.xDomain[1]);
    }
    if (x1 === x0) {
      x0 -= DAY / 2;
      x1 += DAY / 2;
    }
    let y0 = Math.min(...pts.map((p) => p.y));
    let y1 = Math.max(...pts.map((p) => p.y));
    if (opt.threshold && opt.threshold >= y0 * 0.7) {
      y0 = Math.min(y0, opt.threshold);
      y1 = Math.max(y1, opt.threshold);
    }
    const ticks = niceTicks(y0, y1, 4);
    y0 = ticks[0];
    y1 = ticks[ticks.length - 1];

    const pw = W - m.l - m.r;
    const ph = H - m.t - m.b;
    const inset = opt.mode === "dots" ? 7 : 0;
    const sx = (x) => m.l + inset + ((x - x0) / (x1 - x0)) * (pw - 2 * inset);
    const sy = (y) => m.t + (1 - (y - y0) / (y1 - y0)) * ph;
    const svg = sv("svg", { width: W, height: H, viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": opt.aria || "" });

    for (const band of opt.bands || []) {
      const bx0 = Math.max(m.l, sx(band.x0));
      const bx1 = Math.min(W - m.r, sx(band.x1));
      if (bx1 - bx0 > 2) {
        svg.append(sv("rect", { class: "band", x: bx0, y: m.t, width: bx1 - bx0, height: ph }));
        svg.append(sv("text", { class: "lbl", x: (bx0 + bx1) / 2, y: m.t - 5, "text-anchor": "middle" }, band.label));
      }
    }
    for (const v of ticks) {
      const y = sy(v);
      if (v !== ticks[0]) svg.append(sv("line", { class: "grid", x1: m.l, x2: W - m.r, y1: y, y2: y }));
      svg.append(sv("text", { x: m.l - 7, y: y + 4, "text-anchor": "end" }, tickLabel(v)));
    }
    svg.append(sv("line", { class: "base", x1: m.l, x2: W - m.r, y1: m.t + ph, y2: m.t + ph }));
    for (const tick of xTicks(x0, x1, opt.kind, pw)) {
      svg.append(sv("text", { x: sx(tick.x), y: H - 7, "text-anchor": "middle" }, tick.label));
    }
    const showThreshold = opt.threshold && opt.threshold >= y0 && opt.threshold <= y1;
    if (showThreshold) {
      const y = sy(opt.threshold);
      svg.append(sv("line", { class: "thr", x1: m.l, x2: W - m.r, y1: y, y2: y }));
    }

    if (opt.mode === "step") {
      let d = "";
      let prev = null;
      let gaps = false;
      for (const p of opt.points) {
        if (p.y === null || p.y === undefined) {
          if (prev) d += " H" + sx(p.x);
          prev = null;
          gaps = true;
          continue;
        }
        d += prev ? " H" + sx(p.x) + " V" + sy(p.y) : " M" + sx(p.x) + "," + sy(p.y);
        prev = p;
      }
      if (prev && opt.extendTo && opt.extendTo > prev.x) d += " H" + sx(opt.extendTo);
      if (!gaps && pts.length > 1) {
        svg.append(sv("path", { class: "area", d: d + " V" + (m.t + ph) + " H" + sx(pts[0].x) + " Z" }));
      }
      svg.append(sv("path", { class: "line", d: d.trim() }));
      const last = pts[pts.length - 1];
      svg.append(sv("circle", { class: "dot", cx: sx(last.x), cy: sy(last.y), r: 4 }));
    } else {
      for (const p of pts) {
        svg.append(sv("circle", { class: "dot" + (p.gray ? " gray" : ""), cx: sx(p.x), cy: sy(p.y), r: 4 }));
      }
    }
    if (showThreshold) {
      // подпись поверх линий, с подложкой цвета фона
      svg.append(sv("text", { class: "lbl", x: W - m.r, y: sy(opt.threshold) - 5, "text-anchor": "end" }, "порог " + num(opt.threshold)));
    }
    if (opt.mark) {
      const p = opt.mark;
      const x = Math.min(Math.max(sx(p.x), m.l + 20), W - m.r - 20);
      svg.append(sv("text", { class: "lbl", x: x, y: sy(p.y) - 9, "text-anchor": "middle" }, p.label));
    }

    // наведение и касание: перекрестие, точка и подсказка
    const tip = el("div", { class: "tooltip", hidden: true });
    const cross = sv("line", { class: "cross", y1: m.t, y2: m.t + ph, visibility: "hidden" });
    const focus = sv("circle", { class: "focus", r: 5.5, visibility: "hidden" });
    const hit = sv("rect", { x: m.l - 6, y: 0, width: pw + 12, height: H, fill: "transparent" });
    svg.append(cross, focus, hit);
    let index = -1;

    function nearest(px) {
      let best = 0;
      let dist = Infinity;
      pts.forEach((p, i) => {
        const dd = Math.abs(sx(p.x) - px);
        if (dd < dist) {
          dist = dd;
          best = i;
        }
      });
      return best;
    }
    function show(i) {
      index = i;
      const p = pts[i];
      const x = sx(p.x);
      const y = sy(p.y);
      cross.setAttribute("x1", x);
      cross.setAttribute("x2", x);
      cross.setAttribute("visibility", "visible");
      focus.setAttribute("cx", x);
      focus.setAttribute("cy", y);
      focus.setAttribute("visibility", "visible");
      tip.replaceChildren(
        el("div", { class: "tv", text: p.tip[0] }),
        ...p.tip.slice(1).filter(Boolean).map((line) => el("div", { class: "tl", text: line }))
      );
      tip.hidden = false;
      const tw = tip.offsetWidth;
      const th = tip.offsetHeight;
      let left = x + 12;
      if (left + tw > W) left = x - 12 - tw;
      if (left < 0) left = Math.max(0, Math.min(W - tw, x - tw / 2));
      tip.style.left = left + "px";
      tip.style.top = Math.max(0, Math.min(y - th - 12, H - th)) + "px";
    }
    function hide() {
      cross.setAttribute("visibility", "hidden");
      focus.setAttribute("visibility", "hidden");
      tip.hidden = true;
      index = -1;
    }
    const pointerX = (ev) => {
      const rect = svg.getBoundingClientRect();
      return ((ev.clientX - rect.left) * W) / rect.width;
    };
    hit.addEventListener("pointermove", (ev) => show(nearest(pointerX(ev))));
    hit.addEventListener("pointerdown", (ev) => show(nearest(pointerX(ev))));
    hit.addEventListener("pointerleave", (ev) => {
      if (ev.pointerType === "mouse") hide();
    });
    if (opt.onPick) {
      hit.style.cursor = "pointer";
      hit.addEventListener("click", (ev) => opt.onPick(pts[nearest(pointerX(ev))]));
    }
    box.tabIndex = 0;
    box.addEventListener("keydown", (ev) => {
      if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") {
        const step = ev.key === "ArrowRight" ? 1 : -1;
        show(index < 0 ? (step > 0 ? 0 : pts.length - 1) : Math.min(pts.length - 1, Math.max(0, index + step)));
        ev.preventDefault();
      } else if (ev.key === "Enter" && index >= 0 && opt.onPick) {
        opt.onPick(pts[index]);
      } else if (ev.key === "Escape") {
        hide();
      }
    });
    box.addEventListener("blur", hide);
    hiders.push((target) => {
      if (!box.contains(target)) hide();
    });
    box.append(svg, tip);
  }

  function sparkline(series) {
    const W = 300;
    const H = 34;
    const p = 4;
    const xs = series.map((s) => s.t);
    const ys = series.map((s) => s.total);
    const x0 = Math.min(...xs);
    const x1 = Math.max(...xs);
    // Минимальный размах 10% от цены: скачок на 30 ₽ не должен выглядеть как обвал.
    const lo = Math.min(...ys);
    const hi = Math.max(...ys);
    const span = Math.max(hi - lo, hi * 0.1);
    const y0 = (lo + hi) / 2 - span / 2;
    const y1 = (lo + hi) / 2 + span / 2;
    const sx = (x) => p + (x1 === x0 ? (W - 2 * p) / 2 : ((x - x0) / (x1 - x0)) * (W - 2 * p));
    const sy = (y) => (y1 === y0 ? H / 2 : p + (1 - (y - y0) / (y1 - y0)) * (H - 2 * p));
    let d = "";
    series.forEach((s, i) => {
      d += i ? " H" + sx(s.t) + " V" + sy(s.total) : "M" + sx(s.t) + "," + sy(s.total);
    });
    const svg = sv("svg", { class: "spark", viewBox: "0 0 " + W + " " + H, "aria-hidden": "true" });
    svg.append(sv("path", { d: d, fill: "none", stroke: "var(--muted)", "stroke-width": 1.5, "stroke-linejoin": "round" }));
    const last = series[series.length - 1];
    svg.append(sv("circle", { cx: sx(last.t), cy: sy(last.total), r: 3.5, fill: "var(--accent)", stroke: "var(--surface)", "stroke-width": 2 }));
    return svg;
  }

  // ---------- общие части -----------------------------------------------------------
  function deltaEl(change) {
    if (change === null || change === undefined) return null;
    if (change < 0) return el("span", { class: "delta down", text: "↓ " + num(-change) + " за сутки" });
    if (change > 0) return el("span", { class: "delta", text: "↑ " + num(change) + " за сутки" });
    return el("span", { class: "delta", text: "за сутки без изменений" });
  }

  function tripSummary(trip, resort) {
    const parts = [dayLabel(trip.depart) + " → " + dayLabel(trip.return), nightsWord(trip.nights)];
    const route = routeText(trip, resort);
    if (route) parts.push(route);
    if (trip.legs) parts.push(airlinesText(trip));
    return parts.join(" · ");
  }

  function seg(options, current, onChange, label) {
    const group = el("div", { class: "seg", role: "group", "aria-label": label });
    for (const [value, text] of options) {
      group.append(
        el("button", {
          type: "button",
          "aria-pressed": String(value === current),
          text: text,
          onclick: () => onChange(value),
        })
      );
    }
    return group;
  }

  function tripDetail(trip, resort) {
    const box = el("div");
    const legs = el("ul", { class: "legs" });
    const names = { out: "Туда", back: "Обратно" };
    for (const leg of trip.legs) {
      const main = names[leg.dir] + ": " + dayLabel(leg.date) + ", " + leg.time + " · " + leg.from + " → " + leg.to;
      const sub = [flightLabel(leg), airportName(leg.from) + " → " + airportName(leg.to)];
      if (leg.price !== null) sub.push(rub(leg.price) + (leg.bag ? " + багаж ≈" + num(leg.bag) : " · багаж в тарифе"));
      legs.append(el("li", null, el("div", { class: "leg-main", text: main }), el("div", { class: "leg-sub", text: sub.join(" · ") })));
    }
    const bagText = trip.bag ? " + багаж ≈" + num(trip.bag) : " (багаж в тарифе)";
    const totalText = (trip.kind === "rt" ? "Одна бронь туда-обратно: " : "Билеты ") + rub(trip.price) + bagText;
    legs.append(el("li", null, el("div", { class: "leg-main", text: "Итого " + rub(trip.total) }), el("div", { class: "leg-sub", text: totalText })));
    if (trip.found) {
      legs.append(el("li", null, el("div", { class: "leg-sub", text: "Цену видели в поиске Aviasales " + foundText(trip.found) + ". Чем давнее, тем вероятнее, что она изменилась." })));
    }
    const mine = (resort.checks || []).filter((c) => c.trip === trip.key);
    if (mine.length) {
      const c = mine[0];
      legs.append(el("li", null,
        el("div", { class: "leg-main", text: "Вы проверяли: " + rub(c.price) }),
        el("div", { class: "leg-sub", text: moment(parseUtc(c.seen_at)) + " · приложение тогда показывало " + rub(c.app_total) })));
    }
    box.append(legs);

    const links = el("div", { class: "links" });
    links.append(el("a", { class: "button", href: trip.link, target: "_blank", rel: "noopener", text: "Проверить на Aviasales" }));
    if (trip.kind === "legs" && trip.legs[0].to !== trip.legs[1].from) {
      for (const leg of trip.legs) {
        if (leg.link) links.append(el("a", { class: "secondary", href: leg.link, target: "_blank", rel: "noopener", text: "билет " + names[leg.dir].toLowerCase() }));
      }
    }
    box.append(links);
    if (DATA.repo) box.append(checkForm(trip, resort));
    if (trip.low) box.append(el("p", { class: "hint", text: "Поездка задевает даты «" + trip.low + "» — уведомлений по ней нет." }));
    return box;
  }

  function foundText(iso) {
    const days = Math.round((dayMs(moscowToday()) - dayMs(iso)) / DAY);
    if (days <= 0) return "сегодня";
    if (days === 1) return "вчера";
    return ddmm(iso) + " (" + days + " " + (days < 5 ? "дня" : "дней") + " назад)";
  }

  function moscowToday() {
    return new Date(Date.now() + MSK).toISOString().slice(0, 10);
  }

  // «Я проверил цену»: пользователь вводит цену с Aviasales, приложение открывает GitHub с готовым файлом.
  function checkForm(trip, resort) {
    const wrap = el("details", { class: "check" });
    const input = el("input", { type: "number", inputmode: "numeric", min: "1000", step: "1", placeholder: "например, 30935", id: "check-" + trip.key.replace(/\W/g, "-") });
    const out = el("div", { class: "check-out" });
    const save = el("button", { class: "button", type: "button", text: "Сохранить" });
    save.addEventListener("click", () => {
      const price = Math.round(Number(input.value));
      if (!price || price < 1000) {
        out.replaceChildren(el("p", { class: "app-error", text: "Введите цену туда-обратно с багажом в рублях, например 30935." }));
        return;
      }
      const now = new Date();
      const stamp = now.toISOString().slice(0, 16).replace(/[-:T]/g, "");
      const body = JSON.stringify({ resort: resort.id, trip: trip.key, price: price, app_total: trip.total, seen_at: now.toISOString().slice(0, 16) + "Z" }, null, 2);
      const href = "https://github.com/" + DATA.repo + "/new/main?filename=" + encodeURIComponent("checks/" + resort.id + "-" + stamp + ".json") + "&value=" + encodeURIComponent(body);
      const diff = price - trip.total;
      out.replaceChildren(
        el("p", { class: "small", text: "Разница с приложением: " + (diff >= 0 ? "+" : "−") + rub(Math.abs(diff)) + ". Последний шаг — сохранить на GitHub: нажмите «Commit changes»." }),
        el("a", { class: "button", href: href, target: "_blank", rel: "noopener", text: "Открыть GitHub" }));
    });
    wrap.append(
      el("summary", { text: "Я проверил цену на Aviasales" }),
      el("p", { class: "small muted", text: "Сколько стоит эта поездка туда-обратно с багажом на Aviasales (те же даты, прямые рейсы)? По вашим проверкам приложение поправит цены курорта." }),
      el("div", { class: "check-row" }, input, el("span", { class: "muted", text: "₽" }), save),
      out);
    return wrap;
  }

  function lowHint() {
    const periods = (DATA.low_priority || []).map((p) => ddmm(p.from) + "–" + ddmm(p.to));
    const tail = "Нажмите на точку или строку, чтобы увидеть рейсы.";
    if (!periods.length) return tail;
    return "Серая полоса — даты вылета, при которых поездка задевает конференцию (" + periods.join(", ") +
      "): даже если вылет раньше, возвращение придётся на неё. " + tail;
  }

  function footer() {
    const f = el("footer");
    appNote = el("p", { class: "app-note", hidden: true });
    f.append(appNote);
    f.append(el("p", null, el("a", { href: "#help", text: "Как установить приложение и как оно работает" })));
    return f;
  }

  // ---------- главная ------------------------------------------------------------
  function resortCard(resort) {
    const best = bestTrip(resort);
    const card = el("a", { class: "card", href: "#" + resort.id },
      el("div", { class: "card-head" },
        el("span", { class: "name", text: resort.name }),
        el("span", { class: "where", text: resort.airports.map(airportName).join(" · ") })));
    if (!best) {
      card.append(el("p", { class: "muted small", text: "Нет цен на прямые рейсы в эти даты" }));
      return card;
    }
    card.append(el("div", { class: "price-row" }, el("span", { class: "price", text: rub(best.total) }), deltaEl(resort.change_24h)));
    card.append(el("div", { class: "trip-text", text: tripSummary(best, resort) }));
    const since = parseUtc(DATA.updated) - 14 * DAY;
    const series = BEST.filter((b) => b.resort === resort.id && b.total && b.t >= since);
    if (series.length > 1) card.append(sparkline(series));
    const chips = el("div", { class: "chips" });
    if (resort.threshold) {
      chips.append(
        best.total <= resort.threshold
          ? el("span", { class: "chip ok", text: "✓ не дороже порога " + num(resort.threshold) })
          : el("span", { class: "chip", text: "порог " + num(resort.threshold) })
      );
    }
    if (resort.min_ever && resort.min_ever.total < best.total) {
      chips.append(el("span", { class: "chip", text: "минимум был " + num(resort.min_ever.total) }));
    }
    if (chips.childNodes.length) card.append(chips);
    return card;
  }

  function renderOverview() {
    document.title = "Билеты на горнолыжку";
    const cards = el("div", { class: "cards" });
    DATA.resorts.forEach((r) => cards.append(resortCard(r)));
    appPanel = el("section", { class: "panel app-panel", hidden: true, "aria-label": "Приложение и уведомления" });
    app.replaceChildren(appPanel, cards, footer());
  }

  // ---------- приложение: установка и пуш-уведомления --------------------------------
  let appPanel = null;
  let appNote = null;
  let installPrompt = null;
  let pushError = "";

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
  window.addEventListener("beforeinstallprompt", (ev) => {
    ev.preventDefault();
    installPrompt = ev;
    refreshAppPanel();
  });
  window.addEventListener("appinstalled", () => {
    installPrompt = null;
    refreshAppPanel();
  });

  const isStandalone = () => window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

  function getStored(key) {
    try {
      return localStorage.getItem(key);
    } catch (err) {
      return null;
    }
  }
  function setStored(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (err) {
      /* приватный режим — просто не запоминаем */
    }
  }

  function activeRegistration() {
    if (!("serviceWorker" in navigator)) return Promise.resolve(null);
    return Promise.race([navigator.serviceWorker.ready, new Promise((resolve) => setTimeout(() => resolve(null), 8000))]);
  }

  function b64urlToBytes(text) {
    const padded = text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4);
    return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
  }
  function bytesToB64url(buffer) {
    let binary = "";
    new Uint8Array(buffer).forEach((b) => (binary += String.fromCharCode(b)));
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  async function deviceId(endpoint) {
    const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
    return Array.from(new Uint8Array(hash), (b) => b.toString(16).padStart(2, "0")).join("").slice(0, 12);
  }

  async function pushState() {
    const conf = DATA && DATA.push;
    if (!conf || !conf.vapid || !DATA.repo) return { kind: "off" };
    if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
      return { kind: isIOS() && !isStandalone() ? "ios-install" : "unsupported" };
    }
    if (Notification.permission === "denied") return { kind: "denied" };
    const reg = await activeRegistration();
    if (!reg) return { kind: "unsupported" };
    const sub = await reg.pushManager.getSubscription();
    if (!sub) return { kind: "none" };
    const id = await deviceId(sub.endpoint);
    const key = sub.options && sub.options.applicationServerKey ? bytesToB64url(sub.options.applicationServerKey) : conf.vapid;
    if (key !== conf.vapid) return { kind: "stale", sub: sub, id: id };
    if ((conf.devices || []).includes(id)) return { kind: "on", sub: sub, id: id };
    return { kind: "pending", sub: sub, id: id };
  }

  function githubNewFile(sub, id) {
    return "https://github.com/" + DATA.repo + "/new/main?filename=" + encodeURIComponent("push/" + id + ".json") +
      "&value=" + encodeURIComponent(JSON.stringify(sub.toJSON(), null, 2));
  }

  async function enablePush(button) {
    button.disabled = true;
    pushError = "";
    try {
      const permission = await Notification.requestPermission();
      if (permission === "granted") {
        const reg = await activeRegistration();
        if (!reg) throw new Error("фоновый обработчик не запустился, обновите страницу");
        const old = await reg.pushManager.getSubscription();
        if (old) await old.unsubscribe();
        await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64urlToBytes(DATA.push.vapid) });
      }
    } catch (err) {
      pushError = "Не получилось включить уведомления: " + (err && err.message ? err.message : String(err));
    }
    refreshAppPanel();
  }

  function appItem(text, ...actions) {
    return el("div", { class: "app-item" }, el("p", { class: "app-text" }, text), actions.length ? el("div", { class: "app-actions" }, actions) : null);
  }

  async function refreshAppPanel() {
    const panel = appPanel;
    if (!DATA || (!panel && !appNote)) return;
    const items = [];
    if (!isStandalone()) {
      if (installPrompt) {
        items.push(appItem("Установите приложение: иконка на главном экране, открывается без браузера и без сети.",
          el("button", {
            class: "button", type: "button", text: "Установить",
            onclick: async () => {
              const prompt = installPrompt;
              installPrompt = null;
              prompt.prompt();
              try { await prompt.userChoice; } catch (err) { /* закрыли окно */ }
              refreshAppPanel();
            },
          })));
      } else if (!isIOS() && getStored("hide-install-hint") !== "1") {
        items.push(appItem("Установите приложение на главный экран — так оно открывается как обычное и присылает уведомления.",
          el("a", { class: "button", href: "#help", text: "Как установить" }),
          el("button", { class: "link-btn", type: "button", text: "Уже установил", onclick: () => { setStored("hide-install-hint", "1"); refreshAppPanel(); } })));
      } else if (isIOS() && getStored("hide-ios-hint") !== "1") {
        items.push(appItem("Установить на iPhone: в Safari нажмите «Поделиться» (квадрат со стрелкой), затем «На экран „Домой“».",
          el("button", { class: "link-btn", type: "button", text: "Понятно", onclick: () => { setStored("hide-ios-hint", "1"); refreshAppPanel(); } })));
      }
    }

    let state;
    try {
      state = await pushState();
    } catch (err) {
      state = { kind: "unsupported" };
    }
    const note = [];
    if (state.kind === "none" && getStored("hide-push-offer") === "1") {
      note.push(el("button", {
        class: "link-btn", type: "button", text: "Включить уведомления в приложении",
        onclick: () => { setStored("hide-push-offer", "0"); refreshAppPanel(); },
      }));
    } else if (state.kind === "none") {
      items.push(appItem("Уведомления о ценах прямо на этот телефон: новый минимум, цена ниже порога, утренняя сводка.",
        el("button", { class: "button", type: "button", text: "Включить уведомления", onclick: (ev) => enablePush(ev.currentTarget) }),
        el("button", { class: "link-btn", type: "button", text: "Не сейчас", onclick: () => { setStored("hide-push-offer", "1"); refreshAppPanel(); } })));
    } else if (state.kind === "pending") {
      const json = JSON.stringify(state.sub.toJSON(), null, 2);
      const copy = el("button", { class: "link-btn", type: "button", text: "Скопировать" });
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(json);
          copy.textContent = "Скопировано";
        } catch (err) {
          copy.textContent = "Выделите текст и скопируйте вручную";
        }
      });
      items.push(el("div", { class: "app-item" },
        el("p", { class: "app-text" },
          el("b", { text: "Остался один шаг. " }),
          "Откроется GitHub с готовым файлом подписки — нажмите «Commit changes». Через пару минут придёт пробное уведомление, а здесь появится галочка."),
        el("div", { class: "app-actions" },
          el("a", { class: "button", href: githubNewFile(state.sub, state.id), target: "_blank", rel: "noopener", text: "Открыть GitHub" })),
        el("details", { class: "manual" },
          el("summary", { text: "Если файл открылся пустым" }),
          el("p", { class: "small muted", text: "Создайте в репозитории файл push/" + state.id + ".json и вставьте в него этот текст:" }),
          el("pre", { class: "sub-json", text: json }),
          copy)));
    } else if (state.kind === "stale") {
      items.push(appItem("Ключ уведомлений сменился — подключите их заново.",
        el("button", { class: "button", type: "button", text: "Подключить заново", onclick: (ev) => enablePush(ev.currentTarget) })));
    } else if (state.kind === "ios-install") {
      items.push(appItem("На iPhone уведомления работают в установленном приложении (iOS 16.4 и новее): добавьте его на экран «Домой» и откройте оттуда."));
    } else if (state.kind === "denied") {
      note.push("Уведомления в приложении запрещены — их можно разрешить в настройках телефона или браузера.");
    } else if (state.kind === "on") {
      note.push("✓ Уведомления приходят на этот телефон · ",
        el("button", {
          class: "link-btn inline", type: "button", text: "отключить",
          onclick: async () => {
            try { await state.sub.unsubscribe(); } catch (err) { /* уже отписан */ }
            refreshAppPanel();
          },
        }));
    }
    if (pushError) items.push(el("p", { class: "app-error", text: pushError }));
    if (panel && panel === appPanel) {
      panel.replaceChildren(el("h3", { text: "Приложение" }), ...items);
      panel.hidden = !items.length;
    }
    if (appNote) {
      appNote.replaceChildren(...note);
      appNote.hidden = !note.length;
    }
  }

  // ---------- страница курорта ----------------------------------------------------
  function renderResort(resort, openKey) {
    document.title = resort.name + " — билеты";
    const best = bestTrip(resort);
    const updated = parseUtc(DATA.updated);
    const nodes = [];

    nodes.push(el("a", { class: "back", href: "#" }, "← Все курорты"));
    nodes.push(el("h2", { text: resort.name }));
    nodes.push(
      el("p", {
        class: "muted small",
        text:
          (resort.airports.length > 1 ? "Аэропорты: " : "Аэропорт: ") +
          resort.airports.map((a) => airportName(a) + " (" + a + ")").join(", ") +
          " · " + nightsText(resort.nights),
      })
    );

    // плитки
    const tiles = el("div", { class: "tiles" });
    tiles.append(el("div", { class: "tile hero" },
      el("div", { class: "label", text: "Лучшая цена сейчас" }),
      el("div", { class: "value", text: best ? rub(best.total) : "—" }),
      best ? el("div", { class: "note" }, deltaEl(resort.change_24h) || "туда-обратно с багажом") : el("div", { class: "note", text: "нет цен на прямые рейсы" })));
    if (resort.min_ever) {
      tiles.append(el("div", { class: "tile" },
        el("div", { class: "label", text: "Минимум за всё время" }),
        el("div", { class: "value", text: rub(resort.min_ever.total) }),
        el("div", { class: "note", text: resort.min_ever.time ? "замечен " + moment(parseUtc(resort.min_ever.time)) : "" })));
    }
    if (resort.correction) {
      const fix = resort.correction;
      tiles.append(el("div", { class: "tile" },
        el("div", { class: "label", text: "Поправка по вашим проверкам" }),
        el("div", { class: "value", text: (fix > 0 ? "+" : "−") + rub(Math.abs(fix)) }),
        el("div", { class: "note", text: best ? "реальная цена ≈ " + rub(best.total + fix) : "по проверкам за 2 недели" })));
    }
    const thr = resort.threshold;
    let thrNote = "уведомлю, когда цена опустится до порога";
    if (!thr) thrNote = "не задан";
    else if (best && best.total <= thr) thrNote = "✓ цена не выше порога";
    else if (best) thrNote = "ещё " + rub(best.total - thr) + " до порога";
    tiles.append(el("div", { class: "tile" },
      el("div", { class: "label", text: "Порог для уведомления" }),
      el("div", { class: "value", text: thr ? rub(thr) : "—" }),
      el("div", { class: "note" }, thrNote)));
    nodes.push(tiles);

    if (best) {
      nodes.push(el("section", { class: "panel" },
        el("div", { class: "panel-head" }, el("h3", { text: "Лучший вариант: " + dayLabel(best.depart) + " → " + dayLabel(best.return) })),
        el("div", { class: "muted small", text: nightsWord(best.nights) + (routeText(best, resort) ? " · " + routeText(best, resort) : "") }),
        tripDetail(best, resort)));
    }

    // динамика лучшей цены
    const historyPanel = el("section", { class: "panel" });
    const historyChart = el("div");
    const historyTable = el("details", { class: "table-view" });
    function paintHistory() {
      const ranges = { "7": 7 * DAY, "30": 30 * DAY, all: Infinity };
      const since = updated - ranges[UI.range];
      const rows = BEST.filter((b) => b.resort === resort.id && b.t >= since);
      drawChart(historyChart, {
        mode: "step",
        kind: "time",
        height: 210,
        threshold: thr,
        extendTo: updated,
        aria: "Лучшая цена по проверкам",
        empty: "Пока одна проверка — график появится после следующей.",
        points: rows.map((b) => ({
          x: b.t,
          y: b.total,
          tip: [b.total ? rub(b.total) : "нет цены", moment(b.t) + " МСК", b.depart ? dayLabel(b.depart) + ", " + nightsWord(b.nights) : ""],
        })),
      });
      const table = el("table", null, el("tr", null, el("th", { text: "Проверка, МСК" }), el("th", { class: "num", text: "Цена" }), el("th", { text: "Поездка" })));
      rows.slice(-120).reverse().forEach((b) => {
        table.append(el("tr", null,
          el("td", { text: moment(b.t) }),
          el("td", { class: "num", text: b.total ? rub(b.total) : "—" }),
          el("td", { text: b.depart ? dayLabel(b.depart) + ", " + nightsWord(b.nights) : "" })));
      });
      historyTable.replaceChildren(el("summary", { text: "Таблицей" }), el("div", { class: "table-scroll" }, table));
    }
    const rangeSeg = () =>
      seg([["7", "7 дней"], ["30", "30 дней"], ["all", "Всё"]], UI.range, (v) => {
        UI.range = v;
        historyPanel.querySelector(".seg").replaceWith(rangeSeg());
        paintHistory();
      }, "Период");
    historyPanel.append(
      el("div", { class: "panel-head" }, el("h3", { text: "Как менялась лучшая цена" }), rangeSeg()),
      historyChart,
      historyTable
    );
    nodes.push(historyPanel);

    // цены по датам вылета + список
    const datesPanel = el("section", { class: "panel" });
    const datesChart = el("div");
    const list = el("ol", { class: "trip-list" });
    const controls = el("div", { class: "controls" });
    if (!UI.nights[resort.id]) {
      const fromHash = openKey ? +openKey.split("/")[1] : null;
      UI.nights[resort.id] = resort.nights.includes(fromHash) ? fromHash : best ? best.nights : resort.nights[0];
    }
    const bestTotal = best ? best.total : null;

    function openRow(key, scroll) {
      let row = list.querySelector('[data-key="' + key + '"]');
      if (!row && resort.trips.some((t) => t.key === key && t.low) && !UI.showLow) {
        UI.showLow = true;
        paintDates();
        row = list.querySelector('[data-key="' + key + '"]');
      }
      if (!row) return;
      const btn = row.querySelector(".row-btn");
      if (btn.getAttribute("aria-expanded") !== "true") btn.click();
      if (scroll) row.scrollIntoView({ block: "center", behavior: "smooth" });
    }

    function paintDates() {
      const nights = UI.nights[resort.id];
      const trips = resort.trips.filter((t) => t.nights === nights);
      const eligible = trips.filter((t) => t.total && !t.low);
      const minTrip = eligible.length ? eligible.reduce((a, b) => (b.total < a.total ? b : a)) : null;
      const start = dayMs(DATA.period.start);
      const lastDepart = dayMs(DATA.period.end) - nights * DAY;
      drawChart(datesChart, {
        mode: "dots",
        kind: "day",
        height: 210,
        threshold: thr,
        xDomain: [start, lastDepart],
        // Полоса — все даты вылета, при которых поездка задевает конференцию: от (начало − ночи) до конца.
        bands: (DATA.low_priority || []).map((p) => ({ x0: dayMs(p.from) - nights * DAY - DAY / 2, x1: dayMs(p.to) + DAY / 2, label: "поездка задевает " + (p.label || "эти даты") })),
        aria: "Цена по дате вылета",
        empty: "Нет цен на прямые рейсы на эти даты.",
        mark: minTrip ? { x: dayMs(minTrip.depart), y: minTrip.total, label: num(minTrip.total) } : null,
        onPick: (p) => openRow(p.key, true),
        points: trips.filter((t) => t.total).map((t) => ({
          x: dayMs(t.depart),
          y: t.total,
          gray: !!t.low,
          key: t.key,
          tip: [rub(t.total), dayLabel(t.depart) + " → " + dayLabel(t.return), t.low ? "задевает даты «" + t.low + "»" : airlinesText(t)],
        })),
      });

      const sorted = trips.slice();
      if (UI.sort === "price") sorted.sort((a, b) => (a.total || Infinity) - (b.total || Infinity) || a.depart.localeCompare(b.depart));
      const lowCount = trips.filter((t) => t.low).length;
      list.replaceChildren(...sorted.filter((t) => UI.showLow || !t.low).map((t) => tripRow(t)));
      lowToggle.hidden = lowCount === 0;
      lowToggle.textContent = UI.showLow ? "Скрыть даты конференции" : "Показать поездки на даты конференции (" + lowCount + ")";
    }

    const lowToggle = el("button", { class: "link-btn", type: "button" });
    lowToggle.addEventListener("click", () => {
      UI.showLow = !UI.showLow;
      paintDates();
    });

    function tripRow(trip) {
      const cls = ["trip-row"];
      if (trip.low) cls.push("low");
      if (best && trip.key === best.key) cls.push("best");
      if (!trip.total) cls.push("none");
      const li = el("li", { class: cls.join(" "), "data-key": trip.key });
      const sub = [];
      if (trip.low) sub.push(trip.low);
      sub.push(nightsWord(trip.nights));
      if (trip.total) {
        const route = routeText(trip, resort);
        if (route) sub.push(route);
        sub.push(airlinesText(trip));
      } else {
        sub.push("нет прямых рейсов в кэше");
      }
      let over = "";
      if (trip.total && bestTotal !== null && trip.total !== bestTotal) {
        over = (trip.total > bestTotal ? "+" : "−") + num(Math.abs(trip.total - bestTotal));
      }
      const detail = el("div", { class: "row-detail", hidden: true });
      const btn = el("button", { class: "row-btn", type: "button", "aria-expanded": "false" },
        el("span", { class: "d", text: dayLabel(trip.depart) + " → " + dayLabel(trip.return) }),
        el("span", { class: "t", text: trip.total ? rub(trip.total) : "нет цены" }),
        el("span", { class: "s", text: sub.join(" · ") }),
        el("span", { class: "o", text: over }));
      btn.addEventListener("click", () => {
        const open = btn.getAttribute("aria-expanded") === "true";
        btn.setAttribute("aria-expanded", String(!open));
        detail.hidden = open;
        try {
          history.replaceState(null, "", "#" + resort.id + (open ? "" : "/" + trip.key));
        } catch (err) {
          /* во встроенном окне адрес менять нельзя — это не мешает */
        }
        if (!open && !detail.childNodes.length) fillDetail(detail, trip);
      });
      li.append(btn, detail);
      return li;
    }

    function fillDetail(box, trip) {
      if (trip.legs) box.append(tripDetail(trip, resort));
      const hist = el("div", { class: "hist" }, el("p", { class: "muted small", text: "Загружаю историю цены…" }));
      box.append(el("div", { class: "leg-main", text: "История цены этой поездки" }), hist);
      tripHistory(resort.id).then((rows) => {
        const mine = rows.filter((r) => r.depart === trip.depart && +r.nights === trip.nights);
        if (!mine.length) {
          hist.replaceChildren(el("p", { class: "muted small", text: "Цен на эту поездку в кэше пока не было." }));
          return;
        }
        if (mine.length === 1 && mine[0].total) {
          hist.replaceChildren(el("p", { class: "muted small", text: "Цена не менялась с " + moment(parseUtc(mine[0].time)) + "." }));
          return;
        }
        drawChart(hist, {
          mode: "step",
          kind: "time",
          height: 160,
          threshold: trip.low ? null : thr,
          extendTo: updated,
          aria: "История цены поездки",
          points: mine.map((r) => ({
            x: parseUtc(r.time),
            y: r.total ? +r.total : null,
            tip: [r.total ? rub(+r.total) : "нет цены", "с " + moment(parseUtc(r.time)) + " МСК"],
          })),
        });
      });
    }

    const nightsSeg = () =>
      seg(resort.nights.map((n) => [n, nightsWord(n)]), UI.nights[resort.id], (v) => {
        UI.nights[resort.id] = v;
        controls.replaceChildren(...controlItems());
        paintDates();
      }, "Ночей");
    const sortSeg = () =>
      seg([["date", "По дате"], ["price", "По цене"]], UI.sort, (v) => {
        UI.sort = v;
        controls.replaceChildren(...controlItems());
        paintDates();
      }, "Сортировка");
    const controlItems = () => (resort.nights.length > 1 ? [nightsSeg(), sortSeg()] : [sortSeg()]);
    controls.append(...controlItems());

    datesPanel.append(
      el("div", { class: "panel-head" }, el("h3", { text: "Цены по дате вылета" })),
      datesChart,
      el("p", { class: "hint", text: lowHint() }),
      controls,
      list,
      lowToggle
    );
    nodes.push(datesPanel, footer());

    app.replaceChildren(...nodes);
    paintHistory();
    paintDates();
    if (openKey) openRow(openKey, true);
  }


  // ---------- инструкция: установка и как всё работает ---------------------------------
  function platform() {
    const ua = navigator.userAgent;
    if (isIOS()) return "ios";
    if (/Android/i.test(ua)) return /SamsungBrowser/i.test(ua) ? "samsung" : /YaBrowser/i.test(ua) ? "yandex" : "android";
    if (/Macintosh/i.test(ua) && /Safari/i.test(ua) && !/Chrome|Edg/i.test(ua)) return "mac";
    return "desktop";
  }

  function helpSection(id, title, steps, note, open) {
    return el("details", { class: "help-item", open: open || null },
      el("summary", { text: title }),
      el("ol", null, steps.map((s) => el("li", { text: s }))),
      note ? el("p", { class: "small muted", text: note }) : null);
  }

  function renderHelp() {
    document.title = "Как установить — билеты";
    const current = platform();
    const sections = [
      ["android", "Android — Chrome", [
        "Откройте адрес приложения в Chrome.",
        "Нажмите «Установить» в панели «Приложение» вверху главной страницы. Если её нет — меню ⋮ → «Установить приложение» (или «Добавить на гл. экран» → «Установить»).",
        "Подтвердите. Иконка «Горнолыжка» появится на главном экране.",
        "Открывайте приложение с иконки — оно запускается без адресной строки, как обычное.",
      ], "Если Chrome пишет «Это приложение нельзя установить», обновите страницу (потяните вниз), подождите пару секунд и попробуйте снова."],
      ["samsung", "Android — Samsung Internet", [
        "Откройте адрес в Samsung Internet.",
        "Нажмите значок «скачать» в адресной строке или меню ☰ → «Добавить страницу на» → «Главный экран».",
        "Подтвердите «Добавить».",
      ], "Если что-то не получается — откройте адрес в Chrome, там установка работает надёжнее всего."],
      ["yandex", "Android — Яндекс Браузер", [
        "Откройте адрес в Яндекс Браузере.",
        "Меню ⋮ → «Добавить ярлык» или «Установить приложение».",
      ], "Яндекс Браузер иногда создаёт обычный ярлык, и уведомления в нём могут не работать. Для уведомлений лучше Chrome."],
      ["ios", "iPhone и iPad — Safari", [
        "Откройте адрес именно в Safari (в Chrome на iPhone установить нельзя).",
        "Нажмите «Поделиться» — квадрат со стрелкой вверх внизу экрана (на iPad — вверху).",
        "Пролистайте и выберите «На экран „Домой“» → «Добавить».",
        "Открывайте приложение только с иконки на экране «Домой» — уведомления работают только так.",
      ], "Уведомления на iPhone и iPad работают с iOS 16.4 и новее."],
      ["desktop", "Компьютер — Chrome или Edge", [
        "Откройте адрес в Chrome или Edge.",
        "Нажмите значок установки справа в адресной строке (монитор со стрелкой) или меню ⋮ → «Установить приложение».",
        "Приложение откроется в отдельном окне и появится в меню «Пуск» / Launchpad.",
      ], null],
      ["mac", "Mac — Safari", [
        "Откройте адрес в Safari (macOS 14 Sonoma и новее).",
        "Меню «Файл» → «Добавить в Dock».",
      ], null],
    ];
    const nodes = [
      el("a", { class: "back", href: "#" }, "← Все курорты"),
      el("h2", { text: "Как установить" }),
      el("p", { class: "muted", text: "Это приложение не скачивается из магазина: оно устанавливается прямо из браузера и занимает почти ноль места. Ваше устройство открыто первым." }),
      el("section", { class: "panel" },
        sections.slice().sort((a, b) => (a[0] === current ? -1 : b[0] === current ? 1 : 0))
          .map(([id, title, steps, note]) => helpSection(id, title, steps, note, id === current))),
      el("h2", { text: "Уведомления" }),
      el("section", { class: "panel" },
        el("ol", { class: "help-list" },
          el("li", { text: "Откройте установленное приложение и нажмите «Включить уведомления», затем разрешите их." }),
          el("li", { text: "Нажмите «Открыть GitHub» — откроется файл с готовым текстом. Нажмите «Commit changes» и ещё раз «Commit changes»." }),
          el("li", { text: "Через 1–2 минуты придёт «✅ Уведомления включены». Внизу главной страницы появится «✓ Уведомления приходят на этот телефон»." })),
        el("p", { class: "small muted", text: "Если GitHub пишет «A file with the same name already exists» — этот телефон уже подключён, нажмите Cancel. Подключить нужно каждое устройство отдельно. Ночью (23:00–8:00) уведомления приходят без звука." })),
      el("h2", { text: "Как это работает" }),
      el("section", { class: "panel help-text" },
        el("p", { text: "Каждые 30 минут сервис проверяет цены на прямые рейсы из Москвы на все курорты и все подходящие даты, считает поездки туда-обратно и сохраняет историю." }),
        el("p", { text: "Цена указана на 1 взрослого туда-обратно вместе с примерной доплатой за провоз лыж или сноуборда." }),
        el("p", { text: "Цены берутся из Aviasales: это билеты, которые находили в поиске за последние дни. Живая цена может немного отличаться — перед покупкой нажимайте «Проверить на Aviasales»." }),
        el("p", { text: "Уведомление приходит, когда лучшая цена по курорту опускается до порога, становится минимальной за всё время или падает на 5% за сутки. Каждое утро после 8:00 — сводка." }),
        el("p", { text: "Поездки, которые задевают даты конференции, показаны серым и в уведомлениях не участвуют." }),
        el("p", { text: "Без интернета приложение показывает последние сохранённые цены. Обновления приложения приходят сами — достаточно закрыть его и открыть снова." })),
      footer(),
    ];
    app.replaceChildren(...nodes);
  }

  // ---------- навигация ------------------------------------------------------------
  function route(keepScroll) {
    hiders = [];
    appPanel = null;
    appNote = null;
    const parts = decodeURIComponent(location.hash.replace(/^#/, "")).split("/");
    const resort = DATA.resorts.find((r) => r.id === parts[0]);
    if (parts[0] === "help") renderHelp();
    else if (resort) renderResort(resort, parts.length >= 3 ? parts[1] + "/" + parts[2] : null);
    else renderOverview();
    refreshAppPanel();
    if (!keepScroll && !(parts.length >= 3)) window.scrollTo(0, 0);
  }

  async function loadData() {
    offline = false;
    let summary;
    try {
      summary = JSON.parse(await fetchText("data/summary.json"));
    } catch (err) {
      updatedEl.textContent = "";
      app.replaceChildren(
        el("div", { class: "empty" },
          el("h2", { text: "Данных пока нет" }),
          el("p", { class: "muted", text: navigator.onLine === false
            ? "Нет сети, а сохранённых цен на телефоне ещё нет. Откройте приложение, когда появится интернет."
            : "Первая проверка цен ещё не прошла. Она запускается автоматически каждые 30 минут — загляните чуть позже." }))
      );
      return false;
    }
    DATA = summary;
    try {
      BEST = parseCSV(await fetchText("data/best.csv")).map((r) => ({
        t: parseUtc(r.time),
        resort: r.resort,
        total: r.total ? +r.total : null,
        depart: r.depart,
        nights: r.nights ? +r.nights : null,
      }));
    } catch (err) {
      BEST = [];
    }
    const updated = parseUtc(DATA.updated);
    updatedEl.replaceChildren("Обновлено " + moment(updated) + " МСК");
    if (offline) {
      updatedEl.append(" · ", el("span", { class: "warn", text: "нет сети, показаны сохранённые цены" }));
    } else if (Date.now() - updated > 9 * 3600e3) {
      updatedEl.append(" · ", el("span", { class: "warn", text: "давно не обновлялось" }));
    }
    loadedAt = Date.now();
    return true;
  }

  async function load() {
    if (!(await loadData())) return;
    lastWidth = window.innerWidth;
    route();
    window.addEventListener("hashchange", () => route(false));
    window.addEventListener(
      "resize",
      debounce(() => {
        if (window.innerWidth !== lastWidth) {
          lastWidth = window.innerWidth;
          route(true);
        }
      }, 200)
    );
    document.addEventListener("pointerdown", (ev) => hiders.forEach((fn) => fn(ev.target)));
    // Установленное приложение часто просто возвращается из фона — обновим цены.
    document.addEventListener("visibilitychange", async () => {
      if (document.visibilityState === "visible" && Date.now() - loadedAt > 3 * 60e3) {
        if (await loadData()) route(true);
      }
    });
  }

  load();
})();
