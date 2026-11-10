# Installazione del Tesoriere su Hermes (container Docker)

Flusso in 8 fasi. Ogni fase finisce con una verifica: si passa alla successiva solo se è ok.
I punti marcati **[verifica]** dipendono dalla propria installazione.

## Come è organizzato in Docker (configurazione reale, 10/11)
- Hermes è avviato con **docker compose** da `C:\Users\Utente\progetti\hermes` (immagine locale `hermes-m365:local`,
  costruita dal Dockerfile per il server MCP Microsoft 365).
- `/opt/data` è il **volume Docker `hermes_hermes-data`**, non una cartella di Windows: si raggiunge solo dai
  container (`docker exec`, `docker cp`). Non c'è nessun mount `/data`.
- I profili stanno in `/opt/data/profiles/<nome>/`; l'utente di runtime è `hermes` (UID 10000).
- Il Python dell'immagine (`/opt/hermes/tools/python-<versione>`) **cambia a ogni ricostruzione dell'immagine**:
  per questo il Tesoriere usa una propria copia del Python nel volume (`/opt/data/tesoriere/.python`).

| Nel container (volume) | Contenuto |
|---|---|
| `/opt/data/tesoriere` | pacchetto (copia del repository) + `.python` + `.venv` |
| `/opt/data/tesoreria/{in,out,archivio,sim}` | Report ricevuti, proposte, archivio, simulazioni ed export |
| `/opt/data/profiles/tesoriere` | profilo: SOUL.md, `.env`, skill collegata, cron |

Il **repository** sta su Windows (`C:\Users\Utente\progetti\agenteTesoreria`, clone di GitHub) e si copia nel volume
con `docker cp`: così il login a GitHub resta su Windows e nel container non serve alcun token.

Regole PowerShell da tenere a mente:
- un comando su più righe si continua con il **backtick** `` ` `` a fine riga, non con `\`;
- nel dubbio, scrivere il comando su **una riga sola**;
- i percorsi *dentro* il container restano Linux (`/opt/data/...`);
- per passare `$(...)` o `$VAR` al container, racchiudere il comando `sh -c` tra **apici singoli**.

Funzioni di comodo (valgono per la finestra di PowerShell aperta):
```powershell
$H = "hermes"
$R = "C:\Users\Utente\progetti\agenteTesoreria"      # clone del repository su Windows
function TX { docker exec -u hermes -w /opt/data/tesoriere $H @args }
# Python del Tesoriere con il .env del profilo caricato dentro il container
function PYT { docker exec -u hermes $H bash -c 'eval "$(bash /opt/data/tesoriere/skills/riscadenziamento-pagamenti/scripts/prepara_ambiente.sh --env)"; exec /opt/data/tesoriere/.venv/bin/python "$@"' _ @args }
```

---

## 0. Prerequisiti
- Container Hermes attivo (`docker ps` lo mostra) e `docker exec $H hermes doctor` ok.
- PostgreSQL raggiungibile **dal container** (`localhost` dentro il container non è l'host:
  `host.docker.internal` per un Postgres sull'host, il nome del servizio per un container nella stessa rete).
- Un bot Telegram dedicato (token di @BotFather) e il tuo **user ID** Telegram.
- Il terminale dell'agente gira **dentro** il container Hermes.
- Git per Windows installato.

## 1. Pacchetto e ambiente Python
Clone del repository su Windows (una volta sola):
```powershell
cd C:\Users\Utente\progetti
git clone -c core.autocrlf=false https://github.com/filippobandini123/agenteTesoreria.git
```
`core.autocrlf=false` mantiene i fine riga Linux, quelli che servono nel container.

Copia nel volume e permessi:
```powershell
docker exec $H mkdir -p /opt/data/tesoriere /opt/data/tesoreria
docker cp "$R\." "${H}:/opt/data/tesoriere/"
docker exec $H chown -R 10000:10000 /opt/data/tesoriere /opt/data/tesoreria
```
Python nel volume, venv e librerie (crea anche le cartelle di lavoro e verifica tutto):
```powershell
TX bash /opt/data/tesoriere/skills/riscadenziamento-pagamenti/scripts/prepara_ambiente.sh --installa
```
Il database risulterà KO finché non c'è il `.env` del profilo (fase 3): è atteso.

Verifica (una riga sola):
```powershell
docker exec -u hermes -w /opt/data/tesoriere/skills/riscadenziamento-pagamenti/scripts $H /opt/data/tesoriere/.venv/bin/python -m pytest -q tests
```
→ `31 passed`.

## 2. Database
Dal tuo strumento abituale (DBeaver, pgAdmin o `psql`): creare il database `tesoreria` ed eseguire
il file `db\schema.sql` del clone su Windows (`C:\Users\Utente\progetti\agenteTesoreria`).
Utente consigliato per l'agente: privilegi solo sullo schema `tesoreria` (SELECT, INSERT, UPDATE;
niente DROP, niente altri schemi). DSN **visto dal container**, ad esempio
`postgresql://tesoriere:<password>@postgres:5432/tesoreria` (nome del servizio) o
`…@host.docker.internal:5432/tesoreria` (Postgres installato su Windows: con Docker Desktop
`host.docker.internal` funziona già, senza opzioni).

## 3. Profilo Hermes
```powershell
docker exec $H hermes profile create tesoriere --description "<versione breve di description.md>"
docker exec -u hermes $H cp /opt/data/tesoriere/SOUL.md /opt/data/profiles/tesoriere/SOUL.md
TX mkdir -p /opt/data/profiles/tesoriere/skills
TX ln -s /opt/data/tesoriere/skills/riscadenziamento-pagamenti /opt/data/profiles/tesoriere/skills/riscadenziamento-pagamenti
```
Il collegamento va creato **dentro** il container (con percorsi `/opt/data/…`), altrimenti punta
a un percorso dell'host che il container non vede. **[verifica]** la cartella delle skill del profilo.

**Il file `.env` del profilo** sta *dentro il container* in `/opt/data/profiles/tesoriere/.env`.
Sull'host è nella cartella montata su `/opt/data`, che dipende dal tuo compose: per vederla
```powershell
docker inspect $H --format "{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}"
```
Il modo più sicuro è scriverlo **dal container**, aggiungendo in coda (niente BOM, niente CRLF, e non
si cancella quello che `profile create` ha già messo, per esempio la chiave del modello):
```powershell
TX ls -la /opt/data/profiles/tesoriere/          # c'è già un .env? (se sì, si aggiunge in coda)
@'
TESORIERE_HOME=/opt/data/tesoriere
TESORERIA_PG_DSN=postgresql://tesoriere:<password>@host.docker.internal:5432/tesoreria
TELEGRAM_BOT_TOKEN=<token di BotFather>
TELEGRAM_ALLOWED_USERS=<il tuo user ID>
'@ | docker exec -i -u hermes $H sh -c "tr -d '\r' >> /opt/data/profiles/tesoriere/.env && chmod 600 /opt/data/profiles/tesoriere/.env"
TX cut -d= -f1 /opt/data/profiles/tesoriere/.env   # verifica: solo i NOMI delle variabili, senza i segreti
```
Usare `@' … '@` con l'apice singolo: così un `$` nella password non viene interpretato da PowerShell.
Se un nome compare due volte (per esempio `TELEGRAM_BOT_TOKEN` già presente), correggere il file
tenendone uno solo. Se il profilo non ha la chiave del modello, aggiungere anche quella **[verifica]**
il nome della variabile del tuo provider. Dopo ogni modifica del `.env`, riavviare il gateway del profilo
(`docker exec $H hermes -p tesoriere gateway stop` e poi `start`).

Modello del profilo: Claude Sonnet 5.5 (configurazione dei modelli di Hermes per il profilo, **[verifica]** il comando).

Verifica: `docker exec -it $H hermes -p tesoriere chat` → "chi sei e cosa non puoi fare?" → risponde
secondo SOUL.md (niente ERP, niente terzi, anagrafica solo con conferma).

## 4. Anagrafica su Postgres (una volta sola)
Prima controlla l'Excel dell'anagrafica (non è nel repository: va copiato nel volume con `docker cp`): oggi contiene
i valori di prova Angelini = rilevanza 1 e Qualifarma = rilevanza 3.
```powershell
function AN { PYT /opt/data/tesoriere/skills/riscadenziamento-pagamenti/scripts/anagrafica.py @args }
AN importa --da /opt/data/tesoriere/config/anagrafica_fornitori.xlsx --autore "Filippo" --conferma
```
Verifica: `AN cerca qualifarma` → la riga del fornitore; `AN accordi --stato attivo` → Bennet.
(Se il DSN non risponde: è quasi sempre l'host di Postgres visto dal container, vedi fase 0.)

## 5. Prova del motore da terminale (senza Telegram)
```powershell
docker cp "<percorso del Report>.xlsx" "${H}:/opt/data/tesoreria/in/"
PYT /opt/data/tesoriere/skills/riscadenziamento-pagamenti/scripts/riscadenzia.py `
  --regole /opt/data/tesoriere/config/regole.yaml --fonti /opt/data/tesoriere/config/fonti_dati.yaml `
  --input /opt/data/tesoreria/in --out /opt/data/tesoreria/sim --json /opt/data/tesoreria/sim/ultimo_run.json --simula
```
Verifica: uscita `OK — …SIM_Proposta…xlsx` (nel volume in `/opt/data/tesoreria/sim/`); nel JSON
`fonte_anagrafica: database`; a DB una riga in `tes_run` con `tipo_run = simulazione`.

## 6. Telegram
```powershell
docker exec -it $H hermes -p tesoriere gateway setup     # scegli Telegram (se token e ID non sono già nel .env)
docker exec $H hermes -p tesoriere gateway start         # gateway del profilo, supervisionato nel container
```
Nel container ogni profilo ha il suo gateway supervisionato (riparte da solo); log in
`/opt/data/logs/gateways/tesoriere/current` (con `docker exec $H tail …`). **[verifica]** che il gateway del profilo riparta anche
dopo il riavvio del container; se no, aggiungere lo start al compose o al comando di avvio.

- Il bot risponde **solo** agli utenti in `TELEGRAM_ALLOWED_USERS`; un bot dedicato al Tesoriere
  (due profili non possono usare lo stesso token).
- Allegati scaricati fino a 20 MB (il Report ne pesa circa 4).
- La proposta torna come allegato con la riga `MEDIA:<percorso>` nella risposta (già nella skill);
  con il gateway nel container i percorsi `/opt/data/tesoreria/out/…` sono leggibili.
- **[verifica]** dove il gateway salva i documenti ricevuti: la skill dice all'agente di copiarli in
  `/opt/data/tesoreria/in`.

Verifiche in chat, nell'ordine:
1. "mostrami Qualifarma" → dati dell'anagrafica.
2. "metti Qualifarma a rilevanza 2" → anteprima prima/dopo e richiesta di conferma; rispondi **no** →
   niente scritto (`AN storico 101012` invariato).
3. Manda il Report → "Report ricevuto, lancio il run" → messaggio di sintesi con semaforo + Excel allegato.
4. Rimanda lo stesso file → avviso "stesso file già ricevuto".

## 7. Promemoria
In chat con il Tesoriere:
> "Crea un'attività programmata: nei giorni lavorativi alle 11:00, se oggi non c'è un run ufficiale
> riuscito in tes_run, scrivimi su Telegram che il Report di cassa non è ancora arrivato."

Verifica: il job compare in `/opt/data/profiles/tesoriere/cron/`. Attenzione al fuso orario del
container (spesso UTC): **[verifica]** con `docker exec $H date` e imposta `TZ=Europe/Rome` nel compose se serve.

## 8. Messa in produzione
- [ ] Prima settimana: confronto quotidiano della proposta con le scelte fatte a mano; esito in chat
      ("approvata", "approvata con modifiche: …", "respinta: …").
- [ ] Compilare la rilevanza dei fornitori principali (da Telegram o in SQL).
- [ ] Decidere i punti aperti in `DOMANDE_APERTE.md` (corrente degli accordi nei tagli, sigla nella
      nota dei tagliati, protocollo nel foglio Scaduti, Sezdoc).
- [ ] Backup: database `tesoreria` + cartella `/opt/data/tesoreria/archivio` del volume (es. `docker cp ${H}:/opt/data/tesoreria/archivio <cartella di backup>`).

## Aggiornare il pacchetto
1. Se `CHANGELOG.md` lo indica, eseguire **prima** la migrazione del database (`db/migrazione_*.sql`).
2. Aggiornare il clone su Windows e copiarlo nel volume (`.python`, `.venv` e i dati non vengono toccati):
   ```powershell
   git -C $R pull
   docker cp "$R\." "${H}:/opt/data/tesoriere/"
   docker exec $H chown -R 10000:10000 /opt/data/tesoriere
   ```
3. Se è cambiato `requirements.txt`: `TX bash /opt/data/tesoriere/skills/riscadenziamento-pagamenti/scripts/prepara_ambiente.sh --installa`
4. Se è cambiata la SOUL: `docker exec -u hermes $H cp /opt/data/tesoriere/SOUL.md /opt/data/profiles/tesoriere/SOUL.md`
5. Console: `docker compose restart` nella sua cartella.
6. Verifica: `TX bash /opt/data/tesoriere/skills/riscadenziamento-pagamenti/scripts/prepara_ambiente.sh` → `AMBIENTE OK`.

**Ricostruzione dell'immagine Hermes** (`docker compose build`, aggiornamenti): con il Python copiato nel volume il
venv non si rompe più. Se la verifica segnala che il venv dipende ancora dal Python dell'immagine, lanciare una volta
`prepara_ambiente.sh --installa`.

## Curator di Hermes
Il curator riscrive da solo le skill del profilo. Per il Tesoriere va **disattivato**: la skill è quella del repository
e una copia modificata nel profilo (es. `skills/finance/riscadenziamento-pagamenti`) prevale su quella collegata.
Verificare che nel profilo esista solo il collegamento:
```powershell
docker exec $H sh -c 'ls -la /opt/data/profiles/tesoriere/skills; find /opt/data/profiles/tesoriere/skills -name SKILL.md'
```
