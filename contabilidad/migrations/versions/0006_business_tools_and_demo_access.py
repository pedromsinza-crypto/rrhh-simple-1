from alembic import op
import sqlalchemy as sa


revision = "0006_business_tools_demo"
down_revision = "0005_personal_finance_planner"
branch_labels = None
depends_on = None


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}
    if column.name not in columns:
        op.add_column(table, column)


def _create_table_if_missing(name: str, *elements: object) -> None:
    if name not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(name, *elements)


def _create_index_if_missing(name: str, table: str, columns: list[str]) -> None:
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in indexes:
        op.create_index(name, table, columns)


def upgrade() -> None:
    _add_column_if_missing("users", sa.Column("identity_number", sa.String(12), nullable=True))
    _add_column_if_missing("users", sa.Column("personal_rif", sa.String(12), nullable=True))
    _add_column_if_missing("users", sa.Column("personal_address", sa.Text(), nullable=True))
    _add_column_if_missing("users", sa.Column("profession", sa.String(120), nullable=True))
    _add_column_if_missing("companies", sa.Column("contact_email", sa.String(254), nullable=False, server_default=""))
    _add_column_if_missing("companies", sa.Column("contact_phone", sa.String(40), nullable=False, server_default=""))
    _add_column_if_missing("companies", sa.Column("logo_content_type", sa.String(30), nullable=True))
    _add_column_if_missing("companies", sa.Column("logo_content", sa.LargeBinary(), nullable=True))
    _add_column_if_missing(
        "tax_documents",
        sa.Column("commercial_description", sa.String(300), nullable=False, server_default=""),
    )
    inspector = sa.inspect(op.get_bind())
    for name in ("identity_number", "personal_rif"):
        indexes = {item["name"] for item in inspector.get_indexes("users")}
        unique_constraints = {item["name"] for item in inspector.get_unique_constraints("users")}
        if f"uq_users_{name}" not in unique_constraints:
            if f"ix_users_{name}" not in indexes:
                op.create_index(f"ix_users_{name}", "users", [name], unique=True)

    _create_table_if_missing(
        "professional_fees",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("company_id", sa.String(36), nullable=False),
        sa.Column("contact_id", sa.String(36), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("reference", sa.String(30), nullable=False),
        sa.Column("service_description", sa.String(300), nullable=False),
        sa.Column("service_period", sa.String(7), nullable=False, server_default=""),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="VES"),
        sa.Column("issued_at", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("paid_at", sa.Date(), nullable=True),
        sa.Column("payment_reference", sa.String(100), nullable=False, server_default=""),
        sa.Column("notes", sa.String(1000), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_professional_fee_amount"),
        sa.CheckConstraint("currency IN ('VES','USD','EUR')", name="ck_professional_fee_currency"),
        sa.CheckConstraint("status IN ('open','paid','void')", name="ck_professional_fee_status"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "reference", name="uq_professional_fee_company_reference"),
    )
    _create_index_if_missing(
        "ix_professional_fee_company_status_due",
        "professional_fees",
        ["company_id", "status", "due_date"],
    )

    _create_table_if_missing(
        "accountant_clients",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("accountant_user_id", sa.String(36), nullable=False),
        sa.Column("rif", sa.String(12), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("email", sa.String(254), nullable=False, server_default=""),
        sa.Column("phone", sa.String(40), nullable=False, server_default=""),
        sa.Column("address", sa.Text(), nullable=False, server_default=""),
        sa.Column("notes", sa.String(1000), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["accountant_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("accountant_user_id", "rif", name="uq_accountant_client_rif"),
    )
    _create_index_if_missing(
        "ix_accountant_client_owner_name",
        "accountant_clients",
        ["accountant_user_id", "name"],
    )
    _create_table_if_missing(
        "accountant_fees",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("accountant_user_id", sa.String(36), nullable=False),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.Column("reference", sa.String(30), nullable=False),
        sa.Column("service_description", sa.String(300), nullable=False),
        sa.Column("service_period", sa.String(7), nullable=False, server_default=""),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="VES"),
        sa.Column("issued_at", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("paid_at", sa.Date(), nullable=True),
        sa.Column("payment_reference", sa.String(100), nullable=False, server_default=""),
        sa.Column("notes", sa.String(1000), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_accountant_fee_amount"),
        sa.CheckConstraint("currency IN ('VES','USD','EUR')", name="ck_accountant_fee_currency"),
        sa.CheckConstraint("status IN ('open','paid','void')", name="ck_accountant_fee_status"),
        sa.ForeignKeyConstraint(["accountant_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["accountant_clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("accountant_user_id", "reference", name="uq_accountant_fee_owner_reference"),
    )
    _create_index_if_missing(
        "ix_accountant_fee_owner_status_due",
        "accountant_fees",
        ["accountant_user_id", "status", "due_date"],
    )

    _create_table_if_missing(
        "demo_access_requests",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(150), nullable=False),
        sa.Column("identity_number", sa.String(12), nullable=False),
        sa.Column("rif", sa.String(12), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("profession", sa.String(120), nullable=False),
        sa.Column("verification_hash", sa.String(64), nullable=True),
        sa.Column("verification_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending_email"),
        sa.Column("reviewed_by", sa.String(36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_notes", sa.String(1000), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending_email','pending_review','approved','rejected')",
            name="ck_demo_request_status",
        ),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_index_if_missing(
        "ix_demo_request_status_created",
        "demo_access_requests",
        ["status", "created_at"],
    )
    _create_index_if_missing(
        "ix_demo_request_email_created",
        "demo_access_requests",
        ["email", "created_at"],
    )

    _create_table_if_missing(
        "document_extractions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("company_id", sa.String(36), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("reviewed_by", sa.String(36), nullable=True),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending_review"),
        sa.Column("fields_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('processing','pending_review','approved','rejected','failed')",
            name="ck_document_extraction_status",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["company_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_index_if_missing(
        "ix_document_extraction_company_created",
        "document_extractions",
        ["company_id", "created_at"],
    )
    _create_table_if_missing(
        "tax_workpapers",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("company_id", sa.String(36), nullable=False),
        sa.Column("period", sa.String(6), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("prepared_by", sa.String(36), nullable=False),
        sa.Column("reviewed_by", sa.String(36), nullable=True),
        sa.Column("prepared_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('draft','reviewed')", name="ck_tax_workpaper_status"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prepared_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "period", name="uq_tax_workpaper_company_period"),
    )
    _create_index_if_missing(
        "ix_tax_workpaper_company_status",
        "tax_workpapers",
        ["company_id", "status"],
    )


def downgrade() -> None:
    for table in (
        "document_extractions",
        "tax_workpapers",
        "demo_access_requests",
        "accountant_fees",
        "accountant_clients",
        "professional_fees",
    ):
        if table in sa.inspect(op.get_bind()).get_table_names():
            op.drop_table(table)
    inspector = sa.inspect(op.get_bind())
    for table, column in (
        ("tax_documents", "commercial_description"),
        ("companies", "logo_content"),
        ("companies", "logo_content_type"),
        ("companies", "contact_phone"),
        ("companies", "contact_email"),
        ("users", "profession"),
        ("users", "personal_address"),
        ("users", "personal_rif"),
        ("users", "identity_number"),
    ):
        columns = {item["name"] for item in inspector.get_columns(table)}
        if column in columns:
            op.drop_column(table, column)
