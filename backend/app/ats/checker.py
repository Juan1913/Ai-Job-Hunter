"""Verificador determinista tipo escáner ATS. No usa LLM: es la 'prueba' objetiva.

El score (0-100) es la suma de reglas con peso fijo; cada regla informa cuántos puntos dio y por qué
(`AtsReport.breakdown`), así el panel puede explicar 'por qué 78'. Acepta CVs en español e inglés."""
import re
from collections import Counter

from app.cv.lang import HEADINGS, detect_lang
from app.domain.tailoring import AtsReport, GapStatus, KeywordGap, RuleResult, includable
from app.profile import dates
from app.profile.parse import find_email, find_phone
from app.profile.sections import heading_of
from app.text import contains, covered, norm

# Compatibilidad: encabezados canónicos en inglés (el verificador acepta también los de español)
REQUIRED_SECTIONS = ["PROFESSIONAL SUMMARY", "SKILLS", "WORK EXPERIENCE", "EDUCATION"]
_REQUIRED_KEYS = {"PROFESSIONAL SUMMARY": "summary", "SKILLS": "skills", "WORK EXPERIENCE": "experience", "EDUCATION": "education"}

MAX_POINTS = {"keywords": 60, "sections": 10, "contact": 8, "dates": 6, "chronology": 4, "length": 4, "characters": 4, "stuffing": 4}

_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF⭐⬆✅❌✨❤]")
_WEIRD_BULLETS = re.compile(r"[➢➤►▶❖✓✔☑■□◆◇○◦▪▫★☆➔➜→⇒▸‣⁃●♦♣♠]")
_BOX = re.compile(r"[─-╿�​‌‍﻿]")

def _sections_present(cv_text: str) -> set[str]:
    found = set()
    for ln in cv_text.split("\n"):
        h = heading_of(ln)
        if h:
            found.add(h)
    for std, key in _REQUIRED_KEYS.items():             # compatibilidad: encabezados pegados en una sola línea
        if std in cv_text or HEADINGS["es"][key if key != "summary" else "summary"] in cv_text:
            found.add(key)
    for lang in HEADINGS.values():
        for k, v in lang.items():
            if k in ("summary", "skills", "experience", "education") and v in cv_text:
                found.add(k)
    return found

def _experience_lines(cv_text: str) -> list[str]:
    out, on = [], False
    for ln in cv_text.split("\n"):
        h = heading_of(ln)
        if h:
            on = h == "experience"
            continue
        if on:
            out.append(ln)
    return out

def _chronology(lines: list[str]) -> tuple[bool, str]:
    """Las entradas deben ir de la más reciente a la más antigua."""
    rng = [r for r in (dates.find_range(ln) for ln in lines if ln.strip() and not ln.lstrip().startswith(("-", "•"))) if r]
    if len(rng) < 2:
        return True, ""
    keys = [("9999-12" if r[1] is None else (r[1] + ("-06" if len(r[1]) == 4 else "")),
             r[0] + ("-06" if len(r[0]) == 4 else "")) for r in rng]
    for a, b in zip(keys, keys[1:]):
        if b[0] > a[0] and b[1] > a[1]:
            return False, "las experiencias no están en orden cronológico inverso (la más reciente primero)"
    return True, ""

def _heading_lang(cv_text: str) -> str | None:
    """Idioma según los encabezados presentes (más fiable que el idioma del contenido, a menudo mixto)."""
    lines = {ln.strip().upper() for ln in cv_text.split("\n")}
    es = sum(1 for k, v in HEADINGS["es"].items() if k != "present" and v in lines)
    en = sum(1 for k, v in HEADINGS["en"].items() if k != "present" and v in lines)
    return None if es == en else ("es" if es > en else "en")

def check(cv_text: str, gaps: list[KeywordGap], lang: str | None = None) -> AtsReport:
    issues: list[str] = []      # los puede corregir el redactor reescribiendo
    warnings: list[str] = []    # dependen de los datos del perfil: el usuario debe completarlos
    rules: list[RuleResult] = []
    text_n = norm(cv_text)
    lang = lang if lang in HEADINGS else (_heading_lang(cv_text) or detect_lang(cv_text))

    def rule(name: str, frac: float, detail: str) -> None:
        mx = MAX_POINTS[name]
        rules.append(RuleResult(rule=name, ok=frac >= 0.999, points=round(mx * max(0.0, min(1.0, frac)), 1), max_points=mx, detail=detail))

    # ---- palabras clave (solo las incluibles: la regla de oro) y fuga de las no confirmadas
    targets = [g for g in gaps if includable(g)]
    hit = [g for g in targets if covered(text_n, g.keyword)]
    coverage = len(hit) / len(targets) if targets else 1.0
    for g in targets:
        if g not in hit:
            issues.append(f"falta la palabra clave '{g.keyword}'")
    leaks = [g for g in gaps if g.status == GapStatus.MISSING and not g.confirmed and contains(text_n, g.keyword)]
    for g in leaks:
        issues.append(f"'{g.keyword}' aparece en el CV pero el candidato no lo ha confirmado")
    rule("keywords", coverage, f"{len(hit)}/{len(targets)} palabras clave de la oferta presentes" if targets else "sin palabras clave que verificar")

    # ---- secciones estándar (ES o EN)
    present = _sections_present(cv_text)
    lang_head = HEADINGS[lang]
    missing = []
    for std, key in _REQUIRED_KEYS.items():
        if key not in present:
            missing.append(key)
            issues.append(f"falta la sección estándar '{lang_head[key] if lang == 'es' else std}'")
    rule("sections", 1 - len(missing) / len(_REQUIRED_KEYS), "todas las secciones estándar presentes" if not missing else "faltan: " + ", ".join(lang_head[k] for k in missing))

    # ---- contacto
    email = find_email(cv_text)
    phone = find_phone(cv_text)
    if not email:
        issues.append("no hay email legible")
    if not phone:
        warnings.append("no hay teléfono legible")
    rule("contact", (0.62 if email else 0) + (0.38 if phone else 0), f"email {'sí' if email else 'no'}, teléfono {'sí' if phone else 'no'}")

    # ---- fechas
    exp_lines = _experience_lines(cv_text)
    ranges = [dates.find_range(ln) for ln in exp_lines]
    n_ranges = sum(1 for r in ranges if r)
    heads = [ln for ln in exp_lines if ln.strip() and not ln.lstrip().startswith(("-", "•"))]
    ambiguous = [ln for ln in exp_lines if re.search(r"(?<![\d/])\d{1,2}[/-]\d{2}(?![\d/])", ln) and not dates.find_range(ln)]
    d_frac, d_detail = 1.0, "fechas con formato legible"
    if exp_lines and heads and n_ranges == 0:
        warnings.append("no se detectan fechas legibles en la experiencia (usa 'Mar 2021 - Actualidad' o '03/2021 - 12/2022')")
        d_frac, d_detail = 0.0, "sin fechas en la experiencia"
    elif ambiguous:
        warnings.append("hay fechas en formato ambiguo (usa mes y año completos)")
        d_frac, d_detail = 0.5, "fechas ambiguas"
    elif exp_lines and n_ranges < max(1, len(heads) // 3):
        d_frac, d_detail = 0.6, "algunas experiencias sin fechas"
    rule("dates", d_frac, d_detail)

    ok_order, why = _chronology(exp_lines)
    if not ok_order:
        warnings.append(why)
    rule("chronology", 1.0 if ok_order else 0.0, why or "orden cronológico inverso correcto")

    # ---- caracteres que los parsers leen mal
    char_issues = []
    if re.search(r"[|│┃]{2,}|\t", cv_text):
        issues.append("posible tabla/columnas: los ATS las leen mal")
        char_issues.append("tabla/tabulaciones")
    if _EMOJI.search(cv_text):
        issues.append("el CV contiene emojis o símbolos gráficos que el ATS puede leer como basura")
        char_issues.append("emojis")
    if _WEIRD_BULLETS.search(cv_text):
        issues.append("viñetas o símbolos poco comunes (➢ ✓ ■ ★...): usa '-' o '•'")
        char_issues.append("viñetas raras")
    if _BOX.search(cv_text):
        issues.append("caracteres de dibujo, invisibles o dañados en el texto")
        char_issues.append("caracteres dañados")
    rule("characters", 1 - min(1.0, len(char_issues) * 0.5), "texto limpio" if not char_issues else "problemas: " + ", ".join(char_issues))

    # ---- longitud
    words = len(cv_text.split())
    if words < 150:
        issues.append(f"CV muy corto ({words} palabras)")
    if words > 1200:
        issues.append(f"CV muy largo ({words} palabras)")
    if 300 <= words <= 900:
        l_frac = 1.0
    elif 150 <= words < 300 or 900 < words <= 1200:
        l_frac = 0.6
    else:
        l_frac = 0.0
    rule("length", l_frac, f"{words} palabras (ideal 300-900)")

    # ---- relleno de palabras clave (keyword stuffing)
    stuff: list[str] = []
    wl = max(words, 1)
    for g in targets:
        n_occ = len(re.findall(rf"(?<![a-z0-9]){re.escape(norm(g.keyword))}(?![a-z0-9])", text_n)) if norm(g.keyword) else 0
        if n_occ > 7 or (n_occ >= 4 and n_occ / wl > 0.04):
            stuff.append(f"'{g.keyword}' aparece {n_occ} veces")
    longest = max((len(re.split(r"[,;|]", ln)) for ln in cv_text.split("\n")), default=0)
    if longest > 45:
        stuff.append(f"una línea con {longest} elementos separados por comas")
    freq = Counter(re.findall(r"[a-z0-9+#]{4,}", text_n))
    top = freq.most_common(1)
    if top and top[0][1] > 12 and top[0][1] / wl > 0.05:
        stuff.append(f"'{top[0][0]}' se repite {top[0][1]} veces")
    for s in stuff:
        issues.append(f"posible relleno de palabras clave: {s}")
    rule("stuffing", 1.0 if not stuff else 0.0, "densidad de palabras clave natural" if not stuff else "; ".join(stuff))

    score = round(sum(r.points for r in rules))
    return AtsReport(score=max(0, min(score, 100)), keyword_coverage=round(coverage, 2), issues=issues, warnings=warnings, breakdown=rules, lang=lang)

def explain(report: AtsReport) -> list[str]:
    """Mapeo score -> explicación legible por regla ('keywords: 42/60 - 7/10 palabras clave presentes')."""
    return [f"{r.rule}: {r.points:g}/{r.max_points:g} - {r.detail}" for r in report.breakdown]
