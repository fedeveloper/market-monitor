"use strict";

// Market Monitor web interface: plain JavaScript, no build step.
// The server validates every save with the same rules as check-config;
// this page only converts form values to the YAML structure.

const LABELS = {
  interval_minutes: "Intervallo tra i controlli (minuti)", align_to_clock: "Allinea agli orari tondi",
  timezone: "Fuso orario", markets: "Mercati", providers: "Provider", telegram: "Telegram",
  storage: "Database", logging: "Log", status_file: "File di stato (JSON)", web: "Interfaccia web",
  open: "Apertura (HH:MM)", close: "Chiusura (HH:MM)", days: "Giorni", always_open: "Sempre aperto",
  base_url: "URL", timeout_seconds: "Timeout (secondi)", retries: "Tentativi in più",
  request_delay_ms: "Pausa tra richieste (ms)", user_agent: "User-Agent", parse_mode: "Formato",
  max_message_chars: "Caratteri massimi per messaggio", path: "Percorso", retention_days: "Giorni di storico",
  level: "Livello", file: "File", max_bytes: "Dimensione massima (byte)", backup_count: "File di rotazione",
  enabled: "Attivo", recent_alerts: "Alert recenti nel file", host: "Host", port: "Porta",
  defaults: "Soglie per classe", stock: "Azioni", etf: "ETF", crypto: "Crypto",
  fast_move: "Movimento rapido", day_move: "Variazione del giorno", open_gap: "Gap di apertura",
  window_minutes: "Finestra (minuti)", threshold_pct: "Soglia %", step_pct: "Passo per un nuovo alert %",
  steps_pct: "Gradini %", reference: "Riferimento", rearm_pct: "Riarmo (punti %)",
  max_delay_minutes: "Ritardo massimo del primo dato (min)", levels: "Livelli di prezzo",
  hysteresis_pct: "Isteresi %", delivery: "Antispam e consegna", cooldown_minutes: "Cooldown (minuti)",
  max_alerts_per_message: "Alert massimi per messaggio", max_messages_per_hour: "Messaggi massimi all'ora",
  quiet_hours: "Quiet hours", start: "Inizio (HH:MM)", end: "Fine (HH:MM)",
  urgent_bypass: "Gli urgenti passano comunque", urgent_pct: "Soglia urgente %",
  digest: "Riepilogo giornaliero", time: "Ora (HH:MM)", weekend_crypto_only: "Nel weekend solo crypto",
  top_movers: "Maggiori rialzi e ribassi", health: "Salute del servizio",
  error_alert_after_failures: "Cicli falliti prima dell'avviso", stale_data_minutes: "Dato vecchio dopo (minuti)",
};

const OVERRIDE_FIELDS = {
  fast_move: ["window_minutes", "threshold_pct", "step_pct"],
  day_move: ["steps_pct", "rearm_pct", "reference"],
  open_gap: ["threshold_pct", "max_delay_minutes"],
};
const RULE_TITLES = { fast_move: "Movimento rapido", day_move: "Variazione del giorno", open_gap: "Gap di apertura" };

let state = null;          // last /api/state response
const drafts = {};          // editable copies of the YAML data, by file name
const dirty = {};           // unsaved changes, by file name
const notices = {};         // message to show once after a save, by file name

function takeNotice(name) {
  const notice = notices[name];
  delete notices[name];
  return notice ? messagesBox(notice.kind, notice.title, notice.items) : null;
}

// --- small helpers -----------------------------------------------------------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "checked" || key === "disabled" || key === "selected") node[key] = Boolean(value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function clone(value) { return value === undefined ? undefined : JSON.parse(JSON.stringify(value)); }
function label(key) { return LABELS[key] || key; }

let toastTimer = null;
function toast(message) {
  const box = document.getElementById("toast");
  box.textContent = message;
  box.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.remove("show"), 4000);
}

async function api(method, path, body) {
  const options = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.headers["X-MM-Request"] = "1";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let data = {};
  try { data = await response.json(); } catch (e) { data = { ok: false, error: `HTTP ${response.status}` }; }
  return { status: response.status, data };
}

function messagesBox(kind, title, items) {
  if (!items || !items.length) return null;
  return el("div", { class: `messages ${kind}` }, el("strong", {}, title), el("ul", {}, items.map((i) => el("li", {}, i))));
}

const numberFormat = (digits) => new Intl.NumberFormat("it-IT", { minimumFractionDigits: digits, maximumFractionDigits: digits });
function formatPrice(value, currency) {
  if (value === null || value === undefined) return "—";
  const digits = Math.abs(value) >= 1000 ? 0 : Math.abs(value) >= 1 ? 2 : 4;
  const symbols = { EUR: "€", USD: "$", GBP: "£" };
  return `${numberFormat(digits).format(value)}${currency ? " " + (symbols[currency] || currency) : ""}`;
}
function formatPct(value) {
  if (value === null || value === undefined) return "—";
  const text = numberFormat(1).format(Math.abs(value));
  return (value > 0 ? "+" : value < 0 ? "−" : "") + text + "%";
}
function formatTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("it-IT", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

// Form values -> YAML values. Lists use ";" because "," is the decimal separator.
function parseNumber(text) {
  const trimmed = String(text).trim();
  if (trimmed === "") return null;
  const number = Number(trimmed.replace(",", "."));
  return Number.isFinite(number) ? number : trimmed;  // keep the text: the server reports the error
}
function showNumber(value) { return value === null || value === undefined ? "" : String(value).replace(".", ","); }
function parseList(text, numeric) {
  const parts = String(text).split(";").map((p) => p.trim()).filter((p) => p !== "");
  return numeric ? parts.map(parseNumber) : parts;
}
function showList(list) { return (list || []).map((v) => (typeof v === "number" ? showNumber(v) : v)).join("; "); }

// --- generic editor (config.yaml, alerts.yaml) ---------------------------------

function fieldFor(container, key, value, onChange) {
  const id = `f-${Math.random().toString(36).slice(2)}`;
  let input;
  if (typeof value === "boolean") {
    input = el("input", { type: "checkbox", id, checked: value, onchange: (e) => onChange(e.target.checked) });
  } else if (typeof value === "number") {
    input = el("input", { type: "text", id, inputmode: "decimal", value: showNumber(value), onchange: (e) => onChange(parseNumber(e.target.value)) });
  } else if (Array.isArray(value) && value.every((v) => typeof v !== "object")) {
    const numeric = value.length > 0 && value.every((v) => typeof v === "number");
    input = el("input", { type: "text", id, value: showList(value), placeholder: "valori separati da ;", onchange: (e) => onChange(parseList(e.target.value, numeric)) });
  } else if (value === null || typeof value === "string") {
    input = el("input", { type: "text", id, value: value ?? "", placeholder: value === null ? "vuoto" : "", onchange: (e) => onChange(e.target.value === "" ? null : e.target.value) });
  } else {
    input = el("textarea", { id, onchange: (e) => { try { onChange(JSON.parse(e.target.value)); } catch (err) { toast("JSON non valido"); } } }, JSON.stringify(value, null, 1));
  }
  container.append(el("div", { class: "field" }, el("label", { for: id }, label(key), el("small", {}, key)), input));
}

function renderObject(container, obj, onChanged) {
  for (const [key, value] of Object.entries(obj)) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const box = el("fieldset", {}, el("legend", {}, label(key), el("span", { class: "key" }, key)));
      renderObject(box, value, onChanged);
      container.append(box);
    } else {
      fieldFor(container, key, value, (newValue) => { obj[key] = newValue; onChanged(); });
    }
  }
}

function renderGenericTab(name, title, hint) {
  const section = document.getElementById(`tab-${name}`);
  section.replaceChildren();
  const file = state.files[name];
  if (file.error) {
    section.append(messagesBox("bad", `${title}: file non leggibile`, [file.error]));
    return;
  }
  const result = el("div", {}, takeNotice(name));
  const save = el("button", { class: "btn primary", onclick: () => saveFile(name, result) }, "Salva");
  section.append(el("p", { class: "hint" }, hint), el("div", { class: "toolbar" }, save, el("button", { class: "btn", onclick: () => reload() }, "Annulla modifiche")), result);
  const form = el("div", { class: "panel" });
  renderObject(form, drafts[name], () => { dirty[name] = true; });
  section.append(form);
}

// --- save --------------------------------------------------------------------

async function saveFile(name, resultBox) {
  const { status, data } = await api("PUT", `/api/files/${name}`, { data: drafts[name], version: state.files[name].version });
  resultBox.replaceChildren();
  if (status === 200) {
    dirty[name] = false;
    toast(`${name}.yaml salvato: vale dal prossimo ciclo`);
    if (data.warnings && data.warnings.length) notices[name] = { kind: "warn", title: "Salvato, con questi avvisi:", items: data.warnings };
    await reload();
    return;
  }
  if (data.conflict) {
    resultBox.append(messagesBox("bad", data.error, ["Qualcuno ha modificato il file a mano: ricarica la pagina (le modifiche qui andranno perse)."]));
    return;
  }
  resultBox.append(messagesBox("bad", data.error || `Errore HTTP ${status}`, data.errors || []));
}

// --- watchlist ---------------------------------------------------------------

function optionList(values, current) {
  const options = values.map((v) => el("option", { value: v, selected: v === current }, v));
  if (current && !values.includes(current)) options.push(el("option", { value: current, selected: true }, `${current} (?)`));
  return options;
}

function classDefault(assetClass, rule, field) {
  const defaults = drafts.alerts && drafts.alerts.defaults && drafts.alerts.defaults[assetClass];
  const value = defaults && defaults[rule] ? defaults[rule][field] : undefined;
  if (value === undefined) return "";
  return Array.isArray(value) ? showList(value) : typeof value === "number" ? showNumber(value) : String(value);
}

function overrideEditor(asset, onChanged) {
  const grid = el("div", { class: "override-grid" });
  for (const [rule, fields] of Object.entries(OVERRIDE_FIELDS)) {
    const box = el("fieldset", {}, el("legend", {}, RULE_TITLES[rule]));
    const get = () => (asset.overrides && asset.overrides[rule]) || {};
    const set = (field, value) => {
      asset.overrides = asset.overrides || {};
      const ruleData = { ...get() };
      if (value === null || value === "" || (Array.isArray(value) && !value.length)) delete ruleData[field];
      else ruleData[field] = value;
      if (Object.keys(ruleData).length) asset.overrides[rule] = ruleData; else delete asset.overrides[rule];
      if (!Object.keys(asset.overrides).length) delete asset.overrides;
      onChanged();
    };
    const enabled = get().enabled;
    box.append(el("div", { class: "field" }, el("label", {}, "Attivo"),
      el("select", { onchange: (e) => set("enabled", e.target.value === "" ? null : e.target.value === "true") },
        el("option", { value: "", selected: enabled === undefined }, "come la classe"),
        el("option", { value: "true", selected: enabled === true }, "sì"),
        el("option", { value: "false", selected: enabled === false }, "no"))));
    for (const field of fields) {
      const current = get()[field];
      let input;
      if (field === "reference") {
        input = el("select", { onchange: (e) => set(field, e.target.value || null) },
          el("option", { value: "", selected: current === undefined }, "come la classe"),
          ...["prev_close", "rolling_24h"].map((v) => el("option", { value: v, selected: current === v }, v)));
      } else if (field === "steps_pct") {
        input = el("input", { type: "text", value: current ? showList(current) : "", placeholder: classDefault(asset.class, rule, field), onchange: (e) => set(field, parseList(e.target.value, true)) });
      } else {
        input = el("input", { type: "text", inputmode: "decimal", value: showNumber(current), placeholder: classDefault(asset.class, rule, field), onchange: (e) => set(field, parseNumber(e.target.value)) });
      }
      box.append(el("div", { class: "field" }, el("label", {}, label(field)), input));
    }
    grid.append(box);
  }
  return el("div", {}, el("p", { class: "hint" }, "Vuoto = valore della classe (mostrato in grigio)."), grid);
}

function renderWatchlist() {
  const section = document.getElementById("tab-watchlist");
  section.replaceChildren();
  const file = state.files.watchlist;
  if (file.error) { section.append(messagesBox("bad", "watchlist.yaml non leggibile", [file.error])); return; }
  const data = drafts.watchlist;
  data.assets = data.assets || [];
  const config = drafts.config || {};
  const markets = Object.keys(config.markets || {});
  const providers = Object.keys(config.providers || {});
  const changed = () => { dirty.watchlist = true; };
  const result = el("div", {}, takeNotice("watchlist"));
  const tbody = el("tbody");

  const redraw = () => { renderWatchlist(); dirty.watchlist = true; };

  data.assets.forEach((asset, index) => {
    const levels = asset.levels || {};
    const setLevels = (side, text) => {
      const values = parseList(text, true);
      asset.levels = { ...(asset.levels || {}) };
      if (values.length) asset.levels[side] = values; else delete asset.levels[side];
      if (!Object.keys(asset.levels).length) delete asset.levels;
      changed();
    };
    const checkResult = el("div", { class: "hint" });
    const overrideCount = asset.overrides ? Object.keys(asset.overrides).length : 0;
    const overrideRow = el("tr", { class: "override-row", hidden: true }, el("td", { colspan: "10" }, overrideEditor(asset, changed)));
    const row = el("tr", {},
      el("td", {}, el("input", { type: "checkbox", title: "Attivo", checked: asset.enabled !== false,
        onchange: (e) => { if (e.target.checked) delete asset.enabled; else asset.enabled = false; changed(); } })),
      el("td", {}, el("input", { type: "text", value: asset.symbol || "", size: 9, onchange: (e) => { asset.symbol = e.target.value.trim(); changed(); } })),
      el("td", {}, el("input", { type: "text", value: asset.name || "", onchange: (e) => { asset.name = e.target.value; changed(); } })),
      el("td", {}, el("select", { onchange: (e) => { asset.class = e.target.value; redraw(); } }, optionList(state.meta.classes, asset.class))),
      el("td", {}, el("select", { onchange: (e) => { asset.market = e.target.value; changed(); } }, optionList(markets, asset.market))),
      el("td", {}, el("select", { onchange: (e) => { asset.provider = e.target.value; changed(); } }, optionList(providers, asset.provider))),
      el("td", {}, el("input", { type: "text", value: showList(levels.above), placeholder: "es. 16; 17,5", onchange: (e) => setLevels("above", e.target.value) })),
      el("td", {}, el("input", { type: "text", value: showList(levels.below), placeholder: "es. 13,5", onchange: (e) => setLevels("below", e.target.value) })),
      el("td", {}, el("button", { class: "btn small", onclick: () => { overrideRow.hidden = !overrideRow.hidden; } }, overrideCount ? `Override (${overrideCount})` : "Override")),
      el("td", {},
        el("button", { class: "btn small", onclick: async (e) => {
          e.target.disabled = true;
          checkResult.textContent = "verifica in corso...";
          const { data: res } = await api("POST", "/api/check-symbol", { symbol: asset.symbol, provider: asset.provider, class: asset.class, market: asset.market });
          e.target.disabled = false;
          checkResult.className = res.ok ? "hint ok" : "hint bad";
          checkResult.textContent = res.ok
            ? `OK ${res.price_text} ${res.change_text || ""} · dato delle ${res.quote_time} (${res.delay_minutes} min fa)`
            : `Errore: ${res.error}`;
        } }, "Verifica"),
        " ",
        el("button", { class: "btn small danger", onclick: () => {
          if (confirm(`Rimuovere ${asset.symbol || "questo asset"} dalla watchlist?`)) { data.assets.splice(index, 1); redraw(); }
        } }, "Rimuovi"),
        checkResult));
    tbody.append(row, overrideRow);
  });

  const add = el("button", { class: "btn", onclick: () => {
    data.assets.push({ symbol: "", name: "", class: "stock", market: markets[0] || "", provider: providers[0] || "" });
    redraw();
  } }, "Aggiungi asset");
  const save = el("button", { class: "btn primary", onclick: () => saveFile("watchlist", result) }, "Salva watchlist");
  section.append(
    el("p", { class: "hint" }, "Simboli: Yahoo con suffisso .MI per Borsa Italiana (es. ENI.MI), Binance come coppia (es. BTCEUR). Usa \"Verifica\" prima di salvare. Livelli: valori separati da ;"),
    el("div", { class: "toolbar" }, save, add, el("button", { class: "btn", onclick: () => reload() }, "Annulla modifiche")),
    result,
    el("div", { class: "panel table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Attivo", "Simbolo", "Nome", "Classe", "Mercato", "Provider", "Livelli sopra", "Livelli sotto", "Soglie", ""].map((h) => el("th", {}, h)))),
      tbody)));
}

// --- status ------------------------------------------------------------------

async function renderStatus() {
  const section = document.getElementById("tab-stato");
  const secrets = state.secrets;
  const actionResult = el("div", { class: "hint" });
  const action = (path, okText) => async (e) => {
    e.target.disabled = true;
    actionResult.className = "hint";
    actionResult.textContent = "invio in corso...";
    const { data } = await api("POST", path, {});
    e.target.disabled = false;
    actionResult.className = data.ok ? "hint ok" : "hint bad";
    actionResult.textContent = data.ok ? okText : `${data.error}${data.errors ? ": " + data.errors.join("; ") : ""}`;
  };
  section.replaceChildren(
    el("div", { class: "toolbar" },
      el("button", { class: "btn", onclick: () => renderStatus() }, "Aggiorna"),
      el("button", { class: "btn", onclick: action("/api/test-telegram", "Messaggio di prova inviato.") }, "Messaggio di prova"),
      el("button", { class: "btn", onclick: action("/api/digest", "Riepilogo inviato.") }, "Invia riepilogo ora"),
      actionResult),
    el("p", { class: "hint" }, `Telegram: token ${secrets.telegram_token ? "presente" : "mancante"}, chat id ${secrets.telegram_chat_id ? "presente" : "mancante"} (si modificano solo in .env).`));

  const { status, data } = await api("GET", "/api/status");
  if (status !== 200) { section.append(messagesBox("bad", data.error || `Errore ${status}`, data.errors || [])); return; }
  if (!data.status) { section.append(el("p", {}, data.message)); return; }
  const s = data.status;
  const rows = s.assets.map((a) => {
    const change = a.change_day_pct;
    return el("tr", {},
      el("td", {}, a.name, el("span", { class: "key" }, a.symbol)),
      el("td", { class: "num" }, formatPrice(a.price, a.currency)),
      el("td", { class: `num ${change > 0 ? "up" : change < 0 ? "down" : ""}` }, formatPct(change), change === null || change === undefined ? "" : (a.change_reference === "rolling_24h" ? " 24h" : " oggi")),
      el("td", {}, formatTime(a.quote_time)),
      el("td", {}, a.market_open ? "aperto" : "chiuso"));
  });
  const alerts = s.recent_alerts.length
    ? el("ul", {}, s.recent_alerts.map((a) => el("li", {}, `${formatTime(a.ts)} · ${a.symbol} · ${a.detail || a.rule}`)))
    : el("p", { class: "hint" }, "Nessun alert inviato.");
  const providers = Object.entries(s.providers).map(([name, p]) =>
    el("li", { class: p.ok ? "ok" : "bad" }, `${name}: ${p.ok ? "ok" : `in errore da ${p.consecutive_failures} cicli`}`));
  section.append(
    el("div", { class: "panel table-wrap" }, el("h2", {}, "Ultimi prezzi"),
      el("table", {}, el("thead", {}, el("tr", {}, ["Asset", "Prezzo", "Variazione", "Orario del dato", "Mercato"].map((h) => el("th", {}, h)))), el("tbody", {}, rows)),
      el("p", { class: "hint" }, "Dati con ritardo fino a 15 minuti.")),
    el("div", { class: "panel" }, el("h2", {}, "Alert recenti"), alerts),
    el("div", { class: "panel" }, el("h2", {}, "Provider"), providers.length ? el("ul", {}, providers) : el("p", { class: "hint" }, "Nessun dato."),
      el("p", { class: "hint" }, `Ultimo riepilogo: ${s.last_digest ? formatTime(s.last_digest) : "mai"}.`)));
}

// --- page --------------------------------------------------------------------

function renderValidation() {
  const box = document.getElementById("validation");
  box.replaceChildren();
  const v = state.validation;
  const errors = messagesBox("bad", "La configurazione attuale non è valida: il monitor usa l'ultima versione valida finché non la correggi.", v.errors);
  const warnings = messagesBox("warn", "Avvisi sulla configurazione:", v.warnings);
  if (errors) box.append(errors);
  if (warnings) box.append(warnings);
}

async function reload() {
  const { status, data } = await api("GET", "/api/state");
  if (status !== 200) { toast(data.error || `Errore ${status}`); return; }
  state = data;
  for (const name of Object.keys(state.files)) {
    drafts[name] = clone(state.files[name].data) || {};
    dirty[name] = false;
  }
  renderValidation();
  renderWatchlist();
  renderGenericTab("alerts", "alerts.yaml", "Soglie di default per classe, livelli, antispam, riepilogo e salute del servizio. Le soglie di un singolo asset si cambiano nella watchlist (Override).");
  renderGenericTab("config", "config.yaml", "Intervallo, mercati e orari, provider, Telegram, database, log, file di stato e interfaccia web. Le modifiche a web.host e web.port valgono dopo il riavvio dell'interfaccia.");
  showTab(currentTab);
}

let currentTab = "stato";
function showTab(name) {
  currentTab = name;
  for (const button of document.querySelectorAll("#tabs button")) button.classList.toggle("active", button.dataset.tab === name);
  for (const section of document.querySelectorAll("main > section")) section.hidden = section.id !== `tab-${name}`;
  if (name === "stato" && state) renderStatus();
}

document.addEventListener("DOMContentLoaded", () => {
  for (const button of document.querySelectorAll("#tabs button")) button.addEventListener("click", () => showTab(button.dataset.tab));
  window.addEventListener("beforeunload", (e) => {
    if (Object.values(dirty).some(Boolean)) { e.preventDefault(); e.returnValue = ""; }
  });
  reload();
});
