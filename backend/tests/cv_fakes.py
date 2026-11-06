"""Fakes propios de los tests de perfil/CV (no tocan tests/fakes.py)."""
from app.llm.client import LLMError


class ScriptedLLM:
    """LLM guionado: `script` = lista de (fragmento_del_system, respuesta). La respuesta puede ser dict, callable(user)->dict,
    Exception (se lanza) o lista (se consume una por llamada, la última se repite)."""

    def __init__(self, script=None, raw=None):
        self.script = list(script or [])
        self.calls: list[tuple[str, str]] = []
        self.raw = list(raw or [])          # respuestas crudas para `complete` (JSON roto que hay que reparar)

    def _answer(self, system, user):
        for needle, resp in self.script:
            if needle in system:
                if isinstance(resp, list):
                    resp = resp.pop(0) if len(resp) > 1 else resp[0]
                if callable(resp):
                    resp = resp(user)
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise LLMError("JSON inválido del modelo: sin guion")

    def complete_json(self, system, user):
        self.calls.append((system, user))
        return self._answer(system, user)

    def complete(self, system, user, temperature=0.1):
        self.calls.append((system, user))
        if self.raw:
            return self.raw.pop(0) if len(self.raw) > 1 else self.raw[0]
        return "{}"

class BrokenJSONLLM(ScriptedLLM):
    """complete_json siempre falla por JSON inválido; `complete` entrega texto crudo reparable."""

    def complete_json(self, system, user):
        self.calls.append((system, user))
        raise LLMError("JSON inválido del modelo: Expecting ','")

class DownLLM:
    def __init__(self):
        self.calls = 0

    def complete_json(self, system, user):
        self.calls += 1
        raise LLMError("LLM no disponible: connection refused")

    def complete(self, system, user, temperature=0.1):
        self.calls += 1
        raise LLMError("LLM no disponible: connection refused")
