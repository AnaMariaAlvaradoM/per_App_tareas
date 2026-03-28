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
                      update_priority, uncomplete_task, update_task_fields, get_today_load)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TZ = pytz.timezone("America/Bogota")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

init_db()

DAYS_ES    = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
MONTHS_ES  = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto",
               "septiembre","octubre","noviembre","diciembre"]
CATEGORIES = ["trabajo","personal","salud","casa","otro","general"]

def now_str():
    n = datetime.now(TZ)
    return f"{DAYS_ES[n.weekday()]} {n.day} de {MONTHS_ES[n.month-1]} de {n.year}, {n.strftime('%H:%M')} hora Colombia"

def minutes_to_human(mins: int) -> str:
    if not mins:
        return ""
    if mins < 60:
        return f"{mins}min"
    h = mins // 60
    m = mins % 60
    return f"{h}h {m}min" if m else f"{h}h"

# ─── SYSTEM PROMPT ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Eres Nova, asistente personal de productividad de Ana. Eres directa, cálida, un poco sarcástica con afecto. Hablas en español colombiano casual.

════════════════════════════════════════
REGLA #1 — BLOQUE ACTION
════════════════════════════════════════
Cuando necesites ejecutar acciones, ponlas SIEMPRE al final de tu respuesta en este formato exacto:

<<<ACTION>>>
{"action": "NOMBRE", ...}
<<<END>>>

Para múltiples acciones: un JSON por línea dentro del bloque.
NUNCA muestres el bloque en tu texto visible.

════════════════════════════════════════
ACCIONES DISPONIBLES
════════════════════════════════════════
add_task:
  {"action": "add_task", "task": "nombre", "priority": "today|week",
   "category": "trabajo|personal|salud|casa|otro|general",
   "estimated_minutes": 30, "deadline": "2024-03-28"}
  — estimated_minutes y deadline son opcionales.
  — Si el usuario menciona cuánto tarda algo, úsalo: "media hora" → 30, "una hora" → 60, "dos horas" → 120, "15 min" → 15.
  — Si el usuario menciona una fecha, ponla en deadline (formato YYYY-MM-DD).
  — Infiere la categoría del contexto: cliente/reunión/proyecto → trabajo, médico/ejercicio → salud, mercado/casa → casa.

update_priority:
  {"action": "update_priority", "id": 123, "priority": "today|week"}

update_task_fields:
  {"action": "update_task_fields", "id": 123, "category": "trabajo",
   "estimated_minutes": 60, "deadline": "2024-03-28"}
  — Úsala cuando el usuario corrija categoría, tiempo o deadline de una tarea existente.

complete_task:
  {"action": "complete_task", "id": 123}

delete_task:
  {"action": "delete_task", "id": 123}

get_progress:
  {"action": "get_progress"}

════════════════════════════════════════
REGLA #2 — PRIORIDAD
════════════════════════════════════════
- "today": usuario dice "hoy", "urgente", "ya", hora específica de hoy.
- "week": usuario dice "esta semana", "después", "cuando pueda", o no hay señal temporal.
- Default sin señal: "week".

════════════════════════════════════════
REGLA #3 — ANTI-DUPLICADOS
════════════════════════════════════════
- NUNCA listes tareas en tu respuesta. Ana las ve en el sidebar en tiempo real.
- Si pregunta por sus tareas: responde con resumen ("tienes 5 pendientes, 3 para hoy, ~2h de carga") SIN enumerar.
- NUNCA repitas tareas que ya existen.
- NO existe la acción list_tasks.

════════════════════════════════════════
REGLA #4 — CRITERIO E INICIATIVA
════════════════════════════════════════
Tienes contexto completo: tareas, categorías, tiempos estimados, deadlines, carga del día.
Úsalo activamente:
- Si Ana dice "no sé por dónde empezar" → sugiere orden concreto basado en prioridad, categoría y tiempo.
- Si nota tareas HOY con varios días sin completarse → menciónalo con gracia, sin regañar.
- Si la carga del día supera 6h → avísale que el día está muy cargado.
- Si hay deadline próximo → súbelo a HOY automáticamente y avísale.
- Sé proactiva, no solo reactiva."""

# ─── CHECKIN PROMPT ───────────────────────────────────────────────────────────

CHECKIN_PROMPT = """Eres Nova. Genera el check-in de apertura del día para Ana. 

Reglas:
- Máximo 3-4 líneas, tono cálido y directo, español colombiano casual.
- Menciona cuántas tareas tiene para hoy y la carga estimada en horas/minutos si aplica.
- Si hay tareas HOY que llevan más de 1 día sin completarse, menciona UNA (la más vieja) con gracia.
- Si hay deadlines en los próximos 2 días, menciónalos.
- Si no hay tareas para hoy, invítala a planear el día.
- Termina con UNA pregunta o sugerencia concreta para arrancar.
- NO listes tareas. NO uses bullet points. Solo párrafo natural.
- NUNCA muestres bloques ACTION en el check-in."""

# ─── CONTEXTO ─────────────────────────────────────────────────────────────────

def build_context():
    tasks = get_tasks(done=False)
    task_lines = []
    for t in tasks:
        tid, tname, tdone, tpriority, tcategory, testimated, tdeadline = t
        label    = "[HOY]" if tpriority == "today" else "[SEMANA]"
        cat      = f" [{tcategory}]" if tcategory and tcategory != "general" else ""
        est      = f" ~{minutes_to_human(testimated)}" if testimated else ""
        dl       = f" ⚠️ deadline {tdeadline}" if tdeadline else ""
        task_lines.append(f"- #{tid} {label}{cat}{est}{dl}: {tname}")

    task_str = "Tareas pendientes (NO las listes en tu respuesta, Ana las ve en el sidebar):\n"
    task_str += "\n".join(task_lines) if task_lines else "(ninguna)"

    count_today, mins_today = get_today_load()
    task_str += f"\n\nCarga HOY: {count_today} tareas, ~{minutes_to_human(mins_today) or '?'} estimados"

    total, done, pct = get_progress()
    task_str += f"\nProgreso total: {pct}% ({done}/{total})"

    today = get_today_messages()
    today_str = ""
    if today:
        today_str = "\nConversación de hoy:\n"
        for role, content in today[-20:]:
            today_str += f"{'Ana' if role == 'user' else 'Nova'}: {content[:200]}\n"

    recent = get_recent_messages(days=7)
    recent_str = ""
    if recent:
        recent_str = "\nMemoria reciente (últimos 7 días):\n"
        for role, content, day in recent[-10:]:
            recent_str += f"[{day}] {'Ana' if role == 'user' else 'Nova'}: {content[:150]}\n"

    return f"Fecha/hora actual: {now_str()}\n\n{task_str}{today_str}{recent_str}"

# ─── GROQ ─────────────────────────────────────────────────────────────────────

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

# ─── EJECUTAR ACCIONES ────────────────────────────────────────────────────────

def execute_action(action_json: dict) -> str:
    action = action_json.get("action")

    if action == "add_task":
        priority  = action_json.get("priority", "week")
        category  = action_json.get("category", "general")
        estimated = action_json.get("estimated_minutes")
        deadline  = action_json.get("deadline")
        task_id   = add_task(action_json["task"], priority, category, estimated, deadline)
        return f"Tarea #{task_id} agregada"

    elif action == "update_priority":
        name = update_priority(action_json["id"], action_json.get("priority", "week"))
        if name:
            label = "hoy" if action_json.get("priority") == "today" else "esta semana"
            return f"'{name}' movida a {label}"
        return "No encontré esa tarea"

    elif action == "update_task_fields":
        update_task_fields(
            task_id           = action_json["id"],
            category          = action_json.get("category"),
            estimated_minutes = action_json.get("estimated_minutes"),
            deadline          = action_json.get("deadline")
        )
        return "Tarea actualizada"

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
        parts        = raw.split("<<<ACTION>>>")
        clean        = parts[0].strip()
        action_block = parts[1].split("<<<END>>>")[0] if "<<<END>>>" in parts[1] else parts[1]
        for match in re.finditer(r'\{[^{}]+\}', action_block):
            try:
                result = execute_action(json.loads(match.group()))
                if result:
                    results.append(result)
            except Exception as e:
                logger.error(f"Action error: {e}")
    return clean, " · ".join(results) if results else None

# ─── RESPUESTA DE TAREAS ──────────────────────────────────────────────────────

def tasks_response():
    tasks = get_tasks(done=None)
    total, done, pct = get_progress()
    count_today, mins_today = get_today_load()
    return {
        "tasks": [
            {
                "id":                t[0],
                "name":              t[1],
                "done":              bool(t[2]),
                "priority":          t[3] or "week",
                "category":          t[4] or "general",
                "estimated_minutes": t[5],
                "deadline":          str(t[6]) if t[6] else None,
            }
            for t in tasks
        ],
        "progress": {"total": total, "done": done, "pct": pct},
        "today_load": {"count": count_today, "minutes": mins_today}
    }

# ─── RUTAS ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("index.html", encoding="utf-8") as f:
        return f.read()

@app.get("/api/tasks")
async def api_tasks():
    return tasks_response()

@app.post("/api/tasks")
async def api_add_task(request: Request):
    body     = await request.json()
    name     = body.get("name", "").strip()
    priority = body.get("priority", "week")
    category = body.get("category", "general")
    estimated= body.get("estimated_minutes")
    deadline = body.get("deadline")
    if not name:
        return {"ok": False}
    task_id = add_task(name, priority, category, estimated, deadline)
    return {"ok": True, "id": task_id, **tasks_response()}

@app.post("/api/tasks/{task_id}/complete")
async def api_complete(task_id: int):
    complete_task(task_id)
    return {"ok": True, **tasks_response()}

@app.post("/api/tasks/{task_id}/uncomplete")
async def api_uncomplete(task_id: int):
    uncomplete_task(task_id)
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

# ─── CHECK-IN ─────────────────────────────────────────────────────────────────

@app.get("/api/checkin")
async def api_checkin():
    """Nova genera el briefing automático al abrir la app."""
    context  = build_context()
    messages = [
        {"role": "system",    "content": CHECKIN_PROMPT},
        {"role": "user",      "content": f"[Contexto]\n{context}"},
        {"role": "assistant", "content": "Entendido."},
        {"role": "user",      "content": "Genera el check-in de apertura del día."}
    ]
    raw = await call_groq(messages)
    clean, _ = parse_and_execute(raw)
    return {"message": clean}

# ─── CHAT ─────────────────────────────────────────────────────────────────────

@app.post("/api/chat")
async def chat(request: Request):
    body     = await request.json()
    messages = body.get("messages", [])
    context  = build_context()

    groq_messages = [
        {"role": "system",    "content": SYSTEM_PROMPT},
        {"role": "user",      "content": f"[Contexto - no lo menciones al usuario]\n{context}"},
        {"role": "assistant", "content": "Entendido."},
    ] + messages

    raw = await call_groq(groq_messages)
    clean_text, action_result = parse_and_execute(raw)

    if messages:
        save_message("user", messages[-1]["content"])
    save_message("assistant", clean_text)

    tr = tasks_response()
    return {
        "message":       clean_text,
        "action_result": action_result,
        "tasks":         tr["tasks"],
        "progress":      [tr["progress"]["total"], tr["progress"]["done"], tr["progress"]["pct"]],
        "today_load":    tr["today_load"]
    }