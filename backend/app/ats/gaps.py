"""Análisis de brechas: qué pide la oferta vs. qué hay realmente en el perfil."""
import re

from app.ai.report import usable
from app.domain.job import Job
from app.domain.profile import Profile
from app.domain.tailoring import GapStatus, KeywordGap
from app.llm.client import LLMClient
from app.rag.retrieve import ProfileRetriever, _stems
from app.safety.integrity import UNTRUSTED_RULE, offer_block
from app.text import clean_title, contains, covered, norm

EXTRACT_SYS = """Extrae de una oferta de trabajo los requisitos que un filtro ATS buscaría:
SOLO habilidades técnicas duras: herramientas, tecnologías, certificaciones, estándares, metodologías, dominios técnicos.
Cada término debe ser corto (1 a 4 palabras), tal como aparece en la oferta (ej: "SIEM", "Splunk", "CEH", "MITRE ATT&CK", "Threat Hunting").
EXCLUYE: habilidades blandas, años de experiencia, estudios/carreras, beneficios, frases largas, el nombre del cargo.
Devuelve SOLO JSON: {"keywords": ["..."]}. Máximo 20, sin duplicados."""
EXTRACT_SYS += UNTRUSTED_RULE

ADJ_SYS = """Dado el perfil de un candidato y una lista de requisitos que NO aparecen literalmente,
decide cuáles son "adyacentes": el perfil demuestra experiencia real equivalente o muy cercana.
Para cada uno cita en "evidence" un fragmento EXACTO copiado del perfil. Si no hay evidencia real, no lo incluyas.
Devuelve SOLO JSON: {"adjacent": [{"keyword": "...", "evidence": "..."}]}"""


def is_named_term(kw: str) -> bool:
    """Producto, tecnología o certificación con nombre propio ('Darktrace', 'CEH', 'SC-200'): o se tiene o no se tiene.
    No existe 'experiencia parecida a Darktrace'; reescribir otra herramienta con ese nombre sería afirmar algo falso."""
    return " " not in kw.strip() and "/" not in kw and kw[:1].isupper() and re.fullmatch(r"[A-Za-z0-9+#.\-]+", kw.strip()) is not None


def profile_corpus(p: Profile) -> str:
    parts = [p.headline, p.summary, *[s.name for s in p.skills], *p.certifications]
    for e in p.experience:
        parts += [e.title, e.company, *e.bullets]
    return "\n".join(x for x in parts if x)


def extract_requirements(job: Job, llm: LLMClient | None, profile: Profile | None = None, corpus: list[str] | None = None) -> list[str]:
    """Requisitos duros de la oferta. Con LLM disponible lo usa; sin él (modo none, sin consentimiento, presupuesto agotado)
    cae al extractor determinista (léxico + patrones + secciones + TF-IDF contra `corpus` + vocabulario del perfil)."""
    llm = usable(llm)
    if llm is None:
        from app.ai.requirements import extract_requirements_det
        vocab = [s.name for s in profile.skills] + list(profile.certifications) if profile else None
        return extract_requirements_det(job, vocab, corpus)
    data = llm.complete_json(EXTRACT_SYS, offer_block(job, 6000))
    seen: dict[str, str] = {}
    for k in [clean_title(job.title), *data.get("keywords", [])]:
        if isinstance(k, str) and k.strip() and len(k.split()) <= 5:
            seen.setdefault(norm(k), k.strip())
    return list(seen.values())[:21]


# Raíces demasiado genéricas para sostener por sí solas una equivalencia ('gestión de proyectos' != 'gestión de camas')
_GENERIC_STEMS = {"gesti", "admin", "manej", "exper", "conoc", "anali", "desar", "traba", "apoyo", "coord", "elabo", "reali", "manag", "devel",
                  "suppo", "analy", "know", "works", "equip", "proce", "servi", "clien", "atenc", "contr", "plani", "orga", "prepa", "parti"}
ADJ_MIN_SCORE = 0.35      # puntaje mínimo de recuperación (coseno + contexto + léxico) para aceptar una evidencia sin LLM


def _profile_leaves(p: Profile) -> list[str]:
    leaves = [s.name for s in p.skills] + list(p.certifications)
    for e in p.experience:
        leaves += list(e.bullets)
    leaves += [x.strip() for x in re.split(r"(?<=[.!?])\s+", p.summary) if x.strip()]
    return [x for x in leaves if x]


_TOK_STOP = {"de", "del", "la", "el", "en", "y", "a", "los", "las", "con", "para", "por", "un", "una", "the", "and", "of", "to", "in", "for", "or", "with"}


def _content_tokens(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9+#&]{2,}", norm(s)) if t not in _TOK_STOP]


def _tok_match(a: str, b: str) -> bool:
    """Misma palabra o misma raíz larga ('incidentes'~'incidents'), pero NO falsos amigos ('hemodiálisis' != 'hemodinámica')."""
    if a == b:
        return True
    common = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        common += 1
    return common >= max(6, round(0.75 * min(len(a), len(b))))


def _det_adjacent(rest: list[str], profile: Profile, retriever: ProfileRetriever | None, corpus_n: str) -> dict[str, str]:
    """ADJACENT sin LLM: recupera evidencia literal del perfil (embeddings locales/hash) y solo la acepta si
    (1) al menos la mitad de las palabras del requisito aparecen (misma palabra o raíz larga) en esa evidencia y alguna no es genérica,
    (2) ninguna sigla/marca del requisito falta en el perfil ('SAP CRM' no se respalda con 'CRM Salesforce') y
    (3) el puntaje de recuperación supera un umbral. Los nombres propios de una palabra (herramientas/certificaciones) nunca."""
    cand = [k for k in rest if not is_named_term(k)]
    if not cand:
        return {}
    corpus_tokens = set(re.findall(r"[a-z0-9+#&]+", corpus_n))
    if retriever is not None:
        found = [[(e.text, e.score) for e in evs] for evs in retriever.evidence_many(cand, 3)]
    else:                                                    # sin árbol de embeddings: solo solapamiento léxico
        leaves = _profile_leaves(profile)
        found = []
        for k in cand:
            ks = _stems(k)
            found.append(sorted(((t, len(ks & _stems(t)) / len(ks) if ks else 0.0) for t in leaves), key=lambda x: -x[1])[:3])
    out: dict[str, str] = {}
    for k, evs in zip(cand, found, strict=True):
        toks = _content_tokens(k)
        brands = [norm(t) for t in re.findall(r"[A-Za-z0-9+#&]*[A-Z][A-Za-z0-9+#&]*[A-Z0-9][A-Za-z0-9+#&]*|\b[A-Z]{2,}\b", k)]
        if not toks or any(b not in corpus_tokens for b in brands):
            continue
        for text, score in evs:
            ev = _content_tokens(text)
            hits = [t for t in toks if any(_tok_match(t, e) for e in ev)]
            if (len(hits) / len(toks) < 0.5 or not any(t[:5] not in _GENERIC_STEMS for t in hits)
                    or score < ADJ_MIN_SCORE or norm(text) not in corpus_n):
                continue
            out[k] = text
            break
    return out


def _rag_prompt(rest: list[str], retriever: ProfileRetriever, k: int = 3) -> tuple[str, dict[str, str]]:
    """Prompt corto: por requisito, solo las k hojas más cercanas del árbol del perfil (con su contexto)."""
    lines = ["Cada requisito trae las evidencias candidatas del perfil (fragmentos literales)."]
    cands: dict[str, str] = {}
    for kw, evs in zip(rest, retriever.evidence_many(rest, k), strict=True):
        lines.append(f"\nREQUISITO: {kw}")
        for e in evs:
            lines.append(f'  - "{e.text}"' + (f"  [contexto: {e.context[:140]}]" if e.context and e.context != e.text else ""))
        cands[kw] = " | ".join(norm(e.text) for e in evs)
    return "\n".join(lines), cands


def classify(keywords: list[str], profile: Profile, llm: LLMClient | None, confirmed: set[str] | None = None, target_title: str | None = None,
             retriever: ProfileRetriever | None = None) -> list[KeywordGap]:
    llm = usable(llm)
    corpus = profile_corpus(profile)
    corpus_n = norm(corpus)
    confirmed_n = {norm(c) for c in (confirmed or set())}
    gaps: list[KeywordGap] = []
    rest: list[str] = []
    for k in keywords:
        if target_title and norm(k) == norm(clean_title(target_title)) and not contains(corpus_n, k):
            # El cargo objetivo no es una habilidad declarada: va en el titular, respaldado por el
            # rol propio del candidato. El candidato revisa y aprueba el CV final.
            gaps.append(KeywordGap(keyword=k, status=GapStatus.ADJACENT, evidence=profile.headline or None))
        elif covered(corpus_n, k):
            gaps.append(KeywordGap(keyword=k, status=GapStatus.HAVE))
        else:
            rest.append(k)
    adjacent: dict[str, str] = {}
    if rest and llm is None:                 # sin LLM: HAVE y ADJACENT por reglas + evidencia literal; lo demás, MISSING
        adjacent = _det_adjacent(rest, profile, retriever, corpus_n)
    elif rest:
        candidates: dict[str, str] = {}      # requisito -> texto normalizado de sus evidencias recuperadas
        if retriever is not None:
            prompt, candidates = _rag_prompt(rest, retriever)
        else:
            prompt = f"PERFIL:\n{corpus}\n\nREQUISITOS:\n" + "\n".join(rest)
        data = llm.complete_json(ADJ_SYS, prompt)
        for a in data.get("adjacent", []):
            ev, kw = a.get("evidence", ""), a.get("keyword", "")
            # Guardia anti-alucinación: la evidencia debe existir de verdad en el perfil
            # (y, con RAG, además debe ser una de las hojas recuperadas para ese requisito).
            if ev and norm(ev) in corpus_n and kw in rest and not is_named_term(kw) and (retriever is None or norm(ev) in candidates.get(kw, "")):
                adjacent[kw] = ev
    for k in rest:
        if k in adjacent:
            gaps.append(KeywordGap(keyword=k, status=GapStatus.ADJACENT, evidence=adjacent[k]))
        else:
            gaps.append(KeywordGap(keyword=k, status=GapStatus.MISSING, confirmed=norm(k) in confirmed_n))
    return gaps


def match_score(gaps: list[KeywordGap]) -> int:
    """0-100: HAVE vale 1, ADJACENT 0.6, MISSING 0 (0.5 si el usuario lo confirmó)."""
    if not gaps:
        return 0
    w = {GapStatus.HAVE: 1.0, GapStatus.ADJACENT: 0.6}
    pts = sum(w.get(g.status, 0.5 if g.confirmed else 0.0) for g in gaps)
    return round(100 * pts / len(gaps))
