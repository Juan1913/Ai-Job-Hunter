"""Parsers deterministas del CV (sin LLM). Son la verdad terreno para contactos y el respaldo
cuando el modelo falla o devuelve algo que no está en el texto."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.profile import dates
from app.profile.sections import heading_of
from app.text import norm

# --------------------------------------------------------------------------- contacto

EMAIL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+\-]*@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<![\w@/])(\(?\+?\s?\(?\d[\d\s().\-]{6,}\d)(?![\w@])")
_YEARISH = re.compile(r"^\D*(?:19|20)\d{2}\D+(?:19|20)\d{2}\D*$")

def find_email(text: str) -> str | None:
    m = EMAIL_RE.search(text.replace("mailto:", " "))
    return m.group(0).rstrip(".-") if m else None

def find_phone(text: str) -> str | None:
    for m in _PHONE_RE.finditer(text):
        raw = re.sub(r"\s+", " ", m.group(1)).strip(" .-")
        digits = re.sub(r"\D", "", raw)
        if not 8 <= len(digits) <= 15:
            continue
        if _YEARISH.match(raw) or dates.find_range(raw):
            continue
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw) or re.fullmatch(r"\d+[.,]\d+", raw):
            continue                                # cifras tipo 2.800.000 o 12.5
        if "+" not in raw and "(" not in raw and len(digits) < 9:
            continue
        return raw
    return None

_COUNTRIES = ("colombia mexico méxico argentina chile peru perú ecuador españa espana spain united states usa estados unidos brasil brazil uruguay "
              "panama panamá costa rica venezuela dominicana guatemala bolivia paraguay uk united kingdom reino unido canada canadá germany alemania "
              "france francia italia italy portugal ireland irlanda").split()
_REMOTE = re.compile(r"\b(remoto|remote|híbrido|hibrido|hybrid)\b", re.I)

def find_location(lines: list[str]) -> str | None:
    """Ubicación en las líneas de cabecera/contacto: 'Ciudad, País', 'Ciudad D.C.', o país conocido."""
    for ln in lines:
        for part in re.split(r"\s*[|•·●▪]\s*|\s{2,}|\t", ln):
            part = re.sub(r"^(?:ubicaci[oó]n|location|ciudad|city|direcci[oó]n|address|vivo en|based in|residencia)\s*[:\-]\s*", "", part.strip(), flags=re.I)
            part = re.sub(r"\s+\d{5}(?:-\d{4})?$", "", part)       # código postal ('Cambridge, MA 02138')
            if not part or "@" in part or " - " in part or re.search(r"\d{3}", part) or len(part.split()) > 6 or "http" in part or "linkedin" in part.lower():
                continue
            if re.fullmatch(r"[A-ZÁÉÍÓÚÑ][\w.áéíóúñÁÉÍÓÚÑ'\-]*(?: [A-Za-zÁÉÍÓÚÑáéíóúñ.'\-]+){0,3}(?:, ?[A-ZÁÉÍÓÚÑ][\w.áéíóúñÁÉÍÓÚÑ'\- ]+){1,2}", part) \
                    and not re.search(r"\b(?:analyst|engineer|manager|inc|ltd)\b", part, re.I):
                return part
            n = norm(part)
            if any(c == n or n.endswith(" " + c) or n.endswith(", " + c) for c in map(norm, _COUNTRIES)) or re.search(r"\bD\.?\s?C\.?$", part):
                return part
    return None

# --------------------------------------------------------------------------- nombre y titular

JOB_STEMS = (
    "analist|analyst|ingenier|engineer|develop|desarroll|manager|gerent|director|jefe|coordinador|coordinator|asesor|advisor|ejecutiv|executive|asistente|assistant|"
    "especialista|specialist|consult|contador|accountant|auditor|enfermer|nurse|medic|doctor|abogad|lawyer|attorney|profesor|teacher|docente|tecnic|technician|"
    "operari|supervisor|vendedor|salesperson|sales|representante|representative|agente|agent|administrador|administrator|dise[nñ]ador|designer|arquitect|architect|"
    "programador|programmer|tester|qa|devops|scientist|cientific|researcher|investigador|practicante|intern|pasante|trainee|becari|cajer|cociner|chef|conductor|driver|"
    "secretari|recepcionist|officer|oficial|lead|lider|head|president|ceo|cto|cfo|fundador|founder|freelance|emprendedor|comercial|contable|economista|psicolog|"
    "nutricionist|fisioterapeuta|farmaceutic|pharmacist|mecanic|electricist|soldador|logistic|bodeguero|auxiliar|aprendiz|publicista|periodista|journalist|"
    "editor|redactor|copywriter|marketer|community|recruiter|reclutador|talento|hr|rrhh|educador|instructor|trainer|entrenador|terapeuta|therapist|paramedic|"
    "veterinari|dentist|odontolog|bartender|mesero|waiter|barista|promotor|merchandiser|estilista|stylist|maestro|arquitecto|topograf|inspector|planner|"
    "senior|junior|sr|jr|owner|propietari|partner|socio|encargad|responsable|gestor|tecn[oó]log[oa]s?|operador|operator|mozo|ayudante|helper|clerk|cashier|teller|banker|"
    "fellow|teaching|tutor|volunteer|captain|chair|treasurer|secretary|member|miembro|voluntari|"
    "trader|broker|actuar|underwriter|paralegal|notari|investigator|scrum|product owner|dba|sysadmin|devsecops|sre|soc"
)
JOB_RE = re.compile(rf"\b(?:sub|vice|co|jr|sr)?[\- ]?(?:{JOB_STEMS})", re.I)
COMPANY_RE = re.compile(
    r"(?:\b(?:s\.?\s?a\.?\s?s?\.?|s\.?\s?l\.?|s\.?\s?a\.?\s?de\s?c\.?v\.?|ltda\.?|ltd\.?|inc\.?|llc|l\.l\.c|corp\.?|corporation|corporaci[oó]n|gmbh|group|grupo|holding|holdings|bank|banco|"
    r"universidad|university|hospital|cl[ií]nica|clinic|consulting|consultores|consultora|solutions|soluciones|technologies|tecnolog[ií]as|labs|studio|agency|agencia|"
    r"fundaci[oó]n|foundation|ministerio|alcald[ií]a|gobierno|government|institute|instituto|school|colegio|empresa|company|co\.|cia|cía|estudio|laboratorios?|"
    r"society|department|departamento|club|association|asociaci[oó]n|committee|comit[eé]|council|college|center|centre|fund|network|team|"
    r"seguros|insurance|retail|logistics|log[ií]stica|systems|sistemas|software|digital|media|partners|associates|asociados|hotel|restaurante|farmacia|supermercados?)\b|&)", re.I)

_HEADLINE_BAD = re.compile(r"@|https?://|www\.|\d{4}|linkedin|github", re.I)

def _name_like(ln: str) -> bool:
    ln = ln.strip()
    if not ln or len(ln) > 50 or re.search(r"[\d@:/|•,;()+]", ln) or heading_of(ln):
        return False
    toks = ln.split()
    if not 2 <= len(toks) <= 5:
        return False
    particles = {"de", "del", "la", "las", "los", "da", "das", "do", "dos", "van", "von", "der", "bin", "al", "el", "y", "e", "di", "du", "le", "mc", "st"}
    ok = all(t.lower() in particles or re.fullmatch(r"[A-ZÁÉÍÓÚÑÜ][A-Za-zÁÉÍÓÚÑÜáéíóúñü.'\-]*", t) for t in toks)
    caps = sum(1 for t in toks if t.lower() not in particles)
    return ok and caps >= 2 and not JOB_RE.search(norm(ln)) and not COMPANY_RE.search(ln)

def _titlecase_name(s: str) -> str:
    """'ANA BELÉN SÁNCHEZ' -> 'Ana Belén Sánchez' (solo si todo viene en mayúsculas)."""
    if not s.isupper():
        return s
    particles = {"de", "del", "la", "las", "los", "da", "das", "do", "dos", "van", "von", "der", "y", "e", "di", "du", "le"}
    return " ".join(w.lower() if w.lower() in particles else w[:1] + w[1:].lower() for w in s.split())

def find_name(lines: list[str], skip: set[int] | None = None) -> tuple[str | None, int | None]:
    """Primera línea con forma de nombre propio entre las ~40 primeras. Devuelve (nombre, índice de línea)."""
    seen = 0
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        seen += 1
        if seen > 40:
            break
        if skip and i in skip:
            continue
        if _name_like(ln):
            return _titlecase_name(ln.strip()), i
    return None, None

def find_identity(lines: list[str]) -> tuple[str | None, int | None, str | None]:
    """(nombre, índice de línea, titular) también cuando todo viene en una línea: 'Ana Pérez - Contadora - ana@x.com'."""
    name, idx = find_name(lines)
    if name:
        return name, idx, find_headline(lines, idx)
    seen = 0
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        seen += 1
        if seen > 4:
            break
        parts = [p.strip() for p in re.split(r"\s+[-–—|·•]\s+|\s*\|\s*", ln) if p.strip()]
        if len(parts) >= 2 and _name_like(parts[0]):
            parts[0] = _titlecase_name(parts[0])
            head = next((p for p in parts[1:] if JOB_RE.search(norm(p)) and not _HEADLINE_BAD.search(p)), None)
            return parts[0], i, head
    return None, None, None

def find_headline(lines: list[str], name_idx: int | None) -> str | None:
    """Titular: la línea siguiente al nombre si parece un cargo (no contacto, no encabezado)."""
    if name_idx is None:
        return None
    for ln in lines[name_idx + 1: name_idx + 4]:
        s = ln.strip()
        if not s:
            continue
        if heading_of(s) or len(s.split()) > 14 or s.startswith("•"):
            return None
        if _HEADLINE_BAD.search(s):        # 'Full Stack Developer | correo | teléfono': el titular es el primer trozo
            first = re.split(r"\s*[|·•]\s*|\s+[-–—]\s+", s)[0].strip()
            return first if JOB_RE.search(norm(first)) and not _HEADLINE_BAD.search(first) else None
        return s
    return None

# --------------------------------------------------------------------------- habilidades / idiomas / certificaciones

_BULLET_PREFIX = re.compile(r"^\s*(?:[•▪◦●■\-–—*·➢>]+|\d+[.)])\s+")
_LEVEL = re.compile(r"\s*(?:[\(\[][^)\]]*(?:avanzado|intermedio|basico|básico|advanced|intermediate|basic|expert|experto|years?|años|nivel|level|%)[^)\]]*[\)\]]|[-–:]\s*(?:avanzado|intermedio|b[aá]sico|advanced|intermediate|basic|expert|experto|nivel \w+|level \w+)\s*|[★☆●○▪]+\s*)$", re.I)

@dataclass
class SkillParse:
    skills: list[str] = field(default_factory=list)
    phrases: list[str] = field(default_factory=list)      # frases largas que NO son habilidades

def _join_wrapped(lines: list[str]) -> list[str]:
    """Une líneas partidas por el ajuste de línea del PDF ('..., Auditoría' + 'interna, ...')."""
    out: list[str] = []
    for ln in lines:
        s = ln.strip()
        if out and s and out[-1] and not _BULLET_PREFIX.match(s) and (re.fullmatch(r"\d{2,6}(?:[:.\-]\d{4})?[,.;]?", s) or out[-1].rstrip().endswith((",", ";")) or (s[:1].islower() and len(out[-1]) >= 45)):
            out[-1] = out[-1].rstrip() + " " + s
        else:
            out.append(s)
    return out

_NOT_SKILL_LABELS = {"languages", "language", "idiomas", "idioma", "interests", "interest", "intereses", "hobbies", "aficiones", "passatiempos", "activities", "actividades"}

_AMBIGUOUS_LABELS = {"languages", "language", "idiomas", "idioma"}      # 'Languages: Python, Go' (programación) vs 'Languages: English, Spanish'

def _spoken_languages(text: str) -> bool:
    items = [norm(re.sub(r"\(.*?\)", "", x)).strip() for x in re.split(r"[,;|/]", text) if x.strip()]
    return bool(items) and any(i in _LANGS for i in items)

def parse_skills(lines: list[str]) -> SkillParse:
    out = SkillParse()
    seen: set[str] = set()
    for ln in _join_wrapped(lines):
        ln = _BULLET_PREFIX.sub("", ln).strip()
        if not ln:
            continue
        m = re.match(r"^([^:,;|]{2,28}):\s+(.+)$", ln)           # 'Lenguajes: Python, Go'
        if m and len(m.group(1).split()) <= 3:
            if norm(m.group(1)) in _NOT_SKILL_LABELS and not (norm(m.group(1)) in _AMBIGUOUS_LABELS and not _spoken_languages(m.group(2))):
                continue                                             # 'Interests: ...' o 'Languages: English, Spanish' no son habilidades técnicas
            ln = m.group(2)
        for item in re.split(r"\s*[,;|•·●▪]\s*|\s{3,}|\s+/\s+|\t", ln):
            item = _LEVEL.sub("", item.strip().strip(".")).strip(" -–:")
            if not item or (len(item) < 2 and item not in ("C", "R")):
                continue
            if len(item.split()) > 6 or re.search(r"[.!?]\s+[A-ZÁÉÍÓÚ]", item) or item.endswith((".", ";")) or len(item) > 60:
                out.phrases.append(item)
                continue
            n = norm(item)
            if n in seen or n in {"y", "and", "etc"} or re.fullmatch(r"[\W\d_]+", n):
                continue
            seen.add(n)
            out.skills.append(item)
    return out

_LANGS = ("espanol spanish castellano ingles english frances french portugues portuguese aleman german italiano italian chino chinese mandarin cantones cantonese "
          "japones japanese catalan valenciano arabe arabic ruso russian hindi coreano korean euskera basque gallego galician holandes dutch sueco swedish "
          "polaco polish turco turkish hebreo hebrew griego greek rumano romanian ucraniano ukrainian vietnamita vietnamese tailandes thai urdu bengali "
          "quechua guarani latin").split()

def parse_languages(lines: list[str]) -> list[str]:
    out: list[str] = []
    for ln in lines:
        ln = _BULLET_PREFIX.sub("", ln).strip()
        if not ln:
            continue
        ln = re.sub(r"^(?:idiomas?|languages?)\s*[:\-]\s*", "", ln, flags=re.I)
        # separa por comas/pipes pero no dentro de paréntesis
        for item in re.split(r"\s*[;|•·●▪]\s*|,\s*(?![^()]*\))", ln):
            item = re.sub(r"^([^:()]{2,25}):\s*(.+)$", r"\1 (\2)", item.strip(" ."))
            n = norm(item)
            if item and len(item) <= 50 and any(re.search(rf"\b{lg}\b", n) for lg in _LANGS) and norm(item) not in {norm(x) for x in out}:
                out.append(item)
    return out

def parse_certifications(lines: list[str]) -> list[str]:
    out: list[str] = []
    cur = ""
    for ln in lines:
        s = ln.strip()
        if not s:
            if cur:
                out.append(cur)
                cur = ""
            continue
        bulleted = bool(_BULLET_PREFIX.match(s))
        s = _BULLET_PREFIX.sub("", s).strip()
        if cur and not bulleted and (s[:1].islower() or cur.endswith((",", "-", "–", "de", "of", "en"))):
            cur += " " + s                           # línea que continúa la anterior
        else:
            if cur:
                out.append(cur)
            cur = s
    if cur:
        out.append(cur)
    res, seen = [], set()
    for c in out:
        c = c.strip(" .;")
        if 3 <= len(c) <= 140 and norm(c) not in seen and not re.fullmatch(r"[\W\d_]+", c):
            seen.add(norm(c))
            res.append(c)
    return res

# --------------------------------------------------------------------------- experiencia

@dataclass
class RawEntry:
    header_lines: list[str] = field(default_factory=list)
    title: str = ""
    company: str = ""
    start: str | None = None
    end: str | None = None
    has_dates: bool = False
    bullets: list[str] = field(default_factory=list)
    confidence: float = 0.0
    note: str = ""

    @property
    def header_text(self) -> str:
        return " | ".join(self.header_lines)

def _is_bullet(s: str) -> bool:
    return bool(_BULLET_PREFIX.match(s)) or s.startswith(("•", "- ", "* "))

def _header_like(s: str) -> bool:
    s = s.strip()
    abbr = bool(re.search(r"(?:\b\w\.){2,}$|\b(?:inc|ltd|ltda|co|corp|cía|cia|sr|jr|llc|etc)\.$", s, re.I))
    return bool(s) and not _is_bullet(s) and len(s.split()) <= 14 and (abbr or not s.endswith((".", ";"))) and not heading_of(s)

def _strip_dates(s: str) -> str:
    r = dates.find_range(s)
    if r:
        a, b = r[2]
        s = (s[:a] + " " + s[b:])
    else:
        sg = dates.find_single(s)
        if sg and re.search(r"\b(?:desde|since|from|hasta|until)\b|^\W*$", s[:sg[1][0]], re.I):
            s = s[:sg[1][0]] + s[sg[1][1]:]
    s = re.sub(r"\b(?:desde|since|from)\s*$", "", s.strip(), flags=re.I)
    return re.sub(r"\(\s*\)|\[\s*\]", "", s).strip(" -–—|·,:;")

def _trim_location(part: str) -> str:
    """'Zara España, Madrid' -> 'Zara España' (la ciudad no es parte del nombre de la empresa)."""
    m = re.match(r"^(.+?),\s*([A-ZÁÉÍÓÚÑ][\wáéíóúñ.]*(?: [A-ZÁÉÍÓÚÑa-záéíóúñ.]+){0,2})$", part)
    if m and not COMPANY_RE.search(m.group(2)) and not JOB_RE.search(norm(m.group(2))) and not re.search(r"\d", m.group(2)):
        return m.group(1).strip()
    return part

_STATE_OR_COUNTRY = r"(?:[A-Z]{2}|" + "|".join(re.escape(c) for c in _COUNTRIES) + r")"
_LOC_PART = re.compile(rf"[A-ZÁÉÍÓÚÑ][\w.áéíóúñÁÉÍÓÚÑ'\- ]*,\s*{_STATE_OR_COUNTRY}(?:\s+\d{{4,5}}(?:-\d{{4}})?)?", re.I)

_CITY_PREFIX = r"(?:San|Santa|Nueva|New|Los|Las|El|La|Buenos|Ciudad|Puerto|Palo|Costa|Salt|Santiago de|Ciudad de)"
_COUNTRY_WORDS = "|".join(re.escape(c.title()) for c in _COUNTRIES if len(c) > 3)
_TRAIL_CITY = re.compile(rf"^(?P<org>.+?\S)\s+(?P<city>(?:{_CITY_PREFIX}\s+)?[A-ZÁÉÍÓÚÑ][\wáéíóúñ.]+),\s*(?:[A-Z]{{2}}|{_COUNTRY_WORDS})(?:\s+\d{{5}})?$")

def _strip_city_country(line: str) -> str:
    """'Togrow Agencia Medellín, Colombia' -> 'Togrow Agencia' (la ciudad pegada al final no es parte de la empresa)."""
    m = _TRAIL_CITY.match(line.strip())
    if not m:
        return line
    org = re.sub(r"\s+(?:en|in|at|de|del|-|–|·)$", "", m.group("org"), flags=re.I).strip()
    if len(org.split()) < 1 or JOB_RE.fullmatch(norm(org)):
        return line
    return org

def _is_location_part(p: str) -> bool:
    """'Cambridge, MA' / 'Bogotá, Colombia' pegados al margen derecho de la cabecera: no son empresa ni cargo."""
    return bool(_LOC_PART.fullmatch(p.strip())) and not JOB_RE.search(norm(p)) and not COMPANY_RE.search(p)

def _split_parts(line: str, commas: bool = False) -> list[str]:
    if not commas:
        line = _strip_city_country(line)
    line = re.sub(r"\([^)]*\)", " ", line)                     # '(Remoto)', '(Bogotá)'
    line = re.sub(r"(?<=[a-záéíóúñ]{3})\.\s+[A-ZÁÉÍÓÚÑ].*$", "", line) if len(line) > 60 else line   # 'Cargo en Empresa. Hice X...'
    parts = re.split(r"\s+[-–—|·•]\s+|\s*\|\s*|\s+@\s+|\s+at\s+|\s{2,}|\t", line)
    if len(parts) == 1 and (commas or line.count(",") == 1):
        parts = [p for p in line.split(",")]
    elif not commas:
        parts = [_trim_location(p) for p in parts]
        if len(parts) > 1:
            parts = [p for p in parts if not _is_location_part(p)] or parts
    return [p.strip(" -–—|·,:;") for p in parts if p.strip(" -–—|·,:;")]

def _company_hit(p: str) -> bool:
    """COMPANY_RE, pero un '&' dentro de un cargo ('Desarrollador de Software & DevSecOps') no lo vuelve empresa."""
    m = COMPANY_RE.search(p)
    return bool(m) and not (m.group(0) == "&" and JOB_RE.search(norm(p)))

def _title_score(p: str) -> int:
    n = norm(p)
    return (2 if JOB_RE.search(n) else 0) - (1 if _company_hit(p) else 0)

def _company_score(p: str) -> int:
    return 2 if _company_hit(p) else 0

def decide_title_company(parts: list[str]) -> tuple[str, str, float, str]:
    if not parts:
        return "", "", 0.0, "sin cabecera"
    if len(parts) == 1:
        p = parts[0]
        if _title_score(p) > 0:
            return p, "", 0.5, "solo cargo"
        return ("", p, 0.4, "solo empresa") if _company_score(p) else (p, "", 0.35, "una sola línea ambigua")
    scores = [_title_score(p) for p in parts]
    best = max(range(len(parts)), key=lambda i: (scores[i], -i))
    if scores[best] > 0:
        rest = [p for i, p in enumerate(parts) if i != best]
        company = max(rest, key=_company_score) if any(_company_score(r) for r in rest) else rest[0]
        return parts[best], company, 0.85 if _company_score(company) or len(parts) == 2 else 0.7, "cargo reconocido"
    cs = [_company_score(p) for p in parts]
    cb = max(range(len(parts)), key=lambda i: (cs[i], -i))
    if cs[cb] > 0:
        rest = [p for i, p in enumerate(parts) if i != cb]
        return rest[0], parts[cb], 0.7, "empresa reconocida"
    return parts[0], parts[1], 0.45, "orden asumido (cargo primero)"

def parse_experience(lines: list[str]) -> list[RawEntry]:
    L = [ln.strip() for ln in lines]
    n = len(L)
    date_idx = []
    for i, s in enumerate(L):
        if not s or _is_bullet(s):
            continue
        r = dates.find_range(s)
        rest = _strip_dates(s) if r else ""
        if r and len(rest.split()) <= 14:
            date_idx.append(i)
    starts: list[int] = []
    hdr_before: dict[int, list[int]] = {}
    if date_idx:
        lower = 0
        for d in date_idx:
            back: list[int] = []
            j = d - 1
            # la línea del propio rango puede traer también cargo/empresa
            own_has_text = len(_strip_dates(L[d]).split()) >= 1
            while j >= lower and len(back) < (1 if own_has_text else 2):
                s = L[j]
                if not s:
                    if back:
                        break
                    j -= 1
                    if own_has_text:
                        break
                    continue
                if not _header_like(s) or s[:1].islower():
                    break
                if back and _split_parts(L[back[-1]]) and len(_split_parts(L[back[-1]])) > 1:
                    break
                back.append(j)
                j -= 1
            back.reverse()
            hdr_before[d] = back
            starts.append(back[0] if back else d)
            lower = d + 1
    entries: list[RawEntry] = []
    if date_idx:
        for k, d in enumerate(date_idx):
            end_body = starts[k + 1] if k + 1 < len(date_idx) else n
            e = RawEntry(has_dates=True)
            r = dates.find_range(L[d])
            assert r
            e.start, e.end = r[0], r[1]
            hdr = [L[j] for j in hdr_before[d]]
            own = _strip_dates(L[d])
            tail = ""
            ms = re.search(r"(?<=[a-záéíóúñ]{3})\.\s+([A-ZÁÉÍÓÚÑ].*)$", own) if len(own) > 60 else None
            if ms:                                              # '2019 - Presente: Cargo en Empresa. Hice X y Y' -> la frase es una viñeta
                own, tail = own[:ms.start()], ms.group(1)
            body_from = d + 1
            harvard_form = False
            if own:
                hdr.append(own)
                nxt = L[d + 1] if d + 1 < end_body else ""
                if not hdr_before[d] and nxt and _header_like(nxt) and not dates.find_range(nxt) and not nxt[:1].islower():
                    hdr.append(nxt)                             # Harvard: 'Organización ··· fechas' y debajo el cargo en cursiva
                    harvard_form = True
                    body_from = d + 2
            if not hdr:                                         # fecha primero, cargo/empresa debajo
                j = d + 1
                while j < end_body and len(hdr) < 2 and L[j] and _header_like(L[j]):
                    hdr.append(L[j])
                    j += 1
                body_from = j
            e.header_lines = hdr
            parts = [p for h in hdr for p in _split_parts(h)]
            e.title, e.company, e.confidence, e.note = decide_title_company(parts)
            if harvard_form and e.note.startswith("orden asumido") and len(parts) == 2:
                e.title, e.company, e.confidence, e.note = parts[1], parts[0], 0.7, "formato Harvard: organización primero"
            e.bullets = ([tail] if tail else []) + _bullets(L[body_from:end_body])
            entries.append(e)
        return entries
    # Sin fechas: cada línea 'cabecera' que sigue a una viñeta o a una línea en blanco abre una entrada
    cur: RawEntry | None = None
    body: list[str] = []
    prev_blank = True
    prev_bullet = False
    for s in L:
        if not s:
            prev_blank = True
            body.append(s)
            continue
        if _header_like(s) and (prev_blank or prev_bullet) and not s[:1].islower() and (cur is None or cur.bullets or body):
            if cur is not None:
                cur.bullets = _bullets(body)
            cur = RawEntry(header_lines=[s])
            parts = _split_parts(s)
            cur.title, cur.company, cur.confidence, cur.note = decide_title_company(parts)
            cur.confidence = min(cur.confidence, 0.5)
            entries.append(cur)
            body = []
        elif cur is None:
            cur = RawEntry(header_lines=[s])
            parts = _split_parts(s)
            cur.title, cur.company, cur.confidence, cur.note = decide_title_company(parts)
            cur.confidence = min(cur.confidence, 0.5)
            entries.append(cur)
        else:
            body.append(s)
        prev_blank, prev_bullet = False, _is_bullet(s)
    if cur is not None:
        cur.bullets = _bullets(body)
    return entries

def _bullets(lines: list[str]) -> list[str]:
    out: list[str] = []
    marked = any(_is_bullet(s) for s in lines if s)
    for s in lines:
        if not s:
            continue
        if _is_bullet(s):
            out.append(_BULLET_PREFIX.sub("", s).strip())
        elif marked and out:
            out[-1] += " " + s                         # continuación de la viñeta anterior
        else:
            out.append(s)
    return [b for b in out if len(b) > 2]

# --------------------------------------------------------------------------- educación

INSTITUTION_RE = re.compile(r"\b(?:universidad|universitat|university|college|instituto|institute|escuela|school|academia|academy|colegio|polit[eé]cnic\w*|sena|"
                            r"corporaci[oó]n universitaria|fundaci[oó]n universitaria|facultad|faculty|bootcamp|udemy|coursera|platzi|edx|centro de formaci[oó]n|"
                            r"universidade|universit[aà]|conservatorio|tecnol[oó]gico|uniminuto|unam|uba|itesm|udla|upc|ucm|mit)\b", re.I)
DEGREE_RE = re.compile(r"\b(?:grado|licenciatura|licenciado|ingenier\w*|bachelor|b\.?\s?sc?\.?|b\.?a\.?|master|m[aá]ster|m\.?sc?\.?|mba|doctorado|phd|doctor|"
                       r"t[eé]cnic[oa]|tecn[oó]log[oa]|especializaci[oó]n|diplomado|bachillerato|high school|diploma|degree|major|carrera|profesional en|"
                       r"contadur[ií]a|administraci[oó]n|medicina|enfermer[ií]a|derecho|psicolog[ií]a|arquitectura|econom[ií]a|dise[nñ]o|marketing|comunicaci[oó]n)\b", re.I)

@dataclass
class RawEdu:
    institution: str = ""
    degree: str = ""
    year: str | None = None
    header_text: str = ""
    confidence: float = 0.0

def parse_education(lines: list[str]) -> list[RawEdu]:
    blocks: list[list[str]] = [[]]
    for ln in lines:
        s = ln.strip()
        if not s:
            if blocks[-1]:
                blocks.append([])
            continue
        blocks[-1].append(s)
    blocks = [b for b in blocks if b]
    final: list[list[str]] = []
    for b in blocks:                                              # una línea por título cuando cada una trae su año
        if len(b) > 1 and all(dates.find_single(x) for x in b) and all(len(x.split()) >= 3 for x in b):
            final += [[x] for x in b]
        else:
            final.append(b)
    out: list[RawEdu] = []
    for b in final:
        text = " | ".join(b)
        yr = None
        rg = None
        for x in b:
            rg = dates.find_range(x)
            if rg:
                yr = (rg[1] or rg[0] or "")[:4] if rg[1] else None
                if rg[1] is None:
                    yr = None
                break
        if not rg:
            for x in b:
                sg = dates.find_single(x)
                if sg and sg[0]:
                    yr = sg[0][:4]
                    break
        parts = [p for x in b for p in _split_parts(_strip_city_country(_strip_dates(_BULLET_PREFIX.sub("", x))), commas=True)]
        parts = [p for p in parts if not re.fullmatch(r"[\d\W]+", p)]
        if not parts:
            continue
        inst = next((p for p in parts if INSTITUTION_RE.search(p)), "")
        rest = [p for p in parts if p != inst]
        degree = next((p for p in rest if DEGREE_RE.search(p)), rest[0] if rest else "")
        if not inst:
            others = [p for p in rest if p != degree]
            inst = others[0] if others else ""
            conf = 0.5 if inst else 0.4
        else:
            conf = 0.85 if degree else 0.5
        out.append(RawEdu(inst, degree, yr, text, conf))
    return out
