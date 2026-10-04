import os
from datetime import datetime, timedelta
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def pg_bin(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PostgreSQL" / "16" / "bin" / f"{name}.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PostgreSQL" / "15" / "bin" / f"{name}.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError(f"No se encontró {name}; instale las herramientas cliente de PostgreSQL.")


def main() -> int:
    url = os.environ.get("DATABASE_URL", "")
    parsed = urlparse(url)
    if parsed.scheme not in {"postgresql", "postgresql+psycopg"} or not parsed.hostname or not parsed.path:
        print("DATABASE_URL no es una conexión PostgreSQL válida.", file=sys.stderr)
        return 2
    backup_dir = Path(
        os.environ.get(
            "BACKUP_DIR",
            str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Sistema Contable Multiempresa" / "backups"),
        )
    )
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output = backup_dir / f"contabilidad-{timestamp}.dump"
    temporary_output = output.with_suffix(".dump.tmp")
    environment = os.environ.copy()
    environment["PGPASSWORD"] = unquote(parsed.password or "")
    command = [
        pg_bin("pg_dump"),
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--host",
        parsed.hostname,
        "--port",
        str(parsed.port or 5432),
        "--username",
        unquote(parsed.username or ""),
        "--dbname",
        parsed.path.lstrip("/"),
        "--file",
        str(temporary_output),
    ]
    result = subprocess.run(command, env=environment, capture_output=True, text=True)
    if result.returncode:
        temporary_output.unlink(missing_ok=True)
        print(result.stderr.strip() or "pg_dump no pudo crear el respaldo.", file=sys.stderr)
        return result.returncode
    temporary_output.replace(output)
    cutoff = datetime.now().timestamp() - timedelta(days=30).total_seconds()
    for existing in backup_dir.glob("contabilidad-*.dump"):
        if existing != output and existing.stat().st_mtime < cutoff:
            existing.unlink()
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
