"""Expansión de consulta multilingüe y por rol.

De 'analista SOC' genera variantes ES/EN/PT y sinónimos ('SOC analyst', 'analista de ciberseguridad',
'security analyst'...) con un diccionario CURADO y VERSIONADO por familia profesional. Funciona sin red ni LLM; la
expansión por LLM es opcional (`JH_SCOUT_LLM_EXPAND=true`), se valida/sanea y se cachea en BD (tabla QueryExpansion).

Cómo las usa cada fuente (ver `SearchQuery.variants` y `SearchQuery.request_budget`):
  * boolean  -> UNA consulta con operadores:  ("analista SOC" OR "SOC analyst")   [Indeed, LinkedIn]
  * multi    -> varias consultas, como máximo `request_budget` distintas por fuente/país  [portales con slug, APIs con `q`]
  * local    -> la fuente devuelve todo y se filtra en local con CUALQUIERA de los términos  [feeds remotos, ATS de empresa]
La deduplicación entre variantes la hace cada fuente (por URL) y el servicio entre fuentes (dedup_key)."""
import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass, field

from app.domain.job import SearchQuery

log = logging.getLogger(__name__)

DICTIONARY_VERSION = "2026.11.04"
MAX_VARIANTS = 8

# Cada grupo = términos EQUIVALENTES por idioma. Un término de varias palabras casa si todas sus palabras
# aparecen en la consulta (en cualquier orden); uno de una sola palabra debe ser distintivo.
_G = dict   # azúcar: _G(es=[...], en=[...], pt=[...])

FAMILIES: dict[str, list[dict[str, list[str]]]] = {
    "tecnologia_seguridad": [
        _G(es=["analista soc", "analista de ciberseguridad", "analista de seguridad informatica", "analista de seguridad de la informacion"],
           en=["soc analyst", "security analyst", "cybersecurity analyst", "information security analyst"], pt=["analista de seguranca da informacao", "analista soc"]),
        _G(es=["ingeniero de ciberseguridad", "especialista en ciberseguridad", "ingeniero de seguridad informatica"],
           en=["cybersecurity engineer", "security engineer", "information security engineer"], pt=["engenheiro de ciberseguranca"]),
        _G(es=["pentester", "analista de pentesting", "hacker etico"], en=["penetration tester", "pentester", "ethical hacker", "red team"], pt=["analista de pentest"]),
        _G(es=["analista de respuesta a incidentes", "analista dfir", "respuesta a incidentes"], en=["incident responder", "incident response analyst", "dfir analyst"]),
        _G(es=["oficial de seguridad de la informacion", "ciso", "responsable de seguridad"], en=["ciso", "information security officer", "head of security"]),
        _G(es=["desarrollador de software", "programador", "ingeniero de software", "desarrollador"], en=["software developer", "software engineer", "programmer"],
           pt=["desenvolvedor de software", "engenheiro de software", "programador"]),
        _G(es=["desarrollador backend", "desarrollador back end", "programador backend"], en=["backend developer", "backend engineer", "back end developer"], pt=["desenvolvedor backend"]),
        _G(es=["desarrollador frontend", "desarrollador front end", "programador frontend"], en=["frontend developer", "frontend engineer", "front end developer"], pt=["desenvolvedor frontend"]),
        _G(es=["desarrollador full stack", "desarrollador fullstack"], en=["full stack developer", "fullstack developer", "full stack engineer"], pt=["desenvolvedor full stack"]),
        _G(es=["desarrollador movil", "desarrollador de aplicaciones moviles"], en=["mobile developer", "android developer", "ios developer"], pt=["desenvolvedor mobile"]),
        _G(es=["ingeniero devops", "ingeniero de infraestructura", "ingeniero sre"], en=["devops engineer", "site reliability engineer", "sre", "platform engineer"], pt=["engenheiro devops"]),
        _G(es=["ingeniero cloud", "arquitecto cloud", "arquitecto de soluciones en la nube"], en=["cloud engineer", "cloud architect", "solutions architect"], pt=["engenheiro cloud"]),
        _G(es=["analista qa", "ingeniero qa", "probador de software", "analista de pruebas"], en=["qa engineer", "qa analyst", "software tester", "test engineer"], pt=["analista de qualidade", "analista qa"]),
        _G(es=["soporte tecnico", "analista de soporte ti", "mesa de ayuda", "help desk"], en=["it support", "technical support", "help desk analyst", "service desk"], pt=["suporte tecnico", "analista de suporte"]),
        _G(es=["administrador de sistemas", "administrador de redes", "ingeniero de redes"], en=["system administrator", "sysadmin", "network engineer", "network administrator"], pt=["administrador de sistemas", "engenheiro de redes"]),
        _G(es=["administrador de bases de datos", "dba"], en=["database administrator", "dba"], pt=["administrador de banco de dados"]),
        _G(es=["disenador ux", "disenador ui", "disenador de producto digital", "disenador ux ui"], en=["ux designer", "ui designer", "product designer", "ux ui designer"], pt=["designer ux", "designer de produto"]),
        _G(es=["gerente de producto", "product owner", "dueno de producto"], en=["product manager", "product owner"], pt=["gerente de produto"]),
        _G(es=["lider tecnico", "jefe de desarrollo", "gerente de tecnologia"], en=["tech lead", "engineering manager", "technical lead"], pt=["lider tecnico"]),
    ],
    "datos": [
        _G(es=["analista de datos", "analista de informacion"], en=["data analyst", "business data analyst"], pt=["analista de dados"]),
        _G(es=["cientifico de datos", "data scientist"], en=["data scientist", "applied scientist"], pt=["cientista de dados"]),
        _G(es=["ingeniero de datos", "ingeniero de big data"], en=["data engineer", "big data engineer", "etl developer"], pt=["engenheiro de dados"]),
        _G(es=["analista de inteligencia de negocios", "analista bi", "desarrollador bi"], en=["bi analyst", "business intelligence analyst", "bi developer"], pt=["analista de bi", "analista de business intelligence"]),
        _G(es=["ingeniero de machine learning", "ingeniero de aprendizaje automatico", "ingeniero ml"], en=["machine learning engineer", "ml engineer", "ai engineer"], pt=["engenheiro de machine learning"]),
        _G(es=["analista de analitica", "analista de analytics", "analista web analytics"], en=["analytics analyst", "product analyst", "web analyst"], pt=["analista de analytics"]),
    ],
    "ventas": [
        _G(es=["ejecutivo de ventas", "asesor comercial", "vendedor", "representante de ventas"], en=["sales executive", "sales representative", "sales associate", "salesperson"], pt=["executivo de vendas", "representante de vendas", "vendedor"]),
        _G(es=["gerente de ventas", "jefe de ventas", "director comercial"], en=["sales manager", "head of sales", "sales director"], pt=["gerente de vendas", "gerente comercial"]),
        _G(es=["ejecutivo de cuentas", "gerente de cuentas", "key account manager"], en=["account executive", "account manager", "key account manager"], pt=["executivo de contas", "gerente de contas"]),
        _G(es=["desarrollador de negocios", "gerente de desarrollo de negocios", "ejecutivo de desarrollo de negocios"], en=["business developer", "business development manager", "business development representative"], pt=["desenvolvimento de negocios"]),
        _G(es=["representante de desarrollo de ventas", "prospector de ventas", "sdr"], en=["sales development representative", "sdr", "bdr", "lead generation specialist"]),
        _G(es=["asesor de ventas telefonicas", "televentas", "ejecutivo telemarketing"], en=["telesales", "inside sales representative", "telemarketer"], pt=["televendas", "operador de telemarketing"]),
        _G(es=["ejecutivo de exito del cliente", "customer success"], en=["customer success manager", "csm"], pt=["customer success"]),
    ],
    "finanzas_contabilidad": [
        _G(es=["contador", "contador publico", "contable"], en=["accountant", "public accountant", "cpa"], pt=["contador", "contabilista"]),
        _G(es=["auxiliar contable", "asistente contable", "auxiliar de contabilidad"], en=["accounting assistant", "accounting clerk", "bookkeeper"], pt=["auxiliar contabil", "assistente contabil"]),
        _G(es=["analista contable", "analista de contabilidad"], en=["accounting analyst", "staff accountant"], pt=["analista contabil"]),
        _G(es=["analista financiero", "analista de finanzas"], en=["financial analyst", "finance analyst", "fp&a analyst"], pt=["analista financeiro"]),
        _G(es=["auditor", "auditor interno", "auditor externo"], en=["auditor", "internal auditor", "external auditor"], pt=["auditor", "auditor interno"]),
        _G(es=["analista de cuentas por pagar", "auxiliar de cuentas por pagar"], en=["accounts payable analyst", "accounts payable clerk"], pt=["analista de contas a pagar"]),
        _G(es=["analista de cuentas por cobrar", "analista de cartera"], en=["accounts receivable analyst", "collections analyst"], pt=["analista de contas a receber"]),
        _G(es=["analista de nomina", "auxiliar de nomina"], en=["payroll analyst", "payroll specialist"], pt=["analista de folha de pagamento"]),
        _G(es=["analista de impuestos", "analista tributario", "especialista en impuestos"], en=["tax analyst", "tax accountant", "tax specialist"], pt=["analista tributario", "analista fiscal"]),
        _G(es=["tesorero", "analista de tesoreria"], en=["treasury analyst", "treasurer"], pt=["analista de tesouraria"]),
        _G(es=["controller", "controlador financiero", "gerente financiero"], en=["financial controller", "finance manager", "controller"], pt=["controller", "gerente financeiro"]),
    ],
    "salud": [
        _G(es=["enfermera", "enfermero", "auxiliar de enfermeria", "licenciada en enfermeria"], en=["nurse", "registered nurse", "nursing assistant"], pt=["enfermeiro", "enfermeira", "tecnico de enfermagem"]),
        _G(es=["medico general", "medico", "medico asistencial"], en=["physician", "general practitioner", "doctor"], pt=["medico", "medico clinico geral"]),
        _G(es=["odontologo", "dentista", "auxiliar odontologico"], en=["dentist", "dental assistant"], pt=["dentista", "cirurgiao dentista"]),
        _G(es=["psicologo", "psicologa", "psicologo clinico"], en=["psychologist", "clinical psychologist", "therapist"], pt=["psicologo", "psicologa"]),
        _G(es=["fisioterapeuta", "terapeuta fisico", "kinesiologo"], en=["physiotherapist", "physical therapist"], pt=["fisioterapeuta"]),
        _G(es=["farmaceutico", "quimico farmaceutico", "auxiliar de farmacia", "regente de farmacia"], en=["pharmacist", "pharmacy technician", "pharmacy assistant"], pt=["farmaceutico", "auxiliar de farmacia"]),
        _G(es=["nutricionista", "dietista"], en=["nutritionist", "dietitian"], pt=["nutricionista"]),
        _G(es=["tecnico de laboratorio", "bacteriologo", "analista de laboratorio clinico"], en=["lab technician", "medical laboratory technician", "laboratory analyst"], pt=["tecnico de laboratorio"]),
        _G(es=["cuidador de adultos mayores", "cuidador", "auxiliar geriatrico"], en=["caregiver", "home health aide", "care assistant"], pt=["cuidador de idosos"]),
    ],
    "educacion": [
        _G(es=["profesor", "docente", "maestro"], en=["teacher", "professor", "instructor"], pt=["professor", "docente"]),
        _G(es=["profesor de ingles", "docente de ingles", "maestro de ingles"], en=["english teacher", "esl teacher", "english instructor"], pt=["professor de ingles"]),
        _G(es=["tutor", "tutor academico", "profesor particular"], en=["tutor", "academic tutor"], pt=["tutor", "professor particular"]),
        _G(es=["coordinador academico", "director academico"], en=["academic coordinator", "academic director"], pt=["coordenador pedagogico"]),
        _G(es=["disenador instruccional", "disenador de cursos", "desarrollador de contenidos e-learning"], en=["instructional designer", "learning experience designer", "e-learning developer"], pt=["designer instrucional"]),
        _G(es=["capacitador", "facilitador de formacion", "instructor corporativo"], en=["corporate trainer", "training specialist", "learning and development specialist"], pt=["instrutor corporativo", "analista de treinamento"]),
    ],
    "logistica": [
        _G(es=["analista de logistica", "analista logistico", "coordinador de logistica"], en=["logistics analyst", "logistics coordinator", "supply chain analyst"], pt=["analista de logistica", "coordenador de logistica"]),
        _G(es=["auxiliar de bodega", "bodeguero", "operario de bodega", "almacenista"], en=["warehouse associate", "warehouse worker", "warehouse operator"], pt=["auxiliar de almoxarifado", "operador de armazem"]),
        _G(es=["conductor", "conductor de reparto", "repartidor", "mensajero"], en=["delivery driver", "driver", "courier"], pt=["motorista", "entregador"]),
        _G(es=["montacarguista", "operador de montacargas"], en=["forklift operator", "forklift driver"], pt=["operador de empilhadeira"]),
        _G(es=["jefe de almacen", "supervisor de bodega", "jefe de bodega"], en=["warehouse supervisor", "warehouse manager"], pt=["supervisor de armazem"]),
        _G(es=["analista de compras", "comprador", "coordinador de compras"], en=["procurement analyst", "buyer", "purchasing specialist"], pt=["analista de compras", "comprador"]),
        _G(es=["planificador de la demanda", "analista de planeacion de la demanda", "planeador de produccion"], en=["demand planner", "supply planner", "production planner"], pt=["planejador de demanda", "analista de pcp"]),
        _G(es=["coordinador de comercio exterior", "analista de comercio exterior", "agente aduanal"], en=["customs broker", "import export coordinator", "freight forwarder"], pt=["analista de comercio exterior"]),
    ],
    "marketing": [
        _G(es=["analista de marketing", "especialista en marketing", "asistente de marketing"], en=["marketing analyst", "marketing specialist", "marketing coordinator"], pt=["analista de marketing", "assistente de marketing"]),
        _G(es=["community manager", "gestor de redes sociales", "administrador de redes sociales"], en=["community manager", "social media manager", "social media specialist"], pt=["community manager", "social media"]),
        _G(es=["especialista seo", "analista seo"], en=["seo specialist", "seo analyst"], pt=["analista de seo", "especialista em seo"]),
        _G(es=["especialista en publicidad digital", "gestor de pauta", "analista de performance", "trafficker digital"], en=["performance marketing specialist", "ppc specialist", "paid media specialist", "digital advertising specialist"], pt=["analista de midia paga", "gestor de trafego"]),
        _G(es=["gerente de marketing", "jefe de marketing", "director de marketing"], en=["marketing manager", "head of marketing", "marketing director"], pt=["gerente de marketing"]),
        _G(es=["redactor de contenidos", "redactor publicitario", "copywriter", "creador de contenido"], en=["content writer", "copywriter", "content creator"], pt=["redator", "criador de conteudo"]),
        _G(es=["disenador grafico", "disenador publicitario"], en=["graphic designer", "visual designer"], pt=["designer grafico"]),
        _G(es=["especialista en email marketing", "analista de crm", "especialista de marketing automation"], en=["email marketing specialist", "crm marketing specialist", "marketing automation specialist"], pt=["analista de crm", "especialista em email marketing"]),
    ],
    "rrhh": [
        _G(es=["analista de recursos humanos", "analista de rrhh", "analista de gestion humana", "analista de talento humano"], en=["hr analyst", "human resources analyst", "hr generalist"], pt=["analista de recursos humanos", "analista de rh"]),
        _G(es=["reclutador", "analista de seleccion", "especialista en reclutamiento", "headhunter"], en=["recruiter", "talent acquisition specialist", "technical recruiter", "talent acquisition partner"], pt=["recrutador", "analista de recrutamento e selecao"]),
        _G(es=["generalista de recursos humanos", "asistente de recursos humanos", "auxiliar de recursos humanos"], en=["hr generalist", "hr assistant", "human resources coordinator"], pt=["generalista de rh", "assistente de rh"]),
        _G(es=["gerente de recursos humanos", "jefe de recursos humanos", "director de gestion humana"], en=["hr manager", "head of people", "human resources manager"], pt=["gerente de rh", "gerente de recursos humanos"]),
        _G(es=["analista de compensaciones y beneficios", "especialista en compensaciones"], en=["compensation and benefits analyst", "compensation analyst", "benefits specialist"], pt=["analista de remuneracao e beneficios", "analista de cargos e salarios"]),
        _G(es=["analista de bienestar", "analista de cultura organizacional", "analista de clima laboral"], en=["employee experience specialist", "people operations specialist", "culture specialist"], pt=["analista de endomarketing", "analista de clima organizacional"]),
    ],
    "ingenieria": [
        _G(es=["ingeniero civil", "residente de obra", "ingeniero de obra"], en=["civil engineer", "site engineer", "construction engineer"], pt=["engenheiro civil", "engenheiro de obras"]),
        _G(es=["ingeniero mecanico", "ingeniero de mantenimiento mecanico"], en=["mechanical engineer", "maintenance engineer"], pt=["engenheiro mecanico"]),
        _G(es=["ingeniero electrico", "ingeniero electricista"], en=["electrical engineer", "power engineer"], pt=["engenheiro eletricista", "engenheiro eletrico"]),
        _G(es=["ingeniero electronico", "ingeniero de automatizacion", "ingeniero de control"], en=["electronics engineer", "automation engineer", "controls engineer"], pt=["engenheiro de automacao", "engenheiro eletronico"]),
        _G(es=["ingeniero industrial", "analista de procesos", "ingeniero de procesos"], en=["industrial engineer", "process engineer", "process analyst"], pt=["engenheiro industrial", "engenheiro de processos"]),
        _G(es=["ingeniero quimico", "ingeniero de procesos quimicos"], en=["chemical engineer", "process chemical engineer"], pt=["engenheiro quimico"]),
        _G(es=["ingeniero ambiental", "especialista ambiental", "analista ambiental"], en=["environmental engineer", "environmental specialist"], pt=["engenheiro ambiental"]),
        _G(es=["ingeniero de calidad", "analista de calidad", "inspector de calidad"], en=["quality engineer", "quality analyst", "quality inspector"], pt=["engenheiro da qualidade", "analista de qualidade"]),
        _G(es=["ingeniero de seguridad y salud en el trabajo", "especialista sst", "coordinador hse", "prevencionista de riesgos"], en=["hse specialist", "ehs engineer", "occupational safety specialist", "safety engineer"], pt=["engenheiro de seguranca do trabalho", "tecnico de seguranca do trabalho"]),
        _G(es=["ingeniero de proyectos", "jefe de proyectos", "gerente de proyectos"], en=["project engineer", "project manager", "engineering project manager"], pt=["engenheiro de projetos", "gerente de projetos"]),
        _G(es=["tecnico de mantenimiento", "tecnico electromecanico", "operario de mantenimiento"], en=["maintenance technician", "electromechanical technician", "maintenance mechanic"], pt=["tecnico de manutencao", "mecanico de manutencao"]),
        _G(es=["arquitecto", "arquitecto proyectista"], en=["architect", "architectural designer"], pt=["arquiteto"]),
    ],
    "atencion_cliente": [
        _G(es=["asesor de servicio al cliente", "agente de servicio al cliente", "ejecutivo de atencion al cliente", "asesor de atencion al cliente"], en=["customer service representative", "customer service agent", "customer support representative", "customer care agent"], pt=["atendente de atendimento ao cliente", "analista de atendimento ao cliente", "atendente"]),
        _G(es=["agente de call center", "asesor de call center", "operador de call center", "teleoperador"], en=["call center agent", "contact center agent", "call center representative"], pt=["operador de call center", "atendente de call center", "operador de telemarketing"]),
        _G(es=["asesor de soporte al cliente", "especialista de soporte", "agente de soporte"], en=["customer support specialist", "support agent", "technical support specialist"], pt=["analista de suporte ao cliente", "agente de suporte"]),
        _G(es=["supervisor de servicio al cliente", "coordinador de servicio al cliente", "lider de contact center"], en=["customer service supervisor", "customer service team lead", "contact center supervisor"], pt=["supervisor de atendimento", "coordenador de atendimento"]),
        _G(es=["recepcionista", "asistente de recepcion"], en=["receptionist", "front desk associate", "front desk agent"], pt=["recepcionista"]),
        _G(es=["asesor bilingue", "agente bilingue", "agente de servicio al cliente bilingue"], en=["bilingual customer service representative", "bilingual agent", "bilingual support specialist"], pt=["atendente bilingue"]),
    ],
}

# Léxico palabra a palabra (es <-> en) para roles que NO están en ningún grupo: 'contador junior' -> 'accountant junior'.
WORD_PAIRS = {
    "contador": "accountant", "ingeniero": "engineer", "desarrollador": "developer", "programador": "programmer", "analista": "analyst",
    "gerente": "manager", "jefe": "manager", "director": "director", "vendedor": "salesperson", "asistente": "assistant",
    "auxiliar": "assistant", "coordinador": "coordinator", "especialista": "specialist", "consultor": "consultant",
    "tecnico": "technician", "disenador": "designer", "abogado": "lawyer", "administrador": "administrator", "supervisor": "supervisor",
    "operario": "operator", "secretaria": "secretary", "chef": "chef", "cocinero": "cook", "mesero": "waiter", "cajero": "cashier",
    "arquitecto": "architect", "psicologo": "psychologist", "periodista": "journalist", "traductor": "translator", "investigador": "researcher",
}
_SENIORITY = {"junior": "junior", "jr": "junior", "senior": "senior", "sr": "senior", "ssr": "semi senior", "semisenior": "semi senior", "trainee": "trainee",
              "lead": "lead", "principal": "principal", "practicante": "intern", "pasante": "intern"}
_SENIORITY_ALT = {"junior": ["junior", "jr"], "senior": ["senior", "sr"]}
_STOP = {"de", "la", "el", "en", "y", "para", "con", "del", "a", "of", "the", "and", "for", "in", "to"}

FAMILY_LABELS = {
    "tecnologia_seguridad": "Tecnología y seguridad", "datos": "Datos", "ventas": "Ventas", "finanzas_contabilidad": "Finanzas y contabilidad",
    "salud": "Salud", "educacion": "Educación", "logistica": "Logística", "marketing": "Marketing", "rrhh": "Recursos humanos",
    "ingenieria": "Ingeniería", "atencion_cliente": "Atención al cliente",
}


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c))


def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9#+]+", _fold(s))


def _core_and_seniority(phrase: str) -> tuple[list[str], str | None]:
    toks = _tokens(phrase)
    sen = next((_SENIORITY[t] for t in toks if t in _SENIORITY), None)
    return [t for t in toks if t not in _SENIORITY], sen


# índice: (familia, grupo, idioma, término, tokens)
_INDEX: list[tuple[str, int, str, str, frozenset[str]]] = [
    (fam, gi, lang, term, frozenset(_tokens(term)))
    for fam, groups in FAMILIES.items() for gi, g in enumerate(groups) for lang, terms in g.items() for term in terms]


@dataclass
class Expansion:
    original: list[str]
    variants: list[str] = field(default_factory=list)
    family: str | None = None
    method: str = "literal"            # literal | dict | dict+llm
    version: str = DICTIONARY_VERSION

    @property
    def terms(self) -> list[str]:
        return [*self.original, *self.variants]


def _match_groups(core_tokens: set[str]) -> list[tuple[str, int]]:
    """Grupos cuyo algún término tiene TODAS sus palabras en la consulta; los más específicos (más palabras) primero."""
    best: dict[tuple[str, int], int] = {}
    for fam, gi, _lang, _term, toks in _INDEX:
        sig = {t for t in toks if t not in _STOP}
        if sig and sig <= core_tokens:
            best[(fam, gi)] = max(best.get((fam, gi), 0), len(sig))
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    if not ranked:
        return []
    top = ranked[0][1]
    return [k for k, n in ranked if n == top][:2]     # solo los de mayor especificidad: evita arrastrar ruido


def _with_seniority(term: str, lang: str, sen: str | None) -> str:
    if not sen:
        return term
    return f"{sen} {term}" if lang == "en" else f"{term} {sen}"


def _dict_variants(phrase: str, languages: list[str]) -> tuple[list[str], str | None]:
    core, sen = _core_and_seniority(phrase)
    if not core:
        return [], None
    groups = _match_groups(set(core))
    out: list[tuple[str, str]] = []
    family = None
    for fam, gi in groups:
        family = family or fam
        g = FAMILIES[fam][gi]
        for lang, terms in g.items():
            out += [(lang, t) for t in terms]
    if not out:                                           # sin grupo: traducción del NÚCLEO del rol (solo roles del léxico, frases simples)
        es2en, en2es = WORD_PAIRS, {v: k for k, v in WORD_PAIRS.items()}
        if not any(t in _STOP for t in core):             # con 'de/of...' el orden cambia y no se puede traducir sin diccionario: no se inventa
            if core[0] in es2en and len(core) <= 3:       # 'analista legal' -> 'legal analyst'
                out.append(("en", " ".join([*core[1:], es2en[core[0]]])))
            if core[-1] in en2es and len(core) <= 3:      # 'legal analyst' -> 'analista legal'
                out.append(("es", " ".join([en2es[core[-1]], *core[:-1]])))
    pref = [*languages, *[x for x in ("es", "en") if x not in languages]]      # pt solo si el usuario lo pidió
    by_lang = {l: [t for lg, t in out if lg == l] for l in pref}
    out = []
    while any(by_lang.values()):                                  # intercala idiomas: es, en, es, en... (el cupo es corto)
        for l in pref:
            if by_lang[l]:
                out.append((l, by_lang[l].pop(0)))
    seen_core = {" ".join(core)}
    res: list[str] = []
    for lang, term in out:
        if _fold(term) in seen_core:
            continue
        seen_core.add(_fold(term))
        res.append(_with_seniority(term, lang, sen))
    return res, family


def expand_keywords(keywords: list[str], languages: list[str] | None = None, max_variants: int = MAX_VARIANTS) -> Expansion:
    """Solo diccionario (sin red). Las originales van siempre primero y nunca se alteran."""
    langs = list(languages or [])
    original = [k.strip() for k in keywords if k and k.strip()]
    exp = Expansion(original=original)
    seen = {_fold(k) for k in original}
    # reparte el cupo entre las consultas del usuario (round-robin) para que ninguna acapare
    per_kw = [_dict_variants(k, langs) for k in original]
    exp.family = next((f for _, f in per_kw if f), None)
    pools = [list(v) for v, _ in per_kw]
    while len(exp.variants) < max_variants and any(pools):
        for pool in pools:
            while pool:
                v = pool.pop(0)
                if _fold(v) not in seen:
                    seen.add(_fold(v))
                    exp.variants.append(v)
                    break
            if len(exp.variants) >= max_variants:
                break
    if exp.variants:
        exp.method = "dict"
    return exp


# ---------------------------------------------------------------- expansión opcional por LLM (cacheada en BD)
_SAFE = re.compile(r"^[\w][\w\s#+./&'-]{1,58}$", re.U)
_OPERATORS = re.compile(r"\b(OR|AND|NOT)\b|[\"()<>{}\\]")


def sanitize_variants(raw, original: list[str], limit: int = 6) -> list[str]:
    """La salida de un LLM es entrada NO fiable: se aceptan solo frases cortas, sin operadores ni caracteres de control."""
    if not isinstance(raw, list):
        return []
    seen = {_fold(o) for o in original}
    out: list[str] = []
    for v in raw:
        if not isinstance(v, str):
            continue
        if re.search(r"[\x00-\x1f\x7f\u2028\u2029]", v):        # saltos de línea / control: típico de inyección de instrucciones
            continue
        v = " ".join(v.split())
        if not _SAFE.match(v) or _OPERATORS.search(v) or _fold(v) in seen:
            continue
        seen.add(_fold(v))
        out.append(v)
        if len(out) >= limit:
            break
    return out


def _cache_key(keywords: list[str], languages: list[str]) -> str:
    base = "|".join(sorted(_fold(k) for k in keywords)) + "#" + ",".join(sorted(languages)) + "#" + DICTIONARY_VERSION
    return hashlib.sha1(base.encode()).hexdigest()


_LLM_SYSTEM = ("Eres un experto en reclutamiento hispanohablante. Dado un cargo, devuelve JSON {\"variants\": [..]} con hasta 6 "
               "títulos de cargo equivalentes o muy cercanos que usarían las ofertas reales, en español, inglés y portugués "
               "según convenga. Solo títulos cortos de cargo: sin operadores, sin comillas, sin explicaciones.")


def llm_variants(keywords: list[str], languages: list[str] | None = None, *, client=None, session=None) -> list[str]:
    """Variantes del LLM con caché en BD. Nunca lanza: sin LLM o con error devuelve []."""
    langs = list(languages or [])
    key = _cache_key(keywords, langs)
    try:
        from sqlmodel import Session

        from app.db import QueryExpansion, engine
        own = session is None
        s = session or Session(engine)
        try:
            row = s.get(QueryExpansion, key)
            if row is not None:
                return sanitize_variants(row.variants, keywords)
            if client is None:
                from app.llm.factory import build_llm
                client = build_llm("fast")          # sin usuario: en modo api no hay consentimiento -> deshabilitado -> solo diccionario
            data = client.complete_json(_LLM_SYSTEM, f"Cargo(s): {'; '.join(keywords)}\nIdiomas del usuario: {', '.join(langs) or 'es'}")
            variants = sanitize_variants((data or {}).get("variants") if isinstance(data, dict) else data, keywords)
            s.add(QueryExpansion(key=key, keywords=" | ".join(keywords)[:300], variants=variants, model=getattr(client, "model", "") or ""))
            s.commit()
            return variants
        finally:
            if own:
                s.close()
    except Exception as e:  # noqa: BLE001 — la expansión es opcional: jamás rompe la búsqueda
        log.info("expansión por LLM no disponible (%s): se usa solo el diccionario", type(e).__name__)
        return []


def expand_query(q: SearchQuery, *, use_llm: bool | None = None, client=None) -> SearchQuery:
    """Copia de `q` con `variants` rellenas (idempotente: si ya vienen, o `expand=False`, no toca nada)."""
    if not q.expand or q.variants or not any(k.strip() for k in q.keywords):
        return q
    exp = expand_keywords(q.keywords, q.languages)
    if use_llm is None:
        from app.config import settings
        use_llm = settings.scout_llm_expand
    if use_llm:
        extra = llm_variants(q.keywords, q.languages, client=client)
        seen = {_fold(t) for t in exp.terms}
        exp.variants += [v for v in extra if _fold(v) not in seen][: max(0, MAX_VARIANTS + 2 - len(exp.variants))]
        if extra:
            exp.method = "dict+llm" if exp.method == "dict" else "llm"
    return q.model_copy(update={"variants": exp.variants})


# ---------------------------------------------------------------- uso por las fuentes
def query_terms(q: SearchQuery, limit: int | None = None) -> list[str]:
    """Consultas originales + variantes, sin repetir y con tope (`limit` o `request_budget`). Las originales siempre entran."""
    seen, out = set(), []
    for t in [*q.keywords, *q.variants]:
        t = (t or "").strip()
        if t and _fold(t) not in seen:
            seen.add(_fold(t))
            out.append(t)
    n = max(len([k for k in q.keywords if k.strip()]), limit if limit is not None else q.request_budget)
    return out[: max(1, n)] or [""]       # sin términos: una consulta vacía (feeds que devuelven todo)


def boolean_or(terms: list[str], max_terms: int = 4) -> str:
    """('analista SOC' OR "SOC analyst"): comillas en las frases, OR entre ellas. Una sola consulta (Indeed/LinkedIn)."""
    parts = [f'"{t}"' if " " in t else t for t in terms[:max_terms] if t.strip()]
    return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")" if parts else ""
