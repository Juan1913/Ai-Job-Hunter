"""Auditoría de fidelidad del CV: el 'revisor independiente', determinista primero.

Por cada línea del CV generado (titular, frases del resumen, habilidades, cabecera de cada experiencia, viñetas,
educación, certificaciones, idiomas, contacto) se traza su ORIGEN en el perfil y se marca:

    supported  -> texto literal del perfil (o casi: mismas palabras)
    reworded   -> paráfrasis cercana: solapamiento léxico + similitud de embeddings con una línea real del perfil
    unsupported-> sin respaldo. Severidad 'high' si inventa una cifra, fecha, empresa, cargo, certificación, idioma,
                  herramienta/nombre propio o una palabra clave no confirmada; 'medium' si solo es una línea sin origen claro.

Sin LLM: reglas + léxico + embeddings (app/rag/embeddings.py; con el fallback de hashing funciona offline).
El revisor LLM es OPCIONAL (JH_AUDIT_LLM_REVIEW=1 o parámetro): critica el borrador en un contexto limpio, solo ve el
perfil y las líneas, y SOLO puede añadir sospechas (nunca limpiar una marca determinista).

`repair_cv` reduce un CV con líneas 'high' a lo que sí se puede sostener (restaura la viñeta original o quita la línea):
es determinista y no necesita otra llamada lenta al modelo."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.job import Job
from app.domain.profile import Profile
from app.domain.tailoring import KeywordGap, TailoredCV, includable
from app.profile import dates
from app.text import clean_title, contains, norm

log = logging.getLogger(__name__)

Status = Literal["supported", "reworded", "unsupported"]
Severity = Literal["none", "low", "medium", "high"]

LITERAL_RATIO = 0.8        # una línea contenida en (o que contiene a) una del perfil, con este solape de longitud, es literal
REWORD_MIN = 0.55          # puntaje mínimo para considerar una paráfrasis respaldada
INVENTED_MAX = 0.30        # por debajo: la línea no tiene casi nada que ver con el perfil
W_LEX, W_EMB = 0.55, 0.45
EMB_RESCUE = 0.9           # solo una similitud semántica muy alta rescata una línea con casi nada de vocabulario en común

_STOP = set("""de la el en y a los las con para por un una del al se que o e lo su sus como mas muy sobre entre desde hasta todo
the and of to in for or with an is are on as at by be from into over that this their our your it its""".split())
_WORD = re.compile(r"[a-z0-9+#&]+")

# Siglas y palabras con forma de 'nombre propio' que NO son herramientas ni entidades (no hace falta que estén en el perfil).
_GENERIC_CAPS = {norm(x) for x in """KPI KPIs OKR OKRs ROI SLA SLAs B2B B2C TI IT RRHH HR CV EEUU USA UE EU ONU UN PYME PYMES SME SMEs
Enero Febrero Marzo Abril Mayo Junio Julio Agosto Septiembre Octubre Noviembre Diciembre January February March April June July August
September October November December Lunes Martes Miercoles Jueves Viernes Sabado Domingo Monday Tuesday Wednesday Thursday Friday
Español Spanish Inglés English Portugués Portuguese Francés French Alemán German Italiano Italian
Colombia México Mexico Perú Peru Chile Argentina España Spain Ecuador Uruguay Panamá Panama LATAM Latinoamérica""".split()}
_EQUIV = {"ti": {"it"}, "it": {"ti"}, "rrhh": {"hr"}, "hr": {"rrhh"}, "ia": {"ai"}, "ai": {"ia"}}

_H24 = re.compile(r"(?<![\w/])(?:24\s*[/x]\s*7|7\s*[/x]\s*24|24\s*(?:horas|hours|hrs|h)\b)", re.I)
_NUM = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)*(?![\w])")
_YEARS_CLAIM = re.compile(r"(\d{1,2})\s*\+?\s*(?:años|año|anos|ano|years|year|yrs)\b", re.I)
_ENT = re.compile(r"[A-Za-zÁÉÍÓÚÑÜáéíóúñü0-9][\wÁÉÍÓÚÑÜáéíóúñü+#&./\-]*")


# --------------------------------------------------------------------------- modelos del informe

class Claim(BaseModel):
    kind: str                       # number | date | entity | tool | company | role | cert | language | keyword | education | contact | term | llm
    text: str
    supported: bool
    severity: Severity = "none"
    note: str = ""


class LineAudit(BaseModel):
    section: str                    # headline | summary | skill | experience | bullet | education | certification | language | contact | text
    index: int | None = None        # posición dentro de su sección (viñeta j, habilidad k...)
    exp_index: int | None = None    # índice en cv.experience (viñetas y cabecera)
    text: str
    status: Status
    severity: Severity = "none"
    score: float = 0.0              # 0..1: cercanía con la línea de origen
    source_ref: str | None = None   # misma nomenclatura que el RAG: experience:0:bullet:1, skill:2, summary...
    source_text: str | None = None
    claims: list[Claim] = Field(default_factory=list)

    @property
    def flagged(self) -> bool:
        return self.status == "unsupported"


class AuditReport(BaseModel):
    verdict: Literal["ok", "warn", "fail"] = "ok"      # fail = al menos una línea 'high'
    lines: list[LineAudit] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    unsupported_high: list[str] = Field(default_factory=list)   # textos legibles de lo que no se pudo respaldar
    llm_review: bool = False
    repaired: list[str] = Field(default_factory=list)

    @property
    def requires_review(self) -> bool:
        return self.verdict == "fail"

    def compact(self) -> dict:
        """Versión ligera para guardar en TailoredCV.audit: solo lo marcado (el panel resalta esas líneas)."""
        return {"verdict": self.verdict, "counts": self.counts, "llm_review": self.llm_review, "repaired": self.repaired,
                "flagged": [ln.model_dump(mode="json", exclude={"claims"}) | {"claims": [c.model_dump(mode="json") for c in ln.claims if not c.supported]}
                            for ln in self.lines if ln.status != "supported"]}


# --------------------------------------------------------------------------- utilidades de texto

def _words(s: str) -> list[str]:
    return _WORD.findall(norm(s))


def _stems(s: str) -> set[str]:
    """Raíces de 6 caracteres sin cifras: 'monitoreo' ~ 'monitoring', 'incidentes' ~ 'incidents'."""
    return {t[:6] for t in _words(s) if t not in _STOP and len(t) > 1 and not t.isdigit()}


def _canon_num(raw: str) -> str:
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw):
        return re.sub(r"[.,]", "", raw)
    return raw.replace(",", ".")


def numbers_in(text: str) -> list[tuple[str, str]]:
    """[(valor canónico, 'number'|'date')]. '24/7', '24x7', '24 horas' y '24h' son la misma cifra; '2.800' = '2,800'."""
    out: list[tuple[str, str]] = []
    t = text
    for _ in _H24.finditer(t):
        out.append(("24/7", "number"))
    t = _H24.sub(" ", t)
    for m in dates.SINGLE_RE.finditer(t):
        d = dates.parse_date(m.group("d"))
        if d:
            out.append((d, "date"))
    t = dates.SINGLE_RE.sub(" ", t)
    for m in _NUM.finditer(t):
        out.append((_canon_num(m.group(0)), "number"))
    return out


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?;])\s+(?=[A-ZÁÉÍÓÚÑ¿¡0-9])", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _entities(text: str) -> list[tuple[str, str]]:
    """[(token, 'tool'|'proper')]: siglas, CamelCase y símbolos (herramientas) siempre; Capitalizadas solo a mitad de frase."""
    out: list[tuple[str, str]] = []
    for m in _ENT.finditer(text):
        tok = m.group(0).strip(".,;:/-&")
        if len(tok) < 2 or tok.isdigit():
            continue
        letters = [c for c in tok if c.isalpha()]
        if not letters:
            continue
        pre = text[:m.start()].rstrip()
        sentence_start = not pre or pre[-1] in ".!?:;¡¿(\n"
        if all(c.isupper() for c in letters) and len(letters) >= 2:
            out.append((tok, "tool"))
        elif re.search(r"[a-záéíóúñ][A-ZÁÉÍÓÚÑ]", tok) or (re.search(r"[A-Za-z]", tok) and re.search(r"[0-9+#]", tok) and not tok.isdigit() and tok[0].isalpha()):
            out.append((tok, "tool"))
        elif tok[0].isupper() and not sentence_start and len(tok) > 2:
            out.append((tok, "proper"))
    return out


# --------------------------------------------------------------------------- origen: unidades del perfil

@dataclass
class Unit:
    ref: str
    kind: str
    text: str
    group: int | None = None           # índice de experiencia (para viñetas y cargo)
    n: str = field(default="", init=False)
    stems: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        self.n = norm(self.text)
        self.stems = _stems(self.text)


def profile_units(p: Profile) -> list[Unit]:
    u: list[Unit] = []
    if p.headline.strip():
        u.append(Unit("headline", "headline", p.headline))
    if p.summary.strip():
        u.append(Unit("summary", "summary", p.summary))
        for k, s in enumerate(_sentences(p.summary)):
            u.append(Unit(f"summary:{k}", "summary", s))
    for i, e in enumerate(p.experience):
        u.append(Unit(f"experience:{i}:title", "title", e.title, i))
        u.append(Unit(f"experience:{i}:company", "company", e.company, i))
        for j, b in enumerate(e.bullets):
            if isinstance(b, str) and b.strip():
                u.append(Unit(f"experience:{i}:bullet:{j}", "bullet", b, i))
    for k, s in enumerate(p.skills):
        u.append(Unit(f"skill:{k}", "skill", s.name))
    for k, c in enumerate(p.certifications):
        u.append(Unit(f"cert:{k}", "cert", c))
    for k, e in enumerate(p.education):
        u.append(Unit(f"education:{k}", "education", f"{e.degree} {e.institution} {e.year or ''}".strip()))
    for k, lg in enumerate(p.languages):
        u.append(Unit(f"language:{k}", "language", lg))
    return u


def profile_corpus_text(p: Profile) -> str:
    """Todo texto del perfil que puede respaldar una afirmación (incluye datos de contacto y fechas)."""
    parts = [p.full_name, p.email or "", p.phone or "", p.location or "", p.headline, p.summary, *[s.name for s in p.skills],
             *p.certifications, *p.languages]
    for e in p.experience:
        parts += [e.title, e.company, e.start or "", e.end or "", *e.bullets]
    for e in p.education:
        parts += [e.degree, e.institution, e.year or ""]
    return "\n".join(x for x in parts if x)


def experience_span_years(p: Profile, now: datetime | None = None) -> float:
    """Años entre el inicio más antiguo y el fin más reciente (o hoy): sirve para respaldar 'N años de experiencia'."""
    now = now or datetime.now()
    starts, ends = [], []
    for e in p.experience:
        s = dates.parse_date(e.start)
        if s:
            starts.append(int(s[:4]) + (int(s[5:7]) - 1) / 12 if len(s) > 4 else int(s))
        en = dates.parse_date(e.end) if e.end else None
        ends.append((int(en[:4]) + (int(en[5:7]) - 1) / 12 if len(en) > 4 else int(en)) if en else now.year + (now.month - 1) / 12)
    if not starts:
        return 0.0
    return max(ends) - min(starts)


def llm_review_enabled() -> bool:
    return os.environ.get("JH_AUDIT_LLM_REVIEW", "").strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- auditor

class Auditor:
    def __init__(self, profile: Profile, gaps: list[KeywordGap] | None = None, job: Job | None = None, embedder=None,
                 extra_allowed: list[str] | None = None, now: datetime | None = None, lexical: bool = True):
        self.lexical = lexical     # False: el texto fue traducido; el solape léxico no aplica, solo cifras/entidades/estructura
        self.profile, self.gaps, self.job = profile, gaps or [], job
        self.units = profile_units(profile)
        self.corpus = profile_corpus_text(profile)
        self.corpus_n = norm(self.corpus)
        self.corpus_stems = _stems(self.corpus)
        nums = numbers_in(self.corpus)
        self.p_numbers = {v for v, k in nums if k == "number"}
        self.p_years = {v[:4] for v, k in nums if k == "date"}
        for e in profile.experience:
            for d in (dates.parse_date(e.start), dates.parse_date(e.end) if e.end else None):
                if d:
                    self.p_years.add(d[:4])
        self.span = experience_span_years(profile, now)
        # vocabulario respaldado por el análisis de brechas: palabras clave incluibles (tengo / adyacente con evidencia / confirmada)
        self.kw_ok = [g.keyword for g in self.gaps if includable(g)]
        self.kw_ok_n = " ".join(norm(k) for k in self.kw_ok)
        self.kw_stems = {s for k in self.kw_ok for s in _stems(k)}
        self.kw_nums = {v for k in self.kw_ok for v, _ in numbers_in(k)}
        self.leaks = [g.keyword for g in self.gaps if not includable(g)]
        # el cargo objetivo, la empresa y el lugar de la OFERTA solo son vocabulario válido en titular y resumen
        self.target = " ".join(x for x in ((clean_title(job.title) if job else ""), (job.company if job else ""), (job.location if job else ""),
                                           *(extra_allowed or [])) if x)
        self.target_n = norm(self.target)
        self.target_stems = _stems(self.target)
        self.embedder = embedder
        self._vec: dict[str, object] = {}

    # ---- embeddings por lotes (una sola llamada)
    def prefetch(self, texts: list[str]) -> None:
        if self.embedder is None:
            return
        want = [t for t in dict.fromkeys(texts) if t and t not in self._vec]
        if not want:
            return
        try:
            m = self.embedder.embed(want, "doc")
            for t, v in zip(want, m):
                self._vec[t] = v
        except Exception as e:  # noqa: BLE001  (sin embeddings se audita solo con léxico)
            log.warning("auditoría sin embeddings: %s", e)
            self.embedder = None

    def _emb_score(self, text: str, cands: list[Unit]) -> float | None:
        if self.embedder is None or text not in self._vec:
            return None
        from app.rag.embeddings import scale_sim
        best = 0.0
        for u in cands:
            v = self._vec.get(u.text)
            if v is not None:
                best = max(best, scale_sim(self.embedder, float(self._vec[text] @ v)))
        return best

    # ---- respaldo de términos
    def term_backed(self, term: str, allow_target: bool) -> bool:
        t = norm(term)
        if not t:
            return True
        if t in _GENERIC_CAPS or contains(self.corpus_n, term) or contains(self.kw_ok_n, term):
            return True
        if any(contains(self.corpus_n, alt) for alt in _EQUIV.get(t, ())):
            return True
        if t.rstrip("s") != t and contains(self.corpus_n, t.rstrip("s")):       # plural simple
            return True
        return allow_target and contains(self.target_n, term)

    def _allowed_stems(self, loose: bool) -> set[str]:
        return self.kw_stems | (self.target_stems if loose else set())

    # ---- una línea
    def audit_line(self, section: str, text: str, *, index: int | None = None, exp_index: int | None = None, group: int | None = None,
                   loose: bool = False, check_entities: bool = True) -> LineAudit:
        """`group`: experiencia de origen esperada (viñetas). `loose`: titular/resumen, que sintetizan todo el perfil."""
        t = re.sub(r"\s+", " ", text or "").strip()
        n = norm(t)
        claims: list[Claim] = []
        own = [u for u in self.units if u.group == group] if group is not None else []
        pool = self.units if (loose or group is None) else (own or self.units)

        # 1) origen: literal, o la unidad más parecida
        literal: Unit | None = None
        for u in pool if pool else self.units:
            if u.n and n and (n == u.n or (len(n) >= 12 and (n in u.n and len(n) >= LITERAL_RATIO * len(u.n) or u.n in n and len(u.n) >= LITERAL_RATIO * len(n)))):
                literal = u
                break
        allowed = self._allowed_stems(loose)
        ls = _stems(t) - allowed
        best, best_lex = None, 0.0
        for u in pool:
            lex = (len(ls & u.stems) / len(ls)) if ls else 1.0
            if lex > best_lex or best is None:
                best, best_lex = u, lex
        union = (len(ls & self.corpus_stems) / len(ls)) if ls else 1.0
        lex_score = max(best_lex, union) if loose else best_lex
        emb = self._emb_score(t, pool)
        score = 1.0 if literal else (W_LEX * lex_score + W_EMB * emb if emb is not None else lex_score)
        if literal:
            best = literal
        recall = (len(ls & best.stems) / len(best.stems)) if best and best.stems else 0.0

        # 2) cifras y fechas
        best_nums = {v for v, _ in numbers_in(best.text)} if best else set()
        years_claim = {int(m.group(1)) for m in _YEARS_CLAIM.finditer(t)}
        for val, kind in numbers_in(t):
            if kind == "date":
                if val[:4] in self.p_years or val[:4] in {y[:4] for y in self.kw_nums}:
                    claims.append(Claim(kind="date", text=val, supported=True))
                else:
                    claims.append(Claim(kind="date", text=val, supported=False, severity="high", note="fecha que no está en el perfil"))
                continue
            if val in self.p_numbers or val in self.kw_nums:
                if val in best_nums or loose or val in self.kw_nums or literal:
                    claims.append(Claim(kind="number", text=val, supported=True))
                else:
                    claims.append(Claim(kind="number", text=val, supported=False, severity="medium",
                                        note="la cifra existe en el perfil pero en otra línea: puede estar mal atribuida"))
            elif _derived_years(val, years_claim, self.span):
                claims.append(Claim(kind="number", text=val, supported=True, note="años derivados de las fechas de experiencia"))
            else:
                claims.append(Claim(kind="number", text=val, supported=False, severity="high", note="cifra que no está en el perfil"))

        # 3) herramientas, siglas y nombres propios
        if check_entities and not literal:
            seen: set[str] = set()
            for tok, kind in _entities(t):
                k = norm(tok)
                if k in seen or k in _GENERIC_CAPS:
                    continue
                seen.add(k)
                if self.term_backed(tok, allow_target=loose):
                    claims.append(Claim(kind="tool" if kind == "tool" else "entity", text=tok, supported=True))
                else:
                    what = "herramienta o sigla" if kind == "tool" else "nombre propio"
                    claims.append(Claim(kind="tool" if kind == "tool" else "entity", text=tok, supported=False, severity="high",
                                        note=f"{what} que no está en el perfil"))
        # 4) palabras clave no confirmadas (regla de oro), también las escritas en minúscula
        for kw in self.leaks:
            if contains(n, kw):
                claims.append(Claim(kind="keyword", text=kw, supported=False, severity="high", note="palabra clave que el candidato no tiene ni confirmó"))

        # 5) términos nuevos (aviso, no veredicto): palabras sin respaldo en el perfil dentro de una línea por lo demás parecida
        novel = sorted(s for s in ls if s not in self.corpus_stems)
        if len(novel) >= 3 and len(novel) / max(len(ls), 1) > 0.25 and not literal:
            claims.append(Claim(kind="term", text=", ".join(novel[:6]), supported=True, severity="low", note="términos nuevos respecto al perfil"))

        # 6) veredicto de la línea
        high = [c for c in claims if not c.supported and c.severity == "high"]
        med = [c for c in claims if not c.supported and c.severity == "medium"]
        short = len(ls) < 3
        if high:
            status, sev = "unsupported", "high"
        elif literal:
            status, sev = "supported", "none"
        elif self.lexical and lex_score < INVENTED_MAX and not short and (emb is None or emb < EMB_RESCUE):
            status, sev = "unsupported", "high"
            claims.append(Claim(kind="text", text=t[:80], supported=False, severity="high", note="la línea no tiene origen en el perfil"))
        elif self.lexical and score < REWORD_MIN and not short:
            status, sev = "unsupported", "medium"
            claims.append(Claim(kind="text", text=t[:80], supported=False, severity="medium", note="origen dudoso: redacción muy distinta de cualquier línea del perfil"))
        elif med:
            status, sev = "unsupported", "medium"
        elif not self.lexical:
            status, sev = "reworded", "none"
        elif best_lex >= 0.9 and recall >= 0.7 and not novel:
            status, sev = "supported", "none"
        else:
            status, sev = "reworded", "low" if any(c.kind == "term" for c in claims) else "none"
        # una viñeta que solo encaja con OTRA experiencia es una atribución falsa
        if self.lexical and section == "bullet" and group is not None and status != "unsupported" and own:
            own_best = max((len(ls & u.stems) / len(ls)) if ls else 1.0 for u in own)
            other = max(((len(ls & u.stems) / len(ls)) if ls else 1.0 for u in self.units if u.group not in (None, group)), default=0.0)
            if own_best < REWORD_MIN and other >= 0.75 and not literal:
                status, sev = "unsupported", "high"
                claims.append(Claim(kind="text", text=t[:80], supported=False, severity="high", note="la viñeta pertenece a otra experiencia del perfil"))
        return LineAudit(section=section, index=index, exp_index=exp_index, text=t, status=status, severity=sev, score=round(float(score), 3),
                         source_ref=best.ref if best else None, source_text=best.text if best else None, claims=claims)

    # ---- estructura: experiencia, educación, certificaciones, idiomas, habilidades, contacto
    def match_experience(self, e: dict) -> tuple[int | None, list[Claim]]:
        claims: list[Claim] = []
        comp, title = str(e.get("company") or ""), str(e.get("title") or "")
        cn = norm(comp)
        cands = [i for i, pe in enumerate(self.profile.experience)
                 if cn and (norm(pe.company) == cn or (len(cn) >= 3 and (cn in norm(pe.company) or norm(pe.company) in cn))
                            or (_stems(comp) and len(_stems(comp) & _stems(pe.company)) / len(_stems(comp)) >= 0.6))]
        if not cands:
            claims.append(Claim(kind="company", text=comp, supported=False, severity="high", note="empresa que no está en el perfil"))
            return None, claims
        ts = _stems(title)

        def tsim(i: int) -> float:
            ps = _stems(self.profile.experience[i].title)
            return len(ts & ps) / max(len(ts | ps), 1) if (ts or ps) else 1.0
        i = max(cands, key=tsim)
        claims.append(Claim(kind="company", text=comp, supported=True))
        if norm(title) == norm(self.profile.experience[i].title) or tsim(i) >= 0.5:
            claims.append(Claim(kind="role", text=title, supported=True))
        else:
            claims.append(Claim(kind="role", text=title, supported=False, severity="high",
                                note=f"cargo distinto al del perfil ('{self.profile.experience[i].title}')"))
        pe = self.profile.experience[i]
        for label, cvv, pv in (("inicio", e.get("start"), pe.start), ("fin", e.get("end"), pe.end)):
            a, b = dates.parse_date(str(cvv)) if cvv else None, dates.parse_date(pv) if pv else None
            if a == b:
                continue
            if a and b and (a.startswith(b) or b.startswith(a)):
                claims.append(Claim(kind="date", text=str(cvv), supported=False, severity="medium", note=f"{label}: precisión distinta a la del perfil ({pv})"))
            elif a is None and label == "fin" and pe.end:
                claims.append(Claim(kind="date", text="actualidad", supported=False, severity="high", note=f"fin: el perfil dice {pe.end}"))
            else:
                claims.append(Claim(kind="date", text=str(cvv or "(vacío)"), supported=False, severity="high", note=f"{label}: el perfil dice {pv or 'vacío'}"))
        return i, claims

    def _simple_entry(self, section: str, text: str, kind: str, haystack: list[str], note: str) -> LineAudit:
        n = norm(text)
        ok = bool(n) and any(n == norm(h) or (len(n) >= 3 and (n in norm(h) or norm(h) in n)) for h in haystack if h)
        ref = next((u.ref for u in self.units if u.kind == kind and u.n and (n == u.n or n in u.n or u.n in n)), None) if ok else None
        c = Claim(kind=kind, text=text, supported=ok, severity="none" if ok else "high", note="" if ok else note)
        return LineAudit(section=section, text=text, status="supported" if ok else "unsupported", severity="none" if ok else "high",
                         score=1.0 if ok else 0.0, source_ref=ref, claims=[c])

    def audit_skill(self, s: str, k: int) -> LineAudit:
        n = norm(s)
        ok = (any(n == u.n for u in self.units if u.kind == "skill") or contains(self.corpus_n, s) or contains(self.kw_ok_n, s)
              or any(contains(self.corpus_n, alt) for alt in _EQUIV.get(n, ())))
        ref = next((u.ref for u in self.units if u.kind == "skill" and u.n == n), None)
        if ok:
            return LineAudit(section="skill", index=k, text=s, status="supported", score=1.0, source_ref=ref, claims=[Claim(kind="tool", text=s, supported=True)])
        c = Claim(kind="tool", text=s, supported=False, severity="high", note="habilidad o herramienta que no está en el perfil ni fue confirmada")
        return LineAudit(section="skill", index=k, text=s, status="unsupported", severity="high", claims=[c])


def _derived_years(val: str, years_claim: set[int], span: float) -> bool:
    try:
        v = float(val)
    except ValueError:
        return False
    return v == int(v) and int(v) in years_claim and span > 0 and 1 <= v <= span + 1


# --------------------------------------------------------------------------- API pública

def _finalize_report(lines: list[LineAudit]) -> AuditReport:
    counts = {"supported": 0, "reworded": 0, "unsupported": 0, "high": 0, "medium": 0}
    for ln in lines:
        counts[ln.status] += 1
        if ln.status == "unsupported":
            counts[ln.severity if ln.severity in counts else "medium"] += 1
    high = [ln for ln in lines if ln.status == "unsupported" and ln.severity == "high"]
    verdict = "fail" if high else ("warn" if counts["unsupported"] else "ok")
    why = []
    for ln in high:
        bad = [c for c in ln.claims if not c.supported and c.severity == "high"]
        why.append(f"{ln.section}: '{ln.text[:90]}' -> " + "; ".join(f"{c.text} ({c.note})" for c in bad[:3]))
    return AuditReport(verdict=verdict, lines=lines, counts=counts, unsupported_high=why)


def _default_embedder():
    try:
        from app.rag.embeddings import get_embedder
        return get_embedder()
    except Exception as e:  # noqa: BLE001
        log.warning("embeddings no disponibles para la auditoría: %s", e)
        return None


def audit_cv(cv: TailoredCV, profile: Profile, gaps: list[KeywordGap] | None = None, job: Job | None = None, embedder=None,
             use_embeddings: bool = True, llm=None, llm_review: bool | None = None, lexical: bool = True) -> AuditReport:
    """Informe por línea del CV contra el perfil. `llm` + (llm_review o JH_AUDIT_LLM_REVIEW) activan el revisor LLM."""
    aud = Auditor(profile, gaps, job, embedder if embedder is not None else (_default_embedder() if use_embeddings and lexical else None), lexical=lexical)
    plan: list[tuple[str, object]] = []     # auditoría diferida: así se embebe todo el CV en una sola llamada
    if cv.headline.strip():
        plan.append(("line", (cv.headline, dict(section="headline", loose=True))))
    for k, s in enumerate(_sentences(cv.summary or "")):
        plan.append(("line", (s, dict(section="summary", index=k, loose=True))))
    for ei, e in enumerate(cv.experience):
        pi, head_claims = aud.match_experience(e)
        bad = [c for c in head_claims if not c.supported]
        head_txt = " - ".join(x for x in (str(e.get("title") or ""), str(e.get("company") or "")) if x)
        sev = "high" if any(c.severity == "high" for c in bad) else ("medium" if bad else "none")
        plan.append(("done", LineAudit(section="experience", exp_index=ei, text=head_txt, status="unsupported" if bad else "supported", severity=sev,
                                       score=0.0 if any(c.severity == "high" for c in bad) else 1.0,
                                       source_ref=f"experience:{pi}" if pi is not None else None, claims=head_claims)))
        for bi, b in enumerate(e.get("bullets", []) or []):
            if isinstance(b, str) and b.strip():
                plan.append(("line", (b, dict(section="bullet", index=bi, exp_index=ei, group=pi))))
    for k, s in enumerate(cv.skills):
        plan.append(("skill", (s, k)))
    for e in cv.education:
        head = " - ".join(x for x in (str(e.get("degree") or ""), str(e.get("institution") or "")) if x)
        hay = [f"{p.degree} {p.institution}" for p in profile.education] + [p.degree for p in profile.education] + [p.institution for p in profile.education]
        if head:
            la = aud._simple_entry("education", str(e.get("degree") or head), "education", hay, "estudio que no está en el perfil")
            if e.get("institution"):
                lb = aud._simple_entry("education", str(e["institution"]), "education", hay, "institución que no está en el perfil")
                la = la if la.status == "unsupported" or lb.status == "supported" else lb
            la.text = head
            plan.append(("done", la))
    for c in cv.certifications:
        plan.append(("done", aud._simple_entry("certification", c, "cert", profile.certifications, "certificación que no está en el perfil")))
    for lg in cv.languages:
        plan.append(("done", aud._simple_entry("language", lg, "language", profile.languages, "idioma que no está en el perfil")))
    for key, pv in (("full_name", profile.full_name), ("email", profile.email), ("phone", profile.phone), ("location", profile.location)):
        v = (cv.contact or {}).get(key)
        if v:
            ok = norm(str(v)) == norm(pv or "")
            plan.append(("done", LineAudit(section="contact", text=f"{key}: {v}", status="supported" if ok else "unsupported", severity="none" if ok else "high",
                                           score=1.0 if ok else 0.0, claims=[Claim(kind="contact", text=str(v), supported=ok, severity="none" if ok else "high",
                                                                                   note="" if ok else f"{key} distinto al del perfil")])))
    aud.prefetch([t for kind, p in plan if kind == "line" for t in [p[0]]] + [u.text for u in aud.units])
    lines: list[LineAudit] = []
    for kind, p in plan:
        if kind == "line":
            lines.append(aud.audit_line(p[1].pop("section"), p[0], **p[1]))
        elif kind == "skill":
            lines.append(aud.audit_skill(*p))
        else:
            lines.append(p)
    report = _finalize_report(lines)
    if (llm_review if llm_review is not None else llm_review_enabled()) and llm is not None:
        report = review_with_llm(cv, profile, report, llm)
    return report


def audit_texts(items: list[tuple[str, str]], profile: Profile, gaps: list[KeywordGap] | None = None, job: Job | None = None,
                embedder=None, use_embeddings: bool = True) -> list[LineAudit]:
    """Mismo auditor para salidas que no son un CV (historias STAR, respuestas de entrevista...). items = [(sección, texto)]."""
    aud = Auditor(profile, gaps, job, embedder if embedder is not None else (_default_embedder() if use_embeddings else None))
    aud.prefetch([t for _, t in items] + [u.text for u in aud.units])
    return [aud.audit_line(sec if sec else "text", t, loose=True) for sec, t in items]


# --------------------------------------------------------------------------- revisor LLM opcional (contexto limpio)

REVIEW_SYS = """Eres un REVISOR independiente de CVs. No escribes el CV: solo lo auditas con el perfil como única fuente de verdad.
Recibes (1) los hechos del PERFIL y (2) las LÍNEAS del CV numeradas. Marca las líneas que afirmen algo que el perfil no respalda:
cifras, empresas, cargos, herramientas, certificaciones, logros o alcance (equipos dirigidos, presupuestos) que no estén en el perfil.
Reformular con otras palabras lo que SÍ está en el perfil es válido. Si dudas, no la marques.
Devuelve SOLO JSON: {"unsupported": [{"line": 3, "reason": "..."}]}"""


def review_with_llm(cv: TailoredCV, profile: Profile, report: AuditReport, llm) -> AuditReport:
    """Una llamada lenta con el borrador y la lista de afirmaciones ya marcadas. Solo AÑADE sospechas (severidad media)."""
    cand = [ln for ln in report.lines if ln.section in ("headline", "summary", "bullet")]
    if not cand:
        return report
    facts = "\n".join(f"- {u.text}" for u in profile_units(profile) if u.kind in ("headline", "summary", "title", "company", "bullet", "skill", "cert")
                      and not u.ref.startswith("summary:"))
    listing = "\n".join(f"{i}. {ln.text}" + ("   [YA MARCADA: " + "; ".join(c.note for c in ln.claims if not c.supported)[:120] + "]"
                                             if ln.status == "unsupported" else "") for i, ln in enumerate(cand))
    try:
        data = llm.complete_json(REVIEW_SYS, f"PERFIL:\n{facts[:5000]}\n\nLÍNEAS DEL CV:\n{listing}")
    except Exception as e:  # noqa: BLE001
        log.warning("revisor LLM omitido: %s", e)
        return report
    items = data.get("unsupported", []) if isinstance(data, dict) else []
    for it in items if isinstance(items, list) else []:
        try:
            ln = cand[int(it["line"])]
        except (KeyError, ValueError, TypeError, IndexError):
            continue
        if ln.status == "unsupported":
            continue
        ln.status, ln.severity = "unsupported", "medium"
        ln.claims.append(Claim(kind="llm", text=ln.text[:80], supported=False, severity="medium", note=str(it.get("reason", "sospecha del revisor LLM"))[:200]))
    new = _finalize_report(report.lines)
    new.llm_review, new.repaired = True, report.repaired
    return new


# --------------------------------------------------------------------------- reparación determinista

def repair_cv(cv: TailoredCV, profile: Profile, report: AuditReport) -> tuple[TailoredCV, list[str]]:
    """Reduce el CV a lo respaldado: viñeta inventada -> viñeta original del perfil en esa posición (o se quita); frase del resumen
    o habilidad sin respaldo -> se quita; titular -> el del perfil; experiencia con empresa inventada -> se quita; cargo/fechas -> los del perfil."""
    out = cv.model_copy(deep=True)
    acts: list[str] = []
    bad = [ln for ln in report.lines if ln.status == "unsupported" and ln.severity == "high"]
    drop_bullets: dict[int, set[int]] = {}
    new_bullets: dict[tuple[int, int], str] = {}
    drop_exp: set[int] = set()
    for ln in bad:
        if ln.section == "headline":
            out.headline = profile.headline or ""
            acts.append(f"titular restaurado al del perfil ('{ln.text[:50]}')")
        elif ln.section == "summary":
            keep = [s for k, s in enumerate(_sentences(out.summary)) if k != ln.index]
            out.summary = " ".join(keep) or profile.summary
            acts.append(f"frase del resumen eliminada: '{ln.text[:60]}'")
        elif ln.section == "skill":
            out.skills = [s for s in out.skills if norm(s) != norm(ln.text)]
            acts.append(f"habilidad sin respaldo eliminada: {ln.text}")
        elif ln.section == "certification":
            out.certifications = [c for c in out.certifications if norm(c) != norm(ln.text)]
            acts.append(f"certificación sin respaldo eliminada: {ln.text}")
        elif ln.section == "language":
            out.languages = [c for c in out.languages if norm(c) != norm(ln.text)]
            acts.append(f"idioma sin respaldo eliminado: {ln.text}")
        elif ln.section == "education":
            out.education = [e for e in out.education if " - ".join(x for x in (str(e.get("degree") or ""), str(e.get("institution") or "")) if x) != ln.text]
            acts.append(f"estudio sin respaldo eliminado: {ln.text}")
        elif ln.section == "contact":
            k = ln.text.split(":")[0]
            out.contact = {**out.contact, k: getattr(profile, k, None)}
            acts.append(f"contacto restaurado: {k}")
        elif ln.section == "experience" and ln.exp_index is not None:
            if ln.source_ref is None:
                drop_exp.add(ln.exp_index)
                acts.append(f"experiencia sin respaldo eliminada: {ln.text}")
            else:
                pe = profile.experience[int(ln.source_ref.split(":")[1])]
                out.experience[ln.exp_index].update({"title": pe.title, "company": pe.company, "start": pe.start, "end": pe.end})
                acts.append(f"cargo/fechas restaurados desde el perfil: {pe.title} en {pe.company}")
        elif ln.section == "bullet" and ln.exp_index is not None and ln.index is not None:
            e = out.experience[ln.exp_index]
            src = ln.source_ref
            pi = None
            if src and src.startswith("experience:"):
                pi = int(src.split(":")[1])
            if pi is None:
                match = next((i for i, pe in enumerate(profile.experience) if norm(pe.company) == norm(str(e.get("company") or ""))), None)
                pi = match
            orig = profile.experience[pi].bullets if pi is not None else []
            used = {norm(b) for b in e.get("bullets", [])}
            repl = next((b for j, b in enumerate(orig) if j == ln.index and norm(b) not in used), None)
            if repl:
                new_bullets[(ln.exp_index, ln.index)] = repl
                acts.append(f"viñeta inventada sustituida por la original del perfil: '{ln.text[:60]}'")
            else:
                drop_bullets.setdefault(ln.exp_index, set()).add(ln.index)
                acts.append(f"viñeta sin respaldo eliminada: '{ln.text[:60]}'")
    for ei, e in enumerate(out.experience):
        bl = list(e.get("bullets", []) or [])
        for (xe, bi), txt in new_bullets.items():
            if xe == ei and bi < len(bl):
                bl[bi] = txt
        e["bullets"] = [b for bi, b in enumerate(bl) if bi not in drop_bullets.get(ei, set())]
    out.experience = [e for ei, e in enumerate(out.experience) if ei not in drop_exp]
    return out, acts


def audit_and_repair(cv: TailoredCV, profile: Profile, gaps: list[KeywordGap] | None = None, job: Job | None = None, embedder=None,
                     llm=None, llm_review: bool | None = None, max_rounds: int = 2) -> tuple[TailoredCV, AuditReport]:
    """Audita; si hay líneas 'high' las repara (determinista) y vuelve a auditar. El informe final describe el CV ENTREGADO y
    conserva en `repaired` lo que se corrigió. Si algo 'high' sobrevive, el veredicto sigue siendo 'fail' (-> requiere revisión)."""
    emb = embedder if embedder is not None else _default_embedder()
    report = audit_cv(cv, profile, gaps, job, emb, llm=llm, llm_review=llm_review)
    done: list[str] = []
    for _ in range(max_rounds):
        if report.verdict != "fail":
            break
        cv, acts = repair_cv(cv, profile, report)
        if not acts:
            break
        done += acts
        report = audit_cv(cv, profile, gaps, job, emb, llm=None)
    report.repaired = done
    return cv, report
