from alembic import op
import sqlalchemy as sa


revision = "0007_security_retention"
down_revision = "0006_business_tools_demo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    user_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "auth_version" not in user_columns:
        op.add_column(
            "users",
            sa.Column("auth_version", sa.Integer(), server_default="0", nullable=False),
        )

    demo_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("demo_access_requests")
    }
    if "privacy_notice_version" not in demo_columns:
        op.add_column(
            "demo_access_requests",
            sa.Column(
                "privacy_notice_version",
                sa.String(30),
                server_default="legacy-no-consent-record",
                nullable=False,
            ),
        )
    if "privacy_accepted_at" not in demo_columns:
        op.add_column(
            "demo_access_requests",
            sa.Column(
                "privacy_accepted_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "password_reset_challenges" not in tables:
        op.create_table(
            "password_reset_challenges",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("email_digest", sa.String(64), nullable=False),
            sa.Column("user_id", sa.String(36), nullable=True),
            sa.Column("token_digest", sa.String(64), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("password_reset_challenges")
    }
    if "ix_password_reset_email_created" not in indexes:
        op.create_index(
            "ix_password_reset_email_created",
            "password_reset_challenges",
            ["email_digest", "created_at"],
        )
    if "ix_password_reset_user_created" not in indexes:
        op.create_index(
            "ix_password_reset_user_created",
            "password_reset_challenges",
            ["user_id", "created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "password_reset_challenges" in tables:
        op.drop_table("password_reset_challenges")
    for table, column in (
        ("demo_access_requests", "privacy_accepted_at"),
        ("demo_access_requests", "privacy_notice_version"),
        ("users", "auth_version"),
    ):
        columns = {item["name"] for item in sa.inspect(bind).get_columns(table)}
        if column in columns:
            op.drop_column(table, column)
