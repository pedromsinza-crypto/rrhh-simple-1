import re
from decimal import Decimal, ROUND_HALF_UP


CENT = Decimal("0.01")
RIF_PATTERN = re.compile(r"^([JGVE])[- ]?(\d{8})[- ]?(\d)$")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def normalize_rif(value: str) -> str:
    cleaned = value.strip().upper().replace(" ", "")
    match = RIF_PATTERN.fullmatch(cleaned)
    if not match:
        raise ValueError("RIF inválido: use una letra J, G, V o E y nueve dígitos.")
    letter, digits, verifier = match.groups()
    return f"{letter}-{digits}-{verifier}"


def calculate_vat(base: Decimal, rate_percent: Decimal, exempt: bool = False) -> Decimal:
    if base < 0 or rate_percent < 0:
        raise ValueError("La base y la alícuota no pueden ser negativas.")
    return Decimal("0.00") if exempt else money(base * rate_percent / Decimal("100"))


def calculate_igtf(
    amount: Decimal,
    rate_percent: Decimal,
    applicable: bool,
    exchange_rate: Decimal | None = None,
) -> Decimal:
    if amount < 0 or rate_percent < 0:
        raise ValueError("El monto y la alícuota no pueden ser negativos.")
    if not applicable:
        return Decimal("0.00")
    if exchange_rate is None or exchange_rate <= 0:
        raise ValueError("Se requiere una tasa de cambio positiva para calcular IGTF.")
    return money(amount * exchange_rate * rate_percent / Decimal("100"))
