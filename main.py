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

DAYS_ES = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

def get_datetime_context() -> str:
    now = datetime.now(TZ)
    day_name = DAYS_ES[now.weekday()]
    month_name = MONTHS_ES[now.month - 1]
    return f"{day_name} {now.day} de {month_name} de {now.year}, {now.strftime('%H:%M')} (hora Colombia)"

SYSTEM_PROMPT = """Eres Aria, una asistente personal de productividad. Eres inteligente, directa, calida y un poco sarcastica con afecto. Hablas en espanol colombiano.

MEMORIA: Tienes acceso al historial de conversaciones anteriores y del dia de hoy. Usalo para recordar contexto, tareas mencionadas, y preferencias del usuario. Si el usuario menciona algo que ya hablaron, reconocelo naturalmente.

FECHA Y HORA: Siempre sabes la fecha y hora actual porque te la dan al inicio de cada mensaje. Usala naturalmente cuando sea relevante.

TAREAS: Cuando el usuario quiera agregar, completar, eliminar o ver tareas, usa el formato de accion al final del mensaje:

<<<ACTION>>>
{"action": "add_task", "task": "nombre"}
<<<END>>>

Para multiples acciones, un JSON por linea dentro del mismo bloque.

Acciones disponibles:
- {"action": "add_task", "task": "nombre"}
- {"action": "complete_task", "id": 123}
- {"action": "delete_task", "id": 123}
- {"action": "list_tasks"}
- {"action": "get_progress"}

IMPORTANTE:
- El bloque <<<ACTION>>> siempre al FINAL, siempre cerrado con <<<END>>>
- Nunca muestres el bloque ACTION en tu respuesta visible
- Si el usuario dice "elimina todas las tareas excepto X", elimina una por una
- No dupliques tareas, verifica la lista actual antes de agregar"""


async def call_groq(messages: list) -> str:
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1024
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(GROQ_URL, json=payload, headers=headers)
        if r.status_code == 429:
            return "Muchas consultas seguidas, espera unos segundos."
        if r.status_code != 200:
            logger.error(f"Groq error {r.status_code}: {r.text}")
            return f"Error ({r.status_code}), intenta de nuevo."
        data = r.json()
        return data["choices"][0]["message"]["content"]


def execute_action(action_json: dict) -> str:
    action = action_json.get("action")
    if action == "add_task":
        task_id = add_task(action_json["task"])
        return f"Tarea #{task_id} agregada"
    elif action == "complete_task":
        name = complete_task(action_json["id"])
        return f"'{name}' completada" if name else "No encontre esa tarea"
    elif action == "delete_task":
        delete_task(action_json["id"])
        return "Tarea eliminada"
    elif action == "list_tasks":
        tasks = get_tasks(done=False)
        if not tasks:
            return "Sin tareas pendientes"
        return "\n".join([f"#{t[0]} {t[1]}" for t in tasks])
    elif action == "get_progress":
        total, done, pct = get_progress()
        return f"Progreso: {pct}% ({done}/{total})"
    return ""


def parse_actions(raw: str):
    clean_text = raw
    results = []
    if "<<<ACTION>>>" in raw and "<<<END>>>" in raw:
        parts = raw.split("<<<ACTION>>>")
        clean_text = parts[0].strip()
        action_block = parts[1].split("<<<END>>>")[0].strip()
        jsons = re.findall(r'\{[^{}]+\}', action_block)
        for j in jsons:
            try:
                action_json = json.loads(j)
                result = execute_action(action_json)
                if result:
                    results.append(result)
            except Exception as e:
                logger.error(f"Action parse error: {e}")
    return clean_text, " · ".join(results) if results else None


def build_context() -> str:
    """Construye el contexto completo para Aria: fecha, tareas y memoria"""
    now_str = get_datetime_context()

    # Tareas actuales
    tasks = get_tasks(done=False)
    task_context = "Tareas pendientes:\n"
    if tasks:
        task_context += "\n".join([f"- #{t[0]}: {t[1]}" for t in tasks])
    else:
        task_context += "(ninguna)"
    total, done_count, pct = get_progress()
    task_context += f"\nProgreso general: {pct}% ({done_count}/{total})"

    # Historial de hoy
    today_msgs = get_today_messages()
    today_context = ""
    if today_msgs:
        today_context = "\nConversacion de hoy (para tu memoria, no la repitas):\n"
        for role, content in today_msgs[-20:]:  # ultimos 20 mensajes de hoy
            label = "Usuario" if role == "user" else "Aria"
            today_context += f"{label}: {content[:200]}\n"

    # Memoria de dias anteriores (resumida)
    recent = get_recent_messages(days=7)
    memory_context = ""
    if recent:
        memory_context = "\nMemoria de dias anteriores (resumen):\n"
        for role, content, day in recent[-15:]:
            label = "Usuario" if role == "user" else "Aria"
            memory_context += f"[{day}] {label}: {content[:150]}\n"

    return f"""Fecha y hora actual: {now_str}

{task_context}
{today_context}
{memory_context}"""


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("index.html", encoding="utf-8") as f:
        return f.read()


@app.get("/api/tasks")
async def api_tasks():
    tasks = get_tasks(done=None)
    total, done_count, pct = get_progress()
    return {
        "tasks": [{"id": t[0], "name": t[1], "done": bool(t[2])} for t in tasks],
        "progress": {"total": total, "done": done_count, "pct": pct}
    }


@app.post("/api/tasks/{task_id}/complete")
async def api_complete_task(task_id: int):
    complete_task(task_id)
    tasks = get_tasks(done=None)
    total, done_count, pct = get_progress()
    return {
        "ok": True,
        "tasks": [{"id": t[0], "name": t[1], "done": bool(t[2])} for t in tasks],
        "progress": {"total": total, "done": done_count, "pct": pct}
    }


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])

    context = build_context()

    # Construir mensajes para Groq: system + contexto + conversacion actual
    groq_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"[Contexto - no lo menciones]\n{context}\n[Fin contexto]"},
        {"role": "assistant", "content": "Entendido, tengo el contexto."},
    ] + messages

    raw = await call_groq(groq_messages)
    clean_text, action_result = parse_actions(raw)

    # Guardar en memoria
    if messages:
        last_user = messages[-1]["content"]
        save_message("user", last_user)
    save_message("assistant", clean_text)

    all_tasks = get_tasks(done=None)
    total, done_count, pct = get_progress()

    return {
        "message": clean_text,
        "action_result": action_result,
        "tasks": [{"id": t[0], "name": t[1], "done": bool(t[2])} for t in all_tasks],
        "progress": [total, done_count, pct]
    }