"""Dataset de evaluación del matcher (escrito a mano, ES/EN). Relevancia: 2 = encaja, 1 = adyacente, 0 = no encaja
(incluye trampas léxicas: 'Analista de datos' vs 'Analista SOC', y ofertas relevantes pero fuera de país/modalidad).
Es pequeño a propósito: sirve de regresión y de calibración burda, no de benchmark."""
from app.domain.job import Job
from app.domain.profile import Education, Experience, Profile, SearchPreferences, Skill


def J(title, desc, loc="", remote=False, company="X"):
    return Job(title=title, company=company, location=loc, remote=remote, description=desc, url=f"u/{title}/{loc}", source="eval")


SOC = Profile(
    full_name="Luis Gómez", headline="Analista de ciberseguridad SOC", summary="Analista con 4 años en monitoreo de seguridad y respuesta a incidentes.",
    skills=[Skill(name=n) for n in ["SIEM", "Splunk", "Python", "MITRE ATT&CK", "Threat Hunting", "Wireshark"]],
    experience=[Experience(company="Acme", title="Analista de Seguridad", start="2021", bullets=[
        "Monitoreé alertas en Splunk las 24 horas", "Respondí a incidentes de phishing y malware", "Caza de amenazas con MITRE ATT&CK"]),
        Experience(company="Beta", title="Soporte TI", start="2019", end="2021", bullets=["Administré usuarios y endpoints en Active Directory"])],
    education=[Education(institution="UNAL", degree="Ingeniería de Sistemas")], certifications=["CompTIA Security+"], languages=["Español", "Inglés"],
    preferences=SearchPreferences(countries=["CO"], languages=["es", "en"], remote=True, target_roles=["Analista SOC", "Security Analyst"]))

SOC_JOBS = [
    (J("Analista SOC N2", "Buscamos analista para monitoreo 7x24 con SIEM (Splunk o QRadar), respuesta a incidentes y análisis de malware. Conocimiento de MITRE ATT&CK.", "Bogotá, Colombia"), 2),
    (J("Security Operations Analyst", "Monitor alerts in our SIEM, perform incident response and threat hunting. Experience with Splunk and MITRE ATT&CK required.", "Remote", True), 2),
    (J("Analista de Respuesta a Incidentes", "Responsable de contener y erradicar incidentes de seguridad, análisis forense básico, phishing y malware, trabajo con el SOC.", "Medellín, Colombia"), 2),
    (J("Ingeniero de Seguridad Cloud", "Diseño de controles de seguridad en AWS, IAM, integración de logs con SIEM, automatización con Python.", "Bogotá, Colombia"), 1),
    (J("Pentester Junior", "Perform penetration testing on web applications, write reports, knowledge of OWASP and Burp Suite.", "Remote", True), 1),
    (J("Analista de datos Power BI", "Analista para construir tableros en Power BI, SQL y reportes de ventas para la gerencia comercial.", "Bogotá, Colombia"), 0),
    (J("Desarrollador Frontend React", "Desarrollo de interfaces con React, TypeScript y CSS. Experiencia con diseño responsive y pruebas de UI.", "Bogotá, Colombia"), 0),
    (J("Chef Ejecutivo", "Lideraremos la cocina de nuestro restaurante: menús, control de costos, manejo de personal de cocina.", "Medellín, Colombia"), 0),
    (J("Ejecutivo Comercial B2B", "Venta consultiva de servicios a empresas, cumplimiento de metas, manejo de CRM y prospección.", "Cali, Colombia"), 0),
    (J("Analista SOC (presencial)", "Monitoreo 24x7 con SIEM, respuesta a incidentes y threat hunting en nuestro centro de operaciones.", "Madrid, España"), 0),   # relevante pero fuera de país
]

DEV = Profile(
    full_name="Ann Lee", headline="Senior Backend Engineer", summary="Backend engineer with 7 years building Python APIs and distributed systems.",
    skills=[Skill(name=n) for n in ["Python", "FastAPI", "Django", "PostgreSQL", "Docker", "AWS", "REST APIs"]],
    experience=[Experience(company="Foo", title="Senior Backend Engineer", start="2020", bullets=[
        "Built REST APIs with FastAPI serving 2M requests per day", "Designed PostgreSQL schemas and optimized slow queries", "Deployed services on AWS using Docker and CI/CD"]),
        Experience(company="Bar", title="Python Developer", start="2017", end="2020", bullets=["Developed Django web applications and background jobs with Celery"])],
    languages=["English", "Spanish"],
    preferences=SearchPreferences(countries=["US"], languages=["en", "es"], remote=True, target_roles=["Backend Engineer", "Python Developer"]))

DEV_JOBS = [
    (J("Senior Python Backend Engineer", "Design and build scalable APIs in Python (FastAPI or Django), PostgreSQL, Docker, AWS. You will own services end to end.", "Remote", True), 2),
    (J("Ingeniero Backend Python/Django", "Desarrollo de APIs REST con Django y PostgreSQL, despliegue con Docker, trabajo remoto para empresa de EE.UU.", "Remoto", True), 2),
    (J("Software Engineer, APIs", "Join our platform team building FastAPI microservices, REST APIs, PostgreSQL and message queues in the cloud.", "Austin, United States"), 2),
    (J("DevOps Engineer", "Manage Kubernetes clusters on AWS, Terraform, CI/CD pipelines, Docker images and observability.", "Remote", True), 1),
    (J("Data Engineer", "Build ETL pipelines in Python and Airflow, PostgreSQL and Spark, data warehouse modelling.", "New York, United States"), 1),
    (J("Registered Nurse", "Provide patient care in our hospital ward, administer medication, document care plans. RN license required.", "Seattle, United States"), 0),
    (J("Senior Marketing Manager", "Lead our demand generation, brand campaigns and a team of marketers; manage budget and agency relationships.", "New York, United States"), 0),
    (J("Truck Driver CDL-A", "Long haul routes, valid CDL-A license, clean driving record, weekly pay and benefits.", "Austin, United States"), 0),
    (J("Senior Accountant", "Month-end close, reconciliations, financial statements, GAAP knowledge, ERP experience.", "New York, United States"), 0),
    (J("Backend Engineer (onsite Berlin)", "Build Python APIs with FastAPI and PostgreSQL in our Berlin office; German required.", "Berlin, Germany"), 0),   # relevante pero fuera de país
]

MKT = Profile(
    full_name="Sofía Ruiz", headline="Especialista en Marketing Digital", summary="Especialista con 5 años en SEO, SEM y redes sociales para ecommerce.",
    skills=[Skill(name=n) for n in ["SEO", "SEM", "Google Ads", "Google Analytics", "Meta Ads", "Email Marketing", "Redes sociales"]],
    experience=[Experience(company="Tienda Uno", title="Especialista SEO/SEM", start="2020", bullets=[
        "Gestioné campañas de Google Ads con ROAS de 4x", "Posicioné 30 palabras clave en el top 3 con estrategia SEO", "Reporté métricas en Google Analytics"]),
        Experience(company="Agencia Dos", title="Community Manager", start="2018", end="2020", bullets=["Administré redes sociales y calendario de contenidos para 6 marcas"])],
    certifications=["Google Ads Certification"], languages=["Español", "Inglés"],
    preferences=SearchPreferences(countries=["MX"], languages=["es", "en"], remote=True, target_roles=["Marketing Digital", "Community Manager"]))

MKT_JOBS = [
    (J("Especialista en Marketing Digital (SEO/SEM)", "Planear y optimizar campañas de Google Ads y Meta Ads, estrategia SEO, análisis en Google Analytics, reportes de ROI.", "Ciudad de México, México"), 2),
    (J("Community Manager y Social Media", "Gestión de redes sociales, calendario editorial, creación de contenido y reporte de métricas de engagement.", "Guadalajara, México"), 2),
    (J("Digital Marketing Manager", "Own paid search, SEO and email marketing for our ecommerce brand; analyze performance in Google Analytics.", "Remote", True), 2),
    (J("Diseñador Gráfico", "Diseño de piezas para redes sociales y campañas, Photoshop e Illustrator, identidad de marca.", "Ciudad de México, México"), 1),
    (J("Ejecutivo de Ventas Ecommerce", "Gestionar cuentas de marketplaces, conversión de ventas online y relación con clientes digitales.", "Monterrey, México"), 1),
    (J("Ingeniero de Software Java", "Desarrollo de microservicios en Java y Spring Boot, bases de datos Oracle y pruebas automatizadas.", "Ciudad de México, México"), 0),
    (J("Enfermera General", "Atención a pacientes, administración de medicamentos y registro clínico en clínica privada.", "Monterrey, México"), 0),
    (J("Analista SOC", "Monitoreo de eventos de seguridad con SIEM, respuesta a incidentes, análisis de malware y phishing.", "Ciudad de México, México"), 0),
    (J("Soldador Industrial", "Soldadura MIG/TIG en planta, lectura de planos y control de calidad de uniones.", "Monterrey, México"), 0),
    (J("Auditor Financiero", "Auditoría interna, control de riesgos financieros, revisión de estados contables y cumplimiento normativo.", "Ciudad de México, México"), 0),
]

DATASET = [("soc", SOC, SOC_JOBS), ("dev", DEV, DEV_JOBS), ("mkt", MKT, MKT_JOBS)]
