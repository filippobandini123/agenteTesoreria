---
name: riscadenziamento-pagamenti
description: Run giornaliero di riscadenziamento dei pagamenti fornitori sulla previsione di tesoreria; usare per il run del mattino, per rispondere a domande su una proposta o per simulare l'effetto di una modifica alle regole.
version: 0.1.0
---

# Riscadenziamento pagamenti

## Quando usarla
- Arriva su Telegram un allegato `Report_di_cassa_giornaliero*.xlsx` → run ufficiale del giorno.
- Richiesta esplicita "rifai il run".
- Richieste di consultare o modificare anagrafica fornitori e accordi.
- Domande su una proposta già prodotta ("perché…", "quanto…", "chi…").
- Simulazioni "cosa succede se…".

## Percorsi
`$TESORIERE_HOME` (variabile nel `.env` del profilo) è la cartella del pacchetto: contiene `config/`, `db/`
e il virtualenv `.venv`. Abbreviazioni usate sotto:
`PY=$TESORIERE_HOME/.venv/bin/python` · `S=$TESORIERE_HOME/skills/riscadenziamento-pagamenti/scripts` · `C=$TESORIERE_HOME/config`

## Procedura del run giornaliero

### 1. Acquisizione input (allegato Telegram)
- Accetta il file solo da Filippo e solo se il nome corrisponde a `Report_di_cassa_giornaliero*.xlsx`.
  Un altro Excel → chiedi cosa è prima di fare qualsiasi cosa.
- Copia il file ricevuto (dal percorso in cui il gateway lo ha salvato) **con il suo nome** in `/opt/data/tesoreria/in` (svuota prima la cartella dai Report precedenti:
  l'archivio li conserva).
- Rispondi subito con una riga: "Report ricevuto, lancio il run."
- Se il motore segnala `file_gia_elaborato`, chiedi se il Report è stato aggiornato prima di proseguire.

### 2. Esecuzione del motore
```bash
$PY $S/riscadenzia.py \
  --regole $C/regole.yaml \
  --fonti $C/fonti_dati.yaml \
  --input /opt/data/tesoreria/in \
  --out /opt/data/tesoreria/out \
  --json /opt/data/tesoreria/out/ultimo_run.json
```
Codici di uscita: `0` ok · `2` controlli bloccanti KO (nessuna proposta) · `1` errore tecnico.

### 3. Lettura esiti
Leggi **solo** `ultimo_run.json`. Campi principali:
- `controlli[]` (id, esito, messaggio)
- `fabbisogno` (picco, data_picco, primo_giorno_scoperto, quota_classe_0, quota_classe_1, non_collocabili)
- `spostamenti` (n_righe, importo_totale, n_fornitori, per_classe)
- `forzati[]` (righe pagate oltre capacità per vincoli fornitore)
- `azioni_operative[]` (RiBa da richiamare, RID da revocare)
- `file_proposta`

### 4. Pubblicazione
Rispondi su Telegram con il messaggio di sintesi (punto 5) e **allega `file_proposta`**
aggiungendo in fondo alla risposta la riga `MEDIA:<percorso assoluto di file_proposta>`
(il gateway la trasforma in allegato).
Con exit code 2 niente allegato: solo i controlli KO e cosa serve per ripartire.

### 5. Messaggio di sintesi (modello)
```
{semaforo} Tesoreria {data} — run {run_id}
Margine minimo: {allerta.margine_minimo} il {allerta.data_margine_minimo} (soglia {allerta.soglia_margine})
Sotto soglia dal {allerta.primo_giorno_sotto_soglia} · fido sforato dal {allerta.primo_giorno_oltre_fido}
Fabbisogno: {picco} € dal {primo_giorno_scoperto}  [classe 0: {q0} · classe 1: {q1}]
Spostati: {n_righe} pagamenti, {importo} €, {n_fornitori} fornitori
Da fare in banca: {n_riba} RiBa, {n_rid} RID
Anomalie: {elenco controlli in avviso, o "nessuna"}
File: {file_proposta}
```
Aggiungi al massimo 3 righe di commento: cosa è cambiato rispetto a ieri
(usa `tes_v_delta_run` sul DB), solo se rilevante.

### 6. Registrazione
Il motore scrive già run, righe e saldi a DB. Tu aggiungi in `tes_audit`
l'evento `messaggio_inviato` con il testo inviato.

## Rispondere a domande
- **"Perché X è slittato?"** → `tes_proposta_riga` del run, campi `regola`, `motivo`, `classe`.
  Cita la regola con parole semplici (es. "classe 3, pagato pro quota al 40% il 12/11
  perché la capacità di quel giorno era 85.000 €").
- **"Quanto abbiamo slittato su Y?"** → vista `tes_v_slittamento_fornitore`.
- **Mai** ricalcolare a mente: se la risposta richiede un calcolo nuovo, usa una query o una simulazione.

## Anagrafica fornitori e accordi (Postgres)
Script: `$PY $S/anagrafica.py …` (DSN dall'ambiente). Uscita 0 scritto · 3 solo anteprima · 1 errore.

**Consultare** (libero): `cerca "<nome>"` · `mostra <cod>` · `storico <cod>` · `accordi [--stato proposta]` ·
`esporta --a /opt/data/tesoreria/out/anagrafica_AAAAMMGG.xlsx` (poi allegala).

**Modificare** (solo se lo chiede Filippo):
1. Individua il fornitore con `cerca`. Più risultati → chiedi quale.
2. Lancia **senza** `--conferma`:
   `modifica <cod> --set rilevanza=3 --autore "Filippo (Telegram)" --motivo "<parole di Filippo>"`
   oppure `accordo <cod> --decorrenza AAAA-MM-GG --set stato=Rifiutato --autore … --motivo …`
3. Mostra a Filippo solo i campi in `cambiati`, prima → dopo, e chiedi "Confermi?".
4. Solo dopo un sì esplicito rilancia lo stesso comando con `--conferma`. Rispondi "Fatto".
5. Se la modifica cambia la proposta di oggi, proponi di rifare il run (non rifarlo da solo).

Campi fornitore: `in_elenco_acquisto` (S/N) · `classe_priorita` (0-5) · `rilevanza` (1-3) ·
`scaduto_max_tollerato` · `slittamento_max_gg` · `pagamento_minimo` · `note` · `ragione_sociale`;
`vuoto` cancella il valore. `--dal AAAA-MM-GG` per una decorrenza diversa da oggi.
Campi accordo: `stato` (Attivo/Proposta/Rifiutato) · `criterio` · `valido_fino` · `data_pagamento_scaduto` · `note`;
nuovo accordo: `--nuovo --decorrenza … --set ragione_sociale=…`.
Mai UPDATE manuali in SQL dall'agente: solo tramite lo script.
Anagrafica e accordi si modificano anche dalla **Console Tesoreria** (CFO, Specialist): quelle modifiche hanno autore
`… (console)` e motivo in `tes_audit`. Alla domanda "chi ha cambiato X?" rispondi da `tes_audit`
(eventi `anagrafica_modificata`, `accordo_modificato`). Le simulazioni della console sono run `simulazione` con
`avviato_da` valorizzato: non confonderle con il piano ufficiale.

## Simulazioni
```bash
$PY $S/riscadenzia.py ... --simula --override classe:1207=2 --out /opt/data/tesoreria/sim
```
Override disponibili (ripetibili):
`classe:<cod>=<0-5>` · `elenco:<cod>=S|N` · `slittamento_max:<cod>=<gg>` · `scaduto_max:<cod>=<€>` ·
`fido=<€>` · `cuscinetto=<€>` · `orizzonte=<30-60>` · `lookahead=<gg>|orizzonte` · `allerta=<€>` ·
`rilevanza:<cod>=<1-3>` · `piano=due_fasi|cassa`
Le simulazioni hanno `tipo_run = 'simulazione'` a DB; il file `SIM_…` si allega solo se Filippo lo chiede.
Presenta il confronto con il run ufficiale del giorno: fabbisogno, n. spostamenti, fornitori impattati.

## Errori frequenti
| Sintomo | Causa probabile | Azione |
|---|---|---|
| controllo `cache_coerente` in avviso | file salvato senza ricalcolo completo in Excel | segnala; la proposta è valida (il motore ricalcola dallo scadenzario) ma la previsione in Excel è disallineata |
| `data_consuntivo_recente` KO | incassi non ancora caricati | attendi e riprova (step 1) |
| `chiavi_univoche` KO | export ERP duplicato | stop, segnala le chiavi duplicate |
| fabbisogno con quota classe 0 > 0 | anche stipendi/tributi non stanno nel fido | allerta 🔴 in testa al messaggio |
| `orizzonte_coperto` KO | il Report finisce prima della fine dell'orizzonte (es. a fine anno) | segnala: va esteso il Report o ridotto l'orizzonte (`--override orizzonte=N`, solo in simulazione) |
| exit code 1 | errore tecnico (file illeggibile, foglio rinominato…) | riporta `errore` dal JSON a Filippo, non riprovare più di una volta |

## Regole dell'output da conoscere per spiegare
- **Partita** = documento + scadenza attuale: rate con stessa scadenza si spostano insieme.
- `congelata` = scadenza in `date_congelate` (oggi vuoto): non si tocca.
- `congelata_accordo` = scaduto di un fornitore con accordo: fermo, nessun effetto di cassa.
- `anticipata_tutela` = scadenza protetta (elenco, corrente di accordo, classe 0 da anagrafica) che era
  slittata oltre la scadenza originale e che il motore riporta indietro nel primo giorno in cui la cassa
  la regge fino alla vecchia data. Mai prima di `ScadOriginale`.
- Le note di credito seguono il primo pagamento del fornitore e sono nel tracciato di import.
- **Piano in due fasi** (`piano.modalita: due_fasi`, JSON `piano_taglio`):
  - `tutela_scad_documento` = tutelato pagato alla scadenza da documento (piano da regole, senza guardare la cassa);
  - `tagliata` = tolta dal piano da regole perché faceva sforare il fido, ricollocata al primo giorno utile;
  - `tagliata_non_collocabile` = tagliata e parcheggiata a fine orizzonte (genera fabbisogno);
  - ordine di taglio: rilevanza del fornitore (3 → 2 → 1), poi classe, poi scadenza più recente;
  - nel messaggio riporta `scoperto_piano_regole` (quanto costerebbe rispettare tutte le regole) e i tagli per rilevanza;
    se `allerta.fornitori_strategici_tagliati` non è vuoto, segnalalo in testa.
  - Il corrente degli accordi non si taglia. Se `allerta.accordi_non_sostenibili_dal` è valorizzato, scrivi in testa:
    "Accordi non sostenibili dal <data>: scoperto <scoperto_dopo_tagli> € anche tagliando tutto il tagliabile" e
    indica i 3 accordi con `corrente_fino_primo_scoperto` più alto (da `accordi[]`).
  - Per fissare la data di un tagliato: sigla BLOC nella nota e nuova `dat_scadenza` nel gestionale.
- `protetta_c1` con origine `accordo:corrente` = corrente di un accordo: va pagato alla scadenza.
  Se nel JSON `accordi[].corrente_gia_scaduto_n > 0`, c'è corrente già in ritardo: **segnalalo in testa al messaggio**
  (rischio di decadenza dell'accordo).
- Avvisi `accordi_in_scadenza` / `accordi_non_attivi`: riportali nel messaggio del giorno.
- Accordi in stato **Proposta**: applicati come accordi certi (come Attivo). **Rifiutato** = ignorato.
- La nota d'import è la **nota compatta** (sostituisce quella del gestionale, max 240 caratteri) e finisce con
  `C<classe>-<origine>` (BLOC, TRIB, TC, PDR, ACC, ACCS, ELENCO, ANAG, DEF): usala per spiegare le scelte.
  La nota precedente è salvata in `tes_proposta_riga.nota_precedente`.
- `congelata_accordo` con data = scaduto congelato al 31/01/2027 (origine ACCS), fisso quando entra nell'orizzonte.
- Il semaforo viene da `allerta.livello` (verde / giallo / rosso / rosso_prioritario): non ricalcolarlo.
- `non_collocabile` = parcheggiata all'ultimo giorno dell'orizzonte: è debito che genera fabbisogno, non un pagamento programmato.
- `non_collocabile_invariata` = già oltre l'orizzonte, resta dov'è.
- Le partite non importabili (protocollo mancante) compaiono in *Azioni_operative* come "a mano".
