"""Léxico de habilidades duras por familia profesional (ES/EN) para la extracción de requisitos SIN LLM.

No pretende ser exhaustivo: es un ancla de alta precisión. Lo que el léxico no conoce lo recogen los patrones
(siglas, herramientas CamelCase, certificaciones), las secciones 'requisitos' y el vocabulario del propio perfil."""
from __future__ import annotations

_F: dict[str, str] = {
    "tecnologia": """Python|Java|JavaScript|TypeScript|Go|Golang|Rust|C++|C#|PHP|Ruby|Kotlin|Swift|Scala|R|SQL|NoSQL|Bash|PowerShell|HTML|CSS|React|Angular|Vue|Node.js|Django|Flask|FastAPI|Spring Boot|.NET|Laravel|GraphQL|REST|gRPC|Kafka|RabbitMQ|Redis|MongoDB|PostgreSQL|MySQL|Oracle|SQL Server|Elasticsearch|Snowflake|BigQuery|Spark|Hadoop|Airflow|dbt|Docker|Kubernetes|Terraform|Ansible|Jenkins|GitHub Actions|GitLab CI|CI/CD|DevOps|AWS|Azure|GCP|Google Cloud|Linux|Git|Microservicios|Microservices|Machine Learning|Deep Learning|TensorFlow|PyTorch|Pandas|NumPy|Scrum|Kanban|Agile|Jira|Tableau|Power BI|Looker|Excel|Observability|Prometheus|Grafana|Datadog|OpenTelemetry|Selenium|Cypress|Jest|Pytest|ETL|Data Warehouse|API|APIs|SaaS|Figma|UX|UI|Android|iOS|Flutter|React Native|SAP|Salesforce|ServiceNow|ITIL""",
    "seguridad": """SIEM|SOC|Splunk|QRadar|Microsoft Sentinel|Sentinel|Elastic Security|CrowdStrike|SentinelOne|Carbon Black|Defender|MITRE ATT&CK|Threat Hunting|Incident Response|Respuesta a incidentes|Threat Intelligence|Inteligencia de amenazas|Forense digital|Digital Forensics|Malware Analysis|Análisis de malware|Pentesting|Penetration Testing|Red Team|Blue Team|Purple Team|Vulnerability Management|Gestión de vulnerabilidades|Nessus|Qualys|Burp Suite|Metasploit|Nmap|Wireshark|Firewall|IDS|IPS|EDR|XDR|SOAR|Cortex XSOAR|Palo Alto|Fortinet|Cisco|Zero Trust|IAM|PAM|Active Directory|ISO 27001|NIST|PCI DSS|SOC 2|GDPR|CIS Controls|OWASP|CISSP|CISM|CEH|OSCP|Security+|GCIH|GCIA|CompTIA Security+|DLP|Cloud Security|Seguridad en la nube|Phishing|Ciberseguridad|Cybersecurity|Network Security|Seguridad de redes|Hardening|SIEM Engineering|Detection Engineering|Sigma|YARA""",
    "ventas": """Venta consultiva|Consultative Selling|Prospección|Prospecting|Negociación|Negotiation|Cold Calling|Llamadas en frío|CRM|Salesforce|HubSpot|Pipedrive|Zoho CRM|Forecast de ventas|Sales Forecasting|Cierre de ventas|Gestión de cuentas|Gestión de cuentas clave|Key Account Management|Account Management|Account Based Marketing|ABM|B2B|B2C|Inside Sales|Outbound|Inbound|LinkedIn Sales Navigator|Sales Navigator|Lead Generation|Generación de leads|Embudo de ventas|Sales Funnel|Pipeline|Cuota de ventas|Quota|Upselling|Cross-selling|Postventa|Atención al cliente|Customer Success|Retención de clientes|Presentaciones comerciales|Demostraciones de producto|Licitaciones|Plan comercial|KPIs comerciales|SPIN Selling|MEDDIC|Challenger Sale|Trade Marketing|Retail|Ventas corporativas|Enterprise Sales|SaaS Sales|Business Development|Desarrollo de negocio|Telemarketing|Email Marketing|Marketing Digital|SEO|SEM|Google Ads|Meta Ads|Google Analytics""",
    "contabilidad": """NIIF|IFRS|NIC|US GAAP|GAAP|Contabilidad general|Contabilidad de costos|Cierres contables|Cierre contable|Conciliaciones bancarias|Conciliación bancaria|Declaración de renta|Declaraciones de impuestos|IVA|Retención en la fuente|ICA|Impuestos|Tributaria|Estados financieros|Balance general|Flujo de caja|Presupuestos|Auditoría interna|Auditoría externa|Auditoría|Revisoría fiscal|Control interno|SOX|Cuentas por pagar|Cuentas por cobrar|Nómina|Activos fijos|Costos|Tesorería|Facturación electrónica|Medios magnéticos|Cierre mensual|Análisis financiero|FP&A|Presupuesto|Consolidación|Siigo|SAP|SAP FI|Oracle NetSuite|NetSuite|QuickBooks|Alegra|World Office|Helisa|Contpaqi|Aspel|Odoo|Power BI|Excel avanzado|Tablas dinámicas|Macros|VBA|Contador Público|CPA|CIA|CFA|ACCA|Compliance|Cumplimiento|Prevención de lavado de activos|SARLAFT|AML|KYC|Detracciones|Planilla|SUNAT|SAT|DIAN""",
    "salud": """Cuidados intensivos|UCI|Cuidados críticos|Ventilación mecánica|Monitorización hemodinámica|RCP avanzada|RCP|Soporte Vital Avanzado|SVA|BLS|ACLS|Soporte Vital Básico|Seguridad del paciente|Historia clínica electrónica|Historia clínica|Administración de medicación|Administración de medicamentos|Triage|Triaje|Hemodiálisis|Diálisis|Terapias de reemplazo renal|Curación de heridas|Cuidados postoperatorios|Quirófano|Urgencias|Emergencias|Pediatría|Neonatología|Oncología|Geriatría|Salud mental|Enfermería|Control de infecciones|Bioseguridad|Vía venosa|Canalización|Sondaje|Toma de muestras|Electrocardiograma|ECG|Oxigenoterapia|Educación al paciente|Gestión de camas|Epic|Cerner|Fisioterapia|Farmacología|Telemedicina|Primeros auxilios|Patient Safety|Critical Care|Electronic Health Records|EHR|Medication Administration|Wound Care|Infection Control|Telemetry|Phlebotomy|Case Management|Care Planning|HIPAA""",
    "general": """Inglés|English|Español|Portugués|Francés|Alemán|Excel|Word|PowerPoint|Office 365|Microsoft Office|Google Workspace|Outlook|Gestión de proyectos|Project Management|PMP|PRINCE2|Scrum Master|Lean|Six Sigma|Kaizen|Gestión del tiempo|Liderazgo de equipos|Gestión de equipos|Planificación estratégica|Análisis de datos|Data Analysis|Reporting|Reportes|Atención al cliente|Servicio al cliente|Customer Service|Logística|Cadena de suministro|Supply Chain|Inventarios|Compras|Procurement|Recursos Humanos|Reclutamiento|Selección de personal|Capacitación|Formación|Marketing|Redes sociales|Community Management|Copywriting|Diseño gráfico|Photoshop|Illustrator|InDesign|Canva|Derecho laboral|Contratos|Legal|Mantenimiento|Seguridad industrial|SST|HSE|ISO 9001|ISO 14001|ISO 45001|Control de calidad|Quality Assurance|Producción|Manufactura|AutoCAD|SolidWorks|Revit|Gestión de riesgos|Risk Management|Business Intelligence|Power Automate""",
}

_LOWER = {"R"}   # nombres de una sola letra: demasiado ambiguos para casarlos por léxico


def _build() -> dict[str, str]:
    out: dict[str, str] = {}
    for blob in _F.values():
        for t in blob.split("|"):
            t = t.strip()
            if t and t not in _LOWER and len(t) > 1:
                out.setdefault(t.lower(), t)
    return out


TERMS: dict[str, str] = _build()          # minúsculas -> forma canónica
FAMILY_OF: dict[str, str] = {t.lower(): fam for fam, blob in _F.items() for t in blob.split("|") if t.strip()}

# Palabras que nunca son un requisito duro (blandas, beneficios, relleno de ofertas)
NOT_SKILLS = set("""experiencia años año conocimientos conocimiento manejo dominio habilidades habilidad capacidad capacidades excelente buena buen
trabajo equipo equipos proactivo proactiva responsable responsabilidad comunicación liderazgo puesto cargo empresa compañía oferta ofrecemos
buscamos requisitos requisito deseable deseables valorable se valora salario sueldo beneficios horario jornada contrato indefinido remoto presencial
híbrido formación estudios título grado licenciatura ingeniería persona perfil candidato candidata profesional nivel avanzado intermedio básico
experience years year knowledge skills skill ability abilities strong good excellent team teams work company role position offer we are looking
requirements required preferred plus nice have must should degree bachelor master candidate proven track record environment salary benefits
full time part time remote onsite hybrid including include includes etc and or the with for you your our their""".split())

SECTION_HEADS = ("requisitos", "requisito", "requirements", "requirement", "qué buscamos", "que buscamos", "what you'll need", "what you will need",
                 "what we're looking for", "what we are looking for", "you have", "you bring", "perfil", "se requiere", "conocimientos", "habilidades",
                 "skills", "qualifications", "cualificaciones", "must have", "nice to have", "se valora", "deseable", "deseables", "mínimo", "minimo",
                 "formación y experiencia", "experiencia requerida", "tu perfil", "your profile", "about you", "sobre ti", "lo que necesitas", "needed")
STOP_SECTIONS = ("ofrecemos", "beneficios", "benefits", "we offer", "what we offer", "qué ofrecemos", "que ofrecemos", "sobre nosotros", "about us",
                 "responsabilidades", "responsibilities", "funciones", "tus funciones", "what you'll do", "what you will do", "salario", "compensation")
