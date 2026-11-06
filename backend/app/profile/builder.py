"""Constructor de perfil a prueba de modelos pequeños.

Estrategia: determinista primero, LLM solo donde hay ambigüedad o huecos, y NADA entra al perfil si no está en el texto.
1. Segmentación por encabezados ES/EN (sections.py) y parsers deterministas por sección (parse.py).
2. Email/teléfono por regex: son la verdad terreno (el LLM no puede cambiarlos).
3. Prompts cortos por sección (identidad, cargo/empresa ambiguos, educación, habilidades) con reintento y
   reparación de JSON; si el LLM cae, se usa el resultado determinista (el usuario siempre obtiene un perfil editable).
4. Verificación anti-alucinación: toda empresa/cargo/habilidad/nombre debe aparecer en el texto fuente.
5. Informe de calidad por campo (report.py) para que el panel pida revisión.
"""
from __future__ import annotations

import re

from app.ai.report import usable
from app.cv.lang import detect_lang
from app.domain.profile import Education, Experience, Profile, Skill, SkillSource
from app.llm.client import LLMClient, LLMError
from app.profile import dates, parse
from app.profile.jsonfix import loads_loose
from app.profile.report import BuildResult, FieldQuality, ProfileReport, finalize
from app.profile.sections import section_lines, segment
from app.text import contains, norm

MAX_TEXT = 12000

# --------------------------------------------------------------------------- prompts (cortos, un objetivo cada uno)

IDENTITY_SYS = """Eres un extractor de datos de CVs. Recibes las primeras líneas de un CV.
Devuelve SOLO JSON: {"full_name": "", "headline": "", "location": null}
full_name = nombre y apellidos de la persona (no el cargo ni la empresa). headline = cargo o título profesional tal cual aparece.
location = ciudad/país si aparece. Copia el texto exacto; si no aparece, null. No inventes."""

ENTRIES_SYS = """Eres un extractor de datos de CVs. Recibes cabeceras de puestos de trabajo numeradas.
Para cada una indica el cargo de la persona (title) y la empresa u organización (company), copiados tal cual del texto.
Devuelve SOLO JSON: {"entries": [{"n": 1, "title": "", "company": ""}]}. Si falta alguno, déjalo vacío. No inventes."""

EDU_SYS = """Eres un extractor de datos de CVs. Recibes líneas de estudios numeradas.
Para cada una indica el título/carrera (degree) y la institución (institution), copiados tal cual del texto.
Devuelve SOLO JSON: {"entries": [{"n": 1, "degree": "", "institution": ""}]}. Si falta alguno, déjalo vacío. No inventes."""

SKILLS_SYS = """Eres un extractor de datos de CVs. Del texto lista las habilidades técnicas, herramientas, tecnologías,
metodologías y competencias profesionales mencionadas EXPLÍCITAMENTE, copiadas tal cual (1 a 4 palabras cada una).
Sin frases largas ni habilidades que no aparezcan. Devuelve SOLO JSON: {"skills": ["..."]}. Máximo 30."""

JOBS_SYS = """Eres un extractor de datos de CVs. Del texto extrae la experiencia laboral: cada puesto con cargo (title), empresa (company),
inicio (start) y fin (end, null si es actual), copiados tal cual del texto.
Devuelve SOLO JSON: {"experience": [{"title": "", "company": "", "start": null, "end": null}]}. No inventes puestos."""

# --------------------------------------------------------------------------- utilidades

def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", norm(s))

class _Source:
    """Texto fuente normalizado para verificar que algo 'está en el CV'."""

    def __init__(self, text: str):
        self.n = norm(text)
        self.sq = _squash(text)
        self.seq = re.findall(r"[a-z0-9]+", self.n)
        self.tokens = set(self.seq)

    def has(self, term: str) -> bool:
        t = norm(term)
        if not t:
            return False
        if contains(self.n, term) or (len(_squash(term)) >= 4 and _squash(term) in self.sq):
            return True
        toks = [x for x in re.findall(r"[a-z0-9]+", t) if len(x) > 2]
        return len(toks) >= 2 and self._near(toks)

    def _near(self, toks: list[str]) -> bool:
        """Palabras sueltas del término presentes cerca unas de otras (cubre 'Gestión de camas' vs 'gestión y camas')."""
        pos = {x: [i for i, w in enumerate(self.seq) if w == x] for x in set(toks)}
        if any(not p for p in pos.values()):
            return False
        return any(all(any(abs(q - p0) <= len(toks) + 3 for q in pos[x]) for x in toks) for p0 in pos[toks[0]])

    def overlap(self, phrase: str) -> float:
        toks = [x for x in re.findall(r"[a-z0-9]+", norm(phrase)) if len(x) > 2]
        return sum(1 for x in toks if x in self.tokens) / len(toks) if toks else 0.0

class _Gate:
    """Llamadas al LLM con reintento + reparación de JSON y 'cortacircuitos' si el servidor no responde."""

    def __init__(self, llm, report: ProfileReport):
        self.llm, self.report, self.down = llm, report, llm is None

    def ask(self, system: str, user: str, keys: tuple[str, ...]) -> dict | None:
        if self.down:
            return None
        self.report.llm_used = True
        nudge = ""
        for _attempt in (1, 2):
            self.report.llm_calls += 1
            data = None
            try:
                data = self.llm.complete_json(system, user + nudge)
            except LLMError as e:
                if "no disponible" in str(e).lower() or "unavailable" in str(e).lower():
                    self.down = True
                    self.report.llm_failures += 1
                    self.report.warnings.append("El modelo no respondió: perfil construido solo con reglas; revísalo.")
                    return None
                complete = getattr(self.llm, "complete", None)
                if complete:                                         # el JSON vino roto: pedimos de nuevo y lo reparamos nosotros
                    try:
                        data = loads_loose(complete(system, user + nudge))
                    except LLMError:
                        data = None
            except Exception:  # noqa: BLE001
                data = None
            if isinstance(data, dict) and (not keys or any(k in data for k in keys)):
                return data
            self.report.llm_failures += 1
            nudge = "\n\nIMPORTANTE: responde únicamente con un objeto JSON válido con las claves: " + ", ".join(keys)
        return None

def _q(conf: float, source: str, *notes: str) -> FieldQuality:
    return FieldQuality(confidence=round(conf, 2), source=source, notes=list(notes))

# --------------------------------------------------------------------------- constructor

def build_profile(cv_text: str, llm: LLMClient | None = None) -> Profile:
    """Compatibilidad: solo el perfil. Usa `build_profile_with_report` para obtener también la calidad por campo."""
    return build_profile_with_report(cv_text, llm).profile

def build_profile_with_report(cv_text: str, llm: LLMClient | None = None, extraction_method: str | None = None,
                              use_llm: bool = True) -> BuildResult:
    text = cv_text[:MAX_TEXT]
    report = ProfileReport(extraction_method=extraction_method, language=detect_lang(text))
    chosen = usable((llm if llm is not None else LLMClient()) if use_llm else None)
    if use_llm and chosen is None:
        report.warnings.append("Perfil construido solo con reglas (sin IA): revisa nombre, cargo, habilidades y puestos; los campos marcados como faltantes no se inventan.")
    gate = _Gate(chosen, report)
    src = _Source(text)
    lines = text.split("\n")
    header, sections = segment(text)
    report.sections_found = [n for n, _ in sections]
    F = report.fields

    # ---- contacto: regex = verdad terreno
    contact_lines = header + section_lines(sections, "contact")
    email = parse.find_email("\n".join(contact_lines)) or parse.find_email(text)
    phone = parse.find_phone("\n".join(contact_lines)) or parse.find_phone(text[:1500])
    F["email"] = _q(0.98, "regex") if email else _q(0.0, "missing", "No se encontró un email: añádelo, los ATS lo exigen")
    F["phone"] = _q(0.9, "regex") if phone else _q(0.0, "missing", "No se encontró un teléfono")
    location = parse.find_location(contact_lines[:25])

    # ---- identidad
    name, name_idx, headline = parse.find_identity(lines)
    name_src = "heuristic"
    if not name or not headline:
        d = gate.ask(IDENTITY_SYS, "\n".join(lines[:15])[:1500], ("full_name", "headline", "location"))
        if d:
            nm, hl, lc = (str(d.get(k) or "").strip() for k in ("full_name", "headline", "location"))
            if not name and nm:
                if len(nm.split()) >= 2 and all(src.has(t) for t in nm.split() if len(t) > 2):
                    name, name_src = nm, "llm"
                else:
                    report.dropped.append(f"nombre '{nm}' (no aparece en el CV)")
            if not headline and hl:
                if src.overlap(hl) >= 0.7:
                    headline = hl
                else:
                    report.dropped.append(f"titular '{hl}' (no aparece en el CV)")
            if not location and lc:
                if src.has(lc):
                    location = lc
                else:
                    report.dropped.append(f"ubicación '{lc}' (no aparece en el CV)")
    F["full_name"] = _q(0.9 if name_src == "heuristic" else 0.75, name_src) if name else _q(0.0, "missing", "No se pudo identificar el nombre")
    F["headline"] = _q(0.8, "heuristic" if name_idx is not None else "llm") if headline else _q(0.0, "missing", "Sin titular profesional")
    F["location"] = _q(0.7, "heuristic") if location else _q(0.0, "missing")

    # ---- resumen (verbatim)
    summary = _summary(sections, header, lines, name, headline)
    F["summary"] = _q(0.85, "heuristic") if summary[0] and summary[1] else (_q(0.5, "heuristic", "Resumen tomado de la cabecera") if summary[0] else _q(0.0, "missing"))

    # ---- habilidades
    sp = parse.parse_skills(section_lines(sections, "skills"))
    skills, sk_src = sp.skills, "heuristic"
    if len(skills) < 3:
        d = gate.ask(SKILLS_SYS, text[:6000], ("skills",))
        extra: list[str] = []
        if d and isinstance(d.get("skills"), list):
            for s in d["skills"]:
                if isinstance(s, dict):                          # el modelo a veces devuelve {"name": "SIEM"}
                    s = s.get("name") or s.get("skill") or s.get("nombre")
                if isinstance(s, str) and 1 < len(s.strip()) <= 40 and len(s.split()) <= 5:
                    if src.has(s):
                        extra.append(s.strip())
                    else:
                        report.dropped.append(f"habilidad '{s.strip()}' (no aparece en el CV)")
        if extra:
            skills, sk_src = _dedupe(skills + extra), "llm"
    skills = _dedupe(skills)[:60]
    if sp.phrases:
        report.warnings.append(f"{len(sp.phrases)} frase(s) largas de la sección de habilidades no se trataron como habilidades")
    F["skills"] = (_q(0.9 if len(skills) >= 5 else 0.65, sk_src) if sk_src == "heuristic" else _q(0.6, "llm", "Habilidades inferidas del texto: revisa")) \
        if skills else _q(0.0, "missing", "No se encontraron habilidades")

    # ---- experiencia
    exp_lines = section_lines(sections, "experience")
    exp_note = None
    if not exp_lines:
        loose = [ln for ln in lines if ln.strip()]
        if sum(1 for ln in loose if dates.find_range(ln)) >= 1:
            exp_lines = [ln for sec, ls in sections if sec not in ("education", "certifications", "skills", "languages", "contact", "summary") for ln in ls] \
                if sections else header
            exp_note = "Sin encabezado de experiencia: puestos inferidos por las fechas"
    entries = parse.parse_experience(exp_lines) if exp_lines else []
    entries = [e for e in entries if e.title or e.company or e.bullets]
    from_llm = False
    weak = not section_lines(sections, "experience") and all(e.confidence < 0.6 for e in entries)
    if (not entries or weak) and gate.llm is not None and not gate.down:   # CV sin estructura reconocible: pedimos los puestos al modelo
        d = gate.ask(JOBS_SYS, text[:6000], ("experience",))
        llm_entries: list[parse.RawEntry] = []
        for it in (d or {}).get("experience", []) if d else []:
            if isinstance(it, dict) and src.has(str(it.get("company") or "")) and src.has(str(it.get("title") or "")):
                s, e_ = dates.parse_date(it.get("start")), dates.parse_date(it.get("end"))
                ok = lambda v, raw: v is None or v[:4] in text  # noqa: E731
                llm_entries.append(parse.RawEntry(title=str(it["title"]).strip(), company=str(it["company"]).strip(),
                                                  start=s if ok(s, it.get("start")) else None, end=e_ if ok(e_, it.get("end")) else None,
                                                  has_dates=bool(s), confidence=0.6, note="extraído por el modelo (sin estructura)"))
            elif isinstance(it, dict) and (it.get("company") or it.get("title")):
                report.dropped.append(f"puesto '{it.get('title')} - {it.get('company')}' (no aparece en el CV)")
        if llm_entries and len(llm_entries) >= len(entries):
            entries, from_llm = llm_entries, True
    amb = [] if from_llm else [(i, e) for i, e in enumerate(entries) if e.confidence < 0.8]
    if amb:
        d = gate.ask(ENTRIES_SYS, "\n".join(f"{k + 1}) {e.header_text}" for k, (_, e) in enumerate(amb)), ("entries",))
        for it in (d or {}).get("entries", []) if d else []:
            if not isinstance(it, dict) or not isinstance(it.get("n"), int) or not 1 <= it["n"] <= len(amb):
                continue
            e = amb[it["n"] - 1][1]
            hdr = _Source(e.header_text)
            t, c = str(it.get("title") or "").strip(), str(it.get("company") or "").strip()
            t_ok, c_ok = (t and hdr.has(t)), (c and hdr.has(c))
            if t and not t_ok:
                report.dropped.append(f"cargo '{t}' (no aparece en el CV)")
            if c and not c_ok:
                report.dropped.append(f"empresa '{c}' (no aparece en el CV)")
            if t_ok or c_ok:
                e.title, e.company = (t if t_ok else e.title), (c if c_ok else e.company)
                if t_ok and c_ok and norm(t) != norm(c):
                    e.confidence, e.note = 0.8, "asignado por el modelo y verificado"
    experience: list[Experience] = []
    exp_details: list[dict] = []
    for e in entries:
        if (e.company and not src.has(e.company)) or (e.title and not src.has(e.title)):
            report.dropped.append(f"puesto '{e.title} - {e.company}' (no verificable en el CV)")
            continue
        experience.append(Experience(company=e.company, title=e.title, start=e.start, end=e.end,
                                     bullets=[b for b in e.bullets if src.overlap(b) >= 0.8][:12]))
        conf = e.confidence * (1.0 if e.has_dates else 0.8) * (1.0 if e.company and e.title else 0.7)
        exp_details.append({"company": e.company, "title": e.title, "confidence": round(conf, 2), "note": e.note or ("sin fechas" if not e.has_dates else ""),
                            "needs_review": conf < 0.6})
    report.entries["experience"] = exp_details
    if experience:
        avg = sum(d["confidence"] for d in exp_details) / len(exp_details)
        F["experience"] = _q(avg, "heuristic", *([exp_note] if exp_note else []),
                             *(["Algunos puestos no tienen fechas"] if any(not e.has_dates for e in entries) else []))
    else:
        F["experience"] = _q(0.0, "missing", "No se detectaron puestos de trabajo: añádelos manualmente")

    # ---- educación
    edu_raw = parse.parse_education(section_lines(sections, "education"))
    amb_e = [e for e in edu_raw if e.confidence < 0.8]
    if amb_e:
        d = gate.ask(EDU_SYS, "\n".join(f"{k + 1}) {e.header_text}" for k, e in enumerate(amb_e)), ("entries",))
        for it in (d or {}).get("entries", []) if d else []:
            if isinstance(it, dict) and isinstance(it.get("n"), int) and 1 <= it["n"] <= len(amb_e):
                e = amb_e[it["n"] - 1]
                hdr = _Source(e.header_text)
                deg, ins = str(it.get("degree") or "").strip(), str(it.get("institution") or "").strip()
                if deg and hdr.has(deg):
                    e.degree = deg
                elif deg:
                    report.dropped.append(f"estudio '{deg}' (no aparece en el CV)")
                if ins and hdr.has(ins):
                    e.institution, e.confidence = ins, 0.75
                elif ins:
                    report.dropped.append(f"institución '{ins}' (no aparece en el CV)")
    education = [Education(institution=e.institution, degree=e.degree, year=e.year) for e in edu_raw if e.institution or e.degree]
    report.entries["education"] = [{"institution": e.institution, "degree": e.degree, "confidence": e.confidence, "needs_review": e.confidence < 0.6} for e in edu_raw]
    F["education"] = _q(sum(e.confidence for e in edu_raw) / len(edu_raw), "heuristic") if edu_raw else _q(0.0, "missing", "No se detectó formación")

    # ---- idiomas y certificaciones
    langs = parse.parse_languages(section_lines(sections, "languages")) or _inline(lines, ("idiomas", "languages"), parse.parse_languages)
    certs = parse.parse_certifications(section_lines(sections, "certifications"))
    F["languages"] = _q(0.85, "heuristic") if langs else _q(0.0, "missing")
    F["certifications"] = _q(0.8, "heuristic") if certs else _q(0.0, "missing")

    profile = Profile(
        full_name=name or "", email=email, phone=phone, location=location, headline=headline or "", summary=summary[0],
        skills=[Skill(name=s, source=SkillSource.CV) for s in skills], experience=experience, education=education,
        languages=langs, certifications=certs)
    _verify(profile, src, report)
    if not text.strip():
        report.warnings.append("El CV no contiene texto.")
    return BuildResult(profile=profile, report=finalize(report))

# --------------------------------------------------------------------------- auxiliares

def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for s in items:
        k = norm(s)
        if k and k not in seen:
            seen.add(k)
            out.append(s.strip())
    return out

def _inline(lines: list[str], labels: tuple[str, ...], fn) -> list[str]:
    """'Idiomas: Español, Inglés' dentro de otra sección."""
    for ln in lines:
        m = re.match(rf"^\W*({'|'.join(labels)})\s*[:\-]\s*(.+)$", norm(ln), re.I)
        if m:
            return fn([ln.split(":", 1)[-1] if ":" in ln else ln])
    return []

def _summary(sections, header: list[str], lines: list[str], name: str | None, headline: str | None) -> tuple[str, bool]:
    """(texto, ¿viene de una sección de resumen?). Siempre texto literal del CV."""
    sl = section_lines(sections, "summary")
    txt = " ".join(x.strip() for x in sl if x.strip())
    if txt:
        return txt[:1200], True
    cand = []
    for ln in header:
        s = ln.strip()
        if not s or s in (name, headline) or "@" in s or re.search(r"\d{6,}", s):
            continue
        if len(s.split()) >= 18:
            cand.append(s)
    return (" ".join(cand)[:1200], False) if cand else ("", False)

def _verify(profile: Profile, src: _Source, report: ProfileReport) -> None:
    """Última barrera: lo que no se pueda ubicar en el texto fuente sale del perfil (y queda en el informe)."""
    keep = []
    for s in profile.skills:
        if src.has(s.name):
            keep.append(s)
        else:
            report.dropped.append(f"habilidad '{s.name}' (no aparece en el CV)")
    profile.skills = keep
    if profile.full_name and not all(src.has(t) for t in profile.full_name.split() if len(t) > 2):
        report.dropped.append(f"nombre '{profile.full_name}' (no aparece en el CV)")
        profile.full_name = ""
        report.fields["full_name"] = _q(0.0, "missing", "No se pudo verificar el nombre")
    if profile.email and norm(profile.email) not in src.n:
        profile.email = None
    for lst, label in ((profile.languages, "idioma"), (profile.certifications, "certificación")):
        for x in list(lst):
            if not src.has(x) and src.overlap(x) < 0.8:
                lst.remove(x)
                report.dropped.append(f"{label} '{x}' (no aparece en el CV)")
