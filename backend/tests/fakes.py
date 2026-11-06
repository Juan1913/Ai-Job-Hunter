from app.domain.profile import Education, Experience, Profile, Skill

PROFILE = Profile(
    full_name="Luis Gómez", email="luis@x.com", phone="+57 300", location="Bogotá", headline="Analista de ciberseguridad",
    summary="Analista con 4 años en monitoreo de seguridad.", skills=[Skill(name="SIEM"), Skill(name="Splunk"), Skill(name="Python")],
    experience=[Experience(company="Acme", title="Analista de Seguridad", start="2021", end=None,
                           bullets=["Monitoreé alertas en Splunk las 24 horas", "Respondí a incidentes de phishing y malware"])],
    education=[Education(institution="UNAL", degree="Ingeniería de Sistemas", year="2020")], languages=["Español", "Inglés"])


class FakeLLM:
    """Extractor + clasificador + redactor + parser de CV simulados."""

    def __init__(self, bad_first=False):
        self.bad_first, self.calls = bad_first, 0

    def complete_json(self, system, user):
        if "extractor de datos de CVs" in system:
            return PROFILE.model_dump(mode="json", exclude={"preferences"})
        if "Extrae de una oferta" in system:
            return {"keywords": ["SIEM", "AWS", "respuesta a incidentes", "Kubernetes"]}
        if "adyacentes" in system:
            return {"adjacent": [{"keyword": "respuesta a incidentes", "evidence": "Respondí a incidentes de phishing y malware"},
                                 {"keyword": "Kubernetes", "evidence": "inventada que no está en el perfil"}]}
        self.calls += 1
        extra = " Experiencia en AWS." if (self.bad_first and self.calls == 1) else ""
        return {"headline": "Analista SOC", "summary": "Analista SOC con 4 años en monitoreo SIEM y respuesta a incidentes." + extra,
                "skills": ["SIEM", "Splunk", "Python", "Docker"],
                "experience": [{"bullets": ["Monitoreé alertas SIEM en Splunk 24/7 como analista SOC", "Ejecuté respuesta a incidentes de phishing y malware",
                                            "Elaboré reportes de seguridad para la gerencia y coordiné con equipos de TI para contener amenazas"]}]}
