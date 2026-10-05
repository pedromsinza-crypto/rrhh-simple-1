#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT}/docker-compose.aiven.yml"
ENV_FILE="${ROOT}/.env"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker no está instalado. Instala Docker Engine y el plugin Docker Compose, luego vuelve a ejecutar este script." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "No se encontró Docker Compose v2 (docker compose)." >&2
  exit 1
fi
if [[ ! -f "${ROOT}/Dockerfile" || ! -f "${ROOT}/requirements.txt" ]]; then
  echo "No se encuentra el código de la aplicación en ${ROOT}." >&2
  exit 1
fi

if [[ -e "${ENV_FILE}" ]]; then
  read -r -p "Ya existe ${ENV_FILE}. ¿Reemplazar su configuración? [s/N] " replace_env
  if [[ ! "${replace_env}" =~ ^[sS]([iI])?$ ]]; then
    echo "Instalación cancelada; se conservó .env."
    exit 0
  fi
  backup="${ENV_FILE}.backup.$(date +%Y%m%d%H%M%S)"
  cp -- "${ENV_FILE}" "${backup}"
  chmod 600 "${backup}"
  echo "Se guardó una copia privada de la configuración existente."
fi

read -r -p "Host PostgreSQL de Aiven: " db_host
read -r -p "Puerto PostgreSQL [24536]: " db_port
db_port="${db_port:-24536}"
if [[ ! "${db_port}" =~ ^[0-9]{1,5}$ ]] || (( db_port < 1 || db_port > 65535 )); then
  echo "Puerto inválido." >&2
  exit 1
fi
read -r -p "Nombre de la base existente en Aiven [defaultdb]: " db_name
db_name="${db_name:-defaultdb}"
read -r -p "Usuario PostgreSQL: " db_user
read -r -s -p "Contraseña PostgreSQL (no se mostrará): " db_password
printf '\n'
read -r -p "Correo del administrador inicial: " admin_email
read -r -s -p "Contraseña del administrador (mínimo 12 caracteres): " admin_password
printf '\n'

if [[ -z "${db_host}" || "${db_host}" == *"/"* || -z "${db_name}" || "${db_name}" == *"/"* || -z "${db_user}" ]]; then
  echo "Host, nombre de base y usuario son obligatorios." >&2
  exit 1
fi
if [[ -z "${db_password}" || -z "${admin_email}" ]]; then
  echo "La contraseña PostgreSQL y el correo del administrador son obligatorios." >&2
  exit 1
fi
if (( ${#admin_password} < 12 )); then
  echo "La contraseña del administrador debe tener al menos 12 caracteres." >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1 || ! command -v openssl >/dev/null 2>&1; then
  echo "Se requieren python3 y openssl para configurar secretos." >&2
  exit 1
fi

export AIVEN_DB_HOST="${db_host}"
export AIVEN_DB_PORT="${db_port}"
export AIVEN_DB_NAME="${db_name}"
export AIVEN_DB_USER="${db_user}"
export AIVEN_DB_PASSWORD="${db_password}"
export APP_ROOT="${ROOT}"
export APP_ADMIN_EMAIL="${admin_email}"
export APP_ADMIN_PASSWORD="${admin_password}"
export APP_JWT_SECRET
APP_JWT_SECRET="$(openssl rand -hex 32)"
export API_BIND_ADDRESS="127.0.0.1"
export ACCESS_TOKEN_MINUTES="60"
export MAIL_PORT="587"
export MAIL_STARTTLS="true"
export MAIL_HOST="" MAIL_USERNAME="" MAIL_PASSWORD="" MAIL_FROM=""
export AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT="" AZURE_DOCUMENT_INTELLIGENCE_KEY=""

python3 - <<'PY'
import os
from pathlib import Path
from urllib.parse import quote

root = Path(os.environ["APP_ROOT"])
database_url = (
    "postgresql+psycopg://"
    + quote(os.environ["AIVEN_DB_USER"], safe="")
    + ":"
    + quote(os.environ["AIVEN_DB_PASSWORD"], safe="")
    + "@"
    + os.environ["AIVEN_DB_HOST"]
    + ":"
    + os.environ["AIVEN_DB_PORT"]
    + "/"
    + quote(os.environ["AIVEN_DB_NAME"], safe="")
    + "?sslmode=require"
)
values = {
    "DATABASE_URL": database_url,
    "JWT_SECRET": os.environ["APP_JWT_SECRET"],
    "ACCESS_TOKEN_MINUTES": os.environ["ACCESS_TOKEN_MINUTES"],
    "BOOTSTRAP_ADMIN_EMAIL": os.environ["APP_ADMIN_EMAIL"],
    "BOOTSTRAP_ADMIN_PASSWORD": os.environ["APP_ADMIN_PASSWORD"],
    "API_BIND_ADDRESS": os.environ["API_BIND_ADDRESS"],
    "MAIL_HOST": os.environ["MAIL_HOST"],
    "MAIL_PORT": os.environ["MAIL_PORT"],
    "MAIL_USERNAME": os.environ["MAIL_USERNAME"],
    "MAIL_PASSWORD": os.environ["MAIL_PASSWORD"],
    "MAIL_FROM": os.environ["MAIL_FROM"],
    "MAIL_STARTTLS": os.environ["MAIL_STARTTLS"],
    "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT": os.environ["AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"],
    "AZURE_DOCUMENT_INTELLIGENCE_KEY": os.environ["AZURE_DOCUMENT_INTELLIGENCE_KEY"],
}
def compose_value(value):
    if "\n" in value or "\r" in value:
        raise ValueError("Las credenciales no pueden contener saltos de línea.")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

env_file = root / ".env"
env_file.write_text(
    "".join(f"{key}={compose_value(value)}\n" for key, value in values.items()),
    encoding="utf-8",
)
env_file.chmod(0o600)
PY
unset AIVEN_DB_PASSWORD APP_ADMIN_PASSWORD APP_JWT_SECRET

cd -- "${ROOT}"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up --build -d
echo
echo "Aplicación iniciada. Revisa estado: docker compose --env-file .env -f docker-compose.aiven.yml ps"
echo "Disponible localmente en: http://127.0.0.1:8000"
echo "La aplicación aplicará automáticamente las migraciones Alembic al iniciar."
