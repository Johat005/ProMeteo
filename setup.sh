#!/usr/bin/env bash
#
# One-time ProMeteo setup for Linux/macOS clients.
# Windows users: see docs/windows-guide.md.
#
# Usage:
#   bash setup.sh                 # create .venv and install dependencies
#   bash setup.sh --configure     # also prompt for CDS credentials
#   bash setup.sh --recreate      # recreate .venv from scratch
#   bash setup.sh --help
#
# The project launcher (./prometeo) automatically uses .venv after this script.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
RECREATE=false
CONFIGURE=false

usage() {
    cat <<'EOF'
Uso: bash setup.sh [OPCIONES]

Crea un entorno virtual local (.venv), instala ProMeteo y sus dependencias, y
verifica que `python3 prometeo --version` funcione.

Opciones:
  --configure   Después de instalar, solicita las credenciales de CDS.
  --recreate    Borra y recrea .venv desde cero.
  -h, --help    Muestra esta ayuda.

Variables de entorno:
  PYTHON_BIN=python3.12   Use otro intérprete base.
EOF
}

for argument in "$@"; do
    case "$argument" in
        --configure) CONFIGURE=true ;;
        --recreate) RECREATE=true ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Opción desconocida: $argument" >&2
            usage >&2
            exit 2
            ;;
    esac
done

echo "== ProMeteo setup =="
echo "Proyecto: $SCRIPT_DIR"

# Check the base interpreter version before creating anything.
if ! "$PYTHON_BIN" - <<'PY'
import sys

minimum = (3, 10)
if sys.version_info < minimum:
    raise SystemExit(
        f"Se requiere Python 3.10 o superior; se encontró {sys.version.split()[0]}."
    )
PY
then
    echo "Use un Python 3.10+ con PYTHON_BIN=... o instálelo primero." >&2
    exit 1
fi

# venv is a separate OS package on some distributions.
if ! "$PYTHON_BIN" -m venv --help >/dev/null 2>&1; then
    echo "El módulo 'venv' no está disponible para $PYTHON_BIN." >&2
    echo "En Debian/Ubuntu: sudo apt install python3-venv" >&2
    exit 1
fi

if [ "$RECREATE" = true ] && [ -d "$VENV_DIR" ]; then
    echo "Eliminando el entorno anterior: $VENV_DIR"
    rm -rf "$VENV_DIR"
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "Creando entorno virtual en $VENV_DIR ..."
    if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
        echo "No se pudo crear el entorno virtual." >&2
        echo "En Debian/Ubuntu: sudo apt install python3-venv python3-pip" >&2
        exit 1
    fi
else
    echo "Reutilizando el entorno existente: $VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
if [ ! -x "$VENV_PYTHON" ]; then
    echo "No se encontró el intérprete del entorno: $VENV_PYTHON" >&2
    exit 1
fi

# Some minimal venv installs lack pip; ensurepip can add it.
if ! "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; then
    echo "Instalando pip dentro del entorno ..."
    "$VENV_PYTHON" -m ensurepip --upgrade
fi

if [ "${PROMETEO_SKIP_PIP_UPGRADE:-0}" != "1" ]; then
    echo "Actualizando pip/setuptools/wheel ..."
    "$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel
fi

echo "Instalando ProMeteo y dependencias ..."
if ! "$VENV_PYTHON" -m pip install -e "$SCRIPT_DIR"; then
    echo >&2
    echo "No se pudieron instalar las dependencias." >&2
    echo "Revise la conexión a Internet y, en Debian/Ubuntu, instale:" >&2
    echo "  sudo apt install build-essential libnetcdf-dev libhdf5-dev" >&2
    echo "Luego vuelva a ejecutar: bash setup.sh --recreate" >&2
    exit 1
fi

echo "Verificando la instalación ..."
"$VENV_PYTHON" "$SCRIPT_DIR/prometeo" --version

echo
echo "Instalación completada."
echo "Entorno virtual: $VENV_DIR"
echo

if [ -f "${HOME}/.cdsapirc" ]; then
    echo "Credenciales de CDS: encontradas en ~/.cdsapirc"
else
    echo "Credenciales de CDS: aún no configuradas."
    echo "Ejecute: python3 prometeo configure"
fi

echo
echo "Próximos comandos:"
echo "  cd \"$SCRIPT_DIR\""
echo "  python3 prometeo configure"
echo "  python3 prometeo list-models"

if [ "$CONFIGURE" = true ]; then
    echo
    if [ -t 0 ]; then
        echo "Configurando credenciales de CDS ..."
        "$VENV_PYTHON" "$SCRIPT_DIR/prometeo" configure
    else
        echo "No hay una terminal interactiva; omitiendo --configure." >&2
        echo "Ejecute después: python3 prometeo configure" >&2
    fi
fi
