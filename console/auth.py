"""Login con le utenze del gestionale (tabella utente, password MD5) e protezioni di base.

- Solo gli username in CONSOLE_UTENTI possono entrare, anche se la password è giusta.
- La password non viene mai salvata né registrata: si confronta l'MD5 calcolato con quello in tabella.
- 5 tentativi falliti per username o per indirizzo → blocco di 15 minuti.
- Ogni form che scrive porta un token anti-CSRF legato alla sessione.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

from . import config, db

MAX_TENTATIVI = 5
BLOCCO_SEC = 15 * 60
_falliti: dict[str, list[float]] = {}
_lock = threading.Lock()


def _chiavi(username: str, ip: str) -> list[str]:
    return [f"u:{username.lower()}", f"ip:{ip}"]


def bloccato(username: str, ip: str) -> bool:
    ora = time.time()
    with _lock:
        for k in _chiavi(username, ip):
            t = [x for x in _falliti.get(k, []) if ora - x < BLOCCO_SEC]
            _falliti[k] = t
            if len(t) >= MAX_TENTATIVI:
                return True
    return False


def _fallito(username: str, ip: str) -> None:
    with _lock:
        for k in _chiavi(username, ip):
            _falliti.setdefault(k, []).append(time.time())


def _pulisci(username: str, ip: str) -> None:
    with _lock:
        for k in _chiavi(username, ip):
            _falliti.pop(k, None)


def verifica_credenziali(username: str, password: str, ip: str) -> dict | None:
    """Restituisce l'utente se le credenziali sono valide e l'utente è abilitato, altrimenti None."""
    username = (username or "").strip()
    if not username or not password or bloccato(username, ip):
        return None
    riga = db.uno(f"""SELECT idutente, username, nome, cognome, password, stato
                      FROM {config.TABELLA_UTENTI} WHERE lower(username) = lower(%s)""", (username,))
    calcolato = hashlib.md5(password.encode("utf-8")).hexdigest()
    ok = (riga is not None
          and hmac.compare_digest(calcolato, (riga["password"] or "").strip().lower())
          and riga["username"].strip().lower() in config.UTENTI
          and (config.STATO_ATTIVO is None or str(riga["stato"]) == config.STATO_ATTIVO))
    if not ok:
        _fallito(username, ip)
        return None
    _pulisci(username, ip)
    nome = " ".join(x for x in (riga["nome"], riga["cognome"]) if x) or riga["username"]
    return {"username": riga["username"].strip(), "nome": nome.strip(), "id": riga["idutente"],
            "dal": time.time(), "csrf": secrets.token_urlsafe(32)}


def autore(utente: dict) -> str:
    """Testo che finisce nello storico e nel registro come autore della modifica."""
    return f"{utente['nome']} [{utente['username']}] (console)"


def csrf_ok(utente: dict, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(utente.get("csrf", ""), token)
