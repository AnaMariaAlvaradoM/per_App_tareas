import os
import re
import json
import httpx
import logging
from datetime import datetime
import pytz
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from database import (init_db, add_task, get_tasks, complete_task, delete_task,
                      get_progress, save_message, get_today_messages, get_recent_messages)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TZ = pytz.timezone("America/Bogota")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

init_db()

DAYS_ES = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
MONTHS_ES = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]

def now_str():
    n = datetime.now(TZ)
    return f"{DAYS_ES[n.weekday()]} {n.day} de {MONTHS_ES[n.month-1]} de {n.year}, {n.strftime('%H:%M')} hora Colombia"

SYSTEM_PROMPT = """Eres Nova, una asistente personal de productividad. Eres directa, cálida y un poco sarcástica con afecto. Hablas en español.

REGLA MÁS IMPORTANTE: Cuando necesites ejecutar una acción sobre tareas, SIEMPRE pon el bloque ACTION al final de tu respuesta, nunca en medio. El formato es EXACTAMENTE:

<<<ACTION>>>
{"action": "NOMBRE", "task": "texto"}
<<<END>>>

Para múltiples acciones, un JSON por línea dentro del bloque. SIEMPRE cierra con <<<END>>>.

Acciones disponibles:
- add_task: {"action": "add_task", "task": "nombre de la tarea"}
- complete_task: {"action": "complete_task", "id": 123}
- delete_task: {"action": "delete_task", "id": 123}
- list_tasks: {"action": "list_tasks"}
- get_progress: {"action": "get_progress"}

NUNCA muestres el bloque ACTION en tu texto visible. El usuario no debe verlo.
NUNCA repitas tareas que ya existen en la lista.
Si el usuario pide agregar varias tareas de una vez, agrégalas todas en un solo bloque ACTION."""

def build_context():
    tasks = get_tasks(done=False)
    task_str = "Tareas pendientes:\n"
    task_str += "\n".join([f"- #{t[0]}: {t[1]}" for t in tasks]) if tasks else "(ninguna)"
    total, done, pct = get_progress()
    task_str += f"\nProgreso: {pct}% ({done}/{total})"

    today = get_today_messages()
    today_str = ""
    if today:
        today_str = "\nConversación de hoy:\n"
        for role, content in today[-20:]:
            today_str += f"{'Tú' if role == 'user' else 'Nova'}: {content[:200]}\n"

    recent = get_recent_messages(days=7)
    recent_str = ""
    if recent:
        recent_str = "\nMemoria reciente:\n"
        for role, content, day in recent[-10:]:
            recent_str += f"[{day}] {'Tú' if role == 'user' else 'Nova'}: {content[:150]}\n"

    return f"Fecha/hora: {now_str()}\n\n{task_str}{today_str}{recent_str}"

async def call_groq(messages: list) -> str:
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": messages,
        "temperature": 0.6,
        "max_tokens": 1024
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(GROQ_URL, json=payload, headers=headers)
        if r.status_code == 429:
            return "Demasiadas consultas seguidas, espera unos segundos e intenta de nuevo."
        if r.status_code != 200:
            logger.error(f"Groq error {r.status_code}: {r.text}")
            return "Hubo un error, intenta de nuevo."
        return r.json()["choices"][0]["message"]["content"]

def execute_action(action_json: dict) -> str:
    action = action_json.get("action")
    if action == "add_task":
        task_id = add_task(action_json["task"])
        return f"Tarea #{task_id} agregada"
    elif action == "complete_task":
        name = complete_task(action_json["id"])
        return f"'{name}' completada" if name else "No encontré esa tarea"
    elif action == "delete_task":
        delete_task(action_json["id"])
        return "Tarea eliminada"
    elif action == "list_tasks":
        tasks = get_tasks(done=False)
        return "\n".join([f"#{t[0]} {t[1]}" for t in tasks]) if tasks else "Sin tareas pendientes"
    elif action == "get_progress":
        total, done, pct = get_progress()
        return f"Progreso: {pct}% ({done}/{total})"
    return ""

def parse_and_execute(raw: str):
    """Extrae el bloque ACTION, lo ejecuta y devuelve texto limpio + resultado"""
    clean = raw
    results = []

    if "<<<ACTION>>>" in raw:
        # Siempre tomar lo que está ANTES del primer <<<ACTION>>>
        parts = raw.split("<<<ACTION>>>")
        clean = parts[0].strip()

        # Buscar todos los JSONs en el bloque
        action_block = parts[1].split("<<<END>>>")[0] if "<<<END>>>" in parts[1] else parts[1]
        for match in re.finditer(r'\{[^{}]+\}', action_block):
            try:
                result = execute_action(json.loads(match.group()))
                if result:
                    results.append(result)
            except Exception as e:
                logger.error(f"Action error: {e}")

    return clean, " · ".join(results) if results else None

def tasks_response():
    tasks = get_tasks(done=None)
    total, done, pct = get_progress()
    return {
        "tasks": [{"id": t[0], "name": t[1], "done": bool(t[2])} for t in tasks],
        "progress": {"total": total, "done": done, "pct": pct}
    }

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("index.html", encoding="utf-8") as f:
        return f.read()

@app.get("/api/tasks")
async def api_tasks():
    return tasks_response()

@app.post("/api/tasks/{task_id}/complete")
async def api_complete(task_id: int):
    complete_task(task_id)
    return {"ok": True, **tasks_response()}

@app.post("/api/tasks/{task_id}/delete")
async def api_delete(task_id: int):
    delete_task(task_id)
    return {"ok": True, **tasks_response()}

@app.post("/api/tasks/clear-done")
async def api_clear_done():
    for t in get_tasks(done=True):
        delete_task(t[0])
    return {"ok": True, **tasks_response()}

@app.post("/api/tasks/clear-all")
async def api_clear_all():
    for t in get_tasks(done=None):
        delete_task(t[0])
    return {"ok": True, **tasks_response()}

@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])

    context = build_context()
    groq_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"[Contexto - no lo menciones al usuario]\n{context}"},
        {"role": "assistant", "content": "Entendido."},
    ] + messages

    raw = await call_groq(groq_messages)
    clean_text, action_result = parse_and_execute(raw)

    # Guardar en memoria
    if messages:
        save_message("user", messages[-1]["content"])
    save_message("assistant", clean_text)

    tr = tasks_response()
    return {
        "message": clean_text,
        "action_result": action_result,
        "tasks": tr["tasks"],
        "progress": [tr["progress"]["total"], tr["progress"]["done"], tr["progress"]["pct"]]
    }