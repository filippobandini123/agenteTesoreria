# Tesoriere — istruzioni per chi sviluppa (persone e Claude)

Questo file si legge per primo, a ogni sessione. Dice cos'è il progetto, cosa è già deciso, dove gira e come si lavora.
Le decisioni di dettaglio stanno in `DOMANDE_APERTE.md` (decise e aperte) e nel documento di specifica
(Claude Docs: https://claude.ai/artifact/TWYxavQEP6xfekXCX1Pt8n).

## Cos'è
Agente di tesoreria per un'azienda retail in crisi (non in procedura). Ogni mattina, dopo il caricamento degli incassi,
riceve il *Report di cassa giornaliero* e propone il **riscadenziamento dei pagamenti fornitori** entro il fido,
secondo priorità e regole. Produce un Excel nel tracciato di import del gestionale, che una persona rivede e importa.
Attorno all'agente: un database Postgres (memoria, anagrafica, audit) e una console web per CFO e Specialist.

Responsabile del progetto: **Filippo**. Lingua di lavoro: **italiano**.

## Principi (non negoziabili)
- **I numeri li calcola solo il motore deterministico** (`riscadenzia.py` + `tesoriere/`). L'LLM orchestra, spiega,
  segnala; non stima e non ricalcola a mano.
- **Nessuna scrittura nel gestionale (ERP)** e **nessun contatto con fornitori, banche o terzi**. Si produce solo la proposta.
- **`config/regole.yaml` non lo modifica l'agente.** Le modifiche alle regole si propongono con una simulazione.
- **Anagrafica e accordi**: l'agente li modifica solo su richiesta esplicita di Filippo e dopo conferma; la console solo
  con anteprima, motivo e conferma. Sempre a versioni (valido_dal / valido_al) con audit. Mai UPDATE in place.
- **Segreti solo in variabili d'ambiente / file `.env` fuori dal repository.**

## Mappa del repository
| Percorso | Cosa |
|---|---|
| `SOUL.md` | Identità e limiti dell'agente (va nel profilo Hermes) |
| `description.md` | Descrizione del profilo Hermes |
| `skills/riscadenziamento-pagamenti/SKILL.md` | Procedura del run, risposte, simulazioni, modifiche all'anagrafica |
| `skills/…/scripts/riscadenzia.py` | Motore, punto d'ingresso (codici di uscita 0 ok · 1 errore · 2 controlli KO) |
| `skills/…/scripts/anagrafica.py` | CLI anagrafica e accordi su Postgres (anteprima → `--conferma`) |
| `skills/…/scripts/tesoriere/` | Moduli: `carica` · `controlli` · `classifica` · `alloca` (cuore) · `output` · `db` · `anagrafica_db` · `modello` · `calendario` |
| `skills/…/scripts/tests/` | Test pytest del motore |
| `config/regole.yaml` | Regole (versionate: campo `versione`) |
| `config/fonti_dati.yaml` | Mappatura del Report, controlli, tracciato di import, DB |
| `console/` | Console web (FastAPI + pagine HTML, Chart.js locale); `console/docker/` Dockerfile, compose, permessi SQL |
| `db/schema.sql`, `db/migrazione_*.sql` | Schema Postgres `tesoreria` e migrazioni |
| `INSTALL.md`, `CONSOLE.md`, `runbook.md` | Installazione agente, console, esercizio quotidiano |
| `DOMANDE_APERTE.md` | Registro delle decisioni prese e dei punti aperti |
| `CHANGELOG.md` | Versioni del pacchetto e ordine degli aggiornamenti |

## Regole di dominio già decise (sintesi)
- **Partita** = una scadenza del gestionale, chiave `numero_protocollo_effetto` → colonna **Protocollo** del tracciato.
- `ScadOriginale` = scadenza da documento; `dat_scadenza` = scadenza variata internamente (quella che si sposta).
- **Classi**: 0 intoccabile (stipendi, tributi, F24, BLOC, TC, PDR, classe 0 da anagrafica) · 1 da tutelare
  (elenco acquisto, classe 1 da anagrafica, corrente degli accordi) · 2–5 spostabili · default 3.
  Precedenza: BLOC > regole automatiche > accordo > anagrafica > default.
- **Vincolo**: saldo + fido (1.000.000) − cuscinetto (0) ≥ 0. Look-ahead 60 giorni. Rate intere, niente parziali.
- **Piano in due fasi**: (1) piano da regole: protetti alle loro date, tutelati alla scadenza da documento senza guardare
  la cassa → scoperto "vero"; (2) piano di taglio: dove si sfora, si tagliano i tutelati di classe 1 in ordine di
  **rilevanza** (3 → 2 → 1), poi classe, poi scadenza più recente; ricollocati al primo giorno utile, altrimenti a fine orizzonte.
- **Ordine di priorità** (31/10): 1) classe 0 · 2) accordi · 3) classe 1. **Il corrente degli accordi non si taglia**:
  se non basta la cassa, lo scoperto resta visibile nel suo giorno (verifica "classe 0 e accordi onorati, dopo i tagli").
- **Accordi**: dalla decorrenza lo scaduto è congelato (pagamento ipotizzato 31/01/2027, fisso quando entra
  nell'orizzonte), il corrente si paga alla scadenza. Stati: Attivo e Proposta valgono uguale, Rifiutato = ignorato.
- **Note d'import**: l'import SOSTITUISCE la nota (varchar 240). Nota compatta: via le ipotesi HP, solo l'ultima
  TC/TIC/SOL, testo libero conservato, in coda `HPAG(gg/mm):gg/mm(AG)C<classe>-<origine>`. Oltre 240 caratteri → a mano.
- **Tracciato di import**: Numdoc (= `num_protocollo`; se 0, `des_num_doc`) · Sezdoc "00" testo · Datadoc · Datascad_vecchia ·
  TipoCF 1 · Protocollo · Data_Scanuova · Note. Le note di credito seguono il pagamento del fornitore e sono nell'import.
- Allerta: soglia di margine 100.000 €; livelli verde / giallo / rosso / rosso prioritario.

## Ambiente di produzione
- **Server sempre acceso**, raggiunto via **VPN** con indirizzo IP. Docker.
- **Hermes** nel container `hermes` (immagine `nousresearch/hermes-agent`, utente UID 10000). Volume dell'host montato
  su **`/opt/data`**: pacchetto in `/opt/data/tesoriere` (clone di questo repository), dati in `/opt/data/tesoreria/{in,out,archivio,sim}`,
  profilo in `/opt/data/profiles/tesoriere` (SOUL.md, `.env`, skill collegata con link simbolico).
- **Canale**: Telegram. Il Report arriva come allegato; la proposta torna come allegato (`MEDIA:<percorso>`).
- **Postgres**: schema `tesoreria` nello stesso database del gestionale (tabella utenti `public.utente`, password MD5).
- **Console**: container `tesoreria-console`, porta 8080, dalla VPN; monta solo `tesoriere` (sola lettura) e `tesoreria`.
- Filippo lavora da **Windows + PowerShell**: comandi su una riga o con il backtick (mai `\`), percorsi host Windows,
  percorsi container Linux. Nel container Hermes non ci sono `nano` né `uv`. File `.env` in UTF-8 senza BOM, fine riga LF.

## Come si lavora
1. **Domande prima di decidere** quando qualcosa non è chiaro o un'interpretazione cambia i numeri. Filippo preferisce
   essere interpellato; poche domande, mirate, con una proposta di default.
2. **Un branch per modifica**, commit con messaggi chiari in italiano, pull request; il merge lo fa Filippo.
   Niente commit o push senza che lo chieda.
3. **Test**: `cd skills/riscadenziamento-pagamenti/scripts && python -m pytest -q tests` deve passare.
   Test nuovi **solo su richiesta** (indicazione di Filippo, valida fino a nuovo avviso).
4. **Verifica sui dati reali** prima di consegnare una modifica al motore: run su un Report reale (Filippo lo fornisce;
   i Report non stanno nel repository) e confronto dei numeri chiave con il run precedente.
5. **Console**: verificare le pagine toccate in un browser (login, anteprima, conferma, registro).
6. **Ogni modifica di comportamento** va registrata in `DOMANDE_APERTE.md` (sezione "Decise") e in `CHANGELOG.md`;
   se cambia una regola, anche nel documento di specifica.
7. **Database**: ogni cambio di schema = nuovo file `db/migrazione_AAAA-MM-GG[_tema].sql` idempotente
   (`ADD COLUMN IF NOT EXISTS`), più l'aggiornamento di `db/schema.sql` e dei permessi in `console/docker/permessi.sql`.
   In `CHANGELOG.md` va scritto se la migrazione deve precedere il codice.

## Aggiornamento in produzione
1. Se `CHANGELOG.md` lo indica: eseguire prima la migrazione del database.
2. Nel volume: `git pull` in `/opt/data/tesoriere` (dall'host: nella cartella montata).
3. `docker exec hermes chown -R 10000:10000 /opt/data/tesoriere` se i file sono stati scritti da un altro utente.
4. Console: `docker compose restart` (o `up -d --build` se è cambiato `console/requirements.txt`).
5. Agente: la skill è collegata, il codice nuovo vale dal run successivo.

## Cosa non va nel repository
Report, proposte, simulazioni, anagrafica reale (`config/anagrafica_fornitori.xlsx`: serviva solo al caricamento
iniziale, la fonte è Postgres), file `.env` e `console.env`, virtualenv. Vedi `.gitignore`.
