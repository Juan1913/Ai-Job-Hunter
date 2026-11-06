"""Reparación tolerante de JSON producido por modelos pequeños.

Arregla lo habitual: bloques ```json, texto antes/después, comas finales, comillas
tipográficas o simples, claves sin comillas, True/False/None de Python, saltos de
línea crudos dentro de cadenas y respuestas truncadas (llaves sin cerrar).
"""
from __future__ import annotations

import json
import re

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_SMART = str.maketrans({"“": '"', "”": '"', "„": '"', "«": '"', "»": '"', "‘": "'", "’": "'"})


def _extract_candidate(raw: str) -> str | None:
    """Primer objeto/array balanceado (consciente de cadenas); si se trunca, devuelve lo que haya."""
    m = _FENCE.search(raw)
    if m and m.group(1).strip()[:1] in "{[":
        raw = m.group(1)
    start = next((i for i, c in enumerate(raw) if c in "{["), None)
    if start is None:
        return None
    stack: list[str] = []
    in_str: str | None = None
    esc = False
    for i in range(start, len(raw)):
        c = raw[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == in_str:
                in_str = None
            continue
        if c in "\"'" and (c == '"' or _quote_opens(raw, i)):
            in_str = c
        elif c in "{[":
            stack.append("}" if c == "{" else "]")
        elif c in "}]":
            if stack:
                stack.pop()
            if not stack:
                return raw[start:i + 1]
    # truncado: cerramos cadena y estructuras abiertas
    tail = raw[start:]
    if in_str:
        tail += in_str
    return tail + "".join(reversed(stack))


def _quote_opens(raw: str, i: int) -> bool:
    """Una comilla simple abre cadena si va tras { [ , : (no es un apóstrofo dentro de una palabra)."""
    j = i - 1
    while j >= 0 and raw[j].isspace():
        j -= 1
    return j < 0 or raw[j] in "{[,:"


def _fix(text: str) -> str:
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            q = c
            j = i + 1
            buf: list[str] = []
            while j < n:
                d = text[j]
                if d == "\\" and j + 1 < n:
                    nxt = text[j + 1]
                    buf.append("'" if (nxt == "'" and q == "'") else d + nxt)
                    j += 2
                    continue
                if d == q:
                    break
                if d == "\n":
                    buf.append("\\n")
                elif d == "\r":
                    pass
                elif d == "\t":
                    buf.append("\\t")
                elif d == '"' and q == "'":
                    buf.append('\\"')
                else:
                    buf.append(d)
                j += 1
            out.append('"' + "".join(buf) + '"')
            i = j + 1
            continue
        if c == "/" and text[i:i + 2] == "//":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c.isalpha() or c == "_":
            m = re.match(r"[A-Za-z_][\w\-]*", text[i:])
            word = m.group(0)
            rest = text[i + len(word):].lstrip()
            if rest.startswith(":"):
                out.append(f'"{word}"')
            else:
                out.append({"True": "true", "False": "false", "None": "null", "NaN": "null"}.get(word, word))
            i += len(word)
            continue
        out.append(c)
        i += 1
    s = "".join(out)
    s = re.sub(r",\s*([}\]])", r"\1", s)                 # comas finales
    s = re.sub(r"([}\]\"\d])\s*\n\s*(\")", r"\1,\n\2", s)   # falta una coma entre elementos
    return s


def repair_json(raw: str):
    """Devuelve el valor JSON (dict/list) o lanza ValueError si es irrecuperable."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("respuesta vacía")
    raw = raw.strip().translate(_SMART)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    cand = _extract_candidate(raw)
    if cand is None:
        raise ValueError("la respuesta no contiene JSON")
    last: Exception | None = None
    for text in (cand, _fix(cand)):
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            last = e
    raise ValueError(f"JSON irreparable: {last}")
