import os
import importlib
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

_db_file = Path(tempfile.gettempdir()) / f"accounting-tests-{os.getpid()}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file.as_posix()}"
os.environ["JWT_SECRET"] = "test-only-jwt-secret-with-enough-length"
os.environ["BOOTSTRAP_ADMIN_EMAIL"] = "admin@example.com"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "a-secure-test-password"

import pytest
from fastapi.testclient import TestClient

from app.database import engine
from app.main import app
from app.models import DemoAccessRequest, PasswordResetChallenge
from app.database import SessionLocal


@pytest.fixture(scope="module", autouse=True)
def cleanup_test_database():
    yield
    engine.dispose()
    _db_file.unlink(missing_ok=True)


def test_company_document_vault_upload_download_and_archive():
    with TestClient(app) as client:
        token = client.post(
            "/auth/token",
            data={"username": "admin@example.com", "password": "a-secure-test-password"},
        ).json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {token}"}
        license_response = client.post(
            "/admin/licenses",
            headers=admin_headers,
            json={
                "email": "vault@example.com",
                "password": "a-secure-vault-password",
                "role": "admin",
                "subscribed_at": date.today().isoformat(),
                "expires_at": "2030-12-31",
            },
        )
        assert license_response.status_code == 201, license_response.text
        user_token = client.post(
            "/auth/token",
            data={"username": "vault@example.com", "password": "a-secure-vault-password"},
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {user_token}"}
        company = client.post(
            "/companies",
            headers=headers,
            json={"rif": "J-33333333-3", "legal_name": "Empresa de Archivos"},
        )
        assert company.status_code == 201, company.text
        company_id = company.json()["id"]
        uploaded = client.post(
            f"/companies/{company_id}/documents",
            headers=headers,
            files={"file": ("soporte.pdf", b"%PDF-test", "application/pdf")},
            data={"category": "tax", "notes": "Declaración de control"},
        )
        assert uploaded.status_code == 201, uploaded.text
        document = uploaded.json()
        assert document["filename"] == "soporte.pdf"
        assert document["size_bytes"] == len(b"%PDF-test")

        listing = client.get(f"/companies/{company_id}/documents", headers=headers)
        assert listing.status_code == 200
        assert [item["id"] for item in listing.json()] == [document["id"]]
        rejected_type = client.post(
            f"/companies/{company_id}/documents",
            headers=headers,
            files={"file": ("unsafe.html", b"<script>", "text/html")},
        )
        assert rejected_type.status_code == 415

        downloaded = client.get(
            f"/companies/{company_id}/documents/{document['id']}/download",
            headers=headers,
        )
        assert downloaded.status_code == 200
        assert downloaded.content == b"%PDF-test"
        assert downloaded.headers["x-content-type-options"] == "nosniff"
        outsider_license = client.post(
            "/admin/licenses",
            headers=admin_headers,
            json={
                "email": "outsider@example.com",
                "password": "a-secure-outsider-password",
                "role": "accountant",
                "subscribed_at": date.today().isoformat(),
                "expires_at": "2030-12-31",
            },
        )
        assert outsider_license.status_code == 201, outsider_license.text
        outsider_token = client.post(
            "/auth/token",
            data={
                "username": "outsider@example.com",
                "password": "a-secure-outsider-password",
            },
        ).json()["access_token"]
        assert client.get(
            f"/companies/{company_id}/documents",
            headers={"Authorization": f"Bearer {outsider_token}"},
        ).status_code == 404

        archived = client.delete(
            f"/companies/{company_id}/documents/{document['id']}",
            headers=headers,
        )
        assert archived.status_code == 204
        assert client.get(
            f"/companies/{company_id}/documents",
            headers=headers,
        ).json() == []
        assert client.get(
            f"/companies/{company_id}/documents/{document['id']}/download",
            headers=headers,
        ).status_code == 404


def test_personal_finance_and_planner_are_private_to_each_user():
    with TestClient(app) as client:
        admin_token = client.post(
            "/auth/token",
            data={"username": "admin@example.com", "password": "a-secure-test-password"},
        ).json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        for email in ("personal-owner@example.com", "personal-other@example.com"):
            response = client.post(
                "/admin/licenses",
                headers=admin_headers,
                json={
                    "email": email,
                    "password": "a-secure-personal-password",
                    "role": "accountant",
                    "subscribed_at": date.today().isoformat(),
                    "expires_at": "2030-12-31",
                },
            )
            assert response.status_code == 201, response.text

        owner_token = client.post(
            "/auth/token",
            data={
                "username": "personal-owner@example.com",
                "password": "a-secure-personal-password",
            },
        ).json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        other_token = client.post(
            "/auth/token",
            data={
                "username": "personal-other@example.com",
                "password": "a-secure-personal-password",
            },
        ).json()["access_token"]
        other_headers = {"Authorization": f"Bearer {other_token}"}
        today = date.today()
        period = today.strftime("%Y%m")

        income = client.post(
            "/personal/transactions",
            headers=owner_headers,
            json={
                "transaction_date": today.isoformat(),
                "kind": "income",
                "category": "Honorarios",
                "amount": "100.00",
                "currency": "USD",
            },
        )
        assert income.status_code == 201, income.text
        expense = client.post(
            "/personal/transactions",
            headers=owner_headers,
            json={
                "transaction_date": today.isoformat(),
                "kind": "expense",
                "category": "Transporte",
                "amount": "20.00",
                "currency": "USD",
            },
        )
        assert expense.status_code == 201, expense.text
        overview = client.get(
            f"/personal/overview?period={period}",
            headers=owner_headers,
        )
        assert overview.status_code == 200
        assert client.get(
            "/personal/overview?period=202613",
            headers=owner_headers,
        ).status_code == 422
        assert client.get(
            "/personal/overview?period=000000",
            headers=owner_headers,
        ).status_code == 422
        assert overview.json()["totals"]["USD"] == {
            "income": "100.00",
            "expense": "20.00",
            "net": "80.00",
        }
        assert client.get(
            f"/personal/transactions?period={period}",
            headers=other_headers,
        ).json() == []
        assert client.post(
            f"/personal/transactions/{income.json()['id']}/archive",
            headers=other_headers,
        ).status_code == 404

        plan = client.post(
            "/personal/plans",
            headers=owner_headers,
            json={
                "planned_date": today.isoformat(),
                "title": "Revisar presupuesto",
                "notes": "Agenda privada",
            },
        )
        assert plan.status_code == 201, plan.text
        week = client.get(
            f"/personal/plans?start_date={today.isoformat()}&end_date={today.isoformat()}",
            headers=owner_headers,
        )
        assert [item["id"] for item in week.json()] == [plan.json()["id"]]
        completed = client.patch(
            f"/personal/plans/{plan.json()['id']}",
            headers=owner_headers,
            json={"status": "done"},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "done"
        assert client.get(
            f"/personal/plans?start_date={today.isoformat()}&end_date={today.isoformat()}",
            headers=other_headers,
        ).json() == []


def test_company_scoped_purchase_posts_balanced_journal():
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "CONTABILIDAD" in page.text
        token_response = client.post(
            "/auth/token",
            data={"username": "admin@example.com", "password": "a-secure-test-password"},
        )
        assert token_response.status_code == 200
        headers = {"Authorization": f"Bearer {token_response.json()['access_token']}"}
        reviewer_response = client.post(
            "/admin/licenses",
            headers=headers,
            json={
                "email": "reviewer@example.com",
                "password": "another-secure-password",
                "role": "admin",
                "subscribed_at": date.today().isoformat(),
                "expires_at": "2030-12-31",
            },
        )
        assert reviewer_response.status_code == 201
        assert reviewer_response.json()["max_companies"] == 10
        reviewer_login = client.post(
            "/auth/token",
            data={"username": "reviewer@example.com", "password": "another-secure-password"},
        )
        review_headers = {
            "Authorization": f"Bearer {reviewer_login.json()['access_token']}"
        }
        company_response = client.post(
            "/companies",
            headers=review_headers,
            json={
                "rif": "J-12345678-9",
                "legal_name": "Empresa de Prueba",
                "special_taxpayer": True,
            },
        )
        assert company_response.status_code == 201, company_response.text
        company_id = company_response.json()["id"]
        accounts = {}
        for code, name, account_type in [
            ("510101", "Gastos", "expense"),
            ("110401", "IVA crédito", "asset"),
            ("210101", "Proveedores", "liability"),
            ("210302", "IVA retenido por pagar", "liability"),
            ("110102", "Banco", "asset"),
            ("530101", "Gasto IGTF", "expense"),
        ]:
            response = client.post(
                f"/companies/{company_id}/accounts",
                headers=headers,
                json={"code": code, "name": name, "account_type": account_type},
            )
            assert response.status_code == 201
            accounts[code] = response.json()["id"]
            assert response.json()["status"] == "pending"
            if code == "510101":
                assert client.post(
                    f"/companies/{company_id}/accounts/{accounts[code]}/approve",
                    headers=headers,
                ).status_code == 409
            approved = client.post(
                f"/companies/{company_id}/accounts/{accounts[code]}/approve",
                headers=review_headers,
            )
            assert approved.status_code == 200, approved.text

        chart = client.post(
            f"/companies/{company_id}/accounts/templates/venezuela", headers=headers, json={}
        )
        assert chart.status_code == 201, chart.text
        account_rows = client.get(f"/companies/{company_id}/accounts", headers=headers).json()
        equity = next(account for account in account_rows if account["code"] == "31")
        opening = client.post(
            f"/companies/{company_id}/opening-balances",
            headers=headers,
            json={
                "as_of_date": "2026-08-31",
                "lines": [
                    {
                        "account_id": accounts["110102"],
                        "description": "Saldo en bancos",
                        "debit": "200.00",
                        "credit": "0",
                    },
                    {
                        "account_id": equity["id"],
                        "description": "Capital de apertura",
                        "debit": "0",
                        "credit": "200.00",
                    },
                ],
            },
        )
        assert opening.status_code == 201, opening.text
        assert opening.json()["status"] == "pending"
        opening_approval = client.post(
            f"/companies/{company_id}/journals/{opening.json()['journal_id']}/post",
            headers=review_headers,
        )
        assert opening_approval.status_code == 200, opening_approval.text
        assert client.post(
            f"/companies/{company_id}/opening-balances",
            headers=headers,
            json={
                "as_of_date": "2026-08-31",
                "lines": [
                    {"account_id": accounts["110102"], "debit": "1", "credit": "0"},
                    {"account_id": equity["id"], "debit": "0", "credit": "1"},
                ],
            },
        ).status_code == 409

        document_response = client.post(
            f"/companies/{company_id}/tax-documents",
            headers=headers,
            json={
                "direction": "purchase",
                "document_date": "2026-09-01",
                "counterparty_rif": "J-87654321-0",
                "counterparty_name": "Proveedor",
                "invoice_number": "F-001",
                "base_amount": "100.00",
                "vat_withholding_percent": "75",
                "base_account_id": accounts["510101"],
                "vat_account_id": accounts["110401"],
                "counterparty_account_id": accounts["210101"],
                "withholding_account_id": accounts["210302"],
            },
        )
        assert document_response.status_code == 201, document_response.text
        document = document_response.json()
        assert document["vat_amount"] == "16.00"
        assert document["vat_withheld"] == "12.00"
        assert document["total_amount"] == "116.00"
        assert document["journal_id"]
        assert document["status"] == "pending"

        balance_response = client.get(
            f"/companies/{company_id}/reports/trial-balance", headers=headers
        )
        assert balance_response.status_code == 200
        assert sum(Decimal(row["debit"]) for row in balance_response.json()) == Decimal("200.00")
        assert sum(Decimal(row["credit"]) for row in balance_response.json()) == Decimal("200.00")
        approval = client.post(
            f"/companies/{company_id}/tax-documents/{document['id']}/approve",
            headers=review_headers,
        )
        assert approval.status_code == 200, approval.text
        balance_response = client.get(
            f"/companies/{company_id}/reports/trial-balance", headers=headers
        )
        assert sum(Decimal(row["debit"]) for row in balance_response.json()) == Decimal("316.00")
        assert sum(Decimal(row["credit"]) for row in balance_response.json()) == Decimal("316.00")
        statement_response = client.get(
            f"/companies/{company_id}/reports/financial-statements", headers=headers
        )
        assert statement_response.status_code == 200
        assert statement_response.json()["balance_sheet"]["check_difference"] == "0.00"
        tax_summary = client.get(
            f"/companies/{company_id}/reports/tax-summary?period=202609",
            headers=headers,
        )
        assert tax_summary.json()["purchase_vat"] == "16.00"
        contact = client.get(f"/companies/{company_id}/contacts", headers=headers).json()[0]
        contact_balance = client.get(
            f"/companies/{company_id}/contacts/{contact['id']}/balance", headers=headers
        )
        assert contact_balance.json()["balance_ves"] == "-104.00"
        ledger_response = client.get(
            f"/companies/{company_id}/accounts/{accounts['510101']}/ledger",
            headers=headers,
        )
        assert ledger_response.status_code == 200
        assert ledger_response.json()[0]["debit"] == "100.00"

        payment_response = client.post(
            f"/companies/{company_id}/payments",
            headers=headers,
            json={
                "document_id": document["id"],
                "payment_date": "2026-10-02",
                "reference": "TR-001",
                "method": "transfer",
                "amount_ves": "50.00",
                "currency": "USD",
                "foreign_amount": "1.00",
                "exchange_rate": "50.00",
                "igtf_applicable": True,
                "cash_account_id": accounts["110102"],
                "igtf_account_id": accounts["530101"],
            },
        )
        assert payment_response.status_code == 201, payment_response.text
        payment = payment_response.json()
        assert payment["igtf_amount"] == "1.50"
        assert payment["status"] == "pending"
        payment_approval = client.post(
            f"/companies/{company_id}/payments/{payment['id']}/approve",
            headers=review_headers,
        )
        assert payment_approval.status_code == 200, payment_approval.text
        bank_line = client.post(
            f"/companies/{company_id}/bank-statement-lines",
            headers=headers,
            json={
                "bank_account_id": accounts["110102"],
                "statement_date": "2026-10-02",
                "external_reference": "BANK-001",
                "description": "Pago proveedor",
                "amount": "-51.50",
            },
        )
        assert bank_line.status_code == 201, bank_line.text
        reconcile = client.post(
            f"/companies/{company_id}/bank-statement-lines/{bank_line.json()['id']}/match/{payment['id']}",
            headers=review_headers,
        )
        assert reconcile.status_code == 200, reconcile.text
        unmatch = client.post(
            f"/companies/{company_id}/bank-statement-lines/{bank_line.json()['id']}/unmatch",
            headers=review_headers,
        )
        assert unmatch.status_code == 200
        reverse_payment = client.post(
            f"/companies/{company_id}/payments/{payment['id']}/reverse",
            headers=review_headers,
        )
        assert reverse_payment.status_code == 200, reverse_payment.text
        assert client.get(
            f"/companies/{company_id}/contacts/{contact['id']}/balance",
            headers=headers,
        ).json()["balance_ves"] == "-104.00"
        reverse_document = client.post(
            f"/companies/{company_id}/tax-documents/{document['id']}/reverse",
            headers=review_headers,
        )
        assert reverse_document.status_code == 200, reverse_document.text
        assert client.get(
            f"/companies/{company_id}/contacts/{contact['id']}/balance",
            headers=headers,
        ).json()["balance_ves"] == "0.00"
        assert client.put(
            "/admin/settings",
            headers=headers,
            json={"company_limit": 1},
        ).status_code == 200
        first_unlicensed = client.post(
            "/companies",
            headers=headers,
            json={"rif": "J-11111111-1", "legal_name": "Otra Empresa"},
        )
        assert first_unlicensed.status_code == 201
        cap_response = client.post(
            "/companies",
            headers=headers,
            json={"rif": "J-22222222-2", "legal_name": "Excede Límite"},
        )
        assert cap_response.status_code == 409


def test_license_access_company_cap_payments_and_lifecycle():
    with TestClient(app) as client:
        admin_login = client.post(
            "/auth/token",
            data={"username": "admin@example.com", "password": "a-secure-test-password"},
        )
        admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
        created = client.post(
            "/admin/licenses",
            headers=admin_headers,
            json={
                "email": "licensed@example.com",
                "password": "licensed-secure-password",
                "role": "admin",
                "subscribed_at": date.today().isoformat(),
                "expires_at": "2030-12-31",
                "max_companies": 2,
            },
        )
        assert created.status_code == 201, created.text
        license_data = created.json()
        assert license_data["max_companies"] == 2
        assert license_data["status"] == "active"

        account_login = client.post(
            "/auth/token",
            data={"username": "licensed@example.com", "password": "licensed-secure-password"},
        )
        assert account_login.status_code == 200
        account_headers = {"Authorization": f"Bearer {account_login.json()['access_token']}"}
        owned_companies = []
        for rif, name in [
            ("J-00000001-1", "Licenciada uno"),
            ("J-00000002-2", "Licenciada dos"),
        ]:
            response = client.post(
                "/companies",
                headers=account_headers,
                json={"rif": rif, "legal_name": name},
            )
            assert response.status_code == 201, response.text
            owned_companies.append(response.json())
        assert len(client.get("/companies", headers=account_headers).json()) == 2
        over_limit = client.post(
            "/companies",
            headers=account_headers,
            json={"rif": "J-00000003-3", "legal_name": "Exceso"},
        )
        assert over_limit.status_code == 409
        other_access = client.post(
            "/admin/licenses",
            headers=admin_headers,
            json={
                "email": "other-licensed@example.com",
                "password": "other-licensed-password",
                "role": "accountant",
                "subscribed_at": date.today().isoformat(),
                "expires_at": "2030-12-31",
            },
        )
        assert other_access.status_code == 201, other_access.text
        other_login = client.post(
            "/auth/token",
            data={"username": "other-licensed@example.com", "password": "other-licensed-password"},
        )
        other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
        shared = client.post(
            f"/companies/{owned_companies[0]['id']}/members",
            headers=account_headers,
            json={"email": "other-licensed@example.com", "role": "admin"},
        )
        assert shared.status_code == 201, shared.text
        assert shared.json()["role"] == "accountant"
        team = client.get(
            f"/companies/{owned_companies[0]['id']}/members",
            headers=account_headers,
        )
        invited_member = next(
            member for member in team.json()
            if member["email"] == "other-licensed@example.com"
        )
        assert invited_member["role"] == "accountant"
        other_companies = client.get("/companies", headers=other_headers).json()
        assert owned_companies[0]["id"] in {company["id"] for company in other_companies}
        proposed_account = client.post(
            f"/companies/{owned_companies[0]['id']}/accounts",
            headers=account_headers,
            json={
                "code": "7777",
                "name": "Cuenta propuesta",
                "account_type": "expense",
            },
        )
        assert proposed_account.status_code == 201, proposed_account.text
        assert proposed_account.json()["status"] == "pending"
        own_approval = client.post(
            f"/companies/{owned_companies[0]['id']}/accounts/{proposed_account.json()['id']}/approve",
            headers=account_headers,
        )
        assert own_approval.status_code == 409
        peer_approval = client.post(
            f"/companies/{owned_companies[0]['id']}/accounts/{proposed_account.json()['id']}/approve",
            headers=other_headers,
        )
        assert peer_approval.status_code == 200, peer_approval.text
        visible_accounts = client.get(
            f"/companies/{owned_companies[0]['id']}/accounts",
            headers=other_headers,
        ).json()
        assert next(row for row in visible_accounts if row["id"] == proposed_account.json()["id"])["status"] == "approved"
        chart = client.post(
            f"/companies/{owned_companies[0]['id']}/accounts/templates/venezuela",
            headers=account_headers,
            json={},
        )
        assert chart.status_code == 201, chart.text
        approved_accounts = client.get(
            f"/companies/{owned_companies[0]['id']}/accounts",
            headers=account_headers,
        ).json()
        cash = next(row for row in approved_accounts if row["code"] == "1101")
        capital = next(row for row in approved_accounts if row["code"] == "31")
        journal = client.post(
            f"/companies/{owned_companies[0]['id']}/journals",
            headers=account_headers,
            json={
                "entry_date": date.today().isoformat(),
                "description": "Asiento de prueba sujeto a doble revisión",
                "lines": [
                    {"account_id": cash["id"], "debit": "50.00", "credit": "0"},
                    {"account_id": capital["id"], "debit": "0", "credit": "50.00"},
                ],
            },
        )
        assert journal.status_code == 201, journal.text
        submitted = client.post(
            f"/companies/{owned_companies[0]['id']}/journals/{journal.json()['id']}/submit",
            headers=account_headers,
        )
        assert submitted.status_code == 200
        self_approval = client.post(
            f"/companies/{owned_companies[0]['id']}/journals/{journal.json()['id']}/post",
            headers=account_headers,
        )
        assert self_approval.status_code == 409
        peer_approval = client.post(
            f"/companies/{owned_companies[0]['id']}/journals/{journal.json()['id']}/post",
            headers=other_headers,
        )
        assert peer_approval.status_code == 200, peer_approval.text

        edited_company = client.put(
            f"/companies/{owned_companies[0]['id']}",
            headers=account_headers,
            json={"legal_name": "Razón social actualizada"},
        )
        assert edited_company.status_code == 200, edited_company.text
        assert edited_company.json()["legal_name"] == "Razón social actualizada"
        archived_company = client.delete(
            f"/companies/{owned_companies[1]['id']}",
            headers=account_headers,
        )
        assert archived_company.status_code == 200
        assert client.get(
            f"/companies/{owned_companies[1]['id']}/accounts",
            headers=account_headers,
        ).status_code == 404
        restored_company = client.post(
            f"/companies/{owned_companies[1]['id']}/restore",
            headers=account_headers,
        )
        assert restored_company.status_code == 200

        payment = client.post(
            f"/admin/licenses/{license_data['id']}/payments",
            headers=admin_headers,
            json={
                "paid_at": date.today().isoformat(),
                "amount": "125.50",
                "currency": "USD",
                "method": "transfer",
                "reference": "TRX-001",
                "notes": "Pago de prueba",
            },
        )
        assert payment.status_code == 201, payment.text
        assert payment.json()["amount"] == "125.50"
        assert len(client.get(
            f"/admin/licenses/{license_data['id']}/payments",
            headers=admin_headers,
        ).json()) == 1

        stats = client.get("/admin/license-stats", headers=admin_headers)
        assert stats.status_code == 200, stats.text
        stats_row = next(item for item in stats.json()["licenses"] if item["id"] == license_data["id"])
        assert stats_row["company_count"] == 2
        assert stats_row["payment_totals"] == {"USD": "125.50"}

        suspended = client.put(
            f"/admin/licenses/{license_data['id']}/status?status=suspended",
            headers=admin_headers,
        )
        assert suspended.status_code == 200
        assert client.get("/companies", headers=account_headers).status_code == 403
        reactivated = client.put(
            f"/admin/licenses/{license_data['id']}/status?status=active",
            headers=admin_headers,
        )
        assert reactivated.status_code == 200
        updated = client.put(
            f"/admin/licenses/{license_data['id']}",
            headers=admin_headers,
            json={
                "email": "licensed@example.com",
                "role": "accountant",
                "subscribed_at": date.today().isoformat(),
                "expires_at": "2020-01-01",
                "max_companies": 2,
            },
        )
        assert updated.status_code == 422
        expired = client.put(
            f"/admin/licenses/{license_data['id']}",
            headers=admin_headers,
            json={
                "email": "licensed-updated@example.com",
                "password": "reset-licensed-password",
                "role": "accountant",
                "subscribed_at": "2020-01-01",
                "expires_at": "2020-01-02",
                "max_companies": 2,
            },
        )
        assert expired.status_code == 200
        reset_login = client.post(
            "/auth/token",
            data={
                "username": "licensed-updated@example.com",
                "password": "reset-licensed-password",
            },
        )
        assert reset_login.status_code == 200
        old_password = client.post(
            "/auth/token",
            data={"username": "licensed@example.com", "password": "licensed-secure-password"},
        )
        assert old_password.status_code == 401
        assert client.get("/companies", headers=account_headers).status_code == 402
        cancelled = client.delete(
            f"/admin/licenses/{license_data['id']}",
            headers=admin_headers,
        )
        assert cancelled.status_code == 200
        assert client.get("/companies", headers=account_headers).status_code == 403


def test_demo_requires_verified_email_and_manual_review(monkeypatch):
    sent_messages = []
    main_module = importlib.import_module("app.main")
    monkeypatch.setenv("MAIL_HOST", "smtp.example.test")
    monkeypatch.setenv("MAIL_FROM", "noreply@example.test")
    monkeypatch.setattr(
        main_module,
        "send_configured_email",
        lambda recipient, subject, body: sent_messages.append((recipient, subject, body)),
    )
    with TestClient(app) as client:
        submission = client.post(
            "/demo/access",
            json={
                "email": "demo-applicant@example.com",
                "password": "a-secure-demo-password",
                "full_name": "Solicitante de prueba",
                "identity_number": "V12345678",
                "rif": "V-12345678-9",
                "address": "Avenida de prueba, Caracas",
                "profession": "Contadora pública",
                "privacy_notice_accepted": True,
            },
        )
        assert submission.status_code == 202, submission.text
        request_id = submission.json()["id"]
        with SessionLocal() as db:
            consent = db.get(DemoAccessRequest, request_id)
            assert consent.privacy_notice_version == "2026-10-04-v1"
            assert consent.privacy_accepted_at is not None
        code = re.search(r">([0-9]{6})</p>", sent_messages[0][2]).group(1)
        admin_token = client.post(
            "/auth/token",
            data={"username": "admin@example.com", "password": "a-secure-test-password"},
        ).json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        early_decision = client.post(
            f"/admin/demo-requests/{request_id}/decision",
            headers=admin_headers,
            json={"decision": "approve"},
        )
        assert early_decision.status_code == 409
        verified = client.post(
            f"/demo/access/{request_id}/verify",
            json={"code": code},
        )
        assert verified.status_code == 200, verified.text
        approval = client.post(
            f"/admin/demo-requests/{request_id}/decision",
            headers=admin_headers,
            json={"decision": "approve", "notes": "Identidad revisada"},
        )
        assert approval.status_code == 200, approval.text
        assert approval.json()["license_role"] == "accountant"
        assert date.fromisoformat(approval.json()["expires_at"]) == date.today() + timedelta(days=4)
        demo_login = client.post(
            "/auth/token",
            data={
                "username": "demo-applicant@example.com",
                "password": "a-secure-demo-password",
            },
        )
        assert demo_login.status_code == 200
        demo_headers = {"Authorization": f"Bearer {demo_login.json()['access_token']}"}
        me = client.get("/auth/me", headers=demo_headers)
        assert me.status_code == 200
        assert me.json()["company_limit"] == 1


def test_accountant_client_and_fee_records_are_private():
    with TestClient(app) as client:
        admin_token = client.post(
            "/auth/token",
            data={"username": "admin@example.com", "password": "a-secure-test-password"},
        ).json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        accountant_headers = []
        for email in ("portfolio-owner@example.com", "portfolio-other@example.com"):
            created = client.post(
                "/admin/licenses",
                headers=admin_headers,
                json={
                    "email": email,
                    "password": "a-secure-portfolio-password",
                    "role": "accountant",
                    "subscribed_at": date.today().isoformat(),
                    "expires_at": "2030-12-31",
                },
            )
            assert created.status_code == 201, created.text
            token = client.post(
                "/auth/token",
                data={"username": email, "password": "a-secure-portfolio-password"},
            ).json()["access_token"]
            accountant_headers.append({"Authorization": f"Bearer {token}"})

        created_client = client.post(
            "/professional/clients",
            headers=accountant_headers[0],
            json={
                "rif": "J-12345678-1",
                "name": "Cliente de cartera",
                "email": "cliente@example.com",
            },
        )
        assert created_client.status_code == 201, created_client.text
        fee = client.post(
            "/professional/fees",
            headers=accountant_headers[0],
            json={
                "client_id": created_client.json()["id"],
                "reference": "HON-TEST-001",
                "service_description": "Servicios contables",
                "amount": "125.00",
                "currency": "USD",
                "issued_at": date.today().isoformat(),
                "due_date": date.today().isoformat(),
            },
        )
        assert fee.status_code == 201, fee.text
        assert client.get("/professional/clients", headers=accountant_headers[1]).json() == []
        assert client.get("/professional/fees", headers=accountant_headers[1]).json() == []
        payment = client.post(
            f"/professional/fees/{fee.json()['id']}/mark-paid",
            headers=accountant_headers[0],
            json={"paid_at": date.today().isoformat(), "payment_reference": "TRF-001"},
        )
        assert payment.status_code == 200, payment.text
        assert client.get(
            "/professional/fees?status_filter=paid",
            headers=accountant_headers[0],
        ).json()[0]["status"] == "paid"


def test_bank_csv_import_reports_created_duplicates_and_invalid_rows():
    with TestClient(app) as client:
        admin_token = client.post(
            "/auth/token",
            data={"username": "admin@example.com", "password": "a-secure-test-password"},
        ).json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        created_license = client.post(
            "/admin/licenses",
            headers=admin_headers,
            json={
                "email": "bank-import-owner@example.com",
                "password": "a-secure-bank-password",
                "role": "admin",
                "subscribed_at": date.today().isoformat(),
                "expires_at": "2030-12-31",
            },
        )
        assert created_license.status_code == 201, created_license.text
        owner_token = client.post(
            "/auth/token",
            data={
                "username": "bank-import-owner@example.com",
                "password": "a-secure-bank-password",
            },
        ).json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        company_response = client.post(
            "/companies",
            headers=owner_headers,
            json={"rif": "J-55555555-5", "legal_name": "Empresa para extracto"},
        )
        assert company_response.status_code == 201, company_response.text
        company_id = company_response.json()["id"]
        chart = client.post(
            f"/companies/{company_id}/accounts/templates/venezuela",
            headers=owner_headers,
            json={},
        )
        assert chart.status_code == 201, chart.text
        bank_account = next(
            row for row in client.get(
                f"/companies/{company_id}/accounts", headers=owner_headers
            ).json()
            if row["code"] == "1102"
        )
        csv_content = (
            "Fecha;Referencia;Descripción;Monto\n"
            "2026-10-01;BANK-001;Depósito;150,25\n"
            "2026-10-01;BANK-001;Duplicado;150,25\n"
            "fecha-inválida;BANK-002;Fila inválida;10,00\n"
            "2026-10-02;BANK-003;Retiro;-20,00\n"
        )
        imported = client.post(
            f"/companies/{company_id}/bank-statement-lines/import-csv",
            headers=owner_headers,
            data={"bank_account_id": bank_account["id"]},
            files={"file": ("extracto.csv", csv_content.encode("utf-8"), "text/csv")},
        )
        assert imported.status_code == 200, imported.text
        result = imported.json()
        assert result["created"] == 2
        assert result["duplicates"] == 1
        assert len(result["rejected"]) == 1
        assert result["rejected"][0]["row"] == 4
        assert client.post(
            f"/companies/{company_id}/bank-statement-lines/import-csv",
            headers=owner_headers,
            data={"bank_account_id": bank_account["id"]},
            files={"file": ("extracto.csv", csv_content.encode("utf-8"), "text/csv")},
        ).json()["duplicates"] == 3


def test_tax_workpaper_requires_second_member_to_review():
    with TestClient(app) as client:
        admin_token = client.post(
            "/auth/token",
            data={"username": "admin@example.com", "password": "a-secure-test-password"},
        ).json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        owner_login, reviewer_login = [], []
        for email, password, role in (
            ("workpaper-owner@example.com", "a-secure-owner-password", "admin"),
            ("workpaper-reviewer@example.com", "a-secure-reviewer-password", "accountant"),
        ):
            created = client.post(
                "/admin/licenses",
                headers=admin_headers,
                json={
                    "email": email,
                    "password": password,
                    "role": role,
                    "subscribed_at": date.today().isoformat(),
                    "expires_at": "2030-12-31",
                },
            )
            assert created.status_code == 201, created.text
            token = client.post(
                "/auth/token",
                data={"username": email, "password": password},
            ).json()["access_token"]
            (owner_login if role == "admin" else reviewer_login).append(
                {"Authorization": f"Bearer {token}"}
            )
        owner_headers = owner_login[0]
        reviewer_headers = reviewer_login[0]
        company = client.post(
            "/companies",
            headers=owner_headers,
            json={"rif": "J-66666666-6", "legal_name": "Empresa hoja fiscal"},
        )
        assert company.status_code == 201, company.text
        company_id = company.json()["id"]
        membership = client.post(
            f"/companies/{company_id}/members",
            headers=owner_headers,
            json={"email": "workpaper-reviewer@example.com"},
        )
        assert membership.status_code == 201, membership.text
        period = date.today().strftime("%Y%m")
        prepared = client.post(
            f"/companies/{company_id}/tax-workpapers",
            headers=owner_headers,
            params={"period": period},
        )
        assert prepared.status_code == 201, prepared.text
        workpaper_id = prepared.json()["id"]
        self_review = client.post(
            f"/companies/{company_id}/tax-workpapers/{workpaper_id}/review",
            headers=owner_headers,
        )
        assert self_review.status_code == 409
        review = client.post(
            f"/companies/{company_id}/tax-workpapers/{workpaper_id}/review",
            headers=reviewer_headers,
        )
        assert review.status_code == 200, review.text
        assert "no presenta declaraciones" in review.json()["message"]


def test_ocr_result_requires_second_member_and_never_posts_automatically(monkeypatch):
    import httpx

    requests_seen = []

    def azure_handler(request):
        requests_seen.append(request)
        if request.method == "POST":
            return httpx.Response(
                202,
                headers={"Operation-Location": "https://ocr.example.com/operations/1"},
            )
        return httpx.Response(
            200,
            json={
                "status": "succeeded",
                "analyzeResult": {
                    "documents": [
                        {
                            "fields": {
                                "InvoiceId": {"valueString": "INV-001", "confidence": 0.99},
                                "InvoiceTotal": {
                                    "valueCurrency": {"amount": 100, "currencyCode": "VES"},
                                    "confidence": 0.95,
                                },
                            }
                        }
                    ]
                },
            },
        )

    real_http_client = httpx.Client

    def mock_http_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(azure_handler)
        return real_http_client(*args, **kwargs)

    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "https://ocr.example.com")
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "test-ocr-key")
    monkeypatch.setattr(httpx, "Client", mock_http_client)
    with TestClient(app) as client:
        admin_token = client.post(
            "/auth/token",
            data={"username": "admin@example.com", "password": "a-secure-test-password"},
        ).json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        identities = (
            ("ocr-owner@example.com", "a-secure-ocr-owner-password", "admin"),
            ("ocr-reviewer@example.com", "a-secure-ocr-reviewer-password", "accountant"),
        )
        tokens = {}
        for email, password, role in identities:
            created = client.post(
                "/admin/licenses",
                headers=admin_headers,
                json={
                    "email": email,
                    "password": password,
                    "role": role,
                    "subscribed_at": date.today().isoformat(),
                    "expires_at": "2030-12-31",
                },
            )
            assert created.status_code == 201, created.text
            tokens[email] = client.post(
                "/auth/token",
                data={"username": email, "password": password},
            ).json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {tokens['ocr-owner@example.com']}"}
        reviewer_headers = {"Authorization": f"Bearer {tokens['ocr-reviewer@example.com']}"}
        company = client.post(
            "/companies",
            headers=owner_headers,
            json={"rif": "J-77777777-7", "legal_name": "Empresa OCR"},
        )
        assert company.status_code == 201, company.text
        company_id = company.json()["id"]
        invited = client.post(
            f"/companies/{company_id}/members",
            headers=owner_headers,
            json={"email": "ocr-reviewer@example.com"},
        )
        assert invited.status_code == 201, invited.text
        document = client.post(
            f"/companies/{company_id}/documents",
            headers=owner_headers,
            files={"file": ("factura.pdf", b"%PDF-test", "application/pdf")},
            data={"category": "invoice"},
        )
        assert document.status_code == 201, document.text
        extracted = client.post(
            f"/companies/{company_id}/documents/{document.json()['id']}/extract-invoice",
            headers=owner_headers,
        )
        assert extracted.status_code == 202, extracted.text
        extraction_id = extracted.json()["id"]
        assert extracted.json()["status"] == "pending_review"
        assert len(requests_seen) == 2
        assert client.post(
            f"/companies/{company_id}/document-extractions/{extraction_id}/decision",
            headers=owner_headers,
            json={"decision": "approve"},
        ).status_code == 409
        approved = client.post(
            f"/companies/{company_id}/document-extractions/{extraction_id}/decision",
            headers=reviewer_headers,
            json={"decision": "approve"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "approved"
        assert client.get(f"/companies/{company_id}/journals", headers=owner_headers).status_code == 200
        assert client.get(f"/companies/{company_id}/journals", headers=owner_headers).json() == []


def test_commercial_invoice_logo_and_email_require_approval(monkeypatch):
    sent_messages = []
    main_module = importlib.import_module("app.main")
    monkeypatch.setattr(
        main_module,
        "send_configured_email",
        lambda recipient, subject, body: sent_messages.append((recipient, subject, body)),
    )
    with TestClient(app) as client:
        admin_token = client.post(
            "/auth/token",
            data={"username": "admin@example.com", "password": "a-secure-test-password"},
        ).json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        credentials = (
            ("invoice-owner@example.com", "a-secure-invoice-owner-password", "admin"),
            ("invoice-reviewer@example.com", "a-secure-invoice-reviewer-password", "accountant"),
        )
        tokens = {}
        for email, password, role in credentials:
            created = client.post(
                "/admin/licenses",
                headers=admin_headers,
                json={
                    "email": email,
                    "password": password,
                    "role": role,
                    "subscribed_at": date.today().isoformat(),
                    "expires_at": "2030-12-31",
                },
            )
            assert created.status_code == 201, created.text
            tokens[email] = client.post(
                "/auth/token",
                data={"username": email, "password": password},
            ).json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {tokens['invoice-owner@example.com']}"}
        reviewer_headers = {"Authorization": f"Bearer {tokens['invoice-reviewer@example.com']}"}
        company = client.post(
            "/companies",
            headers=owner_headers,
            json={"rif": "J-88888888-8", "legal_name": "Empresa con marca"},
        )
        assert company.status_code == 201, company.text
        company_id = company.json()["id"]
        logo = b"\x89PNG\r\n\x1a\n" + b"test-logo-data"
        uploaded_logo = client.post(
            f"/companies/{company_id}/branding/logo",
            headers=owner_headers,
            files={"file": ("logo.png", logo, "image/png")},
        )
        assert uploaded_logo.status_code == 200, uploaded_logo.text
        branding = client.put(
            f"/companies/{company_id}/branding",
            headers=owner_headers,
            json={"contact_email": "billing@example.com", "contact_phone": "+58 212 555 0100"},
        )
        assert branding.status_code == 200, branding.text
        member = client.post(
            f"/companies/{company_id}/members",
            headers=owner_headers,
            json={"email": "invoice-reviewer@example.com"},
        )
        assert member.status_code == 201, member.text
        chart = client.post(
            f"/companies/{company_id}/accounts/templates/venezuela",
            headers=owner_headers,
            json={},
        )
        assert chart.status_code == 201, chart.text
        accounts = client.get(
            f"/companies/{company_id}/accounts", headers=owner_headers
        ).json()
        by_code = {account["code"]: account["id"] for account in accounts}
        contact = client.post(
            f"/companies/{company_id}/contacts",
            headers=owner_headers,
            json={
                "rif": "J-99999999-9",
                "name": "Cliente con correo",
                "kind": "customer",
                "email": "customer@example.com",
            },
        )
        assert contact.status_code == 201, contact.text
        document = client.post(
            f"/companies/{company_id}/tax-documents",
            headers=owner_headers,
            json={
                "direction": "sale",
                "document_date": date.today().isoformat(),
                "counterparty_rif": "J-99999999-9",
                "counterparty_name": "Cliente con correo",
                "invoice_number": "VENTA-0001",
                "commercial_description": "Servicios administrativos",
                "base_amount": "250.00",
                "base_account_id": by_code["41"],
                "vat_account_id": by_code["210201"],
                "counterparty_account_id": by_code["1201"],
                "contact_id": contact.json()["id"],
            },
        )
        assert document.status_code == 201, document.text
        document_id = document.json()["id"]
        not_yet_approved = client.post(
            f"/companies/{company_id}/tax-documents/{document_id}/send-invoice",
            headers=owner_headers,
        )
        assert not_yet_approved.status_code == 404
        assert sent_messages == []
        preview = client.get(
            f"/companies/{company_id}/tax-documents/{document_id}/commercial-invoice.html",
            headers=owner_headers,
        )
        assert preview.status_code == 200
        assert "data:image/png;base64," in preview.text
        assert "Documento comercial interno." in preview.text
        approval = client.post(
            f"/companies/{company_id}/tax-documents/{document_id}/approve",
            headers=reviewer_headers,
        )
        assert approval.status_code == 200, approval.text
        sent = client.post(
            f"/companies/{company_id}/tax-documents/{document_id}/send-invoice",
            headers=owner_headers,
        )
        assert sent.status_code == 200, sent.text
        assert sent.json()["recipient"] == "customer@example.com"
        assert sent_messages[0][0] == "customer@example.com"
        assert "Empresa con marca" in sent_messages[0][2]
        assert "VENTA-0001" in sent_messages[0][2]


def test_smtp_helper_requires_tls_and_authenticates_before_sending(monkeypatch):
    main_module = importlib.import_module("app.main")
    events = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            events.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            events.append(("close",))

        def ehlo(self):
            events.append(("ehlo",))

        def starttls(self, context):
            events.append(("starttls", context is not None))

        def login(self, username, password):
            events.append(("login", username, password))

        def send_message(self, message):
            events.append(("send", message["To"], message["From"]))

    monkeypatch.setattr(main_module.smtplib, "SMTP", FakeSMTP)
    for name, value in {
        "MAIL_HOST": "smtp.example.com",
        "MAIL_PORT": "587",
        "MAIL_USERNAME": "sender@example.com",
        "MAIL_PASSWORD": "application-password",
        "MAIL_FROM": "sender@example.com",
        "MAIL_STARTTLS": "true",
    }.items():
        monkeypatch.setenv(name, value)
    main_module.send_configured_email("recipient@example.com", "Test", "<p>Test</p>")
    assert events.index(("starttls", True)) < events.index(
        ("login", "sender@example.com", "application-password")
    )
    assert ("send", "recipient@example.com", "sender@example.com") in events

    monkeypatch.setenv("MAIL_STARTTLS", "false")
    with pytest.raises(RuntimeError, match="STARTTLS"):
        main_module.send_configured_email("recipient@example.com", "Test", "<p>Test</p>")


def test_admin_can_check_integrations_and_send_test_email(monkeypatch):
    sent_to = []
    main_module = importlib.import_module("app.main")
    monkeypatch.setenv("MAIL_HOST", "smtp.example.com")
    monkeypatch.setenv("MAIL_FROM", "sender@example.com")
    monkeypatch.setenv("MAIL_USERNAME", "sender@example.com")
    monkeypatch.setenv("MAIL_PASSWORD", "test-app-password")
    monkeypatch.setenv("MAIL_STARTTLS", "true")
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "https://ocr.example.com")
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "test-ocr-key")
    monkeypatch.setattr(
        main_module,
        "send_configured_email",
        lambda recipient, subject, body: sent_to.append(recipient),
    )
    with TestClient(app) as client:
        token = client.post(
            "/auth/token",
            data={"username": "admin@example.com", "password": "a-secure-test-password"},
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        status = client.get("/admin/integrations/status", headers=headers)
        assert status.status_code == 200
        integrations = status.json()
        assert integrations["email"]["configured"] is True
        assert integrations["azure_ocr"]["configured"] is True
        assert integrations["banking"]["direct_bank_sync_configured"] is False
        assert integrations["seniat"]["official_submission_configured"] is False
        assert "test-app-password" not in status.text
        email_test = client.post("/admin/integrations/email-test", headers=headers)
        assert email_test.status_code == 200, email_test.text
        assert email_test.json()["recipient"] == "admin@example.com"
        assert sent_to == ["admin@example.com"]

        license_response = client.post(
            "/admin/licenses",
            headers=headers,
            json={
                "email": "non-admin-integration@example.com",
                "password": "a-secure-integration-password",
                "role": "accountant",
                "subscribed_at": date.today().isoformat(),
                "expires_at": "2030-12-31",
            },
        )
        assert license_response.status_code == 201, license_response.text
        member_token = client.post(
            "/auth/token",
            data={
                "username": "non-admin-integration@example.com",
                "password": "a-secure-integration-password",
            },
        ).json()["access_token"]
        member_headers = {"Authorization": f"Bearer {member_token}"}
        assert client.get("/admin/integrations/status", headers=member_headers).status_code == 403
        assert client.post("/admin/integrations/email-test", headers=member_headers).status_code == 403


def test_compose_requires_database_secret_and_binds_host_ports_to_localhost():
    compose_file = Path(__file__).resolve().parents[1] / "docker-compose.yml"
    compose = compose_file.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?" in compose
    assert "POSTGRES_PASSWORD: contabilidad" not in compose
    assert "127.0.0.1:5433:5432" in compose
    assert '${API_BIND_ADDRESS:-127.0.0.1}:8000:8000' in compose
    assert "contabilidad:contabilidad@" not in compose


def test_readiness_endpoint_checks_database():
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        ready = client.get("/ready")
        assert ready.status_code == 200, ready.text
        assert ready.json()["status"] == "ready"


def test_password_reset_is_single_use_revokes_existing_tokens_and_hides_accounts(monkeypatch):
    sent_messages = []
    main_module = importlib.import_module("app.main")
    monkeypatch.setenv("MAIL_HOST", "smtp.example.test")
    monkeypatch.setenv("MAIL_FROM", "noreply@example.test")
    monkeypatch.setattr(
        main_module,
        "send_configured_email",
        lambda recipient, subject, body: sent_messages.append((recipient, subject, body)),
    )
    with TestClient(app) as client:
        admin_token = client.post(
            "/auth/token",
            data={"username": "admin@example.com", "password": "a-secure-test-password"},
        ).json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        licensed = client.post(
            "/admin/licenses",
            headers=admin_headers,
            json={
                "email": "reset-owner@example.com",
                "password": "a-secure-original-password",
                "role": "accountant",
                "subscribed_at": date.today().isoformat(),
                "expires_at": "2030-12-31",
            },
        )
        assert licensed.status_code == 201, licensed.text
        old_token = client.post(
            "/auth/token",
            data={
                "username": "reset-owner@example.com",
                "password": "a-secure-original-password",
            },
        ).json()["access_token"]

        request = client.post(
            "/auth/password-reset/request",
            json={"email": "reset-owner@example.com"},
        )
        assert request.status_code == 202, request.text
        assert request.json()["message"] == "Si el correo corresponde a una cuenta, recibirá instrucciones de recuperación."
        token = re.search(
            r"font-weight:bold;word-break:break-all'>([^<]+)</p>",
            sent_messages[-1][2],
        ).group(1)
        old_code_rate_limited = client.post(
            "/auth/password-reset/request",
            json={"email": "reset-owner@example.com"},
        )
        assert old_code_rate_limited.status_code == 202
        assert len(sent_messages) == 1

        unknown = client.post(
            "/auth/password-reset/request",
            json={"email": "unknown-reset-owner@example.com"},
        )
        assert unknown.status_code == request.status_code
        assert unknown.json() == request.json()
        assert len(sent_messages) == 1
        assert sent_messages[-1][0] == "reset-owner@example.com"

        completed = client.post(
            "/auth/password-reset/confirm",
            json={
                "email": "reset-owner@example.com",
                "token": token,
                "new_password": "a-secure-replacement-password",
            },
        )
        assert completed.status_code == 200, completed.text
        assert client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {old_token}"},
        ).status_code == 401
        assert client.post(
            "/auth/password-reset/confirm",
            json={
                "email": "reset-owner@example.com",
                "token": token,
                "new_password": "another-secure-password",
            },
        ).status_code == 400
        assert client.post(
            "/auth/token",
            data={
                "username": "reset-owner@example.com",
                "password": "a-secure-original-password",
            },
        ).status_code == 401
        assert client.post(
            "/auth/token",
            data={
                "username": "reset-owner@example.com",
                "password": "a-secure-replacement-password",
            },
        ).status_code == 200


def test_demo_signup_requires_privacy_notice_acceptance(monkeypatch):
    monkeypatch.setenv("MAIL_HOST", "smtp.example.test")
    monkeypatch.setenv("MAIL_FROM", "noreply@example.test")
    with TestClient(app) as client:
        response = client.post(
            "/demo/access",
            json={
                "email": "privacy-demo@example.com",
                "password": "a-secure-privacy-password",
                "full_name": "Usuario de privacidad",
                "identity_number": "V12345679",
                "rif": "V-12345679-9",
                "address": "Dirección de prueba número 1",
                "profession": "Contador",
                "privacy_notice_accepted": False,
            },
        )
        assert response.status_code == 422
        with SessionLocal() as db:
            assert db.query(DemoAccessRequest).filter_by(email="privacy-demo@example.com").count() == 0


def test_retention_job_deletes_old_demo_and_reset_records_only():
    with TestClient(app):
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=100)
        recent = now - timedelta(days=3)
        with SessionLocal.begin() as db:
            db.add(
                DemoAccessRequest(
                    email="old-retention@example.com",
                    password_hash="hashed-test-password",
                    full_name="Old applicant",
                    identity_number="V12345001",
                    rif="V-12345001-1",
                    address="Old application address",
                    profession="Accountant",
                    privacy_notice_version="2026-10-04-v1",
                    privacy_accepted_at=old,
                    status="rejected",
                    created_at=old,
                )
            )
            db.add(
                DemoAccessRequest(
                    email="recent-retention@example.com",
                    password_hash="hashed-test-password",
                    full_name="Recent applicant",
                    identity_number="V12345002",
                    rif="V-12345002-2",
                    address="Recent application address",
                    profession="Accountant",
                    privacy_notice_version="2026-10-04-v1",
                    privacy_accepted_at=recent,
                    status="pending_email",
                    created_at=recent,
                )
            )
            db.add(
                PasswordResetChallenge(
                    email_digest="o" * 64,
                    created_at=old,
                )
            )
            db.add(
                PasswordResetChallenge(
                    email_digest="r" * 64,
                    created_at=recent,
                )
            )
        purge_module = importlib.import_module("scripts.purge_expired_personal_data")
        counts = purge_module.purge_expired_personal_data(dry_run=True)
        assert counts == {"demo_requests": 1, "password_reset_records": 1}
        assert purge_module.purge_expired_personal_data(dry_run=False) == counts
        with SessionLocal() as db:
            assert db.query(DemoAccessRequest).filter_by(email="old-retention@example.com").count() == 0
            assert db.query(DemoAccessRequest).filter_by(email="recent-retention@example.com").count() == 1
            assert db.query(PasswordResetChallenge).filter_by(email_digest="o" * 64).count() == 0
            assert db.query(PasswordResetChallenge).filter_by(email_digest="r" * 64).count() == 1
