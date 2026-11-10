---
name: riscadenziamento-pagamenti
description: Run giornaliero di riscadenziamento dei pagamenti fornitori sulla previsione di tesoreria; usare per il run del mattino, per rispondere a domande su una proposta, per consultare o modificare anagrafica e accordi, per simulare l'effetto di una modifica e per gli export per conto contabile.
version: 0.6.2
---

# Riscadenziamento pagamenti

Questa skill è versionata nel repository GitHub del progetto e arriva nel profilo con un collegamento.
**Non modificarla e non crearne copie nel profilo**: se una regola ti sembra mancare, proponila a Filippo.

## Quando usarla
- Arriva su Telegram da Filippo un allegato `Report_di_cassa_giornaliero*.xlsx` (anche senza testo) → run ufficiale del giorno.
- Richiesta esplicita "rifai il run".
- Domande su una proposta già prodotta ("perché…", "quanto…", "chi…"), sui saldi, su fornitori e accordi.
- Richieste di modificare anagrafica fornitori e accordi.
- Simulazioni "cosa succede se…" ed export Excel per conto contabile.

## Percorsi (solo questi)
| Cosa | Percorso |
|---|---|
| Pacchetto (motore, config, venv) | `$TESORIERE_HOME` = `/opt/data/tesoriere` |
| Report ricevuti | `/opt/data/tesoreria/in` |
| Proposte e JSON del run | `/opt/data/tesoreria/out` |
| Archivio dei Report | `/opt/data/tesoreria/archivio` |
| Simulazioni ed export | `/opt/data/tesoreria/sim` |

Non esistono altri percorsi di lavoro (in particolare **non esiste `/data`**). Usa sempre percorsi assoluti.
Abbreviazioni: `PY=/opt/data/tesoriere/.venv/bin/python` · `S=/opt/data/tesoriere/skills/riscadenziamento-pagamenti/scripts` ·
`C=/opt/data/tesoriere/config`.

Prima di ogni comando che usa il database carica il `.env` del profilo nella stessa shell, senza stamparlo
(non usare `source` sul file: un valore con spazi lo spezzerebbe):
```bash
eval "$(bash $S/prepara_ambiente.sh --env)"
```

## Procedura del run giornaliero

### 1. Verifica dell'ambiente (sempre, prima del run)
```bash
bash $S/prepara_ambiente.sh
```
- `AMBIENTE OK` (uscita 0) → prosegui.
- `KO python` → lancia **una volta** `bash $S/prepara_ambiente.sh --installa` e rifai la verifica.
- Qualsiasi KO che resta (Python, database, cartelle, config) → **fermati**. Scrivi a Filippo cosa è KO e cosa serve.
  **Mai** ripiegare sul Python di sistema, su librerie trovate altrove o su cartelle temporanee: una proposta fatta
  così non è valida.
- Database non raggiungibile = server spento, VPN o rete assente: senza anagrafica il run non è valido e non si
  sostituisce con stime.

### 2. Acquisizione del Report
- Accetta il file solo da Filippo e solo se è un `Report_di_cassa_giornaliero*.xlsx`. Un altro Excel → chiedi cos'è.
- Copia l'allegato (dal percorso in cui il gateway lo ha salvato) in `/opt/data/tesoreria/in/` **con il suo nome**,
  sovrascrivendo una copia omonima. Togli dalla cartella i Report dei giorni precedenti (l'archivio li conserva).
- Rispondi subito con una riga: "Report ricevuto, lancio il run."

### 3. Esecuzione del motore (run ufficiale, non simulazione)
Passa il file **esplicitamente** con `--input`: così il run non dipende dal nome dell'allegato né da file rimasti
in cartella.
```bash
$PY $S/riscadenzia.py \
  --regole $C/regole.yaml \
  --fonti $C/fonti_dati.yaml \
  --input "/opt/data/tesoreria/in/<nome del file>.xlsx" \
  --out /opt/data/tesoreria/out \
  --json /opt/data/tesoreria/out/ultimo_run.json
echo "uscita=$?"
```

### 4. Decidere dall'esito
Decidi da `esito` in `ultimo_run.json` **e** dal codice di uscita; se discordano vale il caso peggiore.
- `esito: "ok"`, uscita 0 e `file_proposta` valorizzato → proposta pubblicabile.
- `esito: "controlli_ko"` (uscita 2) → **fermati**: indica il controllo fallito e cosa serve per ripartire.
  Nessun allegato, nessun numero preso da run precedenti.
- Uscita 1 / `errore` → errore tecnico: riporta il messaggio a Filippo, non riprovare più di una volta,
  non sostituirlo con dati plausibili.
- `file_gia_elaborato` (avviso): chiedi se il Report è stato aggiornato. Se Filippo ha chiesto il rilancio dopo
  modifiche ad anagrafica o accordi, è un avviso non bloccante: dillo nella sintesi.

### 5. Lettura degli esiti
Leggi **solo** `ultimo_run.json`. Prima i campi critici: `allerta`, `fabbisogno`, `piano_taglio` (compreso
`dopo_tagli`), accordi con corrente in ritardo, controlli. Poi `spostamenti`, `azioni_operative` (RiBa da richiamare,
RID da revocare), avvisi.

### 6. Pubblicazione
Rispondi su Telegram con il messaggio di sintesi (punto 7) e allega `file_proposta` aggiungendo in fondo alla risposta
la riga `MEDIA:<percorso assoluto di file_proposta>` (il gateway la trasforma in allegato). Con un KO niente allegato.

### 7. Messaggio di sintesi (modello, uno schermo di telefono)
```
{semaforo} Tesoreria {data} — run {run_id}
Margine minimo: {allerta.margine_minimo} il {allerta.data_margine_minimo} (soglia {allerta.soglia_margine})
Sotto soglia dal {allerta.primo_giorno_sotto_soglia} · fido sforato dal {allerta.primo_giorno_oltre_fido}
Fabbisogno: {picco} € dal {primo_giorno_scoperto}  [classe 0: {q0} · classe 1: {q1}]
Spostati: {n_righe} pagamenti, {importo} €, {n_fornitori} fornitori
Da fare in banca: {n_riba} RiBa, {n_rid} RID
Anomalie: {elenco controlli in avviso, o "nessuna"}
```
In testa, quando ci sono: accordi non sostenibili (vedi sotto), fornitori strategici tagliati, corrente di accordi in
ritardo, quota classe 0 scoperta. Al massimo 3 righe di commento su cosa è cambiato rispetto a ieri
(`tes_v_delta_run`), solo se rilevante. Formato europeo, date gg/mm/aaaa.

### 8. Registrazione
Il motore scrive già run, righe e saldi a DB. Tu aggiungi in `tes_audit` l'evento `messaggio_inviato` con il testo
inviato, poi rileggi la riga per verificare che sia stata scritta.

## Controlli bloccanti e rimedi
| Sintomo | Causa probabile | Azione |
|---|---|---|
| `data_consuntivo_recente` KO | incassi non ancora caricati | attendi un Report aggiornato |
| `orizzonte_coperto` KO | il Report finisce prima della fine dell'orizzonte | chiedi a Filippo un Report esteso almeno alla data indicata. **Non** aggirarlo con `--override orizzonte=N`: trasforma il run in simulazione e non produce una proposta ufficiale |
| `chiavi_univoche` / `chiave_scadenza` KO | export del gestionale duplicato o incompleto | fermati, segnala le chiavi |
| `cache_coerente` in avviso | Report salvato senza ricalcolo in Excel | segnala; la proposta resta valida |
| quota classe 0 > 0 | anche stipendi/tributi non stanno nel fido | 🔴 in testa al messaggio |
| uscita 1 | errore tecnico | riporta `errore` dal JSON, un solo nuovo tentativo |

## Rispondere a domande
- **"Perché X è slittato?"** → `tes_proposta_riga` del run: `regola`, `motivo`, `classe`. Cita la regola in parole semplici.
- **"Quanto abbiamo slittato su Y?"** → vista `tes_v_slittamento_fornitore`.
- **Saldo rilevato** → dal Report: la data del consuntivo è l'ultimo giorno chiuso, il saldo è la riga configurata in
  `fonti_dati.yaml` (`report.righe.saldo_finale`).
- **Saldo prospettico** → foglio `Saldo_giornaliero` della proposta (o simulazione): `Saldo post proposta` e
  `Margine post`, sempre con la data.
- **Mai** ricalcolare a mente: per un calcolo nuovo usa una query o una simulazione.

## Anagrafica fornitori e accordi (Postgres)
Script: `$PY $S/anagrafica.py …`. Uscita 0 scritto · 3 solo anteprima · 1 errore. La fonte è il database, non l'Excel.

**Consultare** (libero): `cerca "<nome>"` · `mostra <cod>` · `storico <cod>` · `accordi [--stato proposta]` ·
`esporta --a /opt/data/tesoreria/sim/anagrafica_AAAAMMGG.xlsx` (poi allegala).

**Modificare** (solo se lo chiede Filippo):
1. Individua il fornitore con `cerca`. Più risultati → chiedi quale.
2. Lancia **senza** `--conferma`:
   `modifica <cod> --set rilevanza=3 --autore "Filippo (Telegram)" --motivo "<parole di Filippo>"`
   oppure `accordo <cod> --decorrenza AAAA-MM-GG --set stato=Rifiutato --autore … --motivo …`
3. Mostra a Filippo solo i campi in `cambiati`, prima → dopo, e chiedi "Confermi?".
4. Solo dopo un sì esplicito rilancia lo stesso comando con `--conferma`, poi verifica con `mostra`. Rispondi "Fatto".
5. Se la modifica cambia la proposta di oggi, proponi di rifare il run (non rifarlo da solo).

Campi fornitore: `in_elenco_acquisto` (S/N) · `classe_priorita` (0-5) · `rilevanza` (1-3) · `scaduto_max_tollerato` ·
`slittamento_max_gg` · `pagamento_minimo` · `note` · `ragione_sociale`; `vuoto` cancella il valore; `--dal AAAA-MM-GG`
per una decorrenza diversa da oggi. Campi accordo: `stato` (Attivo/Proposta/Rifiutato) · `criterio` · `valido_fino` ·
`data_pagamento_scaduto` · `note`; nuovo accordo: `--nuovo --decorrenza … --set ragione_sociale=…`.
Mai UPDATE manuali in SQL: solo tramite lo script.

Anagrafica e accordi si modificano anche dalla **Console Tesoreria** (CFO, Specialist): autore `… (console)`, motivo in
`tes_audit`. "Chi ha cambiato X?" → `tes_audit` (eventi `anagrafica_modificata`, `accordo_modificato`). Le simulazioni
della console sono run `simulazione` con `avviato_da` valorizzato: non confonderle con il piano ufficiale.

## Simulazioni
```bash
$PY $S/riscadenzia.py --regole $C/regole.yaml --fonti $C/fonti_dati.yaml \
  --input "/opt/data/tesoreria/in/<Report>.xlsx" --out /opt/data/tesoreria/sim \
  --json /opt/data/tesoreria/sim/sim.json --simula --override classe:1207=2
```
Override (ripetibili): `classe:<cod>=<0-5>` · `elenco:<cod>=S|N` · `rilevanza:<cod>=<1-3>` ·
`slittamento_max:<cod>=<gg>` · `scaduto_max:<cod>=<€>` · `stato_accordo:<cod>=Attivo|Proposta|Rifiutato` ·
`fido=<€>` · `cuscinetto=<€>` · `orizzonte=<giorni>` · `lookahead=<gg>|orizzonte` · `allerta=<€>` · `piano=due_fasi|cassa`.
- Output sempre in `/opt/data/tesoreria/sim`; mai sovrascrivere la proposta ufficiale. Il file `SIM_…` si allega solo se
  Filippo lo chiede.
- **Verifica fino a una data**: calcola i giorni dalla data del run e usa `--override orizzonte=<giorni>`; controlla nel
  JSON che `orizzonte.ultimo_pagamento` sia la data voluta.
- **Apporto di cassa in una data precisa**: chiedi a Filippo una copia del Report con l'apporto già inserito e passala
  con `--input`. **Non** simularlo alzando il fido: cambierebbe anche i giorni precedenti.
- Confronto con il run ufficiale solo dai JSON: fabbisogno, primo giorno oltre il fido, quote classe 0/1, verifica degli
  accordi (`piano_taglio.dopo_tagli`), spostamenti, non collocabili, fornitori strategici tagliati.

## Export Excel per conto contabile
Quando Filippo chiede i fornitori di un conto (es. «Fornitori Farmaco e Parafarmaco»):
```bash
$PY $S/export_fornitori_conto.py --elenco-conti
$PY $S/export_fornitori_conto.py --conto "Fornitori Farmaco e Parafarmaco" \
  --out /opt/data/tesoreria/sim/Fornitori_Farmaco_AAAAMMGG.xlsx
```
Legge l'ultimo run ufficiale dal database (`--run-id` per un altro) e usa **le stesse definizioni della pagina Fornitori
della console** (spiegate nel foglio `Note`): debito, scaduto, nell'orizzonte, tagliato, parcheggiato, conto prevalente
per importo. Le differenze con le schermate del gestionale (stato importato contro stato calcolato) vanno dichiarate nel
messaggio, non nascoste. Allega il file con `MEDIA:`.

## Regole dell'output da conoscere per spiegare
- **Partita** = documento + scadenza attuale: rate con la stessa scadenza si spostano insieme.
- **Ordine di priorità**: classe 0 → accordi → classe 1 (in classe 1 per rilevanza 3 → 2 → 1).
- **Piano in due fasi** (`piano_taglio`):
  - `tutela_scad_documento` = tutelato pagato alla scadenza da documento (piano da regole);
  - `tagliata` = tolta dal piano da regole perché faceva sforare il fido, ricollocata al primo giorno utile;
  - `tagliata_non_collocabile` = tagliata e parcheggiata a fine orizzonte (genera fabbisogno);
  - nel messaggio riporta `scoperto_piano_regole` e i tagli per rilevanza; se `allerta.fornitori_strategici_tagliati`
    non è vuoto, segnalalo in testa.
- **Il corrente degli accordi non si taglia.** Se `allerta.accordi_non_sostenibili_dal` è valorizzato, scrivi in testa:
  "Accordi non sostenibili dal <data>: scoperto <scoperto_dopo_tagli> € anche tagliando tutto il tagliabile" e indica i 3
  accordi con `corrente_fino_primo_scoperto` più alto (da `accordi[]`).
- `protetta_c1` con origine `accordo:corrente` = corrente di un accordo. Se `accordi[].corrente_gia_scaduto_n > 0`
  c'è corrente già in ritardo: **in testa al messaggio** (rischio di decadenza dell'accordo).
- `congelata_accordo` = scaduto di un fornitore con accordo, al 31/01/2027 (origine ACCS), fisso quando entra nell'orizzonte.
- Accordi in stato **Proposta** valgono come Attivo; **Rifiutato** = ignorato. Avvisi `accordi_in_scadenza` /
  `accordi_non_attivi`: riportali.
- Per fissare la data di un tagliato: sigla BLOC nella nota e nuova `dat_scadenza` nel gestionale.
- La nota d'import è la **nota compatta** (sostituisce quella del gestionale, max 240 caratteri) e finisce con
  `C<classe>-<origine>` (BLOC, TRIB, TC, PDR, ACC, ACCS, ELENCO, ANAG, DEF). La nota precedente è in
  `tes_proposta_riga.nota_precedente`.
- Il semaforo viene da `allerta.livello` (verde / giallo / rosso / rosso_prioritario): non ricalcolarlo.
- `non_collocabile` = parcheggiata all'ultimo giorno dell'orizzonte: debito che genera fabbisogno, non un pagamento
  programmato. `non_collocabile_invariata` = già oltre l'orizzonte, resta dov'è.
- Le note di credito seguono il primo pagamento del fornitore e sono nel tracciato di import.
- Le partite non importabili (protocollo mancante) compaiono in *Azioni_operative* come "a mano".
