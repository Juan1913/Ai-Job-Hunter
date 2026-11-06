"""Datos y dobles compartidos por los tests de API."""
from app.domain.job import Job

API = "/api/v1"
CV = {"file": ("cv.txt", ("Luis Gomez analista de ciberseguridad con experiencia en SIEM y respuesta a incidentes " * 3).encode())}


class StubSource:
    countries: set = set()

    def __init__(self, name, jobs):
        self.name, self._jobs = name, jobs

    def search(self, q):
        if self.name == "roto":
            raise RuntimeError("bloqueado")
        return self._jobs


JOBS = [Job(title="Analista SOC", company="Bancolombia", location="Bogotá", url="https://b/1", apply_url="https://ats.b/apply/1", source="a",
            description="Requiere SIEM, AWS y respuesta a incidentes"),
        Job(title="Cocinero", company="Hotel", location="Cali", url="https://h/2", source="a", description="Cocina")]
