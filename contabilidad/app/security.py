import os
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import AccessLicense, Company, Membership, User


ALGORITHM = "HS256"
JWT_SECRET = os.getenv("JWT_SECRET", "")
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "60"))
password_hash = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")
DbSession = Annotated[Session, Depends(get_db)]


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


def create_access_token(subject: str, auth_version: int = 0) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_MINUTES)
    return jwt.encode(
        {"sub": subject, "ver": auth_version, "exp": expires},
        JWT_SECRET,
        algorithm=ALGORITHM,
    )


def current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: DbSession,
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales inválidas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise credentials_error
    except JWTError as exc:
        raise credentials_error from exc
    user = db.get(User, user_id)
    if (
        not user
        or not user.active
        or payload.get("ver", 0) != user.auth_version
    ):
        raise credentials_error
    if not user.is_superuser:
        license_access = db.scalar(
            select(AccessLicense).where(AccessLicense.user_id == user.id)
        )
        if not license_access or license_access.status != "active":
            raise HTTPException(status_code=403, detail="El acceso no tiene una licencia activa")
        if license_access.expires_at < date.today():
            raise HTTPException(status_code=402, detail="La licencia venció; contacte al administrador")
    return user


def company_role(
    company_id: str,
    user: Annotated[User, Depends(current_user)],
    db: DbSession,
    allowed_roles: set[str] | None = None,
) -> Membership:
    company = db.get(Company, company_id)
    if not company or not company.active:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    if user.is_superuser:
        return Membership(user_id=user.id, company_id=company_id, role="admin")
    membership = db.scalar(
        select(Membership).where(
            Membership.company_id == company_id,
            Membership.user_id == user.id,
        )
    )
    if not membership:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    license_access = db.scalar(
        select(AccessLicense).where(
            AccessLicense.user_id == user.id,
            AccessLicense.status == "active",
            AccessLicense.expires_at >= date.today(),
        )
    )
    if not license_access:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    if allowed_roles and membership.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="No tiene permisos para esta operación")
    return membership


def require_company_role(allowed_roles: set[str]):
    def dependency(
        company_id: str,
        user: Annotated[User, Depends(current_user)],
        db: DbSession,
    ) -> Membership:
        return company_role(company_id, user, db, allowed_roles)

    return dependency
