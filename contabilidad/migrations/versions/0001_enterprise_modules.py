from alembic import op
import sqlalchemy as sa

from app.database import Base

revision = "0001_enterprise_modules"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)
    inspector = sa.inspect(bind)

    journal_columns = {column["name"] for column in inspector.get_columns("journals")}
    if "source_type" not in journal_columns:
        op.add_column(
            "journals",
            sa.Column("source_type", sa.String(30), nullable=False, server_default="manual"),
        )
    if "reversal_of_id" not in journal_columns:
        op.add_column(
            "journals",
            sa.Column("reversal_of_id", sa.String(36), nullable=True),
        )
        op.create_foreign_key(
            "fk_journals_reversal_of_id",
            "journals",
            "journals",
            ["reversal_of_id"],
            ["id"],
        )
    if "source_id" not in journal_columns:
        op.add_column("journals", sa.Column("source_id", sa.String(36), nullable=True))

    tax_columns = {column["name"] for column in inspector.get_columns("tax_documents")}
    if "contact_id" not in tax_columns:
        op.add_column(
            "tax_documents",
            sa.Column("contact_id", sa.String(36), nullable=True),
        )
        op.create_foreign_key(
            "fk_tax_documents_contact_id",
            "tax_documents",
            "contacts",
            ["contact_id"],
            ["id"],
        )
    if "status" not in tax_columns:
        op.add_column(
            "tax_documents",
            sa.Column("status", sa.String(10), nullable=False, server_default="posted"),
        )
    tax_checks = {
        constraint["name"]
        for constraint in sa.inspect(bind).get_check_constraints("tax_documents")
    }
    if "ck_tax_document_status" not in tax_checks:
        op.create_check_constraint(
            "ck_tax_document_status",
            "tax_documents",
            "status IN ('pending','posted','reversed')",
        )

    journal_checks = {constraint["name"] for constraint in inspector.get_check_constraints("journals")}
    if "ck_journal_status" in journal_checks:
        op.drop_constraint("ck_journal_status", "journals", type_="check")
    op.create_check_constraint(
        "ck_journal_status",
        "journals",
        "status IN ('draft','pending','posted','reversed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tax_document_status", "tax_documents", type_="check")
    op.drop_constraint("ck_journal_status", "journals", type_="check")
    op.create_check_constraint(
        "ck_journal_status", "journals", "status IN ('draft','posted')"
    )
    if "contact_id" in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("tax_documents")}:
        op.drop_constraint("fk_tax_documents_contact_id", "tax_documents", type_="foreignkey")
        op.drop_column("tax_documents", "contact_id")
    op.drop_column("tax_documents", "status")
    op.drop_constraint("fk_journals_reversal_of_id", "journals", type_="foreignkey")
    op.drop_column("journals", "reversal_of_id")
    op.drop_column("journals", "source_id")
    op.drop_column("journals", "source_type")
