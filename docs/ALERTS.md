# Catalogo degli alert

Le soglie di default sono pensate per una notifica Telegram: poche, significative, senza ripetizioni. Tutti i valori si cambiano in `config/alerts.yaml` (default per classe di asset) e, per un singolo asset, in `config/watchlist.yaml` (sezione `overrides`). Le modifiche valgono dal ciclo successivo.

Il servizio riporta dati e soglie superate. Non produce raccomandazioni.

## Regole

| Regola | Cosa rileva | Classi | Default |
|---|---|---|---|
| `fast_move` | Variazione % su una finestra breve (movimento rapido) | stock, etf, crypto | finestra 60 min; soglia: stock 2,0% · etf 1,2% · crypto 3,0% |
| `day_move` | Variazione % rispetto al riferimento della giornata, con gradini | stock, etf, crypto | gradini: stock 3/5/8% · etf 2/4/6% · crypto 5/8/12% |
| `open_gap` | Gap di apertura rispetto alla chiusura precedente | stock, etf | stock 2,0% · etf 1,0%; crypto disattivato |
| `level_above` / `level_below` | Prezzo che supera un livello scelto da te | tutte | nessun livello di default |

### `fast_move`

- Confronta il prezzo attuale con il campione di `window_minutes` fa, solo con campioni della stessa sessione (per azioni ed ETF non attraversa mai la notte o il weekend).
- Scatta se `abs(variazione) >= threshold_pct`.
- Dopo un alert, un nuovo alert nella stessa direzione richiede un ulteriore `step_pct` oltre l'ultimo livello notificato, oppure il passaggio del cooldown. Così un titolo che sale lentamente non genera una notifica a ogni ciclo.
- Senza storico sufficiente (avvio a freddo) la regola resta muta finché la finestra non è piena.

### `day_move`

- Riferimento: chiusura precedente per azioni ed ETF; per le crypto la variazione sulle ultime 24 ore (`reference: rolling_24h`).
- Un alert per ogni gradino superato, per direzione. Esempio con gradini 3/5/8: un titolo a +3,2% genera un alert; il successivo arriva solo a +5%, poi a +8%.
- Riarmo (decisione presa con l'utente): un gradino già notificato si riarma quando la variazione rientra di almeno `rearm_pct` punti percentuali sotto il gradino (es. gradino 3% con `rearm_pct: 1.0`: si riarma sotto +2%). Se poi la variazione torna oltre il gradino, l'alert riparte, ma solo dopo il cooldown (120 minuti di default) dall'ultimo alert per la stessa regola. Default proposti per `rearm_pct`: stock 1,0 · etf 0,6 · crypto 1,5, sovrascrivibili per asset. Il parametro va aggiunto a `alerts.example.yaml` e alla validazione in M5.
- Azioni ed ETF: lo stato dei gradini si azzera a ogni nuova sessione (cambia la chiusura precedente di riferimento).
- Crypto (decisione presa con l'utente): riferimento sulle 24 ore mobili e **nessun azzeramento a mezzanotte**. Un gradino torna a scattare solo dopo il riarmo descritto sopra, così lo stesso movimento non viene notificato due volte a cavallo della mezzanotte.
- All'avvio o dopo un riavvio a metà sessione, i gradini già superati si inizializzano in silenzio (nessun alert retroattivo); il riepilogo giornaliero mostra comunque la situazione.

### `open_gap`

- Valutata una sola volta per sessione, sul primo campione dopo l'apertura: `(prezzo - chiusura_precedente) / chiusura_precedente`.
- Scatta se `abs(gap) >= threshold_pct`.
- Se il primo dato disponibile arriva molto dopo l'apertura (oltre `max_delay_minutes`), la regola viene saltata per quella sessione.

### Livelli di prezzo (`level_above`, `level_below`)

- Definiti per asset in `config/watchlist.yaml` (`levels.above`, `levels.below`).
- Scattano una sola volta, quando il prezzo incrocia il livello. Si riarmano solo quando il prezzo si allontana dal livello di `hysteresis_pct` (default 0,5%), per evitare ripetizioni quando oscilla attorno alla soglia.

## Antispam e consegna

| Meccanismo | Comportamento | Default |
|---|---|---|
| Cooldown | Un solo alert per coppia asset/regola in questo intervallo | 120 minuti |
| Batch | Un solo messaggio per ciclo, con tutti gli alert del ciclo raggruppati | max 10 alert per messaggio, poi "+N altri" |
| Limite orario | Massimo di messaggi di alert in un'ora | 6 |
| Quiet hours | Nessun alert non urgente nella fascia notturna (fuso `Europe/Rome`) | 23:00-07:00 |
| Urgenti | Passano anche in quiet hours i `day_move` oltre `urgent_pct` | stock 8% · etf 6% · crypto 10% |

Gli alert soppressi (quiet hours, limite orario) **non** aggiornano lo stato della regola: se la condizione è ancora vera quando la soppressione termina, l'alert parte allora.

## Messaggi

Un messaggio per ciclo, in italiano, numeri con virgola decimale e punto per le migliaia. Esempio:

```
Market Monitor · 14:30

▲ Eni (ENI.MI)  14,52 €
   +2,3% in 1 ora · +3,1% oggi (gradino 3%)

▼ Bitcoin (BTCEUR)  62.310 €
   −3,4% in 1 ora · −5,2% in 24 ore (gradino 5%)

Dati con ritardo fino a 15 minuti.
```

## Riepilogo giornaliero (digest)

- Ogni giorno alle 18:00 (`Europe/Rome`), dopo la chiusura di Borsa Italiana.
- Contenuto: per ogni asset prezzo e variazione del giorno, i 3 maggiori rialzi e ribassi, eventuali livelli personali raggiunti, stato del servizio.
- Nel weekend include solo le crypto (`weekend_crypto_only: true`).
- Funziona anche da heartbeat: se non arriva, il servizio è fermo.
- Invio manuale: `python -m market_monitor digest --now`.

## Salute del servizio

- Dopo `error_alert_after_failures` cicli consecutivi falliti di un provider: un solo messaggio di errore; un solo messaggio quando torna a funzionare.
- Dato più vecchio di `stale_data_minutes` (default 45): il mercato di quell'asset è considerato chiuso o il dato inaffidabile, e nessun alert viene generato per lui in quel ciclo.
- Un asset che fallisce non blocca gli altri.

## Casi da coprire nei test

Soglia esattamente sul limite; gradini in salita e in discesa; rientro con isteresi; cooldown; quiet hours con e senza urgenti; avvio a freddo senza storico; riavvio a metà sessione; dato mancante o `null`; gap di apertura con primo dato in ritardo; finestra `fast_move` che attraverserebbe la notte; più alert nello stesso ciclo oltre il limite per messaggio.
