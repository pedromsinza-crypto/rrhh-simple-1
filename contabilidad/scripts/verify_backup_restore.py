import argparse
import os
from pathlib import Path
import subprocess
import sys
import uuid

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from backup_database import pg_bin


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
REQUIRED_TABLES = {"alembic_version", "users", "companies", "journals", "journal_lines"}


def verify_restore(backup_file: Path) -> dict[str, object]:
    if not backup_file.is_file() or backup_file.suffix.lower() != ".dump":
        raise ValueError("Indique un archivo .dump existente creado por el sistema.")
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL no está configurada.")
    target_url = make_url(database_url)
    if target_url.get_backend_name() != "postgresql":
        raise RuntimeError("La restauración de prueba requiere PostgreSQL.")
    maintenance_url = target_url.set(database="postgres")
    temporary_database = f"accounting_restore_test_{uuid.uuid4().hex[:16]}"
    admin_engine = create_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    test_engine = None
    password = target_url.password or ""
    environment = os.environ.copy()
    environment["PGPASSWORD"] = password
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{temporary_database}"'))
        restored_url = target_url.set(database=temporary_database)
        restore = subprocess.run(
            [
                pg_bin("pg_restore"),
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "--dbname",
                restored_url.set(password=None).render_as_string(hide_password=False),
                str(backup_file.resolve()),
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if restore.returncode:
            raise RuntimeError(
                "pg_restore falló al restaurar en la base temporal: "
                + (restore.stderr.strip() or f"código {restore.returncode}")
            )
        test_engine = create_engine(restored_url, pool_pre_ping=True)
        inspector = inspect(test_engine)
        tables = set(inspector.get_table_names())
        missing = REQUIRED_TABLES - tables
        if missing:
            raise RuntimeError(f"La restauración no contiene tablas esenciales: {', '.join(sorted(missing))}")
        with test_engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            user_count = connection.execute(text("SELECT count(*) FROM users")).scalar_one()
            company_count = connection.execute(text("SELECT count(*) FROM companies")).scalar_one()
        return {
            "database_revision": revision,
            "users_restored": user_count,
            "companies_restored": company_count,
            "tables_restored": len(tables),
        }
    finally:
        if test_engine is not None:
            test_engine.dispose()
        try:
            with admin_engine.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :database AND pid <> pg_backend_pid()"
                    ),
                    {"database": temporary_database},
                )
                connection.execute(text(f'DROP DATABASE IF EXISTS "{temporary_database}"'))
        finally:
            admin_engine.dispose()
            environment.pop("PGPASSWORD", None)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restaura un respaldo en una base PostgreSQL temporal y comprueba las tablas esenciales."
    )
    parser.add_argument("backup_file", type=Path, help="Archivo .dump que se va a restaurar y verificar")
    arguments = parser.parse_args()
    try:
        result = verify_restore(arguments.backup_file)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"FALLÓ la prueba de restauración: {exc}", file=sys.stderr)
        return 1
    print("Restauración de prueba correcta; la base temporal se eliminó.")
    for name, value in result.items():
        print(f"{name}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
