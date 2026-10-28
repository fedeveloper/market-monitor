# File di stato (M9)

Market Monitor può scrivere a fine ciclo un file JSON con lo stato corrente: ultimi prezzi, alert recenti, salute dei provider. Serve a far leggere i dati ad altri programmi (uno script, una dashboard, un assistente) senza toccare il database.

```
market-monitor --(scrive a fine ciclo)--> status.json --(legge)--> altri programmi
```

- Direzione unica: il servizio scrive, gli altri leggono. Il servizio non dipende da chi legge.
- Scrittura atomica (file temporaneo + `os.replace`): chi legge non vede mai un file a metà.
- Nel file solo dati di mercato e stato degli alert: **mai** token o chat id.

## 1. Attivazione

In `config/config.yaml` (vedi `config/config.example.yaml`):

```yaml
status_file:
  enabled: true
  path: /dev/shm/market-monitor/status.json
  recent_alerts: 20
```

`/dev/shm` è in RAM (tmpfs): nessuna scrittura sulla SD. Dopo un riavvio del Pi il file manca finché non gira il primo ciclo. La modifica vale dal ciclo successivo, senza riavviare il servizio. Il file ha permessi `644`: è leggibile anche da altri utenti del Pi.

Verifica, dopo un ciclo:

```bash
cd ~/market-monitor
PYTHONPATH=src .venv/bin/python -m market_monitor status --print
ls -l /dev/shm/market-monitor/status.json
```

## 2. Formato

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

- `change_day_pct`: variazione sulla chiusura precedente se `change_reference` è `prev_close`, sulle ultime 24 ore se è `rolling_24h` (crypto).
- `levels` c'è solo per gli asset con livelli personali.
- `price` e gli altri campi del dato sono `null` se per quell'asset non c'è ancora nessun campione salvato.
- Se un provider fallisce, l'asset mostra l'ultimo dato salvato, con il suo `quote_time`, e il provider risulta con `ok: false`.
- `recent_alerts` è in ordine dal più recente; `rule` è uno tra `fast_move`, `day_move`, `open_gap`, `level_above`, `level_below`.
- Se `generated_at` è più vecchio di due intervalli, il servizio potrebbe essere fermo.
- I cambi incompatibili del formato incrementano `schema_version`; i campi nuovi possono essere aggiunti senza cambiarlo.

## 3. Problemi comuni

- **`status --print` dice "File di stato disattivato"**: manca `enabled: true` in `status_file`.
- **"non esiste ancora"**: il servizio non ha ancora completato un ciclo dopo l'attivazione o dopo il riavvio del Pi.
- **Errore nel journal `could not write the status file`**: percorso non scrivibile dall'utente del servizio. Il ciclo continua comunque.
