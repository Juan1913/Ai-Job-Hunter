# 0004 — Tokens de sesión y límites de tasa

**Decisión.** Access token JWT de 30 min (`typ=access`, con huella de la contraseña `pv` para invalidar al cambiarla) y
refresh token opaco de 14 días guardado solo como hash SHA-256, **rotatorio**: cada uso emite otro de la misma «familia»;
reutilizar uno ya usado revoca la familia (robo detectado). Rate limiting con token bucket en memoria por IP y usuario,
más estricto en `/auth/*`; bloqueo temporal de login por (IP, email) y por email (umbral 5x); cuotas diarias por usuario
contadas en BD (sobreviven reinicios).

**Alternativas descartadas.** Sesiones en Redis y límites distribuidos (innecesarios con una instancia); JWT largo sin
refresh (no revocable).

**Consecuencias.** Los límites en memoria se reinician con el proceso. El registro no puede ocultar la existencia de un
email sin verificación por correo; se mitiga (ver README).
