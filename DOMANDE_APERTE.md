# Domande aperte

Ogni voce corrisponde a un `[DA CONFERMARE]` nei file. Il default indicato è già
scritto nella configurazione, quindi il sistema funziona anche prima della risposta.

## Decise (24/10)
- Pagamenti parziali: **non importabili** → il motore sposta solo rate intere; i parziali utili compaiono in un foglio informativo.
- Fornitori in elenco: pagati alla **scadenza attuale** (`dat_scadenza`).
- Righe non collocabili: parcheggiate all'**ultimo giorno dell'orizzonte**.

## Decise (26/10)
- **Accordi con i fornitori** (foglio *Accordi* dell'anagrafica): dalla decorrenza lo scaduto resta congelato e il corrente si paga alla scadenza (classe 1). Criterio per accordo: data documento (default) o scadenza originale.
- **Piano di rientro**: oggi nessuno; quando ci sarà, le scadenze delle rate si aggiornano nel gestionale con la nota "piano di rientro" e il motore le blocca a quelle date (regola automatica già attiva).
- **13/11**: il Report verrà corretto togliendo la compensazione "Altro". Quando arriva il file nuovo e gli accordi sono censiti, la voce `date_congelate` si potrà togliere: il motore avvisa se la compensazione non c'è più.
- **Stato degli accordi**: Attivo (o vuoto) e Proposta applicati allo stesso modo (le proposte valgono come accordi certi, aggiornate man mano); Rifiutato = ignorato. Lo scenario separato "proposte accettate" va tolto. *(implementato 26/10)*
- **Scadenze bloccate**: sigla `BLOC` nella nota del gestionale → classe 0, pagata a `dat_scadenza`, precedenza su tutto (anche accordi). *(implementato 26/10)*
- **Protocollo**: colonna `numero_protocollo_effetto` dello scadenzario (univoca, coerente con la vista del gestionale). Da aggiungere anche al foglio Scaduti.
- **Note (26/10 sera)**: l'import SOVRASCRIVE la nota (varchar 240). Grammatica delle sigle: BLOC, TC, PDR, TIC (spostabile ma segnalata), SOL (informativa), HP (tolta), HPAG…Cn-XXX (sostituita), testo libero conservato. Nota compatta; se >240 nessun taglio → azione manuale. Il DB salva la nota precedente. *(implementato 26/10)*
- **Scaduto congelato**: pagamento al 31/01/2027 (data in input), nel tracciato di import con origine ACCS; fisso quando entra nell'orizzonte. Da dettagliare. *(implementato 26/10)*
- **Date variate senza nota**: ripianificate secondo la classe del fornitore (comportamento attuale).
- `ScadOriginale` = scadenza da documento; `dat_scadenza` = scadenza variata internamente.
- **Tracciato di import confermato**: Numdoc, Sezdoc ("00"), Datadoc, Datascad_vecchia, TipoCF (1), Protocollo (chiave univoca della scadenza), Data_Scanuova, Note. Nota: vedi sotto (sovrascrittura e nota compatta).

## Decise (27/10)
- **Tutela compatibile con la cassa**: le scadenze protette che oggi stanno *dopo* la scadenza originale
  (fornitori in elenco, corrente degli accordi, classe 0 da anagrafica) vengono **anticipate** al primo
  giorno in cui la cassa le regge fino alla vecchia data (stessa finestra di look-ahead), mai prima di
  `ScadOriginale` né prima di oggi. Regola `anticipata_tutela`. Parametro `tutela.anticipo_se_cassa_consente`. *(implementato 27/10)*
  - Da confermare: l'elenco `tutela.origini` (oggi: elenco_acquisto, accordo, anagrafica_classe0). Tributi, BLOC, TC e PDR esclusi: hanno già la loro data.
- **Note di credito nel tracciato di import**: incluse (prima restavano escluse e la data nel gestionale non si aggiornava). *(implementato 27/10)*

- **Piano in due fasi (27/10 pomeriggio, da specificare)**: (1) piano da regole, i tutelati pagati secondo le
  loro regole anche oltre la cassa → scoperto "vero"; (2) piano di taglio, il motore propone chi non pagare,
  dal fornitore meno strategico, finché si rientra nel fido; Filippo valida. I tagliati vanno al primo
  momento utile, oppure alla data che Filippo fissa con BLOC sulla scadenza (regola già attiva).
  Confermato 28/10: tutelati alla scadenza da documento senza guardare la cassa; classe 0 mai tagliabile;
  classi 2-5 come prima; colonna `rilevanza` 1/2/3 (vuota = 2); ordine di taglio rilevanza → classe →
  scadenza più recente; taglio di un fornitore di rilevanza 1 = allerta. *(implementato 28/10, `piano.modalita: due_fasi`)*
  Da decidere: il corrente degli accordi è tagliabile come gli altri (oggi sì, rilevanza del fornitore)?
  Sigla nella nota per le scadenze tagliate?

- **Canale Telegram (28/10)**: il Report arriva come allegato, la proposta torna come allegato;
  niente OneDrive (`previsione.modalita: allegato`), archivio dei Report con impronta. *(implementato 28/10)*
- **Anagrafica e accordi su Postgres (28/10)**: fonte unica, storico a versioni, modifiche in SQL o da
  Telegram con anteprima e conferma di Filippo (`anagrafica.py`). L'Excel serve solo al caricamento iniziale. *(implementato 28/10)*

## Decise (29/10)
- **Il corrente degli accordi non si taglia** (`taglio.accordi: false`): resta alla sua data come la classe 0,
  così lo scoperto compare nel giorno in cui gli accordi non sono sostenibili. Nuovo indicatore "classe 0 e
  accordi onorati, dopo i tagli" (primo giorno oltre il fido e scoperto massimo, escluso il giorno di parcheggio)
  nel Fabbisogno, nel JSON (`piano_taglio.dopo_tagli`, `allerta.accordi_non_sostenibili_dal`) e nella console;
  per accordo, il corrente da pagare fino a quel giorno. *(implementato 29/10)*
  - Conseguenza: per proteggere gli accordi si tagliano anche fornitori di rilevanza 1 (es. Angelini).
- **Ordine di priorità (31/10)**: 1) classe 0, 2) accordi, 3) classe 1 (al suo interno per rilevanza 3 → 2 → 1).
  Se anche tagliando tutta la classe 1 classe 0 e accordi non stanno nel fido, gli accordi **non** si tagliano:
  lo scoperto resta visibile nel suo giorno come fabbisogno (opzione A). *(comportamento già attivo)*
- **Console web** per CFO, Specialist e Filippo, dalla VPN: login con le utenze del gestionale (`public.utente`, MD5)
  limitato a `CONSOLE_UTENTI`; entrambi modificano anagrafica e accordi (anteprima, motivo, storico, registro);
  consultazione dei run e simulazioni. Nessun run ufficiale dalla console. *(implementato 29/10, vedi CONSOLE.md)*
  - Da chiarire: quale valore di `utente.stato` indica un utente attivo (`CONSOLE_STATO_ATTIVO`).
  - Consigliato: HTTPS con un reverse proxy anche dentro la VPN.

## Nuove dal primo run (25/10) — le più importanti
- **13/11**: ripianificato secondo le specifiche (`date_congelate` vuoto). *(implementato 26/10)*
- **Look-ahead**: 60 giorni, parametro `allocazione.lookahead_giorni`. *(implementato 26/10)*
- **Allerta**: solo allerta, nessun cuscinetto. Soglia di margine 100.000 € (`allerte.margine_minimo`). *(implementato 26/10)*
2. ~~**Look-ahead.**~~ Con il look-ahead sull'intero orizzonte, uno scoperto degli stipendi a
   novembre blocca *tutti* i pagamenti fornitori da oggi. Con una finestra di N giorni si
   paga con la cassa di oggi, e lo scoperto futuro emerge come fabbisogno da coprire.
   Simulazione sul file del 23/10:

   | Look-ahead | Partite pagate nell'orizzonte | Primo giorno scoperto | Non collocabili |
   |---|---|---|---|
   | intero orizzonte | 0 | 09/11 | 1.151 · 5,77 M€ |
   | 14 gg | 21 | 09/11 | 1.130 · 5,70 M€ |
   | 7 gg | 96 | 02/11 | 1.059 · 4,89 M€ |
   | 0 gg (solo saldo del giorno) | 122 | 27/10 | 1.036 · 4,56 M€ |

   → default: intero orizzonte (massima prudenza).

## Ancora aperte prima della produzione
3. **Canale e destinatari di approvazione ed escalation** (per ora solo chat).

## Regole (default già applicato)
4. **Punto di partenza della ripianificazione** per gli altri: ogni giorno da
   `ScadOriginale`, quindi le ipotesi HP dei giorni precedenti possono cambiare; oppure da
   `dat_scadenza`, quindi si sposta solo in avanti? → default: da ScadOriginale.
5. **Righe con trattativa conclusa (TC)**: bloccate alla data concordata? → default: sì.
6. **Righe sospese (Sospeso = 1)**: escluse dal piano? → default: escluse e segnalate.
7. **Note di credito**: compensate con le fatture dello stesso fornitore? → default: sì.
8. **Giorni di pagamento**: tutti i giorni lavorativi bancari o solo alcuni? → default: tutti.
9. **Soglia "rossa" del fabbisogno** → default: 500.000 €.
10. **Sigla per le note amministrative** generate dall'agente → default: `HPAG(gg/mm):gg/mm(AG)`.
11. **Causale VTS** (17 righe): sono tributi da trattare come classe 0?

## Tecniche
0. **Sezdoc nel tracciato di import**: oggi testo "00" (formato @). Nel primo import è servito un aggiustamento a mano; Filippo verifica il formato atteso dal gestionale. Per ora invariato.
12. ~~Cartella OneDrive~~ superata dal canale Telegram (28/10).
13. **Ora limite del promemoria** se il Report non arriva (default 11:00).
14. **Controllo `cache_coerente`**: bloccante o solo avviso? Vedi la nota nel README §5.
