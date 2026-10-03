(() => {
  "use strict";

  const KIND = {
    major: { label: "大型アップデート", icon: "ti-star", order: 0 },
    patch: { label: "パッチ・クライアント更新", icon: "ti-tool", order: 1 },
    maint: { label: "メンテナンス", icon: "ti-settings", order: 2 },
    pre: { label: "事前ダウンロード", icon: "ti-download", order: 3 },
    event: { label: "イベント", icon: "ti-confetti", order: 4 },
    news: { label: "お知らせ", icon: "ti-news", order: 5 },
  };
  const UPDATE_KINDS = ["major", "patch", "maint"];
  const STATUS = { confirmed: ["確定", "ok"], scheduled: ["予定", "warn"], announced: ["告知", ""] };
  const W = "日月火水木金土";
  const NARROW = 600;
  const $ = (id) => document.getElementById(id);

  const store = {
    get(key, fallback) {
      try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* 保存できなくても表示は続ける */ }
    },
  };

  // ------------------------------------------------------------ 日付（すべて日本時間で扱う）
  const fmtParts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  });
  function jst(date) {
    const p = {};
    for (const x of fmtParts.formatToParts(date)) p[x.type] = x.value;
    return { y: +p.year, m: +p.month, d: +p.day, H: +p.hour, M: +p.minute };
  }
  const pad = (n) => String(n).padStart(2, "0");
  const dkey = (y, m, d) => `${y}-${pad(m)}-${pad(d)}`;
  const isDateOnly = (s) => /^\d{4}-\d{2}-\d{2}$/.test(s);
  function dayOf(s) {
    if (isDateOnly(s)) return s;
    const j = jst(new Date(s));
    return dkey(j.y, j.m, j.d);
  }
  function timeOf(s) {
    if (isDateOnly(s)) return "";
    const j = jst(new Date(s));
    return `${pad(j.H)}:${pad(j.M)}`;
  }
  function addDays(key, n) {
    const [y, m, d] = key.split("-").map(Number);
    const t = new Date(Date.UTC(y, m - 1, d + n));
    return dkey(t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate());
  }
  function weekday(key) {
    const [y, m, d] = key.split("-").map(Number);
    return new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  }
  function longDay(key) {
    const [y, m, d] = key.split("-").map(Number);
    return `${y}/${m}/${d}（${W[weekday(key)]}）`;
  }
  // 事前ダウンロード期間の帯の最終日（午前中に終わる場合は前日までを帯にする）
  function preEndDay(item) {
    const start = dayOf(item.start);
    if (!item.end) return start;
    let end = dayOf(item.end);
    if (!isDateOnly(item.end) && jst(new Date(item.end)).H < 12) end = addDays(end, -1);
    return end < start ? start : end;
  }

  // ------------------------------------------------------------ 状態
  const state = {
    data: null,
    games: {},
    hidden: new Set(store.get("hiddenGames", [])),
    dlOnly: store.get("dlOnly", true),
    view: null,
    auto: null,
    month: null,
    today: null,
  };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isUpdate = (it) => UPDATE_KINDS.includes(it.kind);
  const tentative = (it) => isUpdate(it) && it.status !== "confirmed" && it.dl !== "no";
  function visible(it) {
    if (state.hidden.has(it.game) || !state.games[it.game]) return false;
    if (!state.dlOnly) return true;
    return it.kind === "pre" || (isUpdate(it) && (it.dl === "yes" || it.dl === "likely"));
  }
  const fill = (g) => `background:${g.color};color:${g.text_color};border-color:${g.color};`;
  const outline = (g) => `border-color:${g.color};color:var(--text);`;
  const tint = (g) => `background:color-mix(in srgb, ${g.color} 22%, transparent);border:1.5px dashed ${g.color};`;
  const byOrder = (a, b) => KIND[a.kind].order - KIND[b.kind].order || String(a.start).localeCompare(String(b.start));

  function dlText(it) {
    if (it.dl === "yes") return it.status === "confirmed" ? "あり（確定）" : "あり（公式告知に記載）";
    if (it.dl === "likely") return it.status === "confirmed" ? "あり見込み（告知の種類から推定）" : "あり見込み（告知の種類から推定・配信後に確定）";
    if (it.dl === "no") return "なし";
    return "不明";
  }
  function whenText(it) {
    const sd = dayOf(it.start);
    let s = longDay(sd);
    if (!isDateOnly(it.start)) s += " " + timeOf(it.start);
    if (it.end) {
      const ed = dayOf(it.end);
      if (ed !== sd) s += " 〜 " + longDay(ed) + (isDateOnly(it.end) ? "" : " " + timeOf(it.end));
      else if (!isDateOnly(it.end)) s += "〜" + timeOf(it.end);
    }
    return s;
  }

  // ------------------------------------------------------------ カレンダー
  function evButton(it) {
    const g = state.games[it.game];
    const t = tentative(it);
    return `<button class="ev${t ? " tentative" : ""}" data-id="${esc(it.id)}" title="${esc(g.name + " " + it.title)}" style="${t ? outline(g) : fill(g)}">` +
      `<i class="ti ${KIND[it.kind].icon}" aria-hidden="true"></i> ${esc(g.name)}</button>`;
  }

  function calendarHTML() {
    const { y, m } = state.month;
    const firstWd = new Date(Date.UTC(y, m - 1, 1)).getUTCDay();
    const days = new Date(Date.UTC(y, m, 0)).getUTCDate();
    const startKey = addDays(dkey(y, m, 1), -firstWd);
    const weeks = Math.ceil((firstWd + days) / 7);
    const items = state.data.items.filter(visible);
    const pres = items.filter((i) => i.kind === "pre").map((i) => ({ it: i, s: dayOf(i.start), e: preEndDay(i) }));
    const singles = items.filter((i) => i.kind !== "pre");

    let h = '<div class="wk head">' + [...W].map((w, i) =>
      `<div class="bg dn ${i === 0 ? "sun" : i === 6 ? "sat" : ""}">${w}</div>`).join("") + "</div>";
    for (let w = 0; w < weeks; w++) {
      const keys = Array.from({ length: 7 }, (_, c) => addDays(startKey, w * 7 + c));
      const lo = keys[0], hi = keys[6];
      const bars = [];
      for (const p of pres) {
        if (p.e < lo || p.s > hi) continue;
        const s = p.s < lo ? lo : p.s, e = p.e > hi ? hi : p.e;
        const c = keys.indexOf(s);
        bars.push({ ...p, c, span: keys.indexOf(e) - c + 1, first: s === p.s, last: e === p.e });
      }
      const nb = bars.length;
      let g = `<div class="wk" style="grid-template-rows:auto ${nb ? `repeat(${nb},auto) ` : ""}1fr">`;
      keys.forEach((k, c) => {
        const cls = ["bg", k === state.today ? "today" : "", +k.slice(5, 7) !== m ? "other" : ""].join(" ");
        g += `<div class="${cls}" style="grid-column:${c + 1};grid-row:1/-1"></div>`;
        g += `<div class="dn ${k === state.today ? "today" : ""} ${c === 0 ? "sun" : c === 6 ? "sat" : ""}" style="grid-column:${c + 1};grid-row:1">${+k.slice(8)}</div>`;
      });
      bars.forEach((b, j) => {
        const gm = state.games[b.it.game];
        const cut = (b.first ? "" : "border-left-style:none;border-top-left-radius:0;border-bottom-left-radius:0;margin-left:0;") +
          (b.last ? "" : "border-right-style:none;border-top-right-radius:0;border-bottom-right-radius:0;margin-right:0;");
        g += `<button class="bar" data-id="${esc(b.it.id)}" title="${esc(gm.name + " " + b.it.title)}" style="${tint(gm)}grid-column:${b.c + 1} / span ${b.span};grid-row:${j + 2};${cut}">` +
          `<i class="ti ti-download" aria-hidden="true"></i> ${esc(gm.name)} 事前DL</button>`;
      });
      keys.forEach((k, c) => {
        const its = singles.filter((i) => dayOf(i.start) === k).sort(byOrder);
        if (its.length) g += `<div class="cell-items" style="grid-column:${c + 1};grid-row:${nb + 2}">${its.map(evButton).join("")}</div>`;
      });
      h += g + "</div>";
    }
    return h;
  }

  // 一番長いラベルが 1 行に収まる大きさに、カレンダー内の文字サイズをそろえる
  function fitText() {
    const v = $("view");
    const els = [...v.querySelectorAll(".ev,.bar")];
    let f = 11;
    v.style.setProperty("--fs", f + "px");
    const over = () => els.some((el) => el.scrollWidth > el.clientWidth + 0.5);
    while (f > 7 && over()) { f -= 0.5; v.style.setProperty("--fs", f + "px"); }
  }

  // ------------------------------------------------------------ 一覧
  function listHTML() {
    const { y, m } = state.month;
    const first = dkey(y, m, 1);
    const last = dkey(y, m, new Date(Date.UTC(y, m, 0)).getUTCDate());
    const rows = [];
    for (const it of state.data.items.filter(visible)) {
      let d = dayOf(it.start);
      if (it.kind === "pre" && d < first && preEndDay(it) >= first) d = first;  // 前月から続く事前DL
      if (d >= first && d <= last) rows.push({ d, it });
    }
    if (!rows.length) return '<div class="empty">この月に該当する予定はありません</div>';
    rows.sort((a, b) => a.d.localeCompare(b.d) || byOrder(a.it, b.it));
    let h = "", cur = "";
    for (const { d, it } of rows) {
      if (d !== cur) {
        cur = d;
        h += `<div class="day-head ${d === state.today ? "today" : ""}">${longDay(d)}${d === state.today ? "　今日" : ""}</div>`;
      }
      const g = state.games[it.game];
      const pre = it.kind === "pre";
      const t = tentative(it);
      const badge = pre ? tint(g) + "color:var(--text);" : t ? outline(g) + "border:1.5px solid " + g.color + ";" : fill(g);
      const st = STATUS[it.status] || ["", ""];
      const sub = [pre ? whenText(it) : (isDateOnly(it.start) ? "終日" : timeOf(it.start) + (it.end && dayOf(it.end) === d && !isDateOnly(it.end) ? "〜" + timeOf(it.end) : ""))];
      if (it.size) sub.push(it.size);
      h += `<button class="li" data-id="${esc(it.id)}" style="${pre ? "border:1.5px dashed " + g.color : ""}">` +
        `<span class="bar-l" style="background:${g.color}"></span><span class="body">` +
        `<span class="row1"><span class="gbadge" style="${badge}">${esc(g.name)}</span>` +
        `<span class="tag"><i class="ti ${KIND[it.kind].icon}" aria-hidden="true"></i> ${KIND[it.kind].label}</span>` +
        (isUpdate(it) || pre ? `<span class="tag ${st[1]}">${st[0]}</span>` : "") +
        (isUpdate(it) || pre ? `<span class="tag ${it.dl === "yes" ? "ok" : it.dl === "likely" ? "warn" : ""}">DL ${it.dl === "yes" ? "あり" : it.dl === "likely" ? "見込み" : it.dl === "no" ? "なし" : "不明"}</span>` : "") +
        (it.stale ? '<span class="tag warn">前回取得分</span>' : "") +
        `</span><span class="ttl">${esc(it.title)}</span><span class="sub">${esc(sub.filter(Boolean).join(" ・ "))}</span></span></button>`;
    }
    return h;
  }

  // ------------------------------------------------------------ 詳細
  function showDetail(id, anchor) {
    const it = state.data.items.find((i) => i.id === id);
    if (!it) return;
    $("detail").innerHTML = detailHTML(it);
    if (state.view === "list" && anchor) {
      // 一覧では、押した予定のすぐ下に詳細を開く（もう一度押すと閉じる）
      const open = $("view").querySelector(".inline-detail");
      const same = open && open.previousElementSibling === anchor;
      if (open) open.remove();
      if (!same) {
        const box = document.createElement("div");
        box.className = "detail inline-detail";
        box.innerHTML = detailHTML(it);
        anchor.after(box);
      }
    } else {
      $("detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }

  function detailHTML(it) {
    const g = state.games[it.game];
    const st = STATUS[it.status] || ["", ""];
    let h = `<div class="meta"><span class="gbadge" style="${fill(g)}">${esc(g.name)}</span>` +
      `<span class="tag"><i class="ti ${KIND[it.kind].icon}" aria-hidden="true"></i> ${KIND[it.kind].label}</span>` +
      `<span class="tag ${st[1]}">${st[0]}</span>` +
      (it.stale ? '<span class="tag warn">前回取得分（今回の取得に失敗）</span>' : "") + "</div>";
    h += `<h2>${esc(it.title)}</h2><dl>`;
    h += `<dt>日時</dt><dd>${esc(whenText(it))}${isDateOnly(it.start) ? "（終日）" : ""}</dd>`;
    if (isUpdate(it) || it.kind === "pre") {
      h += `<dt>ダウンロード</dt><dd>${esc(dlText(it))}</dd>`;
      h += `<dt>サイズ</dt><dd>${esc(it.size || "公表なし")}</dd>`;
    }
    if (it.basis && it.basis.length) h += `<dt>判定根拠</dt><dd><ul>${it.basis.map((b) => `<li>${esc(b)}</li>`).join("")}</ul></dd>`;
    if (it.sources && it.sources.length) {
      h += `<dt>情報源</dt><dd><ul>${it.sources.map((s) => s.url
        ? `<li><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.label)} <i class="ti ti-external-link" aria-hidden="true"></i></a></li>`
        : `<li>${esc(s.label)}</li>`).join("")}</ul></dd>`;
    }
    if (it.note) h += `<dt>注意</dt><dd>${esc(it.note)}</dd>`;
    h += "</dl>";
    if (it.excerpt) h += `<div class="excerpt">${esc(it.excerpt)}</div>`;
    return h;
  }

  // ------------------------------------------------------------ 画面の組み立て
  function renderChips() {
    const c = $("chips");
    c.innerHTML = "";
    for (const g of state.data.games) {
      const b = document.createElement("button");
      const on = !state.hidden.has(g.id);
      b.className = "chip";
      b.setAttribute("aria-pressed", String(on));
      b.style.cssText = fill(g);
      b.textContent = g.name;
      b.onclick = () => {
        on ? state.hidden.add(g.id) : state.hidden.delete(g.id);
        store.set("hiddenGames", [...state.hidden]);
        renderChips();
        render();
      };
      c.appendChild(b);
    }
  }

  function renderSources() {
    const rows = (state.data.sources || []).map((s) => {
      const names = s.games.map((id) => (state.games[id] || { name: id }).name).join("・");
      const last = s.last_success ? s.last_success.replace("T", " ").slice(0, 16) : "—";
      return `<tr><td><span class="tag ${s.ok ? "ok" : "ng"}">${s.ok ? "正常" : "取得失敗"}</span></td>` +
        `<td>${esc(s.label)}<br><span style="color:var(--text-3)">${esc(names)}</span></td>` +
        `<td>最終成功<br>${esc(last)}</td><td>${esc(s.message || "")}</td></tr>`;
    });
    $("sources").innerHTML = `<table>${rows.join("")}</table>`;
  }

  function render() {
    const { y, m } = state.month;
    $("month-label").textContent = `${y}年${m}月`;
    $("v-cal").setAttribute("aria-pressed", String(state.view === "cal"));
    $("v-list").setAttribute("aria-pressed", String(state.view === "list"));
    $("f-dl").setAttribute("aria-pressed", String(state.dlOnly));
    $("f-all").setAttribute("aria-pressed", String(!state.dlOnly));
    $("view").innerHTML = state.view === "cal" ? calendarHTML() : listHTML();
    $("detail").hidden = state.view !== "cal";  // 一覧では各予定の下に詳細を開くため、下部の欄は使わない
    if (state.view === "cal") fitText();
  }

  function shiftMonth(n) {
    let { y, m } = state.month;
    m += n;
    if (m < 1) { m = 12; y--; }
    if (m > 12) { m = 1; y++; }
    state.month = { y, m };
    render();
  }

  function setup() {
    $("prev").onclick = () => shiftMonth(-1);
    $("next").onclick = () => shiftMonth(1);
    $("today").onclick = () => { const t = jst(new Date()); state.month = { y: t.y, m: t.m }; render(); };
    $("v-cal").onclick = () => { state.view = "cal"; render(); };
    $("v-list").onclick = () => { state.view = "list"; render(); };
    $("f-dl").onclick = () => { state.dlOnly = true; store.set("dlOnly", true); render(); };
    $("f-all").onclick = () => { state.dlOnly = false; store.set("dlOnly", false); render(); };
    $("view").addEventListener("click", (e) => {
      if (e.target.closest(".inline-detail")) return;  // 詳細内のリンクはそのまま開く
      const el = e.target.closest("[data-id]");
      if (el) showDetail(el.getAttribute("data-id"), el);
    });
    // 画面幅でカレンダーと一覧を自動で切り替える（ボタンで手動切り替えも可能）
    let lastWidth = 0;
    const fit = () => {
      const w = $("root").clientWidth;
      const a = w < NARROW ? "list" : "cal";
      if (a !== state.auto) { state.auto = a; state.view = a; render(); }
      else if (Math.abs(w - lastWidth) > 4 && state.view === "cal") fitText();
      lastWidth = w;
    };
    fit();
    if ("ResizeObserver" in window) new ResizeObserver(fit).observe($("root"));
    else window.addEventListener("resize", fit);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { if (state.view === "cal") fitText(); });
  }

  async function main() {
    const t = jst(new Date());
    state.today = dkey(t.y, t.m, t.d);
    state.month = { y: t.y, m: t.m };
    try {
      const res = await fetch("data/events.json", { cache: "no-cache" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      state.data = await res.json();
    } catch (e) {
      $("month-label").textContent = "読み込みエラー";
      $("view").innerHTML = `<div class="empty">データを読み込めませんでした（${esc(e.message)}）。時間をおいて再読み込みしてください。</div>`;
      return;
    }
    for (const g of state.data.games) state.games[g.id] = g;
    renderChips();
    renderSources();
    const gen = (state.data.generated_at || "").replace("T", " ").slice(0, 16);
    const failed = (state.data.sources || []).filter((s) => !s.ok).length;
    $("foot").innerHTML = `最終更新 ${esc(gen)}（日本時間）・ 予定 ${state.data.items.length} 件` +
      (failed ? ` ・ <span class="stale-note"><i class="ti ti-alert-triangle" aria-hidden="true"></i> 取得に失敗した情報源が ${failed} 件あります（詳細は上の取得状況）</span>` : "");
    setup();
  }

  main();
})();
