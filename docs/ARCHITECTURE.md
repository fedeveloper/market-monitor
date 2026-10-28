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
-- Aggiunte in M2:
CREATE TABLE sent_alerts (ts INTEGER, symbol TEXT, rule TEXT, detail TEXT);  -- alert inviati (digest: livelli raggiunti)
CREATE TABLE sent_messages (ts INTEGER);                                     -- messaggi di alert inviati (limite orario)
```

Note di implementazione (M2):
- `quotes` ha in più la colonna `currency` ed è `WITHOUT ROWID`. La chiave `(symbol, ts)` usa l'orario del dato, non quello del ciclo: un dato vecchio letto di nuovo (mercato chiuso, festivo) non viene duplicato.
- `alert_state.rule` contiene la chiave dello stato, non solo il nome della regola: `fast_move`, `day_move:up:3`, `day_move:down:5`, `open_gap`, `level_above:70000`, `level_below:50000`. Ogni gradino e ogni livello ha il suo stato e il suo cooldown.
- Un solo commit per ciclo (`Storage.commit()` alla fine del ciclo); se il ciclo fallisce a metà, niente viene scritto.
- `journal_mode=WAL` con `synchronous=NORMAL`: per un piccolo commit ogni 15 minuti il WAL scrive in append e fa meno `fsync` del journal classico, che a ogni commit crea e cancella un file. Restano i file `market.db-wal` e `market.db-shm` accanto al database: è normale.
- Retention: una volta al giorno (`kv.last_prune`) si cancellano i campioni e lo storico degli alert più vecchi di `storage.retention_days`.

Stima volume: 20 asset x 96 campioni/giorno x 30 giorni circa 58.000 righe, pochi MB.

## Orari di mercato

- Orari base in `config.yaml` (IT 09:00-17:30 Europe/Rome, US 09:30-16:00 America/New_York, giorni feriali, CRYPTO sempre aperto).
- Le festività non sono in un calendario: se l'ultimo dato del provider è più vecchio di `health.stale_data_minutes` (default 45 in `alerts.yaml`, per tollerare il ritardo di circa 15 minuti dei dati gratuiti), il mercato è trattato come chiuso e quell'asset non genera alert nel ciclo.
- Le finestre di confronto (`fast_move`) non attraversano mai la notte o il weekend per azioni ed ETF: si usano solo campioni della stessa sessione.
- Fuori orario i titoli non vengono interrogati. Dopo la chiusura restano altri 20 minuti di letture (`CLOSE_GRACE_MINUTES` in `markets.py`), per avere il prezzo finale nonostante il ritardo di circa 15 minuti.

## Provider: formato delle risposte (verifica M1)

Esito della verifica fatta in M1 (28/10/2026) dall'ambiente di sviluppo cloud:

- **Binance** risponde HTTP 451 (blocco geografico) da `api.binance.com` per gli IP del cloud; dal Raspberry in Italia è atteso funzionante. Il mirror pubblico ufficiale `https://data-api.binance.vision` (solo dati di mercato, stesso formato) ha risposto e le sue risposte reali sono in `tests/fixtures/`. Se `api.binance.com` dovesse dare 451 anche dal Pi, basta cambiare `providers.binance.base_url`.
- **Yahoo** risponde quasi sempre HTTP 429 agli IP del cloud. Una richiesta reale (ciclo `run --once --dry-run` in M8) è andata a buon fine ed è stata letta senza errori dal parser, ma la fixture resta sintetica. **Da verificare sul Pi** con `check-symbols` (vedi `docs/DEPLOY.md`).

Yahoo `chart` (non ufficiale):
- `GET https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=15m&range=1d`
- Campi usati: `chart.result[0].meta.regularMarketPrice` e `regularMarketTime` (prezzo e orario del dato), `previousClose` o in mancanza `chartPreviousClose` (riferimento del giorno), `regularMarketVolume`, `currency`; il primo `indicators.quote[0].open` non `null` è l'apertura del giorno. Se manca `regularMarketPrice` si usa l'ultima candela con `close` non `null`.
- Simbolo sconosciuto: HTTP 404 con `chart.error.description` (atteso "No data found, symbol may be delisted").
- Da misurare sul Pi: ritardo effettivo dei dati per Borsa Italiana e USA (`check-symbols` mostra "dato delle HH:MM (N min fa)"), comportamento con 429, validità dei simboli di esempio.

Binance (pubblica, senza chiave), verificato:
- `GET {base_url}/api/v3/ticker/24hr?symbol=BTCEUR`. Campi usati: `lastPrice` (prezzo), `openPrice` (prezzo di 24 ore fa: riferimento di `day_move` con `rolling_24h`), `volume`, `closeTime` in millisecondi (orario del dato). Valori numerici come stringhe.
- Simbolo sconosciuto: HTTP 400 con `{"code": -1121, "msg": "Invalid symbol."}`.
- Le candele (`/api/v3/klines`) non servono: lo storico per `fast_move` è costruito dai campioni salvati a ogni ciclo.

Gestione comune (`HttpClient`): timeout, retry con backoff esponenziale (1 s, 2 s, ...) su errori di rete, HTTP 5xx e 429 (rispettando `Retry-After`, al massimo 30 s), nessun retry sugli altri 4xx, `User-Agent` da config, pausa minima tra due richieste (`request_delay_ms`).

CoinGecko (alternativa, non implementata):
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
