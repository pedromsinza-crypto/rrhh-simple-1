import csv
import base64
import hashlib
import html
import hmac
import json
import logging
import os
import re
import secrets
import smtplib
import ssl
import subprocess
import sys
import time
import unicodedata
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from io import StringIO
from pathlib import Path
from typing import Annotated, AsyncIterator
from urllib.parse import quote, urlparse

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from dotenv import load_dotenv

load_dotenv()

from .database import Base, SessionLocal, engine, get_db
from .models import (
    AccountantClient,
    AccountantFee,
    AccessLicense,
    Account,
    AuditEvent,
    BankStatementLine,
    Company,
    CompanyDocument,
    Contact,
    DemoAccessRequest,
    DocumentExtraction,
    FiscalPeriod,
    Journal,
    JournalLine,
    LicensePayment,
    Membership,
    OpeningBalance,
    Payment,
    PasswordResetChallenge,
    PersonalPlanItem,
    PersonalTransaction,
    ProfessionalFee,
    SystemSetting,
    TaxDocument,
    TaxWorkpaper,
    User,
)
from .schemas import (
    AccountantClientCreate,
    AccountantClientRead,
    AccountantFeeCreate,
    AccountantFeePayment,
    AccountantFeeRead,
    AccessLicenseCreate,
    AccessLicenseUpdate,
    AccountCreate,
    AccountRead,
    BankStatementLineCreate,
    CompanyCreate,
    CompanyBrandingUpdate,
    CompanyDocumentRead,
    CompanyMemberInvite,
    CompanyRead,
    CompanyUpdate,
    ContactCreate,
    ContactRead,
    DemoAccessRequestCreate,
    DemoAccessRequestDecision,
    DemoAccessRequestRead,
    DemoAccessRequestSubmission,
    DemoEmailVerification,
    DocumentExtractionDecision,
    DocumentExtractionRead,
    JournalCreate,
    JournalRead,
    LicensePaymentCreate,
    MembershipCreate,
    OpeningBalanceCreate,
    PasswordChange,
    PasswordResetConfirm,
    PasswordResetRequest,
    PaymentCreate,
    PaymentRead,
    PersonalPlanItemCreate,
    PersonalPlanItemRead,
    PersonalPlanItemUpdate,
    PersonalTransactionCreate,
    PersonalTransactionRead,
    ProfessionalFeeCreate,
    ProfessionalFeePayment,
    ProfessionalFeeRead,
    SystemSettingsUpdate,
    TaxDocumentCreate,
    TaxDocumentRead,
    TaxWorkpaperRead,
    UserCreate,
    UserRead,
)
from .security import (
    DbSession,
    create_access_token,
    current_user,
    hash_password,
    require_company_role,
    verify_password,
)
from .services import calculate_igtf, calculate_vat, money, normalize_rif


MAX_COMPANY_DOCUMENT_BYTES = 10 * 1024 * 1024
logger = logging.getLogger(__name__)
ALLOWED_COMPANY_DOCUMENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "text/plain",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if len(os.getenv("JWT_SECRET", "")) < 32:
        raise RuntimeError("JWT_SECRET debe tener al menos 32 caracteres")
    email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not email or len(password) < 12:
        raise RuntimeError(
            "Configure BOOTSTRAP_ADMIN_EMAIL y BOOTSTRAP_ADMIN_PASSWORD (mínimo 12 caracteres)"
        )
    if engine.dialect.name == "postgresql":
        from alembic import command
        from alembic.config import Config

        project_root = Path(__file__).resolve().parents[1]
        migration_config = Config(str(project_root / "alembic.ini"))
        migration_config.set_main_option("script_location", str(project_root / "migrations"))
        command.upgrade(migration_config, "head")
    else:
        Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if not db.get(SystemSetting, "company_limit"):
            db.add(SystemSetting(key="company_limit", value="10"))
        existing = db.scalar(select(User).where(User.email == email))
        if not existing:
            db.add(
                User(
                    email=email,
                    password_hash=hash_password(password),
                    is_superuser=True,
                )
            )
        db.commit()
    yield


def backup_directory() -> Path:
    return Path(
        os.environ.get(
            "BACKUP_DIR",
            str(
                Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
                / "Sistema Contable Multiempresa"
                / "backups"
            ),
        )
    )


app = FastAPI(
    title="Sistema Contable Multiempresa",
    description=(
        "API contable multiempresa con PostgreSQL. Los cálculos fiscales son "
        "parametrizables y requieren validación profesional y normativa vigente."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

WEB_APP = Path(__file__).parent / "static" / "index.html"


def send_configured_email(recipient: str, subject: str, html_body: str) -> None:
    host = os.getenv("MAIL_HOST", "").strip()
    sender = os.getenv("MAIL_FROM", "").strip()
    if not host or not sender:
        raise RuntimeError("El envío de correo no está configurado (MAIL_HOST y MAIL_FROM)")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content("Este mensaje requiere un cliente de correo compatible con HTML.")
    message.add_alternative(html_body, subtype="html")
    port = int(os.getenv("MAIL_PORT", "587"))
    username = os.getenv("MAIL_USERNAME", "")
    password = os.getenv("MAIL_PASSWORD", "")
    starttls = os.getenv("MAIL_STARTTLS", "true").strip().lower() in {"1", "true", "yes"}
    if not starttls:
        raise RuntimeError("El servidor SMTP debe usar STARTTLS para proteger el correo y las credenciales")
    if bool(username) != bool(password):
        raise RuntimeError("Configure tanto MAIL_USERNAME como MAIL_PASSWORD para autenticar SMTP")
    with smtplib.SMTP(host, port, timeout=20) as server:
        server.ehlo()
        if starttls:
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        if username:
            server.login(username, password)
        server.send_message(message)


def demo_code_digest(request_id: str, code: str) -> str:
    key = os.getenv("JWT_SECRET", "").encode("utf-8")
    return hmac.new(key, f"demo-email:{request_id}:{code}".encode("utf-8"), hashlib.sha256).hexdigest()


def require_accountant_license(user: User, db: Session) -> None:
    license_access = db.scalar(
        select(AccessLicense).where(
            AccessLicense.user_id == user.id,
            AccessLicense.status == "active",
            AccessLicense.expires_at >= date.today(),
            AccessLicense.role == "accountant",
        )
    )
    if not license_access:
        raise HTTPException(status_code=403, detail="Este módulo requiere una licencia activa de contador")


def render_accountant_fee_invoice(client: AccountantClient, fee: AccountantFee, accountant: User) -> str:
    return f"""<!doctype html><html lang="es"><meta charset="utf-8"><title>Cuenta de honorarios {html.escape(fee.reference)}</title>
<body style="font:15px Arial,sans-serif;max-width:760px;margin:40px auto;color:#1b2a41">
<header style="border-bottom:2px solid #2059c9;padding-bottom:18px"><h1>Cuenta de honorarios profesionales</h1>
<p>Profesional: {html.escape(accountant.email)}</p></header>
<h2>Cliente</h2><p>{html.escape(client.name)} · RIF {html.escape(client.rif)}</p>
<p>Referencia: {html.escape(fee.reference)} · Emisión: {fee.issued_at.isoformat()} · Vencimiento: {fee.due_date.isoformat()}</p>
<table style="width:100%;border-collapse:collapse"><tr><th style="text-align:left">{html.escape(fee.service_description)}</th><td style="text-align:right">{fee.amount} {fee.currency}</td></tr></table>
<p>Período de servicio: {html.escape(fee.service_period or "No indicado")}</p>
<p style="margin-top:28px;color:#8a2a2a;border:1px solid #e4b7b7;padding:12px"><strong>Cuenta de cobro comercial.</strong> No es factura fiscal autorizada por el SENIAT ni sustituye las obligaciones de facturación vigentes.</p>
</body></html>"""


def render_commercial_invoice(company: Company, document: TaxDocument) -> str:
    logo = ""
    if company.logo_content and company.logo_content_type:
        encoded = base64.b64encode(company.logo_content).decode("ascii")
        logo = f'<img alt="Logo de {html.escape(company.legal_name)}" style="max-width:180px;max-height:90px" src="data:{company.logo_content_type};base64,{encoded}">'
    status_label = {"pending": "Pendiente de aprobación", "posted": "Aprobada", "reversed": "Reversada"}.get(document.status, document.status)
    rows = [
        (document.commercial_description or "Bienes o servicios", document.base_amount + document.exempt_amount),
        (f"IVA ({document.vat_rate_percent}%)", document.vat_amount),
        ("Retención de IVA", -document.vat_withheld),
        ("Total del documento", document.total_amount - document.vat_withheld),
    ]
    amount_rows = "".join(
        f"<tr><th>{html.escape(label)}</th><td style='text-align:right'>{html.escape(str(amount))} VES</td></tr>"
        for label, amount in rows
    )
    return f"""<!doctype html><html lang="es"><meta charset="utf-8"><title>Factura comercial {html.escape(document.invoice_number)}</title>
<body style="font:15px Arial,sans-serif;max-width:780px;margin:40px auto;color:#1b2a41">
<header style="display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #2059c9;padding-bottom:18px">
<div>{logo}<h1>{html.escape(company.legal_name)}</h1><p>RIF {html.escape(company.rif)} · {html.escape(company.tax_address)}</p></div>
<div><h2>Factura comercial</h2><p>N.º {html.escape(document.invoice_number)}</p><p>Control: {html.escape(document.control_number or "—")}</p><p>Fecha: {document.document_date.isoformat()}</p></div></header>
<h3>Facturado a</h3><p>{html.escape(document.counterparty_name)} · RIF {html.escape(document.counterparty_rif)}</p>
<table style="width:100%;border-collapse:collapse;margin-top:28px"><tbody>{amount_rows}</tbody></table>
<p style="margin-top:28px;color:#8a2a2a;border:1px solid #e4b7b7;padding:12px"><strong>Documento comercial interno.</strong> No constituye factura fiscal autorizada por el SENIAT, documento preimpreso autorizado ni comprobante de facturación electrónica. Verifique los requisitos legales aplicables antes de emitirlo al cliente.</p>
<p>Estado contable: {html.escape(status_label)}</p></body></html>"""


@app.get("/admin/backups")
def list_backups(user: Annotated[User, Depends(current_user)]) -> list[dict[str, str | int]]:
    require_superuser(user)
    root = backup_directory()
    if not root.exists():
        return []
    return [
        {
            "name": backup.name,
            "size_bytes": backup.stat().st_size,
            "created_at": datetime.fromtimestamp(
                backup.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
        }
        for backup in sorted(root.glob("contabilidad-*.dump"), key=lambda file: file.stat().st_mtime, reverse=True)
    ]


@app.get("/admin/integrations/status")
def integration_status(user: Annotated[User, Depends(current_user)]) -> dict[str, object]:
    require_superuser(user)
    mail_host = bool(os.getenv("MAIL_HOST", "").strip())
    mail_sender = bool(os.getenv("MAIL_FROM", "").strip())
    mail_username = bool(os.getenv("MAIL_USERNAME", "").strip())
    mail_password = bool(os.getenv("MAIL_PASSWORD", "").strip())
    starttls = os.getenv("MAIL_STARTTLS", "true").strip().lower() in {"1", "true", "yes"}
    azure_endpoint = bool(os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "").strip())
    azure_key = bool(os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "").strip())
    return {
        "email": {
            "configured": mail_host and mail_sender and starttls and mail_username == mail_password,
            "host_configured": mail_host,
            "sender_configured": mail_sender,
            "credentials_configured": mail_username and mail_password,
            "starttls_enabled": starttls,
        },
        "azure_ocr": {
            "configured": azure_endpoint and azure_key,
            "endpoint_configured": azure_endpoint,
            "key_configured": azure_key,
        },
        "banking": {
            "csv_import_available": True,
            "direct_bank_sync_configured": False,
        },
        "seniat": {
            "internal_workpapers_available": True,
            "official_submission_configured": False,
        },
    }


@app.post("/admin/integrations/email-test")
def send_integration_test_email(
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, str]:
    require_superuser(user)
    if not user.email:
        raise HTTPException(status_code=409, detail="La cuenta superadministradora no tiene correo registrado")
    try:
        send_configured_email(
            user.email,
            "Prueba de correo · Sistema Contable Multiempresa",
            "<p>La configuración SMTP del Sistema Contable Multiempresa funciona correctamente.</p>",
        )
    except (OSError, RuntimeError, smtplib.SMTPException, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"No se pudo enviar el correo de prueba: {exc}") from exc
    add_audit(db, user.id, None, "integration.email_test", "user", user.id, request)
    db.commit()
    return {"status": "sent", "recipient": user.email}


@app.post("/admin/backups")
def create_backup(user: Annotated[User, Depends(current_user)]) -> dict[str, str]:
    require_superuser(user)
    script = Path(__file__).resolve().parents[1] / "scripts" / "backup_database.py"
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="El respaldo excedió el tiempo máximo") from exc
    if result.returncode:
        raise HTTPException(
            status_code=500,
            detail=result.stderr.strip() or "No se pudo crear el respaldo",
        )
    filename = Path(result.stdout.strip()).name
    if not re.fullmatch(r"contabilidad-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.dump", filename):
        raise HTTPException(status_code=500, detail="El proceso de respaldo devolvió un nombre inválido")
    return {"name": filename, "status": "created"}


@app.get("/admin/backups/{filename}")
def download_backup(
    filename: str,
    user: Annotated[User, Depends(current_user)],
) -> FileResponse:
    require_superuser(user)
    if not re.fullmatch(r"contabilidad-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.dump", filename):
        raise HTTPException(status_code=404, detail="Respaldo no encontrado")
    path = backup_directory() / filename
    if not path.is_file() or path.parent.resolve() != backup_directory().resolve():
        raise HTTPException(status_code=404, detail="Respaldo no encontrado")
    return FileResponse(path, media_type="application/octet-stream", filename=filename)


@app.get("/", include_in_schema=False)
def web_app() -> FileResponse:
    return FileResponse(WEB_APP)


def add_audit(
    db: Session,
    user_id: str | None,
    company_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str = "",
    request: Request | None = None,
) -> None:
    ip = request.client.host if request and request.client else ""
    db.add(
        AuditEvent(
            user_id=user_id,
            company_id=company_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            ip_address=ip,
        )
    )


def require_superuser(user: User) -> None:
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Se requiere rol de superadministrador")


def ensure_period_open(db: Session, company_id: str, entry_date: date) -> None:
    closed_period = db.scalar(
        select(FiscalPeriod).where(
            FiscalPeriod.company_id == company_id,
            FiscalPeriod.closed.is_(True),
            FiscalPeriod.start_date <= entry_date,
            FiscalPeriod.end_date >= entry_date,
        )
    )
    if closed_period:
        raise HTTPException(status_code=409, detail="El período contable está cerrado")


def get_personal_period_bounds(period: str) -> tuple[date, date]:
    if len(period) != 6 or not period.isdigit():
        raise HTTPException(status_code=422, detail="El período debe tener formato AAAAMM")
    year, month = int(period[:4]), int(period[4:])
    if not 1 <= year <= 9999 or not 1 <= month <= 12 or (year == 9999 and month == 12):
        raise HTTPException(status_code=422, detail="Período inválido")
    start_date = date(year, month, 1)
    end_date = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start_date, end_date


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "contabilidad"}


@app.get("/ready")
def readiness(db: DbSession) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.exception("Database readiness check failed")
        raise HTTPException(status_code=503, detail="La base de datos no está disponible") from exc
    return {"status": "ready", "service": "contabilidad"}


@app.post("/auth/token")
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbSession,
) -> dict[str, str]:
    user = db.scalar(select(User).where(User.email == form.username.lower()))
    if not user or not user.active or not verify_password(form.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {
        "access_token": create_access_token(user.id, user.auth_version),
        "token_type": "bearer",
    }


@app.get("/auth/me")
def who_am_i(
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, object]:
    memberships = db.scalars(
        select(Membership).where(Membership.user_id == user.id)
    ).all()
    license_access = db.scalar(
        select(AccessLicense).where(AccessLicense.user_id == user.id)
    )
    owned_company_count = (
        db.scalar(
            select(func.count())
            .select_from(Company)
            .where(
                Company.license_id == license_access.id,
                Company.active.is_(True),
            )
        )
        if license_access
        else 0
    )
    return {
        "id": user.id,
        "email": user.email,
        "is_superuser": user.is_superuser,
        "license_role": license_access.role if license_access else None,
        "company_limit": license_access.max_companies if license_access else None,
        "owned_company_count": owned_company_count,
        "memberships": [
            {"company_id": item.company_id, "role": item.role}
            for item in memberships
        ],
    }


@app.post("/auth/change-password")
def change_password(
    payload: PasswordChange,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, str]:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="La contraseña actual no coincide")
    user.password_hash = hash_password(payload.new_password)
    add_audit(db, user.id, None, "password.change", "user", user.id, request)
    db.commit()
    return {"status": "updated"}


def password_reset_email_digest(email: str) -> str:
    key = os.getenv("JWT_SECRET", "").encode("utf-8")
    return hmac.new(
        key,
        f"password-reset-email:{email.strip().lower()}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def password_reset_token_digest(token: str) -> str:
    key = os.getenv("JWT_SECRET", "").encode("utf-8")
    return hmac.new(key, f"password-reset-token:{token}".encode("utf-8"), hashlib.sha256).hexdigest()


@app.post("/auth/password-reset/request", status_code=202)
def request_password_reset(
    payload: PasswordResetRequest,
    db: DbSession,
) -> dict[str, str]:
    if not os.getenv("MAIL_HOST", "").strip() or not os.getenv("MAIL_FROM", "").strip():
        raise HTTPException(
            status_code=503,
            detail="La recuperación de contraseña requiere configurar el correo del sistema.",
        )
    email = str(payload.email).strip().lower()
    email_digest = password_reset_email_digest(email)
    now = datetime.now(timezone.utc)
    latest = db.scalar(
        select(PasswordResetChallenge)
        .where(PasswordResetChallenge.email_digest == email_digest)
        .order_by(PasswordResetChallenge.created_at.desc())
        .limit(1)
    )
    if latest:
        created_at = latest.created_at
        created_at = created_at.replace(tzinfo=timezone.utc) if created_at.tzinfo is None else created_at
        if now - created_at < timedelta(seconds=60):
            return {
                "status": "accepted",
                "message": "Si el correo corresponde a una cuenta, recibirá instrucciones de recuperación.",
            }

    account = db.scalar(select(User).where(User.email == email, User.active.is_(True)))
    token = secrets.token_urlsafe(32) if account else None
    challenge = PasswordResetChallenge(
        email_digest=email_digest,
        user_id=account.id if account else None,
        token_digest=password_reset_token_digest(token) if token else None,
        expires_at=now + timedelta(minutes=30) if token else None,
    )
    db.add(challenge)
    db.commit()
    if not token:
        return {
            "status": "accepted",
            "message": "Si el correo corresponde a una cuenta, recibirá instrucciones de recuperación.",
        }
    body = (
        f"<p>Se solicitó recuperar el acceso a su cuenta.</p>"
        f"<p>Si su correo corresponde a una cuenta, use este código en la pantalla de recuperación. "
        f"Vence en 30 minutos y solo puede utilizarse una vez:</p>"
        f"<p style='font-size:20px;font-weight:bold;word-break:break-all'>{html.escape(token)}</p>"
        "<p>Si no solicitó este cambio, ignore este mensaje. La contraseña actual seguirá vigente.</p>"
    )
    try:
        send_configured_email(email, "Recuperación de acceso · Sistema Contable Multiempresa", body)
    except (OSError, RuntimeError, smtplib.SMTPException, ValueError) as exc:
        logger.warning(
            "Password recovery email could not be sent (email_digest=%s): %s",
            email_digest,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="El servicio de correo no pudo enviar las instrucciones. Intente más tarde.",
        ) from exc
    return {
        "status": "accepted",
        "message": "Si el correo corresponde a una cuenta, recibirá instrucciones de recuperación.",
    }


@app.post("/auth/password-reset/confirm")
def confirm_password_reset(
    payload: PasswordResetConfirm,
    request: Request,
    db: DbSession,
) -> dict[str, str]:
    email = str(payload.email).strip().lower()
    now = datetime.now(timezone.utc)
    challenge = db.scalar(
        select(PasswordResetChallenge)
        .where(
            PasswordResetChallenge.email_digest == password_reset_email_digest(email),
            PasswordResetChallenge.token_digest == password_reset_token_digest(payload.token),
            PasswordResetChallenge.used_at.is_(None),
            PasswordResetChallenge.expires_at > now,
        )
        .order_by(PasswordResetChallenge.created_at.desc())
        .with_for_update()
    )
    if not challenge or not challenge.user_id:
        raise HTTPException(status_code=400, detail="El código no es válido o venció; solicite otro.")
    account = db.scalar(
        select(User).where(User.id == challenge.user_id, User.active.is_(True)).with_for_update()
    )
    if not account:
        raise HTTPException(status_code=400, detail="El código no es válido o venció; solicite otro.")
    account.password_hash = hash_password(payload.new_password)
    account.auth_version += 1
    tokens = db.scalars(
        select(PasswordResetChallenge).where(
            PasswordResetChallenge.user_id == account.id,
            PasswordResetChallenge.used_at.is_(None),
        )
    ).all()
    for item in tokens:
        item.used_at = now
    add_audit(db, account.id, None, "password.reset", "user", account.id, request)
    db.commit()
    return {"status": "updated", "message": "Contraseña actualizada. Inicie sesión nuevamente."}


@app.post("/demo/access", response_model=DemoAccessRequestSubmission, status_code=202)
def request_demo_access(
    payload: DemoAccessRequestCreate,
    request: Request,
    db: DbSession,
) -> DemoAccessRequestSubmission:
    email = str(payload.email).strip().lower()
    if not payload.privacy_notice_accepted:
        raise HTTPException(status_code=422, detail="Debe aceptar el aviso de privacidad para solicitar acceso.")
    if not os.getenv("MAIL_HOST") or not os.getenv("MAIL_FROM"):
        raise HTTPException(
            status_code=503,
            detail="El correo de verificación no está configurado. El administrador debe configurar el servicio de correo.",
        )
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status_code=409, detail="Ese correo ya tiene una cuenta")
    if db.scalar(select(User.id).where(User.identity_number == payload.identity_number)):
        raise HTTPException(status_code=409, detail="Esa cédula ya está asociada a una cuenta")
    recent_request = db.scalar(
        select(DemoAccessRequest.id).where(
            DemoAccessRequest.email == email,
            DemoAccessRequest.created_at >= datetime.now(timezone.utc) - timedelta(hours=24),
        )
    )
    if recent_request:
        raise HTTPException(status_code=429, detail="Ya se envió una solicitud con este correo en las últimas 24 horas")
    pending = db.scalar(
        select(DemoAccessRequest).where(
            DemoAccessRequest.email == email,
            DemoAccessRequest.status.in_(["pending_email", "pending_review"]),
        )
    )
    if pending:
        raise HTTPException(status_code=409, detail=f"Ya existe una solicitud pendiente: {pending.id}")
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = datetime.now(timezone.utc)
    demo_request = DemoAccessRequest(
        email=email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name.strip(),
        identity_number=payload.identity_number,
        rif=payload.rif,
        address=payload.address.strip(),
        profession=payload.profession.strip(),
        privacy_notice_version="2026-10-04-v1",
        privacy_accepted_at=now,
        status="pending_email",
        verification_attempts=0,
        verification_expires_at=now + timedelta(minutes=15),
        verification_sent_at=now,
    )
    db.add(demo_request)
    db.flush()
    demo_request.verification_hash = demo_code_digest(demo_request.id, code)
    add_audit(db, None, None, "demo.request.create", "demo_access_request", demo_request.id, request)
    db.commit()
    db.refresh(demo_request)
    try:
        send_configured_email(
            email,
            "Verificación de correo · Sistema Contable Multiempresa",
            f"<p>Su código de verificación vence en 15 minutos:</p><p style='font-size:28px;font-weight:bold;letter-spacing:6px'>{code}</p><p>La cuenta no se activa hasta que el administrador revise su cédula, RIF, dirección y profesión. La demo dura cinco días desde la aprobación.</p>",
        )
    except (OSError, RuntimeError, smtplib.SMTPException, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Solicitud guardada como {demo_request.id}, pero no se pudo enviar el código. Configure el correo y solicite el reenvío.",
        ) from exc
    return DemoAccessRequestSubmission(
        id=demo_request.id,
        email=demo_request.email,
        status=demo_request.status,
    )


@app.post("/demo/access/{request_id}/verify")
def verify_demo_email(
    request_id: str,
    payload: DemoEmailVerification,
    request: Request,
    db: DbSession,
) -> dict[str, str]:
    demo_request = db.scalar(
        select(DemoAccessRequest)
        .where(
            DemoAccessRequest.id == request_id,
            DemoAccessRequest.status == "pending_email",
        )
        .with_for_update()
    )
    if not demo_request:
        raise HTTPException(status_code=404, detail="Solicitud pendiente de verificación no encontrada")
    now = datetime.now(timezone.utc)
    expires_at = demo_request.verification_expires_at
    if (
        not expires_at
        or (expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at) < now
        or demo_request.verification_attempts >= 5
    ):
        raise HTTPException(status_code=410, detail="El código venció o se agotaron los intentos; solicite otro")
    demo_request.verification_attempts += 1
    supplied = demo_code_digest(demo_request.id, payload.code)
    if not demo_request.verification_hash or not hmac.compare_digest(
        supplied, demo_request.verification_hash
    ):
        add_audit(db, None, None, "demo.email.verify_failed", "demo_access_request", demo_request.id, request)
        db.commit()
        raise HTTPException(status_code=422, detail="Código de verificación incorrecto")
    demo_request.email_verified_at = now
    demo_request.verification_hash = None
    demo_request.status = "pending_review"
    add_audit(db, None, None, "demo.email.verified", "demo_access_request", demo_request.id, request)
    db.commit()
    return {"status": "pending_review", "message": "Correo verificado; el administrador revisará los datos."}


@app.post(
    "/demo/access/{request_id}/resend",
    response_model=DemoAccessRequestSubmission,
    status_code=202,
)
def resend_demo_verification(
    request_id: str,
    request: Request,
    db: DbSession,
) -> DemoAccessRequestSubmission:
    demo_request = db.scalar(
        select(DemoAccessRequest).where(
            DemoAccessRequest.id == request_id,
            DemoAccessRequest.status == "pending_email",
        )
    )
    if not demo_request:
        raise HTTPException(status_code=404, detail="Solicitud pendiente de verificación no encontrada")
    now = datetime.now(timezone.utc)
    sent_at = demo_request.verification_sent_at
    if sent_at:
        sent_at = sent_at.replace(tzinfo=timezone.utc) if sent_at.tzinfo is None else sent_at
        if now - sent_at < timedelta(seconds=60):
            raise HTTPException(status_code=429, detail="Espere 60 segundos antes de solicitar otro código")
    code = f"{secrets.randbelow(1_000_000):06d}"
    demo_request.verification_hash = demo_code_digest(demo_request.id, code)
    demo_request.verification_expires_at = now + timedelta(minutes=15)
    demo_request.verification_sent_at = now
    demo_request.verification_attempts = 0
    db.commit()
    try:
        send_configured_email(
            demo_request.email,
            "Nuevo código de verificación · Sistema Contable Multiempresa",
            f"<p>Su nuevo código de verificación vence en 15 minutos:</p><p style='font-size:28px;font-weight:bold;letter-spacing:6px'>{code}</p>",
        )
    except (OSError, RuntimeError, smtplib.SMTPException, ValueError) as exc:
        raise HTTPException(status_code=503, detail="No se pudo enviar el correo de verificación") from exc
    add_audit(db, None, None, "demo.email.resend", "demo_access_request", demo_request.id, request)
    db.commit()
    db.refresh(demo_request)
    return DemoAccessRequestSubmission(
        id=demo_request.id,
        email=demo_request.email,
        status=demo_request.status,
    )


@app.get("/professional/clients", response_model=list[AccountantClientRead])
def list_accountant_clients(
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
    include_inactive: bool = False,
) -> list[AccountantClient]:
    require_accountant_license(user, db)
    query = select(AccountantClient).where(AccountantClient.accountant_user_id == user.id)
    if not include_inactive:
        query = query.where(AccountantClient.active.is_(True))
    return db.scalars(query.order_by(AccountantClient.name).limit(1000)).all()


@app.post("/professional/clients", response_model=AccountantClientRead, status_code=201)
def create_accountant_client(
    payload: AccountantClientCreate,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> AccountantClient:
    require_accountant_license(user, db)
    client_record = AccountantClient(
        accountant_user_id=user.id,
        rif=payload.rif,
        name=payload.name.strip(),
        email=str(payload.email or ""),
        phone=payload.phone.strip(),
        address=payload.address.strip(),
        notes=payload.notes.strip(),
    )
    db.add(client_record)
    try:
        db.flush()
        add_audit(
            db, user.id, None, "accountant_client.create", "accountant_client", client_record.id, request
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ya existe un cliente con ese RIF en su cartera") from exc
    db.refresh(client_record)
    return client_record


@app.delete("/professional/clients/{client_id}", status_code=204)
def archive_accountant_client(
    client_id: str,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> Response:
    require_accountant_license(user, db)
    client_record = db.scalar(
        select(AccountantClient).where(
            AccountantClient.id == client_id,
            AccountantClient.accountant_user_id == user.id,
            AccountantClient.active.is_(True),
        )
    )
    if not client_record:
        raise HTTPException(status_code=404, detail="Cliente de cartera no encontrado")
    client_record.active = False
    add_audit(
        db, user.id, None, "accountant_client.archive", "accountant_client", client_record.id, request
    )
    db.commit()
    return Response(status_code=204)


@app.get("/professional/fees", response_model=list[AccountantFeeRead])
def list_accountant_fees(
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
    status_filter: str | None = None,
) -> list[AccountantFee]:
    require_accountant_license(user, db)
    if status_filter and status_filter not in {"open", "paid", "void"}:
        raise HTTPException(status_code=422, detail="Estado de honorario inválido")
    query = select(AccountantFee).where(AccountantFee.accountant_user_id == user.id)
    if status_filter:
        query = query.where(AccountantFee.status == status_filter)
    return db.scalars(query.order_by(AccountantFee.due_date.desc()).limit(1000)).all()


@app.post("/professional/fees", response_model=AccountantFeeRead, status_code=201)
def create_accountant_fee(
    payload: AccountantFeeCreate,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> AccountantFee:
    require_accountant_license(user, db)
    client_record = db.scalar(
        select(AccountantClient).where(
            AccountantClient.id == payload.client_id,
            AccountantClient.accountant_user_id == user.id,
            AccountantClient.active.is_(True),
        )
    )
    if not client_record:
        raise HTTPException(status_code=404, detail="Cliente activo no encontrado en su cartera")
    if payload.due_date < payload.issued_at:
        raise HTTPException(status_code=422, detail="El vencimiento no puede preceder a la emisión")
    fee = AccountantFee(accountant_user_id=user.id, **payload.model_dump())
    db.add(fee)
    try:
        db.flush()
        add_audit(db, user.id, None, "accountant_fee.create", "accountant_fee", fee.id, request)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ya existe un honorario con esa referencia") from exc
    db.refresh(fee)
    return fee


@app.post("/professional/fees/{fee_id}/mark-paid")
def mark_accountant_fee_paid(
    fee_id: str,
    payload: AccountantFeePayment,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, str]:
    require_accountant_license(user, db)
    fee = db.scalar(
        select(AccountantFee)
        .where(AccountantFee.id == fee_id, AccountantFee.accountant_user_id == user.id)
        .with_for_update()
    )
    if not fee:
        raise HTTPException(status_code=404, detail="Honorario no encontrado")
    if fee.status != "open":
        raise HTTPException(status_code=409, detail="Solo se puede registrar el cobro de un honorario pendiente")
    fee.status = "paid"
    fee.paid_at = payload.paid_at
    fee.payment_reference = payload.payment_reference.strip()
    add_audit(db, user.id, None, "accountant_fee.paid", "accountant_fee", fee.id, request)
    db.commit()
    return {"status": "paid", "fee_id": fee.id}


@app.post("/professional/fees/{fee_id}/send-reminder")
def send_accountant_fee_reminder(
    fee_id: str,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, str]:
    require_accountant_license(user, db)
    fee = db.scalar(
        select(AccountantFee).where(
            AccountantFee.id == fee_id,
            AccountantFee.accountant_user_id == user.id,
            AccountantFee.status == "open",
        )
    )
    if not fee:
        raise HTTPException(status_code=404, detail="Honorario pendiente no encontrado")
    client_record = db.get(AccountantClient, fee.client_id)
    if not client_record or not client_record.email:
        raise HTTPException(status_code=409, detail="Registre un correo en la ficha del cliente")
    body = (
        f"<p>Estimado/a {html.escape(client_record.name)}:</p>"
        f"<p>Le recordamos el pago de honorarios por {html.escape(fee.service_description)}.</p>"
        f"<p>Referencia {html.escape(fee.reference)} · Monto {fee.amount} {fee.currency} · "
        f"Vencimiento {fee.due_date.isoformat()}.</p>"
        "<p>Este aviso es un recordatorio de cobro y no constituye factura fiscal.</p>"
    )
    try:
        send_configured_email(
            client_record.email,
            f"Recordatorio de honorarios {fee.reference}",
            body,
        )
    except (OSError, RuntimeError, smtplib.SMTPException, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"No se pudo enviar el recordatorio: {exc}") from exc
    add_audit(db, user.id, None, "accountant_fee.reminder", "accountant_fee", fee.id, request)
    db.commit()
    return {"status": "sent", "recipient": client_record.email}


@app.get("/professional/fees/{fee_id}/invoice.html")
def accountant_fee_invoice(
    fee_id: str,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> Response:
    require_accountant_license(user, db)
    fee = db.scalar(
        select(AccountantFee).where(
            AccountantFee.id == fee_id,
            AccountantFee.accountant_user_id == user.id,
        )
    )
    if not fee:
        raise HTTPException(status_code=404, detail="Honorario no encontrado")
    client_record = db.get(AccountantClient, fee.client_id)
    if not client_record:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    return Response(
        render_accountant_fee_invoice(client_record, fee, user),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@app.post("/professional/fees/{fee_id}/send-invoice")
def send_accountant_fee_invoice(
    fee_id: str,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, str]:
    require_accountant_license(user, db)
    fee = db.scalar(
        select(AccountantFee).where(
            AccountantFee.id == fee_id,
            AccountantFee.accountant_user_id == user.id,
        )
    )
    if not fee:
        raise HTTPException(status_code=404, detail="Honorario no encontrado")
    client_record = db.get(AccountantClient, fee.client_id)
    if not client_record or not client_record.email:
        raise HTTPException(status_code=409, detail="Registre un correo en la ficha del cliente")
    try:
        send_configured_email(
            client_record.email,
            f"Cuenta de honorarios {fee.reference}",
            render_accountant_fee_invoice(client_record, fee, user),
        )
    except (OSError, RuntimeError, smtplib.SMTPException, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"No se pudo enviar la cuenta de cobro: {exc}") from exc
    add_audit(db, user.id, None, "accountant_fee.invoice_email", "accountant_fee", fee.id, request)
    db.commit()
    return {"status": "sent", "recipient": client_record.email}


@app.get("/admin/demo-requests", response_model=list[DemoAccessRequestRead])
def list_demo_access_requests(
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
    status_filter: str = "pending_review",
) -> list[DemoAccessRequest]:
    require_superuser(user)
    if status_filter not in {"pending_email", "pending_review", "approved", "rejected"}:
        raise HTTPException(status_code=422, detail="Estado de solicitud inválido")
    return db.scalars(
        select(DemoAccessRequest)
        .where(DemoAccessRequest.status == status_filter)
        .order_by(DemoAccessRequest.created_at.desc())
        .limit(200)
    ).all()


@app.post("/admin/demo-requests/{request_id}/decision")
def decide_demo_access_request(
    request_id: str,
    payload: DemoAccessRequestDecision,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, str]:
    require_superuser(user)
    demo_request = db.scalar(
        select(DemoAccessRequest)
        .where(DemoAccessRequest.id == request_id)
        .with_for_update()
    )
    if not demo_request or demo_request.status != "pending_review" or not demo_request.email_verified_at:
        raise HTTPException(
            status_code=409,
            detail="La solicitud debe tener correo verificado y estar pendiente de revisión",
        )
    now = datetime.now(timezone.utc)
    demo_request.reviewed_by = user.id
    demo_request.reviewed_at = now
    demo_request.review_notes = payload.notes.strip()
    if payload.decision == "reject":
        demo_request.status = "rejected"
        add_audit(db, user.id, None, "demo.request.reject", "demo_access_request", demo_request.id, request)
        db.commit()
        return {"status": "rejected", "request_id": demo_request.id}
    if db.scalar(select(User.id).where(User.email == demo_request.email)):
        raise HTTPException(status_code=409, detail="El correo ya tiene una cuenta")
    if db.scalar(select(User.id).where(User.identity_number == demo_request.identity_number)):
        raise HTTPException(status_code=409, detail="La cédula ya está asociada a una cuenta")
    if db.scalar(select(User.id).where(User.personal_rif == demo_request.rif)):
        raise HTTPException(status_code=409, detail="El RIF personal ya está asociado a una cuenta")
    account_user = User(
        email=demo_request.email,
        password_hash=demo_request.password_hash,
        active=True,
        is_superuser=False,
        identity_number=demo_request.identity_number,
        personal_rif=demo_request.rif,
        personal_address=demo_request.address,
        profession=demo_request.profession,
    )
    db.add(account_user)
    db.flush()
    today = date.today()
    access_license = AccessLicense(
        user_id=account_user.id,
        role=payload.role,
        status="active",
        subscribed_at=today,
        expires_at=today + timedelta(days=4),
        max_companies=1,
        created_by=user.id,
    )
    db.add(access_license)
    db.flush()
    demo_request.status = "approved"
    add_audit(db, user.id, None, "demo.request.approve", "demo_access_request", demo_request.id, request)
    add_audit(db, user.id, None, "demo.license.create", "access_license", access_license.id, request)
    db.commit()
    return {
        "status": "approved",
        "request_id": demo_request.id,
        "email": account_user.email,
        "expires_at": access_license.expires_at.isoformat(),
        "license_role": access_license.role,
    }


@app.get("/admin/users", response_model=list[UserRead])
def list_users(
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> list[User]:
    require_superuser(user)
    return list(db.scalars(select(User).order_by(User.email)).all())


def license_summary(db: Session, license_access: AccessLicense, account: User) -> dict[str, object]:
    companies = db.scalar(
        select(func.count()).select_from(Company).where(Company.license_id == license_access.id)
    ) or 0
    payments = list(
        db.scalars(
            select(LicensePayment)
            .where(LicensePayment.license_id == license_access.id)
            .order_by(LicensePayment.paid_at.desc(), LicensePayment.created_at.desc())
        ).all()
    )
    totals: dict[str, Decimal] = {}
    for payment in payments:
        totals[payment.currency] = totals.get(payment.currency, Decimal("0")) + payment.amount
    return {
        "id": license_access.id,
        "user_id": account.id,
        "email": account.email,
        "role": license_access.role,
        "status": (
            "expired"
            if license_access.status == "active" and license_access.expires_at < date.today()
            else license_access.status
        ),
        "subscribed_at": license_access.subscribed_at,
        "expires_at": license_access.expires_at,
        "max_companies": license_access.max_companies,
        "company_count": companies,
        "payments_count": len(payments),
        "payment_totals": {currency: str(amount) for currency, amount in totals.items()},
        "last_payment_at": payments[0].paid_at if payments else None,
    }


@app.get("/admin/licenses")
def list_access_licenses(
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> list[dict[str, object]]:
    require_superuser(user)
    records = db.execute(
        select(AccessLicense, User)
        .join(User, AccessLicense.user_id == User.id)
        .order_by(User.email)
    ).all()
    return [license_summary(db, license_access, account) for license_access, account in records]


@app.post("/admin/licenses", status_code=201)
def create_access_license(
    payload: AccessLicenseCreate,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, object]:
    require_superuser(user)
    account = User(
        email=str(payload.email).lower(),
        password_hash=hash_password(payload.password),
    )
    db.add(account)
    try:
        db.flush()
        license_access = AccessLicense(
            user_id=account.id,
            role=payload.role,
            status="active",
            subscribed_at=payload.subscribed_at,
            expires_at=payload.expires_at,
            max_companies=payload.max_companies,
            created_by=user.id,
        )
        db.add(license_access)
        db.flush()
        add_audit(db, user.id, None, "license.create", "access_license", license_access.id, request)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El correo ya está registrado") from exc
    db.refresh(license_access)
    return license_summary(db, license_access, account)


@app.put("/admin/licenses/{license_id}")
def update_access_license(
    license_id: str,
    payload: AccessLicenseUpdate,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, object]:
    require_superuser(user)
    license_access = db.get(AccessLicense, license_id)
    if not license_access:
        raise HTTPException(status_code=404, detail="Licencia no encontrada")
    account = db.get(User, license_access.user_id)
    if not account:
        raise HTTPException(status_code=409, detail="La cuenta asociada a la licencia no existe")
    account.email = str(payload.email).lower()
    if payload.password:
        account.password_hash = hash_password(payload.password)
    license_access.role = payload.role
    license_access.subscribed_at = payload.subscribed_at
    license_access.expires_at = payload.expires_at
    license_access.max_companies = payload.max_companies
    for membership in db.scalars(
        select(Membership).where(Membership.user_id == license_access.user_id)
    ).all():
        company = db.get(Company, membership.company_id)
        if company and company.license_id == license_access.id:
            membership.role = payload.role
    add_audit(db, user.id, None, "license.update", "access_license", license_id, request)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El correo ya está registrado") from exc
    db.refresh(license_access)
    return license_summary(db, license_access, account)


@app.put("/admin/licenses/{license_id}/status")
def set_access_license_status(
    license_id: str,
    status: str,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, str]:
    require_superuser(user)
    if status not in {"active", "suspended"}:
        raise HTTPException(status_code=422, detail="El estado debe ser active o suspended")
    license_access = db.get(AccessLicense, license_id)
    if not license_access:
        raise HTTPException(status_code=404, detail="Licencia no encontrada")
    license_access.status = status
    account = db.get(User, license_access.user_id)
    if account:
        account.active = True
    add_audit(db, user.id, None, f"license.{status}", "access_license", license_id, request)
    db.commit()
    return {"id": license_id, "status": status}


@app.delete("/admin/licenses/{license_id}")
def cancel_access_license(
    license_id: str,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, str]:
    require_superuser(user)
    license_access = db.get(AccessLicense, license_id)
    if not license_access:
        raise HTTPException(status_code=404, detail="Licencia no encontrada")
    license_access.status = "cancelled"
    add_audit(db, user.id, None, "license.cancel", "access_license", license_id, request)
    db.commit()
    return {"id": license_id, "status": "cancelled"}


@app.get("/admin/licenses/{license_id}/payments")
def list_license_payments(
    license_id: str,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> list[dict[str, object]]:
    require_superuser(user)
    if not db.get(AccessLicense, license_id):
        raise HTTPException(status_code=404, detail="Licencia no encontrada")
    payments = db.scalars(
        select(LicensePayment)
        .where(LicensePayment.license_id == license_id)
        .order_by(LicensePayment.paid_at.desc(), LicensePayment.created_at.desc())
    ).all()
    return [
        {
            "id": payment.id,
            "paid_at": payment.paid_at,
            "amount": str(payment.amount),
            "currency": payment.currency,
            "method": payment.method,
            "reference": payment.reference,
            "notes": payment.notes,
        }
        for payment in payments
    ]


@app.post("/admin/licenses/{license_id}/payments", status_code=201)
def record_license_payment(
    license_id: str,
    payload: LicensePaymentCreate,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, object]:
    require_superuser(user)
    if not db.get(AccessLicense, license_id):
        raise HTTPException(status_code=404, detail="Licencia no encontrada")
    payment = LicensePayment(
        license_id=license_id,
        recorded_by=user.id,
        **payload.model_dump(),
    )
    db.add(payment)
    db.flush()
    add_audit(db, user.id, None, "license.payment.record", "license_payment", payment.id, request)
    db.commit()
    return {
        "id": payment.id,
        "license_id": payment.license_id,
        "paid_at": payment.paid_at,
        "amount": str(payment.amount),
        "currency": payment.currency,
        "method": payment.method,
        "reference": payment.reference,
        "notes": payment.notes,
    }


@app.get("/admin/license-stats")
def get_license_statistics(
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, object]:
    require_superuser(user)
    if db.bind and db.bind.dialect.name == "postgresql":
        database_size = int(db.scalar(text("SELECT pg_database_size(current_database())")) or 0)
    else:
        database_path = engine.url.database
        database_size = Path(database_path).stat().st_size if database_path and Path(database_path).is_file() else 0

    records = db.execute(
        select(AccessLicense, User)
        .join(User, AccessLicense.user_id == User.id)
        .order_by(User.email)
    ).all()
    usage_models = (
        Account, Journal, FiscalPeriod, Contact, TaxDocument,
        OpeningBalance, Payment, BankStatementLine, AuditEvent,
    )
    license_rows: list[dict[str, object]] = []
    total_companies = 0
    for license_access, account in records:
        company_ids = list(
            db.scalars(select(Company.id).where(Company.license_id == license_access.id)).all()
        )
        company_count = len(company_ids)
        total_companies += company_count
        estimated_bytes = 0
        if company_ids and db.bind and db.bind.dialect.name == "postgresql":
            for model in (Company, *usage_models):
                estimated_bytes += int(
                    db.scalar(
                        select(
                            func.coalesce(
                                func.sum(func.pg_column_size(model.__table__.table_valued())),
                                0,
                            )
                        )
                        .where(model.license_id == license_access.id)
                        if model is Company
                        else select(
                            func.coalesce(
                                func.sum(func.pg_column_size(model.__table__.table_valued())),
                                0,
                            )
                        )
                        .where(model.company_id.in_(company_ids))
                    ) or 0
                )
            journal_ids = select(Journal.id).where(Journal.company_id.in_(company_ids))
            estimated_bytes += int(
                db.scalar(
                    select(
                        func.coalesce(
                            func.sum(func.pg_column_size(JournalLine.__table__.table_valued())),
                            0,
                        )
                    )
                    .where(JournalLine.journal_id.in_(journal_ids))
                ) or 0
            )
        elif company_count:
            row_count = sum(
                int(
                    db.scalar(
                        select(func.count()).select_from(model).where(model.company_id.in_(company_ids))
                    ) or 0
                )
                for model in usage_models
            )
            estimated_bytes = company_count * 256 + row_count * 256
        item = license_summary(db, license_access, account)
        item["estimated_data_bytes"] = estimated_bytes
        item["storage_estimate"] = True
        license_rows.append(item)
    today = date.today()
    return {
        "database_size_bytes": database_size,
        "license_count": len(records),
        "active_licenses": sum(
            access.status == "active" and access.expires_at >= today
            for access, _ in records
        ),
        "suspended_licenses": sum(access.status == "suspended" for access, _ in records),
        "expired_licenses": sum(
            access.status == "active" and access.expires_at < today
            for access, _ in records
        ),
        "company_count": total_companies,
        "storage_note": "El uso por acceso es estimado; PostgreSQL comparte sus archivos entre todos los accesos.",
        "licenses": license_rows,
    }


@app.post("/admin/users", response_model=UserRead, status_code=201)
def create_user(
    payload: UserCreate,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> User:
    require_superuser(user)
    raise HTTPException(
        status_code=409,
        detail="Cree los accesos desde el módulo de licencias para asignarles su suscripción.",
    )


@app.put("/admin/users/{user_id}/active")
def set_user_active(
    user_id: str,
    active: bool,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, bool]:
    require_superuser(user)
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if target.id == user.id and not active:
        raise HTTPException(status_code=409, detail="No puedes desactivar tu propio usuario")
    target.active = active
    add_audit(db, user.id, None, "user.active.update", "user", target.id, request)
    db.commit()
    return {"active": target.active}


@app.post("/admin/users/{user_id}/companies/{company_id}", status_code=201)
def assign_user_to_company(
    user_id: str,
    company_id: str,
    payload: MembershipCreate,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, str]:
    require_superuser(user)
    target = db.get(User, user_id)
    company = db.get(Company, company_id)
    if not target or not company:
        raise HTTPException(status_code=404, detail="Usuario o empresa no encontrado")
    if str(payload.email).lower() != target.email:
        raise HTTPException(status_code=422, detail="El correo no coincide con el usuario seleccionado")
    membership = Membership(user_id=target.id, company_id=company.id, role=payload.role)
    db.add(membership)
    try:
        db.flush()
        add_audit(db, user.id, company.id, "membership.create", "membership", membership.id, request)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El usuario ya tiene acceso a esta empresa") from exc
    return {"status": "created", "user_id": target.id, "company_id": company.id, "role": payload.role}


@app.get("/admin/companies/{company_id}/members")
def list_company_members(
    company_id: str,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> list[dict[str, str | bool]]:
    require_superuser(user)
    rows = db.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.company_id == company_id)
        .order_by(User.email)
    )
    return [
        {
            "membership_id": membership.id,
            "user_id": account.id,
            "email": account.email,
            "role": membership.role,
            "active": account.active,
        }
        for account, membership in rows
    ]


@app.put("/admin/members/{membership_id}")
def update_membership_role(
    membership_id: str,
    payload: MembershipCreate,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, str]:
    require_superuser(user)
    membership = db.get(Membership, membership_id)
    if not membership:
        raise HTTPException(status_code=404, detail="Acceso a empresa no encontrado")
    target = db.get(User, membership.user_id)
    if not target or target.email != str(payload.email).lower():
        raise HTTPException(status_code=422, detail="El correo no coincide con el acceso seleccionado")
    membership.role = payload.role
    add_audit(
        db, user.id, membership.company_id, "membership.role.update",
        "membership", membership.id, request,
    )
    db.commit()
    return {"membership_id": membership.id, "role": membership.role}


@app.get("/admin/settings")
def get_admin_settings(
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, int]:
    require_superuser(user)
    setting = db.get(SystemSetting, "company_limit")
    return {"company_limit": int(setting.value) if setting else 10}


@app.put("/admin/settings")
def update_admin_settings(
    payload: SystemSettingsUpdate,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, int]:
    require_superuser(user)
    setting = db.get(SystemSetting, "company_limit")
    if not setting:
        setting = SystemSetting(key="company_limit", value=str(payload.company_limit))
        db.add(setting)
    else:
        setting.value = str(payload.company_limit)
    add_audit(db, user.id, None, "settings.company_limit.update", "system_setting", "company_limit", request)
    db.commit()
    return {"company_limit": payload.company_limit}


@app.get("/companies", response_model=list[CompanyRead])
def list_companies(
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> list[Company]:
    if user.is_superuser:
        return list(db.scalars(select(Company).order_by(Company.legal_name)).all())
    license_access = db.scalar(
        select(AccessLicense).where(AccessLicense.user_id == user.id)
    )
    if not license_access:
        return []
    return list(
        db.scalars(
            select(Company)
            .join(Membership)
            .where(
                Membership.user_id == user.id,
            )
            .order_by(Company.legal_name)
        ).all()
    )


@app.post("/companies", response_model=CompanyRead, status_code=201)
def create_company(
    payload: CompanyCreate,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> Company:
    license_access = None
    if user.is_superuser:
        if db.bind and db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(1096845396)"))
        limit = db.get(SystemSetting, "company_limit")
        company_limit = int(limit.value) if limit else 10
        current_count = db.scalar(
            select(func.count())
            .select_from(Company)
            .where(Company.license_id.is_(None), Company.active.is_(True))
        ) or 0
    else:
        license_access = db.scalar(
            select(AccessLicense).where(
                AccessLicense.user_id == user.id,
                AccessLicense.status == "active",
                AccessLicense.expires_at >= date.today(),
            )
        )
        if not license_access:
            raise HTTPException(status_code=403, detail="Se requiere una licencia activa para crear empresas")
        if db.bind and db.bind.dialect.name == "postgresql":
            lock_id = int(license_access.id.replace("-", "")[:15], 16)
            db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
        company_limit = license_access.max_companies
        current_count = db.scalar(
            select(func.count())
            .select_from(Company)
            .where(
                Company.license_id == license_access.id,
                Company.active.is_(True),
            )
        ) or 0
    if current_count >= company_limit:
        raise HTTPException(
            status_code=409,
            detail=f"Se alcanzó el límite de {company_limit} empresas de la licencia",
        )
    company = Company(**payload.model_dump(), license_id=license_access.id if license_access else None)
    db.add(company)
    try:
        db.flush()
        db.add(
            Membership(
                user_id=user.id,
                company_id=company.id,
                role=license_access.role if license_access else "admin",
            )
        )
        add_audit(db, user.id, company.id, "company.create", "company", company.id, request)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El RIF ya está registrado") from exc
    db.refresh(company)
    return company


def require_company_admin_access(company_id: str, user: User, db: Session) -> Company:
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    if user.is_superuser:
        return company
    membership = db.scalar(
        select(Membership).where(
            Membership.company_id == company_id,
            Membership.user_id == user.id,
        )
    )
    if not membership:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    if membership.role != "admin":
        raise HTTPException(status_code=403, detail="Se requiere rol administrador de la empresa")
    return company


@app.put("/companies/{company_id}", response_model=CompanyRead)
def update_company(
    company_id: str,
    payload: CompanyUpdate,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> Company:
    company = require_company_admin_access(company_id, user, db)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in changes.items():
        setattr(company, field, value)
    add_audit(db, user.id, company.id, "company.update", "company", company.id, request)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El RIF ya está registrado") from exc
    db.refresh(company)
    return company


@app.delete("/companies/{company_id}")
def archive_company(
    company_id: str,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, str]:
    company = require_company_admin_access(company_id, user, db)
    if not company.active:
        raise HTTPException(status_code=409, detail="La empresa ya está archivada")
    company.active = False
    add_audit(db, user.id, company.id, "company.archive", "company", company.id, request)
    db.commit()
    return {"id": company.id, "status": "archived"}


@app.post("/companies/{company_id}/restore")
def restore_company(
    company_id: str,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> dict[str, str]:
    company = require_company_admin_access(company_id, user, db)
    if company.active:
        raise HTTPException(status_code=409, detail="La empresa ya está activa")
    if not user.is_superuser:
        license_access = db.scalar(
            select(AccessLicense).where(
                AccessLicense.user_id == user.id,
                AccessLicense.status == "active",
                AccessLicense.expires_at >= date.today(),
            )
        )
        if not license_access:
            raise HTTPException(status_code=403, detail="Se requiere una licencia activa")
        owned_count = db.scalar(
            select(func.count()).select_from(Company).where(
                Company.license_id == license_access.id,
                Company.active.is_(True),
            )
        ) or 0
        if company.license_id == license_access.id and owned_count >= license_access.max_companies:
            raise HTTPException(status_code=409, detail="Se alcanzó el límite de empresas de la licencia")
    else:
        setting = db.get(SystemSetting, "company_limit")
        company_limit = int(setting.value) if setting else 10
        active_legacy_companies = db.scalar(
            select(func.count())
            .select_from(Company)
            .where(Company.license_id.is_(None), Company.active.is_(True))
        ) or 0
        if company.license_id is None and active_legacy_companies >= company_limit:
            raise HTTPException(status_code=409, detail="Se alcanzó el límite global de empresas")
    company.active = True
    add_audit(db, user.id, company.id, "company.restore", "company", company.id, request)
    db.commit()
    return {"id": company.id, "status": "active"}


@app.put("/companies/{company_id}/branding")
def update_company_branding(
    company_id: str,
    payload: CompanyBrandingUpdate,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"admin"}))],
    db: DbSession,
) -> dict[str, str]:
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    if payload.contact_email is not None:
        company.contact_email = str(payload.contact_email)
    company.contact_phone = payload.contact_phone.strip()
    add_audit(db, membership.user_id, company_id, "company.branding.update", "company", company_id, request)
    db.commit()
    return {"status": "updated", "contact_email": company.contact_email}


@app.post("/companies/{company_id}/branding/logo")
async def upload_company_logo(
    company_id: str,
    request: Request,
    file: Annotated[UploadFile, File()],
    membership: Annotated[Membership, Depends(require_company_role({"admin"}))],
    db: DbSession,
) -> dict[str, str]:
    content_type = (file.content_type or "").split(";", 1)[0].lower()
    signatures = {
        "image/png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": lambda data: data.startswith(b"\xff\xd8\xff"),
        "image/webp": lambda data: len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP",
    }
    if content_type not in signatures:
        raise HTTPException(status_code=415, detail="El logo debe ser PNG, JPEG o WebP")
    content = await file.read(1024 * 1024 + 1)
    if not content or len(content) > 1024 * 1024:
        raise HTTPException(status_code=413, detail="El logo debe ocupar entre 1 byte y 1 MB")
    if not signatures[content_type](content):
        raise HTTPException(status_code=415, detail="El contenido no coincide con el tipo de imagen")
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    company.logo_content = content
    company.logo_content_type = content_type
    add_audit(db, membership.user_id, company_id, "company.logo.update", "company", company_id, request)
    db.commit()
    return {"status": "updated"}


@app.post("/companies/{company_id}/members", status_code=201)
def add_member(
    company_id: str,
    payload: CompanyMemberInvite,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"admin"}))],
    db: DbSession,
) -> dict[str, str]:
    company = db.get(Company, company_id)
    if not company or not company.active:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    target = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    target_license = db.scalar(
        select(AccessLicense).where(
            AccessLicense.user_id == target.id,
            AccessLicense.status == "active",
            AccessLicense.expires_at >= date.today(),
        )
    )
    if not target.active or not target_license:
        raise HTTPException(
            status_code=403,
            detail="Solo se pueden invitar accesos con una licencia vigente asignada por el administrador del sistema.",
        )
    try:
        new_membership = Membership(
            user_id=target.id,
            company_id=company_id,
            role=target_license.role,
        )
        db.add(new_membership)
        add_audit(
            db,
            membership.user_id,
            company_id,
            "membership.create",
            "membership",
            new_membership.id,
            request,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="El usuario ya tiene acceso a esta empresa"
        ) from exc
    return {
        "status": "created",
        "company_id": company_id,
        "user_id": target.id,
        "role": target_license.role,
    }


@app.get("/companies/{company_id}/members")
def list_company_members(
    company_id: str,
    membership: Annotated[Membership, Depends(require_company_role({"admin"}))],
    db: DbSession,
) -> list[dict[str, str]]:
    rows = db.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.company_id == company_id)
        .order_by(User.email)
    )
    return [
        {"user_id": account.id, "email": account.email, "role": member.role}
        for account, member in rows
    ]


@app.get("/companies/{company_id}/accounts", response_model=list[AccountRead])
def list_accounts(
    company_id: str,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))],
    db: DbSession,
) -> list[Account]:
    return list(
        db.scalars(
            select(Account).where(Account.company_id == company_id).order_by(Account.code)
        ).all()
    )


@app.post("/companies/{company_id}/accounts", response_model=AccountRead, status_code=201)
def create_account(
    company_id: str,
    payload: AccountCreate,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"accountant", "admin"}))],
    db: DbSession,
) -> Account:
    if payload.parent_id:
        parent = db.get(Account, payload.parent_id)
        if (
            not parent
            or parent.company_id != company_id
            or parent.status != "approved"
            or not parent.active
        ):
            raise HTTPException(
                status_code=422,
                detail="La cuenta padre debe estar aprobada y pertenecer a la empresa",
            )
    account = Account(
        company_id=company_id,
        created_by=membership.user_id,
        status="pending",
        active=False,
        **payload.model_dump(),
    )
    db.add(account)
    try:
        db.flush()
        add_audit(db, membership.user_id, company_id, "account.create", "account", account.id, request)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El código de cuenta ya existe") from exc
    db.refresh(account)
    return account


@app.post("/companies/{company_id}/accounts/{account_id}/approve")
def approve_account(
    company_id: str,
    account_id: str,
    request: Request,
    membership: Annotated[
        Membership, Depends(require_company_role({"admin", "accountant"}))
    ],
    db: DbSession,
) -> dict[str, str]:
    account = db.scalar(
        select(Account)
        .where(Account.id == account_id, Account.company_id == company_id)
        .with_for_update()
    )
    if not account:
        raise HTTPException(status_code=404, detail="Cuenta contable no encontrada")
    if account.status != "pending":
        raise HTTPException(status_code=409, detail="La cuenta no está pendiente de aprobación")
    if not account.created_by or account.created_by == membership.user_id:
        raise HTTPException(status_code=409, detail="El autor no puede aprobar su propia cuenta")
    account.status = "approved"
    account.active = True
    account.reviewed_by = membership.user_id
    add_audit(db, membership.user_id, company_id, "account.approve", "account", account.id, request)
    db.commit()
    return {"id": account.id, "status": account.status}


VENEZUELA_CHART = [
    ("1", "ACTIVO", "asset", None, True),
    ("11", "Efectivo y equivalentes", "asset", "1", True),
    ("1101", "Caja", "asset", "11", True),
    ("1102", "Bancos", "asset", "11", True),
    ("12", "Cuentas por cobrar", "asset", "1", True),
    ("1201", "Clientes", "asset", "12", True),
    ("1202", "IVA crédito fiscal", "asset", "12", True),
    ("13", "Inventarios", "asset", "1", False),
    ("2", "PASIVO", "liability", None, True),
    ("21", "Pasivo corriente", "liability", "2", True),
    ("2101", "Proveedores", "liability", "21", True),
    ("2102", "Tributos por pagar", "liability", "21", True),
    ("210201", "IVA débito fiscal", "liability", "2102", True),
    ("210202", "IVA retenido por pagar", "liability", "2102", True),
    ("210203", "IGTF por pagar", "liability", "2102", True),
    ("3", "PATRIMONIO", "equity", None, True),
    ("31", "Capital social", "equity", "3", True),
    ("32", "Resultados acumulados", "equity", "3", True),
    ("4", "INGRESOS", "revenue", None, True),
    ("41", "Ingresos por ventas", "revenue", "4", True),
    ("5", "COSTOS Y GASTOS", "expense", None, True),
    ("51", "Costo de ventas", "expense", "5", False),
    ("52", "Gastos operativos", "expense", "5", True),
    ("5201", "Servicios profesionales", "expense", "52", True),
    ("6", "OTROS INGRESOS Y GASTOS", "revenue", None, True),
    ("61", "Otros ingresos", "revenue", "6", True),
    ("62", "Gastos financieros e IGTF", "expense", "5", True),
]


@app.post("/companies/{company_id}/accounts/templates/venezuela", status_code=201)
def install_venezuela_chart(
    company_id: str,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant"}))],
    db: DbSession,
) -> dict[str, int]:
    existing = {
        account.code: account
        for account in db.scalars(select(Account).where(Account.company_id == company_id)).all()
    }
    created = 0
    for code, name, account_type, parent_code, monetary in VENEZUELA_CHART:
        if code in existing:
            if existing[code].status != "approved" or not existing[code].active:
                raise HTTPException(
                    status_code=409,
                    detail=f"La cuenta {code} debe aprobarse o corregirse antes de instalar la plantilla.",
                )
            continue
        parent = existing.get(parent_code) if parent_code else None
        account = Account(
            company_id=company_id,
            code=code,
            name=name,
            account_type=account_type,
            parent_id=parent.id if parent else None,
            monetary=monetary,
            status="approved",
            active=True,
        )
        db.add(account)
        db.flush()
        existing[code] = account
        created += 1
    add_audit(
        db,
        membership.user_id,
        company_id,
        "account.template.install",
        "account",
        "",
        request,
    )
    db.commit()
    return {"created": created, "total": len(existing)}


@app.post("/companies/{company_id}/opening-balances", status_code=201)
def create_opening_balance(
    company_id: str,
    payload: OpeningBalanceCreate,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant"}))],
    db: DbSession,
) -> dict[str, str]:
    company = db.scalar(
        select(Company).where(Company.id == company_id).with_for_update()
    )
    if not company:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    if db.scalar(select(OpeningBalance.id).where(OpeningBalance.company_id == company_id)):
        raise HTTPException(status_code=409, detail="Ya se registraron saldos de apertura")
    ensure_period_open(db, company_id, payload.as_of_date)
    account_ids = {line.account_id for line in payload.lines}
    accounts = list(
        db.scalars(
            select(Account).where(
                Account.company_id == company_id,
                Account.id.in_(account_ids),
                Account.active.is_(True),
                Account.status == "approved",
            )
        ).all()
    )
    if len(accounts) != len(account_ids):
        raise HTTPException(status_code=422, detail="Cada cuenta debe estar activa y pertenecer a la empresa")
    if any(account.account_type not in {"asset", "liability", "equity"} for account in accounts):
        raise HTTPException(status_code=422, detail="Los saldos de apertura solo admiten cuentas de balance")
    if any((line.debit > 0) == (line.credit > 0) for line in payload.lines):
        raise HTTPException(status_code=422, detail="Cada saldo debe tener solo debe o haber")
    debit_total = sum((line.debit for line in payload.lines), Decimal("0"))
    credit_total = sum((line.credit for line in payload.lines), Decimal("0"))
    if debit_total <= 0 or debit_total != credit_total:
        raise HTTPException(status_code=422, detail="Los saldos de apertura deben cuadrar y ser mayores a cero")

    journal = Journal(
        company_id=company_id,
        entry_date=payload.as_of_date,
        description="Saldos de apertura",
        reference=f"APERTURA-{payload.as_of_date:%Y%m%d}",
        status="pending",
        source_type="opening",
        created_by=membership.user_id,
        lines=[JournalLine(**line.model_dump()) for line in payload.lines],
    )
    db.add(journal)
    db.flush()
    opening = OpeningBalance(
        company_id=company_id,
        as_of_date=payload.as_of_date,
        journal_id=journal.id,
        created_by=membership.user_id,
    )
    db.add(opening)
    try:
        db.flush()
        add_audit(db, membership.user_id, company_id, "opening_balance.create", "journal", journal.id, request)
        add_audit(
            db,
            membership.user_id,
            company_id,
            "opening_balance.submit",
            "journal",
            journal.id,
            request,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Los saldos de apertura ya fueron registrados") from exc
    return {"journal_id": journal.id, "status": "pending"}


@app.get("/companies/{company_id}/opening-balances")
def get_opening_balance(
    company_id: str,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))],
    db: DbSession,
) -> dict[str, str] | None:
    opening = db.scalar(select(OpeningBalance).where(OpeningBalance.company_id == company_id))
    if not opening:
        return None
    return {
        "id": opening.id,
        "as_of_date": opening.as_of_date.isoformat(),
        "journal_id": opening.journal_id,
        "status": db.get(Journal, opening.journal_id).status,
    }


@app.get("/companies/{company_id}/contacts", response_model=list[ContactRead])
def list_contacts(
    company_id: str,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))],
    db: DbSession,
    kind: str | None = None,
) -> list[Contact]:
    query = select(Contact).where(Contact.company_id == company_id, Contact.active.is_(True))
    if kind:
        if kind not in {"customer", "supplier", "both"}:
            raise HTTPException(status_code=422, detail="Tipo de contacto inválido")
        query = query.where(Contact.kind.in_([kind, "both"]))
    return list(db.scalars(query.order_by(Contact.name)).all())


@app.post("/companies/{company_id}/contacts", response_model=ContactRead, status_code=201)
def create_contact(
    company_id: str,
    payload: ContactCreate,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant"}))],
    db: DbSession,
) -> Contact:
    contact = Contact(
        company_id=company_id,
        rif=payload.rif,
        name=payload.name.strip(),
        kind=payload.kind,
        email=str(payload.email or ""),
        phone=payload.phone,
        address=payload.address,
    )
    db.add(contact)
    try:
        db.flush()
        add_audit(db, membership.user_id, company_id, "contact.create", "contact", contact.id, request)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ya existe un cliente/proveedor con ese RIF") from exc
    db.refresh(contact)
    return contact


@app.get(
    "/companies/{company_id}/professional-fees",
    response_model=list[ProfessionalFeeRead],
)
def list_professional_fees(
    company_id: str,
    membership: Annotated[
        Membership, Depends(require_company_role({"accountant", "admin"}))
    ],
    db: DbSession,
    status_filter: str | None = None,
) -> list[ProfessionalFee]:
    if status_filter and status_filter not in {"open", "paid", "void"}:
        raise HTTPException(status_code=422, detail="Estado de honorario inválido")
    query = select(ProfessionalFee).where(ProfessionalFee.company_id == company_id)
    if status_filter:
        query = query.where(ProfessionalFee.status == status_filter)
    return db.scalars(
        query.order_by(ProfessionalFee.issued_at.desc(), ProfessionalFee.created_at.desc()).limit(500)
    ).all()


@app.post(
    "/companies/{company_id}/professional-fees",
    response_model=ProfessionalFeeRead,
    status_code=201,
)
def create_professional_fee(
    company_id: str,
    payload: ProfessionalFeeCreate,
    request: Request,
    membership: Annotated[
        Membership, Depends(require_company_role({"accountant"}))
    ],
    db: DbSession,
) -> ProfessionalFee:
    contact = db.scalar(
        select(Contact).where(
            Contact.id == payload.contact_id,
            Contact.company_id == company_id,
            Contact.active.is_(True),
            Contact.kind.in_(["customer", "both"]),
        )
    )
    if not contact:
        raise HTTPException(status_code=404, detail="Cliente activo no encontrado")
    if payload.due_date < payload.issued_at:
        raise HTTPException(status_code=422, detail="El vencimiento no puede preceder a la emisión")
    fee_values = payload.model_dump()
    fee = ProfessionalFee(
        company_id=company_id,
        created_by=membership.user_id,
        **fee_values,
    )
    db.add(fee)
    try:
        db.flush()
        add_audit(db, membership.user_id, company_id, "professional_fee.create", "professional_fee", fee.id, request)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ya existe un honorario con esa referencia") from exc
    db.refresh(fee)
    return fee


@app.post("/companies/{company_id}/professional-fees/{fee_id}/mark-paid")
def mark_professional_fee_paid(
    company_id: str,
    fee_id: str,
    payload: ProfessionalFeePayment,
    request: Request,
    membership: Annotated[
        Membership, Depends(require_company_role({"accountant"}))
    ],
    db: DbSession,
) -> dict[str, str]:
    fee = db.scalar(
        select(ProfessionalFee)
        .where(ProfessionalFee.id == fee_id, ProfessionalFee.company_id == company_id)
        .with_for_update()
    )
    if not fee:
        raise HTTPException(status_code=404, detail="Honorario no encontrado")
    if fee.status != "open":
        raise HTTPException(status_code=409, detail="Solo se puede cobrar un honorario pendiente")
    fee.status = "paid"
    fee.paid_at = payload.paid_at
    fee.payment_reference = payload.payment_reference.strip()
    add_audit(db, membership.user_id, company_id, "professional_fee.paid", "professional_fee", fee.id, request)
    db.commit()
    return {"status": "paid", "fee_id": fee.id}


@app.post("/companies/{company_id}/professional-fees/{fee_id}/send-reminder")
def send_professional_fee_reminder(
    company_id: str,
    fee_id: str,
    request: Request,
    membership: Annotated[
        Membership, Depends(require_company_role({"accountant"}))
    ],
    db: DbSession,
) -> dict[str, str]:
    fee = db.scalar(
        select(ProfessionalFee).where(
            ProfessionalFee.id == fee_id,
            ProfessionalFee.company_id == company_id,
            ProfessionalFee.status == "open",
        )
    )
    if not fee:
        raise HTTPException(status_code=404, detail="Honorario pendiente no encontrado")
    contact = db.get(Contact, fee.contact_id)
    if not contact or not contact.email:
        raise HTTPException(status_code=409, detail="Registre un correo en la ficha del cliente")
    body = (
        f"<p>Estimado/a {html.escape(contact.name)}:</p>"
        f"<p>Le recordamos el pago de honorarios por {html.escape(fee.service_description)}.</p>"
        f"<p>Referencia {html.escape(fee.reference)} · Monto {fee.amount} {fee.currency} · "
        f"Vencimiento {fee.due_date.isoformat()}.</p>"
        "<p>Este aviso es un recordatorio de cobro y no constituye factura fiscal.</p>"
    )
    try:
        send_configured_email(contact.email, f"Recordatorio de honorarios {fee.reference}", body)
    except (OSError, RuntimeError, smtplib.SMTPException, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"No se pudo enviar el recordatorio: {exc}") from exc
    add_audit(
        db,
        membership.user_id,
        company_id,
        "professional_fee.reminder",
        "professional_fee",
        fee.id,
        request,
    )
    db.commit()
    return {"status": "sent", "recipient": contact.email}


@app.get("/companies/{company_id}/contacts/{contact_id}/balance")
def contact_balance(
    company_id: str,
    contact_id: str,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))],
    db: DbSession,
) -> dict[str, str]:
    contact = db.scalar(
        select(Contact).where(Contact.id == contact_id, Contact.company_id == company_id)
    )
    if not contact:
        raise HTTPException(status_code=404, detail="Cliente/proveedor no encontrado")
    docs = db.scalars(
        select(TaxDocument).where(
            TaxDocument.company_id == company_id,
            TaxDocument.contact_id == contact_id,
            TaxDocument.status == "posted",
        )
    ).all()
    result = Decimal("0")
    for document in docs:
        payments = db.scalars(
            select(Payment).where(
                Payment.document_id == document.id, Payment.status == "posted"
            )
        ).all()
        opening_amount = document.total_amount - document.vat_withheld
        paid_amount = sum((payment.amount_ves for payment in payments), Decimal("0"))
        outstanding = max(Decimal("0"), opening_amount - paid_amount)
        result += outstanding if document.direction == "sale" else -outstanding
    return {"balance_ves": str(money(result))}


@app.get("/companies/{company_id}/journals", response_model=list[JournalRead])
def list_journals(
    company_id: str,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))],
    db: DbSession,
    start_date: date | None = None,
    end_date: date | None = None,
    status_filter: str | None = None,
) -> list[Journal]:
    query = select(Journal).options(joinedload(Journal.lines)).where(
        Journal.company_id == company_id
    )
    if start_date:
        query = query.where(Journal.entry_date >= start_date)
    if end_date:
        query = query.where(Journal.entry_date <= end_date)
    if status_filter:
        if status_filter not in {"draft", "pending", "posted", "reversed"}:
            raise HTTPException(status_code=422, detail="Estado inválido")
        query = query.where(Journal.status == status_filter)
    return list(db.scalars(query.order_by(Journal.entry_date, Journal.id)).unique().all())


@app.post("/companies/{company_id}/journals", response_model=JournalRead, status_code=201)
def create_journal(
    company_id: str,
    payload: JournalCreate,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant"}))],
    db: DbSession,
) -> Journal:
    ensure_period_open(db, company_id, payload.entry_date)
    account_ids = {line.account_id for line in payload.lines}
    accounts = list(
        db.scalars(
            select(Account).where(
                Account.company_id == company_id,
                Account.id.in_(account_ids),
                Account.active.is_(True),
                Account.status == "approved",
            )
        ).all()
    )
    if len(accounts) != len(account_ids):
        raise HTTPException(status_code=422, detail="Una cuenta no existe, está inactiva o no pertenece a la empresa")
    for line in payload.lines:
        if (line.debit > 0) == (line.credit > 0):
            raise HTTPException(status_code=422, detail="Cada línea debe tener solo debe o haber")
    journal = Journal(
        company_id=company_id,
        entry_date=payload.entry_date,
        description=payload.description,
        reference=payload.reference,
        created_by=membership.user_id,
        lines=[JournalLine(**line.model_dump()) for line in payload.lines],
    )
    db.add(journal)
    db.flush()
    add_audit(db, membership.user_id, company_id, "journal.create", "journal", journal.id, request)
    db.commit()
    db.refresh(journal)
    return journal


@app.post("/companies/{company_id}/journals/{journal_id}/submit")
def submit_journal(
    company_id: str,
    journal_id: str,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant"}))],
    db: DbSession,
) -> dict[str, str]:
    journal = db.scalar(
        select(Journal)
        .where(Journal.id == journal_id, Journal.company_id == company_id)
        .with_for_update()
    )
    if not journal:
        raise HTTPException(status_code=404, detail="Asiento no encontrado")
    if journal.status != "draft" or journal.created_by != membership.user_id:
        raise HTTPException(status_code=409, detail="Solo el autor puede enviar un asiento en borrador a revisión")
    ensure_period_open(db, company_id, journal.entry_date)
    journal.status = "pending"
    add_audit(db, membership.user_id, company_id, "journal.submit", "journal", journal.id, request)
    db.commit()
    return {"status": "pending"}


@app.get("/companies/{company_id}/review-queue")
def review_queue(
    company_id: str,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))],
    db: DbSession,
) -> dict[str, list[dict[str, str]]]:
    journals = db.scalars(
        select(Journal)
        .where(
            Journal.company_id == company_id,
            Journal.status == "pending",
            Journal.source_type.in_(["manual", "opening"]),
        )
        .order_by(Journal.entry_date, Journal.id)
    ).all()
    documents = db.scalars(
        select(TaxDocument)
        .where(TaxDocument.company_id == company_id, TaxDocument.status == "pending")
        .order_by(TaxDocument.document_date, TaxDocument.id)
    ).all()
    payments = db.scalars(
        select(Payment)
        .where(Payment.company_id == company_id, Payment.status == "pending")
        .order_by(Payment.payment_date, Payment.id)
    ).all()
    accounts = db.scalars(
        select(Account)
        .where(Account.company_id == company_id, Account.status == "pending")
        .order_by(Account.code)
    ).all()
    return {
        "journals": [
            {
                "id": item.id,
                "date": item.entry_date.isoformat(),
                "description": item.description,
                "created_by": item.created_by,
            }
            for item in journals
        ],
        "documents": [
            {
                "id": item.id,
                "date": item.document_date.isoformat(),
                "invoice_number": item.invoice_number,
                "direction": item.direction,
                "counterparty": item.counterparty_name,
                "total": str(item.total_amount),
                "created_by": db.scalar(
                    select(Journal.created_by).where(Journal.id == item.journal_id)
                ) or "",
            }
            for item in documents
        ],
        "payments": [
            {
                "id": item.id,
                "date": item.payment_date.isoformat(),
                "reference": item.reference or item.id[:8],
                "direction": item.direction,
                "amount": str(item.amount_ves),
                "created_by": item.created_by,
            }
            for item in payments
        ],
        "accounts": [
            {
                "id": item.id,
                "date": item.created_at.date().isoformat(),
                "code": item.code,
                "name": item.name,
                "created_by": item.created_by or "",
            }
            for item in accounts
        ],
    }


@app.post("/companies/{company_id}/journals/{journal_id}/post", response_model=JournalRead)
def post_journal(
    company_id: str,
    journal_id: str,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant"}))],
    db: DbSession,
) -> Journal:
    journal = db.scalar(
        select(Journal)
        .where(Journal.id == journal_id, Journal.company_id == company_id)
        .with_for_update()
    )
    if not journal:
        raise HTTPException(status_code=404, detail="Asiento no encontrado")
    if journal.status != "pending":
        raise HTTPException(status_code=409, detail="El asiento debe estar enviado y pendiente de aprobación")
    if journal.source_type not in {"manual", "opening"}:
        raise HTTPException(
            status_code=409,
            detail="Aprueba el documento origen desde su módulo para mantener saldos y libros sincronizados",
        )
    if journal.created_by == membership.user_id:
        raise HTTPException(status_code=409, detail="El autor no puede aprobar su propio asiento")
    ensure_period_open(db, company_id, journal.entry_date)
    debit_total = sum((line.debit for line in journal.lines), Decimal("0.00"))
    credit_total = sum((line.credit for line in journal.lines), Decimal("0.00"))
    if debit_total <= 0 or debit_total != credit_total:
        raise HTTPException(status_code=422, detail="El asiento debe cuadrar y tener un monto mayor a cero")
    journal.status = "posted"
    journal.posted_at = datetime.now(timezone.utc)
    add_audit(db, membership.user_id, company_id, "journal.post", "journal", journal.id, request)
    db.commit()
    db.refresh(journal)
    return journal


@app.post("/companies/{company_id}/journals/{journal_id}/reverse", response_model=JournalRead)
def reverse_journal(
    company_id: str,
    journal_id: str,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"admin"}))],
    db: DbSession,
) -> Journal:
    original = db.scalar(
        select(Journal)
        .where(Journal.id == journal_id, Journal.company_id == company_id)
        .with_for_update()
    )
    if not original:
        raise HTTPException(status_code=404, detail="Asiento no encontrado")
    if original.status != "posted":
        raise HTTPException(status_code=409, detail="Solo se pueden reversar asientos contabilizados")
    if original.source_type in {"tax_document", "payment", "opening"}:
        raise HTTPException(
            status_code=409,
            detail="Este asiento requiere reversar su documento origen para mantener saldos y libros sincronizados",
        )
    ensure_period_open(db, company_id, date.today())
    reversal = create_reversal_entry(db, company_id, original, membership.user_id)
    db.flush()
    add_audit(db, membership.user_id, company_id, "journal.reverse", "journal", original.id, request)
    db.commit()
    db.refresh(reversal)
    return reversal


def create_reversal_entry(
    db: Session, company_id: str, original: Journal, user_id: str
) -> Journal:
    reversal = Journal(
        company_id=company_id,
        entry_date=date.today(),
        description=f"Reverso de: {original.description}",
        reference=f"REV-{original.reference or original.id[:8]}",
        status="posted",
        source_type="reversal",
        reversal_of_id=original.id,
        created_by=user_id,
        posted_at=datetime.now(timezone.utc),
        lines=[
            JournalLine(
                account_id=line.account_id,
                description=f"Reverso: {line.description}",
                debit=line.credit,
                credit=line.debit,
            )
            for line in original.lines
        ],
    )
    db.add(reversal)
    original.status = "reversed"
    return reversal


@app.post("/companies/{company_id}/tax-documents", response_model=TaxDocumentRead, status_code=201)
def create_tax_document(
    company_id: str,
    payload: TaxDocumentCreate,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant"}))],
    db: DbSession,
) -> TaxDocument:
    ensure_period_open(db, company_id, payload.document_date)
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    expected_kind = "supplier" if payload.direction == "purchase" else "customer"
    contact = None
    if payload.contact_id:
        contact = db.scalar(
            select(Contact).where(
                Contact.id == payload.contact_id,
                Contact.company_id == company_id,
                Contact.active.is_(True),
            )
        )
        if (
            not contact
            or contact.rif != payload.counterparty_rif
            or contact.kind not in {expected_kind, "both"}
        ):
            raise HTTPException(status_code=422, detail="El cliente/proveedor no coincide con el documento")
    else:
        contact = db.scalar(
            select(Contact).where(
                Contact.company_id == company_id,
                Contact.rif == payload.counterparty_rif,
            )
        )
        if not contact:
            contact = Contact(
                company_id=company_id,
                rif=payload.counterparty_rif,
                name=payload.counterparty_name,
                kind=expected_kind,
            )
            db.add(contact)
            db.flush()
        elif contact.kind not in {expected_kind, "both"}:
            raise HTTPException(status_code=422, detail="El contacto está registrado con otro tipo")
    vat = calculate_vat(payload.base_amount, payload.vat_rate_percent)
    withheld = money(vat * payload.vat_withholding_percent / Decimal("100"))
    total = money(payload.base_amount + payload.exempt_amount + vat)
    if payload.igtf_applicable:
        raise HTTPException(
            status_code=422,
            detail="El IGTF se registra en el módulo de pagos, no en la factura",
        )
    igtf = Decimal("0.00")
    account_ids = {
        payload.base_account_id,
        payload.vat_account_id,
        payload.counterparty_account_id,
    }
    if payload.withholding_account_id:
        account_ids.add(payload.withholding_account_id)
    accounts = {
        account.id: account
        for account in db.scalars(
            select(Account).where(
                Account.company_id == company_id,
                Account.id.in_(account_ids),
                Account.active.is_(True),
                Account.status == "approved",
            )
        ).all()
    }
    if len(accounts) != len(account_ids):
        raise HTTPException(status_code=422, detail="Las cuentas contables deben estar activas y pertenecer a la empresa")
    if (
        payload.vat_withholding_percent > 0
        and not payload.withholding_account_id
    ):
        raise HTTPException(status_code=422, detail="Indique la cuenta contable de retenciones de IVA")
    expected_types = {
        "base_account_id": {"asset", "expense"} if payload.direction == "purchase" else {"revenue"},
        "vat_account_id": {"asset"} if payload.direction == "purchase" else {"liability"},
        "counterparty_account_id": {"liability"} if payload.direction == "purchase" else {"asset"},
    }
    for field, valid_types in expected_types.items():
        account = accounts[getattr(payload, field)]
        if account.account_type not in valid_types:
            raise HTTPException(
                status_code=422,
                detail=f"La cuenta {account.code} no es válida para {field}",
            )
    if payload.withholding_account_id:
        withholding_type = "liability" if payload.direction == "purchase" else "asset"
        if accounts[payload.withholding_account_id].account_type != withholding_type:
            raise HTTPException(
                status_code=422,
                detail="La cuenta de retención debe ser pasivo para compras o activo para ventas",
            )

    gross = money(payload.base_amount + payload.exempt_amount + vat)
    retained = withheld
    journal_lines: list[JournalLine] = []
    base_amount = money(payload.base_amount + payload.exempt_amount)
    if payload.direction == "purchase":
        if base_amount:
            journal_lines.append(
                JournalLine(account_id=payload.base_account_id, description="Base de compra", debit=base_amount)
            )
        if vat:
            journal_lines.append(
                JournalLine(account_id=payload.vat_account_id, description="IVA crédito fiscal", debit=vat)
            )
        payable = money(gross - retained)
        if payable:
            journal_lines.append(
                JournalLine(
                    account_id=payload.counterparty_account_id,
                    description="Cuenta por pagar al proveedor",
                    credit=payable,
                )
            )
        if retained:
            journal_lines.append(
                JournalLine(
                    account_id=payload.withholding_account_id,
                    description="IVA retenido por pagar",
                    credit=retained,
                )
            )
    else:
        receivable = money(gross - retained)
        if receivable:
            journal_lines.append(
                JournalLine(
                    account_id=payload.counterparty_account_id,
                    description="Cuenta por cobrar al cliente",
                    debit=receivable,
                )
            )
        if retained:
            journal_lines.append(
                JournalLine(
                    account_id=payload.withholding_account_id,
                    description="IVA retenido por el cliente",
                    debit=retained,
                )
            )
        if base_amount:
            journal_lines.append(
                JournalLine(account_id=payload.base_account_id, description="Base de venta", credit=base_amount)
            )
        if vat:
            journal_lines.append(
                JournalLine(account_id=payload.vat_account_id, description="IVA débito fiscal", credit=vat)
            )
    if len(journal_lines) < 2:
        raise HTTPException(status_code=422, detail="La factura debe producir un asiento con al menos dos líneas")

    journal = Journal(
        company_id=company_id,
        entry_date=payload.document_date,
        description=f"Factura de {'compra' if payload.direction == 'purchase' else 'venta'} {payload.invoice_number}",
        reference=payload.invoice_number,
        status="pending",
        source_type="tax_document",
        created_by=membership.user_id,
        lines=journal_lines,
    )
    db.add(journal)
    db.flush()
    document_values = payload.model_dump(
        exclude={
            "base_account_id",
            "vat_account_id",
            "counterparty_account_id",
            "withholding_account_id",
            "contact_id",
        }
    )
    document = TaxDocument(
        company_id=company_id,
        period=payload.document_date.strftime("%Y%m"),
        vat_amount=vat,
        vat_withheld=withheld,
        total_amount=total,
        igtf_amount=igtf,
        journal_id=journal.id,
        contact_id=contact.id,
        status="pending",
        **document_values,
    )
    db.add(document)
    try:
        db.flush()
        journal.source_id = document.id
        add_audit(db, membership.user_id, company_id, "journal.create_pending", "journal", journal.id, request)
        add_audit(
            db,
            membership.user_id,
            company_id,
            "tax_document.create",
            "tax_document",
            document.id,
            request,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="La factura ya está registrada para esta contraparte"
        ) from exc
    db.refresh(document)
    return document


@app.post("/companies/{company_id}/tax-documents/{document_id}/approve")
def approve_tax_document(
    company_id: str,
    document_id: str,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant"}))],
    db: DbSession,
) -> dict[str, str]:
    document = db.scalar(
        select(TaxDocument)
        .where(TaxDocument.id == document_id, TaxDocument.company_id == company_id)
        .with_for_update()
    )
    if not document:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    journal = db.scalar(
        select(Journal)
        .where(Journal.id == document.journal_id, Journal.company_id == company_id)
        .with_for_update()
    )
    if not journal or document.status != "pending" or journal.status != "pending":
        raise HTTPException(status_code=409, detail="El documento no está pendiente de aprobación")
    if journal.created_by == membership.user_id:
        raise HTTPException(status_code=409, detail="El autor no puede aprobar su propio documento")
    ensure_period_open(db, company_id, document.document_date)
    document.status = "posted"
    journal.status = "posted"
    journal.posted_at = datetime.now(timezone.utc)
    add_audit(db, membership.user_id, company_id, "tax_document.approve", "tax_document", document.id, request)
    add_audit(db, membership.user_id, company_id, "journal.post", "journal", journal.id, request)
    db.commit()
    return {"status": "posted", "journal_id": journal.id}


@app.get(
    "/companies/{company_id}/tax-documents",
    response_model=list[TaxDocumentRead],
)
def list_tax_documents(
    company_id: str,
    membership: Annotated[
        Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))
    ],
    db: DbSession,
    direction: str = "sale",
) -> list[TaxDocument]:
    if direction not in {"purchase", "sale"}:
        raise HTTPException(status_code=422, detail="Tipo de documento inválido")
    return db.scalars(
        select(TaxDocument)
        .where(TaxDocument.company_id == company_id, TaxDocument.direction == direction)
        .order_by(TaxDocument.document_date.desc(), TaxDocument.invoice_number.desc())
        .limit(500)
    ).all()


@app.get("/companies/{company_id}/tax-documents/{document_id}/commercial-invoice.html")
def preview_commercial_invoice(
    company_id: str,
    document_id: str,
    membership: Annotated[
        Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))
    ],
    db: DbSession,
) -> Response:
    document = db.scalar(
        select(TaxDocument).where(
            TaxDocument.id == document_id,
            TaxDocument.company_id == company_id,
            TaxDocument.direction == "sale",
        )
    )
    company = db.get(Company, company_id)
    if not document or not company:
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    return Response(
        content=render_commercial_invoice(company, document),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@app.post("/companies/{company_id}/tax-documents/{document_id}/send-invoice")
def email_commercial_invoice(
    company_id: str,
    document_id: str,
    request: Request,
    membership: Annotated[
        Membership, Depends(require_company_role({"admin", "accountant"}))
    ],
    db: DbSession,
) -> dict[str, str]:
    document = db.scalar(
        select(TaxDocument).where(
            TaxDocument.id == document_id,
            TaxDocument.company_id == company_id,
            TaxDocument.direction == "sale",
            TaxDocument.status == "posted",
        )
    )
    if not document:
        raise HTTPException(status_code=404, detail="Factura de venta aprobada no encontrada")
    contact = db.get(Contact, document.contact_id) if document.contact_id else None
    if not contact or not contact.email:
        raise HTTPException(status_code=409, detail="Registre un correo en la ficha del cliente")
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    try:
        send_configured_email(
            contact.email,
            f"Factura comercial {document.invoice_number} · {company.legal_name}",
            render_commercial_invoice(company, document),
        )
    except (OSError, RuntimeError, smtplib.SMTPException, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"No se pudo enviar el correo: {exc}") from exc
    add_audit(
        db,
        membership.user_id,
        company_id,
        "invoice.email",
        "tax_document",
        document.id,
        request,
    )
    db.commit()
    return {"status": "sent", "recipient": contact.email}


@app.post("/companies/{company_id}/tax-documents/{document_id}/reverse")
def reverse_tax_document(
    company_id: str,
    document_id: str,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"admin"}))],
    db: DbSession,
) -> dict[str, str]:
    document = db.scalar(
        select(TaxDocument)
        .where(TaxDocument.id == document_id, TaxDocument.company_id == company_id)
        .with_for_update()
    )
    if not document or document.status != "posted":
        raise HTTPException(status_code=404, detail="Factura contabilizada no encontrada")
    payments = db.scalar(
        select(func.count()).select_from(Payment).where(
            Payment.document_id == document.id,
            Payment.status.in_(["pending", "posted"]),
        )
    )
    if payments:
        raise HTTPException(status_code=409, detail="Primero reversa o cancela los pagos asociados")
    original = db.scalar(
        select(Journal)
        .where(Journal.id == document.journal_id, Journal.company_id == company_id)
        .with_for_update()
    )
    if not original or original.status != "posted":
        raise HTTPException(status_code=409, detail="El asiento de la factura no está contabilizado")
    ensure_period_open(db, company_id, date.today())
    reversal = create_reversal_entry(db, company_id, original, membership.user_id)
    document.status = "reversed"
    db.flush()
    add_audit(db, membership.user_id, company_id, "tax_document.reverse", "tax_document", document.id, request)
    add_audit(db, membership.user_id, company_id, "journal.reverse", "journal", original.id, request)
    db.commit()
    return {"status": "reversed", "reversal_journal_id": reversal.id}


def get_document_open_balance(db: Session, document: TaxDocument) -> Decimal:
    settled = db.scalar(
        select(func.coalesce(func.sum(Payment.amount_ves), 0)).where(
            Payment.document_id == document.id,
            Payment.status.in_(["pending", "posted"]),
        )
    )
    return max(Decimal("0"), document.total_amount - document.vat_withheld - Decimal(settled))


@app.get("/companies/{company_id}/open-documents")
def list_open_documents(
    company_id: str,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))],
    db: DbSession,
    direction: str | None = None,
) -> list[dict[str, str]]:
    query = select(TaxDocument).where(
        TaxDocument.company_id == company_id,
        TaxDocument.status == "posted",
    )
    if direction:
        if direction not in {"purchase", "sale"}:
            raise HTTPException(status_code=422, detail="Tipo de factura inválido")
        query = query.where(TaxDocument.direction == direction)
    result = []
    for document in db.scalars(query.order_by(TaxDocument.document_date)).all():
        outstanding = get_document_open_balance(db, document)
        if outstanding > 0:
            result.append({
                "id": document.id,
                "direction": document.direction,
                "date": document.document_date.isoformat(),
                "invoice_number": document.invoice_number,
                "contact_id": document.contact_id or "",
                "counterparty_name": document.counterparty_name,
                "total_amount": str(document.total_amount),
                "vat_withheld": str(document.vat_withheld),
                "outstanding_ves": str(money(outstanding)),
            })
    return result


@app.get("/companies/{company_id}/payments", response_model=list[PaymentRead])
def list_payments(
    company_id: str,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))],
    db: DbSession,
    status_filter: str | None = None,
) -> list[Payment]:
    query = select(Payment).where(Payment.company_id == company_id)
    if status_filter:
        if status_filter not in {"pending", "posted", "reversed"}:
            raise HTTPException(status_code=422, detail="Estado de pago inválido")
        query = query.where(Payment.status == status_filter)
    return list(db.scalars(query.order_by(Payment.payment_date.desc())).all())


@app.post("/companies/{company_id}/payments", response_model=PaymentRead, status_code=201)
def create_payment(
    company_id: str,
    payload: PaymentCreate,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant"}))],
    db: DbSession,
) -> Payment:
    document = db.scalar(
        select(TaxDocument)
        .where(
            TaxDocument.id == payload.document_id,
            TaxDocument.company_id == company_id,
            TaxDocument.status == "posted",
        )
        .with_for_update()
    )
    if not document:
        raise HTTPException(status_code=404, detail="Factura contabilizada no encontrada")
    ensure_period_open(db, company_id, payload.payment_date)
    direction = "payable" if document.direction == "purchase" else "receivable"
    outstanding = get_document_open_balance(db, document)
    if payload.amount_ves > outstanding:
        raise HTTPException(
            status_code=422,
            detail=f"El saldo disponible es Bs. {money(outstanding)}",
        )
    if payload.currency == "VES":
        if payload.foreign_amount or payload.exchange_rate:
            raise HTTPException(status_code=422, detail="Para VES no debe indicar monto en divisa ni tasa")
        if payload.igtf_applicable:
            raise HTTPException(status_code=422, detail="El IGTF de este módulo se calcula solo sobre pagos en divisa")
        foreign_amount = Decimal("0")
        igtf = Decimal("0")
    else:
        if not payload.foreign_amount or not payload.exchange_rate:
            raise HTTPException(status_code=422, detail="Indique monto en divisa y tasa de cambio")
        if money(payload.foreign_amount * payload.exchange_rate) != money(payload.amount_ves):
            raise HTTPException(status_code=422, detail="El monto en VES no coincide con divisa por tasa de cambio")
        foreign_amount = payload.foreign_amount
        igtf = calculate_igtf(
            foreign_amount,
            payload.igtf_rate_percent,
            payload.igtf_applicable,
            payload.exchange_rate,
        )
    company = db.get(Company, company_id)
    if payload.igtf_applicable and not company.special_taxpayer:
        raise HTTPException(status_code=422, detail="La empresa no está marcada como Sujeto Pasivo Especial")
    if igtf and not payload.igtf_account_id:
        raise HTTPException(status_code=422, detail="Indique la cuenta contable del IGTF")
    counterpart_description = (
        "Cuenta por pagar al proveedor"
        if document.direction == "purchase"
        else "Cuenta por cobrar al cliente"
    )
    counterpart_line = db.scalar(
        select(JournalLine)
        .join(Journal, Journal.id == JournalLine.journal_id)
        .where(
            Journal.id == document.journal_id,
            Journal.company_id == company_id,
            JournalLine.description == counterpart_description,
        )
    )
    if not counterpart_line:
        raise HTTPException(status_code=409, detail="La factura no tiene una cuenta de contraparte disponible")
    accounts_needed = {payload.cash_account_id, counterpart_line.account_id}
    if payload.igtf_account_id:
        accounts_needed.add(payload.igtf_account_id)
    accounts = {
        account.id: account
        for account in db.scalars(
            select(Account).where(
                Account.company_id == company_id,
                Account.id.in_(accounts_needed),
                Account.active.is_(True),
                Account.status == "approved",
            )
        ).all()
    }
    if len(accounts) != len(accounts_needed) or accounts[payload.cash_account_id].account_type != "asset":
        raise HTTPException(status_code=422, detail="Seleccione cuentas activas de esta empresa y una cuenta de activo")
    if igtf:
        expected_igtf_type = "expense" if direction == "payable" else "liability"
        if accounts[payload.igtf_account_id].account_type != expected_igtf_type:
            raise HTTPException(status_code=422, detail="Cuenta IGTF debe ser gasto al pagar o pasivo al cobrar")

    cash_total = money(payload.amount_ves + igtf)
    lines: list[JournalLine] = []
    if direction == "payable":
        lines.append(
            JournalLine(
                account_id=counterpart_line.account_id,
                description="Pago a proveedor",
                debit=payload.amount_ves,
            )
        )
        if igtf:
            lines.append(
                JournalLine(account_id=payload.igtf_account_id, description="IGTF pagado", debit=igtf)
            )
        lines.append(
            JournalLine(account_id=payload.cash_account_id, description="Salida de fondos", credit=cash_total)
        )
    else:
        lines.append(
            JournalLine(account_id=payload.cash_account_id, description="Entrada de fondos", debit=cash_total)
        )
        lines.append(
            JournalLine(
                account_id=counterpart_line.account_id,
                description="Cobro a cliente",
                credit=payload.amount_ves,
            )
        )
        if igtf:
            lines.append(
                JournalLine(
                    account_id=payload.igtf_account_id,
                    description="IGTF recaudado por enterar",
                    credit=igtf,
                )
            )
    contact_id = document.contact_id
    if not contact_id:
        raise HTTPException(status_code=409, detail="La factura no está asociada a un cliente/proveedor")
    journal = Journal(
        company_id=company_id,
        entry_date=payload.payment_date,
        description=("Pago" if direction == "payable" else "Cobro") + f" factura {document.invoice_number}",
        reference=payload.reference,
        status="pending",
        source_type="payment",
        created_by=membership.user_id,
        lines=lines,
    )
    db.add(journal)
    db.flush()
    payment = Payment(
        company_id=company_id,
        contact_id=contact_id,
        document_id=document.id,
        direction=direction,
        payment_date=payload.payment_date,
        reference=payload.reference,
        method=payload.method,
        currency=payload.currency,
        amount_ves=payload.amount_ves,
        foreign_amount=foreign_amount,
        exchange_rate=payload.exchange_rate,
        igtf_rate_percent=payload.igtf_rate_percent,
        igtf_amount=igtf,
        cash_account_id=payload.cash_account_id,
        igtf_account_id=payload.igtf_account_id,
        journal_id=journal.id,
        status="pending",
        created_by=membership.user_id,
    )
    db.add(payment)
    db.flush()
    journal.source_id = payment.id
    add_audit(db, membership.user_id, company_id, "payment.create_pending", "payment", payment.id, request)
    db.commit()
    db.refresh(payment)
    return payment


@app.post("/companies/{company_id}/payments/{payment_id}/approve")
def approve_payment(
    company_id: str,
    payment_id: str,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant"}))],
    db: DbSession,
) -> dict[str, str]:
    payment = db.scalar(
        select(Payment)
        .where(Payment.id == payment_id, Payment.company_id == company_id)
        .with_for_update()
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Pago/cobro no encontrado")
    if payment.status != "pending":
        raise HTTPException(status_code=409, detail="El pago/cobro no está pendiente de aprobación")
    if payment.created_by == membership.user_id:
        raise HTTPException(status_code=409, detail="El autor no puede aprobar su propio pago/cobro")
    ensure_period_open(db, company_id, payment.payment_date)
    journal = db.scalar(
        select(Journal)
        .where(Journal.id == payment.journal_id, Journal.company_id == company_id)
        .with_for_update()
    )
    if not journal or journal.status != "pending":
        raise HTTPException(status_code=409, detail="El asiento del pago no está pendiente")
    payment.status = "posted"
    journal.status = "posted"
    journal.posted_at = datetime.now(timezone.utc)
    payment.posted_at = journal.posted_at
    add_audit(db, membership.user_id, company_id, "payment.approve", "payment", payment.id, request)
    add_audit(db, membership.user_id, company_id, "journal.post", "journal", journal.id, request)
    db.commit()
    return {"status": "posted", "journal_id": journal.id}


@app.post("/companies/{company_id}/payments/{payment_id}/reverse")
def reverse_payment(
    company_id: str,
    payment_id: str,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"admin"}))],
    db: DbSession,
) -> dict[str, str]:
    payment = db.scalar(
        select(Payment)
        .where(Payment.id == payment_id, Payment.company_id == company_id)
        .with_for_update()
    )
    if not payment or payment.status != "posted":
        raise HTTPException(status_code=404, detail="Pago contabilizado no encontrado")
    reconciled = db.scalar(
        select(BankStatementLine.id).where(
            BankStatementLine.matched_payment_id == payment.id
        )
    )
    if reconciled:
        raise HTTPException(
            status_code=409,
            detail="Desconcilia el movimiento bancario antes de reversar el pago",
        )
    original = db.scalar(
        select(Journal)
        .where(Journal.id == payment.journal_id, Journal.company_id == company_id)
        .with_for_update()
    )
    if not original or original.status != "posted":
        raise HTTPException(status_code=409, detail="El asiento del pago no está contabilizado")
    ensure_period_open(db, company_id, date.today())
    reversal = create_reversal_entry(db, company_id, original, membership.user_id)
    payment.status = "reversed"
    db.flush()
    add_audit(db, membership.user_id, company_id, "payment.reverse", "payment", payment.id, request)
    add_audit(db, membership.user_id, company_id, "journal.reverse", "journal", original.id, request)
    db.commit()
    return {"status": "reversed", "reversal_journal_id": reversal.id}


@app.post("/companies/{company_id}/bank-statement-lines", status_code=201)
def add_bank_statement_line(
    company_id: str,
    payload: BankStatementLineCreate,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant"}))],
    db: DbSession,
) -> dict[str, str]:
    account = db.scalar(
        select(Account).where(
            Account.id == payload.bank_account_id,
            Account.company_id == company_id,
            Account.active.is_(True),
            Account.status == "approved",
            Account.account_type == "asset",
        )
    )
    if not account:
        raise HTTPException(status_code=422, detail="Seleccione una cuenta bancaria activa de tipo activo")
    line = BankStatementLine(company_id=company_id, **payload.model_dump())
    db.add(line)
    try:
        db.flush()
        add_audit(db, membership.user_id, company_id, "bank_statement_line.create", "bank_line", line.id, request)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Referencia bancaria duplicada") from exc
    return {"id": line.id, "status": "unreconciled"}


@app.get("/companies/{company_id}/bank-statement-lines")
def list_bank_statement_lines(
    company_id: str,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))],
    db: DbSession,
    unreconciled_only: bool = True,
) -> list[dict[str, str | None]]:
    query = select(BankStatementLine).where(BankStatementLine.company_id == company_id)
    if unreconciled_only:
        query = query.where(BankStatementLine.matched_payment_id.is_(None))
    rows = db.scalars(query.order_by(BankStatementLine.statement_date.desc())).all()
    return [
        {
            "id": line.id,
            "date": line.statement_date.isoformat(),
            "reference": line.external_reference,
            "description": line.description,
            "amount": str(line.amount),
            "bank_account_id": line.bank_account_id,
            "matched_payment_id": line.matched_payment_id,
        }
        for line in rows
    ]


def parse_bank_statement_date(raw: str) -> date:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError("fecha no válida; use AAAA-MM-DD o DD/MM/AAAA")


def parse_bank_statement_amount(raw: str) -> Decimal:
    value = raw.strip().replace(" ", "")
    if not value:
        raise ValueError("monto vacío")
    if "," in value and "." in value:
        decimal_mark = "," if value.rfind(",") > value.rfind(".") else "."
        thousands_mark = "." if decimal_mark == "," else ","
        value = value.replace(thousands_mark, "").replace(decimal_mark, ".")
    elif "," in value:
        value = value.replace(".", "").replace(",", ".")
    amount = Decimal(value)
    if not amount.is_finite() or amount == 0:
        raise ValueError("monto debe ser distinto de cero")
    return money(amount)


def normalize_azure_invoice_result(result: dict[str, object]) -> dict[str, object]:
    analyze = result.get("analyzeResult")
    if not isinstance(analyze, dict):
        raise ValueError("El proveedor OCR devolvió una respuesta sin analyzeResult")
    documents = analyze.get("documents")
    if not isinstance(documents, list) or not documents or not isinstance(documents[0], dict):
        raise ValueError("No se identificó una factura en el archivo")
    fields = documents[0].get("fields")
    if not isinstance(fields, dict):
        raise ValueError("El proveedor OCR no devolvió campos estructurados")
    names = {
        "invoice_number": ("InvoiceId",),
        "invoice_date": ("InvoiceDate",),
        "vendor_name": ("VendorName",),
        "vendor_rif": ("VendorTaxId",),
        "customer_name": ("CustomerName",),
        "subtotal": ("SubTotal", "AmountDue"),
        "tax_total": ("TotalTax",),
        "total": ("InvoiceTotal",),
        "currency": ("InvoiceTotal",),
    }
    extracted: dict[str, object] = {}
    confidence: dict[str, float] = {}
    for key, candidates in names.items():
        field_value = next(
            (fields[name] for name in candidates if isinstance(fields.get(name), dict)),
            None,
        )
        if not isinstance(field_value, dict):
            continue
        if key in {"subtotal", "tax_total", "total"}:
            currency = field_value.get("valueCurrency")
            if isinstance(currency, dict) and currency.get("amount") is not None:
                extracted[key] = str(currency["amount"])
                if currency.get("currencyCode"):
                    extracted["currency"] = str(currency["currencyCode"])
        elif key == "currency":
            continue
        else:
            value = field_value.get("valueString")
            if value is None:
                value = field_value.get("valueDate")
            if value is None:
                value = field_value.get("content")
            if value is not None:
                extracted[key] = str(value)
        if isinstance(field_value.get("confidence"), (int, float)):
            confidence[key] = float(field_value["confidence"])
    extracted["confidence"] = confidence
    return extracted


@app.post("/companies/{company_id}/bank-statement-lines/import-csv")
async def import_bank_statement_csv(
    company_id: str,
    request: Request,
    file: Annotated[UploadFile, File()],
    bank_account_id: Annotated[str, Form()],
    membership: Annotated[
        Membership, Depends(require_company_role({"admin", "accountant"}))
    ],
    db: DbSession,
) -> dict[str, object]:
    if (file.content_type or "").split(";", 1)[0].lower() not in {
        "text/csv",
        "application/vnd.ms-excel",
        "application/octet-stream",
    }:
        raise HTTPException(status_code=415, detail="Suba un archivo CSV")
    content = await file.read(5 * 1024 * 1024 + 1)
    if not content or len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="El CSV debe ocupar entre 1 byte y 5 MB")
    account = db.scalar(
        select(Account).where(
            Account.id == bank_account_id,
            Account.company_id == company_id,
            Account.active.is_(True),
            Account.status == "approved",
            Account.account_type == "asset",
        )
    )
    if not account:
        raise HTTPException(status_code=422, detail="Seleccione una cuenta bancaria de activo aprobada")
    try:
        text_content = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text_content = content.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=422, detail="El archivo no tiene una codificación reconocida") from exc
    try:
        dialect = csv.Sniffer().sniff(text_content[:4096], delimiters=",;|\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(StringIO(text_content), dialect=dialect)
    if not reader.fieldnames:
        raise HTTPException(status_code=422, detail="El CSV debe incluir una fila de encabezados")
    aliases = {
        "date": {"date", "fecha", "fecha valor"},
        "reference": {"reference", "referencia", "nro referencia", "numero referencia"},
        "description": {"description", "descripcion", "concepto", "detalle"},
        "amount": {"amount", "monto", "importe"},
        "debit": {"debit", "debito", "cargo"},
        "credit": {"credit", "credito", "abono"},
    }
    normalized_headers = {
        unicodedata.normalize("NFKD", name or "")
        .encode("ascii", "ignore")
        .decode("ascii")
        .strip()
        .lower(): name
        for name in reader.fieldnames
    }
    columns: dict[str, str] = {}
    for key, choices in aliases.items():
        for normalized, original in normalized_headers.items():
            if normalized in choices:
                columns[key] = original
                break
    if not all(key in columns for key in ("date", "reference")) or not (
        "amount" in columns or ("debit" in columns and "credit" in columns)
    ):
        raise HTTPException(
            status_code=422,
            detail="Se requieren columnas fecha, referencia y monto; o débito y crédito. Descripción es opcional.",
        )
    created = 0
    duplicates = 0
    rejected: list[dict[str, str | int]] = []
    seen_references: set[str] = set()
    for row_number, row in enumerate(reader, start=2):
        if row_number > 2001:
            rejected.append({"row": row_number, "error": "Límite de 2000 movimientos por archivo"})
            break
        try:
            reference = (row.get(columns["reference"]) or "").strip()
            if not reference:
                raise ValueError("referencia vacía")
            if reference in seen_references:
                duplicates += 1
                continue
            seen_references.add(reference)
            statement_date = parse_bank_statement_date(row.get(columns["date"]) or "")
            if "amount" in columns:
                amount = parse_bank_statement_amount(row.get(columns["amount"]) or "")
            else:
                debit_text = (row.get(columns["debit"]) or "").strip()
                credit_text = (row.get(columns["credit"]) or "").strip()
                debit = parse_bank_statement_amount(debit_text) if debit_text else Decimal("0")
                credit = parse_bank_statement_amount(credit_text) if credit_text else Decimal("0")
                if debit < 0 or credit < 0 or (debit > 0 and credit > 0):
                    raise ValueError("indique solo débito o crédito, sin importes negativos")
                amount = money(credit - debit)
                if amount == 0:
                    raise ValueError("monto debe ser distinto de cero")
            exists = db.scalar(
                select(BankStatementLine.id).where(
                    BankStatementLine.company_id == company_id,
                    BankStatementLine.bank_account_id == bank_account_id,
                    BankStatementLine.external_reference == reference,
                )
            )
            if exists:
                duplicates += 1
                continue
            description = (row.get(columns.get("description", "")) or "").strip()[:300]
            db.add(
                BankStatementLine(
                    company_id=company_id,
                    bank_account_id=bank_account_id,
                    statement_date=statement_date,
                    external_reference=reference[:100],
                    description=description,
                    amount=amount,
                )
            )
            created += 1
        except (ValueError, InvalidOperation) as exc:
            rejected.append({"row": row_number, "error": str(exc)})
    try:
        db.flush()
        add_audit(
            db,
            membership.user_id,
            company_id,
            "bank_statement.import_csv",
            "bank_statement",
            request=request,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="No se importó el archivo por referencias bancarias duplicadas") from exc
    return {"created": created, "duplicates": duplicates, "rejected": rejected}


@app.post("/companies/{company_id}/bank-statement-lines/{line_id}/match/{payment_id}")
def reconcile_bank_line(
    company_id: str,
    line_id: str,
    payment_id: str,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant"}))],
    db: DbSession,
) -> dict[str, str]:
    line = db.scalar(
        select(BankStatementLine)
        .where(BankStatementLine.id == line_id, BankStatementLine.company_id == company_id)
        .with_for_update()
    )
    payment = db.scalar(
        select(Payment)
        .where(Payment.id == payment_id, Payment.company_id == company_id)
        .with_for_update()
    )
    if not line or not payment:
        raise HTTPException(status_code=404, detail="Movimiento bancario o pago no encontrado")
    if payment.status != "posted" or payment.method not in {"bank", "transfer"}:
        raise HTTPException(status_code=409, detail="Solo se concilian pagos bancarios contabilizados")
    if line.matched_payment_id:
        raise HTTPException(status_code=409, detail="El movimiento ya está conciliado")
    expected_sign = Decimal("-1") if payment.direction == "payable" else Decimal("1")
    expected_amount = money((payment.amount_ves + payment.igtf_amount) * expected_sign)
    if (
        line.bank_account_id != payment.cash_account_id
        or money(line.amount) != expected_amount
        or line.statement_date != payment.payment_date
    ):
        raise HTTPException(
            status_code=422,
            detail=f"No coincide banco, fecha o monto. Se esperaba Bs. {expected_amount}.",
        )
    duplicate = db.scalar(
        select(BankStatementLine.id).where(
            BankStatementLine.matched_payment_id == payment.id
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="El pago ya fue conciliado")
    line.matched_payment_id = payment.id
    line.reconciled_at = datetime.now(timezone.utc)
    add_audit(db, membership.user_id, company_id, "bank_line.reconcile", "bank_line", line.id, request)
    db.commit()
    return {"status": "reconciled", "line_id": line.id, "payment_id": payment.id}


@app.post("/companies/{company_id}/bank-statement-lines/{line_id}/unmatch")
def unreconcile_bank_line(
    company_id: str,
    line_id: str,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"admin"}))],
    db: DbSession,
) -> dict[str, str]:
    line = db.scalar(
        select(BankStatementLine)
        .where(BankStatementLine.id == line_id, BankStatementLine.company_id == company_id)
        .with_for_update()
    )
    if not line:
        raise HTTPException(status_code=404, detail="Movimiento bancario no encontrado")
    if not line.matched_payment_id:
        raise HTTPException(status_code=409, detail="El movimiento ya está sin conciliar")
    payment_id = line.matched_payment_id
    line.matched_payment_id = None
    line.reconciled_at = None
    add_audit(db, membership.user_id, company_id, "bank_line.unreconcile", "bank_line", line.id, request)
    db.commit()
    return {"status": "unreconciled", "payment_id": payment_id}


@app.get("/companies/{company_id}/audit-events")
def company_audit_events(
    company_id: str,
    membership: Annotated[Membership, Depends(require_company_role({"admin"}))],
    db: DbSession,
    limit: int = 100,
) -> list[dict[str, str]]:
    rows = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.company_id == company_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(max(1, min(limit, 500)))
    ).all()
    return [
        {
            "timestamp": event.created_at.isoformat(),
            "user_id": event.user_id or "",
            "action": event.action,
            "entity_type": event.entity_type,
            "entity_id": event.entity_id,
            "ip_address": event.ip_address,
        }
        for event in rows
    ]


@app.get(
    "/companies/{company_id}/documents",
    response_model=list[CompanyDocumentRead],
)
def list_company_documents(
    company_id: str,
    membership: Annotated[
        Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))
    ],
    db: DbSession,
) -> list[CompanyDocument]:
    return db.scalars(
        select(CompanyDocument)
        .where(CompanyDocument.company_id == company_id, CompanyDocument.active.is_(True))
        .order_by(CompanyDocument.uploaded_at.desc())
    ).all()


@app.post(
    "/companies/{company_id}/documents",
    response_model=CompanyDocumentRead,
    status_code=201,
)
async def upload_company_document(
    company_id: str,
    request: Request,
    file: Annotated[UploadFile, File()],
    membership: Annotated[
        Membership, Depends(require_company_role({"admin", "accountant"}))
    ],
    db: DbSession,
    category: Annotated[str, Form()] = "other",
    notes: Annotated[str, Form()] = "",
) -> CompanyDocument:
    allowed_categories = {"invoice", "tax", "bank", "contract", "receipt", "other"}
    if category not in allowed_categories:
        raise HTTPException(status_code=422, detail="Categoría de documento no válida")
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    if content_type not in ALLOWED_COMPANY_DOCUMENT_TYPES:
        raise HTTPException(status_code=415, detail="Tipo de archivo no permitido")
    filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not filename or len(filename) > 255:
        raise HTTPException(status_code=422, detail="Nombre de archivo no válido")
    content = await file.read(MAX_COMPANY_DOCUMENT_BYTES + 1)
    if not content:
        raise HTTPException(status_code=422, detail="El archivo está vacío")
    if len(content) > MAX_COMPANY_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="El archivo supera el límite de 10 MB")
    document = CompanyDocument(
        company_id=company_id,
        uploaded_by=membership.user_id,
        filename=filename,
        content_type=content_type,
        category=category,
        notes=notes.strip()[:1000],
        size_bytes=len(content),
        content=content,
    )
    db.add(document)
    db.flush()
    add_audit(
        db, membership.user_id, company_id, "document.upload", "company_document", document.id, request
    )
    db.commit()
    db.refresh(document)
    return document


@app.get("/companies/{company_id}/documents/{document_id}/download")
def download_company_document(
    company_id: str,
    document_id: str,
    membership: Annotated[
        Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))
    ],
    db: DbSession,
) -> Response:
    document = db.scalar(
        select(CompanyDocument).where(
            CompanyDocument.id == document_id,
            CompanyDocument.company_id == company_id,
            CompanyDocument.active.is_(True),
        )
    )
    if not document:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    return Response(
        content=document.content,
        media_type=document.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(document.filename, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@app.delete("/companies/{company_id}/documents/{document_id}", status_code=204)
def archive_company_document(
    company_id: str,
    document_id: str,
    request: Request,
    membership: Annotated[
        Membership, Depends(require_company_role({"admin", "accountant"}))
    ],
    db: DbSession,
) -> Response:
    document = db.scalar(
        select(CompanyDocument).where(
            CompanyDocument.id == document_id,
            CompanyDocument.company_id == company_id,
            CompanyDocument.active.is_(True),
        )
    )
    if not document:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    if membership.role != "admin" and document.uploaded_by != membership.user_id:
        raise HTTPException(status_code=403, detail="Solo puede archivar documentos que usted cargó")
    document.active = False
    document.deleted_at = datetime.now(timezone.utc)
    add_audit(
        db,
        membership.user_id,
        company_id,
        "document.archive",
        "company_document",
        document.id,
        request,
    )
    db.commit()
    return Response(status_code=204)


@app.post(
    "/companies/{company_id}/documents/{document_id}/extract-invoice",
    response_model=DocumentExtractionRead,
    status_code=202,
)
def extract_invoice_fields(
    company_id: str,
    document_id: str,
    request: Request,
    membership: Annotated[
        Membership, Depends(require_company_role({"admin", "accountant"}))
    ],
    db: DbSession,
) -> DocumentExtraction:
    endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "").strip().rstrip("/")
    api_key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "").strip()
    if not endpoint or not api_key:
        raise HTTPException(
            status_code=503,
            detail="OCR no configurado. Configure AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT y AZURE_DOCUMENT_INTELLIGENCE_KEY.",
        )
    parsed_endpoint = urlparse(endpoint)
    if parsed_endpoint.scheme != "https" or not parsed_endpoint.hostname or parsed_endpoint.username:
        raise HTTPException(status_code=503, detail="El endpoint OCR debe ser una URL HTTPS válida")
    document = db.scalar(
        select(CompanyDocument).where(
            CompanyDocument.id == document_id,
            CompanyDocument.company_id == company_id,
            CompanyDocument.active.is_(True),
        )
    )
    if not document:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    if document.content_type not in {"application/pdf", "image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=415, detail="OCR de factura admite PDF, JPEG, PNG o WebP")
    post_url = (
        f"{endpoint}/documentintelligence/documentModels/prebuilt-invoice:analyze"
        "?api-version=2024-11-30"
    )
    try:
        with httpx.Client(timeout=httpx.Timeout(25.0, connect=5.0), follow_redirects=False) as client:
            started = client.post(
                post_url,
                headers={
                    "Ocp-Apim-Subscription-Key": api_key,
                    "Content-Type": document.content_type,
                },
                content=document.content,
            )
            if started.status_code != 202:
                raise HTTPException(
                    status_code=502,
                    detail=f"El proveedor OCR no aceptó el archivo (HTTP {started.status_code})",
                )
            operation_url = started.headers.get("Operation-Location")
            parsed_operation = urlparse(operation_url or "")
            if (
                not operation_url
                or parsed_operation.scheme != "https"
                or parsed_operation.hostname != parsed_endpoint.hostname
                or parsed_operation.port != parsed_endpoint.port
            ):
                raise HTTPException(status_code=502, detail="El proveedor OCR devolvió una ubicación inválida")
            result: dict[str, object] | None = None
            for attempt in range(20):
                if attempt:
                    time.sleep(1)
                response = client.get(
                    operation_url,
                    headers={"Ocp-Apim-Subscription-Key": api_key},
                )
                if response.status_code != 200:
                    raise HTTPException(
                        status_code=502,
                        detail=f"No se pudo consultar el resultado OCR (HTTP {response.status_code})",
                    )
                operation = response.json()
                if not isinstance(operation, dict):
                    raise HTTPException(status_code=502, detail="El proveedor OCR devolvió un formato inválido")
                status_value = str(operation.get("status", "")).lower()
                if status_value == "succeeded":
                    result = normalize_azure_invoice_result(operation)
                    break
                if status_value == "failed":
                    raise HTTPException(status_code=422, detail="El proveedor OCR no pudo extraer la factura")
            if result is None:
                raise HTTPException(status_code=504, detail="El OCR excedió el tiempo de espera; puede volver a intentarlo")
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="No se pudo conectar con el proveedor OCR configurado") from exc
    extraction = DocumentExtraction(
        company_id=company_id,
        document_id=document_id,
        created_by=membership.user_id,
        provider="azure-document-intelligence/prebuilt-invoice",
        status="pending_review",
        fields_json=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
    )
    db.add(extraction)
    db.flush()
    add_audit(
        db,
        membership.user_id,
        company_id,
        "document.invoice_extract",
        "document_extraction",
        extraction.id,
        request,
    )
    db.commit()
    db.refresh(extraction)
    return extraction


@app.get(
    "/companies/{company_id}/document-extractions",
    response_model=list[DocumentExtractionRead],
)
def list_document_extractions(
    company_id: str,
    membership: Annotated[
        Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))
    ],
    db: DbSession,
) -> list[DocumentExtraction]:
    return db.scalars(
        select(DocumentExtraction)
        .where(DocumentExtraction.company_id == company_id)
        .order_by(DocumentExtraction.created_at.desc())
        .limit(100)
    ).all()


@app.post("/companies/{company_id}/document-extractions/{extraction_id}/decision")
def decide_document_extraction(
    company_id: str,
    extraction_id: str,
    payload: DocumentExtractionDecision,
    request: Request,
    membership: Annotated[
        Membership, Depends(require_company_role({"admin", "accountant"}))
    ],
    db: DbSession,
) -> dict[str, str]:
    extraction = db.scalar(
        select(DocumentExtraction)
        .where(
            DocumentExtraction.id == extraction_id,
            DocumentExtraction.company_id == company_id,
        )
        .with_for_update()
    )
    if not extraction or extraction.status != "pending_review":
        raise HTTPException(status_code=404, detail="Extracción OCR pendiente no encontrada")
    if extraction.created_by == membership.user_id:
        raise HTTPException(status_code=409, detail="La extracción debe ser revisada por otra persona")
    extraction.status = "approved" if payload.decision == "approve" else "rejected"
    extraction.reviewed_by = membership.user_id
    extraction.reviewed_at = datetime.now(timezone.utc)
    add_audit(
        db,
        membership.user_id,
        company_id,
        f"document.ocr_{payload.decision}",
        "document_extraction",
        extraction.id,
        request,
    )
    db.commit()
    return {"status": extraction.status, "extraction_id": extraction.id}


@app.get("/personal/overview")
def personal_overview(
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
    period: str,
) -> dict[str, object]:
    start_date, end_date = get_personal_period_bounds(period)
    rows = db.scalars(
        select(PersonalTransaction).where(
            PersonalTransaction.user_id == user.id,
            PersonalTransaction.status == "active",
            PersonalTransaction.transaction_date >= start_date,
            PersonalTransaction.transaction_date < end_date,
        )
    ).all()
    totals: dict[str, dict[str, Decimal]] = {}
    for row in rows:
        currency_totals = totals.setdefault(
            row.currency, {"income": Decimal("0.00"), "expense": Decimal("0.00")}
        )
        currency_totals[row.kind] += row.amount
    return {
        "period": period,
        "totals": {
            currency: {
                "income": str(values["income"]),
                "expense": str(values["expense"]),
                "net": str(values["income"] - values["expense"]),
            }
            for currency, values in totals.items()
        },
    }


@app.get("/personal/transactions", response_model=list[PersonalTransactionRead])
def list_personal_transactions(
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
    period: str,
) -> list[PersonalTransaction]:
    start_date, end_date = get_personal_period_bounds(period)
    return db.scalars(
        select(PersonalTransaction)
        .where(
            PersonalTransaction.user_id == user.id,
            PersonalTransaction.status == "active",
            PersonalTransaction.transaction_date >= start_date,
            PersonalTransaction.transaction_date < end_date,
        )
        .order_by(PersonalTransaction.transaction_date.desc(), PersonalTransaction.created_at.desc())
        .limit(500)
    ).all()


@app.post("/personal/transactions", response_model=PersonalTransactionRead, status_code=201)
def create_personal_transaction(
    payload: PersonalTransactionCreate,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> PersonalTransaction:
    transaction = PersonalTransaction(user_id=user.id, **payload.model_dump())
    db.add(transaction)
    db.flush()
    add_audit(db, user.id, None, "personal_transaction.create", "personal_transaction", transaction.id, request)
    db.commit()
    db.refresh(transaction)
    return transaction


@app.post("/personal/transactions/{transaction_id}/archive", status_code=204)
def archive_personal_transaction(
    transaction_id: str,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> Response:
    transaction = db.scalar(
        select(PersonalTransaction).where(
            PersonalTransaction.id == transaction_id,
            PersonalTransaction.user_id == user.id,
            PersonalTransaction.status == "active",
        )
    )
    if not transaction:
        raise HTTPException(status_code=404, detail="Movimiento personal no encontrado")
    transaction.status = "archived"
    add_audit(
        db, user.id, None, "personal_transaction.archive", "personal_transaction", transaction.id, request
    )
    db.commit()
    return Response(status_code=204)


@app.get("/personal/plans", response_model=list[PersonalPlanItemRead])
def list_personal_plans(
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
    start_date: date,
    end_date: date,
) -> list[PersonalPlanItem]:
    if end_date < start_date or (end_date - start_date).days > 30:
        raise HTTPException(status_code=422, detail="El rango debe ser de hasta 31 días")
    return db.scalars(
        select(PersonalPlanItem)
        .where(
            PersonalPlanItem.user_id == user.id,
            PersonalPlanItem.status != "archived",
            PersonalPlanItem.planned_date >= start_date,
            PersonalPlanItem.planned_date <= end_date,
        )
        .order_by(PersonalPlanItem.planned_date, PersonalPlanItem.created_at)
    ).all()


@app.post("/personal/plans", response_model=PersonalPlanItemRead, status_code=201)
def create_personal_plan(
    payload: PersonalPlanItemCreate,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> PersonalPlanItem:
    item = PersonalPlanItem(user_id=user.id, **payload.model_dump())
    db.add(item)
    db.flush()
    add_audit(db, user.id, None, "personal_plan.create", "personal_plan_item", item.id, request)
    db.commit()
    db.refresh(item)
    return item


@app.patch("/personal/plans/{item_id}", response_model=PersonalPlanItemRead)
def update_personal_plan(
    item_id: str,
    payload: PersonalPlanItemUpdate,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> PersonalPlanItem:
    item = db.scalar(
        select(PersonalPlanItem).where(
            PersonalPlanItem.id == item_id,
            PersonalPlanItem.user_id == user.id,
            PersonalPlanItem.status != "archived",
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Actividad no encontrada")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    add_audit(db, user.id, None, "personal_plan.update", "personal_plan_item", item.id, request)
    db.commit()
    db.refresh(item)
    return item


@app.delete("/personal/plans/{item_id}", status_code=204)
def archive_personal_plan(
    item_id: str,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
) -> Response:
    item = db.scalar(
        select(PersonalPlanItem).where(
            PersonalPlanItem.id == item_id,
            PersonalPlanItem.user_id == user.id,
            PersonalPlanItem.status != "archived",
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Actividad no encontrada")
    item.status = "archived"
    add_audit(db, user.id, None, "personal_plan.archive", "personal_plan_item", item.id, request)
    db.commit()
    return Response(status_code=204)


@app.get("/companies/{company_id}/tax-books/{direction}.csv")
def export_tax_book(
    company_id: str,
    direction: str,
    period: str,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))],
    db: DbSession,
) -> StreamingResponse:
    if direction not in {"purchase", "sale"}:
        raise HTTPException(status_code=404, detail="Libro no encontrado")
    if len(period) != 6 or not period.isdigit():
        raise HTTPException(status_code=422, detail="El período debe tener formato AAAAMM")
    documents = db.scalars(
        select(TaxDocument)
        .where(
            TaxDocument.company_id == company_id,
            TaxDocument.direction == direction,
            TaxDocument.period == period,
            TaxDocument.status == "posted",
        )
        .order_by(TaxDocument.document_date, TaxDocument.invoice_number)
    ).all()
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter="|", lineterminator="\r\n")
    writer.writerow(
        [
            "rif_contraparte",
            "nombre_contraparte",
            "fecha",
            "factura",
            "control",
            "base",
            "exento",
            "alicuota_iva",
            "iva",
            "iva_retenido",
            "total",
            "moneda_pago",
            "tasa_cambio",
            "igtf",
        ]
    )
    for document in documents:
        writer.writerow(
            [
                document.counterparty_rif,
                document.counterparty_name,
                document.document_date.isoformat(),
                document.invoice_number,
                document.control_number,
                document.base_amount,
                document.exempt_amount,
                document.vat_rate_percent,
                document.vat_amount,
                document.vat_withheld,
                document.total_amount,
                document.payment_currency,
                document.exchange_rate or "",
                document.igtf_amount,
            ]
        )
    output.seek(0)
    filename = f"libro-{direction}-{company_id}-{period}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/companies/{company_id}/reports/trial-balance")
def trial_balance(
    company_id: str,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))],
    db: DbSession,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, str]]:
    query = (
        select(
            Account.code,
            Account.name,
            func.coalesce(func.sum(JournalLine.debit), 0).label("debit"),
            func.coalesce(func.sum(JournalLine.credit), 0).label("credit"),
        )
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(Journal, Journal.id == JournalLine.journal_id)
        .where(Account.company_id == company_id, Journal.status == "posted")
        .group_by(Account.id)
        .order_by(Account.code)
    )
    if start_date:
        query = query.where(Journal.entry_date >= start_date)
    if end_date:
        query = query.where(Journal.entry_date <= end_date)
    return [
        {
            "code": row.code,
            "name": row.name,
            "debit": str(money(Decimal(row.debit))),
            "credit": str(money(Decimal(row.credit))),
            "balance": str(money(Decimal(row.debit) - Decimal(row.credit))),
        }
        for row in db.execute(query)
    ]


@app.get("/companies/{company_id}/reports/financial-statements")
def financial_statements(
    company_id: str,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))],
    db: DbSession,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, object]:
    query = (
        select(
            Account.code,
            Account.name,
            Account.account_type,
            func.coalesce(func.sum(JournalLine.debit), 0).label("debit"),
            func.coalesce(func.sum(JournalLine.credit), 0).label("credit"),
        )
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(Journal, Journal.id == JournalLine.journal_id)
        .where(Account.company_id == company_id, Journal.status == "posted")
        .group_by(Account.id)
        .order_by(Account.code)
    )
    if start_date:
        query = query.where(Journal.entry_date >= start_date)
    if end_date:
        query = query.where(Journal.entry_date <= end_date)
    accounts: list[dict[str, str]] = []
    for row in db.execute(query):
        debit, credit = Decimal(row.debit), Decimal(row.credit)
        normal_balance = debit - credit if row.account_type in {"asset", "expense"} else credit - debit
        accounts.append({
            "code": row.code,
            "name": row.name,
            "type": row.account_type,
            "debit": str(money(debit)),
            "credit": str(money(credit)),
            "balance": str(money(normal_balance)),
        })
    by_type = {
        kind: [item for item in accounts if item["type"] == kind]
        for kind in ("asset", "liability", "equity", "revenue", "expense")
    }
    totals = {
        kind: money(sum((Decimal(item["balance"]) for item in by_type[kind]), Decimal("0")))
        for kind in by_type
    }
    profit = money(totals["revenue"] - totals["expense"])
    return {
        "balance_sheet": {
            "assets": by_type["asset"],
            "liabilities": by_type["liability"],
            "equity": by_type["equity"],
            "total_assets": str(totals["asset"]),
            "total_liabilities": str(totals["liability"]),
            "total_equity": str(totals["equity"]),
            "period_result": str(profit),
            "check_difference": str(
                money(totals["asset"] - totals["liability"] - totals["equity"] - profit)
            ),
        },
        "income_statement": {
            "revenues": by_type["revenue"],
            "expenses": by_type["expense"],
            "total_revenue": str(totals["revenue"]),
            "total_expenses": str(totals["expense"]),
            "period_result": str(profit),
        },
    }


@app.get("/companies/{company_id}/reports/tax-summary")
def tax_summary(
    company_id: str,
    period: str,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))],
    db: DbSession,
) -> dict[str, str]:
    if len(period) != 6 or not period.isdigit():
        raise HTTPException(status_code=422, detail="El período debe tener formato AAAAMM")
    rows = db.execute(
        select(
            TaxDocument.direction,
            func.coalesce(func.sum(TaxDocument.base_amount), 0).label("base"),
            func.coalesce(func.sum(TaxDocument.exempt_amount), 0).label("exempt"),
            func.coalesce(func.sum(TaxDocument.vat_amount), 0).label("vat"),
            func.coalesce(func.sum(TaxDocument.vat_withheld), 0).label("withheld"),
        )
        .where(
            TaxDocument.company_id == company_id,
            TaxDocument.period == period,
            TaxDocument.status == "posted",
        )
        .group_by(TaxDocument.direction)
    )
    result = {
        "purchase_base": Decimal("0"),
        "purchase_exempt": Decimal("0"),
        "purchase_vat": Decimal("0"),
        "purchase_vat_withheld": Decimal("0"),
        "sale_base": Decimal("0"),
        "sale_exempt": Decimal("0"),
        "sale_vat": Decimal("0"),
        "sale_vat_withheld": Decimal("0"),
    }
    for row in rows:
        prefix = "purchase" if row.direction == "purchase" else "sale"
        result[f"{prefix}_base"] = Decimal(row.base)
        result[f"{prefix}_exempt"] = Decimal(row.exempt)
        result[f"{prefix}_vat"] = Decimal(row.vat)
        result[f"{prefix}_vat_withheld"] = Decimal(row.withheld)
    return {key: str(money(value)) for key, value in result.items()}


@app.get(
    "/companies/{company_id}/tax-workpapers",
    response_model=list[TaxWorkpaperRead],
)
def list_tax_workpapers(
    company_id: str,
    membership: Annotated[
        Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))
    ],
    db: DbSession,
) -> list[TaxWorkpaper]:
    return db.scalars(
        select(TaxWorkpaper)
        .where(TaxWorkpaper.company_id == company_id)
        .order_by(TaxWorkpaper.period.desc())
        .limit(120)
    ).all()


@app.post(
    "/companies/{company_id}/tax-workpapers",
    response_model=TaxWorkpaperRead,
    status_code=201,
)
def prepare_tax_workpaper(
    company_id: str,
    period: str,
    request: Request,
    membership: Annotated[
        Membership, Depends(require_company_role({"admin", "accountant"}))
    ],
    db: DbSession,
) -> TaxWorkpaper:
    if len(period) != 6 or not period.isdigit():
        raise HTTPException(status_code=422, detail="El período debe tener formato AAAAMM")
    try:
        get_personal_period_bounds(period)
    except HTTPException as exc:
        raise HTTPException(status_code=422, detail="Período inválido") from exc
    exists = db.scalar(
        select(TaxWorkpaper.id).where(
            TaxWorkpaper.company_id == company_id,
            TaxWorkpaper.period == period,
        )
    )
    if exists:
        raise HTTPException(status_code=409, detail="Ya existe una hoja de trabajo para el período")
    summary = tax_summary(company_id, period, membership, db)
    workpaper = TaxWorkpaper(
        company_id=company_id,
        period=period,
        status="draft",
        snapshot_json=json.dumps(summary, separators=(",", ":")),
        prepared_by=membership.user_id,
    )
    db.add(workpaper)
    db.flush()
    add_audit(
        db, membership.user_id, company_id, "tax_workpaper.prepare", "tax_workpaper", workpaper.id, request
    )
    db.commit()
    db.refresh(workpaper)
    return workpaper


@app.post("/companies/{company_id}/tax-workpapers/{workpaper_id}/review")
def review_tax_workpaper(
    company_id: str,
    workpaper_id: str,
    request: Request,
    membership: Annotated[
        Membership, Depends(require_company_role({"admin", "accountant"}))
    ],
    db: DbSession,
) -> dict[str, str]:
    workpaper = db.scalar(
        select(TaxWorkpaper)
        .where(
            TaxWorkpaper.id == workpaper_id,
            TaxWorkpaper.company_id == company_id,
        )
        .with_for_update()
    )
    if not workpaper or workpaper.status != "draft":
        raise HTTPException(status_code=404, detail="Hoja fiscal pendiente no encontrada")
    if workpaper.prepared_by == membership.user_id:
        raise HTTPException(status_code=409, detail="La hoja debe ser revisada por otra persona")
    workpaper.status = "reviewed"
    workpaper.reviewed_by = membership.user_id
    workpaper.reviewed_at = datetime.now(timezone.utc)
    add_audit(
        db, membership.user_id, company_id, "tax_workpaper.review", "tax_workpaper", workpaper.id, request
    )
    db.commit()
    return {
        "status": "reviewed",
        "message": "Hoja de trabajo revisada. Este sistema no presenta declaraciones ni transmite datos al SENIAT.",
    }


@app.get("/companies/{company_id}/accounts/{account_id}/ledger")
def account_ledger(
    company_id: str,
    account_id: str,
    membership: Annotated[Membership, Depends(require_company_role({"admin", "accountant", "auditor"}))],
    db: DbSession,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, str]]:
    account = db.scalar(
        select(Account).where(
            Account.id == account_id,
            Account.company_id == company_id,
        )
    )
    if not account:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")
    if account.status != "approved" or not account.active:
        raise HTTPException(status_code=409, detail="La cuenta debe aprobarse antes de consultar su mayor")
    query = (
        select(
            Journal.entry_date,
            Journal.id.label("journal_id"),
            Journal.reference,
            Journal.description.label("journal_description"),
            JournalLine.description.label("line_description"),
            JournalLine.debit,
            JournalLine.credit,
        )
        .join(Journal, Journal.id == JournalLine.journal_id)
        .where(
            Journal.company_id == company_id,
            JournalLine.account_id == account_id,
            Journal.status == "posted",
        )
    )
    if start_date:
        query = query.where(Journal.entry_date >= start_date)
    if end_date:
        query = query.where(Journal.entry_date <= end_date)
    rows = db.execute(
        query.order_by(Journal.entry_date, Journal.id, JournalLine.id)
    )
    balance = Decimal("0.00")
    ledger = []
    for row in rows:
        balance += row.debit - row.credit
        ledger.append(
            {
                "date": row.entry_date.isoformat(),
                "journal_id": row.journal_id,
                "reference": row.reference,
                "description": row.line_description or row.journal_description,
                "debit": str(money(row.debit)),
                "credit": str(money(row.credit)),
                "balance": str(money(balance)),
            }
        )
    return ledger


@app.post("/companies/{company_id}/periods/close", status_code=201)
def close_period(
    company_id: str,
    start_date: date,
    end_date: date,
    request: Request,
    membership: Annotated[Membership, Depends(require_company_role({"admin"}))],
    db: DbSession,
) -> dict[str, str | bool]:
    if end_date < start_date:
        raise HTTPException(status_code=422, detail="La fecha final debe ser posterior a la inicial")
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    overlaps = db.scalar(
        select(FiscalPeriod).where(
            FiscalPeriod.company_id == company_id,
            FiscalPeriod.start_date <= end_date,
            FiscalPeriod.end_date >= start_date,
        )
    )
    if overlaps:
        raise HTTPException(status_code=409, detail="El período se superpone con un período ya registrado")
    drafts = db.scalar(
        select(func.count())
        .select_from(Journal)
        .where(
            Journal.company_id == company_id,
            Journal.status == "draft",
            Journal.entry_date >= start_date,
            Journal.entry_date <= end_date,
        )
    )
    if drafts:
        raise HTTPException(status_code=409, detail="No se puede cerrar el período con asientos en borrador")
    period = FiscalPeriod(
        company_id=company_id,
        start_date=start_date,
        end_date=end_date,
        closed=True,
        closed_by=membership.user_id,
        closed_at=datetime.now(timezone.utc),
    )
    db.add(period)
    try:
        db.flush()
        add_audit(db, membership.user_id, company_id, "period.close", "period", period.id, request)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El período ya existe") from exc
    return {"id": period.id, "closed": True}
