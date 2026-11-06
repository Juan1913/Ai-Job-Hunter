"""Raíz de pytest: fija el backend de embeddings determinista antes de importar la app,
para que los tests no dependan de que haya (o no) un Ollama local."""
import os

os.environ.setdefault("JH_EMBED_BACKEND", "hash")

# Los tests NUNCA deben hablar con un modelo real (lento, no determinista y gasta CPU): puerto cerrado => fallo inmediato y visible.
os.environ.setdefault("JH_LLM_BASE_URL", "http://127.0.0.1:9")
