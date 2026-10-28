# Integrazione con picoclaw (M9)

Market Monitor scrive a fine ciclo un file di stato JSON; l'agente di picoclaw lo legge per rispondere a domande come "come va Eni oggi?" o per commentare gli alert.

```
market-monitor --(scrive a fine ciclo)--> status.json --(legge)--> agente picoclaw
```

- Direzione unica: il servizio scrive, picoclaw legge. Il servizio non chiama picoclaw e non dipende da lui: se picoclaw è spento non cambia niente.
- Scrittura atomica (file temporaneo + `os.replace`): picoclaw non vede mai un file a metà.
- Nel file solo dati di mercato e stato degli alert: **mai** token o chat id. Quando picoclaw lo legge, il contenuto arriva al provider LLM che usa.

## 1. Attivare il file di stato

In `config/config.yaml` (vedi `config/config.example.yaml`):

```yaml
status_file:
  enabled: true
  path: /dev/shm/market-monitor/status.json
  recent_alerts: 20
```

`/dev/shm` è in RAM (tmpfs): nessuna scrittura sulla SD. Dopo un riavvio del Pi il file manca finché non gira il primo ciclo. La modifica vale dal ciclo successivo, senza riavviare il servizio.

Verifica, dopo un ciclo (al quarto d'ora successivo, o subito con `run --once`):

```bash
cd ~/market-monitor
PYTHONPATH=src .venv/bin/python -m market_monitor status --print
ls -l /dev/shm/market-monitor/status.json
```

## 2. Dare a picoclaw l'accesso al file

Verificato nel `config.example.json` ufficiale di picoclaw (repository `sipeed/picoclaw`, ottobre 2026):
- il workspace predefinito è `~/.picoclaw/workspace` (`agents.defaults.workspace`);
- `agents.defaults.restrict_to_workspace` è `true`: gli strumenti dei file lavorano solo dentro il workspace;
- `tools.allow_read_paths` (di default `null`) elenca i percorsi leggibili anche fuori dal workspace.

Secondo la documentazione i link simbolici che portano fuori dal workspace vengono bloccati: per questo **non** usare un link simbolico. Ci sono due strade.

### Opzione A (consigliata): `allow_read_paths`

In `~/.picoclaw/config.json`, sezione `tools`:

```json
"allow_read_paths": ["/dev/shm/market-monitor/status.json"]
```

Poi riavvia picoclaw come fai di solito. **Da verificare** sulla tua versione: se la voce accetta anche una cartella (`/dev/shm/market-monitor`) e se il nome della chiave è lo stesso (nelle versioni vecchie potrebbe mancare). Se picoclaw risponde che il percorso è fuori dal workspace, usa l'opzione B.

### Opzione B: file dentro il workspace

In `config/config.yaml` di Market Monitor:

```yaml
status_file:
  enabled: true
  path: /home/<utente>/.picoclaw/workspace/market-monitor/status.json
```

Funziona senza toccare la config di picoclaw. È l'unico punto in cui Market Monitor scrive nella cartella di picoclaw (solo la sottocartella `market-monitor/`), e il file sta sulla SD: circa 5 kB riscritti ogni 15 minuti.

In entrambi i casi il file ha permessi `644`: picoclaw lo legge anche se gira con un altro utente.

## 3. Istruzioni per l'agente

Il modo più semplice è scriverle in chat a picoclaw una volta, chiedendogli di ricordarle. In alternativa puoi creare una skill: il README di picoclaw dice che le skill si caricano dai file `SKILL.md` nel workspace. **Da verificare** la cartella esatta sulla tua versione; di solito è `~/.picoclaw/workspace/skills/market-monitor/SKILL.md`:

```markdown
---
name: market-monitor
description: Legge lo stato di Market Monitor (prezzi, variazioni, alert) per rispondere a domande su azioni, ETF e crypto della watchlist.
---

Quando ti chiedo dei miei titoli, della borsa o degli alert, leggi il file
/dev/shm/market-monitor/status.json (formato JSON, schema_version 1).

- assets: per ogni titolo name, symbol, price, currency, quote_time (orario del dato),
  change_day_pct (variazione: sulla chiusura precedente se change_reference è
  "prev_close", sulle ultime 24 ore se è "rolling_24h"), market_open, levels.
- recent_alerts: ultimi alert inviati, il più recente per primo (rule: fast_move,
  day_move, open_gap, level_above, level_below; detail: testo dell'alert).
- levels_reached_today: livelli personali raggiunti oggi.
- providers: ok=false vuol dire che quella fonte dati ha problemi.
- generated_at: se è più vecchio di 30 minuti, avvisami che il servizio
  potrebbe essere fermo.

I dati hanno fino a 15 minuti di ritardo. Riporta dati e variazioni, non dare
consigli di acquisto o vendita. Il file è in sola lettura: non modificarlo.
```

Se usi l'opzione B, sostituisci il percorso con quello del workspace.

## 4. Esempi di domande

- "Come va Eni oggi?"
- "Quali titoli della watchlist sono in calo più del 2%?"
- "Che alert sono arrivati oggi? Spiegami il più importante."
- "Bitcoin è vicino al livello che ho impostato?"
- "Il monitor sta funzionando? Quando è stato aggiornato l'ultima volta?"

## 5. Formato del file

```json
{
 "schema_version": 1,
 "generated_at": "2026-10-28T10:00:00+01:00",
 "interval_minutes": 15,
 "assets": [
  {"symbol": "ENI.MI", "name": "Eni", "class": "stock", "market": "IT", "market_open": true,
   "price": 14.52, "currency": "EUR", "quote_time": "2026-10-28T09:55:00+01:00",
   "change_day_pct": 3.12, "change_reference": "prev_close",
   "levels": {"above": [16.0], "below": []}}
 ],
 "recent_alerts": [
  {"ts": "2026-10-28T09:30:00+01:00", "symbol": "ENI.MI", "rule": "day_move", "detail": "+3,1% oggi (gradino 3%)"}
 ],
 "levels_reached_today": [],
 "providers": {"binance": {"ok": true, "consecutive_failures": 0}, "yahoo": {"ok": true, "consecutive_failures": 0}},
 "last_digest": "2026-10-27T18:00:00+01:00",
 "note": "Dati di mercato con ritardo fino a 15 minuti. Non sono consigli di investimento."
}
```

- `levels` c'è solo per gli asset con livelli personali.
- `price` e gli altri campi del dato sono `null` se per quell'asset non c'è ancora nessun campione salvato.
- Se un provider fallisce, l'asset mostra l'ultimo dato salvato, con il suo `quote_time`, e il provider risulta con `ok: false`.
- I cambi incompatibili del formato incrementano `schema_version`; i campi nuovi possono essere aggiunti senza cambiarlo.

## 6. Problemi comuni

- **`status --print` dice "File di stato disattivato"**: manca `enabled: true` in `status_file`.
- **"non esiste ancora"**: il servizio non ha ancora completato un ciclo dopo l'attivazione o dopo il riavvio del Pi.
- **picoclaw non riesce a leggerlo**: controlla `allow_read_paths` (opzione A) o passa all'opzione B; verifica i permessi con `ls -l`.
- **Errore nel journal `could not write the status file`**: percorso non scrivibile dall'utente del servizio. Il ciclo continua comunque.
