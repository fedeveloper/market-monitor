# Installazione e collaudo sul Raspberry

Le istruzioni complete di installazione arrivano con M8. Questa pagina raccoglie intanto le verifiche che si possono fare solo dal Pi.

## Verifiche dei provider (M1)

Dall'ambiente di sviluppo Yahoo rispondeva 429 e Binance 451, quindi queste verifiche vanno fatte dal Pi:

1. `PYTHONPATH=src .venv/bin/python -m market_monitor check-symbols`
   - atteso `OK` per ogni simbolo, con prezzo, variazione e "dato delle HH:MM (N min fa)";
   - durante l'orario di Borsa Italiana annota il ritardo dei titoli `.MI`, e dopo le 15:30 quello dei titoli USA: è il ritardo reale dei dati gratuiti (atteso circa 15 minuti);
   - un `ERRORE` su un simbolo indica che va corretto nella watchlist.
2. Se Binance dà `HTTP 451`, metti `base_url: https://data-api.binance.vision` in `providers.binance` di `config/config.yaml`.
3. Facoltativo, per sostituire la fixture sintetica di Yahoo con una reale:
   `curl -s -A "Mozilla/5.0 (compatible; market-monitor/0.1)" "https://query1.finance.yahoo.com/v8/finance/chart/ENI.MI?interval=15m&range=1d" > yahoo_ENI.json`
   e manda il file per aggiornare `tests/fixtures/`.
