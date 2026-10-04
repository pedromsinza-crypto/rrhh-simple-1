import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, delete, select, func
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.models import DemoAccessRequest, PasswordResetChallenge  # noqa: E402


def purge_expired_personal_data(dry_run: bool = True) -> dict[str, int]:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL no está configurada.")
    demo_retention_days = 90
    now = datetime.now(timezone.utc)
    demo_cutoff = now - timedelta(days=demo_retention_days)
    reset_cutoff = now - timedelta(days=7)
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with Session(engine) as db:
            demo_count = db.scalar(
                select(func.count())
                .select_from(DemoAccessRequest)
                .where(DemoAccessRequest.created_at < demo_cutoff)
            ) or 0
            reset_count = db.scalar(
                select(func.count())
                .select_from(PasswordResetChallenge)
                .where(PasswordResetChallenge.created_at < reset_cutoff)
            ) or 0
            if not dry_run:
                db.execute(
                    delete(DemoAccessRequest).where(DemoAccessRequest.created_at < demo_cutoff),
                    execution_options={"synchronize_session": False},
                )
                db.execute(
                    delete(PasswordResetChallenge).where(
                        PasswordResetChallenge.created_at < reset_cutoff
                    ),
                    execution_options={"synchronize_session": False},
                )
                db.commit()
            return {"demo_requests": demo_count, "password_reset_records": reset_count}
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Elimina solicitudes demo caducadas y registros viejos de recuperación de contraseña."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Borra los registros. Sin esta opción solo muestra cuántos registros borrarían.",
    )
    arguments = parser.parse_args()
    try:
        counts = purge_expired_personal_data(dry_run=not arguments.execute)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"No se pudo procesar la retención de datos: {exc}", file=sys.stderr)
        return 1
    action = "Eliminados" if arguments.execute else "Se eliminarían"
    print(
        f"{action}: {counts['demo_requests']} solicitudes demo antiguas y "
        f"{counts['password_reset_records']} registros de recuperación vencidos."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
