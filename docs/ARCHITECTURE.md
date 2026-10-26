# Architettura

## Decisioni prese

| Tema | Decisione | Motivo |
|---|---|---|
| Esecuzione | Un solo processo sempre attivo con loop interno, gestito da systemd (`Restart=always`) | L'intervallo sta nel YAML (unica fonte di verità); con un timer systemd andrebbe modificato in due posti |
| Allineamento | Il ciclo parte agli orari "tondi" (:00, :15, :30, :45 con intervallo 15) | Campioni regolari, finestre di confronto affidabili |
| Dati azioni/ETF | Endpoint `chart` di Yahoo Finance chiamato con `requests` | `yfinance` porta pandas/numpy, pesanti su Pi 3B |
| Dati crypto | Binance API pubblica (coppia in EUR, es. `BTCEUR`); CoinGecko come alternativa | Gratuita, senza chiave, campioni a 15 minuti |
| Storage | SQLite (`sqlite3` stdlib) | Zero manutenzione, poche scritture |
| Notifiche | Telegram Bot API con il token di picoclaw, solo `sendMessage` | Il canale esiste già; niente `getUpdates` per non confliggere con il gateway |
| Config | Tre YAML ricaricati a caldo + `.env` per i segreti | Watchlist e soglie modificabili senza riavvio |
| Fuso orario | `zoneinfo` (standard library) | Disponibile in Python 3.11, nessuna dipendenza extra (`pytz` non serve) |

## Flusso di un ciclo

```
scheduler (sleep fino al prossimo slot)
  └─> ricarica config se i file sono cambiati
  └─> per ogni asset della watchlist:
        ├─ mercato aperto? (orari + freschezza dell'ultimo dato; crypto sempre aperto)
        └─ provider.fetch(asset) -> Quote
  └─> storage.save(quotes)
  └─> alert_engine.evaluate(...) -> [Alert]        (funzioni pure)
  └─> delivery.filter(cooldown, quiet hours, rate limit) -> [Alert]
  └─> notifier.send(messaggio unico per ciclo)
  └─> se è l'ora del riepilogo -> digest
  └─> health check provider (errori consecutivi / recupero)
  └─> una volta al giorno: prune dei dati oltre la retention
```

## Componenti

- `config`: caricamento e validazione dei YAML, merge dei default per classe con gli override per asset, lettura del `.env`.
- `providers`: interfaccia `QuoteProvider.fetch(asset) -> Quote`. Implementazioni: `YahooChartProvider`, `BinanceProvider`, opzionale `CoinGeckoProvider`. Gestione di timeout, retry con backoff, HTTP 429, pausa breve tra richieste (circa 300 ms), `User-Agent` esplicito.
- `markets`: stato aperto/chiuso per mercato (IT, US, CRYPTO) da orari in config.
- `storage`: schema SQLite, salvataggio campioni, query di finestra, retention.
- `alerts`: motore di regole (vedi `ALERTS.md`), puro e testabile.
- `delivery`: cooldown, quiet hours, limite messaggi/ora, batch per ciclo.
- `notifier`: formattazione in italiano e invio Telegram, split oltre 4096 caratteri.
- `digest`: riepilogo giornaliero.
- `health`: contatori di fallimenti per provider.
- `cli`: sottocomandi (vedi `CLAUDE.md`).

## Modello dati

`Quote`: `symbol`, `ts` (epoch UTC del campione), `price`, `prev_close` (opzionale), `day_open` (opzionale), `volume` (opzionale), `currency`.

```sql
CREATE TABLE quotes (
  symbol TEXT NOT NULL,
  ts INTEGER NOT NULL,
  price REAL NOT NULL,
  prev_close REAL,
  day_open REAL,
  volume REAL,
  PRIMARY KEY (symbol, ts)
);
CREATE TABLE alert_state (
  symbol TEXT NOT NULL,
  rule TEXT NOT NULL,            -- fast_move, day_move, open_gap, level_above, level_below
  last_sent_ts INTEGER,
  last_level REAL,               -- gradino raggiunto
  session_key TEXT,              -- sessione/giorno per il reset
  armed INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (symbol, rule)
);
CREATE TABLE provider_health (
  provider TEXT PRIMARY KEY,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  alerted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT);   -- es. ultimo digest inviato
```

Stima volume: 20 asset x 96 campioni/giorno x 30 giorni circa 58.000 righe, pochi MB.

## Orari di mercato

- Orari base in `config.yaml` (IT 09:00-17:30 Europe/Rome, US 09:30-16:00 America/New_York, giorni feriali, CRYPTO sempre aperto).
- Le festività non sono in un calendario: se l'ultimo dato del provider è più vecchio di `health.stale_data_minutes` (default 45 in `alerts.yaml`, per tollerare il ritardo di circa 15 minuti dei dati gratuiti), il mercato è trattato come chiuso e quell'asset non genera alert nel ciclo.
- Le finestre di confronto (`fast_move`) non attraversano mai la notte o il weekend per azioni ed ETF: si usano solo campioni della stessa sessione.

## Provider: dettagli da verificare con una chiamata reale (milestone M1)

Yahoo `chart` (non ufficiale):
- `GET https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=15m&range=1d`
- Atteso: `chart.result[0].meta` con `regularMarketPrice`, `chartPreviousClose`/`previousClose`, `regularMarketTime`, `currency`, `exchangeTimezoneName`; `timestamp[]` e `indicators.quote[0].{open,close,volume}` con possibili valori `null`.
- Da misurare: ritardo effettivo dei dati per Borsa Italiana e USA, comportamento con troppe richieste (429), necessità di `User-Agent`.

Binance (pubblica, senza chiave):
- `GET https://api.binance.com/api/v3/ticker/24hr?symbol=BTCEUR` (prezzo, variazione 24h)
- `GET https://api.binance.com/api/v3/klines?symbol=BTCEUR&interval=15m&limit=100` (candele)

CoinGecko (alternativa, limiti più stretti):
- `GET https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=eur&include_24hr_change=true`

Fallback possibili per azioni/ETF se Yahoo smette di funzionare: Stooq (CSV, spesso solo giornaliero), Twelve Data (piano gratuito con limiti giornalieri). Da valutare solo se serve.

## Gestione errori

- Timeout e retry con backoff esponenziale per richiesta; un asset che fallisce viene saltato nel ciclo, non blocca gli altri.
- Dopo `error_alert_after_failures` cicli consecutivi falliti di un provider: un solo messaggio di errore; un solo messaggio di recupero quando torna a funzionare.
- Se Telegram non risponde: retry breve, poi il messaggio viene scartato con log (niente code persistenti in v1).
- Il processo non deve mai uscire per un'eccezione nel ciclo: log dello stack e si passa al ciclo successivo.
- Se il servizio è fermo, non può avvisare: il segnale è l'assenza del riepilogo giornaliero.

## Evolutiva 1: integrazione con picoclaw (M9, dopo la v1)

In v1 picoclaw è solo il proprietario del bot Telegram condiviso: il servizio non chiama la sua CLI e non legge i suoi file. La prima evolutiva, da iniziare con la v1 stabile e dopo conferma dell'utente (dettagli in `ROADMAP.md`, M9), è questa:

```
market-monitor --(scrive a fine ciclo)--> status.json --(legge)--> agente picoclaw
```

- Direzione unica: il servizio scrive, picoclaw legge. Il servizio non dipende da picoclaw.
- Scrittura atomica, `schema_version`, nessun segreto, percorso configurabile (anche tmpfs).
- Da verificare: come l'agente di picoclaw accede ai file (sandbox, workspace predefinito `~/.picoclaw/workspace`).

Struttura indicativa del file:

```json
{
  "schema_version": 1,
  "generated_at": "2026-10-26T14:30:02+01:00",
  "interval_minutes": 15,
  "assets": [
    {"symbol": "ENI.MI", "name": "Eni", "class": "stock", "price": 14.52, "currency": "EUR",
     "quote_time": "2026-10-26T14:15:00+01:00", "change_day_pct": 3.1, "market_open": true}
  ],
  "recent_alerts": [
    {"ts": "2026-10-26T14:30:00+01:00", "symbol": "ENI.MI", "rule": "day_move",
     "detail": "+3,1% oggi (gradino 3%)"}
  ],
  "providers": {
    "yahoo": {"ok": true, "consecutive_failures": 0},
    "binance": {"ok": true, "consecutive_failures": 0}
  },
  "last_digest": "2026-10-25T18:00:00+01:00"
}
```

## Evoluzioni possibili (dopo M9)

Volumi anomali, massimi/minimi a 52 settimane, correlazione con il portafoglio, fallback automatico tra provider, comandi di stato da Telegram tramite picoclaw (mai con `getUpdates` dal servizio).
