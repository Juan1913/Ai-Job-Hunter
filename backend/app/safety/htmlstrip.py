"""Texto oculto en HTML: se elimina ANTES de convertir a texto (después ya no se distingue lo visible de lo oculto).

Una oferta puede esconder instrucciones para el LLM que el humano nunca ve (display:none, fuente de tamaño 0, texto del
mismo color que el fondo, bloques aria-hidden, comentarios HTML...). `strip_hidden_html` devuelve el HTML solo con lo que
un navegador mostraría. Donde quitó algo relevante deja un marcador `⟦jh:hidden:<severidad>⟧` que `integrity.inspect`
convierte en hallazgo y retira del texto (así la señal viaja por `clean_html -> Job.description` sin cambiar ninguna API)."""
from __future__ import annotations

import re
from html.parser import HTMLParser

HIDDEN_MARK = re.compile(r"⟦jh:hidden:(high|medium)⟧")
_VOID = {"br", "img", "hr", "input", "meta", "link", "source", "wbr", "area", "base", "col", "embed", "param", "track"}
_DROP_TAGS = {"script", "style", "template", "noscript", "iframe", "object", "head", "title", "textarea"}   # su contenido nunca se ve como texto de la oferta
MIN_LONG_ARIA = 80          # aria-hidden en bloques cortos (iconos, etiquetas decorativas) es normal; en bloques largos no
MIN_REPORT = 20             # texto oculto más corto que esto se descarta sin alarma

_COLORS = {"white": (255, 255, 255), "black": (0, 0, 0), "red": (255, 0, 0), "yellow": (255, 255, 0), "silver": (192, 192, 192),
           "gray": (128, 128, 128), "grey": (128, 128, 128), "blue": (0, 0, 255), "green": (0, 128, 0), "whitesmoke": (245, 245, 245),
           "ivory": (255, 255, 240), "snow": (255, 250, 250), "lightgray": (211, 211, 211), "lightgrey": (211, 211, 211)}


def _rgb(v: str | None) -> tuple[int, int, int] | None:
    v = (v or "").strip().lower().replace("!important", "").strip()
    if v in _COLORS:
        return _COLORS[v]
    m = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", v)
    if m:
        h = m.group(1)
        h = "".join(c * 2 for c in h) if len(h) == 3 else h
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    m = re.fullmatch(r"rgba?\(\s*(\d+)[ ,]+(\d+)[ ,]+(\d+)", v.split("/")[0].rstrip(") ") if v.startswith("rgb") else "")
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def _near(a: tuple[int, int, int], b: tuple[int, int, int]) -> bool:
    return sum(abs(x - y) for x, y in zip(a, b)) <= 24


def _decls(style: str) -> dict[str, str]:
    out = {}
    for part in (style or "").lower().split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _px(v: str) -> float | None:
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*(px|pt|em|rem|%|vw|vh|ex|ch)?$", v.strip().replace("!important", "").strip())
    return float(m.group(1)) if m else None


def style_hides(d: dict[str, str], bg: tuple[int, int, int] | None) -> str | None:
    """Motivo por el que este estilo en línea oculta el texto de ESTE elemento (None = visible)."""
    if d.get("display", "").replace("!important", "").strip() == "none":
        return "display:none"
    if d.get("visibility", "").split()[0:1] in (["hidden"], ["collapse"]):
        return "visibility:hidden"
    fs = d.get("font-size")
    if fs and (_px(fs) is not None and _px(fs) <= 1 or fs.strip() in ("0", "0px")):
        return "font-size:0"
    op = d.get("opacity")
    if op and _px(op) is not None and _px(op) <= 0.05:
        return "opacity:0"
    ti = d.get("text-indent")
    if ti and (_px(ti) or 0) <= -500:
        return "text-indent negativo"
    if d.get("position", "") in ("absolute", "fixed"):
        for k in ("left", "top", "right", "bottom", "margin-left", "margin-top"):
            if k in d and (_px(d[k]) or 0) <= -500:
                return "fuera de pantalla"
    if d.get("clip", "").startswith("rect(0") or d.get("clip-path", "").startswith("inset(100%") or d.get("clip-path", "").startswith("inset(50%"):
        return "clip"
    if d.get("overflow") == "hidden" and ((_px(d.get("height", "x")) == 0) or (_px(d.get("width", "x")) == 0) or (_px(d.get("max-height", "x")) == 0)):
        return "alto/ancho 0"
    col = d.get("color", "")
    if col.replace(" ", "") in ("transparent", "rgba(0,0,0,0)") or d.get("-webkit-text-fill-color", "") == "transparent":
        return "texto transparente"
    fg = _rgb(col)
    if fg is not None:
        bg_local = _rgb(d.get("background-color") or d.get("background")) or bg
        if bg_local is not None and _near(fg, bg_local):
            return "color igual al fondo"
        if bg_local is None and _near(fg, (255, 255, 255)):
            return "texto blanco sobre fondo blanco"
    return None


class _Frame:
    __slots__ = ("tag", "start", "hidden", "aria", "buf", "bg", "text_len", "reason")

    def __init__(self, tag: str, start: str, hidden: str | None, aria: bool, bg):
        self.tag, self.start, self.hidden, self.aria, self.bg = tag, start, hidden, aria, bg
        self.buf: list[str] = []
        self.text_len = 0
        self.reason = hidden


class _Stripper(HTMLParser):
    def __init__(self, scanner):
        super().__init__(convert_charrefs=False)
        self.stack: list[_Frame] = [_Frame("#root", "", None, False, None)]
        self.scanner = scanner            # callable(texto) -> True si el texto oculto parece instrucción al modelo
        self.removed: list[tuple[str, bool]] = []   # (motivo, parece instrucción)

    # --- helpers
    def _top(self) -> _Frame:
        return self.stack[-1]

    def _emit(self, s: str, visible_text: int = 0) -> None:
        f = self._top()
        f.buf.append(s)
        f.text_len += visible_text

    def _close(self, f: _Frame, end: str) -> None:
        parent = self.stack[-1]
        content = "".join(f.buf)
        text = re.sub(r"<[^>]+>", " ", content)
        hidden = f.hidden or ("aria-hidden largo" if f.aria and len(text.strip()) >= MIN_LONG_ARIA else None)
        if f.tag in _DROP_TAGS:
            hidden = hidden or "etiqueta no visible"
            note = f.tag not in ("head", "title", "textarea", "script", "style")
        else:
            note = True
        if hidden:
            if note and len(text.strip()) >= MIN_REPORT:
                self.removed.append((hidden, bool(self.scanner(text))))
            return
        parent.buf.append(f.start + content + end)
        parent.text_len += f.text_len

    # --- eventos
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        raw = self.get_starttag_text() or ""
        if tag in _VOID:
            self._emit(raw)
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        parent = self._top()
        bg = _rgb(a.get("bgcolor")) or parent.bg
        d = _decls(a.get("style", ""))
        if "color" in a and tag == "font":
            d.setdefault("color", a["color"])
        bgl = _rgb(d.get("background-color") or d.get("background"))
        if bgl:
            bg = bgl
        reason = "atributo hidden" if "hidden" in a and a.get("type", "").lower() != "hidden" else style_hides(d, bg)
        if reason is None and a.get("aria-hidden", "").lower() == "true" and tag in ("div", "p", "span", "section", "li", "ul", "article", "aside", "td"):
            self.stack.append(_Frame(tag, raw, None, True, bg))
            return
        self.stack.append(_Frame(tag, raw, reason, False, bg))

    def handle_startendtag(self, tag, attrs):
        self._emit(self.get_starttag_text() or "")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                while len(self.stack) - 1 > i:           # etiquetas sin cerrar dentro de ésta: se cierran solas
                    f = self.stack.pop()
                    self._close(f, "")
                f = self.stack.pop()
                self._close(f, f"</{tag}>")
                return

    def handle_data(self, data):
        self._emit(data, len(data.strip()))

    def handle_entityref(self, name):
        self._emit(f"&{name};", 1)

    def handle_charref(self, name):
        self._emit(f"&#{name};", 1)

    def handle_comment(self, data):
        if len(data.strip()) >= MIN_REPORT:
            self.removed.append(("comentario HTML", bool(self.scanner(data))))

    def handle_decl(self, decl):
        pass

    def unknown_decl(self, data):
        pass

    def handle_pi(self, data):
        pass


def strip_hidden_html(html: str, scanner=None) -> str:
    """HTML -> HTML solo con lo visible. `scanner(texto)->bool` dice si un texto oculto parece una instrucción al modelo
    (lo inyecta `integrity`; por defecto no se evalúa y todo oculto se marca `medium`)."""
    if not html or "<" not in html:
        return html or ""
    p = _Stripper(scanner or (lambda t: False))
    try:
        p.feed(html)
        p.close()
    except Exception:   # noqa: BLE001  HTML roto: mejor devolver el original que perder la oferta
        return html
    while len(p.stack) > 1:
        f = p.stack.pop()
        p._close(f, "")
    out = "".join(p.stack[0].buf)
    marks = ""
    if p.removed:
        sev = "high" if any(inst for _, inst in p.removed) else "medium"
        marks = f" ⟦jh:hidden:{sev}⟧ "
    return marks + out
