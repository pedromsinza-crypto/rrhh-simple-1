from alembic import op
import sqlalchemy as sa


revision = "0004_company_document_vault"
down_revision = "0003_company_account_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "company_documents" not in inspector.get_table_names():
        op.create_table(
            "company_documents",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("company_id", sa.String(length=36), nullable=False),
            sa.Column("uploaded_by", sa.String(length=36), nullable=False),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("content_type", sa.String(length=100), nullable=False),
            sa.Column("category", sa.String(length=40), nullable=False),
            sa.Column("notes", sa.String(length=1000), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("content", sa.LargeBinary(), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column(
                "uploaded_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "size_bytes > 0 AND size_bytes <= 10485760",
                name="ck_company_document_size",
            ),
            sa.ForeignKeyConstraint(
                ["company_id"], ["companies.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("company_documents")}
    if "ix_company_document_company_uploaded" not in indexes:
        op.create_index(
            "ix_company_document_company_uploaded",
            "company_documents",
            ["company_id", "uploaded_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "company_documents" in sa.inspect(bind).get_table_names():
        op.drop_table("company_documents")
