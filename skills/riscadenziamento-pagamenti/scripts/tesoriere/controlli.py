"""Controlli di coerenza sugli input. Esito: ok | avviso | ko (ko = bloccante)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .calendario import Calendario
from .modello import Partita, Previsione


def _eur(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


@dataclass
class Esito:
    id: str
    esito: str
    messaggio: str


def _cfg(fonti: dict, id_: str) -> dict:
    return next((c for c in fonti.get("controlli", []) if c["id"] == id_), {})


def esegui(file_input: Path, prev: Previsione, partite: list[Partita], fonti: dict, regole: dict,
           oggi: date, cal: Calendario, simulazione: bool, ora: datetime | None = None) -> list[Esito]:
    out: list[Esito] = []
    ora = ora or datetime.now()

    # 1. freschezza del file
    ore_max = float(fonti["previsione"].get("freschezza_max_ore", 18))
    eta = (ora - datetime.fromtimestamp(file_input.stat().st_mtime)).total_seconds() / 3600
    ko = _cfg(fonti, "file_aggiornato").get("esito_ko", "stop")
    if fonti["previsione"].get("modalita") == "allegato":
        out.append(Esito("file_aggiornato", "ok", "Modalità allegato: Report ricevuto in conversazione; "
                         "l'aggiornamento è verificato dalla data del consuntivo."))
    elif eta <= ore_max:
        out.append(Esito("file_aggiornato", "ok", f"File modificato {eta:.1f} ore fa."))
    else:
        out.append(Esito("file_aggiornato", "avviso" if simulazione or ko != "stop" else "ko",
                         f"File modificato {eta:.1f} ore fa (massimo {ore_max:.0f})."))

    # 2. ultimo giorno a consuntivo
    atteso = cal.precedente(oggi - timedelta(days=1))
    if prev.data_consuntivo >= atteso:
        out.append(Esito("data_consuntivo_recente", "ok", f"Consuntivo al {prev.data_consuntivo:%d/%m/%Y}."))
    else:
        out.append(Esito("data_consuntivo_recente", "avviso" if simulazione else "ko",
                         f"Consuntivo al {prev.data_consuntivo:%d/%m/%Y}, atteso almeno {atteso:%d/%m/%Y}: "
                         "incassi non ancora caricati?"))

    # 3. orizzonte coperto dalla previsione
    primo = cal.prossimo(max(prev.data_consuntivo + timedelta(days=1), oggi))
    fine = primo + timedelta(days=int(regole["orizzonte"]["giorni"]) - 1)
    ultima = max(prev.flussi_fissi) if prev.flussi_fissi else prev.data_consuntivo
    if ultima >= fine:
        out.append(Esito("orizzonte_coperto", "ok", f"Previsione fino al {ultima:%d/%m/%Y}."))
    else:
        out.append(Esito("orizzonte_coperto", "ko",
                         f"La previsione finisce il {ultima:%d/%m/%Y}, l'orizzonte arriva al {fine:%d/%m/%Y}: "
                         "estendere il Report o ridurre l'orizzonte."))

    # 4. chiavi di import
    non_imp = [p for p in partite if not p.importabile and p.importo > 0]
    if non_imp:
        out.append(Esito("chiavi_import", "avviso",
                         f"{len(non_imp)} partite ({_eur(sum(p.importo for p in non_imp))}) senza protocollo "
                         "o con chiave di import ambigua: se spostate vanno riscadenziate a mano."))
    else:
        out.append(Esito("chiavi_import", "ok", "Tutte le partite sono importabili."))

    # 4-bis. chiave della scadenza ("Protocollo" del tracciato di import)
    if any(c.get("da") == "id_scadenza" for c in fonti["output"]["import_erp"]["colonne"]):
        senza_id = [p for p in partite if p.importo > 0 and not p.id_scadenza]
        if len(senza_id) == len([p for p in partite if p.importo > 0]):
            out.append(Esito("chiave_scadenza", "avviso",
                             "Lo scadenzario non contiene il campo Protocollo (chiave della scadenza): "
                             "il foglio Import_ERP ha la colonna Protocollo vuota e non è importabile così."))
        elif senza_id:
            out.append(Esito("chiave_scadenza", "avviso",
                             f"{len(senza_id)} partite senza Protocollo: escluse da Import_ERP, da gestire a mano."))
        else:
            out.append(Esito("chiave_scadenza", "ok", "Tutte le partite hanno il Protocollo della scadenza."))

    # 5. coerenza tra cache del Report e scadenzario
    cfg = _cfg(fonti, "cache_coerente")
    tol = float(cfg.get("tolleranza_euro", 1.0))
    ricalc: dict[date, float] = {}
    for p in partite:
        if p.fonte == "scadenzario":
            ricalc[p.scad_corrente] = ricalc.get(p.scad_corrente, 0.0) - p.importo
    diff = [(d, prev.righe_fornitori_cache.get(d, 0.0) - ricalc.get(d, 0.0))
            for d in sorted(prev.righe_fornitori_cache) if d <= fine]
    diff = [(d, x) for d, x in diff if abs(x) > tol]
    if not diff:
        out.append(Esito("cache_coerente", "ok", "Righe fornitori del Report coerenti con lo scadenzario."))
    else:
        peggiori = sorted(diff, key=lambda t: -abs(t[1]))[:3]
        dett = "; ".join(f"{d:%d/%m} {_eur(x)}" for d, x in peggiori)
        out.append(Esito("cache_coerente", "ko" if cfg.get("esito_ko") == "stop" else "avviso",
                         f"{len(diff)} giorni con righe fornitori in cache diverse dallo scadenzario "
                         f"(es. {dett}). Il motore usa lo scadenzario; la previsione in Excel è da ricalcolare."))

    # 6. identità contabile del Report
    tol = float(_cfg(fonti, "totali_coerenti").get("tolleranza_euro", 1.0))
    scarti = [(d, s) for d, s in prev.controllo_totali if abs(s) > tol and d <= fine]
    if scarti:
        out.append(Esito("totali_coerenti", "ko",
                         f"Saldo ≠ saldo iniziale + entrate + uscite in {len(scarti)} giorni "
                         f"(primo: {scarti[0][0]:%d/%m}, {_eur(scarti[0][1])})."))
    else:
        out.append(Esito("totali_coerenti", "ok", "Identità saldo/entrate/uscite rispettata."))
    return out


def controlli_accordi(accordi: dict, ignorati: list, partite: list[Partita], prev: Previsione,
                      regole: dict, oggi: date, proposte: dict | None = None) -> list[Esito]:
    """Controlli non bloccanti su accordi e date congelate."""
    out: list[Esito] = []
    if proposte:
        out.append(Esito("accordi_proposti", "avviso",
                         f"{len(proposte)} accordi in stato Proposta, applicati come certi: se un fornitore "
                         "rifiuta, mettere lo stato Rifiutato e il suo scaduto torna nel piano."))
    cod_presenti = {p.cod_fornitore for p in partite}
    senza = [a for a in accordi.values() if a.cod_fornitore not in cod_presenti]
    if senza:
        out.append(Esito("accordi_fornitore", "avviso",
                         "Accordi su fornitori senza partite aperte: "
                         + ", ".join(f"{a.cod_fornitore} {a.ragione_sociale}".strip() for a in senza[:10])))
    elif accordi:
        out.append(Esito("accordi_fornitore", "ok", f"{len(accordi)} accordi attivi."))
    gg = int((regole.get("accordi") or {}).get("avviso_scadenza_accordo_gg", 15))
    in_scad = [a for a in accordi.values() if a.valido_fino and (a.valido_fino - oggi).days <= gg]
    if in_scad:
        out.append(Esito("accordi_in_scadenza", "avviso",
                         "Accordi in scadenza: " + ", ".join(
                             f"{a.ragione_sociale or a.cod_fornitore} ({a.valido_fino:%d/%m/%Y})" for a in in_scad)))
    if ignorati:
        out.append(Esito("accordi_non_attivi", "avviso",
                         "Accordi ignorati (scaduti, non ancora in vigore, rifiutati o già coperti da un accordo attivo): " + ", ".join(
                             f"{a.ragione_sociale or a.cod_fornitore}" for a in ignorati[:10])))
    for x in regole.get("date_congelate") or []:
        dc = x["data"] if isinstance(x["data"], date) else date.fromisoformat(str(x["data"]))
        comp = prev.fisse_per_riga.get(str(x.get("riga_compensazione", "")).strip(), {}).get(dc, 0.0)
        if abs(comp) < 1:
            out.append(Esito("date_congelate", "avviso",
                             f"Data congelata {dc:%d/%m/%Y} senza compensazione nella riga "
                             f"'{x.get('riga_compensazione')}': se il Report è stato corretto, togliere la "
                             "data da regole.yaml (le partite sono comunque escluse dal piano)."))
    return out
