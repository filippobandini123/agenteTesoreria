"""Anagrafica fornitori e accordi su PostgreSQL (fonte unica dal 28/10).

- `tes_anagrafica_fornitori`: storico a versioni (valido_dal / valido_al). Una modifica chiude la
  versione corrente e ne apre una nuova; nella stessa data la versione viene aggiornata.
- `tes_accordo`: un record per (fornitore, decorrenza); ogni modifica lascia traccia in `tes_audit`
  con i valori prima e dopo.
- Nessuna scrittura senza `conferma=True`: senza conferma si calcola solo il prima/dopo da mostrare.
"""
from __future__ import annotations

import json
import numbers
from datetime import date
from decimal import Decimal
from pathlib import Path

from .carica import accordi_da_righe, fornitori_da_righe, STATI_ACCORDO

CAMPI_FORNITORE = {
    "ragione_sociale": ("testo", None),
    "in_elenco_acquisto": ("sn", None),
    "classe_priorita": ("intero", (0, 5)),
    "rilevanza": ("intero", (1, 3)),
    "scaduto_max_tollerato": ("importo", None),
    "slittamento_max_gg": ("intero", (0, 3650)),
    "pagamento_minimo": ("importo", None),
    "note": ("testo", None),
}
CAMPI_ACCORDO = {
    "ragione_sociale": ("testo", None),
    "stato": ("stato", None),
    "criterio": ("criterio", None),
    "valido_fino": ("data", None),
    "data_pagamento_scaduto": ("data", None),
    "note": ("testo", None),
}
VUOTO = {"", "vuoto", "null", "none", "-"}


def connetti(dsn: str, schema: str):
    import psycopg
    conn = psycopg.connect(dsn)
    conn.execute(f"SET search_path = {schema}")
    return conn


def converti(campo: str, valore, tabella: dict):
    """Testo inserito dall'utente (o dal CLI) → valore tipizzato; errore leggibile se non valido."""
    if campo not in tabella:
        raise ValueError(f"campo '{campo}' non modificabile (ammessi: {', '.join(tabella)})")
    tipo, rng = tabella[campo]
    if valore is None or (isinstance(valore, str) and valore.strip().lower() in VUOTO):
        if tipo in ("sn", "stato", "criterio"):
            raise ValueError(f"{campo}: valore obbligatorio")
        return None
    if tipo == "importo" and isinstance(valore, numbers.Real):
        return float(valore)
    v = str(valore).strip()
    if tipo == "testo":
        return v
    if tipo == "sn":
        if v.upper() not in ("S", "N", "SI", "SÌ", "NO", "TRUE", "FALSE"):
            raise ValueError(f"{campo}: usare S o N")
        return v.upper() in ("S", "SI", "SÌ", "TRUE")
    if tipo == "intero":
        n = int(v)
        if rng and not rng[0] <= n <= rng[1]:
            raise ValueError(f"{campo}: valore {n} fuori da {rng[0]}-{rng[1]}")
        return n
    if tipo == "importo":
        v = v.replace("€", "").replace(" ", "")
        if "," in v:                                   # formato italiano 1.234,56
            return float(v.replace(".", "").replace(",", "."))
        if v.count(".") >= 1 and all(len(x) == 3 for x in v.split(".")[1:]):   # 5.000 = cinquemila
            return float(v.replace(".", ""))
        return float(v)
    if tipo == "data":
        if "/" in v:
            g, m, a = v.split("/")
            return date(int(a) if len(a) == 4 else 2000 + int(a), int(m), int(g))
        return date.fromisoformat(v)
    if tipo == "stato":
        s = STATI_ACCORDO.get(v.lower())
        if not s:
            raise ValueError("stato: Attivo, Proposta o Rifiutato")
        return s
    if tipo == "criterio":
        if v not in ("data_doc", "scadenza_originale"):
            raise ValueError("criterio: data_doc o scadenza_originale")
        return v
    raise ValueError(campo)


def _riga(cur) -> list[dict]:
    cols = [c.name for c in cur.description]
    return [{k: (float(v) if isinstance(v, Decimal) else v) for k, v in zip(cols, r)} for r in cur.fetchall()]


# ------------------------------------------------------------------ lettura

def fornitori_validi(cur, oggi: date) -> list[dict]:
    cur.execute("""SELECT cod_fornitore, ragione_sociale, in_elenco_acquisto, classe_priorita, rilevanza,
                          scaduto_max_tollerato, slittamento_max_gg, pagamento_minimo, note, valido_dal
                   FROM tes_anagrafica_fornitori
                   WHERE valido_dal <= %s AND (valido_al IS NULL OR valido_al > %s)
                   ORDER BY cod_fornitore""", (oggi, oggi))
    return _riga(cur)


def accordi_tutti(cur) -> list[dict]:
    cur.execute("""SELECT cod_fornitore, ragione_sociale, tipo, decorrenza, criterio, valido_fino, note, stato,
                          data_pagamento_scaduto
                   FROM tes_accordo ORDER BY cod_fornitore, decorrenza""")
    return _riga(cur)


def carica_da_db(dsn: str, schema: str, oggi: date, criterio_default: str = "data_doc"):
    """Per il motore: (anagrafica, (accordi attivi, proposte, ignorati)) valida alla data del run."""
    with connetti(dsn, schema) as conn, conn.cursor() as cur:
        f = fornitori_validi(cur, oggi)
        a = accordi_tutti(cur)
    if not f:
        raise RuntimeError("Anagrafica fornitori vuota su database: eseguire prima `anagrafica.py importa`.")
    return fornitori_da_righe(f, oggi), accordi_da_righe(a, oggi, criterio_default)


def versione_corrente(cur, cod: int) -> dict | None:
    cur.execute("""SELECT * FROM tes_anagrafica_fornitori WHERE cod_fornitore = %s AND valido_al IS NULL""", (cod,))
    r = _riga(cur)
    return r[0] if r else None


def cerca_fornitori(cur, testo: str, oggi: date) -> list[dict]:
    cur.execute("""SELECT cod_fornitore, ragione_sociale, in_elenco_acquisto, classe_priorita, rilevanza
                   FROM tes_anagrafica_fornitori
                   WHERE valido_al IS NULL AND (ragione_sociale ILIKE %s OR cod_fornitore::text = %s)
                   ORDER BY ragione_sociale""", (f"%{testo}%", testo.strip()))
    return _riga(cur)


def storico_fornitore(cur, cod: int) -> list[dict]:
    cur.execute("""SELECT valido_dal, valido_al, in_elenco_acquisto, classe_priorita, rilevanza, scaduto_max_tollerato,
                          slittamento_max_gg, pagamento_minimo, note, modificato_da, motivo, registrato_il
                   FROM tes_anagrafica_fornitori WHERE cod_fornitore = %s ORDER BY valido_dal""", (cod,))
    return _riga(cur)


# ------------------------------------------------------------------ scrittura

def _audit(cur, evento: str, dettaglio: dict, autore: str) -> None:
    cur.execute("INSERT INTO tes_audit (evento, dettaglio, autore) VALUES (%s, %s, %s)",
                (evento, json.dumps(dettaglio, default=str, ensure_ascii=False), autore))


def modifica_fornitore(cur, cod: int, valori: dict, autore: str, motivo: str, dal: date,
                       conferma: bool = False) -> dict:
    """Restituisce {'prima', 'dopo', 'cambiati', 'scritto'}. Scrive solo con conferma=True."""
    if not motivo or not autore:
        raise ValueError("autore e motivo sono obbligatori")
    nuovi = {k: converti(k, v, CAMPI_FORNITORE) for k, v in valori.items()}
    att = versione_corrente(cur, cod)
    if att is None and "ragione_sociale" not in nuovi:
        raise ValueError(f"fornitore {cod} non presente: per crearlo indicare anche ragione_sociale")
    prima = {k: (att or {}).get(k) for k in CAMPI_FORNITORE}
    if att is None:
        prima["in_elenco_acquisto"] = False
    dopo = {**prima, **nuovi}
    cambiati = [k for k in CAMPI_FORNITORE if prima.get(k) != dopo.get(k)]
    esito = {"cod_fornitore": cod, "prima": prima, "dopo": dopo, "cambiati": cambiati, "dal": dal,
             "scritto": False}
    if not cambiati or not conferma:
        return esito
    if att and att["valido_dal"] > dal:
        raise ValueError(f"la versione corrente vale dal {att['valido_dal']}: la modifica non può decorrere prima")
    campi = list(CAMPI_FORNITORE)
    if att and att["valido_dal"] == dal:
        cur.execute(f"""UPDATE tes_anagrafica_fornitori SET {', '.join(f'{c} = %s' for c in campi)},
                        modificato_da = %s, motivo = %s, registrato_il = now() WHERE id = %s""",
                    (*[dopo[c] for c in campi], autore, motivo, att["id"]))
    else:
        if att:
            cur.execute("UPDATE tes_anagrafica_fornitori SET valido_al = %s WHERE id = %s", (dal, att["id"]))
        cur.execute(f"""INSERT INTO tes_anagrafica_fornitori (cod_fornitore, {', '.join(campi)}, valido_dal,
                        modificato_da, motivo) VALUES (%s, {', '.join(['%s'] * len(campi))}, %s, %s, %s)""",
                    (cod, *[dopo[c] for c in campi], dal, autore, motivo))
    _audit(cur, "anagrafica_modificata", {**esito, "motivo": motivo}, autore)
    esito["scritto"] = True
    return esito


def modifica_accordo(cur, cod: int, decorrenza: date, valori: dict, autore: str, motivo: str,
                     conferma: bool = False, nuovo: bool = False) -> dict:
    if not motivo or not autore:
        raise ValueError("autore e motivo sono obbligatori")
    nuovi = {k: converti(k, v, CAMPI_ACCORDO) for k, v in valori.items()}
    cur.execute("SELECT * FROM tes_accordo WHERE cod_fornitore = %s AND decorrenza = %s", (cod, decorrenza))
    r = _riga(cur)
    att = r[0] if r else None
    if att is None and not nuovo:
        cur.execute("SELECT decorrenza, stato FROM tes_accordo WHERE cod_fornitore = %s", (cod,))
        altri = _riga(cur)
        raise ValueError(f"nessun accordo del fornitore {cod} con decorrenza {decorrenza}"
                         + (f" (presenti: {', '.join(str(x['decorrenza']) for x in altri)})" if altri else "")
                         + "; per crearne uno usare --nuovo")
    if att is not None and nuovo:
        raise ValueError("accordo già presente con questa decorrenza")
    base = {k: (att or {}).get(k) for k in CAMPI_ACCORDO}
    if att is None:
        base.update(stato="proposta", criterio="data_doc")
    dopo = {**base, **nuovi}
    cambiati = [k for k in CAMPI_ACCORDO if base.get(k) != dopo.get(k)] or (["nuovo"] if nuovo else [])
    esito = {"cod_fornitore": cod, "decorrenza": decorrenza, "prima": None if nuovo else base, "dopo": dopo,
             "cambiati": cambiati, "scritto": False}
    if not cambiati or not conferma:
        return esito
    campi = list(CAMPI_ACCORDO)
    if att is None:
        cur.execute(f"""INSERT INTO tes_accordo (cod_fornitore, decorrenza, {', '.join(campi)}, modificato_da, motivo)
                        VALUES (%s, %s, {', '.join(['%s'] * len(campi))}, %s, %s)""",
                    (cod, decorrenza, *[dopo[c] for c in campi], autore, motivo))
    else:
        cur.execute(f"""UPDATE tes_accordo SET {', '.join(f'{c} = %s' for c in campi)}, modificato_da = %s,
                        motivo = %s, aggiornato_il = now() WHERE cod_fornitore = %s AND decorrenza = %s""",
                    (*[dopo[c] for c in campi], autore, motivo, cod, decorrenza))
    _audit(cur, "accordo_modificato", {**esito, "motivo": motivo}, autore)
    esito["scritto"] = True
    return esito


# ------------------------------------------------------------------ import / export Excel

def importa_excel(cur, path: Path, foglio: str, foglio_accordi: str, dal: date, autore: str,
                  forza: bool = False) -> dict:
    """Caricamento iniziale dall'Excel (una tantum). Rifiuta se il DB è già popolato, salvo forza=True
    (in quel caso ogni differenza diventa una nuova versione con data `dal`)."""
    import pandas as pd
    cur.execute("SELECT count(*) FROM tes_anagrafica_fornitori")
    if cur.fetchone()[0] and not forza:
        raise RuntimeError("anagrafica già presente su database: usare --forza per riallineare dall'Excel")
    df = pd.read_excel(path, sheet_name=foglio)
    n_f = n_a = 0
    for r in df.to_dict("records"):
        if pd.isna(r.get("cod_fornitore")):
            continue
        valori = {}
        for k in CAMPI_FORNITORE:
            v = r.get(k)
            valori[k] = None if v is None or (isinstance(v, float) and pd.isna(v)) else v
        valori["in_elenco_acquisto"] = valori.get("in_elenco_acquisto") or "N"
        valori = {k: (str(int(v)) if isinstance(v, float) and v.is_integer() else v) for k, v in valori.items()}
        dal_r = pd.Timestamp(r["valido_dal"]).date() if not pd.isna(r.get("valido_dal")) else dal
        e = modifica_fornitore(cur, int(r["cod_fornitore"]), valori, autore, "import iniziale da Excel", dal_r, True)
        n_f += e["scritto"]
    try:
        da = pd.read_excel(path, sheet_name=foglio_accordi)
    except ValueError:
        da = pd.DataFrame()
    for r in da.to_dict("records"):
        if pd.isna(r.get("cod_fornitore")) or pd.isna(r.get("decorrenza")):
            continue
        dec = pd.Timestamp(r["decorrenza"]).date()
        valori = {k: (None if r.get(k) is None or (isinstance(r.get(k), float) and pd.isna(r.get(k)))
                      else (pd.Timestamp(r[k]).date().isoformat() if k in ("valido_fino", "data_pagamento_scaduto")
                            else r[k]))
                  for k in CAMPI_ACCORDO if k in r}
        valori["stato"] = valori.get("stato") or "Attivo"
        valori["criterio"] = valori.get("criterio") or "data_doc"
        cur.execute("SELECT 1 FROM tes_accordo WHERE cod_fornitore = %s AND decorrenza = %s", (int(r["cod_fornitore"]), dec))
        esiste = cur.fetchone() is not None
        e = modifica_accordo(cur, int(r["cod_fornitore"]), dec, valori, autore, "import iniziale da Excel",
                             True, nuovo=not esiste)
        n_a += e["scritto"]
    return {"fornitori_scritti": n_f, "accordi_scritti": n_a}


def esporta_excel(cur, path: Path, oggi: date) -> Path:
    """Copia in sola lettura dell'anagrafica valida a `oggi` (stesse colonne lette dal motore)."""
    import pandas as pd
    f = pd.DataFrame(fornitori_validi(cur, oggi))
    if not f.empty:
        f["in_elenco_acquisto"] = f["in_elenco_acquisto"].map({True: "S", False: "N"})
    a = pd.DataFrame(accordi_tutti(cur))
    if not a.empty:
        a["stato"] = a["stato"].str.capitalize()
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path) as w:
        f.to_excel(w, sheet_name="Fornitori", index=False)
        a.to_excel(w, sheet_name="Accordi", index=False)
    return path
