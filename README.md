# Market Monitor

Servizio leggero per Raspberry Pi che monitora azioni, ETF e crypto con dati gratuiti e invia alert e riepiloghi su Telegram, riusando il bot di picoclaw.

- Intervallo di controllo: 15 minuti (configurabile in `config/config.yaml`)
- Dati: Yahoo Finance (endpoint `chart`, azioni ed ETF) e Binance (crypto), dietro un'interfaccia sostituibile
- Storage: SQLite, nessun server esterno
- Notifiche: Telegram Bot API, con filtri antispam (cooldown, quiet hours, batch)
- Nessun consiglio di investimento: solo dati e soglie superate

> Stato: v1 (milestone M0-M7) completa, collaudo sul Raspberry (M8) da fare. Avanzamento in `docs/ROADMAP.md`, contesto per Claude Code in `CLAUDE.md`. Guida completa di installazione e collaudo: `docs/DEPLOY.md`.

## Requisiti

- Raspberry Pi 3B, Raspbian, Python 3.11
- picoclaw già configurato con il bot Telegram (si riusa lo stesso token)

## Installazione sul Raspberry

```bash
git clone <url-del-repo> market-monitor
cd market-monitor
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env                                  # inserisci token e chat id
cp config/config.example.yaml    config/config.yaml
cp config/watchlist.example.yaml config/watchlist.yaml
cp config/alerts.example.yaml    config/alerts.yaml
```

Prova:

```bash
export PYTHONPATH=src
.venv/bin/python -m market_monitor check-config
.venv/bin/python -m market_monitor check-symbols
.venv/bin/python -m market_monitor test-telegram
.venv/bin/python -m market_monitor run --once --dry-run
.venv/bin/python -m market_monitor digest --now --dry-run
```

Come servizio:

```bash
sed -e "s#User=pi#User=$USER#" -e "s#/home/pi/market-monitor#$HOME/market-monitor#g" \
    deploy/market-monitor.service | sudo tee /etc/systemd/system/market-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now market-monitor
journalctl -u market-monitor -f
```

## Configurazione

| File | Contenuto |
|---|---|
| `config/config.yaml` | intervallo, fuso orario, mercati e orari, provider, Telegram, storage, log |
| `config/watchlist.yaml` | asset monitorati (azioni, ETF, crypto), livelli di prezzo personali, override per asset |
| `config/alerts.yaml` | regole di alert per classe di asset, antispam, riepilogo giornaliero, salute del servizio |
| `.env` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (mai nel repo) |

Le modifiche a watchlist e alert vengono lette al ciclo successivo, senza riavviare. Il catalogo degli alert è in `docs/ALERTS.md`.

## Note

- I dati gratuiti di Borsa Italiana e USA via Yahoo sono spesso ritardati (circa 15 minuti) e l'endpoint non è ufficiale: può cambiare. Il provider è isolato per poterlo sostituire.
- Se non arriva più il riepilogo giornaliero, il servizio è fermo: controlla `systemctl status market-monitor`.
- Prima evolutiva prevista dopo la v1: un file di stato che l'agente di picoclaw può leggere (M9 in `docs/ROADMAP.md`).
