"""Calibración del motor de evaluación con casos explícitos: cada caso declara qué se espera y por qué.
Fecha fija para que los casos de frescura no caduquen."""
from datetime import date

import pytest

from app.domain.job import Job
from app.domain.profile import DealBreakers, Experience, Profile, SearchPreferences, Skill
from app.evaluation.engine import evaluate

TODAY = date(2026, 11, 4)


def profile(db: DealBreakers | None = None, *, years_start="2019-03", langs=("Español nativo", "Inglés B2"), remote=True) -> Profile:
    return Profile(full_name="Luis Gómez", headline="Analista de ciberseguridad", location="Bogotá, Colombia",
                   skills=[Skill(name="SIEM"), Skill(name="Splunk"), Skill(name="Python")], languages=list(langs),
                   experience=[Experience(company="Acme", title="Analista de Seguridad", start=years_start, end=None, bullets=["Monitoreé alertas"])],
                   preferences=SearchPreferences(countries=["CO"], regions=["Bogotá"], languages=["es", "en"], remote=remote, deal_breakers=db))


def job(**kw) -> Job:
    base = dict(title="Analista SOC", company="Bancolombia", location="Bogotá, Colombia", url="https://x/1", source="t", country="CO", language="es",
                work_mode="hybrid", posted_at="2026-10-30", description="Buscamos analista SOC con SIEM y respuesta a incidentes. Ofrecemos contrato indefinido.")
    base.update(kw)
    return Job(**base)


def ev(j: Job, p: Profile | None = None, **kw):
    kw.setdefault("stage1", 85.0)
    return evaluate(j, p or profile(), today=TODAY, **kw)


def dim(e, key):
    return next(d for d in e.dimensions if d.key == key)


def test_good_fit_is_apply_with_clear_reasons():
    e = ev(job(description="Analista SOC senior. Mínimo 5 años de experiencia en SIEM. Inglés B2. Contrato indefinido."), match=88)
    assert e.recommendation == "apply" and 3.8 <= e.score <= 5 and not e.vetoes
    assert 1 <= len(e.reasons_top3) <= 3
    assert abs(sum(d.effective_weight for d in e.dimensions) - 1) < 0.01


def test_overqualified_is_flagged_and_not_apply():
    e = ev(job(title="Analista SOC Junior", description="Analista SOC junior. 1 año de experiencia. Turnos rotativos."), match=90)
    assert any(f.code == "overqualified" for f in e.flags)
    assert dim(e, "seniority").score <= 0.6 and e.recommendation != "apply"


def test_underqualified_blocks_apply_even_with_perfect_skills():
    e = ev(job(title="Director de Seguridad", description="Director de seguridad de la información. Mínimo 12 años de experiencia liderando equipos."), match=95)
    assert any(f.code == "underqualified" for f in e.flags)
    assert dim(e, "seniority").score < 0.4 and e.recommendation != "apply"


def test_missing_high_level_language_is_a_hard_veto():
    e = ev(job(description="Analista SOC. Alemán avanzado (C1) obligatorio para trabajar con el equipo de Múnich. SIEM."), match=90)
    assert any(v.code == "language_missing" for v in e.vetoes)
    assert e.recommendation == "skip" and e.score <= 2.0 and e.raw_score > e.score


def test_missing_low_level_language_is_only_a_warning():
    e = ev(job(description="Analista SOC con SIEM. Se valora portugués básico. Contrato indefinido."), match=90)
    assert not e.vetoes and dim(e, "language").score < 1


def test_language_level_gap_warns():
    e = ev(job(description="Analyst. Fluent English (C1) required. SIEM experience.", language="en"), match=90)
    assert not e.vetoes and dim(e, "language").score <= 0.7 and dim(e, "language").status == "warning"


def test_posting_in_undeclared_language_warns():
    e = ev(job(description="Wir suchen einen Analysten mit SIEM Erfahrung und Teamgeist für unser Büro.", language="de"), match=80)
    assert dim(e, "language").status == "warning"


def test_low_salary_is_a_veto_with_explicit_reason():
    p = profile(DealBreakers(min_salary=3000, salary_currency="USD", salary_period="month"))
    e = ev(job(salary_min=1_500_000, salary_max=1_800_000, salary_currency="COP", salary_period="month"), p, match=90)
    assert [v.code for v in e.vetoes] == ["salary_below_min"] and e.recommendation == "skip"
    assert "3,000" in e.vetoes[0].reason or "3.000" in e.vetoes[0].reason or "mínimo" in e.vetoes[0].reason


def test_salary_comparison_normalizes_currency_and_period():
    p = profile(DealBreakers(min_salary=3000, salary_currency="USD", salary_period="month"))
    cop_month = ev(job(salary_min=18_000_000, salary_max=22_000_000, salary_currency="COP", salary_period="month"), p, match=90)
    usd_year = ev(job(salary_min=40_000, salary_max=48_000, salary_currency="USD", salary_period="year"), p, match=90)
    assert not cop_month.vetoes and dim(cop_month, "salary").score >= 0.85
    assert not usd_year.vetoes and dim(usd_year, "salary").score >= 0.85          # 48k/año = 4k/mes
    hourly = ev(job(salary_min=10, salary_max=12, salary_currency="USD", salary_period="hour"), p, match=90)
    assert any(v.code == "salary_below_min" for v in hourly.vetoes)                # ~2.1k/mes < 3k


def test_no_salary_is_neutral_and_says_so():
    e = ev(job(), match=90)
    d = dim(e, "salary")
    assert d.status == "neutral" and d.effective_weight == 0 and "no publica" in d.explanation.lower()
    assert any(f.code == "no_salary" for f in e.flags)


def test_expired_is_veto_and_urgent_is_flag():
    e = ev(job(expires_at="2026-11-03"), match=90)
    assert [v.code for v in e.vetoes] == ["expired"] and e.recommendation == "skip"
    u = ev(job(expires_at="2026-11-07"), match=90)
    assert not u.vetoes and any(f.code == "urgent" for f in u.flags) and u.recommendation in ("apply", "consider")


def test_closed_flag_is_veto():
    assert ev(job(closed=True), match=90).recommendation == "skip"


@pytest.mark.parametrize("db,job_kw,code", [
    (DealBreakers(excluded_companies=["bancolombia"]), {}, "excluded_company"),
    (DealBreakers(excluded_keywords=["comisiones"]), {"description": "Analista SOC. Ingresos por comisiones. SIEM."}, "excluded_keyword"),
    (DealBreakers(excluded_modes=["hybrid"]), {}, "excluded_mode"),
    (DealBreakers(min_level="senior"), {"title": "Analista SOC Junior"}, "level_out_of_range"),
    (DealBreakers(max_level="mid"), {"title": "Lead SOC Analyst"}, "level_out_of_range"),
    (DealBreakers(required_languages=["en"]), {}, "language_required"),
])
def test_user_deal_breakers_force_skip_with_reason(db, job_kw, code):
    e = ev(job(**job_kw), profile(db), match=95)
    assert code in [v.code for v in e.vetoes] and e.recommendation == "skip" and all(v.reason for v in e.vetoes)
    assert e.reasons_top3[0] == next(v.reason for v in e.vetoes)


def test_no_deal_breakers_means_no_user_vetoes():
    assert not ev(job(), profile(None), match=90).vetoes


def test_integrity_risk_caps_apply_to_consider():
    ok = ev(job(description="Analista SOC senior. 5 años de experiencia. SIEM."), match=95)
    assert ok.recommendation == "apply"
    bad = ev(job(description="Analista SOC senior. 5 años de experiencia. SIEM.", integrity_risk="high", integrity_flags=["instruction_injection"]), match=95)
    assert bad.recommendation == "consider" and any(f.code == "integrity" and f.severity == "alert" for f in bad.flags)


def test_user_thresholds_change_the_recommendation():
    j = job(description="Analista SOC. SIEM.")
    strict = ev(j, profile(DealBreakers(apply_threshold=5.0, consider_threshold=4.95)), match=70)
    lax = ev(j, profile(DealBreakers(apply_threshold=2.0, consider_threshold=1.5)), match=70)
    assert strict.recommendation == "skip" and lax.recommendation == "apply" and strict.score == lax.score


def test_remote_vs_onsite_in_other_country():
    far = ev(job(location="Madrid, España", country="ES", work_mode="onsite", language="es"), match=90)
    assert dim(far, "location").score <= 0.15
    remote = ev(job(location="Remoto", country=None, work_mode="remote"), match=90)
    assert dim(remote, "location").score == 1.0


def test_english_report_and_stable_schema():
    e = ev(job(), match=80, lang="en")
    assert e.lang == "en" and "Skills" in dim(e, "skills").label and "Weights" in e.weights_explained
    d = e.model_dump(mode="json")
    assert {"dimensions", "vetoes", "flags", "score", "recommendation", "reasons_top3", "schema_version"} <= d.keys()
    assert {x["key"] for x in d["dimensions"]} == {"skills", "seniority", "location", "language", "salary", "freshness"}
    assert 1 <= d["score"] <= 5


def test_years_extraction_regex():
    from app.evaluation.extract import required_languages, required_years
    assert required_years("Se requieren mínimo 3 años de experiencia en ventas")[0] == 3
    assert required_years("3 a 5 años de experiencia")[0] == 3
    assert required_years("5+ years of experience with Python")[0] == 5
    assert required_years("Fundada hace 20 años, somos líderes del mercado")[0] is None
    assert required_years("Deseable 4 años de experiencia en Go")[0] is None
    langs = required_languages("Inglés avanzado (C1) requerido. Portugués deseable.", "es")
    assert {(x["code"], x["required"]) for x in langs} == {("en", True), ("pt", False)}
    assert required_languages("Buscamos analista. Escribe en español claro.", "es") == []


def test_profile_years_union_of_overlapping_jobs():
    from app.evaluation.extract import profile_years
    p = Profile(experience=[Experience(company="A", title="x", start="2020-01", end="2022-01"), Experience(company="B", title="y", start="2021-01", end="2023-01")])
    assert profile_years(p, TODAY) == 3.0
