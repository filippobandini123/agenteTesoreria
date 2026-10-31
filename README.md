# Tesoriere — agente di riscadenziamento pagamenti (profilo Hermes)

## 1. Architettura in breve
```
 LLM (Hermes, profilo "tesoriere")       orchestrazione, spiegazione, messaggi
        │  esegue / legge JSON
        ▼
 riscadenzia.py (Python, deterministico) calcolo: saldo, capacità, allocazione, fabbisogno
        │  legge                       │  scrive
        ▼                              ▼
 config/*.yaml + Postgres (anagrafica, accordi)   Postgres (memoria + audit) · Excel proposta

 Canale: Telegram. Il Report arriva come allegato, la proposta torna come allegato.
```
Il modello linguistico non tocca mai i numeri. Tutte le regole sono nei file di
configurazione, quindi si possono simulare e testare anche senza l'agente.

## 2. Contenuto
| File | Ruolo |
|---|---|
| `SOUL.md` | Identità, perimetro e limiti dell'agente (va nella home del profilo) |
| `skills/riscadenziamento-pagamenti/SKILL.md` | Procedura del run, risposte a domande, simulazioni |
| `config/regole.yaml` | Orizzonte, vincolo di cassa, classi, algoritmo di allocazione, fabbisogno |
| `config/fonti_dati.yaml` | Mappatura del file Excel, controlli di input, output, DB |
| `config/anagrafica_fornitori.xlsx` | Solo per il caricamento iniziale su Postgres (263 fornitori, accordi): dal 28/10 la fonte è il database |
| `INSTALL.md` | Flusso di installazione su Hermes, fase per fase |
| `CONSOLE.md` | Console web (CFO, Specialist): anagrafica, accordi, consultazione, simulazioni; installazione |
| `console/` | Codice della console (FastAPI + pagine HTML) e file Docker (`console/docker/`) |
| `description.md` | Descrizione del profilo per Hermes (`--description`) |
| `db/migrazione_2026-10-28.sql` | Colonne aggiunte il 28/10 (rilevanza, autore e motivo delle modifiche) per un DB già creato |
| `skills/…/scripts/anagrafica.py` | Consultazione e modifica di anagrafica e accordi su Postgres (anteprima → conferma) |
| `db/schema.sql` | Tabelle e viste per memoria, storico slittamenti e audit |
| `runbook.md` | Orari, flusso, escalation, ripartenza, modifica regole |
| `test/scenari.md` | Scenari sintetici più il test di accettazione sul file reale |
| `DOMANDE_APERTE.md` | Decisioni ancora da prendere (i default sono già impostati) |
| `skills/…/scripts/riscadenzia.py` | Il motore: punto d'ingresso da riga di comando |
| `skills/…/scripts/tesoriere/` | Moduli del motore (vedi sotto) |
| `skills/…/scripts/tests/test_scenari.py` | Test automatici: `python -m pytest -q tests` |

### Moduli del motore
| Modulo | Cosa fa |
|---|---|
| `modello.py` | Strutture dati (Partita, Fornitore, Previsione, Risultato): nessuna dipendenza da Excel o DB |
| `calendario.py` | Giorni lavorativi bancari italiani + chiusure extra |
| `carica.py` | Legge Report, scadenzario, scaduti e anagrafica; aggrega le righe in **partite** |
| `controlli.py` | Controlli di input (freschezza, consuntivo, orizzonte, chiavi, cache, totali) |
| `classifica.py` | Classe di priorità: regole automatiche > anagrafica > default |
| `alloca.py` | Il cuore: protetti, look-ahead, pro quota a rate intere, forzati, parcheggio, note di credito |
| `output.py` | Excel di proposta (8 fogli) e JSON di sintesi per l'agente |
| `db.py` | Scrittura su Postgres in un'unica transazione |
| `anagrafica_db.py` | Anagrafica e accordi su Postgres: lettura alla data del run, modifiche versionate con audit, import/export Excel |

**Accordi** (foglio *Accordi* di `anagrafica_fornitori.xlsx`): per ogni fornitore con
accordo di congelamento, lo scaduto (prima della decorrenza) è escluso dal piano e il
corrente si paga alla scadenza. Precedenza: tributi, TC e piani di rientro > accordo >
anagrafica > default. Il foglio *Accordi* della proposta mostra, per ogni accordo, lo
scaduto congelato, il corrente nell'orizzonte e l'eventuale corrente già in ritardo
(campanello d'allarme sul rispetto dell'accordo).

**Partita** = una scadenza del gestionale, identificata dal campo **Protocollo** (chiave
univoca della scadenza) quando l'export dello scadenzario lo contiene. Senza quel campo il
motore ripiega su documento + scadenza attuale (rate uguali si spostano insieme) e il
controllo `chiave_scadenza` avvisa che `Import_ERP` non è importabile.

**Tracciato di import** (confermato il 26/10): Numdoc · Sezdoc "00" · Datadoc ·
Datascad_vecchia · TipoCF 1 · Protocollo · Data_Scanuova · Note. La nota **sostituisce**
quella del gestionale: si manda la nota compatta (max 240 caratteri) che termina con
`HPAG(gg/mm):gg/mm(AG)C<classe>-<origine>`.

Uso:
```bash
cd skills/riscadenziamento-pagamenti/scripts
pip install -r requirements.txt
python riscadenzia.py --regole ../../../config/regole.yaml --fonti ../../../config/fonti_dati.yaml \
  --input /opt/data/tesoreria/in --out /opt/data/tesoreria/out          # anagrafica letta da Postgres
# offline, con l'anagrafica in Excel: --anagrafica ../../../config/anagrafica_fornitori.xlsx --no-db
# simulazione (non pubblicata, marcata a DB):
python riscadenzia.py ... --override lookahead=7 --override elenco:1280=S --override fido=1500000
# senza database: --no-db    ·    data del run diversa da oggi: --oggi 2026-10-23
```

## 3. Installazione nel profilo Hermes
Procedura completa, con le verifiche di ogni fase: **`INSTALL.md`**.

## 4. Arrivo del Report: allegato Telegram (deciso 28/10)
Il Report si manda all'agente come allegato nella chat Telegram. L'agente lo salva in
`/opt/data/tesoreria/in`, lancia il run e risponde con il messaggio di sintesi e la proposta in allegato.
Ogni Report del run ufficiale viene archiviato in `/opt/data/tesoreria/archivio` con data e
impronta nel nome; se arriva due volte lo stesso file il motore avvisa.
Limite Telegram per i file scaricati da un bot: 20 MB (il Report pesa circa 4 MB).
Resta valida la condizione: il file va **salvato da Excel dopo il refresh** delle query.

### Alternativa: OneDrive (`previsione.modalita: cartella`)
Ci sono due strade.
- **rclone (consigliata).** Si configura un remote `onedrive` e si dà accesso solo a una
  cartella dedicata, ad esempio `Tesoreria/Agente/in` e `/out`. Il trasferimento è
  deterministico, scriptabile e funziona uguale anche sul server always-on. Il token OAuth
  va nel volume di configurazione, non nell'immagine.
- **ms-365-mcp-server**, che è già nel container. Funziona, ma passa dall'LLM per
  scaricare e caricare i file: va bene per gli usi interattivi, meno per un run schedulato.

Per entrambe vale una condizione: il file va **salvato da Excel dopo il refresh** delle
query. Il motore legge i valori in cache del foglio *Report* (entrate previste, uscite da
budget), perché non può ricalcolare le formule.

## 5. Cosa emerge dal file allegato (24/10/2026)
- **Perimetro spostabile.** In *Report* le uscite fornitori (RID, RIBA, bonifico, scaduto)
  sono calcolate dalla tabella `Scadenzario_Attivo_Passivo`, con la data di pagamento
  `dat_scadenza`. Stipendi, INPS/IRPEF, IVA, affitti e finanziamenti invece vengono da
  *BudgetUscite*. Il motore quindi sposta solo righe dello scadenzario, e le uscite da
  budget restano fisse per costruzione. Questo è coerente con "stipendi e F24 intoccabili".
- **Convenzioni esistenti riusate.** La riga riscadenziata diventa `BO` nella previsione,
  le note amministrative seguono il formato `HPEI(gg/mm):gg/mm(sigla)` e le sigle sono
  TIC, TC e SOL. Il blocco "RISCADENZIAMENTI" sembra il tracciato di import (vedi domande).
- **Riconciliazione.** Il saldo ricalcolato dal motore coincide al centesimo con la riga
  del saldo del Report su tutti i 60 giorni. Il controllo `cache_coerente` resta come
  protezione per i file salvati senza ricalcolo.
- **Data congelata 13/11.** 246 partite (1,43 M€, 90 fornitori, scadenze originali
  mar–ott) sono parcheggiate al 13/11 e compensate in *BudgetUscite → Altro*
  (+1.427.027 €). Per questo il "TOTALE USCITE" di quel giorno vale solo −7 k€. Il motore
  le tratta come **congelate**: non le sposta e toglie la compensazione dai flussi, perché
  altrimenti diventerebbe cassa fittizia (`regole.yaml → date_congelate`).
- **Primo run (23/10, anagrafica vuota, tutti in classe 3).** Il picco di fabbisogno è
  di circa 5,9 M€ al 21/12. Le sole uscite fisse da budget sforano il fido di circa
  0,7 M€, con primo scoperto il 9/11 (stipendi). 1.151 partite (5,8 M€) risultano non
  collocabili. Con il look-ahead sull'intero orizzonte **nessun pagamento spostabile
  viene eseguito**, perché la cassa di oggi serve agli stipendi futuri già scoperti.
  Vedi `DOMANDE_APERTE.md`, domanda 2.
- **Scaduti.** Il foglio *Scaduti* ha date passate e non entra nella previsione futura.
  Il motore lo mette in coda dal primo giorno, quindi il saldo dopo il riscadenziamento
  può risultare leggermente inferiore a quello del file Excel.
- **Note di credito.** Compensano solo se nell'orizzonte c'è un pagamento dello stesso
  fornitore. Altrimenti non hanno effetto di cassa.
