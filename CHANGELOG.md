# Changelog

Versioni del pacchetto Tesoriere (agente, motore, console). Per ogni versione: cosa cambia e, se serve, l'ordine
dell'aggiornamento. **⚠ migrazione prima** = eseguire la migrazione del database *prima* di aggiornare il codice.

## 0.6.1 — 31/10/2026
- Decisione: ordine di priorità 1) classe 0 · 2) accordi · 3) classe 1. Se anche tagliando tutta la classe 1 la cassa non
  basta, gli accordi non si tagliano e lo scoperto resta visibile nel suo giorno. Nessuna modifica al codice.
- Aggiunti `CLAUDE.md`, `CHANGELOG.md`, `.gitignore`: il progetto passa su repository Git.

## 0.6.0 — 29/10/2026 — Console Tesoreria
- **Console web** (`console/`, `CONSOLE.md`): cruscotto, fornitori, accordi, simulazioni, registro. Login con le utenze
  del gestionale limitato a `CONSOLE_UTENTI`; modifiche con anteprima, motivo, storico e audit.
- Fornitori: conto prevalente, debito totale, scaduto, filtro per conto; fornitori dello scadenzario non in anagrafica
  mostrati in coda e aggiungibili.
- **Il corrente degli accordi non si taglia** (`taglio.accordi: false`); nuova verifica "classe 0 e accordi onorati,
  dopo i tagli" (Excel, JSON, console, messaggio dell'agente).
- Motore: `--autore` sul run, override `stato_accordo:<cod>=…` per le simulazioni, conto contabile salvato su ogni riga.
- Database: `migrazione_2026-10-29.sql` (`tes_run.avviato_da`), **⚠ migrazione prima** `migrazione_2026-10-29_conto.sql`
  (`tes_proposta_riga.conto`); nuovo utente `tesoreria_console` (`console/docker/permessi.sql`).

## 0.5.0 — 28/10/2026 — Due fasi, Telegram, anagrafica su Postgres
- **Piano in due fasi**: piano da regole + piano di taglio per rilevanza (`piano.modalita: due_fasi`, `taglio`).
  Nuova colonna `rilevanza` (1 strategico · 2 normale · 3 sacrificabile).
- **Report via allegato Telegram** (`previsione.modalita: allegato`), archivio dei Report con impronta, avviso se arriva
  due volte lo stesso file; proposta restituita in allegato.
- **Anagrafica e accordi su Postgres** come fonte unica, a versioni con audit; `anagrafica.py` (anteprima → conferma).
  Database: `migrazione_2026-10-28.sql`.
- Installazione su Hermes in Docker (`INSTALL.md`, comandi PowerShell, `TESORIERE_HOME`).
- Correzioni: Numdoc vuoto quando il protocollo era intero numpy; Numdoc = numero documento quando il protocollo è 0.

## 0.4.0 — 27/10/2026
- Tutela compatibile con la cassa (poi assorbita dalle due fasi).
- Note di credito incluse nel tracciato di import.

## 0.3.0 — 26/10/2026
- Accordi con i fornitori (scaduto congelato, corrente protetto; stati Attivo / Proposta / Rifiutato).
- Scadenze bloccate `BLOC`, protocollo della scadenza come chiave di import, nota compatta (varchar 240),
  codici di origine della classe nella nota, scaduto congelato al 31/01/2027.
- Look-ahead 60 giorni, allerta con soglia di margine 100.000 €.

## 0.2.0 — 25/10/2026
- Niente pagamenti parziali (rate intere), classe 1 alla scadenza corrente, parcheggio a fine orizzonte.

## 0.1.0 — 24/10/2026
- Primo motore deterministico, configurazione, schema del database, SOUL e skill dell'agente.
