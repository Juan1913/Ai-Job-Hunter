"""CVs 'sucios' en texto (como salen de extractores reales) para probar el constructor de perfil.
truth: lo que DEBE salir; absent: cosas que NO deben aparecer."""

MESSY: dict[str, dict] = {
    "gerente_tienda_es": {
        "text": """ANA BELÉN SÁNCHEZ GARCÍA
Gerente de Tienda
Madrid, España · (+34) 91 123 45 67 · E-mail: anabelen.sanchez@gmail.com

PERFIL PROFESIONAL
Gerente de tienda con 12 años de experiencia en retail de moda. Gestión de equipos de hasta 25 personas.

TRAYECTORIA PROFESIONAL
Enero 2019 – Presente
Gerente de Tienda
Zara España, Madrid
• Dirigí un equipo de 25 personas y alcancé un 108% del objetivo de ventas anual.
• Reduje la merma un 15% mediante controles de inventario semanales.

Marzo 2013 – Diciembre 2018
Subgerente
Mango, Barcelona
• Coordiné la apertura de 2 nuevas tiendas.

FORMACIÓN
Grado en Administración y Dirección de Empresas
Universidad Complutense de Madrid
2008 - 2012

COMPETENCIAS
Gestión de equipos, Merchandising visual, Control de inventario, Atención al cliente, Excel

IDIOMAS
Español: nativo · Inglés: C1 · Francés: B1
""",
        "truth": {"name": "Ana Belén Sánchez García", "email": "anabelen.sanchez@gmail.com", "phone": "(+34) 91 123 45 67",
                  "companies": ["Zara España", "Mango"], "titles": ["Gerente de Tienda", "Subgerente"], "skills": ["Merchandising visual", "Excel"], "n_exp": 2},
        "absent": ["Inditex", "SAP"],
    },
    "developer_at_en": {
        "text": """Michael O'Connor
Full Stack Developer | michael.oconnor@proton.me | +353 85 123 4567 | Dublin, Ireland

SUMMARY
Full stack developer focused on React and Node.js products.

EXPERIENCE
Full Stack Developer at Intercom (Dublin)
Jun 2020 - Present
- Built the billing dashboard in React and TypeScript.
- Improved Node.js API response times by 40%.

Junior Developer at Wayflyer
Sep 2018 - May 2020
- Maintained Python microservices.

EDUCATION
BSc Computer Science, Trinity College Dublin, 2018

TECHNICAL SKILLS
Languages: JavaScript, TypeScript, Python
Tools: React, Node.js, Docker, Git, AWS

CERTIFICATIONS
AWS Certified Developer - Associate
""",
        "truth": {"name": "Michael O'Connor", "email": "michael.oconnor@proton.me", "phone": "+353 85 123 4567",
                  "companies": ["Intercom", "Wayflyer"], "titles": ["Full Stack Developer", "Junior Developer"], "skills": ["TypeScript", "Docker", "React"], "n_exp": 2},
        "absent": ["Kubernetes", "Google"],
    },
    "analista_credito_es": {
        "text": """LAURA MARCELA RODRÍGUEZ
Analista de Crédito
laura.rodriguez@outlook.com
Cel: 311 456 7890
Cali, Colombia

EXPERIENCIA LABORAL

Banco de Bogotá
Analista de Crédito
03/2017 – 11/2019
Evaluación de solicitudes de crédito de consumo y análisis de riesgo. Elaboración de informes mensuales de cartera.

Bancolombia
Asesora Comercial
01/2014 – 02/2017
Colocación de productos financieros y atención a clientes persona natural.

ESTUDIOS
Economía - Universidad del Valle (2013)

HABILIDADES
Análisis de riesgo crediticio
Excel avanzado
Power BI
""",
        "truth": {"name": "Laura Marcela Rodríguez", "email": "laura.rodriguez@outlook.com", "phone": "311 456 7890",
                  "companies": ["Banco de Bogotá", "Bancolombia"], "titles": ["Analista de Crédito", "Asesora Comercial"], "skills": ["Power BI", "Excel avanzado"], "n_exp": 2},
        "absent": ["Davivienda"],
    },
    "docente_es": {
        "text": """Carlos Eduardo Mejía
Docente de Matemáticas
carlos.mejia@educa.edu.co | 300 111 2233 | Barranquilla

EXPERIENCIA
Profesor de Matemáticas - Colegio San José (2015 - 2020)
Dicté clases de álgebra y cálculo a grados 10 y 11. Diseñé guías de trabajo.
Auxiliar Docente - Universidad del Norte (2012 - 2014)
Apoyo en cursos de cálculo diferencial.

EDUCACIÓN
Licenciatura en Matemáticas - Universidad del Atlántico - 2011

CURSOS
Diplomado en Didáctica de las Matemáticas
""",
        "truth": {"name": "Carlos Eduardo Mejía", "email": "carlos.mejia@educa.edu.co", "phone": "300 111 2233",
                  "companies": ["Colegio San José", "Universidad del Norte"], "titles": ["Profesor de Matemáticas", "Auxiliar Docente"], "skills": [], "n_exp": 2},
        "absent": [],
    },
    "sin_secciones_en": {
        "text": """Sofia Martinez - Marketing Manager - sofia.martinez@mail.com - +1 305 555 0199
I am a marketing manager with 6 years of experience in digital campaigns, SEO and email marketing.
2019 - Present: Marketing Manager at Brightside Media, Miami. Led SEO and paid campaigns.
2016 - 2019: Marketing Specialist at Nexo Agency. Ran email marketing and social media.
""",
        "truth": {"email": "sofia.martinez@mail.com", "phone": "+1 305 555 0199", "companies": ["Brightside Media", "Nexo Agency"], "n_exp": 2},
        "absent": [],
    },
}
