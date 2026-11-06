"""Conversión APROXIMADA de monedas a USD para comparar órdenes de magnitud de salarios (no sirve para pagos ni contratos).
Valores aproximados de referencia; si una moneda no está, el salario se trata como «sin dato comparable»."""

USD_PER_UNIT: dict[str, float] = {
    "USD": 1.0, "EUR": 1.08, "GBP": 1.27, "CAD": 0.73, "BRL": 0.18, "MXN": 0.055, "COP": 0.00025, "ARS": 0.0010, "CLP": 0.0011,
    "PEN": 0.27, "UYU": 0.025, "CRC": 0.0019, "DOP": 0.017, "GTQ": 0.13, "PYG": 0.00013, "BOB": 0.145, "HNL": 0.040, "NIO": 0.027, "VES": 0.027,
}

# Periodo -> factor para llevarlo a un mes (40 h/semana, 52 semanas, 260 días hábiles)
PERIODS_PER_MONTH: dict[str, float] = {"hour": 173.33, "day": 21.67, "week": 4.333, "month": 1.0, "year": 1 / 12}


def monthly_usd(amount: float, currency: str | None, period: str | None) -> float | None:
    rate = USD_PER_UNIT.get((currency or "").upper())
    per = PERIODS_PER_MONTH.get((period or "").lower())
    if rate is None or per is None:
        return None
    return amount * per * rate
