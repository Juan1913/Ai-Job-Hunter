"""Contabilidad de uso del LLM (tabla LLMUsage) y presupuesto diario en modo api.

Topes por usuario y globales (tokens, llamadas, coste estimado), día UTC. Al agotarse, `state()` lo indica y el
llamador cae a la ruta determinista con un aviso claro; nunca es un error 500."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from app.ai.models import LLMUsage
from app.config import settings
from app.db import engine

log = logging.getLogger(__name__)


def today() -> date:
    return datetime.now(timezone.utc).date()


def cost_usd(price_in: float, price_out: float, tokens_in: int, tokens_out: int) -> float:
    return round((tokens_in * price_in + tokens_out * price_out) / 1_000_000, 6)


def record(user_id: int | None, model: str, provider: str, tokens_in: int, tokens_out: int, cost: float = 0.0) -> None:
    """Suma una llamada al uso del día. Nunca lanza: la contabilidad no debe romper una generación ya pagada."""
    uid, day = user_id or 0, today()
    for _ in range(3):
        try:
            with Session(engine) as s:
                row = s.exec(select(LLMUsage).where(LLMUsage.user_id == uid, LLMUsage.day == day, LLMUsage.model == model)).first()
                if row is None:
                    row = LLMUsage(user_id=uid, day=day, model=model, provider=provider)
                row.calls += 1
                row.tokens_in += int(tokens_in)
                row.tokens_out += int(tokens_out)
                row.cost_usd = round(row.cost_usd + cost, 6)
                row.updated_at = datetime.now(timezone.utc)
                s.add(row)
                s.commit()
            return
        except IntegrityError:
            continue                       # otra hebra creó la fila a la vez: reintenta como actualización
        except Exception as e:  # noqa: BLE001
            log.warning("no se pudo registrar el uso del LLM: %s", e)
            return


def _totals(s: Session, user_id: int | None, day: date) -> dict:
    q = select(func.coalesce(func.sum(LLMUsage.calls), 0), func.coalesce(func.sum(LLMUsage.tokens_in), 0),
               func.coalesce(func.sum(LLMUsage.tokens_out), 0), func.coalesce(func.sum(LLMUsage.cost_usd), 0.0)).where(LLMUsage.day == day)
    if user_id is not None:
        q = q.where(LLMUsage.user_id == user_id)
    calls, tin, tout, cost = s.exec(q).one()
    return {"calls": int(calls), "tokens_in": int(tin), "tokens_out": int(tout), "tokens": int(tin) + int(tout), "cost_usd": round(float(cost), 6)}


@dataclass
class BudgetState:
    exhausted: bool = False
    scope: str | None = None            # user | global
    metric: str | None = None           # tokens | calls | cost
    user: dict = field(default_factory=dict)
    globally: dict = field(default_factory=dict)
    limits: dict = field(default_factory=dict)

    @property
    def message(self) -> str | None:
        if not self.exhausted:
            return None
        who = "tu presupuesto diario de IA" if self.scope == "user" else "el presupuesto diario de IA del servicio"
        return f"Se agotó {who}. Seguirás con la ruta sin IA (reglas y extracción literal) hasta mañana (UTC)."


def limits() -> dict:
    return {"user_tokens_per_day": settings.ai_budget_user_tokens_per_day, "user_calls_per_day": settings.ai_budget_user_calls_per_day,
            "global_tokens_per_day": settings.ai_budget_global_tokens_per_day, "global_calls_per_day": settings.ai_budget_global_calls_per_day,
            "global_cost_usd_per_day": settings.ai_budget_global_cost_per_day}


def _over(used: int | float, limit: int | float) -> bool:
    return bool(limit) and used >= limit


def state(user_id: int | None = None) -> BudgetState:
    """Estado del presupuesto del día. `user_id` None = solo el global."""
    day = today()
    with Session(engine) as s:
        g = _totals(s, None, day)
        u = _totals(s, user_id, day) if user_id is not None else {}
    st = BudgetState(user=u, globally=g, limits=limits())
    for scope, tot, checks in (("global", g, (("tokens", settings.ai_budget_global_tokens_per_day), ("calls", settings.ai_budget_global_calls_per_day),
                                              ("cost", settings.ai_budget_global_cost_per_day))),
                               ("user", u, (("tokens", settings.ai_budget_user_tokens_per_day), ("calls", settings.ai_budget_user_calls_per_day)))):
        if not tot:
            continue
        for metric, lim in checks:
            used = tot["cost_usd"] if metric == "cost" else tot[metric]
            if _over(used, lim):
                st.exhausted, st.scope, st.metric = True, scope, metric
                return st
    return st
