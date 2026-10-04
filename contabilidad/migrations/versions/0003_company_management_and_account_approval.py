from alembic import op
import sqlalchemy as sa

revision = "0003_company_account_approval"
down_revision = "0002_access_licensing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    company_columns = {column["name"] for column in inspector.get_columns("companies")}
    if "active" not in company_columns:
        op.add_column(
            "companies",
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        )

    account_columns = {column["name"] for column in inspector.get_columns("accounts")}
    if "status" not in account_columns:
        op.add_column(
            "accounts",
            sa.Column("status", sa.String(10), nullable=False, server_default="approved"),
        )
    if "created_by" not in account_columns:
        op.add_column("accounts", sa.Column("created_by", sa.String(36), nullable=True))
        op.create_foreign_key(
            "fk_accounts_created_by_users",
            "accounts",
            "users",
            ["created_by"],
            ["id"],
        )
    if "reviewed_by" not in account_columns:
        op.add_column("accounts", sa.Column("reviewed_by", sa.String(36), nullable=True))
        op.create_foreign_key(
            "fk_accounts_reviewed_by_users",
            "accounts",
            "users",
            ["reviewed_by"],
            ["id"],
        )
    if "created_at" not in account_columns:
        op.add_column(
            "accounts",
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
    checks = {
        constraint["name"]
        for constraint in sa.inspect(bind).get_check_constraints("accounts")
    }
    if "ck_account_status" not in checks:
        op.create_check_constraint(
            "ck_account_status",
            "accounts",
            "status IN ('pending','approved')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    account_columns = {column["name"] for column in inspector.get_columns("accounts")}
    checks = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("accounts")
    }
    if "ck_account_status" in checks:
        op.drop_constraint("ck_account_status", "accounts", type_="check")
    for column, constraint in (
        ("reviewed_by", "fk_accounts_reviewed_by_users"),
        ("created_by", "fk_accounts_created_by_users"),
    ):
        if column in account_columns:
            op.drop_constraint(constraint, "accounts", type_="foreignkey")
            op.drop_column("accounts", column)
    if "created_at" in account_columns:
        op.drop_column("accounts", "created_at")
    if "status" in account_columns:
        op.drop_column("accounts", "status")
    company_columns = {column["name"] for column in inspector.get_columns("companies")}
    if "active" in company_columns:
        op.drop_column("companies", "active")
