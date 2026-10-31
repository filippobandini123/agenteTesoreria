# Scenari di test del motore

Ogni scenario è un piccolo dataset sintetico (5–15 righe) con l'esito atteso.
Da tradurre in test automatici (`pytest`) quando si scrive `riscadenzia.py`.
Convenzione: fido = 100 (scala ridotta), cuscinetto = 0, orizzonte 10 giorni lavorativi.

| # | Scenario | Dati | Esito atteso |
|---|---|---|---|
| 1 | Cassa abbondante | saldo sempre ≥ 0, 5 pagamenti classe 3 | nessuno spostamento, fabbisogno 0 |
| 2 | Scopertura temporanea | G3 pagamenti 150, G6 incasso 200 | pagamenti classe 3 di G3 spostati a G6 (primo giorno con capienza), fabbisogno 0 |
| 3 | Look-ahead su protetto | G2 capacità 80; G5 stipendi (classe 0) che lasciano margine 30 | a G2 si pagano al massimo 30 di classe 3, il resto slitta |
| 4 | Ordine tra classi | G2 capacità 50; coda: classe 2 = 40 (una rata), classe 4 = 40 (una rata) | classe 2 pagata, classe 4 slitta (10 di avanzo, nessun frazionamento); in *Parziali_suggeriti* compare classe 4 per 10 |
| 5 | Pro quota a rate intere | G2 capacità 60; classe 3: A rate 50+50, B rate 25+25 | quote teoriche A 40, B 20. Prima passata: nessuna rata sta nella quota (50 > 40, 25 > 20). Seconda passata: scarto maggiore A (40) → A paga una rata da 50, restano 10; la rata di B (25) non ci sta. Esito: A 50, B 0, avanzo 10 |
| 6 | FIFO dentro il fornitore | fornitore A: rate 30 (scad. 01) + 30 (scad. 05); quota teorica 45 | rata 01 pagata, rata 05 slitta; avanzo 15 alla seconda passata |
| 6b | Nessun frazionamento | qualunque input | nessuna riga con importo pagato < residuo; `frazionata` sempre false |
| 7 | Protetti non coperti | G4 classe 0 = 300, nessun incasso | classe 0 resta a G4, fabbisogno 200 in quota classe 0, allerta rossa |
| 8 | Fornitore in elenco | classe 1 in scadenza G3, cassa insufficiente | non spostato, fabbisogno in quota classe 1 |
| 9 | Slittamento massimo | fornitore con `slittamento_max_gg` = 5, rata già a +5 | rata forzata (pagata), regola `forzata_slitt_max`, fabbisogno se necessario |
| 10 | Scaduto massimo | `scaduto_max_tollerato` = 50, scaduto 80 | pagati almeno 30, regola `forzata_scaduto_max` |
| 11 | Non collocabile | deficit strutturale su tutto l'orizzonte | righe classe 2–5 parcheggiate all'ultimo giorno di pagamento dell'orizzonte; il fabbisogno di quel giorno le include |
| 12 | Riga sospesa | Sospeso = 1 | esclusa, non spostata, elencata |
| 13 | Nota di credito | fornitore A: fattura 100, NC −30 | pagamento netto 70 nel giorno della fattura (se `compensa_fornitore`) |
| 14 | Trattativa conclusa | NoteAmm con "TC" | classe 0, mai spostata |
| 15 | Anticipo dopo miglioramento | ieri rata spostata da G2 a G8; oggi incasso extra a G3 | oggi proposta a G3 (se `anticipa_riscadenziati` = true) |
| 16 | Stabilità | stesso input due volte | output identico (determinismo, stesso hash) |
| 17 | RiBa spostata | RB spostata | `azione_operativa` = richiamo RiBa, modalità a previsione = BO |
| 18 | Cache incoerente | riga Fornitori RIBA in cache ≠ somma tabella | controllo `cache_coerente` in avviso, proposta comunque prodotta |
| 19 | File vecchio | file modificato 2 giorni fa | exit code 2, nessuna proposta |
| 20 | Fornitore nuovo | cod_clifor assente da anagrafica | classe default 3, segnalato in elenco "nuovi fornitori" |
| 21 | Accordo: scaduto e corrente | fornitore con accordo dal 01/09: fattura di giugno + fattura di settembre | giugno `congelata_accordo` (fuori dal piano), settembre protetta classe 1 alla scadenza |
| 22 | Accordo per scadenza originale | `criterio = scadenza_originale` | lo split segue la scadenza da documento, non la data documento |
| 23 | Precedenza | TC o "piano di rientro" su un fornitore con accordo | vince la regola automatica (classe 0, data bloccata) |
| 24 | Scaduto congelato e scaduto massimo | accordo + `scaduto_max_tollerato` | lo scaduto congelato non fa scattare pagamenti forzati |

Implementati in `skills/riscadenziamento-pagamenti/scripts/tests/test_scenari.py` (26 test).

## Test sul file reale (accettazione)
1. Run sul file di ottobre 2026 con `anagrafica` vuota (tutti classe 3): verificare che saldo_ante
   ricalcolato coincida con la riga 12 del Report (a meno dello scarto rilevato da `cache_coerente`).
2. Stesso run con 10–20 fornitori in elenco: confrontare fabbisogno e numero spostamenti.
3. Rivedere a mano 10 righe a campione della proposta: la motivazione deve essere comprensibile senza aprire il codice.
