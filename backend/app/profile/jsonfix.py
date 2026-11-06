"""Reparación de JSON 'casi válido' típico de modelos pequeños (cercas ```, comas finales, comillas simples,
claves sin comillas, None/True, salida truncada)."""
import json
import re


def _balance(s: str) -> str:
    """Cierra cadenas/llaves/corchetes abiertos (salida cortada por límite de tokens)."""
    stack: list[str] = []
    in_str = esc = False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    if in_str:
        s += '"'
    s = re.sub(r",\s*$", "", s.rstrip())
    return s + "".join(reversed(stack))

def loads_loose(raw: str) -> dict | list | None:
    if not isinstance(raw, str):
        return None
    t = raw.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I)
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if not starts:
        return None
    t = t[min(starts):]
    t = t.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    cands = [t]
    end = max(t.rfind("}"), t.rfind("]"))
    if end > 0:
        cands.append(t[: end + 1])
    for c in list(cands):
        c2 = re.sub(r",\s*([}\]])", r"\1", c)
        c2 = re.sub(r"([{,]\s*)([A-Za-z_]\w*)\s*:", r'\1"\2":', c2)
        c2 = re.sub(r"\bNone\b", "null", c2)
        c2 = re.sub(r"\bTrue\b", "true", c2)
        c2 = re.sub(r"\bFalse\b", "false", c2)
        cands.append(c2)
        if "'" in c2 and '"' not in c2:
            cands.append(c2.replace("'", '"'))
        cands.append(_balance(c2))
        cands.append(_balance(re.sub(r',\s*"[^"]*"?\s*:?\s*[^,{}\[\]]*$', "", c2)))   # corta el último par incompleto
    for c in cands:
        try:
            v = json.loads(c)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(v, (dict, list)):
            return v
    return None
