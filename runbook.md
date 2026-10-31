# Runbook — Tesoriere

## Calendario
| Quando | Cosa | Chi |
|---|---|---|
| ogni mattina, dopo gli incassi | Filippo manda il Report su Telegram; l'agente lancia subito il run | Filippo → agente |
| 11:00 | Se il Report non è arrivato → promemoria su Telegram | agente |
| entro le 12:00 | Revisione proposta, import nel gestionale | tesoreria [DA DEFINIRE chi] |
| dopo l'import | Esito registrato (approvata / con modifiche / respinta) | tesoreria → agente |

Orari indicativi: da allineare all'ora in cui di solito sono caricati gli incassi.

Promemoria Hermes (linguaggio naturale, convertito da Hermes):
> "Nei giorni lavorativi alle 11:00, se oggi non è stato fatto un run ufficiale, scrivi a Filippo su Telegram che il Report di cassa non è ancora arrivato."

## Flusso
```
Telegram (Report_di_cassa_*.xlsx in allegato, salvato da Excel dopo refresh)
   │  l'agente salva l'allegato
   ▼
/opt/data/tesoreria/in ──► riscadenzia.py ◄── Postgres (anagrafica, accordi)
                            │        ──► Postgres (run, righe, saldi, audit) · archivio del Report
                            ├─► Proposta_riscadenziamento_AAAAMMGG.xlsx ──► Telegram (allegato)
                            └─► ultimo_run.json ──► agente ──► messaggio di sintesi su Telegram
```

## Stati della proposta
`da_rivedere` → `approvata` | `approvata_con_modifiche` | `respinta` → (il giorno dopo) `superata`

Finché il canale di approvazione non è definito, l'esito si registra rispondendo
all'agente in chat ("approvata", "approvata con modifiche: …", "respinta: …").
L'agente scrive l'evento in `tes_audit` e aggiorna `tes_run.stato_approvazione`.

## Escalation
| Condizione | Livello | Destinatario |
|---|---|---|
| Margine sempre ≥ soglia (`allerte.margine_minimo`, 100.000 €) | 🟢 info | [DA DEFINIRE] |
| Margine sotto soglia, fido non sforato | 🟡 | [DA DEFINIRE] |
| Fido sforato (fabbisogno > 0) | 🔴 | [DA DEFINIRE] |
| Quota fabbisogno da classe 0 > 0 (stipendi/tributi scoperti) | 🔴 prioritario | [DA DEFINIRE] |
| Controllo bloccante KO / errore tecnico | 🔴 tecnico | Filippo |

## Ripartenza dopo un errore
1. Leggere `tes_controllo` dell'ultimo run (`esito = 'ko'`).
2. Correggere la causa a monte (file, export ERP, anagrafica).
3. Chiedere all'agente "rifai il run": crea un nuovo `run_id`, il precedente resta come traccia.

## Modifica delle regole
1. Modificare `config/regole.yaml` e incrementare `versione`.
2. Far girare una simulazione sul file del giorno e confrontarla con il run ufficiale.
3. Solo dopo, usare la nuova versione nel run ufficiale del giorno seguente.
Il motore registra l'hash del file a ogni run: una modifica non versionata viene comunque tracciata.

## Manutenzione anagrafica fornitori e accordi (su Postgres dal 28/10)
- Da Telegram: "metti Qualifarma a rilevanza 3", "l'accordo di X è rifiutato". L'agente mostra
  prima/dopo, aspetta l'ok, scrive con `anagrafica.py … --conferma`. Ogni modifica crea una nuova
  versione (`valido_dal`/`valido_al`) con autore e motivo, più un evento in `tes_audit`.
- In SQL direttamente: chiudere la versione corrente (`valido_al`) e inserirne una nuova, oppure
  usare `anagrafica.py` dal terminale. Non fare UPDATE in place: si perde lo storico.
- Copia consultabile: "mandami l'anagrafica" → `anagrafica.py esporta` in Excel (sola lettura).
- Ogni lunedì l'agente segnala i fornitori nuovi (presenti nello scadenzario ma non
  in anagrafica), che finiscono in classe default.
