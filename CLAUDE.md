# CLAUDE.md — Market Monitor

Contesto per Claude Code. Leggi questo file a inizio sessione, poi `docs/ROADMAP.md` per sapere a che punto siamo.

## Scopo

Servizio leggero che gira su un Raspberry Pi 3B. Ogni 15 minuti (configurabile) legge i prezzi di azioni, ETF e crypto da fonti gratuite, applica regole di alert e invia le notifiche su Telegram.

Stato iniziale del repository: solo documentazione e config di esempio. Il codice va scritto seguendo le milestone in `docs/ROADMAP.md`.

## Vincoli (non negoziabili)

- Hardware: Raspberry Pi 3B (1 GB RAM, SD card), Raspbian. Obiettivo: meno di 60 MB di RSS e CPU quasi a zero tra un ciclo e l'altro.
- Python 3.11 (versione installata sul Pi, vedi sezione sotto). Niente Docker.
- Solo dati gratuiti e senza carta di credito. Nessuna chiave API a pagamento.
- Dipendenze runtime ammesse: `requests`, `PyYAML`, più la standard library (`sqlite3`, `logging`, `dataclasses`, `argparse`). Niente pandas/numpy/yfinance. Non aggiungere dipendenze senza chiedere.
- Poche scritture su disco (SD card): un commit SQLite per ciclo, log con rotazione, retention dei dati configurabile.
- Il servizio non dà consigli di investimento. Le notifiche riportano dati e soglie superate, mai raccomandazioni di acquisto o vendita.

## Python 3.11

Sul Pi gira Python 3.11.2. Sviluppa e testa in un venv 3.11 (pyenv o simili) per avere lo stesso comportamento del Pi.

- Sintassi e libreria standard fino a 3.11 sono ammesse: `list[str]`, `str | None`, `match`, `zoneinfo`, `tomllib`, `functools.cache`, `datetime.UTC`.
- Vietate le novità di 3.12 o successive, che sul Pi non girerebbero: `type X = ...`, generics `def f[T]()`, `itertools.batched`, `typing.override`, f-string con le stesse virgolette annidate.
- Fusi orari con `zoneinfo` della standard library, non `pytz`. Serve il pacchetto di sistema `tzdata` (normalmente già presente su Debian). Se manca sulla macchina di sviluppo, chiedi prima di aggiungere `tzdata` come dipendenza di sviluppo.

## Telegram

- Bot Telegram: token e chat id stanno in `.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). In chat privata il chat id coincide con lo user id Telegram dell'utente.
- Invio solo con la Bot API: `POST https://api.telegram.org/bot<TOKEN>/sendMessage` via `requests`.
- Mai `getUpdates` né webhook: il servizio invia soltanto. Il bot può essere usato anche da un altro programma che ne legge gli aggiornamenti, e un secondo consumer genererebbe errori 409.
- `parse_mode=HTML` con escape dei caratteri speciali. Limite 4096 caratteri per messaggio: spezza i messaggi lunghi.
- Non loggare mai il token (nemmeno negli URL di errore: maschera `bot<TOKEN>`).

## Convenzioni

- Codice, nomi e commenti in inglese. Messaggi Telegram, README e docs in italiano.
- Layout: `src/market_monitor/` (pacchetto), `tests/`, `config/`, `deploy/`, `docs/`. Esecuzione con `PYTHONPATH=src python -m market_monitor ...` oppure aggiungi un `pyproject.toml` minimo per `pip install -e .`.
- Logica degli alert in funzioni pure (input: storico, quote, regole, stato; output: lista di alert), senza I/O. Provider, storage e notifier dietro interfacce semplici.
- Un provider che fallisce non blocca gli altri: il ciclo termina sempre, anche con fallimenti parziali. Errori loggati con contesto, mai silenziati.
- Niente `print`: usa `logging`.
- Formato numeri nei messaggi: locale italiano scritto a mano (virgola decimale, punto per le migliaia), senza dipendere dal locale di sistema.
- Configurazione in YAML, ricaricata a caldo a ogni ciclo se il file è cambiato (watchlist modificabile senza riavviare).

## Comandi da implementare

```
python -m market_monitor run                 # loop continuo (servizio)
python -m market_monitor run --once          # un solo ciclo, poi esce
python -m market_monitor run --once --dry-run  # stampa i messaggi invece di inviarli
python -m market_monitor check-config        # valida config, watchlist e alert
python -m market_monitor check-symbols       # prova ogni simbolo con il suo provider
python -m market_monitor test-telegram       # invia un messaggio di prova
python -m market_monitor digest --now        # genera e invia subito il riepilogo
python -m market_monitor status --print      # M9: stampa il file di stato
python -m market_monitor web                 # M10: interfaccia web di configurazione
```

## Interfaccia web (M10)

- Processo separato (`python -m market_monitor web`, suo servizio systemd): se l'interfaccia ha un problema il monitor continua a girare. Le due parti comunicano solo tramite i file YAML (ricarica a caldo), il database SQLite in sola lettura e il file di stato.
- Solo standard library (`http.server`) e una pagina HTML con JavaScript senza build: nessuna nuova dipendenza.
- Accesso dalla rete di casa con password (`WEB_PASSWORD` in `.env`), protezione CSRF con un header obbligatorio sulle richieste che modificano. Token e chat id non si vedono e non si modificano dall'interfaccia.
- Ogni salvataggio valida i file con lo stesso codice di `check-config`, fa una copia `.bak` e scrive in modo atomico. Accettato con l'utente: i file salvati dall'interfaccia perdono i commenti (i riferimenti restano nei `*.example.yaml`).

## Test

- `pytest`, sempre offline: nessuna chiamata di rete nei test. Le risposte dei provider sono fixture JSON in `tests/fixtures/`.
- Copri bene il motore degli alert (soglie, gradini, cooldown, isteresi, quiet hours, avvio a freddo senza storico).
- Prima di ogni commit: `pytest` verde.

## Come lavorare

- Procedi per milestone (`docs/ROADMAP.md`), una alla volta, con commit piccoli. A fine milestone aggiorna lo stato nella roadmap.
- La v1 è M0-M8. Non iniziare evolutive (M9 in poi) finché la v1 non gira stabile sul Pi e l'utente non lo conferma.
- Le decisioni in `docs/ARCHITECTURE.md` sono state prese con l'utente: se ritieni vada cambiata una, proponilo e chiedi prima di procedere.
- A ogni PR (nella descrizione e nel riepilogo all'utente) includi due sezioni in italiano: come installare o aggiornare l'applicazione sul Raspberry (comandi pronti da copiare, eventuali nuovi file di config o chiavi da aggiungere, riavvio del servizio) e cosa testare sul Pi in quel rilascio (comandi da lanciare e risultato atteso).
- Le parti marcate "da verificare" nei docs (formato delle risposte dei provider, orari, latenza dei dati) vanno controllate con una chiamata reale e poi aggiornate nei docs.

## Sicurezza

- `.env`, `config/config.yaml`, `config/watchlist.yaml`, `config/alerts.yaml`, `data/` e `logs/` non vanno mai committati (già in `.gitignore`). Nel repo ci sono solo i file `*.example.*`.
