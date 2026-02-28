import os
import psycopg2
from psycopg2.extras import RealDictCursor

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