# Fixture dei provider

| File | Origine |
|---|---|
| `binance_ticker_24hr_BTCEUR.json` | Risposte reali (28/10/2026) dal mirror pubblico ufficiale `data-api.binance.vision`, stesso formato di `api.binance.com` |
| `yahoo_chart_ENI.MI.json`, `yahoo_chart_not_found.json` | **Sintetiche**, costruite sul formato noto dell'endpoint `chart`: dall'ambiente di sviluppo Yahoo rispondeva HTTP 429. Da sostituire con una risposta reale presa dal Raspberry (vedi `docs/ARCHITECTURE.md`) |
