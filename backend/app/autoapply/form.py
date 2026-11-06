"""Motor genérico de formularios de postulación (Playwright). Rellena lo que reconoce con los datos del usuario y se DETIENE ante lo que no sabe:
nunca inventa una respuesta, nunca resuelve captchas, nunca marca casillas de marketing. Todo lo dudoso termina en «needs_manual»."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.autoapply.models import ApplyData

CONFIRM_RX = re.compile(r"thank you|thanks for appl|application (has been )?(received|submitted|sent)|we(?:'ve| have) received|gracias por (postular|aplicar|tu inter|enviar)|hemos recibido|recibimos tu|postulaci[oó]n (enviada|recibida)|solicitud (enviada|recibida)|candidatura (enviada|recibida)", re.I)
CAPTCHA_SEL = "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='turnstile'], .g-recaptcha, .h-captcha, [data-sitekey], .cf-turnstile"
APPLY_BTN_RX = re.compile(r"^(apply|apply now|apply for this job|postular|postularme|aplicar|aplicar ahora|solicitar)\b", re.I)
SUBMIT_RX = re.compile(r"submit|send application|apply|enviar|postular|aplicar|solicitar", re.I)


def _n(s: str | None) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()).strip()


# (clave, regex sobre la etiqueta normalizada) en orden de prioridad
RULES: list[tuple[str, re.Pattern]] = [
    ("first_name", re.compile(r"first.?name|given.?name|^nombres?$|^nombre\b(?!.*(completo|apellido))|primer nombre")),
    ("last_name", re.compile(r"last.?name|sur.?name|family.?name|apellidos?")),
    ("full_name", re.compile(r"^(full )?name\b|nombre completo|your name|nombre y apellidos?|^nombre$")),
    ("email", re.compile(r"e-?mail|correo")),
    ("phone", re.compile(r"phone|tel[eé]fono|telefono|celular|mobile|m[oó]vil|whatsapp")),
    ("linkedin", re.compile(r"linkedin")),
    ("github", re.compile(r"github")),
    ("portfolio", re.compile(r"portfolio|portafolio|website|sitio web|personal (site|page)|pagina web|url")),
    ("salary", re.compile(r"salary|salario|sueldo|pretensi|compensation|remuneraci")),
    ("notice", re.compile(r"notice|preaviso|disponibilidad|availability|when can you start|cuando puedes")),
    ("city", re.compile(r"location|city|ciudad|ubicaci|residen|where are you (based|located)|donde (vives|resides)")),
]
_FILE_CV = re.compile(r"resume|\bcv\b|curriculum|hoja de vida|curr[ií]culo")
_COVER = re.compile(r"cover|carta|motivaci|motivation|presentaci")
_PRIVACY = re.compile(r"privacy|privacidad|terms|t[eé]rminos|terminos|pol[ií]tica|politica|consent|consentimiento|gdpr|datos personales|personal data|acepto|i agree|i accept|data processing|tratamiento de datos")
_MARKETING = re.compile(r"marketing|newsletter|promo|boletin|bolet[ií]n|offers|ofertas|contact me about other|receive (emails|updates)")
_DECLINE = re.compile(r"prefer not|decline|no deseo|prefiero no|do not wish|rather not|no responder|not to (say|answer)")


@dataclass
class Applicant:
    full_name: str
    email: str
    phone: str = ""
    city: str = ""
    data: ApplyData = field(default_factory=ApplyData)
    cover_letter: str = ""

    @property
    def first(self) -> str:
        return (self.full_name.split() or [""])[0]

    @property
    def last(self) -> str:
        return " ".join(self.full_name.split()[1:])


@dataclass
class FormResult:
    status: str                                  # submitted | dry_run | needs_manual | unconfirmed | failed
    message: str = ""
    filled: list[str] = field(default_factory=list)
    unanswered: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    final_url: str = ""


def classify(label: str, a: Applicant) -> tuple[str, str] | None:
    """(clave, valor) para una etiqueta, o None si no se sabe qué poner. Las respuestas fijas del usuario mandan."""
    for qa in a.data.answers:
        try:
            if re.search(qa.pattern, label, re.I) or re.search(_n(qa.pattern), label):
                return "custom", qa.answer
        except re.error:
            continue
    values = {"first_name": a.first, "last_name": a.last or a.first, "full_name": a.full_name, "email": a.email, "phone": a.phone, "city": a.city or a.data.city or "",
              "linkedin": a.data.linkedin_url or "", "github": a.data.github_url or "", "portfolio": a.data.portfolio_url or a.data.github_url or "",
              "salary": a.data.expected_salary or "", "notice": a.data.notice_period or ""}
    for key, rx in RULES:
        if rx.search(label):
            return (key, values[key]) if values.get(key) else None
    return None


_JS_FIELDS = """() => {
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const labelOf = (e) => {
    const parts = [];
    if (e.labels) for (const l of e.labels) parts.push(l.innerText);
    parts.push(e.getAttribute('aria-label') || '', e.placeholder || '', e.getAttribute('aria-labelledby') ? (document.getElementById(e.getAttribute('aria-labelledby').split(' ')[0])||{}).innerText||'' : '');
    if (!parts.join('').trim()) { const p = e.closest('label, .field, .form-group, [class*=field], li, div'); if (p) parts.push(p.innerText.slice(0, 160)); }
    parts.push(e.name || '', e.id || '', e.getAttribute('autocomplete') || '');
    return parts.join(' | ');
  };
  const out = []; let i = 0;
  for (const e of document.querySelectorAll('input, textarea, select')) {
    const t = (e.type || e.tagName).toLowerCase();
    if (['hidden','submit','button','image','reset','search'].includes(t)) continue;
    if (t !== 'file' && t !== 'checkbox' && t !== 'radio' && !vis(e)) continue;
    e.setAttribute('data-jh', String(i));
    const req = e.required || e.getAttribute('aria-required') === 'true' || /\\*\\s*$/.test((e.labels && e.labels[0] ? e.labels[0].innerText : '').trim());
    out.push({ i, type: t, tag: e.tagName.toLowerCase(), label: labelOf(e), required: req, name: e.name || '', options: t === 'select' ? [...e.options].map(o => o.text) : [] });
    i++;
  }
  return out;
}"""


def run_form(url: str, a: Applicant, resume_path: str | None, *, dry_run: bool, timeout_s: int = 60, headless: bool = True) -> FormResult:
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright

    res = FormResult("failed")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            ctx = browser.new_context(locale="es-CO", viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            page.set_default_timeout(timeout_s * 1000)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(800)
            res.steps.append("Abrí la página de la oferta")
            if page.locator("form input:not([type=hidden]), form textarea").count() == 0:        # a veces hay que pulsar «Postular» para ver el formulario
                for el in page.locator("a, button").all()[:80]:
                    try:
                        if APPLY_BTN_RX.search((el.inner_text() or "").strip()) and el.is_visible():
                            el.click()
                            page.wait_for_timeout(1200)
                            res.steps.append("Pulsé el botón de postular para abrir el formulario")
                            break
                    except Exception:  # noqa: BLE001
                        continue
            if page.locator(CAPTCHA_SEL).count() > 0:
                return FormResult("needs_manual", "La página usa captcha: no lo resuelvo. Postula tú desde el enlace.", steps=res.steps, final_url=page.url)
            fields = page.evaluate(_JS_FIELDS)
            if not any(f["type"] != "checkbox" for f in fields):
                return FormResult("needs_manual", "No encontré un formulario de postulación en esa página (puede exigir iniciar sesión).", steps=res.steps, final_url=page.url)
            filled, unanswered = [], []
            for f in fields:
                loc = page.locator(f'[data-jh="{f["i"]}"]')
                label = _n(f["label"])
                t = f["type"]
                try:
                    if t == "file":
                        if _FILE_CV.search(label) and resume_path:
                            loc.set_input_files(resume_path)
                            filled.append("CV adjunto")
                        elif f["required"] and not _COVER.search(label):
                            unanswered.append(f["label"].split("|")[0].strip() or "archivo")
                        continue
                    if t == "checkbox":
                        if _MARKETING.search(label):
                            continue
                        if _PRIVACY.search(label):
                            if a.data.accept_privacy_notices:
                                loc.check(force=True)
                                filled.append("Aviso de privacidad aceptado")
                            elif f["required"]:
                                unanswered.append("aceptar el aviso de privacidad (actívalo en «Mis datos» si lo autorizas)")
                        elif f["required"]:
                            unanswered.append(f["label"].split("|")[0].strip() or "casilla obligatoria")
                        continue
                    if t == "radio":
                        continue
                    if t == "select":
                        opts = f["options"]
                        pick = next((o for o in opts if _DECLINE.search(_n(o))), None) if a.data.decline_demographics else None
                        hit = classify(label, a)
                        if hit and hit[0] == "custom":
                            pick = next((o for o in opts if _n(hit[1]) in _n(o)), None) or pick
                        if pick:
                            loc.select_option(label=pick)
                            filled.append(f"Seleccioné «{pick}»")
                        elif f["required"]:
                            unanswered.append(f["label"].split("|")[0].strip() or "lista obligatoria")
                        continue
                    if t == "textarea" and _COVER.search(label):
                        if a.cover_letter:
                            loc.fill(a.cover_letter)
                            filled.append("Carta de presentación")
                        elif f["required"]:
                            unanswered.append("carta de presentación")
                        continue
                    hit = classify(label, a)
                    if hit:
                        loc.fill(hit[1])
                        filled.append(hit[0] if hit[0] != "custom" else "respuesta fija")
                    elif f["required"]:
                        unanswered.append(f["label"].split("|")[0].strip()[:90] or "campo obligatorio")
                except Exception as e:  # noqa: BLE001 — un campo rebelde no tumba el resto: se anota
                    if f["required"]:
                        unanswered.append(f"{f['label'].split('|')[0].strip()[:60]} ({type(e).__name__})")
            res = FormResult("failed", filled=filled, unanswered=sorted(set(unanswered)), steps=[*res.steps, f"Rellené {len(filled)} campos con tus datos"], final_url=page.url)
            if res.unanswered:
                res.status = "needs_manual"
                res.message = "El formulario pide cosas que no tengo y no las invento: " + "; ".join(res.unanswered[:6])
                return res
            if dry_run:
                res.status, res.message = "dry_run", "Ensayo: el formulario quedó listo pero NO se envió."
                return res
            btn = next((b for b in page.locator("button[type=submit], input[type=submit], button").all() if b.is_visible() and SUBMIT_RX.search((b.inner_text() if b.evaluate("e => e.tagName") == "BUTTON" else b.get_attribute("value") or "") or "")), None)
            if btn is None:
                res.status, res.message = "needs_manual", "No encontré el botón de enviar."
                return res
            before = page.url
            btn.click()
            res.steps.append("Pulsé enviar")
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PWTimeout:
                pass
            page.wait_for_timeout(1000)
            body = page.inner_text("body")
            res.final_url = page.url
            if CONFIRM_RX.search(body):
                res.status, res.message = "submitted", "El portal confirmó la recepción."
            elif page.locator("form input:not([type=hidden])").count() == 0 and page.url != before:
                res.status, res.message = "submitted", "El formulario se envió (la página cambió)."
            else:
                res.status, res.message = "unconfirmed", "Pulsé enviar pero no pude confirmar la recepción: verifícalo en el portal."
            return res
        except Exception as e:  # noqa: BLE001
            return FormResult("failed", f"{type(e).__name__}: {str(e)[:200]}", steps=res.steps)
        finally:
            browser.close()
