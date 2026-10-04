import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def new_id() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    identity_number: Mapped[str | None] = mapped_column(String(12), unique=True)
    personal_rif: Mapped[str | None] = mapped_column(String(12), unique=True)
    personal_address: Mapped[str | None] = mapped_column(Text)
    profession: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class AccessLicense(Base):
    __tablename__ = "access_licenses"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','suspended','cancelled')",
            name="ck_access_license_status",
        ),
        CheckConstraint("role IN ('accountant','admin')", name="ck_access_license_role"),
        CheckConstraint("max_companies BETWEEN 1 AND 10", name="ck_access_license_company_limit"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    subscribed_at: Mapped[date] = mapped_column(Date, nullable=False)
    expires_at: Mapped[date] = mapped_column(Date, nullable=False)
    max_companies: Mapped[int] = mapped_column(default=10, nullable=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class LicensePayment(Base):
    __tablename__ = "license_payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_license_payment_positive"),
        Index("ix_license_payment_license_paid", "license_id", "paid_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    license_id: Mapped[str] = mapped_column(
        ForeignKey("access_licenses.id", ondelete="RESTRICT"), nullable=False
    )
    paid_at: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    method: Mapped[str] = mapped_column(String(30), nullable=False)
    reference: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    recorded_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    license_id: Mapped[str | None] = mapped_column(
        ForeignKey("access_licenses.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    rif: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    legal_name: Mapped[str] = mapped_column(String(150))
    tax_address: Mapped[str] = mapped_column(Text, default="")
    mercantile_registry: Mapped[str] = mapped_column(Text, default="")
    framework: Mapped[str] = mapped_column(String(10), default="PYME")
    special_taxpayer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="VES", nullable=False)
    contact_email: Mapped[str] = mapped_column(String(254), default="", nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    logo_content_type: Mapped[str | None] = mapped_column(String(30))
    logo_content: Mapped[bytes | None] = mapped_column(LargeBinary)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class Membership(Base):
    __tablename__ = "company_memberships"
    __table_args__ = (UniqueConstraint("user_id", "company_id", name="uq_user_company"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))
    user: Mapped[User] = relationship(back_populates="memberships")
    company: Mapped[Company] = relationship(back_populates="memberships")


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_account_company_code"),
        CheckConstraint("status IN ('pending','approved')", name="ck_account_status"),
        CheckConstraint(
            "account_type IN ('asset','liability','equity','revenue','expense')",
            name="ck_account_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(150))
    account_type: Mapped[str] = mapped_column(String(20))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    monetary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="approved", nullable=False)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Journal(Base):
    __tablename__ = "journals"
    __table_args__ = (
        Index("ix_journal_company_date", "company_id", "entry_date"),
        CheckConstraint(
            "status IN ('draft','pending','posted','reversed')",
            name="ck_journal_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    entry_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(String(500))
    reference: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(10), default="draft", nullable=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_type: Mapped[str] = mapped_column(String(30), default="manual", nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(36))
    reversal_of_id: Mapped[str | None] = mapped_column(ForeignKey("journals.id"))
    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="journal", cascade="all, delete-orphan"
    )


class JournalLine(Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        CheckConstraint("debit >= 0 AND credit >= 0", name="ck_line_nonnegative"),
        CheckConstraint(
            "(debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0)",
            name="ck_line_one_side",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    journal_id: Mapped[str] = mapped_column(ForeignKey("journals.id", ondelete="CASCADE"))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    description: Mapped[str] = mapped_column(String(300), default="")
    debit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    journal: Mapped[Journal] = relationship(back_populates="lines")


class FiscalPeriod(Base):
    __tablename__ = "fiscal_periods"
    __table_args__ = (
        UniqueConstraint("company_id", "start_date", "end_date", name="uq_company_period"),
        CheckConstraint("end_date >= start_date", name="ck_period_dates"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    closed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("company_id", "rif", name="uq_contact_company_rif"),
        CheckConstraint("kind IN ('customer','supplier','both')", name="ck_contact_kind"),
        Index("ix_contact_company_name", "company_id", "name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    rif: Mapped[str] = mapped_column(String(12))
    name: Mapped[str] = mapped_column(String(150))
    kind: Mapped[str] = mapped_column(String(10))
    email: Mapped[str] = mapped_column(String(254), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TaxDocument(Base):
    __tablename__ = "tax_documents"
    __table_args__ = (
        Index("ix_tax_document_company_date", "company_id", "document_date"),
        UniqueConstraint(
            "company_id", "direction", "counterparty_rif", "invoice_number",
            name="uq_tax_document_invoice",
        ),
        CheckConstraint("direction IN ('purchase','sale')", name="ck_tax_direction"),
        CheckConstraint(
            "status IN ('pending','posted','reversed')",
            name="ck_tax_document_status",
        ),
        CheckConstraint(
            "base_amount >= 0 AND exempt_amount >= 0 AND vat_amount >= 0",
            name="ck_tax_document_amounts",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    direction: Mapped[str] = mapped_column(String(10))
    document_date: Mapped[date] = mapped_column(Date)
    period: Mapped[str] = mapped_column(String(6))
    counterparty_rif: Mapped[str] = mapped_column(String(12))
    counterparty_name: Mapped[str] = mapped_column(String(150))
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id"))
    invoice_number: Mapped[str] = mapped_column(String(30))
    control_number: Mapped[str] = mapped_column(String(30), default="")
    commercial_description: Mapped[str] = mapped_column(String(300), default="")
    base_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    exempt_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    vat_rate_percent: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("16"))
    vat_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    vat_withholding_percent: Mapped[Decimal] = mapped_column(
        Numeric(7, 4), default=Decimal("0")
    )
    vat_withheld: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    payment_currency: Mapped[str] = mapped_column(String(3), default="VES")
    payment_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    igtf_applicable: Mapped[bool] = mapped_column(Boolean, default=False)
    igtf_rate_percent: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("3"))
    igtf_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    journal_id: Mapped[str | None] = mapped_column(ForeignKey("journals.id"))
    status: Mapped[str] = mapped_column(String(10), default="pending", nullable=False)


class OpeningBalance(Base):
    __tablename__ = "opening_balances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), unique=True
    )
    as_of_date: Mapped[date] = mapped_column(Date)
    journal_id: Mapped[str] = mapped_column(ForeignKey("journals.id"))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        Index("ix_payment_company_date", "company_id", "payment_date"),
        CheckConstraint("direction IN ('payable','receivable')", name="ck_payment_direction"),
        CheckConstraint("status IN ('pending','posted','reversed')", name="ck_payment_status"),
        CheckConstraint(
            "amount_ves > 0 AND foreign_amount >= 0 AND igtf_amount >= 0",
            name="ck_payment_amounts",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"))
    document_id: Mapped[str] = mapped_column(ForeignKey("tax_documents.id"))
    direction: Mapped[str] = mapped_column(String(12))
    payment_date: Mapped[date] = mapped_column(Date)
    reference: Mapped[str] = mapped_column(String(100), default="")
    method: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3), default="VES")
    amount_ves: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    foreign_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    igtf_rate_percent: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("3"))
    igtf_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    cash_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    igtf_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    journal_id: Mapped[str] = mapped_column(ForeignKey("journals.id"))
    status: Mapped[str] = mapped_column(String(10), default="pending", nullable=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BankStatementLine(Base):
    __tablename__ = "bank_statement_lines"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "bank_account_id", "external_reference",
            name="uq_bank_line_reference",
        ),
        CheckConstraint("amount <> 0", name="ck_bank_line_nonzero"),
        Index("ix_bank_line_company_date", "company_id", "statement_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    bank_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    statement_date: Mapped[date] = mapped_column(Date)
    external_reference: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(300), default="")
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    matched_payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id"))
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_company_created", "company_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"))
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str] = mapped_column(String(36), default="")
    ip_address: Mapped[str] = mapped_column(String(45), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CompanyDocument(Base):
    __tablename__ = "company_documents"
    __table_args__ = (
        Index("ix_company_document_company_uploaded", "company_id", "uploaded_at"),
        CheckConstraint("size_bytes > 0 AND size_bytes <= 10485760", name="ck_company_document_size"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(40), default="other")
    notes: Mapped[str] = mapped_column(String(1000), default="")
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PersonalTransaction(Base):
    __tablename__ = "personal_transactions"
    __table_args__ = (
        CheckConstraint("kind IN ('income','expense')", name="ck_personal_transaction_kind"),
        CheckConstraint("status IN ('active','archived')", name="ck_personal_transaction_status"),
        CheckConstraint("amount > 0", name="ck_personal_transaction_amount"),
        Index("ix_personal_transaction_user_date", "user_id", "transaction_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    transaction_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(300), default="")
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), default="VES")
    status: Mapped[str] = mapped_column(String(10), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PersonalPlanItem(Base):
    __tablename__ = "personal_plan_items"
    __table_args__ = (
        CheckConstraint("status IN ('open','done','archived')", name="ck_personal_plan_status"),
        Index("ix_personal_plan_user_date", "user_id", "planned_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    planned_date: Mapped[date] = mapped_column(Date)
    title: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str] = mapped_column(String(1000), default="")
    status: Mapped[str] = mapped_column(String(10), default="open", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProfessionalFee(Base):
    __tablename__ = "professional_fees"
    __table_args__ = (
        UniqueConstraint("company_id", "reference", name="uq_professional_fee_company_reference"),
        CheckConstraint("amount > 0", name="ck_professional_fee_amount"),
        CheckConstraint("currency IN ('VES','USD','EUR')", name="ck_professional_fee_currency"),
        CheckConstraint("status IN ('open','paid','void')", name="ck_professional_fee_status"),
        Index("ix_professional_fee_company_status_due", "company_id", "status", "due_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reference: Mapped[str] = mapped_column(String(30))
    service_description: Mapped[str] = mapped_column(String(300))
    service_period: Mapped[str] = mapped_column(String(7), default="")
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), default="VES")
    issued_at: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="open", nullable=False)
    paid_at: Mapped[date | None] = mapped_column(Date)
    payment_reference: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    notes: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AccountantClient(Base):
    __tablename__ = "accountant_clients"
    __table_args__ = (
        UniqueConstraint("accountant_user_id", "rif", name="uq_accountant_client_rif"),
        Index("ix_accountant_client_owner_name", "accountant_user_id", "name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    accountant_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    rif: Mapped[str] = mapped_column(String(12))
    name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(254), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    address: Mapped[str] = mapped_column(Text, default="", nullable=False)
    notes: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AccountantFee(Base):
    __tablename__ = "accountant_fees"
    __table_args__ = (
        UniqueConstraint("accountant_user_id", "reference", name="uq_accountant_fee_owner_reference"),
        CheckConstraint("amount > 0", name="ck_accountant_fee_amount"),
        CheckConstraint("currency IN ('VES','USD','EUR')", name="ck_accountant_fee_currency"),
        CheckConstraint("status IN ('open','paid','void')", name="ck_accountant_fee_status"),
        Index("ix_accountant_fee_owner_status_due", "accountant_user_id", "status", "due_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    accountant_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    client_id: Mapped[str] = mapped_column(ForeignKey("accountant_clients.id", ondelete="CASCADE"))
    reference: Mapped[str] = mapped_column(String(30))
    service_description: Mapped[str] = mapped_column(String(300))
    service_period: Mapped[str] = mapped_column(String(7), default="")
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), default="VES")
    issued_at: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="open", nullable=False)
    paid_at: Mapped[date | None] = mapped_column(Date)
    payment_reference: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    notes: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DemoAccessRequest(Base):
    __tablename__ = "demo_access_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_email','pending_review','approved','rejected')",
            name="ck_demo_request_status",
        ),
        Index("ix_demo_request_status_created", "status", "created_at"),
        Index("ix_demo_request_email_created", "email", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    identity_number: Mapped[str] = mapped_column(String(12), nullable=False)
    rif: Mapped[str] = mapped_column(String(12), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    profession: Mapped[str] = mapped_column(String(120), nullable=False)
    verification_hash: Mapped[str | None] = mapped_column(String(64))
    verification_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="pending_email", nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_notes: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DocumentExtraction(Base):
    __tablename__ = "document_extractions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing','pending_review','approved','rejected','failed')",
            name="ck_document_extraction_status",
        ),
        Index("ix_document_extraction_company_created", "company_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    document_id: Mapped[str] = mapped_column(ForeignKey("company_documents.id", ondelete="CASCADE"))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    provider: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="pending_review", nullable=False)
    fields_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaxWorkpaper(Base):
    __tablename__ = "tax_workpapers"
    __table_args__ = (
        UniqueConstraint("company_id", "period", name="uq_tax_workpaper_company_period"),
        CheckConstraint("status IN ('draft','reviewed')", name="ck_tax_workpaper_status"),
        Index("ix_tax_workpaper_company_status", "company_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    period: Mapped[str] = mapped_column(String(6))
    status: Mapped[str] = mapped_column(String(10), default="draft", nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    prepared_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    prepared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
