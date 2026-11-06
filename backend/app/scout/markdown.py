"""HTML de una oferta -> markdown limpio (equivalente a `description_format="markdown"` de JobSpy, sin dependencias
nuevas: lxml). Conserva encabezados, listas (anidadas), negritas/cursivas, enlaces http(s), citas y tablas simples;
descarta script/style/formularios. Tamaño acotado (`MAX_CHARS`) cortando en límite de párrafo.

Entrada insegura por definición (HTML de terceros): no se ejecuta nada, se leen solo texto y `href` http(s)."""
import re
from html import unescape

from lxml import etree
from lxml import html as lhtml

MAX_CHARS = 12_000

_SKIP = {"script", "style", "noscript", "form", "iframe", "svg", "head", "button", "select", "textarea", "input", "template"}
_BLOCK = {"p", "div", "section", "article", "header", "footer", "main", "aside", "figure", "figcaption", "address", "details", "summary", "dl"}
_HEAD = {f"h{i}": i for i in range(1, 7)}
_HAS_TAG = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")


def _inline_ws(s: str) -> str:
    return re.sub(r"[ \t\r\n ]+", " ", s)


class _Renderer:
    def render(self, node, list_stack: tuple = ()) -> str:
        if not isinstance(node.tag, str):          # comentarios / PIs
            return ""
        tag = node.tag.lower()
        if tag in _SKIP:
            return ""
        if tag == "br":
            return "\n"
        if tag == "hr":
            return "\n\n---\n\n"
        inner = self._children(node, list_stack)
        if tag in _HEAD:
            text = inner.strip()
            return f"\n\n{'#' * _HEAD[tag]} {text}\n\n" if text else ""
        if tag in ("strong", "b"):
            t = inner.strip()
            return f" **{t}** " if t else ""
        if tag in ("em", "i"):
            t = inner.strip()
            return f" *{t}* " if t else ""
        if tag == "a":
            href = (node.get("href") or "").strip()
            t = inner.strip()
            if href.lower().startswith(("http://", "https://")) and t and t != href:
                return f"[{t}]({href})"
            return inner
        if tag == "code":
            t = inner.strip()
            return f"`{t}`" if t and "\n" not in t else inner
        if tag == "pre":
            code = "".join(node.itertext()).strip("\n")
            return f"\n\n```\n{code}\n```\n\n" if code.strip() else ""
        if tag == "blockquote":
            body = inner.strip()
            return "\n\n" + "\n".join(f"> {ln}" if ln.strip() else ">" for ln in body.split("\n")) + "\n\n" if body else ""
        if tag in ("ul", "ol"):
            return "\n" + self._list(node, tag, list_stack) + "\n"
        if tag == "li":                             # <li> suelto (fuera de ul/ol)
            return f"\n- {inner.strip()}\n"
        if tag == "table":
            return "\n\n" + self._table(node) + "\n\n"
        if tag in ("tr", "td", "th", "tbody", "thead"):
            return inner
        if tag in _BLOCK or tag in ("dd", "dt"):
            return f"\n\n{inner.strip()}\n\n" if inner.strip() else ""
        return inner

    def _children(self, node, list_stack) -> str:
        out = [_inline_ws(node.text or "")] if node.text else []
        for ch in node:
            out.append(self.render(ch, list_stack))
            if ch.tail:
                out.append(_inline_ws(ch.tail))
        return "".join(out)

    def _list(self, node, tag: str, stack: tuple) -> str:
        depth, lines, n = len(stack), [], 0
        for li in node:
            if not isinstance(li.tag, str):
                continue
            if li.tag.lower() != "li":
                continue
            n += 1
            own = self._li_text(li, stack + (tag,))
            marker = f"{n}." if tag == "ol" else "-"
            lines.append(f"{'  ' * depth}{marker} {own[0]}".rstrip())
            lines.extend(own[1])
        return "\n".join(lines)

    def _li_text(self, li, stack: tuple) -> tuple[str, list[str]]:
        text = [_inline_ws(li.text or "")] if li.text else []
        subs: list[str] = []
        for ch in li:
            if isinstance(ch.tag, str) and ch.tag.lower() in ("ul", "ol"):
                subs.append(self._list(ch, ch.tag.lower(), stack))
            else:
                text.append(self.render(ch, stack))
            if ch.tail:
                text.append(_inline_ws(ch.tail))
        return " ".join("".join(text).split()), [x for x in subs if x]

    def _table(self, node) -> str:
        rows = []
        for tr in node.iter("tr"):
            cells = [" ".join("".join(c.itertext()).split()) for c in tr if isinstance(c.tag, str) and c.tag.lower() in ("td", "th")]
            if any(cells):
                rows.append("| " + " | ".join(cells) + " |")
        if len(rows) > 1:
            rows.insert(1, "| " + " | ".join("---" for _ in range(rows[0].count("|") - 1)) + " |")
        return "\n".join(rows)


def _tidy(text: str) -> str:
    lines = [ln.rstrip() for ln in text.replace("\r", "").split("\n")]
    out: list[str] = []
    blank = 0
    for ln in lines:
        ln = re.sub(r"[ \t]{2,}", " ", ln) if not ln.startswith(("  ", "```")) else ln
        if not ln.strip():
            blank += 1
            if blank <= 1 and out:
                out.append("")
            continue
        blank = 0
        out.append(ln.strip() if not ln.startswith("  ") else ln)
    s = "\n".join(out).strip()
    s = re.sub(r"\*\*\s*\*\*", "", s)
    s = re.sub(r"\*\*([^*\n]+?)\*\*\s*\*\*([^*\n]+?)\*\*", r"**\1 \2**", s)     # <b>a</b><b>b</b>
    s = re.sub(r"(?m)^[•·▪‣◦●]\s*", "- ", s)
    return s


def _plain_to_md(text: str) -> str:
    """Texto sin etiquetas (algunas APIs entregan texto plano): se respetan los saltos y las viñetas."""
    s = unescape(text).replace("\r", "")
    s = re.sub(r"(?m)^[ \t]*[•·▪‣◦●*][ \t]+", "- ", s)
    return _tidy(s)


def truncate(text: str, limit: int = MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    para = cut.rfind("\n\n")
    cut = cut[:para] if para > limit * 0.6 else cut[: max(cut.rfind(" "), 0) or limit]
    return cut.rstrip() + "\n\n[…]"


def html_to_markdown(html: str | None, limit: int = MAX_CHARS) -> str:
    """HTML (incluso doblemente escapado, como Greenhouse) -> markdown acotado. '' si no hay contenido."""
    s = html or ""
    if not s.strip():
        return ""
    if "&lt;" in s and "<" not in s:
        s = unescape(s)
    if not _HAS_TAG.search(s):
        return truncate(_plain_to_md(s), limit)
    try:
        root = lhtml.fromstring(f"<div>{s}</div>")
    except (etree.ParserError, ValueError):
        return truncate(_plain_to_md(re.sub(r"<[^>]+>", " ", s)), limit)
    return truncate(_tidy(_Renderer().render(root)), limit)
