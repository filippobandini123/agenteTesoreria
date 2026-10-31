"""Assegnazione della classe di priorità a ogni partita.
Precedenza: regole automatiche (regole.yaml) > anagrafica fornitori > classe default."""
from __future__ import annotations

import re
from datetime import date

from .modello import Accordo, Fornitore, Partita


def _sigla_presente(testo: str, sigla: str) -> bool:
    # la sigla deve essere isolata: "TC" non deve corrispondere dentro "TIC" o "HPTC1"
    return re.search(rf"(?<![A-Za-z]){re.escape(sigla)}(?![A-Za-z])", testo or "") is not None


def _corrisponde(p: Partita, cond: dict) -> bool:
    for chiave, valore in cond.items():
        if chiave == "des_piacont":
            if (p.conto or "").strip() != str(valore).strip():
                return False
        elif chiave == "cod_caucon_in":
            if (p.causale or "").strip() not in {str(v).strip() for v in valore}:
                return False
        elif chiave == "note_amm_contiene_sigla":
            if not _sigla_presente(p.note, str(valore)):
                return False
        elif chiave == "note_amm_contiene_testo":
            if str(valore).lower() not in (p.note or "").lower():
                return False
        elif chiave == "cod_fornitore_in":
            if p.cod_fornitore not in set(valore):
                return False
        else:
            raise ValueError(f"Condizione non gestita in regole_classe_automatiche: {chiave}")
    return True


def classifica(partite: list[Partita], anagrafica: dict[int, Fornitore], regole: dict,
               accordi: dict[int, Accordo] | None = None) -> None:
    """Precedenza: regole automatiche (tributi, TC, piano di rientro) > accordo > anagrafica > default."""
    default = int(regole.get("classe_default_fornitore_non_classificato", 3))
    automatiche = regole.get("regole_classe_automatiche") or []
    cfg_acc = regole.get("accordi") or {}
    classe_corrente = int(cfg_acc.get("classe_corrente", 1))
    data_default = cfg_acc.get("data_pagamento_scaduto")
    if data_default is not None and not isinstance(data_default, date):
        data_default = date.fromisoformat(str(data_default))
    accordi = accordi or {}
    for p in partite:
        p.classe, p.origine_classe, p.congelata_accordo, p.data_fissa = None, "", False, None
        for r in automatiche:
            if _corrisponde(p, r.get("condizione") or {}):
                p.classe, p.origine_classe = int(r["classe"]), f"regola_auto:{r['nome']}"
                break
        if p.classe is not None:
            continue
        acc = accordi.get(p.cod_fornitore)
        if acc is not None:
            if acc.e_corrente(p):
                p.classe, p.origine_classe = classe_corrente, f"accordo:corrente dal {acc.decorrenza:%d/%m/%Y}"
            else:
                p.classe, p.origine_classe = classe_corrente, f"accordo:scaduto congelato (ante {acc.decorrenza:%d/%m/%Y})"
                p.congelata_accordo = True
                p.data_fissa = acc.data_pagamento_scaduto or data_default
            continue
        f = anagrafica.get(p.cod_fornitore)
        if f is not None and (f.in_elenco_acquisto or f.classe_priorita == 1):
            p.classe, p.origine_classe = 1, "anagrafica:elenco_acquisto"
        elif f is not None and f.classe_priorita is not None:
            p.classe, p.origine_classe = int(f.classe_priorita), "anagrafica:classe"
        else:
            p.classe, p.origine_classe = default, "default"


def fornitori_nuovi(partite: list[Partita], anagrafica: dict[int, Fornitore]) -> list[tuple[int, str]]:
    visti = {}
    for p in partite:
        if p.cod_fornitore not in anagrafica:
            visti.setdefault(p.cod_fornitore, p.ragione_sociale)
    return sorted(visti.items())
