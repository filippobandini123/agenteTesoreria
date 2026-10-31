# Console Tesoreria

Interfaccia web per CFO, Specialist e Filippo, raggiungibile dalla VPN.

| Pagina | Cosa si fa |
|---|---|
| **Cruscotto** | Ultimo piano ufficiale: semaforo, fabbisogno, tagli, grafico del margine sul fido, ultimi run, Excel della proposta |
| **Fornitori** | Anagrafica valida oggi con gli importi dell'ultimo run; modifica singola o in blocco (rilevanza, elenco, classe, vincoli) |
| **Accordi** | Stato, decorrenza, scadenze, scaduto congelato e corrente; modifica e nuovo accordo |
| **Simulazioni** | Rifà il piano su un Report archiviato cambiando rilevanza, classe, elenco, vincoli, stato degli accordi, fido, look-ahead; confronto con il piano ufficiale |
| **Registro** | Chi ha cambiato cosa, quando e perché; accessi alla console |

## Regole che la console rispetta
- **Ogni modifica passa dall'anteprima**: prima/dopo, motivo obbligatorio, poi conferma. Niente viene scritto prima.
- **Storico a versioni**, come per l'agente: la versione precedente viene chiusa, mai sovrascritta. Più modifiche
  dello stesso fornitore nello stesso giorno aggiornano la versione del giorno; il registro le conserva tutte.
- **Autore** = nome dell'utente collegato, es. `Laura Bianchi [lbianchi] (console)`.
- **Le simulazioni non toccano nulla**: anagrafica e piano ufficiale restano invariati. Usano l'anagrafica di oggi
  più le modifiche simulate; se dopo il piano ufficiale qualcuno ha cambiato l'anagrafica, la pagina del risultato
  lo elenca, perché quelle modifiche entrano nel confronto.
- **Il piano ufficiale resta dell'agente**: la console non lancia run ufficiali e non scrive nel gestionale.

## Accesso e sicurezza
- Login con utente e password **del gestionale** (tabella `public.utente`, password MD5): la console legge la
  tabella in sola lettura e non salva mai la password.
- Entrano **solo** gli username elencati in `CONSOLE_UTENTI`, anche se altri hanno credenziali valide.
- 5 tentativi falliti (per utente o per indirizzo) → blocco di 15 minuti. Sessione di 8 ore.
- Protezione CSRF sui moduli, intestazioni di sicurezza (niente iframe, niente script esterni: grafici e
  script sono serviti dalla console stessa).
- Utente PostgreSQL dedicato con permessi minimi (`console/docker/permessi.sql`): niente DELETE, niente
  accesso ad altre tabelle del gestionale; della tabella utenti solo le colonne del login.
- Il container monta solo il pacchetto (sola lettura) e la cartella dei dati, non i segreti di Hermes.

Due note sul sistema esistente, non introdotte dalla console:
- l'MD5 senza sale è un hash debole: chi leggesse la tabella `utente` potrebbe risalire alle password semplici;
- il vincolo `UNIQUE (password)` sulla tabella impedisce a due utenti di avere la stessa password e, indirettamente,
  rivela quando una password è già in uso. Da valutare con chi gestisce il gestionale.

---

## Installazione (sul server dove gira Hermes)

### 1. Pacchetto aggiornato sul volume
Come per ogni aggiornamento (INSTALL.md, "Aggiornare il pacchetto"): i file nuovi vanno in
`<HERMES_DATA>/tesoriere`, compresa la cartella `console/`.

### 2. Database
```sql
-- una volta, come amministratore del database
\i db/migrazione_2026-10-29.sql          -- colonna tes_run.avviato_da
\i console/docker/permessi.sql           -- utente tesoreria_console (mettere una password vera)
```
(da pgAdmin: aprire i due file ed eseguirli.)

### 3. Cartella della console, fuori dal volume di Hermes
Creare sul server una cartella, es. `tesoreria-console`, e copiarci da `<HERMES_DATA>/tesoriere/console/docker/`:
- `docker-compose.yml`
- `console.env.esempio` → rinominarlo `console.env` e compilarlo

Nella stessa cartella creare un file `.env` con una sola riga, il percorso dell'host montato su `/opt/data` in Hermes:
```
HERMES_DATA=C:\Users\<utente>\.hermes
```
(su Linux, ad esempio `HERMES_DATA=/home/<utente>/.hermes`.)

In `console.env`:
- `TESORERIA_PG_DSN`: utente `tesoreria_console`; host **visto dal container** (come per Hermes:
  `host.docker.internal` se Postgres è sull'host, nome del servizio se è un container nella stessa rete).
- `CONSOLE_SECRET`: chiave casuale lunga. In PowerShell:
  `$b = New-Object byte[] 48; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b)`
- `CONSOLE_UTENTI`: username del gestionale di CFO, Specialist e tuo, separati da virgola.
- `CONSOLE_STATO_ATTIVO`: se `utente.stato` distingue gli utenti attivi, il valore che indica "attivo".

### 4. Avvio
Dalla cartella `tesoreria-console`:
```powershell
docker compose up -d --build
docker compose logs -f          # deve comparire "Application startup complete"
```
Se Postgres è un container, decommentare `networks` nel compose con il nome della sua rete
(`docker network ls`).

### 5. Rete
Aprire la porta **8080** del server solo verso gli indirizzi della VPN (firewall del server).
Indirizzo per gli utenti: `http://<IP del server>:8080`.

### 6. Verifiche
1. Login con un utente **non** in `CONSOLE_UTENTI` → rifiutato.
2. Login con il tuo utente → cruscotto con l'ultimo piano ufficiale.
3. Fornitori → seleziona un fornitore, rilevanza 2 → anteprima → **Annulla**: nel registro non compare nulla.
4. Simulazioni → un fornitore a rilevanza 1 → risultato con il confronto e l'Excel scaricabile.
5. Registro → l'accesso e le eventuali modifiche con il tuo nome.

### HTTPS (consigliato)
La VPN cifra il traffico fino alla rete aziendale; dentro la rete la console viaggia in HTTP. Per cifrare anche
quel tratto si mette davanti un reverse proxy con certificato (es. Caddy con certificato interno, o il proxy già
in uso in azienda) e si imposta `CONSOLE_HTTPS=1`. I browser vanno configurati per fidarsi del certificato interno.

## Aggiornamento del 29/10 pomeriggio: conto prevalente, debito totale, fornitori non in anagrafica
Nella pagina Fornitori: colonne **Conto prevalente**, **Debito totale** e **di cui scaduto**, filtro per conto,
ordinamento per debito o scaduto, totale del debito dei fornitori filtrati in testata. Nella scheda del fornitore,
debito, scaduto e conto in alto.
In coda all'elenco, evidenziati, i **fornitori presenti nello scadenzario ma non in anagrafica** (il motore li tratta
con la classe di default). Si aggiungono selezionandoli e impostando un campo (modifica in blocco) oppure dalla
loro scheda: la ragione sociale viene presa dallo scadenzario, con anteprima e conferma come ogni modifica.
- Il debito è la somma di tutte le scadenze del fornitore nell'ultimo piano ufficiale: anche oltre l'orizzonte,
  sospese e scaduto congelato, al netto delle note di credito. "Scaduto" = scadenza da documento passata.
- Il conto prevalente è il conto contabile con l'importo maggiore tra le scadenze del fornitore.

**Ordine dell'aggiornamento (importante):**
1. eseguire `db/migrazione_2026-10-29_conto.sql` (aggiunge la colonna `conto`);
2. poi aggiornare il pacchetto sul volume e fare `docker compose restart` della console.
Al contrario, il run dell'agente fallirebbe perché il motore nuovo scrive una colonna che ancora non c'è.
Conto e debito compaiono dal **primo run ufficiale successivo** all'aggiornamento.

## Verifica degli accordi (29/10)
Il corrente degli accordi non viene più tagliato. Nel cruscotto il riquadro **"Classe 0 e accordi onorati, dopo i
tagli"** dice se, tagliando tutto il tagliabile, stipendi, tributi e accordi stanno nel fido e, se no, da quale giorno
e di quanto. Nella pagina Accordi la colonna **"Corrente fino al primo scoperto"** indica quanto pesa ciascun accordo
fino a quel giorno. Per valutare un accordo: Simulazioni → stato accordo Rifiutato → confronto delle due righe
"Classe 0 e accordi onorati".

## Esercizio
- **Aggiornare la console**: aggiornare il pacchetto sul volume, poi `docker compose restart`
  (o `up -d --build` se è cambiato `console/requirements.txt`).
- **Aggiungere o togliere un utente**: modificare `CONSOLE_UTENTI` in `console.env`, poi `docker compose up -d`.
- **Simulazioni vecchie**: i file Excel restano in `<HERMES_DATA>/tesoreria/sim/<utente>/`; si possono cancellare
  quando non servono più (i dati restano nel database).
- **Log**: `docker compose logs --tail 200`.
