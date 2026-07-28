import os
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")


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
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS due DATE DEFAULT NULL")
        conn.commit()


def add_item(text: str, due: str = None) -> dict:
    """Guarda una línea. due opcional en formato 'YYYY-MM-DD' o None."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO items (text, due) VALUES (%s, %s) RETURNING id, text, done, due",
                (text.strip(), due),
            )
            row = cur.fetchone()
        conn.commit()
    return _row_to_dict(row)


def get_items():
    """
    Retorna todos los items ordenados:
    pendientes primero, dentro de esos los que tienen fecha más cercana,
    luego los sin fecha por más recientes. Completados al final.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, text, done, due
                FROM items
                ORDER BY
                    done ASC,
                    CASE WHEN due IS NULL THEN 1 ELSE 0 END ASC,
                    due ASC,
                    id DESC
            """)
            rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def toggle_item(item_id: int) -> dict:
    """Marca hecho/no hecho con un toque. Retorna el estado nuevo."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT done FROM items WHERE id = %s", (item_id,))
            row = cur.fetchone()
            if not row:
                return None
            new_done = not row[0]
            cur.execute(
                "UPDATE items SET done = %s WHERE id = %s RETURNING id, text, done, due",
                (new_done, item_id),
            )
            updated = cur.fetchone()
        conn.commit()
    return _row_to_dict(updated)


def set_due(item_id: int, due: str) -> dict:
    """Asigna o cambia la fecha de un item. due='YYYY-MM-DD' o None para quitarla."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE items SET due = %s WHERE id = %s RETURNING id, text, done, due",
                (due, item_id),
            )
            row = cur.fetchone()
        conn.commit()
    return _row_to_dict(row) if row else None


def delete_item(item_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM items WHERE id = %s", (item_id,))
        conn.commit()


def get_due_today_count() -> int:
    """Cuántos pendientes tienen fecha de hoy o vencida. Para el saludo."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM items
                WHERE done = FALSE AND due IS NOT NULL AND due <= CURRENT_DATE
            """)
            count = cur.fetchone()[0]
    return int(count)


def _row_to_dict(row):
    if not row:
        return None
    return {
        "id": row[0],
        "text": row[1],
        "done": row[2],
        "due": row[3].isoformat() if row[3] else None,
    }
