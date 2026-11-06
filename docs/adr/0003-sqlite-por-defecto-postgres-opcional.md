# 0003 — SQLite por defecto, PostgreSQL opcional, backups

**Contexto.** Carga de escritura baja (perfiles, ofertas, eventos), un solo proceso, despliegue en una máquina.

**Decisión.** SQLite en WAL sobre un volumen es el valor por defecto (cero operación). `jobhunter:database=postgres`
añade un contenedor PostgreSQL 16 y apunta el backend a él (`postgresql+psycopg`, extra `postgres`). Ambos soportados
por el mismo código (`db.py` solo pasa `check_same_thread` a SQLite) y por las mismas migraciones Alembic (verificado
contra PostgreSQL 17). Un contenedor `backup` toma copias consistentes cada 6 h (API de backup + `integrity_check`, o
`pg_dump`) con rotación y destino opcional en el host.

**Consecuencias.** Sin migración automática de datos entre motores. El backup en el mismo disco no protege de perder el
VPS: hay que sincronizarlo fuera. En producción el esquema lo gestiona Alembic (`DB_AUTO_CREATE=false`) y un test
falla si los modelos y las migraciones divergen.
