#!/usr/bin/env bash
# =====================================================================
#  Ambiente del Tesoriere: verifica (default) o installazione.
#
#    prepara_ambiente.sh             verifica: Python, librerie, config, cartelle, database
#    prepara_ambiente.sh --installa  copia il Python nel volume, ricrea il venv, installa le librerie, verifica
#    prepara_ambiente.sh --installa --forza   ricopia anche il Python
#    eval "$(prepara_ambiente.sh --env)"      carica il .env del profilo nella shell corrente, senza stamparlo
#
#  Perché il Python nel volume: il venv creato con il Python dell'immagine Hermes si rompe a ogni
#  aggiornamento dell'immagine (cambia la cartella /opt/hermes/tools/python-<versione>). La copia in
#  $TESORIERE_HOME/.python è una distribuzione standalone, spostabile, che resta valida.
#
#  Uscita: 0 ambiente pronto · 3 ambiente non pronto (NON lanciare il run) · 1 errore dello script.
#  Non stampa mai il DSN né altri segreti.
# =====================================================================
set -u

HOME_T="${TESORIERE_HOME:-/opt/data/tesoriere}"
DATI="${TESORERIA_DATI:-/opt/data/tesoreria}"
PYDIR="$HOME_T/.python"
VENV="$HOME_T/.venv"
REQ="$HOME_T/skills/riscadenziamento-pagamenti/scripts/requirements.txt"
MODO="verifica"; FORZA=0
for a in "$@"; do
  case "$a" in
    --env) MODO="env" ;;
    --installa) MODO="installa" ;;
    --verifica) MODO="verifica" ;;
    --forza) FORZA=1 ;;
    *) echo "argomento non riconosciuto: $a" >&2; exit 1 ;;
  esac
done

# Lettura del .env del profilo riga per riga (valori con spazi, CRLF, BOM e virgolette gestiti; nessun eval del file)
leggi_env() {
  local f="${HERMES_HOME:-/opt/data/profiles/tesoriere}/.env" riga k v
  [ -f "$f" ] || return 0
  while IFS= read -r riga || [ -n "$riga" ]; do
    riga="${riga%$'\r'}"; riga="${riga#$'\xef\xbb\xbf'}"
    case "$riga" in ''|\#*) continue ;; esac
    riga="${riga#export }"
    k="${riga%%=*}"; v="${riga#*=}"
    [[ "$k" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if [[ "$v" == \"*\" || "$v" == \'*\' ]]; then v="${v:1:${#v}-2}"; fi
    if [ "$MODO" = "env" ]; then printf 'export %s=%q\n' "$k" "$v"; else export "$k=$v"; fi
  done < "$f"
}
if [ "$MODO" = "env" ]; then leggi_env; exit 0; fi
[ -z "${TESORERIA_PG_DSN:-}" ] && leggi_env

installa() {
  if [ ! -x "$PYDIR/bin/python3" ] || [ "$FORZA" = 1 ]; then
    SRC="$(ls -d /opt/hermes/tools/python-3.* 2>/dev/null | sort -V | tail -1)"
    if [ -z "$SRC" ] || [ ! -x "$SRC/bin/python3" ]; then
      echo "KO installazione: nessun Python standalone in /opt/hermes/tools" >&2; return 1
    fi
    echo "copio $SRC in $PYDIR"
    rm -rf "$PYDIR.tmp" && cp -a "$SRC" "$PYDIR.tmp" && rm -rf "$PYDIR" && mv "$PYDIR.tmp" "$PYDIR" || return 1
  fi
  echo "ricreo il venv in $VENV"
  rm -rf "$VENV" && "$PYDIR/bin/python3" -m venv "$VENV" || return 1
  "$VENV/bin/python" -m pip --version >/dev/null 2>&1 || "$VENV/bin/python" -m ensurepip --upgrade >/dev/null || return 1
  "$VENV/bin/python" -m pip install -q --disable-pip-version-check -r "$REQ" || return 1
}

verifica() {
  local ko=0
  # 1. Python e librerie
  if "$VENV/bin/python" -c "import pandas, openpyxl, yaml, psycopg" 2>/dev/null; then
    echo "OK  python      $("$VENV/bin/python" --version 2>&1) con pandas, openpyxl, yaml, psycopg"
    if ! grep -q "^home = $PYDIR" "$VENV/pyvenv.cfg" 2>/dev/null; then
      echo "AVVISO python  il venv dipende dal Python dell'immagine: si romperà al prossimo aggiornamento (lanciare --installa)"
    fi
  else
    echo "KO  python      venv assente o rotto ($VENV): lanciare prepara_ambiente.sh --installa"; ko=1
  fi
  # 2. Configurazione
  for f in "$HOME_T/config/regole.yaml" "$HOME_T/config/fonti_dati.yaml"; do
    [ -f "$f" ] && echo "OK  config      $f" || { echo "KO  config      manca $f"; ko=1; }
  done
  # 3. Cartelle di lavoro
  for d in in out archivio sim; do
    mkdir -p "$DATI/$d" 2>/dev/null
    if [ -d "$DATI/$d" ] && [ -w "$DATI/$d" ]; then echo "OK  cartella    $DATI/$d"
    else echo "KO  cartella    $DATI/$d non esiste o non è scrivibile"; ko=1; fi
  done
  # 4. Database (solo se il Python funziona)
  if [ -z "${TESORERIA_PG_DSN:-}" ]; then
    echo "KO  database    TESORERIA_PG_DSN non impostata (caricare il .env del profilo)"; ko=1
  elif [ "$ko" = 0 ] || "$VENV/bin/python" -c "import psycopg" 2>/dev/null; then
    if out="$("$VENV/bin/python" - <<'EOF' 2>&1
import os, psycopg
with psycopg.connect(os.environ["TESORERIA_PG_DSN"], connect_timeout=8) as c:
    n = c.execute("SELECT count(*) FROM tesoreria.tes_anagrafica_fornitori WHERE valido_al IS NULL").fetchone()[0]
print(n)
EOF
)"; then echo "OK  database    raggiungibile, $out fornitori in anagrafica"
    else echo "KO  database    non raggiungibile: $(echo "$out" | tail -1 | sed 's/password=[^ ]*/password=***/g')"; ko=1; fi
  else
    echo "KO  database    non verificabile senza Python funzionante"; ko=1
  fi
  if [ "$ko" = 0 ]; then echo "AMBIENTE OK"; return 0; else echo "AMBIENTE KO: non lanciare il run"; return 3; fi
}

if [ "$MODO" = "installa" ]; then
  installa || { echo "AMBIENTE KO: installazione non riuscita"; exit 3; }
fi
verifica
exit $?
