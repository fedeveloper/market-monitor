# Roadmap

Stato: `[ ]` da fare · `[~]` in corso · `[x]` fatto. A fine milestone aggiorna questo file e fai un commit.

Regola generale: `pytest` verde (offline) prima di ogni commit; sviluppo e test in un venv Python 3.11.

La v1 (senza agente AI) comprende le milestone M0-M8. Le evolutive (M9 in poi) partono solo quando la v1 gira stabile sul Raspberry e l'utente lo conferma.

## M0 — Scheletro del progetto `[x]`

- `pyproject.toml` minimo, pacchetto `src/market_monitor/`, `__main__.py`, `requirements.txt` e `requirements-dev.txt` già presenti.
- Logging con rotazione file + stdout, mascheramento del token.
- Caricamento e validazione di `config.yaml`, `watchlist.yaml`, `alerts.yaml` con merge dei default per classe e override per asset; lettura di `.env`.
- Ricarica a caldo dei file modificati (controllo `mtime`).
- CLI: `check-config`.
- Test: config valida, config non valida con messaggi chiari, override per asset.
- Note: errori di validazione raccolti tutti insieme con file e percorso della chiave; chiavi sconosciute trattate come errore (refusi); le variabili d'ambiente prevalgono su `.env`; `.env` non viene ricaricato a caldo (serve un riavvio); se la config modificata non è valida resta attiva la precedente; percorsi relativi risolti rispetto alla radice del progetto.

## M1 — Provider di dati `[x]`

- Interfaccia `QuoteProvider` e dataclass `Quote`.
- `YahooChartProvider` (azioni ed ETF) e `BinanceProvider` (crypto), con timeout, retry, backoff, gestione 429, `User-Agent`, pausa tra richieste.
- CLI: `check-symbols`.
- Verifica con chiamate reali e aggiornamento di `docs/ARCHITECTURE.md`: formato reale delle risposte, ritardo effettivo dei dati su Borsa Italiana e USA, validità dei simboli di esempio della watchlist.
- Fixture JSON salvate in `tests/fixtures/` (da risposte reali, ripulite). Test di parsing, dati `null`, errori HTTP.
- Note: Binance verificato con risposte reali (mirror `data-api.binance.vision`); Yahoo rispondeva 429 dal cloud, fixture sintetica. **Da completare sul Pi**: formato reale Yahoo, ritardo dei dati, validità dei simboli (vedi `docs/DEPLOY.md`).

## M2 — Storage `[x]`

- Schema SQLite (vedi `docs/ARCHITECTURE.md`), un commit per ciclo, `WAL` valutato rispetto alle scritture su SD.
- Salvataggio campioni, query di finestra per `fast_move`, retention giornaliera.
- Test su database in memoria e su file temporaneo.

## M3 — Notifiche Telegram `[x]`

- `TelegramNotifier`: `sendMessage` con `requests`, `parse_mode=HTML` con escape, split oltre il limite, retry breve, token mascherato nei log.
- Formattazione italiana dei numeri e dei messaggi.
- CLI: `test-telegram`.
- Test con `requests` simulato: split, escape, errori 4xx/5xx, nessun token nei log.

## M4 — Ciclo completo e primo end-to-end `[x]`

- Scheduler allineato agli slot dell'intervallo, mercato aperto/chiuso, controllo di freschezza del dato.
- Ciclo: fetch di tutti gli asset, salvataggio, messaggio di prova con le variazioni (senza motore alert).
- CLI: `run`, `run --once`, `run --once --dry-run`.
- Prova reale sul Raspberry: tempi di un ciclo, RSS, CPU.
- Note: ogni ciclo logga durata e RSS (`cycle done in ...`); la prova reale sul Pi è nella checklist di `docs/DEPLOY.md`. `--dry-run` non scrive nel database. I mercati con orari vengono letti fino a 20 minuti dopo la chiusura, per avere il prezzo finale nonostante il ritardo dei dati.

## M5 — Motore degli alert `[ ]`

- Funzioni pure per `fast_move`, `day_move`, `open_gap`, livelli, con stato (`alert_state`).
- Tutti i casi elencati in `docs/ALERTS.md`.

## M6 — Filtri di consegna `[ ]`

- Cooldown, quiet hours con urgenti, limite orario, batch per ciclo con "+N altri".
- Gli alert soppressi non aggiornano lo stato.

## M7 — Riepilogo e salute `[ ]`

- Digest giornaliero e comando `digest --now`; variante weekend solo crypto.
- Contatori di fallimenti per provider, messaggi di errore e recupero, nessuna ripetizione.

## M8 — Deploy e collaudo sul Raspberry `[ ]`

- Adatta `deploy/market-monitor.service`, abilita il servizio, controlla `journalctl`.
- Misura RSS e CPU a regime, dimensione del database dopo 48 ore.
- Prova di resilienza: rete assente, Telegram non raggiungibile, provider che risponde 429, riavvio del Pi.
- Taratura delle soglie in `alerts.yaml` dopo qualche giorno di uso reale.

## Evolutiva 1 — M9: integrazione con picoclaw tramite file di stato `[ ]`

Da iniziare solo dopo M8, con la v1 stabile sul Raspberry e conferma dell'utente. Obiettivo: far leggere a picoclaw (agente AI) lo stato del servizio, così può rispondere a domande tipo "come va Eni oggi?" o commentare gli alert. Il servizio resta indipendente: picoclaw legge, non scrive e non controlla nulla. Se picoclaw è spento, non cambia niente.

- Il servizio scrive a fine ciclo un file di stato (`status.json`). Percorso e on/off in `config.yaml`, sezione `status_file`, disattivato di default.
- Scrittura atomica (file temporaneo + `os.replace`), così il lettore non vede mai un file a metà. Una scrittura per ciclo; il percorso può stare su tmpfs (es. `/dev/shm`) per non scrivere sulla SD: dopo un riavvio il file si rigenera al ciclo successivo.
- Contenuto, con `schema_version` per le evoluzioni future: data e ora dell'ultimo ciclo e intervallo; per ogni asset nome, simbolo, ultimo prezzo e orario del dato, variazione sul giorno, mercato aperto o chiuso; ultimi alert inviati (numero configurabile); livelli personali raggiunti; salute dei provider; data dell'ultimo riepilogo. Esempio in `docs/ARCHITECTURE.md`.
- Nessun segreto nel file (mai token né chat id). Quando picoclaw lo legge, il contenuto può arrivare al provider LLM che usa: solo dati di mercato e stato degli alert.
- Lato picoclaw, da verificare nella sua documentazione come l'agente legge i file. In base alla documentazione consultata il suo workspace predefinito è `~/.picoclaw/workspace` e di default lavora in sandbox: il file potrebbe dover stare lì o essere collegato con un link simbolico. Documentare la procedura in `docs/PICOCLAW.md`, con le istruzioni da dare all'agente ed esempi di domande.
- CLI: `python -m market_monitor status --print` stampa il file di stato (utile per il debug).
- Test: schema e `schema_version`, scrittura atomica (nessun file parziale anche con un errore a metà), assenza di segreti, comportamento con fallimenti parziali dei provider.

## Idee future (dopo M9)

Volumi anomali, massimi/minimi a 52 settimane, fallback automatico tra provider, comandi di stato da Telegram tramite picoclaw (mai con `getUpdates` dal servizio).
