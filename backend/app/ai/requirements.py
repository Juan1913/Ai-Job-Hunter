"""Extracción de requisitos de una oferta SIN LLM.

Señales (cada una suma al puntaje de un término candidato):
  - léxico por familia profesional (app/ai/lexicon.py)
  - vocabulario del propio perfil (habilidades/certificaciones del candidato que la oferta menciona)
  - patrones: siglas, herramientas CamelCase, símbolos (C++, CI/CD, Node.js), secuencias en Mayúscula Inicial (Palo Alto Cortex XSOAR)
  - secciones 'requisitos / requirements / se valora...' y frases gatillo ('manejo de', 'experience with', 'is a plus')
  - n-gramas TF-IDF contra un corpus local de ofertas (las demás ofertas del usuario) para premiar lo distintivo
Con tope y limpieza: nada de habilidades blandas, beneficios ni frases largas."""
from __future__ import annotations

import math
import re
from collections import Counter

from app.ai.lexicon import NOT_SKILLS, SECTION_HEADS, STOP_SECTIONS, TERMS
from app.domain.job import Job
from app.text import clean_title, contains, norm

MAX_TERMS = 20
THRESHOLD = 2.0

_STOP = set("""de del la el en y e o u a al los las con para por un una unos unas que se su sus es son ser como más mas muy sobre entre sin
the and or of to in for with a an is are be as at by on from that this these those your you we our their it its into over under will can may
también tambien así asi ya pero si no lo le les""".split())
_TRIGGER = re.compile(r"(?i)\b(?:looking for|seeking|buscamos|busca |con experiencia|requiere[n]?|requisitos?|se requiere|manejo (?:de|del|avanzado de)|dominio (?:de|del)|conocimientos? (?:de|del|en|sobre)|experiencia (?:con|en|de|previa en|comprobable en)|"
                      r"experience (?:with|in|using|of)|knowledge of|familiar(?:ity)? with|proficien(?:t|cy) (?:in|with)|hands-on|expertise in|background in|skilled in|"
                      r"se valora|se valoran|deseable|valorable|nice to have|is a plus|are a plus|a plus|preferred|must have|required|obligatorio|certificaci[oó]n|certified|certification)")
_PREFIX = re.compile(r"(?i)^(?:(?:se\s+)?requiere[n]?|requisitos?|manejo(?:\s+avanzado)?|dominio|conocimientos?|experiencia(?:\s+previa|\s+comprobable)?|experience|knowledge|familiarity|"
                     r"proficiency|expertise|background|hands-on|strong|solid|s[oó]lid[oa]s?|buen[oa]s?|avanzad[oa]s?|excelente|deseable|se\s+valora[n]?|valorable|nice to have|"
                     r"with|using|in|of|con|en|de|del|sobre|y|e|o|and|or|the|a|an|el|la|los|las|un|una|conocimiento)\b[\s:,\-]*")
_SUFFIX = re.compile(r"(?i)[\s,;:.\-]*(?:(?:experience|experiencia|skills|knowledge|conocimientos?|expertise|y|and)|(?:es|son|is|are)\s+(?:un|una|a)\s+(?:plus|valor|ventaja)|is a plus|are a plus|a plus|required|requerid[oa]s?|deseable[s]?|valorable[s]?|obligatori[oa]s?|etc\.?)$")
_ACRO = re.compile(r"\b[A-Z][A-Z0-9&+#/.\-]{1,9}\b")
_CAMEL = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+\b|\b[A-Za-z]+[+#]{1,2}(?![\w])|\b[A-Za-z]+\.(?:js|net|io)\b|\b[A-Za-z]{2,}/[A-Za-z]{2,}\b")
_CT = r"[A-ZÁÉÍÓÚÑ][\wáéíóúñ&+#\-]*(?:\.[\wáéíóúñ]+)*"
_CAPSEQ = re.compile(rf"\b{_CT}(?:\s+(?:{_CT}|de|del|of)){{0,3}}")
_COMMON_ACRO = {"US", "UK", "EU", "CV", "RRHH", "HR", "ID", "OK", "PM", "AM", "NA", "TBD", "SA", "SAS", "LTDA", "INC", "LLC", "SL", "CEO", "COO", "CFO", "CTO", "VP", "II", "III", "IV", "EEUU", "USA", "COP", "USD", "EUR", "MXN"}
_GENERIC_CAP = {norm(w) for w in """Requisitos Requisito Buscamos Ofrecemos Experiencia Conocimientos Manejo Dominio Se Si Nuestro Nuestra Somos Somo Trabajar Únete Unete Somos We Our You Your
The This If As Knowledge Strong Nice Required Must Responsabilidades Funciones Beneficios Perfil Formación Estudios Inglés Ingles Español Spanish English Remote Remoto Híbrido Hibrido
Colombia México Mexico Perú Peru Chile España Spain Argentina Bogotá Medellín Madrid Barcelona Valencia London Austin Cali Lima Santiago Latam LATAM Deseable Valorable Se valora
Importante Además Ademas Tendrás Tendras Serás Seras Contrato Salario Horario Jornada Lunes Viernes Empresa Compañía Equipo Team Job Role Position Puesto Cargo""".split()}
_SENT = re.compile(r"(?<=[.!?;])\s+|\n+|•|·|•")
_BULLET = re.compile(r"^[\s\-–—*•·▪●◦\d.)]+")


def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9+#&]+", norm(s))


def _is_noise(term: str) -> bool:
    toks = _tokens(term)
    if not toks or len(term.strip()) < 2 or len(toks) > 4:
        return True
    if all(t in _STOP or t in NOT_SKILLS or t.isdigit() for t in toks):
        return True
    if toks[0] in _STOP or toks[-1] in _STOP:
        return True
    if toks[0] in NOT_SKILLS and toks[0] not in TERMS:
        return True
    if re.search(r"\d", term) and not re.search(r"[A-Za-z]", term):
        return True
    return False


def _zones(text: str) -> tuple[list[str], list[str]]:
    """(líneas de secciones de requisitos, frases con gatillo). Si no hay ninguna, el llamador usa todo el texto."""
    in_req, req_lines, trig = False, [], []
    for raw in re.split(r"\n+", text):
        line = _BULLET.sub("", raw).strip()
        if not line:
            continue
        ln = norm(line).rstrip(":").strip()
        head = next((h for h in SECTION_HEADS if ln.startswith(h)), None)
        stop = next((h for h in STOP_SECTIONS if ln.startswith(norm(h))), None)
        if stop and len(ln) < 60:
            in_req = False
            continue
        if head and (len(ln) < 60 or ":" in line[: len(head) + 3]):
            in_req = True
            rest = line.split(":", 1)[1].strip() if ":" in line else ""
            if rest:
                req_lines.append(rest)
            continue
        if in_req:
            req_lines.append(line)
        for sent in _SENT.split(line):
            if _TRIGGER.search(sent):
                trig.append(sent.strip())
    return req_lines, trig


def _chunks(segment: str) -> list[str]:
    """Trozos candidatos de una frase: se parte por comas/'y'/'/' y se limpian prefijos gatillo ('manejo de') y colas."""
    out = []
    seg = re.sub(r"(?i)\b(?:requisitos?|requirements?|se valora[n]?|deseable|nice to have)\s*:", ",", segment)
    seg = re.sub(r"(?i)\b(?:Requiere|Se requiere|Buscamos|We are looking for)\b[^,:;]*?\b(?:con|with)\b", ",", seg)
    for part in re.split(r"[,;:()]|\.(?=\s|$)|\s+(?:y|e|o|u|and|or|y/o|and/or)\s+|\s+/\s+|\s+&\s+", seg):
        p = part.strip(" .-–")
        prev = None
        while p and p != prev:                       # quita varios prefijos encadenados ('experiencia previa en ...')
            prev, p = p, _PREFIX.sub("", p).strip()
        p = _SUFFIX.sub("", p).strip(" .-–")
        if p and not _is_noise(p):
            out.append(p)
    return out


def _display(term: str, text: str) -> str:
    """Forma de escritura: nombres propios de una palabra en su forma canónica; el resto como la escribe la oferta."""
    canon = TERMS.get(norm(term))
    if canon and (" " not in canon or canon[:1].isupper() and canon != canon.capitalize()):
        return canon
    m = re.search(rf"(?i)(?<![\w]){re.escape(term)}(?![\w])", text)
    if m:
        got = m.group(0)
        return canon if (canon and got.islower() and canon != canon.lower() and " " not in canon) else got
    return canon or term


class _Idf:
    def __init__(self, corpus: list[str] | None):
        self.n = len(corpus or [])
        self.df: Counter = Counter()
        for doc in corpus or []:
            self.df.update(set(self._grams(doc)))

    @staticmethod
    def _grams(doc: str, nmax: int = 3) -> list[str]:
        toks = _tokens(doc)
        return [" ".join(toks[i:i + n]) for n in range(1, nmax + 1) for i in range(len(toks) - n + 1)]

    def idf(self, gram: str) -> float:
        return math.log((self.n + 1) / (self.df.get(gram, 0) + 1)) + 1.0


def extract_requirements_det(job: Job, vocab: list[str] | None = None, corpus: list[str] | None = None, *,
                             use_lexicon: bool = True, limit: int = MAX_TERMS) -> list[str]:
    """Lista de requisitos duros de la oferta (el cargo limpio va primero, como en la ruta con LLM)."""
    text = (job.description or "")[:8000]
    title = clean_title(job.title)
    full = f"{job.title}\n{text}"
    full_n = norm(full)
    req_lines, trig = _zones(text)
    zone_text = "\n".join([*req_lines, *trig]) or text
    zone_n = norm(zone_text)
    scores: dict[str, float] = {}
    shown: dict[str, str] = {}

    def add(term: str, w: float) -> None:
        term = term.strip(" .,;:-–()")
        if _is_noise(term) or norm(term) == norm(title):
            return
        k = norm(term)
        scores[k] = scores.get(k, 0.0) + w
        shown.setdefault(k, term)

    # léxico
    if use_lexicon:
        for low, canon in TERMS.items():
            if contains(full_n, low):
                add(_display(canon, full), 3.0)
    # vocabulario del perfil
    for v in vocab or []:
        if v and contains(full_n, v) and not _is_noise(v):
            add(_display(v, full), 2.5)
    # patrones
    for m in _ACRO.finditer(zone_text if zone_text != text else full):
        a = m.group(0).strip(".-/")
        if a not in _COMMON_ACRO and len(a) >= 2 and not a.isdigit():
            add(a, 2.0)
    for m in _CAMEL.finditer(full):
        add(m.group(0), 2.0)
    for m in _CAPSEQ.finditer(zone_text if zone_text != text else full):
        seq = m.group(0).strip(" .,;:")
        toks = seq.split()
        while toks and (norm(toks[0]) in _GENERIC_CAP or toks[0].lower() in ("de", "del", "of")):
            toks.pop(0)
        while toks and (norm(toks[-1]) in _GENERIC_CAP or toks[-1].lower() in ("de", "del", "of")):
            toks.pop()
        if toks and not (len(toks) == 1 and (toks[0].isupper() and len(toks[0]) <= 1)):
            seq = " ".join(toks)
            if len(toks) >= 2 or (len(toks[0]) > 3 and not m.start() == 0 and zone_text != text):
                add(seq, 1.5 if len(toks) >= 2 else 1.0)
    # secciones y frases gatillo: trozos de listas
    for seg in [*req_lines, *trig]:
        for c in _chunks(seg):
            add(c, 1.5 if seg in req_lines else 1.0)
    # TF-IDF de n-gramas (premia lo distintivo frente a otras ofertas del usuario)
    idf = _Idf(corpus)
    grams = Counter(g for g in _Idf._grams(zone_text) if g.count(" ") <= 2)
    best = 0.0
    tf: dict[str, float] = {}
    for g, c in grams.items():
        if g in scores:
            tf[g] = (1 + math.log(c)) * idf.idf(g)
            best = max(best, tf[g])
    for g, v in tf.items():
        scores[g] += 1.5 * v / best if best else 0.0
    # repeticiones y presencia en la zona de requisitos
    for k in list(scores):
        n = len(re.findall(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", full_n))
        scores[k] += 0.3 * min(max(n - 1, 0), 3)
        if contains(zone_n, k):
            scores[k] += 1.0

    ranked = sorted((k for k, v in scores.items() if v >= THRESHOLD), key=lambda k: (-scores[k], full_n.find(k)))
    chosen: list[str] = []
    for k in ranked:
        # un término que solo aparece dentro de otro más largo ya elegido ('SAP' dentro de 'SAP CRM') sobra
        redundant = False
        for o in chosen:
            if len(o) > len(k) and contains(o, k):
                masked = re.sub(rf"(?<![a-z0-9]){re.escape(o)}(?![a-z0-9])", " ", full_n)
                if not contains(masked, k):
                    redundant = True
                    break
        if not redundant:
            chosen.append(k)
        if len(chosen) >= limit:
            break
    # si un término largo elegido después deja sin sentido a uno corto ya elegido, se quita el corto
    final = [k for k in chosen if not any(len(o) > len(k) and contains(o, k) and not contains(re.sub(
        rf"(?<![a-z0-9]){re.escape(o)}(?![a-z0-9])", " ", full_n), k) for o in chosen)]
    return [title, *[shown[k] for k in final]][: limit + 1]
