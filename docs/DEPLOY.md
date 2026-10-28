# Installazione e collaudo sul Raspberry

Guida completa per installare, aggiornare e collaudare Market Monitor su Raspberry Pi 3B con Raspbian e Python 3.11. I comandi assumono che il repository stia in `~/market-monitor`.

## 1. Prerequisiti

```bash
python3 --version          # atteso Python 3.11.x
sudo apt install -y git python3-venv tzdata
```

Il bot Telegram di picoclaw deve essere già configurato: servono il suo token e il tuo chat id (in chat privata è il tuo user id Telegram).

## 2. Installazione

```bash
cd ~
git clone https://github.com/fedeveloper/market-monitor.git
cd market-monitor
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
nano .env                  # TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID
chmod 600 .env

cp config/config.example.yaml    config/config.yaml
cp config/watchlist.example.yaml config/watchlist.yaml
cp config/alerts.example.yaml    config/alerts.yaml
```

Per i comandi manuali qui sotto:

```bash
cd ~/market-monitor
export PYTHONPATH=src
alias mm='.venv/bin/python -m market_monitor'
```

## 3. Verifiche manuali, prima del servizio

Esegui in quest'ordine. Ogni comando esce con codice 0 se va tutto bene (`echo $?`).

| # | Comando | Risultato atteso |
|---|---|---|
| 1 | `mm check-config` | "Configurazione valida.", 13 asset attivi, "token presente, chat id presente". Il token non compare mai. |
| 2 | `mm check-symbols` | `OK` per ogni simbolo, con prezzo in formato italiano, variazione e "dato delle HH:MM (N min fa)". |
| 3 | `mm test-telegram` | Messaggio "Market Monitor · messaggio di prova" nella chat con il bot. picoclaw continua a rispondere normalmente. |
| 4 | `mm run --once --dry-run` | Nessun errore e la riga `cycle done in X s: N ok, ... RSS NN MB`. Gli eventuali alert compaiono come "[dry-run] messaggio non inviato". Non scrive nel database. |
| 5 | `mm run --once` | Come sopra, ma salva i dati: viene creato `data/market.db`. Al primo ciclo gli alert già "superati" vengono registrati in silenzio. |
| 6 | `mm digest --now --dry-run` | Il riepilogo nel terminale, con gli ultimi prezzi salvati. |
| 7 | `mm digest --now` | Il riepilogo su Telegram. |

### Note sulle verifiche dei provider (da M1)

- Se in `check-symbols` Binance dà `HTTP 451`, imposta `base_url: https://data-api.binance.vision` in `providers.binance` di `config/config.yaml`.
- Durante l'orario di Borsa Italiana annota il ritardo dei titoli `.MI` ("N min fa"), e dopo le 15:30 quello dei titoli USA, se ne aggiungi. È atteso intorno ai 15 minuti. Se è molto più alto, va alzato `health.stale_data_minutes` in `alerts.yaml` (oggi 45).
- Un `ERRORE` su un simbolo indica che va corretto nella watchlist (su Yahoo i titoli di Borsa Italiana hanno il suffisso `.MI`).
- Dallo sviluppo la fixture Yahoo è sintetica: se vuoi sostituirla con una reale, salva una risposta e mandamela:
  `curl -s -A "Mozilla/5.0 (compatible; market-monitor/0.1)" "https://query1.finance.yahoo.com/v8/finance/chart/ENI.MI?interval=15m&range=1d" > yahoo_ENI.json`

## 4. Servizio systemd

Il file `deploy/market-monitor.service` usa l'utente `pi` e `/home/pi/market-monitor`. Questo comando li adatta al tuo utente e lo installa:

```bash
sed -e "s#User=pi#User=$USER#" -e "s#/home/pi/market-monitor#$HOME/market-monitor#g" \
    deploy/market-monitor.service | sudo tee /etc/systemd/system/market-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now market-monitor
systemctl status market-monitor
journalctl -u market-monitor -f
```

Atteso nel journal: `service started: every 15 min aligned to the clock, 13 assets`, poi una riga `cycle done ...` a ogni :00, :15, :30, :45. Fuori orario di Borsa i titoli risultano `skipped (market closed)` e viene letto solo Bitcoin.

Prima di partire il servizio esegue `check-config`: se la configurazione non è valida non parte, e l'errore è in `journalctl -u market-monitor`.

I log vanno sia nel journal sia in `logs/market-monitor.log` (con rotazione: 1 MB per 3 file). Per scrivere meno sulla SD puoi mettere `file: null` nella sezione `logging` di `config.yaml` e tenere solo il journal.

## 5. Aggiornamento

```bash
cd ~/market-monitor
git pull
.venv/bin/pip install -r requirements.txt
PYTHONPATH=src .venv/bin/python -m market_monitor check-config
sudo systemctl restart market-monitor
journalctl -u market-monitor -n 30
```

Le modifiche a `watchlist.yaml` e `alerts.yaml` (e alle altre sezioni di `config.yaml`) non richiedono il riavvio: vengono lette al ciclo successivo. Se una modifica non è valida resta attiva la configurazione precedente e l'errore va nel log. Le modifiche a `.env` richiedono `sudo systemctl restart market-monitor`.

Confronta ogni tanto i tuoi file con i `*.example.yaml`: le chiavi nuove sono sempre opzionali, ma lì trovi i valori consigliati.

## 6. Collaudo (M8)

### Risorse

Dopo qualche ora di servizio:

```bash
systemctl status market-monitor | grep -E "Memory|CPU"
ps -o pid,rss,pcpu,etime,cmd -p "$(pgrep -f "market_monitor run")"
journalctl -u market-monitor --since "1 hour ago" | grep "cycle done"
```

Atteso:
- RSS sotto i 60 MB (in sviluppo 35 MB);
- CPU media vicina a 0%, con il processo che dorme tra i cicli;
- un ciclo che dura pochi secondi: con 12 titoli Yahoo e una pausa di 300 ms tra le richieste, circa 5-10 s.

Dopo 48 ore: `ls -lh data/`. È atteso qualche centinaio di kB: con 13 asset e 30 giorni di retention si arriva a pochi MB.

### Resilienza

| Prova | Come | Atteso |
|---|---|---|
| Rete assente | Stacca il cavo o spegni il Wi-Fi per 3-4 cicli in orario di Borsa | Nel journal warning di rete per ogni asset e `telegram: network error ..., message dropped`; il servizio non si ferma. Non arriva nessun messaggio, perché anche Telegram è irraggiungibile. Quando la rete torna, il ciclo successivo riprende normalmente. |
| Un provider giù | In `config.yaml` metti `base_url: https://query1.finance.yahoo.invalid` per `yahoo` e lascia passare 3 cicli in orario di Borsa | Un solo messaggio "il provider yahoo non risponde da 3 cicli consecutivi". Ripristinato l'URL, al ciclo dopo arriva un solo "funziona di nuovo". |
| Telegram non raggiungibile | Difficile da isolare senza fermare anche picoclaw: basta la prova "rete assente" | Nessun crash. Gli alert non consegnati vengono rivalutati al ciclo dopo. |
| Provider con 429 | Succede da solo con Yahoo se si fanno molte richieste. Puoi provare abbassando `interval_minutes` a 5 per un'ora | Righe `HTTP 429 ... retry in` nel journal e nessun blocco. Ripristina poi 15. |
| Configurazione rotta a caldo | Scrivi un errore in `watchlist.yaml` (es. `class: bond`) | Nel journal "configuration changed but is invalid, keeping the previous one", una sola volta. Il servizio continua. Correggi il file e al ciclo dopo compare "configuration reloaded". |
| Riavvio del Pi | `sudo reboot` a Borsa aperta | Il servizio riparte da solo (`systemctl status`). Nessun alert retroattivo per i gradini già superati prima del riavvio. |
| Heartbeat | Alle 18:00 | Il riepilogo arriva una sola volta al giorno; sabato e domenica solo con Bitcoin. |

### Taratura delle soglie

Dopo qualche giorno di uso reale, guarda quanti alert arrivano (`journalctl -u market-monitor | grep "alert sent"`) e regola `config/alerts.yaml`:
- troppi alert su un titolo: usa un `overrides` per quell'asset in `watchlist.yaml` (es. `fast_move: {threshold_pct: 3.0}`);
- troppi alert per una classe: alza `threshold_pct` o i gradini `steps_pct` in `defaults.<classe>`;
- troppe ripetizioni dello stesso gradino: alza `rearm_pct` o `delivery.cooldown_minutes`.

Le modifiche valgono dal ciclo successivo, senza riavvio.

## 7. Problemi comuni

- **Il servizio non parte**: `journalctl -u market-monitor -n 50`. Di solito è la configurazione (lancia `mm check-config`) o un percorso sbagliato nel file `.service`.
- **`TELEGRAM_BOT_TOKEN mancante`**: `.env` non è nella cartella del progetto, oppure manca la variabile.
- **`HTTP 400 (Bad Request: chat not found)`**: chat id sbagliato, oppure non hai mai scritto al bot.
- **Errori 409 in picoclaw**: Market Monitor non chiama mai `getUpdates`, quindi la causa è altrove (un'altra istanza di picoclaw?).
- **Nessun riepilogo alle 18:00**: il servizio è fermo (`systemctl status market-monitor`), oppure `digest.enabled` è `false`.
