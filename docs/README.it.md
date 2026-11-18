# Market Monitor

Servizio leggero per Raspberry Pi che monitora azioni, ETF e crypto con dati gratuiti e invia alert e riepiloghi su un bot Telegram.

- Intervallo di controllo: 15 minuti (configurabile in `config/config.yaml`)
- Dati: Yahoo Finance (endpoint `chart`, azioni ed ETF) e Binance (crypto), dietro un'interfaccia sostituibile
- Storage: SQLite, nessun server esterno
- Notifiche: Telegram Bot API, con filtri antispam (cooldown, quiet hours, batch)
- Nessun consiglio di investimento: solo dati e soglie superate

> Stato: v1 (milestone M0-M8) completa e collaudata sul Raspberry; file di stato (M9) e interfaccia web (M10) disponibili. Avanzamento in `docs/ROADMAP.md`, contesto per Claude Code in `CLAUDE.md`. Guida completa di installazione e collaudo: `docs/DEPLOY.md`.

## Comandi rapidi (promemoria)

Tutti da lanciare nella cartella del progetto: `cd ~/market-monitor`. I servizi sono due: `market-monitor` (il monitor) e `market-monitor-web` (l'interfaccia web).

**Aggiornare all'ultima versione**
```bash
cd ~/market-monitor
git pull
.venv/bin/pip install -r requirements.txt
PYTHONPATH=src .venv/bin/python -m market_monitor check-config
sudo systemctl restart market-monitor market-monitor-web
```
Se la PR lo dice, prima del restart reinstalla anche i file `.service` (comandi nella sezione "Come servizio" più sotto) e lancia `sudo systemctl daemon-reload`.

**Riavviare, fermare, avviare**
```bash
sudo systemctl restart market-monitor market-monitor-web   # riavvia entrambi
sudo systemctl restart market-monitor                      # solo il monitor
sudo systemctl restart market-monitor-web                  # solo l'interfaccia web
sudo systemctl stop market-monitor market-monitor-web      # ferma entrambi
sudo systemctl start market-monitor market-monitor-web     # avvia entrambi
sudo systemctl disable --now market-monitor-web            # ferma e non avviare più al boot
sudo systemctl enable --now market-monitor-web             # riattiva al boot e avvia
```

**Controllare che giri**
```bash
systemctl status market-monitor --no-pager                  # deve dire active (running)
systemctl status market-monitor-web --no-pager
journalctl -u market-monitor -n 30 --no-pager               # ultime 30 righe di log
journalctl -u market-monitor -f                             # log in tempo reale (Ctrl+C per uscire)
journalctl -u market-monitor-web -n 30 --no-pager
```

**Comandi del programma**
```bash
export PYTHONPATH=src
.venv/bin/python -m market_monitor check-config      # la configurazione è valida?
.venv/bin/python -m market_monitor check-symbols     # i simboli rispondono?
.venv/bin/python -m market_monitor test-telegram     # arriva il messaggio di prova?
.venv/bin/python -m market_monitor digest --now      # manda subito il riepilogo
.venv/bin/python -m market_monitor run --once --dry-run   # un ciclo di prova, senza inviare né salvare
.venv/bin/python -m market_monitor status --print    # file di stato (se attivo)
```

**Cosa richiede un riavvio**
- Modifiche a `watchlist.yaml`, `alerts.yaml`, `config.yaml` (a mano o dall'interfaccia web): **nessun riavvio**, valgono dal ciclo successivo (al massimo 15 minuti).
- Modifiche a `.env` (token, chat id, password web): `sudo systemctl restart market-monitor market-monitor-web`.
- Modifiche a `web.host` / `web.port`: `sudo systemctl restart market-monitor-web`.
- Dopo un `git pull`: `sudo systemctl restart market-monitor market-monitor-web`.

**Interfaccia web**: `http://<ip-del-pi>:8080` (IP con `hostname -I`, o `tailscale ip -4` da fuori casa). Utente qualsiasi, password `WEB_PASSWORD` di `.env`.

## Requisiti

- Raspberry Pi 3B, Raspbian, Python 3.11
- Un bot Telegram (token da @BotFather) e il tuo chat id

## Installazione sul Raspberry

```bash
git clone https://github.com/fedeveloper/market-monitor.git
cd market-monitor
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env                                  # inserisci token, chat id e password web
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

Come servizio (monitor e interfaccia web; per la web serve `WEB_PASSWORD` in `.env`, vedi `docs/WEB.md`):

```bash
for unit in market-monitor market-monitor-web; do
  sed -e "s#User=pi#User=$USER#" -e "s#/home/pi/market-monitor#$HOME/market-monitor#g" \
      deploy/$unit.service | sudo tee /etc/systemd/system/$unit.service > /dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now market-monitor market-monitor-web
journalctl -u market-monitor -f
```

## Configurazione

| File | Contenuto |
|---|---|
| `config/config.yaml` | intervallo, fuso orario, mercati e orari, provider, Telegram, storage, log |
| `config/watchlist.yaml` | asset monitorati (azioni, ETF, crypto), livelli di prezzo personali, override per asset |
| `config/alerts.yaml` | regole di alert per classe di asset, antispam, riepilogo giornaliero, salute del servizio |
| `.env` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `WEB_PASSWORD` (mai nel repo) |

Le modifiche a watchlist e alert vengono lette al ciclo successivo, senza riavviare. Il catalogo degli alert è in `docs/ALERTS.md`.

## Note

- I dati gratuiti di Borsa Italiana e USA via Yahoo sono spesso ritardati (circa 15 minuti) e l'endpoint non è ufficiale: può cambiare. Il provider è isolato per poterlo sostituire.
- Se non arriva più il riepilogo giornaliero, il servizio è fermo: controlla `systemctl status market-monitor`.
- Interfaccia web di configurazione (M10): watchlist, soglie e impostazioni dal browser, con validazione prima del salvataggio. Guida in `docs/WEB.md`.
- File di stato JSON (M9): lo stato corrente leggibile da altri programmi, disattivato di default. Guida in `docs/STATUS_FILE.md`.
