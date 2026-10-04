from decimal import Decimal

import pytest

from app.services import calculate_igtf, calculate_vat, money, normalize_rif


def test_normalizes_venezuelan_rif_format():
    assert normalize_rif("j123456789") == "J-12345678-9"


def test_rejects_invalid_rif_shape():
    with pytest.raises(ValueError):
        normalize_rif("A-12345678-9")


def test_vat_rounds_half_up_to_cents():
    assert calculate_vat(Decimal("10.03"), Decimal("16")) == Decimal("1.60")
    assert money(Decimal("1.005")) == Decimal("1.01")


def test_exempt_vat_is_zero():
    assert calculate_vat(Decimal("100"), Decimal("16"), exempt=True) == Decimal("0.00")


def test_igtf_uses_exchange_rate_and_is_opt_in():
    assert calculate_igtf(
        Decimal("100"), Decimal("3"), True, Decimal("36.50")
    ) == Decimal("109.50")
    assert calculate_igtf(Decimal("100"), Decimal("3"), False) == Decimal("0.00")


def test_igtf_requires_positive_exchange_rate_when_applicable():
    with pytest.raises(ValueError):
        calculate_igtf(Decimal("100"), Decimal("3"), True)
