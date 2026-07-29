import os
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")

# Tope de tareas activas ("today = true, done = false") permitidas en Hoy.
# Vive aquí porque es una regla de datos, no solo de presentación:
# el backend nunca debe guardar una sexta prioridad.
TODAY_LIMIT = 5


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """Crea la tabla si no existe. Migración segura: no borra datos previos."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    id SERIAL PRIMARY KEY,
                    text TEXT NOT NULL,
                    done BOOLEAN DEFAULT FALSE,
                    due DATE DEFAULT NULL,
                    sort INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            # Columnas agregadas en versiones posteriores. IF NOT EXISTS
            # hace que esto sea seguro de correr cada vez que arranca la app.
            cur.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS due DATE DEFAULT NULL")
            cur.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS sort INTEGER DEFAULT 0")
            cur.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS today BOOLEAN DEFAULT FALSE")
            cur.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS important BOOLEAN DEFAULT FALSE")
            cur.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP DEFAULT NULL")
        conn.commit()


def add_item(text: str, due: str = None, today: bool = False, important: bool = False) -> dict:
    """
    Guarda una línea. Todo opcional salvo el texto:
    due='YYYY-MM-DD' o None, today/important en False por defecto.
    Nuevo item va arriba de su lista (sort menor que el mínimo actual entre los pendientes).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MIN(sort), 0) - 1 FROM items WHERE done = FALSE")
            new_sort = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO items (text, due, today, important, sort)
                   VALUES (%s, %s, %s, %s, %s)
                   RETURNING id, text, done, due, today, important, completed_at""",
                (text.strip(), due, today, important, new_sort),
            )
            row = cur.fetchone()
        conn.commit()
    return _row_to_dict(row)


def get_items():
    """
    Retorna todos los items. El orden que importa a cada vista lo aplica
    quien consume esta lista (main.py), porque cada vista ordena distinto
    (Hoy y Inbox por sort manual, Próximas por fecha, Completadas por fecha
    de cierre). Aquí solo se entrega una base ya coherente para eso.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, text, done, due, today, important, completed_at
                FROM items
                ORDER BY
                    done ASC,
                    sort ASC,
                    id DESC
            """)
            rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def toggle_item(item_id: int) -> dict:
    """Marca hecho/no hecho con un toque. Guarda completed_at para poder
    ordenar Completadas por fecha de cierre."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT done FROM items WHERE id = %s", (item_id,))
            row = cur.fetchone()
            if not row:
                return None
            new_done = not row[0]
            cur.execute(
                """UPDATE items
                   SET done = %s, completed_at = CASE WHEN %s THEN NOW() ELSE NULL END
                   WHERE id = %s
                   RETURNING id, text, done, due, today, important, completed_at""",
                (new_done, new_done, item_id),
            )
            updated = cur.fetchone()
        conn.commit()
    return _row_to_dict(updated)


def set_due(item_id: int, due: str) -> dict:
    """Asigna o cambia la fecha de un item. due='YYYY-MM-DD' o None para quitarla."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE items SET due = %s WHERE id = %s
                   RETURNING id, text, done, due, today, important, completed_at""",
                (due, item_id),
            )
            row = cur.fetchone()
        conn.commit()
    return _row_to_dict(row) if row else None


def count_today_active() -> int:
    """Cuántas tareas activas (no completadas) están marcadas para Hoy."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM items WHERE today = TRUE AND done = FALSE")
            return int(cur.fetchone()[0])


def toggle_today(item_id: int) -> dict:
    """
    Mueve una tarea a Hoy, o la saca si ya estaba. Si va a entrar y ya hay
    TODAY_LIMIT tareas activas en Hoy, no la mueve y retorna
    {"limit_reached": True} en vez del item, para que main.py pueda avisar
    sin adivinar reglas de negocio en la capa HTTP.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT today FROM items WHERE id = %s", (item_id,))
            row = cur.fetchone()
            if not row:
                return None
            new_value = not row[0]
            if new_value and count_today_active() >= TODAY_LIMIT:
                return {"limit_reached": True}
            cur.execute(
                """UPDATE items SET today = %s WHERE id = %s
                   RETURNING id, text, done, due, today, important, completed_at""",
                (new_value, item_id),
            )
            updated = cur.fetchone()
        conn.commit()
    return _row_to_dict(updated)


def toggle_important(item_id: int) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT important FROM items WHERE id = %s", (item_id,))
            row = cur.fetchone()
            if not row:
                return None
            new_value = not row[0]
            cur.execute(
                """UPDATE items SET important = %s WHERE id = %s
                   RETURNING id, text, done, due, today, important, completed_at""",
                (new_value, item_id),
            )
            updated = cur.fetchone()
        conn.commit()
    return _row_to_dict(updated)


def edit_item(item_id: int, text: str) -> dict:
    """Corrige el texto de un item sin borrarlo."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE items SET text = %s WHERE id = %s
                   RETURNING id, text, done, due, today, important, completed_at""",
                (text.strip(), item_id),
            )
            row = cur.fetchone()
        conn.commit()
    return _row_to_dict(row) if row else None


def reorder_items(ordered_ids: list):
    """Guarda el nuevo orden dentro de una vista. ordered_ids = ids en el
    orden deseado; solo se tocan esos ids, así que reordenar Hoy no
    afecta el orden guardado de Inbox ni de Próximas."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            for pos, iid in enumerate(ordered_ids):
                cur.execute("UPDATE items SET sort = %s WHERE id = %s", (pos, iid))
        conn.commit()


def delete_item(item_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM items WHERE id = %s", (item_id,))
        conn.commit()


def get_today_count() -> int:
    """Para el saludo: cuántas tareas activas están marcadas para Hoy."""
    return count_today_active()


def _row_to_dict(row):
    if not row:
        return None
    return {
        "id": row[0],
        "text": row[1],
        "done": row[2],
        "due": row[3].isoformat() if row[3] else None,
        "today": row[4],
        "important": row[5],
        "completed_at": row[6].isoformat() if row[6] else None,
    }
