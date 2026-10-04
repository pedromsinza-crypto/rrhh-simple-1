from alembic import op
import sqlalchemy as sa


revision = "0005_personal_finance_planner"
down_revision = "0004_company_document_vault"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "personal_transactions" not in tables:
        op.create_table(
            "personal_transactions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("transaction_date", sa.Date(), nullable=False),
            sa.Column("kind", sa.String(length=10), nullable=False),
            sa.Column("category", sa.String(length=60), nullable=False),
            sa.Column("description", sa.String(length=300), nullable=False),
            sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
            sa.Column("currency", sa.String(length=3), nullable=False),
            sa.Column("status", sa.String(length=10), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                "kind IN ('income','expense')", name="ck_personal_transaction_kind"
            ),
            sa.CheckConstraint(
                "status IN ('active','archived')",
                name="ck_personal_transaction_status",
            ),
            sa.CheckConstraint("amount > 0", name="ck_personal_transaction_amount"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    tables = set(sa.inspect(bind).get_table_names())
    if "personal_plan_items" not in tables:
        op.create_table(
            "personal_plan_items",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("planned_date", sa.Date(), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("notes", sa.String(length=1000), nullable=False),
            sa.Column("status", sa.String(length=10), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                "status IN ('open','done','archived')", name="ck_personal_plan_status"
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    inspector = sa.inspect(bind)
    transaction_indexes = {
        index["name"] for index in inspector.get_indexes("personal_transactions")
    }
    if "ix_personal_transaction_user_date" not in transaction_indexes:
        op.create_index(
            "ix_personal_transaction_user_date",
            "personal_transactions",
            ["user_id", "transaction_date"],
        )
    plan_indexes = {index["name"] for index in inspector.get_indexes("personal_plan_items")}
    if "ix_personal_plan_user_date" not in plan_indexes:
        op.create_index(
            "ix_personal_plan_user_date",
            "personal_plan_items",
            ["user_id", "planned_date"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "personal_plan_items" in tables:
        op.drop_table("personal_plan_items")
    if "personal_transactions" in tables:
        op.drop_table("personal_transactions")
