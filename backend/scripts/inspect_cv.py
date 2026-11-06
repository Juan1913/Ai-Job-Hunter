"""Muestra EN TU TERMINAL cómo se leyó un CV (perfil y calidad por campo). Corre local, sin enviar nada a ningún servicio.
Uso: .venv/bin/python scripts/inspect_cv.py ruta/al/cv.pdf [--llm]"""
import json
import sys

from app.llm.client import LLMClient
from app.profile.builder import build_profile_with_report
from app.profile.extract import extract_document


def main() -> None:
    path = next(a for a in sys.argv[1:] if not a.startswith("--"))
    use_llm = "--llm" in sys.argv
    with open(path, "rb") as f:
        ex = extract_document(path, f.read())
    r = build_profile_with_report(ex.text, LLMClient() if use_llm else None, ex.method, use_llm=use_llm)
    print(json.dumps({"perfil": r.profile.model_dump(mode="json", exclude={"preferences"}), "informe": r.report.model_dump(mode="json")},
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
