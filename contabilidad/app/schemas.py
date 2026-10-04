from datetime import date, datetime
from decimal import Decimal
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .services import normalize_rif


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    active: bool
    is_superuser: bool


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=128)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    email: EmailStr
    token: str = Field(min_length=32, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class SystemSettingsUpdate(BaseModel):
    company_limit: int = Field(ge=1, le=1000)


class AccessLicenseCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    role: Literal["accountant", "admin"]
    subscribed_at: date
    expires_at: date
    max_companies: int = Field(default=10, ge=1, le=10)

    @field_validator("expires_at")
    @classmethod
    def validate_expiration(cls, value: date, info) -> date:
        subscribed_at = info.data.get("subscribed_at")
        if subscribed_at and value < subscribed_at:
            raise ValueError("La fecha de vencimiento no puede ser anterior a la suscripción")
        return value


class AccessLicenseUpdate(BaseModel):
    email: EmailStr
    password: str | None = Field(default=None, min_length=12, max_length=128)
    role: Literal["accountant", "admin"]
    subscribed_at: date
    expires_at: date
    max_companies: int = Field(ge=1, le=10)

    @field_validator("expires_at")
    @classmethod
    def validate_expiration(cls, value: date, info) -> date:
        subscribed_at = info.data.get("subscribed_at")
        if subscribed_at and value < subscribed_at:
            raise ValueError("La fecha de vencimiento no puede ser anterior a la suscripción")
        return value


class LicensePaymentCreate(BaseModel):
    paid_at: date
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: Literal["VES", "USD"]
    method: Literal["transfer", "cash", "card", "mobile_payment", "other"]
    reference: str = Field(default="", max_length=100)
    notes: str = Field(default="", max_length=2000)


class CompanyCreate(BaseModel):
    rif: str
    legal_name: str = Field(min_length=2, max_length=150)
    tax_address: str = Field(default="", max_length=2000)
    mercantile_registry: str = Field(default="", max_length=2000)
    framework: Literal["GE", "PYME"] = "PYME"
    special_taxpayer: bool = False
    currency: Literal["VES"] = "VES"

    @field_validator("rif")
    @classmethod
    def validate_rif(cls, value: str) -> str:
        return normalize_rif(value)


class CompanyUpdate(BaseModel):
    rif: str | None = None
    legal_name: str | None = Field(default=None, min_length=2, max_length=150)
    tax_address: str | None = Field(default=None, max_length=2000)
    mercantile_registry: str | None = Field(default=None, max_length=2000)
    framework: Literal["GE", "PYME"] | None = None
    special_taxpayer: bool | None = None
    currency: Literal["VES"] | None = None

    @field_validator("rif")
    @classmethod
    def validate_rif(cls, value: str | None) -> str | None:
        return normalize_rif(value) if value is not None else None


class CompanyBrandingUpdate(BaseModel):
    contact_email: EmailStr | None = None
    contact_phone: str = Field(default="", max_length=40)


class DemoAccessRequestCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    full_name: str = Field(min_length=3, max_length=150)
    identity_number: str = Field(min_length=6, max_length=12)
    rif: str
    address: str = Field(min_length=8, max_length=2000)
    profession: str = Field(min_length=2, max_length=120)
    privacy_notice_accepted: bool
    verification_code: str | None = Field(default=None, pattern=r"^[0-9]{6}$")

    @field_validator("identity_number")
    @classmethod
    def validate_identity_number(cls, value: str) -> str:
        normalized = value.strip().upper().replace(" ", "").replace("-", "")
        if not re.fullmatch(r"[VE][0-9]{5,10}", normalized):
            raise ValueError("La cédula debe tener formato V/E seguido de 5 a 10 dígitos")
        return normalized

    @field_validator("rif")
    @classmethod
    def validate_demo_rif(cls, value: str) -> str:
        return normalize_rif(value)


class DemoAccessRequestDecision(BaseModel):
    decision: Literal["approve", "reject"]
    notes: str = Field(default="", max_length=1000)
    role: Literal["accountant", "admin"] = "accountant"


class DemoEmailVerification(BaseModel):
    code: str = Field(pattern=r"^[0-9]{6}$")


class DocumentExtractionDecision(BaseModel):
    decision: Literal["approve", "reject"]


class DemoAccessRequestRead(BaseModel):
    id: str
    email: EmailStr
    full_name: str
    identity_number: str
    rif: str
    address: str
    profession: str
    status: str
    email_verified_at: datetime | None
    created_at: datetime


class DemoAccessRequestSubmission(BaseModel):
    id: str
    email: EmailStr
    status: str


class ProfessionalFeeCreate(BaseModel):
    contact_id: str
    reference: str = Field(min_length=1, max_length=30)
    service_description: str = Field(min_length=1, max_length=300)
    service_period: str = Field(default="", max_length=7)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: Literal["VES", "USD", "EUR"] = "VES"
    issued_at: date
    due_date: date
    notes: str = Field(default="", max_length=1000)

    @field_validator("service_period")
    @classmethod
    def validate_service_period(cls, value: str) -> str:
        if value and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
            raise ValueError("El período debe tener formato AAAA-MM")
        return value


class ProfessionalFeePayment(BaseModel):
    paid_at: date
    payment_reference: str = Field(default="", max_length=100)


class ProfessionalFeeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    company_id: str
    contact_id: str
    created_by: str
    reference: str
    service_description: str
    service_period: str
    amount: Decimal
    currency: str
    issued_at: date
    due_date: date
    status: str
    paid_at: date | None
    payment_reference: str
    notes: str


class CompanyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    license_id: str | None
    rif: str
    legal_name: str
    tax_address: str
    mercantile_registry: str
    framework: str
    special_taxpayer: bool
    currency: str
    active: bool
    contact_email: str = ""
    contact_phone: str = ""


class MembershipCreate(BaseModel):
    email: EmailStr
    role: Literal["admin", "accountant", "auditor"]


class CompanyMemberInvite(BaseModel):
    email: EmailStr


class ContactCreate(BaseModel):
    rif: str
    name: str = Field(min_length=1, max_length=150)
    kind: Literal["customer", "supplier", "both"]
    email: EmailStr | None = None
    phone: str = Field(default="", max_length=40)
    address: str = Field(default="", max_length=2000)

    @field_validator("rif")
    @classmethod
    def validate_rif(cls, value: str) -> str:
        return normalize_rif(value)


class ContactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    company_id: str
    rif: str
    name: str
    kind: str
    email: str
    phone: str
    address: str
    active: bool


class CompanyDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    company_id: str
    uploaded_by: str
    filename: str
    content_type: str
    category: str
    notes: str
    size_bytes: int
    uploaded_at: datetime


class DocumentExtractionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    company_id: str
    document_id: str
    created_by: str
    reviewed_by: str | None
    provider: str
    status: str
    fields_json: str
    created_at: datetime
    reviewed_at: datetime | None


class PersonalTransactionCreate(BaseModel):
    transaction_date: date
    kind: Literal["income", "expense"]
    category: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=300)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: Literal["VES", "USD", "EUR"] = "VES"


class PersonalTransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    transaction_date: date
    kind: str
    category: str
    description: str
    amount: Decimal
    currency: str
    status: str


class PersonalPlanItemCreate(BaseModel):
    planned_date: date
    title: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=1000)


class PersonalPlanItemUpdate(BaseModel):
    planned_date: date | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=1000)
    status: Literal["open", "done"] | None = None


class PersonalPlanItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    planned_date: date
    title: str
    notes: str
    status: str


class AccountantClientCreate(BaseModel):
    rif: str
    name: str = Field(min_length=1, max_length=150)
    email: EmailStr | None = None
    phone: str = Field(default="", max_length=40)
    address: str = Field(default="", max_length=2000)
    notes: str = Field(default="", max_length=1000)

    @field_validator("rif")
    @classmethod
    def validate_accountant_client_rif(cls, value: str) -> str:
        return normalize_rif(value)


class AccountantClientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    rif: str
    name: str
    email: str
    phone: str
    address: str
    notes: str
    active: bool


class AccountantFeeCreate(BaseModel):
    client_id: str
    reference: str = Field(min_length=1, max_length=30)
    service_description: str = Field(min_length=1, max_length=300)
    service_period: str = Field(default="", max_length=7)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: Literal["VES", "USD", "EUR"] = "VES"
    issued_at: date
    due_date: date
    notes: str = Field(default="", max_length=1000)

    @field_validator("service_period")
    @classmethod
    def validate_accountant_fee_period(cls, value: str) -> str:
        if value and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
            raise ValueError("El período debe tener formato AAAA-MM")
        return value


class AccountantFeeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    accountant_user_id: str
    client_id: str
    reference: str
    service_description: str
    service_period: str
    amount: Decimal
    currency: str
    issued_at: date
    due_date: date
    status: str
    paid_at: date | None
    payment_reference: str
    notes: str


class AccountantFeePayment(BaseModel):
    paid_at: date
    payment_reference: str = Field(default="", max_length=100)


class AccountCreate(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=150)
    account_type: Literal["asset", "liability", "equity", "revenue", "expense"]
    parent_id: str | None = None
    monetary: bool = True


class AccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    code: str
    name: str
    account_type: str
    parent_id: str | None
    monetary: bool
    active: bool
    status: str
    created_by: str | None
    reviewed_by: str | None


class JournalLineCreate(BaseModel):
    account_id: str
    description: str = Field(default="", max_length=300)
    debit: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)
    credit: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)


class JournalCreate(BaseModel):
    entry_date: date
    description: str = Field(min_length=1, max_length=500)
    reference: str = Field(default="", max_length=100)
    lines: list[JournalLineCreate] = Field(min_length=2, max_length=200)


class JournalLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    account_id: str
    description: str
    debit: Decimal
    credit: Decimal


class JournalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    company_id: str
    entry_date: date
    description: str
    reference: str
    status: str
    source_type: str
    source_id: str | None
    reversal_of_id: str | None
    lines: list[JournalLineRead]


class OpeningBalanceCreate(BaseModel):
    as_of_date: date
    lines: list[JournalLineCreate] = Field(min_length=2, max_length=300)


class TaxDocumentCreate(BaseModel):
    direction: Literal["purchase", "sale"]
    document_date: date
    counterparty_rif: str
    counterparty_name: str = Field(min_length=1, max_length=150)
    invoice_number: str = Field(min_length=1, max_length=30)
    control_number: str = Field(default="", max_length=30)
    commercial_description: str = Field(default="", max_length=300)
    base_amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    exempt_amount: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)
    vat_rate_percent: Decimal = Field(default=Decimal("16"), ge=0, le=100, max_digits=7, decimal_places=4)
    vat_withholding_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=7, decimal_places=4)
    payment_currency: Literal["VES", "USD", "EUR", "OTHER"] = "VES"
    payment_amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    exchange_rate: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=6)
    igtf_applicable: bool = False
    igtf_rate_percent: Decimal = Field(default=Decimal("3"), ge=0, le=100, max_digits=7, decimal_places=4)
    base_account_id: str
    vat_account_id: str
    counterparty_account_id: str
    withholding_account_id: str | None = None
    contact_id: str | None = None

    @field_validator("counterparty_rif")
    @classmethod
    def validate_counterparty_rif(cls, value: str) -> str:
        return normalize_rif(value)


class TaxDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    company_id: str
    direction: str
    document_date: date
    period: str
    counterparty_rif: str
    counterparty_name: str
    contact_id: str | None
    invoice_number: str
    control_number: str
    commercial_description: str
    base_amount: Decimal
    exempt_amount: Decimal
    vat_rate_percent: Decimal
    vat_amount: Decimal
    vat_withholding_percent: Decimal
    vat_withheld: Decimal
    total_amount: Decimal
    payment_currency: str
    payment_amount: Decimal | None
    exchange_rate: Decimal | None
    igtf_applicable: bool
    igtf_rate_percent: Decimal
    igtf_amount: Decimal
    journal_id: str | None
    status: str


class TaxWorkpaperRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    company_id: str
    period: str
    status: str
    snapshot_json: str
    prepared_by: str
    reviewed_by: str | None
    prepared_at: datetime
    reviewed_at: datetime | None


class PaymentCreate(BaseModel):
    document_id: str
    payment_date: date
    reference: str = Field(default="", max_length=100)
    method: Literal["bank", "cash", "transfer", "card", "divisa", "crypto", "other"]
    amount_ves: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: Literal["VES", "USD", "EUR", "OTHER"] = "VES"
    foreign_amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    exchange_rate: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=6)
    igtf_applicable: bool = False
    igtf_rate_percent: Decimal = Field(default=Decimal("3"), ge=0, le=100, max_digits=7, decimal_places=4)
    cash_account_id: str
    igtf_account_id: str | None = None


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    company_id: str
    contact_id: str
    document_id: str
    direction: str
    payment_date: date
    reference: str
    method: str
    currency: str
    amount_ves: Decimal
    foreign_amount: Decimal
    exchange_rate: Decimal | None
    igtf_rate_percent: Decimal
    igtf_amount: Decimal
    journal_id: str
    cash_account_id: str
    status: str
    created_by: str
    posted_at: datetime | None


class BankStatementLineCreate(BaseModel):
    bank_account_id: str
    statement_date: date
    external_reference: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=300)
    amount: Decimal = Field(max_digits=18, decimal_places=2)
