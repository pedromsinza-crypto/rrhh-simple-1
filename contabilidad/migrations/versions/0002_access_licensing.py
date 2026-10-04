from alembic import op
import sqlalchemy as sa

revision = "0002_access_licensing"
down_revision = "0001_enterprise_modules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "access_licenses" not in tables:
        op.create_table(
            "access_licenses",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("role", sa.String(20), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("subscribed_at", sa.Date(), nullable=False),
            sa.Column("expires_at", sa.Date(), nullable=False),
            sa.Column("max_companies", sa.Integer(), nullable=False, server_default="10"),
            sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint(
                "status IN ('active','suspended','cancelled')",
                name="ck_access_license_status",
            ),
            sa.CheckConstraint(
                "role IN ('accountant','admin')",
                name="ck_access_license_role",
            ),
            sa.CheckConstraint(
                "max_companies BETWEEN 1 AND 10",
                name="ck_access_license_company_limit",
            ),
        )
        op.create_index(
            "ix_access_licenses_user_id", "access_licenses", ["user_id"], unique=True
        )
    if "license_payments" not in tables:
        op.create_table(
            "license_payments",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "license_id",
                sa.String(36),
                sa.ForeignKey("access_licenses.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("paid_at", sa.Date(), nullable=False),
            sa.Column("amount", sa.Numeric(18, 2), nullable=False),
            sa.Column("currency", sa.String(3), nullable=False),
            sa.Column("method", sa.String(30), nullable=False),
            sa.Column("reference", sa.String(100), nullable=False, server_default=""),
            sa.Column("notes", sa.Text(), nullable=False, server_default=""),
            sa.Column("recorded_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("amount > 0", name="ck_license_payment_positive"),
        )
        op.create_index(
            "ix_license_payment_license_paid",
            "license_payments",
            ["license_id", "paid_at"],
        )

    company_columns = {column["name"] for column in inspector.get_columns("companies")}
    if "license_id" not in company_columns:
        op.add_column("companies", sa.Column("license_id", sa.String(36), nullable=True))
        op.create_foreign_key(
            "fk_companies_license_id_access_licenses",
            "companies",
            "access_licenses",
            ["license_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_index("ix_companies_license_id", "companies", ["license_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    company_columns = {column["name"] for column in inspector.get_columns("companies")}
    if "license_id" in company_columns:
        for index in inspector.get_indexes("companies"):
            if "license_id" in index["column_names"]:
                op.drop_index(index["name"], table_name="companies")
        for constraint in inspector.get_foreign_keys("companies"):
            if constraint["constrained_columns"] == ["license_id"]:
                op.drop_constraint(constraint["name"], "companies", type_="foreignkey")
        op.drop_column("companies", "license_id")
    tables = set(inspector.get_table_names())
    if "license_payments" in tables:
        op.drop_table("license_payments")
    if "access_licenses" in tables:
        op.drop_table("access_licenses")
