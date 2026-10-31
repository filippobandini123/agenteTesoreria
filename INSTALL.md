# Installazione del Tesoriere su Hermes (container Docker)

Flusso in 8 fasi. Ogni fase finisce con una verifica: si passa alla successiva solo se è ok.
I punti marcati **[verifica]** dipendono dalla propria installazione.

## Come è organizzato in Docker
Nell'immagine ufficiale `nousresearch/hermes-agent`:
- il volume `~/.hermes` dell'host è montato in **`/opt/data`** nel container (`HERMES_HOME`);
- i profili stanno in `/opt/data/profiles/<nome>/`;
- l'utente di runtime è `hermes` (UID 10000);
- l'ambiente Python dell'immagine (`/opt/hermes/.venv`) è in sola lettura.

**Tutto il Tesoriere va quindi sul volume**, così sopravvive alla ricreazione del container:

| Nel container | Sull'host | Contenuto |
|---|---|---|
| `/opt/data/tesoriere` | `~/.hermes/tesoriere` | pacchetto (config, db, skill, motore) + virtualenv `.venv` |
| `/opt/data/tesoreria/{in,out,archivio,sim}` | `~/.hermes/tesoreria/…` | Report ricevuti, proposte, archivio, simulazioni |
| `/opt/data/profiles/tesoriere` | `~/.hermes/profiles/tesoriere` | profilo: SOUL.md, .env, skills, cron |

Host **Windows (PowerShell, Docker Desktop)**: `~/.hermes` = `C:\Users\<utente>\.hermes`.
Regole PowerShell da tenere a mente:
- un comando su più righe si continua con il **backtick** `` ` `` a fine riga, non con `\`
  (con `\` Docker riceve "\" come programma da eseguire: *executable file not found*);
- nel dubbio, scrivere il comando su **una riga sola**;
- i percorsi *dentro* il container restano Linux (`/opt/data/...`), quelli dell'host sono Windows.

Convenzioni: container `hermes` (cambia il nome se il tuo è diverso). In PowerShell, per comodità:
```powershell
$H = "hermes"                                   # nome del container
$EnvFile = "$HOME\.hermes\profiles\tesoriere\.env"   # percorso SULL'HOST: correggilo se /opt/data è montato altrove
function TX { docker exec -u hermes -w /opt/data/tesoriere $H @args }      # comando come utente hermes
function PYT { docker exec -u hermes --env-file $EnvFile $H /opt/data/tesoriere/.venv/bin/python @args }
```
Le due funzioni valgono per la finestra di PowerShell aperta: se la chiudi, rilanciale.

---

## 0. Prerequisiti
- Container Hermes attivo (`docker ps` lo mostra) e `docker exec $H hermes doctor` ok.
- PostgreSQL raggiungibile **dal container**. `localhost` dentro il container non è l'host:
  - Postgres in un altro container: stessa rete Docker, si usa il nome del servizio come host;
  - Postgres sull'host: `host.docker.internal`; su Linux serve `--add-host=host.docker.internal:host-gateway`
    (o `extra_hosts` nel compose).
- Un bot Telegram dedicato, creato con @BotFather (token), e il tuo **user ID** Telegram (es. @userinfobot).
- **[verifica]** Il terminale dell'agente deve girare **dentro** il container Hermes (backend `local`).
  Se usi un backend sandbox separato, quella sandbox deve montare `/opt/data`.

## 1. Pacchetto e ambiente Python
> **Dal 31/10 il pacchetto sta su GitHub.** Al posto dello zip, nella cartella dell'host montata su `/opt/data`:
> `git clone <url del repository> tesoriere` (prima installazione) oppure `git -C tesoriere pull` (aggiornamenti).
> Il file `config/anagrafica_fornitori.xlsx` non è nel repository: serviva solo al caricamento iniziale (fase 4),
> già fatto. Il resto della fase resta valido (virtualenv, test).

Sull'host (PowerShell):
```powershell
Expand-Archive .\agente-tesoreria.zip -DestinationPath $env:TEMP\tes -Force
New-Item -ItemType Directory -Force "$HOME\.hermes\tesoriere" | Out-Null
Copy-Item "$env:TEMP\tes\agente-tesoreria\*" "$HOME\.hermes\tesoriere\" -Recurse -Force
"in","out","archivio","sim" | ForEach-Object { New-Item -ItemType Directory -Force "$HOME\.hermes\tesoreria\$_" | Out-Null }
# proprietario = utente hermes del container (su Docker Desktop di solito non serve, ma non fa danni)
docker exec $H chown -R 10000:10000 /opt/data/tesoriere /opt/data/tesoreria
```
Virtualenv sul volume, creato con il Python dell'immagine:
```powershell
TX /opt/hermes/.venv/bin/python -m venv /opt/data/tesoriere/.venv
TX /opt/data/tesoriere/.venv/bin/pip install -r skills/riscadenziamento-pagamenti/scripts/requirements.txt
```
Se `pip` manca nel virtualenv (immagini con Python senza `ensurepip`), usare `uv`:
```powershell
TX uv venv /opt/data/tesoriere/.venv
TX uv pip install --python /opt/data/tesoriere/.venv/bin/python -r skills/riscadenziamento-pagamenti/scripts/requirements.txt
```
Ultima strada: un'immagine derivata `FROM nousresearch/hermes-agent` con i pacchetti installati.

Verifica (una riga sola):
```powershell
docker exec -u hermes -w /opt/data/tesoriere/skills/riscadenziamento-pagamenti/scripts $H /opt/data/tesoriere/.venv/bin/python -m pytest -q tests
```
→ `31 passed`.

## 2. Database
Dal tuo strumento abituale (DBeaver, pgAdmin o `psql`): creare il database `tesoreria` ed eseguire
il file `C:\Users\<utente>\.hermes\tesoriere\db\schema.sql`.
Utente consigliato per l'agente: privilegi solo sullo schema `tesoreria` (SELECT, INSERT, UPDATE;
niente DROP, niente altri schemi). DSN **visto dal container**, ad esempio
`postgresql://tesoriere:<password>@postgres:5432/tesoreria` (nome del servizio) o
`…@host.docker.internal:5432/tesoreria` (Postgres installato su Windows: con Docker Desktop
`host.docker.internal` funziona già, senza opzioni).

## 3. Profilo Hermes
```powershell
docker exec $H hermes profile create tesoriere --description "<versione breve di description.md>"
Copy-Item "$HOME\.hermes\tesoriere\SOUL.md" "$HOME\.hermes\profiles\tesoriere\SOUL.md" -Force
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
Prima controlla `C:\Users\<utente>\.hermes\tesoriere\config\anagrafica_fornitori.xlsx`: oggi contiene
i valori di prova Angelini = rilevanza 1 e Qualifarma = rilevanza 3.
```powershell
function AN { PYT /opt/data/tesoriere/skills/riscadenziamento-pagamenti/scripts/anagrafica.py @args }
AN importa --da /opt/data/tesoriere/config/anagrafica_fornitori.xlsx --autore "Filippo" --conferma
```
Verifica: `AN cerca qualifarma` → la riga del fornitore; `AN accordi --stato attivo` → Bennet.
(Se il DSN non risponde: è quasi sempre l'host di Postgres visto dal container, vedi fase 0.)

## 5. Prova del motore da terminale (senza Telegram)
```powershell
Copy-Item "<percorso del Report>.xlsx" "$HOME\.hermes\tesoreria\in\"
PYT /opt/data/tesoriere/skills/riscadenziamento-pagamenti/scripts/riscadenzia.py `
  --regole /opt/data/tesoriere/config/regole.yaml --fonti /opt/data/tesoriere/config/fonti_dati.yaml `
  --input /opt/data/tesoreria/in --out /opt/data/tesoreria/sim --json /opt/data/tesoreria/sim/ultimo_run.json --simula
```
Verifica: uscita `OK — …SIM_Proposta…xlsx` (sull'host in `~/.hermes/tesoreria/sim/`); nel JSON
`fonte_anagrafica: database`; a DB una riga in `tes_run` con `tipo_run = simulazione`.

## 6. Telegram
```powershell
docker exec -it $H hermes -p tesoriere gateway setup     # scegli Telegram (se token e ID non sono già nel .env)
docker exec $H hermes -p tesoriere gateway start         # gateway del profilo, supervisionato nel container
```
Nel container ogni profilo ha il suo gateway supervisionato (riparte da solo); log in
`C:\Users\<utente>\.hermes\logs\gateways\tesoriere\current`. **[verifica]** che il gateway del profilo riparta anche
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

Verifica: il job compare in `C:\Users\<utente>\.hermes\profiles\tesoriere\cron\`. Attenzione al fuso orario del
container (spesso UTC): **[verifica]** con `docker exec $H date` e imposta `TZ=Europe/Rome` nel compose se serve.

## 8. Messa in produzione
- [ ] Prima settimana: confronto quotidiano della proposta con le scelte fatte a mano; esito in chat
      ("approvata", "approvata con modifiche: …", "respinta: …").
- [ ] Compilare la rilevanza dei fornitori principali (da Telegram o in SQL).
- [ ] Decidere i punti aperti in `DOMANDE_APERTE.md` (corrente degli accordi nei tagli, sigla nella
      nota dei tagliati, protocollo nel foglio Scaduti, Sezdoc).
- [ ] Backup: database `tesoreria` + cartella `C:\Users\<utente>\.hermes\tesoreria\archivio` (il resto si ricrea dal pacchetto).

## Aggiornare il pacchetto
Sull'host, sostituire i file in `C:\Users\<utente>\.hermes\tesoriere` **tranne `.venv`** (il `.env` sta nel profilo),
rifare il `chown` della fase 1, poi i test della fase 1. Se `requirements.txt` è cambiato, rilanciare il
`pip install`. Se c'è un nuovo `db/migrazione_*.sql`, eseguirlo una volta sul database.
Aggiornare l'immagine Hermes non tocca il Tesoriere (sta tutto sul volume); dopo un aggiornamento
rifare comunque i test, perché cambia il Python dell'immagine con cui è stato creato il virtualenv:
se i test falliscono, ricreare `.venv` (fase 1).
