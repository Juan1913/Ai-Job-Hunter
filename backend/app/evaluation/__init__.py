"""Evaluación multidimensional de ofertas (puntaje 1-5, vetos, recomendación). Ver engine.py para los pesos y reglas."""
from app.evaluation.engine import evaluate  # noqa: F401
from app.evaluation.models import Dimension, Evaluation, Flag, Veto  # noqa: F401
