"""Lettura del file di previsione (foglio Report + scadenzario) e dell'anagrafica fornitori."""
from __future__ import annotations

import math
import numbers
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from .modello import Accordo, Fornitore, Partita, Previsione


def _d(v) -> date | None:
    if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NaT:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, pd.Timestamp):
        return v.date()
    return None


def _num(v) -> float:
    if v is None or isinstance(v, str):
        return 0.0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(f) else f


def _righe_report(path: Path, foglio: str, max_riga: int, data_only: bool):
    wb = load_workbook(path, read_only=True, data_only=data_only)
    righe = [list(r) for r in wb[foglio].iter_rows(min_row=1, max_row=max_riga, values_only=True)]
    wb.close()
    return righe


def carica_previsione(path: Path, fonti: dict) -> Previsione:
    rc = fonti["previsione"]["report"]
    max_riga = 80
    val = _righe_report(path, rc["foglio"], max_riga, data_only=True)
    frm = _righe_report(path, rc["foglio"], max_riga, data_only=False)
    col_etich = ord(rc["colonna_etichette"].upper()) - ord("A")
    r_date = rc["riga_date"] - 1

    etichette = {}
    for i, riga in enumerate(frm):
        if len(riga) > col_etich and isinstance(riga[col_etich], str):
            etichette.setdefault(riga[col_etich].strip().lower(), i)

    def riga(spec) -> int:
        if isinstance(spec, dict):
            return int(spec["riga"]) - 1
        k = str(spec).strip().lower()
        if k not in etichette:
            raise KeyError(f"Riga '{spec}' non trovata nel foglio {rc['foglio']}")
        return etichette[k]

    righe = rc["righe"]
    colonne_data = {}
    for c, v in enumerate(val[r_date]):
        d = _d(v)
        if d:
            colonne_data[d] = c

    # ultimo giorno a consuntivo: la formula della riga test punta ai movimenti banca
    dc = rc["data_consuntivo"]
    r_test = riga(dc["riga_test"])
    cons = None
    for d, c in sorted(colonne_data.items()):
        f = frm[r_test][c] if c < len(frm[r_test]) else None
        if isinstance(f, str) and dc["marcatore_consuntivo"] in f:
            cons = d
    if cons is None:
        raise ValueError("Impossibile individuare l'ultimo giorno a consuntivo nel Report")

    r_saldo = riga(righe["saldo_finale"])
    r_entr = [riga(x) for x in righe["entrate"]]
    r_fisse = [riga(x) for x in righe["fisse_budget"]]
    r_forn = [riga(righe[k]) for k in ("fornitori_rid", "fornitori_riba", "fornitori_bo", "scaduto")]
    r_ini, r_te, r_tu = riga(righe["saldo_iniziale"]), riga(righe["totale_entrate"]), riga(righe["totale_uscite"])

    def v(r, c):
        return _num(val[r][c]) if c < len(val[r]) else 0.0

    flussi, cache_forn, saldo_cache, scarti = {}, {}, {}, []
    per_riga = {str(x).strip(): {} for x in righe["fisse_budget"]}
    for d, c in sorted(colonne_data.items()):
        saldo_cache[d] = v(r_saldo, c)
        if d <= cons:
            continue
        flussi[d] = sum(v(r, c) for r in r_entr) + sum(v(r, c) for r in r_fisse)
        for etich, r in zip(righe["fisse_budget"], r_fisse):
            per_riga[str(etich).strip()][d] = v(r, c)
        cache_forn[d] = sum(v(r, c) for r in r_forn)
        scarti.append((d, v(r_saldo, c) - (v(r_ini, c) + v(r_te, c) + v(r_tu, c))))

    return Previsione(data_consuntivo=cons, saldo_consuntivo=saldo_cache[cons], flussi_fissi=flussi,
                      righe_fornitori_cache=cache_forn, saldo_cache=saldo_cache, controllo_totali=scarti,
                      fisse_per_riga=per_riga)


def carica_partite(path: Path, fonti: dict) -> list[Partita]:
    prev = fonti["previsione"]
    frames = []
    for sezione, fonte in (("scadenzario", "scadenzario"), ("scaduti", "scaduti")):
        cfg = prev.get(sezione)
        if not cfg:
            continue
        df = pd.read_excel(path, sheet_name=cfg["foglio"],
                           dtype={"prg_documento": str, "des_num_doc": str, "cod_caucon": str})
        for k, v in (cfg.get("filtro") or {}).items():
            df = df[df[k] == v]
        df = df.assign(_fonte=fonte)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    col = prev["scadenzario"]["colonne"]
    df = df.rename(columns={v: k for k, v in col.items() if v in df.columns})
    df["scadenza_corrente"] = pd.to_datetime(df["scadenza_corrente"]).dt.date
    df["scadenza_originale"] = pd.to_datetime(df["scadenza_originale"]).dt.date
    df["data_doc"] = pd.to_datetime(df["data_doc"]).dt.date
    # una riga degli scaduti già presente nello scadenzario non deve contare due volte
    # (le righe ripetute DENTRO lo scadenzario sono rate distinte: la previsione le somma)
    chiave = ["id_documento", "scadenza_corrente", "scadenza_originale", "importo_residuo"]
    gia = set(map(tuple, df.loc[df["_fonte"] == "scadenzario", chiave].itertuples(index=False, name=None)))
    doppie = df["_fonte"].eq("scaduti") & df[chiave].apply(tuple, axis=1).isin(gia)
    df = df[~doppie]

    def _id(v):
        if v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, str) and not v.strip()):
            return None
        return str(int(v)) if isinstance(v, numbers.Real) else str(v).strip()

    con_chiave = "id_scadenza" in df.columns
    if con_chiave:
        # una riga = una scadenza del gestionale; scaduti già presenti nello scadenzario → una sola volta
        df = df.assign(_ids=df["id_scadenza"].map(_id))
        df = df[~(df["_ids"].notna() & df.duplicated(subset=["_ids"], keep="first"))]
        df = df.assign(_gruppo=df["_ids"].where(df["_ids"].notna(),
                                               "R" + df.index.astype(str)))
        gruppi = df.groupby("_gruppo", sort=True)
    else:
        gruppi = df.groupby(["id_documento", "scadenza_corrente"], sort=True)

    partite = []
    for chiave, g in gruppi:
        r0 = g.iloc[0]
        prg, scad = str(r0["id_documento"]), r0["scadenza_corrente"]
        prot = r0["protocollo"]
        # numbers.Real copre anche gli interi numpy (int64): con la colonna senza vuoti pandas non usa float
        prot = int(prot) if isinstance(prot, numbers.Real) and not math.isnan(prot) and int(prot) != 0 else None
        note = "_".join(dict.fromkeys(str(x) for x in g["note"] if isinstance(x, str) and x.strip()))
        ids = _id(r0["_ids"]) if con_chiave else None
        partite.append(Partita(
            id=(f"S{ids}" if ids else f"{prg}|{scad:%Y%m%d}|{chiave}") if con_chiave else f"{prg}|{scad:%Y%m%d}",
            prg_documento=prg, num_protocollo=prot, data_doc=r0["data_doc"],
            numero_doc=str(r0["numero_doc"] or ""), cod_fornitore=int(r0["cod_fornitore"]),
            ragione_sociale=str(r0["ragione_sociale"] or "").strip(), conto=str(r0["conto"] or "").strip(),
            causale=str(r0["causale"] or "").strip(), modalita=str(r0["modalita"] or "").strip(),
            importo=round(float(g["importo_residuo"].sum()), 2), scad_originale=min(g["scadenza_originale"]),
            scad_corrente=scad, note=note, sospesa=bool(_num(g["sospeso"].max())),
            fonte=r0["_fonte"], n_righe=len(g), id_scadenza=ids))

    if con_chiave:
        # con la chiave della scadenza basta che il Protocollo ci sia
        for p in partite:
            p.importabile = p.id_scadenza is not None
        return partite

    # senza chiave: protocollo documento presente e (protocollo, data doc, scadenza) univoca
    chiavi = defaultdict(set)
    for p in partite:
        chiavi[(p.num_protocollo, p.data_doc, p.scad_corrente)].add(p.prg_documento)
    for p in partite:
        p.importabile = p.num_protocollo is not None and len(chiavi[(p.num_protocollo, p.data_doc, p.scad_corrente)]) == 1
    return partite


def carica_anagrafica(path: Path, foglio: str, oggi: date) -> dict[int, Fornitore]:
    if not path or not Path(path).exists():
        return {}
    df = pd.read_excel(path, sheet_name=foglio)
    return fornitori_da_righe(df.to_dict("records"), oggi)


def fornitori_da_righe(righe, oggi: date) -> dict[int, Fornitore]:
    """Righe (dict) dell'anagrafica, da Excel o da Postgres → versione valida alla data `oggi`."""
    out: dict[int, Fornitore] = {}

    def opt(x, tipo):
        if x is None or (isinstance(x, float) and math.isnan(x)) or (isinstance(x, str) and not x.strip()):
            return None
        return tipo(x)

    for r in righe:
        cod = opt(r.get("cod_fornitore"), int)
        if cod is None:
            continue
        dal = _d(r.get("valido_dal")) or date.min
        if dal > oggi:
            continue
        if cod in out and (out[cod].valido_dal or date.min) > dal:
            continue
        out[cod] = Fornitore(
            cod_fornitore=cod, ragione_sociale=str(r.get("ragione_sociale") or ""),
            in_elenco_acquisto=str(r.get("in_elenco_acquisto") or "N").strip().upper() in ("S", "SI", "SÌ", "Y", "1", "TRUE"),
            classe_priorita=opt(r.get("classe_priorita"), int),
            scaduto_max_tollerato=opt(r.get("scaduto_max_tollerato"), float),
            slittamento_max_gg=opt(r.get("slittamento_max_gg"), int),
            pagamento_minimo=opt(r.get("pagamento_minimo"), float),
            rilevanza=opt(r.get("rilevanza"), int),
            note=str(r.get("note") or "") if not (isinstance(r.get("note"), float)) else "",
            valido_dal=None if dal == date.min else dal)
    return out


STATI_ACCORDO = {"attivo": "attivo", "attiva": "attivo", "validato": "attivo", "approvato": "attivo",
                 "proposta": "proposta", "proposto": "proposta",
                 "rifiutato": "rifiutato", "rifiutata": "rifiutato", "respinto": "rifiutato"}


def carica_accordi(path: Path, foglio: str, oggi: date, criterio_default: str = "data_doc"
                   ) -> tuple[dict[int, Accordo], dict[int, Accordo], list[Accordo]]:
    """Restituisce (accordi attivi, proposte in attesa di validazione, accordi ignorati).
    Stato vuoto = attivo; 'Proposta' = non applicata nel piano ufficiale."""
    if not path or not Path(path).exists():
        return {}, {}, []
    try:
        df = pd.read_excel(path, sheet_name=foglio)
    except ValueError:  # foglio assente
        return {}, {}, []
    return accordi_da_righe(df.to_dict("records"), oggi, criterio_default)


def accordi_da_righe(righe, oggi: date, criterio_default: str = "data_doc"
                     ) -> tuple[dict[int, Accordo], dict[int, Accordo], list[Accordo]]:
    """Righe (dict) degli accordi, da Excel o da Postgres → (attivi, proposte, ignorati)."""
    attivi: dict[int, Accordo] = {}
    proposte: dict[int, Accordo] = {}
    ignorati: list[Accordo] = []
    for r in righe:
        cod = r.get("cod_fornitore")
        dec = _d(r.get("decorrenza"))
        if cod is None or (isinstance(cod, float) and math.isnan(cod)) or dec is None:
            continue
        crit = r.get("criterio")
        crit = str(crit).strip() if isinstance(crit, str) and crit.strip() else criterio_default
        if crit not in ("data_doc", "scadenza_originale"):
            raise ValueError(f"Accordo fornitore {int(cod)}: criterio '{crit}' non valido")
        tipo = r.get("tipo")
        tipo = tipo.strip() if isinstance(tipo, str) and tipo.strip() else "congelamento_scaduto"
        nome = r.get("ragione_sociale")
        a = Accordo(cod_fornitore=int(cod), decorrenza=dec, tipo=tipo, criterio=crit,
                    valido_fino=_d(r.get("valido_fino")),
                    ragione_sociale=nome.strip() if isinstance(nome, str) else "",
                    note=r.get("note") if isinstance(r.get("note"), str) else "",
                    data_pagamento_scaduto=_d(r.get("data_pagamento_scaduto")))
        st = r.get("stato")
        st = st.strip().lower() if isinstance(st, str) and st.strip() else "attivo"
        if st not in STATI_ACCORDO:
            raise ValueError(f"Accordo fornitore {a.cod_fornitore}: stato '{r.get('stato')}' non valido "
                             "(ammessi: Attivo, Proposta, Rifiutato)")
        a.stato = STATI_ACCORDO[st]
        if a.tipo != "congelamento_scaduto":
            raise ValueError(f"Accordo fornitore {a.cod_fornitore}: tipo '{a.tipo}' non gestito")
        if a.stato == "rifiutato" or (a.stato == "attivo" and a.decorrenza > oggi) \
                or (a.valido_fino and a.valido_fino < oggi):
            ignorati.append(a)
            continue
        dest = attivi if a.stato == "attivo" else proposte
        if a.cod_fornitore in dest and dest[a.cod_fornitore].decorrenza > a.decorrenza:
            continue
        dest[a.cod_fornitore] = a
    # un fornitore con accordo attivo non ha bisogno della proposta
    for cod in list(proposte):
        if cod in attivi:
            ignorati.append(proposte.pop(cod))
    return attivi, proposte, ignorati


def trova_input(percorso: Path, pattern: str) -> Path:
    percorso = Path(percorso)
    if percorso.is_file():
        return percorso
    candidati = sorted(percorso.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    candidati = [c for c in candidati if not c.name.startswith("~$")]
    if not candidati:
        raise FileNotFoundError(f"Nessun file '{pattern}' in {percorso}")
    return candidati[0]
