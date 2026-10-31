"""Configurazione della console, solo da variabili d'ambiente (nessun segreto nei file)."""
from __future__ import annotations

import os
from pathlib import Path


def _lista(nome: str) -> list[str]:
    return [x.strip().lower() for x in os.environ.get(nome, "").split(",") if x.strip()]


DSN = os.environ.get("TESORERIA_PG_DSN", "")
SCHEMA = os.environ.get("TESORERIA_PG_SCHEMA", "tesoreria")
SEGRETO = os.environ.get("CONSOLE_SECRET", "")
UTENTI = _lista("CONSOLE_UTENTI")                       # username del gestionale abilitati
STATO_ATTIVO = os.environ.get("CONSOLE_STATO_ATTIVO")    # se valorizzato: utente.stato deve valere questo
SOLO_HTTPS = os.environ.get("CONSOLE_HTTPS", "0") == "1"  # cookie "secure" se la console è dietro HTTPS
DURATA_SESSIONE_ORE = float(os.environ.get("CONSOLE_SESSIONE_ORE", "8"))
TABELLA_UTENTI = os.environ.get("CONSOLE_TABELLA_UTENTI", "public.utente")

HOME = Path(os.environ.get("TESORIERE_HOME", "/opt/data/tesoriere"))
DATI = Path(os.environ.get("TESORERIA_DATI", "/opt/data/tesoreria"))
SCRIPTS = HOME / "skills" / "riscadenziamento-pagamenti" / "scripts"
REGOLE = HOME / "config" / "regole.yaml"
FONTI = HOME / "config" / "fonti_dati.yaml"
ARCHIVIO = DATI / "archivio"
SIM = DATI / "sim"


def verifica() -> list[str]:
    """Errori di configurazione bloccanti, mostrati all'avvio."""
    err = []
    if not DSN:
        err.append("TESORERIA_PG_DSN non impostata")
    if len(SEGRETO) < 32:
        err.append("CONSOLE_SECRET mancante o troppo corta (almeno 32 caratteri casuali)")
    if not UTENTI:
        err.append("CONSOLE_UTENTI vuota: nessun utente abilitato")
    if not (SCRIPTS / "riscadenzia.py").exists():
        err.append(f"motore non trovato in {SCRIPTS} (TESORIERE_HOME?)")
    return err
