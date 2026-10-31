"""Console della tesoreria: anagrafica fornitori, accordi, consultazione dei run, simulazioni.

Avvio:  uvicorn console.app:app --host 0.0.0.0 --port 8080   (dalla cartella del pacchetto)
Tutte le modifiche passano da tesoriere.anagrafica_db: storico a versioni + audit, come per l'agente.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Undefined
from starlette.middleware.sessions import SessionMiddleware

from . import auth, config, db

sys.path.insert(0, str(config.SCRIPTS))
from tesoriere import anagrafica_db as adb  # noqa: E402

BASE = Path(__file__).resolve().parent
app = FastAPI(title="Console Tesoreria", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=config.SEGRETO or "non-configurata",
                   session_cookie="tes_console", max_age=int(config.DURATA_SESSIONE_ORE * 3600),
                   same_site="strict", https_only=config.SOLO_HTTPS)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
T = Jinja2Templates(directory=BASE / "templates")

ERRORI_CONFIG = config.verifica()

CAMPI_FORNITORE = {
    "in_elenco_acquisto": "Elenco acquisto (S/N)",
    "classe_priorita": "Classe (0-5)",
    "rilevanza": "Rilevanza (1-3)",
    "scaduto_max_tollerato": "Scaduto max tollerato €",
    "slittamento_max_gg": "Slittamento max (gg)",
    "pagamento_minimo": "Pagamento minimo €",
    "note": "Note",
}
CAMPI_ACCORDO = {
    "stato": "Stato", "criterio": "Criterio", "valido_fino": "Valido fino",
    "data_pagamento_scaduto": "Pagamento scaduto", "note": "Note", "ragione_sociale": "Ragione sociale",
}
NON_PAGATE = ("non_collocabile", "tagliata_non_collocabile", "non_collocabile_invariata", "fuori_orizzonte",
              "congelata_accordo", "sospesa", "importo_zero", "nota_credito_non_compensata", "congelata")


# ------------------------------------------------------------------ formati

def eur(x, dec: int = 0) -> str:
    if x is None or isinstance(x, Undefined) or x == "":
        return ""
    s = f"{float(x):,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s


def gg(d) -> str:
    if not d or isinstance(d, Undefined):
        return ""
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    return d.strftime("%d/%m/%Y")


def sn(v) -> str:
    if isinstance(v, Undefined):
        return ""
    return "S" if v is True else ("N" if v is False else ("" if v is None else str(v)))


def val(v) -> str:
    """Valore leggibile per anteprima e registro: S/N, importi all'italiana, date gg/mm/aaaa."""
    if v is None or isinstance(v, Undefined) or v == "":
        return "—"
    if isinstance(v, bool):
        return sn(v)
    if isinstance(v, (int, float)):
        return eur(v, 0 if float(v).is_integer() else 2)
    if isinstance(v, (date, datetime)) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v)):
        return gg(v)
    return str(v)


T.env.filters.update(eur=eur, gg=gg, sn=sn, val=val)
T.env.globals.update(CAMPI_FORNITORE=CAMPI_FORNITORE, CAMPI_ACCORDO=CAMPI_ACCORDO)


# ------------------------------------------------------------------ sicurezza

@app.middleware("http")
async def intestazioni(request: Request, call_next):
    r = await call_next(request)
    r.headers["X-Frame-Options"] = "DENY"
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "same-origin"
    r.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; style-src 'self'; "
                                            "script-src 'self'; form-action 'self'; frame-ancestors 'none'")
    r.headers["Cache-Control"] = "no-store"
    return r


def utente(request: Request) -> dict | None:
    u = request.session.get("utente")
    if not u or time.time() - u.get("dal", 0) > config.DURATA_SESSIONE_ORE * 3600:
        return None
    return u


def pagina(request: Request, nome: str, **ctx) -> HTMLResponse:
    return T.TemplateResponse(request, nome, {"utente": utente(request), "oggi": date.today(), **ctx})


def richiede_login(request: Request):
    if ERRORI_CONFIG:
        return HTMLResponse("<h1>Console non configurata</h1><ul>" +
                            "".join(f"<li>{e}</li>" for e in ERRORI_CONFIG) + "</ul>", status_code=500)
    if not utente(request):
        return RedirectResponse("/login", status_code=303)
    return None


def controlla_csrf(request: Request, token: str | None) -> Response | None:
    if not auth.csrf_ok(utente(request) or {}, token):
        return HTMLResponse("Richiesta non valida (sessione scaduta?). Ricarica la pagina.", status_code=400)
    return None


# ------------------------------------------------------------------ login

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return pagina(request, "login.html", errore=None, errori_config=ERRORI_CONFIG)


@app.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form("")):
    ip = request.client.host if request.client else "?"
    if auth.bloccato(username, ip):
        return pagina(request, "login.html", errore="Troppi tentativi: riprova tra 15 minuti.", errori_config=[])
    u = auth.verifica_credenziali(username, password, ip)
    if not u:
        return pagina(request, "login.html", errore="Credenziali non valide o utente non abilitato.", errori_config=[])
    request.session.clear()
    request.session["utente"] = u
    adb_audit("accesso_console", {"username": u["username"], "ip": ip}, auth.autore(u))
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf: str = Form(None)):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


def adb_audit(evento: str, dettaglio: dict, autore: str) -> None:
    with adb.connetti(config.DSN, config.SCHEMA) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO tes_audit (evento, dettaglio, autore) VALUES (%s, %s, %s)",
                    (evento, json.dumps(dettaglio, default=str, ensure_ascii=False), autore))


# ------------------------------------------------------------------ cruscotto

def ultimo_ufficiale() -> dict | None:
    return db.uno("""SELECT * FROM tes_run WHERE tipo_run='ufficiale' AND esito='ok'
                     ORDER BY avviato_il DESC LIMIT 1""")


def kpi(run: dict | None) -> dict:
    if not run or not run.get("riepilogo_json"):
        return {}
    r = run["riepilogo_json"]
    f, a, pt = r.get("fabbisogno") or {}, r.get("allerta") or {}, r.get("piano_taglio") or {}
    return {"livello": a.get("livello"), "picco": f.get("picco"), "data_picco": f.get("data_picco"),
            "primo_scoperto": a.get("primo_giorno_oltre_fido"), "margine_min": a.get("margine_minimo"),
            "data_margine_min": a.get("data_margine_minimo"), "scoperto_regole": pt.get("scoperto_piano_regole"),
            "tagli_n": pt.get("tagli_n"), "tagli_importo": pt.get("tagli_importo"),
            "nc_n": f.get("non_collocabili_n"), "nc_importo": f.get("non_collocabili_importo"),
            "consuntivo": r.get("data_consuntivo"), "strategici": a.get("fornitori_strategici_tagliati") or [],
            "accordi_ritardo": a.get("accordi_con_corrente_in_ritardo") or [],
            "spostamenti": (r.get("spostamenti") or {}).get("n_righe"),
            "acc_dal": (pt.get("dopo_tagli") or {}).get("primo_giorno_oltre_fido"),
            "acc_scoperto": (pt.get("dopo_tagli") or {}).get("scoperto_massimo"),
            "acc_data_max": (pt.get("dopo_tagli") or {}).get("data_scoperto_massimo")}


def serie_margine(run_id) -> list[dict]:
    return db.tutti("""SELECT data, saldo_post - limite AS margine FROM tes_saldo_giornaliero
                       WHERE run_id = %s ORDER BY data""", (run_id,))


@app.get("/", response_class=HTMLResponse)
def cruscotto(request: Request):
    if (r := richiede_login(request)):
        return r
    run = ultimo_ufficiale()
    runs = db.tutti("""SELECT run_id, tipo_run, avviato_il, avviato_da, esito, data_consuntivo, fabbisogno_picco,
                              stato_approvazione FROM tes_run ORDER BY avviato_il DESC LIMIT 15""")
    serie = serie_margine(run["run_id"]) if run else []
    soglia = ((run or {}).get("riepilogo_json") or {}).get("allerta", {}).get("soglia_margine", 0)
    grafico = json.dumps({"etichette": [gg(x["data"])[:5] for x in serie],
                          "valori": [round(x["margine"]) for x in serie], "soglia": soglia})
    return pagina(request, "cruscotto.html", run=run, k=kpi(run), runs=runs, grafico=grafico)


@app.get("/run/{run_id}", response_class=HTMLResponse)
def dettaglio_run(request: Request, run_id: str):
    if (r := richiede_login(request)):
        return r
    run = db.uno("SELECT * FROM tes_run WHERE run_id::text = %s", (run_id,))
    if not run:
        return HTMLResponse("Run non trovato", status_code=404)
    tagli = db.tutti("""SELECT cod_fornitore, max(ragione_sociale) AS fornitore, count(*) AS n,
                               sum(importo_residuo) AS importo,
                               count(*) FILTER (WHERE regola = 'tagliata_non_collocabile') AS n_nc
                        FROM tes_proposta_riga WHERE run_id = %s AND regola LIKE 'tagliata%%'
                        GROUP BY cod_fornitore ORDER BY importo DESC""", (run["run_id"],))
    nc = db.tutti("""SELECT cod_fornitore, max(ragione_sociale) AS fornitore, max(classe) AS classe, count(*) AS n,
                            sum(importo_residuo) AS importo
                     FROM tes_proposta_riga WHERE run_id = %s AND regola = 'non_collocabile'
                     GROUP BY cod_fornitore ORDER BY importo DESC LIMIT 50""", (run["run_id"],))
    serie = serie_margine(run["run_id"])
    soglia = (run.get("riepilogo_json") or {}).get("allerta", {}).get("soglia_margine", 0)
    grafico = json.dumps({"etichette": [gg(x["data"])[:5] for x in serie],
                          "valori": [round(x["margine"]) for x in serie], "soglia": soglia})
    controlli = (run.get("riepilogo_json") or {}).get("controlli") or []
    return pagina(request, "run.html", run=run, k=kpi(run), tagli=tagli, nc=nc, grafico=grafico,
                  controlli=[c for c in controlli if c.get("esito") != "ok"])


@app.get("/run/{run_id}/excel")
def scarica_excel(request: Request, run_id: str):
    if (r := richiede_login(request)):
        return r
    run = db.uno("SELECT file_output FROM tes_run WHERE run_id::text = %s", (run_id,))
    p = Path((run or {}).get("file_output") or "")
    try:
        p.resolve().relative_to(config.DATI.resolve())
    except ValueError:
        return HTMLResponse("File non disponibile", status_code=404)
    if not p.is_file():
        return HTMLResponse("File non più presente sul server", status_code=404)
    return FileResponse(p, filename=p.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ------------------------------------------------------------------ fornitori

def fornitori_correnti(oggi: date) -> list[dict]:
    with adb.connetti(config.DSN, config.SCHEMA) as conn, conn.cursor() as cur:
        righe = adb.fornitori_validi(cur, oggi)
    run = ultimo_ufficiale()
    if run:
        # debito = tutte le scadenze del fornitore nello scadenzario del run (anche oltre l'orizzonte,
        # sospese e scaduto congelato), al netto delle note di credito; scaduto = scadenza da documento passata
        data_run = (run.get("riepilogo_json") or {}).get("data_run") or run["avviato_il"].date().isoformat()
        agg = {x["cod_fornitore"]: x for x in db.tutti(
            """WITH c AS (
                 SELECT DISTINCT ON (cod_fornitore) cod_fornitore, conto AS conto_prevalente
                 FROM (SELECT cod_fornitore, conto, sum(abs(importo_residuo)) AS peso FROM tes_proposta_riga
                       WHERE run_id = %(r)s AND conto IS NOT NULL GROUP BY 1, 2) x
                 ORDER BY cod_fornitore, peso DESC)
               SELECT p.cod_fornitore, max(p.ragione_sociale) AS nome_run, max(c.conto_prevalente) AS conto_prevalente,
                      sum(importo_residuo) AS debito_totale,
                      sum(importo_residuo) FILTER (WHERE scad_originale < %(d)s::date) AS scaduto,
                      sum(importo_residuo) FILTER (WHERE importo_residuo > 0 AND nuova_data IS NOT NULL) AS in_orizzonte,
                      sum(importo_residuo) FILTER (WHERE regola LIKE 'tagliata%%') AS tagliato,
                      sum(importo_residuo) FILTER (WHERE regola IN ('non_collocabile','tagliata_non_collocabile')) AS parcheggiato
               FROM tes_proposta_riga p LEFT JOIN c USING (cod_fornitore)
               WHERE p.run_id = %(r)s GROUP BY p.cod_fornitore""", {"r": run["run_id"], "d": data_run})}
        for f in righe:
            f.update({k: v for k, v in agg.get(f["cod_fornitore"], {}).items() if k != "cod_fornitore"})
        # fornitori presenti nello scadenzario ma non in anagrafica (il motore li tratta con la classe di default)
        noti = {f["cod_fornitore"] for f in righe}
        for cod, x in agg.items():
            if cod not in noti:
                righe.append({**{k: None for k in ("in_elenco_acquisto", "classe_priorita", "rilevanza",
                                                   "scaduto_max_tollerato", "slittamento_max_gg",
                                                   "pagamento_minimo", "note", "valido_dal")},
                              **{k: v for k, v in x.items()}, "ragione_sociale": x["nome_run"], "nuovo": True})
    return righe


def nome_da_run(cod: int) -> str | None:
    run = ultimo_ufficiale()
    r = db.uno("""SELECT max(ragione_sociale) AS n FROM tes_proposta_riga WHERE run_id = %s AND cod_fornitore = %s""",
               (run["run_id"], cod)) if run else None
    return (r or {}).get("n")


@app.get("/fornitori", response_class=HTMLResponse)
def fornitori(request: Request, q: str = "", elenco: str = "", classe: str = "", rilevanza: str = "",
              conto: str = "", ordine: str = "debito_totale"):
    if (r := richiede_login(request)):
        return r
    righe_tutte = fornitori_correnti(date.today())
    righe = list(righe_tutte)
    if q:
        righe = [f for f in righe if q.lower() in (f["ragione_sociale"] or "").lower() or q == str(f["cod_fornitore"])]
    if elenco in ("S", "N"):
        righe = [f for f in righe if bool(f["in_elenco_acquisto"]) == (elenco == "S")]
    if classe != "":
        righe = [f for f in righe if str(f["classe_priorita"] if f["classe_priorita"] is not None else "") == classe]
    if rilevanza != "":
        righe = [f for f in righe if str(f["rilevanza"] or "") == rilevanza]
    tutti_conti = sorted({f.get("conto_prevalente") or "" for f in righe_tutte} - {""})
    if conto:
        righe = [f for f in righe if (f.get("conto_prevalente") or "") == conto]
    totale = sum(f.get("debito_totale") or 0 for f in righe)
    chiave = {"nome": lambda f: (f["ragione_sociale"] or "").lower()}.get(
        ordine, lambda f: -(f.get(ordine) or 0))
    righe.sort(key=chiave)
    righe.sort(key=lambda f: bool(f.get("nuovo")))          # i fornitori non in anagrafica in coda
    n_nuovi = sum(1 for f in righe if f.get("nuovo"))
    return pagina(request, "fornitori.html", righe=righe, conti=tutti_conti, totale=totale, n_nuovi=n_nuovi,
                  filtri=dict(q=q, elenco=elenco, classe=classe, rilevanza=rilevanza, conto=conto, ordine=ordine))


@app.get("/fornitori/{cod}", response_class=HTMLResponse)
def fornitore(request: Request, cod: int):
    if (r := richiede_login(request)):
        return r
    with adb.connetti(config.DSN, config.SCHEMA) as conn, conn.cursor() as cur:
        att = adb.versione_corrente(cur, cod)
        storico = adb.storico_fornitore(cur, cod)
    nuovo = att is None
    if nuovo:
        nome = nome_da_run(cod)
        if not nome:
            return HTMLResponse("Fornitore non trovato", status_code=404)
        att = {"cod_fornitore": cod, "ragione_sociale": nome, "in_elenco_acquisto": False, "classe_priorita": None,
               "rilevanza": None, "scaduto_max_tollerato": None, "slittamento_max_gg": None,
               "pagamento_minimo": None, "note": None, "valido_dal": None, "modificato_da": None}
    run = ultimo_ufficiale()
    partite = db.tutti("""SELECT id_scadenza, num_protocollo, importo_residuo, scad_originale, scadenza_corrente,
                                 nuova_data, regola, motivo, classe, origine_classe, conto
                          FROM tes_proposta_riga WHERE run_id = %s AND cod_fornitore = %s
                          ORDER BY coalesce(nuova_data, scadenza_corrente)""",
                       (run["run_id"], cod)) if run else []
    pesi: dict[str, float] = {}
    for p in partite:
        if p.get("conto"):
            pesi[p["conto"]] = pesi.get(p["conto"], 0) + abs(p["importo_residuo"] or 0)
    data_run = ((run or {}).get("riepilogo_json") or {}).get("data_run")
    data_run = date.fromisoformat(data_run[:10]) if data_run else date.today()
    sintesi_f = {"debito": sum(p["importo_residuo"] or 0 for p in partite),
                 "scaduto": sum(p["importo_residuo"] or 0 for p in partite if p["scad_originale"] < data_run),
                 "conto": max(pesi, key=pesi.get) if pesi else None}
    return pagina(request, "fornitore.html", f=att, storico=storico, partite=partite, run=run, sf=sintesi_f,
                  nuovo=nuovo)


def _anteprima_fornitori(modifiche: list[dict]) -> list[dict]:
    out = []
    with adb.connetti(config.DSN, config.SCHEMA) as conn, conn.cursor() as cur:
        for m in modifiche:
            e = adb.modifica_fornitore(cur, m["cod"], m["valori"], "anteprima", "anteprima", date.today(), False)
            e["ragione_sociale"] = e["dopo"].get("ragione_sociale")
            out.append(e)
        conn.rollback()
    return out


@app.post("/fornitori/anteprima", response_class=HTMLResponse)
async def fornitori_anteprima(request: Request):
    if (r := richiede_login(request)):
        return r
    form = await request.form()
    if (r := controlla_csrf(request, form.get("csrf"))):
        return r
    try:
        if form.get("modo") == "blocco":
            cods = [int(x) for x in form.getlist("sel")]
            campo, valore = form.get("campo"), form.get("valore", "")
            if not cods:
                raise ValueError("nessun fornitore selezionato")
            if campo not in CAMPI_FORNITORE:
                raise ValueError("campo non valido")
            modifiche = [{"cod": c, "valori": {campo: valore}} for c in cods]
        else:
            cod = int(form.get("cod"))
            valori = {k: form.get(k, "") for k in CAMPI_FORNITORE if k in form}
            modifiche = [{"cod": cod, "valori": valori}]
        # fornitore non ancora in anagrafica: si crea con la ragione sociale dello scadenzario
        with adb.connetti(config.DSN, config.SCHEMA) as conn, conn.cursor() as cur:
            for m in modifiche:
                if adb.versione_corrente(cur, m["cod"]) is None:
                    nome = nome_da_run(m["cod"])
                    if not nome:
                        raise ValueError(f"fornitore {m['cod']} non trovato né in anagrafica né nello scadenzario")
                    m["valori"]["ragione_sociale"] = nome
        esiti = _anteprima_fornitori(modifiche)
    except ValueError as exc:
        return pagina(request, "errore.html", messaggio=str(exc))
    esiti = [e for e in esiti if e["cambiati"]]
    return pagina(request, "anteprima.html", tipo="fornitori", esiti=esiti,
                  payload=json.dumps(modifiche), etichette={**CAMPI_FORNITORE, "ragione_sociale": "Ragione sociale"})


@app.post("/fornitori/conferma")
def fornitori_conferma(request: Request, csrf: str = Form(None), payload: str = Form(...),
                       motivo: str = Form(""), dal: str = Form("")):
    if (r := richiede_login(request)):
        return r
    if (r := controlla_csrf(request, csrf)):
        return r
    if not motivo.strip():
        return pagina(request, "errore.html", messaggio="Il motivo è obbligatorio.")
    u = utente(request)
    decorrenza = date.fromisoformat(dal) if dal else date.today()
    try:
        modifiche = json.loads(payload)
        with adb.connetti(config.DSN, config.SCHEMA) as conn, conn.cursor() as cur:   # una sola transazione
            n = sum(adb.modifica_fornitore(cur, int(m["cod"]), m["valori"], auth.autore(u), motivo.strip(),
                                           decorrenza, True)["scritto"] for m in modifiche)
    except (ValueError, KeyError) as exc:
        return pagina(request, "errore.html", messaggio=str(exc))
    return pagina(request, "fatto.html", messaggio=f"Modifiche registrate: {n} fornitori.",
                  indietro="/fornitori")


# ------------------------------------------------------------------ accordi

@app.get("/accordi", response_class=HTMLResponse)
def accordi(request: Request, stato: str = ""):
    if (r := richiede_login(request)):
        return r
    with adb.connetti(config.DSN, config.SCHEMA) as conn, conn.cursor() as cur:
        righe = adb.accordi_tutti(cur)
    run = ultimo_ufficiale()
    riep = {x["cod_fornitore"]: x for x in ((run or {}).get("riepilogo_json") or {}).get("accordi") or []}
    for a in righe:
        a["riep"] = riep.get(a["cod_fornitore"], {})
    if stato:
        righe = [a for a in righe if a["stato"] == stato]
    return pagina(request, "accordi.html", righe=righe, stato=stato)


@app.get("/accordi/nuovo", response_class=HTMLResponse)
def accordo_nuovo(request: Request):
    if (r := richiede_login(request)):
        return r
    return pagina(request, "accordo.html", a=None)


@app.get("/accordi/{cod}/{decorrenza}", response_class=HTMLResponse)
def accordo(request: Request, cod: int, decorrenza: str):
    if (r := richiede_login(request)):
        return r
    with adb.connetti(config.DSN, config.SCHEMA) as conn, conn.cursor() as cur:
        righe = [a for a in adb.accordi_tutti(cur) if a["cod_fornitore"] == cod and str(a["decorrenza"]) == decorrenza]
    if not righe:
        return HTMLResponse("Accordo non trovato", status_code=404)
    return pagina(request, "accordo.html", a=righe[0])


@app.post("/accordi/anteprima", response_class=HTMLResponse)
async def accordo_anteprima(request: Request):
    if (r := richiede_login(request)):
        return r
    form = await request.form()
    if (r := controlla_csrf(request, form.get("csrf"))):
        return r
    try:
        cod, dec = int(form.get("cod")), date.fromisoformat(form.get("decorrenza"))
        nuovo = form.get("nuovo") == "1"
        valori = {k: form.get(k, "") for k in CAMPI_ACCORDO if k in form}
        with adb.connetti(config.DSN, config.SCHEMA) as conn, conn.cursor() as cur:
            e = adb.modifica_accordo(cur, cod, dec, valori, "anteprima", "anteprima", False, nuovo)
            conn.rollback()
    except (ValueError, TypeError) as exc:
        return pagina(request, "errore.html", messaggio=str(exc))
    e["ragione_sociale"] = e["dopo"].get("ragione_sociale")
    payload = json.dumps({"cod": cod, "decorrenza": str(dec), "nuovo": nuovo, "valori": valori})
    return pagina(request, "anteprima.html", tipo="accordi", esiti=[e] if e["cambiati"] else [],
                  payload=payload, etichette=CAMPI_ACCORDO)


@app.post("/accordi/conferma")
def accordo_conferma(request: Request, csrf: str = Form(None), payload: str = Form(...), motivo: str = Form(""),
                     dal: str = Form("")):
    if (r := richiede_login(request)):
        return r
    if (r := controlla_csrf(request, csrf)):
        return r
    if not motivo.strip():
        return pagina(request, "errore.html", messaggio="Il motivo è obbligatorio.")
    u = utente(request)
    try:
        m = json.loads(payload)
        with adb.connetti(config.DSN, config.SCHEMA) as conn, conn.cursor() as cur:
            adb.modifica_accordo(cur, int(m["cod"]), date.fromisoformat(m["decorrenza"]), m["valori"],
                                 auth.autore(u), motivo.strip(), True, bool(m.get("nuovo")))
    except (ValueError, KeyError) as exc:
        return pagina(request, "errore.html", messaggio=str(exc))
    return pagina(request, "fatto.html", messaggio="Accordo registrato.", indietro="/accordi")


# ------------------------------------------------------------------ registro

@app.get("/registro", response_class=HTMLResponse)
def registro(request: Request, tipo: str = "", chi: str = ""):
    if (r := richiede_login(request)):
        return r
    eventi = {"": ("anagrafica_modificata", "accordo_modificato", "accesso_console"),
              "fornitori": ("anagrafica_modificata",), "accordi": ("accordo_modificato",),
              "accessi": ("accesso_console",)}.get(tipo, ("anagrafica_modificata", "accordo_modificato"))
    righe = db.tutti("""SELECT quando, evento, autore, dettaglio FROM tes_audit
                        WHERE evento = ANY(%s) AND (%s = '' OR autore ILIKE %s)
                        AND coalesce(dettaglio->>'motivo', '') <> 'import iniziale da Excel'
                        ORDER BY quando DESC LIMIT 300""", (list(eventi), chi, f"%{chi}%"))
    return pagina(request, "registro.html", righe=righe, tipo=tipo, chi=chi)


# ------------------------------------------------------------------ simulazioni

CAMPI_SIM = {"rilevanza": "rilevanza", "classe": "classe", "elenco": "elenco (S/N)",
             "slittamento_max": "slittamento max gg", "scaduto_max": "scaduto max €", "stato_accordo": "stato accordo"}


def reports_archiviati() -> list[dict]:
    out = []
    for p in sorted(config.ARCHIVIO.glob("*.xlsx"), reverse=True)[:30]:
        m = re.match(r"(\d{8})_", p.name)
        out.append({"nome": p.name, "data": datetime.strptime(m.group(1), "%Y%m%d").date() if m else None})
    return out


@app.get("/simulazioni", response_class=HTMLResponse)
def simulazioni(request: Request):
    if (r := richiede_login(request)):
        return r
    with adb.connetti(config.DSN, config.SCHEMA) as conn, conn.cursor() as cur:
        forn = adb.fornitori_validi(cur, date.today())
    recenti = db.tutti("""SELECT run_id, avviato_il, avviato_da, esito, fabbisogno_picco, override_json
                          FROM tes_run WHERE tipo_run = 'simulazione' ORDER BY avviato_il DESC LIMIT 20""")
    for x in recenti:
        x["ov"] = leggibili(x.get("override_json") or [])
    return pagina(request, "simulazioni.html", reports=reports_archiviati(), fornitori=forn, recenti=recenti,
                  campi=CAMPI_SIM, righe_override=range(8))


@app.post("/simulazioni", response_class=HTMLResponse)
async def simula(request: Request):
    if (r := richiede_login(request)):
        return r
    form = await request.form()
    if (r := controlla_csrf(request, form.get("csrf"))):
        return r
    u = utente(request)
    validi = {x["nome"]: x for x in reports_archiviati()}
    rep = validi.get(form.get("report", ""))
    if not rep:
        return pagina(request, "errore.html", messaggio="Report non trovato nell'archivio.")
    override = []
    try:
        for i in range(8):
            cod, campo, val = form.get(f"cod_{i}", "").strip(), form.get(f"campo_{i}", ""), form.get(f"val_{i}", "").strip()
            if not cod and not val:
                continue
            cod = int(re.search(r"\d+", cod).group(0)) if re.search(r"\d+", cod) else None
            if cod is None or campo not in CAMPI_SIM or not val:
                raise ValueError(f"riga {i + 1}: indicare fornitore, campo e valore")
            if campo in ("rilevanza", "classe", "slittamento_max"):
                int(val)
            if campo == "scaduto_max":
                val = str(float(val.replace(".", "").replace(",", ".")))
            override.append(f"{campo}:{cod}={val}")
        for nome in ("fido", "lookahead"):
            v = form.get(nome, "").strip().replace(".", "")
            if v:
                override.append(f"{nome}={int(v)}")
    except (ValueError, AttributeError) as exc:
        return pagina(request, "errore.html", messaggio=f"Parametri non validi: {exc}")
    if not override:
        return pagina(request, "errore.html", messaggio="Nessuna modifica indicata: la simulazione sarebbe uguale al piano ufficiale.")
    cartella = config.SIM / re.sub(r"[^a-z0-9_.-]", "_", u["username"].lower())
    cartella.mkdir(parents=True, exist_ok=True)
    js = cartella / f"sim_{int(time.time())}.json"
    cmd = [sys.executable, str(config.SCRIPTS / "riscadenzia.py"), "--regole", str(config.REGOLE),
           "--fonti", str(config.FONTI), "--input", str(config.ARCHIVIO / rep["nome"]), "--out", str(cartella),
           "--json", str(js), "--simula", "--autore", auth.autore(u)]
    if rep["data"]:
        cmd += ["--oggi", rep["data"].isoformat()]
    for o in override:
        cmd += ["--override", o]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(config.SCRIPTS))
    except subprocess.TimeoutExpired:
        return pagina(request, "errore.html", messaggio="La simulazione ha superato i 5 minuti.")
    if not js.exists():
        return pagina(request, "errore.html", messaggio="Errore del motore: " + (p.stderr or p.stdout)[-800:])
    esito = json.loads(js.read_text(encoding="utf-8"))
    if esito.get("esito") != "ok":
        return pagina(request, "errore.html", messaggio="Simulazione non riuscita: " +
                      (esito.get("errore") or "; ".join(c["messaggio"] for c in esito.get("controlli", []) if c["esito"] == "ko")))
    return RedirectResponse(f"/simulazioni/{esito['run_id']}", status_code=303)


@app.get("/simulazioni/{run_id}", response_class=HTMLResponse)
def risultato_simulazione(request: Request, run_id: str):
    if (r := richiede_login(request)):
        return r
    sim = db.uno("SELECT * FROM tes_run WHERE run_id::text = %s AND tipo_run = 'simulazione'", (run_id,))
    if not sim:
        return HTMLResponse("Simulazione non trovata", status_code=404)
    uff = db.uno("""SELECT * FROM tes_run WHERE tipo_run = 'ufficiale' AND esito = 'ok' AND hash_input = %s
                    ORDER BY avviato_il DESC LIMIT 1""", (sim["hash_input"],))
    confronto = []
    if uff:
        confronto = db.tutti("""
            WITH p AS (
              SELECT run_id, cod_fornitore, max(ragione_sociale) AS fornitore,
                     sum(importo_residuo) FILTER (WHERE importo_residuo > 0 AND nuova_data IS NOT NULL
                                                  AND regola <> ALL(%s)) AS pagato,
                     sum(importo_residuo) FILTER (WHERE regola LIKE 'tagliata%%') AS tagliato
              FROM tes_proposta_riga WHERE run_id IN (%s, %s) GROUP BY run_id, cod_fornitore)
            SELECT coalesce(s.cod_fornitore, u.cod_fornitore) AS cod_fornitore,
                   coalesce(s.fornitore, u.fornitore) AS fornitore,
                   coalesce(u.pagato, 0) AS pagato_uff, coalesce(s.pagato, 0) AS pagato_sim,
                   coalesce(s.pagato, 0) - coalesce(u.pagato, 0) AS delta,
                   coalesce(u.tagliato, 0) AS tagliato_uff, coalesce(s.tagliato, 0) AS tagliato_sim
            FROM (SELECT * FROM p WHERE run_id = %s) s FULL JOIN (SELECT * FROM p WHERE run_id = %s) u
                 USING (cod_fornitore)
            WHERE abs(coalesce(s.pagato, 0) - coalesce(u.pagato, 0)) >= 1
               OR abs(coalesce(s.tagliato, 0) - coalesce(u.tagliato, 0)) >= 1
            ORDER BY abs(coalesce(s.pagato, 0) - coalesce(u.pagato, 0)) DESC LIMIT 60""",
                             (list(NON_PAGATE), sim["run_id"], uff["run_id"], sim["run_id"], uff["run_id"]))
    # modifiche ad anagrafica e accordi fatte dopo il piano ufficiale: entrano nella simulazione
    # (che legge l'anagrafica di oggi) e vanno mostrate, altrimenti il confronto si legge male
    dopo = db.tutti("""SELECT quando, autore, dettaglio FROM tes_audit
                       WHERE evento IN ('anagrafica_modificata', 'accordo_modificato')
                         AND quando > %s AND quando < %s ORDER BY quando""",
                    (uff["avviato_il"], sim["avviato_il"])) if uff else []
    serie_s, serie_u = serie_margine(sim["run_id"]), (serie_margine(uff["run_id"]) if uff else [])
    mu = {x["data"]: x["margine"] for x in serie_u}
    grafico = json.dumps({"etichette": [gg(x["data"])[:5] for x in serie_s],
                          "valori": [round(x["margine"]) for x in serie_s],
                          "valori2": [round(mu[x["data"]]) if x["data"] in mu else None for x in serie_s],
                          "soglia": (sim.get("riepilogo_json") or {}).get("allerta", {}).get("soglia_margine", 0)})
    return pagina(request, "simulazione.html", sim=sim, uff=uff, ks=kpi(sim), ku=kpi(uff), confronto=confronto,
                  grafico=grafico, override=leggibili(sim.get("override_json") or []), dopo=dopo)


def leggibili(override: list[str]) -> list[str]:
    """'rilevanza:101012=1' → 'QUALIFARMA SRL: rilevanza 1'."""
    nomi = {x["cod_fornitore"]: x["ragione_sociale"] for x in db.tutti(
        "SELECT cod_fornitore, ragione_sociale FROM tes_anagrafica_fornitori WHERE valido_al IS NULL")}
    out = []
    for o in override:
        chiave, _, v = o.partition("=")
        campo, _, cod = chiave.partition(":")
        if cod.isdigit():
            out.append(f"{nomi.get(int(cod), cod)}: {CAMPI_SIM.get(campo, campo)} {v}")
        else:
            out.append(f"{campo} {eur(v) if v.replace('.', '').isdigit() else v}")
    return out
