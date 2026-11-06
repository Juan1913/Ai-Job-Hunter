"""Harness: ejecuta un agente con presupuesto de intentos, validación y traza.
Cada intento recibe los problemas del anterior para corregirse."""
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class RunResult(Generic[T]):
    value: T | None
    ok: bool
    trace: list[str] = field(default_factory=list)


def run_with_guard(generate: Callable[[list[str]], T], validate: Callable[[T], list[str]], max_attempts: int = 3) -> RunResult[T]:
    trace: list[str] = []
    issues: list[str] = []
    best: T | None = None
    best_issues: list[str] | None = None
    for i in range(1, max_attempts + 1):
        try:
            candidate = generate(issues)
        except Exception as e:  # noqa: BLE001
            trace.append(f"intento {i}: error de generación: {e}")
            issues = [f"el intento anterior falló: {e}"]
            continue
        issues = validate(candidate)
        trace.append(f"intento {i}: {len(issues)} problemas" + (f" -> {issues}" if issues else ""))
        if best_issues is None or len(issues) < len(best_issues):
            best, best_issues = candidate, issues
        if not issues:
            return RunResult(candidate, True, trace)
    return RunResult(best, False, trace)
