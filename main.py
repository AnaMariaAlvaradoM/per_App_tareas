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
                      get_progress, save_message, get_today_messages, get_recent_messages,
                      update_priority, uncomplete_task)

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

SYSTEM_PROMPT = """Eres Nova, una asistente personal de productividad. Eres directa, cálida y un poco sarcástica con afecto. Hablas en español colombiano.

REGLA MÁS IMPORTANTE: Cuando necesites ejecutar una acción sobre tareas, SIEMPRE pon el bloque ACTION al final de tu respuesta, nunca en medio. El formato es EXACTAMENTE:

<<<ACTION>>>
{"action": "NOMBRE", "task": "texto"}
<<<END>>>

Para múltiples acciones, un JSON por línea dentro del bloque. SIEMPRE cierra con <<<END>>>.

Acciones disponibles:
- add_task: {"action": "add_task", "task": "nombre", "priority": "today"} — o "week"
- update_priority: {"action": "update_priority", "id": 123, "priority": "today"}
  * Úsala cuando el usuario diga "eso es para hoy", "mueve X a hoy", "X es urgente", "cambia a semana", etc.
- complete_task: {"action": "complete_task", "id": 123}
- delete_task: {"action": "delete_task", "id": 123}
- get_progress: {"action": "get_progress"}

REGLA DE PRIORIDAD:
- "today": cuando el usuario diga "hoy", "urgente", "ya", "esta tarde", hora específica de hoy.
- "week": cuando diga "esta semana", "después", "cuando pueda", o no mencione tiempo.
- Default si no hay señal: "week".

REGLA CRÍTICA ANTI-DUPLICADOS:
- NUNCA listes las tareas en tu respuesta de texto. El usuario ya las ve en el sidebar izquierdo, siempre actualizadas.
- NUNCA escribas cosas como "tus tareas son: 1. X 2. Y 3. Z" — eso duplica lo que ya está visible.
- Si el usuario pregunta por sus tareas, responde con resumen breve ("tienes 5 pendientes, 2 para hoy") SIN enumerar.
- NUNCA repitas tareas que ya existen en la lista.
- NO existe la acción list_tasks. El sidebar muestra las tareas en tiempo real.

Si el usuario pide agregar varias tareas, agrégalas todas en un solo bloque ACTION.
NUNCA muestres el bloque ACTION en tu texto visible."""

def build_context():
    tasks = get_tasks(done=False)
    task_str = "Tareas pendientes (el usuario las ve en el sidebar, NO las listes en tu respuesta):\n"
    if tasks:
        for t in tasks:
            tid, tname, tdone, tpriority = t
            label = "[HOY]" if tpriority == "today" else "[SEMANA]"
            task_str += f"- #{tid} {label}: {tname}\n"
    else:
        task_str += "(ninguna)"
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
        priority = action_json.get("priority", "week")
        task_id = add_task(action_json["task"], priority)
        return f"Tarea #{task_id} agregada"
    elif action == "update_priority":
        name = update_priority(action_json["id"], action_json.get("priority", "week"))
        if name:
            label = "hoy" if action_json.get("priority") == "today" else "esta semana"
            return f"'{name}' movida a {label}"
        return "No encontré esa tarea"
    elif action == "complete_task":
        name = complete_task(action_json["id"])
        return f"'{name}' completada" if name else "No encontré esa tarea"
    elif action == "delete_task":
        delete_task(action_json["id"])
        return "Tarea eliminada"
    elif action == "get_progress":
        total, done, pct = get_progress()
        return f"Progreso: {pct}% ({done}/{total})"
    return ""

def parse_and_execute(raw: str):
    clean = raw
    results = []
    if "<<<ACTION>>>" in raw:
        parts = raw.split("<<<ACTION>>>")
        clean = parts[0].strip()
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
        "tasks": [{"id": t[0], "name": t[1], "done": bool(t[2]), "priority": t[3] or "week"} for t in tasks],
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

@app.post("/api/tasks/{task_id}/priority")
async def api_priority(task_id: int, request: Request):
    body = await request.json()
    update_priority(task_id, body.get("priority", "week"))
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

@app.post("/api/tasks")
async def api_add_task(request: Request):
    body = await request.json()
    name = body.get("name", "").strip()
    priority = body.get("priority", "week")
    if not name:
        return {"ok": False}
    task_id = add_task(name, priority)
    return {"ok": True, "id": task_id, **tasks_response()}

@app.post("/api/tasks/{task_id}/uncomplete")
async def api_uncomplete(task_id: int):
    uncomplete_task(task_id)
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