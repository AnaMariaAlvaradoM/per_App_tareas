import os
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    done BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
        conn.commit()

def add_task(name: str) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO tasks (name) VALUES (%s) RETURNING id", (name,))
            task_id = cur.fetchone()[0]
        conn.commit()
    return task_id

def get_tasks(done=False):
    with get_conn() as conn:
        with conn.cursor() as cur:
            if done is None:
                cur.execute("SELECT id, name, done FROM tasks ORDER BY done ASC, id DESC")
            else:
                cur.execute("SELECT id, name, done FROM tasks WHERE done = %s ORDER BY id DESC", (done,))
            return cur.fetchall()

def complete_task(task_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM tasks WHERE id = %s", (task_id,))
            row = cur.fetchone()
            if row:
                cur.execute("UPDATE tasks SET done = TRUE WHERE id = %s", (task_id,))
                conn.commit()
                return row[0]
    return None

def delete_task(task_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
        conn.commit()

def get_progress():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM tasks")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM tasks WHERE done = TRUE")
            done = cur.fetchone()[0]
    pct = round((done / total * 100) if total > 0 else 0)
    return total, done, pct

def save_message(role: str, content: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO messages (role, content) VALUES (%s, %s)", (role, content))
        conn.commit()

def get_today_messages():
    """Trae todos los mensajes de hoy para contexto interno"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT role, content FROM messages
                WHERE created_at::date = CURRENT_DATE
                ORDER BY created_at ASC
            """)
            return cur.fetchall()

def get_recent_messages(days=7):
    """Trae resumen de los ultimos N dias para memoria de largo plazo"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT role, content, created_at::date as day FROM messages
                WHERE created_at >= NOW() - INTERVAL '%s days'
                AND created_at::date < CURRENT_DATE
                ORDER BY created_at ASC
            """, (days,))
            return cur.fetchall()