# Interfaccia web di configurazione (M10)

Una pagina web, raggiungibile dalla rete di casa, per gestire Market Monitor senza modificare i file a mano:

- **Stato**: ultimi prezzi, alert recenti, salute dei provider, ultimo riepilogo. Pulsanti "Messaggio di prova" e "Invia riepilogo ora".
- **Watchlist**: aggiungere, togliere e disattivare asset; livelli di prezzo; soglie diverse per un singolo asset (Override); pulsante "Verifica" che interroga il provider.
- **Alert e antispam** (`alerts.yaml`): soglie per classe, livelli, cooldown, quiet hours, riepilogo, salute.
- **Generale** (`config.yaml`): intervallo, mercati e orari, provider, Telegram, database, log, file di stato, interfaccia web.

L'interfaccia è un processo separato dal monitor: se ha un problema, il monitor continua a girare. Le due parti comunicano solo tramite i file YAML (il monitor li rilegge al ciclo successivo, senza riavvio) e il database, che l'interfaccia legge senza mai modificarlo.

## 1. Attivazione

1. Scegli una password (almeno 8 caratteri) e aggiungila a `.env`:
   ```
   WEB_PASSWORD=una-password-lunga
   ```
2. Facoltativo: host e porta in `config/config.yaml` (default `0.0.0.0:8080`, cioè raggiungibile dalla rete di casa):
   ```yaml
   web:
     host: 0.0.0.0
     port: 8080
   ```
3. Prova a mano:
   ```bash
   cd ~/market-monitor
   PYTHONPATH=src .venv/bin/python -m market_monitor web
   ```
   Atteso: `Interfaccia web su http://0.0.0.0:8080`. Dal PC o dal telefono, sulla stessa rete, apri `http://<indirizzo-del-pi>:8080` (l'indirizzo lo trovi con `hostname -I`). Il browser chiede utente e password: l'utente può essere qualsiasi (es. `admin`), la password è `WEB_PASSWORD`. Chiudi con Ctrl+C.

Senza `WEB_PASSWORD` l'interfaccia parte solo con `host: 127.0.0.1` (accesso solo dal Pi stesso).

## 2. Servizio systemd

```bash
cd ~/market-monitor
sed -e "s#User=pi#User=$USER#" -e "s#/home/pi/market-monitor#$HOME/market-monitor#g" \
    deploy/market-monitor-web.service | sudo tee /etc/systemd/system/market-monitor-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now market-monitor-web
systemctl status market-monitor-web --no-pager
journalctl -u market-monitor-web -n 20 --no-pager
```

## 3. Come si usa

- I numeri si scrivono con la virgola (`1,5`). Nelle liste (gradini, livelli, giorni) i valori si separano con `;` (es. `3; 5; 8`).
- **Override** di un asset: i campi vuoti usano il valore della classe, mostrato in grigio. Scrivi solo quello che vuoi cambiare.
- **Verifica** interroga il provider con il simbolo scritto nella riga: usala prima di salvare un asset nuovo.
- Ogni scheda ha il suo **Salva**. Le modifiche valgono dal ciclo successivo del monitor (al massimo 15 minuti), senza riavvio.
- Se lasci la pagina con modifiche non salvate, il browser chiede conferma.

## 4. Cosa succede quando salvi

1. Il file viene validato con le stesse regole di `check-config`, insieme agli altri due file. Se c'è un errore, la pagina lo mostra (es. `alerts.yaml: delivery.quiet_hours.start: orario non valido '25:00'`) e **non viene salvato niente**.
2. La versione precedente viene copiata in `config/<file>.yaml.bak`.
3. Il nuovo file viene scritto in modo atomico: il monitor non legge mai un file a metà.
4. Se nel frattempo il file è stato modificato a mano, il salvataggio viene rifiutato: ricarica la pagina.

I file salvati dall'interfaccia **perdono i commenti** (in testa compare una riga che lo ricorda). Le spiegazioni dei campi restano nei file `config/*.example.yaml`.

Non si modificano dall'interfaccia: token e chat id Telegram e `WEB_PASSWORD` (solo in `.env`). Le modifiche a `web.host` e `web.port` valgono dopo `sudo systemctl restart market-monitor-web`.

## 5. Sicurezza

- Pensata per la **rete di casa**. Non aprire la porta 8080 sul router verso internet: la connessione è HTTP, non cifrata.
- Per usarla da fuori casa: una VPN (es. WireGuard o Tailscale) oppure un tunnel SSH con `web.host: 127.0.0.1`:
  ```bash
  ssh -L 8080:localhost:8080 <utente>@<indirizzo-del-pi>
  ```
  e poi `http://localhost:8080` sul tuo PC.
- Protezioni: password con confronto a tempo costante e un secondo di attesa dopo una password sbagliata; header obbligatorio sulle richieste che modificano (blocca le richieste da altri siti); richieste limitate a 256 kB; Content-Security-Policy restrittiva; nessun segreto nelle risposte; i simboli accettano solo lettere, cifre e `. ^ = _ -`.

## 6. Risorse

Circa 20-25 MB di RAM, CPU a zero quando nessuno usa la pagina. Il servizio ha `MemoryMax=100M` come rete di sicurezza.

## 7. Problemi comuni

- **`WEB_PASSWORD mancante`** all'avvio: aggiungila a `.env` o usa `web.host: 127.0.0.1`.
- **`Impossibile aprire 0.0.0.0:8080`**: la porta è già usata da un altro programma. Cambia `web.port`.
- **La pagina non si apre dal telefono**: controlla di essere sulla stessa rete e l'indirizzo con `hostname -I`; verifica `systemctl status market-monitor-web`.
- **"la configurazione attuale non è valida"** in alto: un file modificato a mano ha un errore. Il monitor continua con l'ultima versione valida; correggi dall'interfaccia (o a mano) e salva.
- **Ripristinare la versione precedente** di un file: `cp config/watchlist.yaml.bak config/watchlist.yaml`.
