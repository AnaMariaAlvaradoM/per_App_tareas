import os
import re
import json
import httpx
import logging
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from database import init_db, add_task, get_tasks, complete_task, delete_task, get_progress

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

init_db()

SYSTEM_PROMPT = """Eres un agente personal de productividad llamado "Aria". Eres inteligente, directa, calida y un poco sarcastica (con afecto). Hablas en espanol.

Tienes acceso a las tareas del usuario. Cuando el usuario quiera agregar, completar, eliminar o ver tareas, responde con un JSON especial ademas de tu mensaje.

FORMATO DE RESPUESTA cuando necesites ejecutar una accion:
Responde SIEMPRE con texto natural primero, luego si hay accion, agrega al final en un solo bloque:
<<<ACTION>>>
{"action": "add_task", "task": "nombre de la tarea"}
<<<END>>>

Si hay multiples acciones, pon un JSON por linea dentro del mismo bloque:
<<<ACTION>>>
{"action": "add_task", "task": "tarea 1"}
{"action": "add_task", "task": "tarea 2"}
<<<END>>>

Acciones disponibles:
- {"action": "add_task", "task": "nombre"}
- {"action": "complete_task", "id": 123}
- {"action": "delete_task", "id": 123}
- {"action": "list_tasks"}
- {"action": "get_progress"}

IMPORTANTE: El bloque <<<ACTION>>> debe estar al FINAL del mensaje y siempre cerrado con <<<END>>>. Nunca pongas el bloque en medio del texto.

Ejemplos:
- "tengo que llamar al medico" -> agrega la tarea, confirma con entusiasmo
- "ya termine el informe" -> completa la tarea si sabes el ID, o pregunta
- "como voy?" -> muestra progreso con comentario motivador

Si el usuario habla de algo que no son tareas, responde normal y con personalidad.
Cuando muestres tareas, usa emojis y se concisa pero calida."""


async def call_gemini(messages: list) -> str:
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
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
                logger.error(f"Action parse error: {e} | raw: {j}")

    return clean_text, " · ".join(results) if results else None


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

    tasks = get_tasks(done=False)
    task_context = "Tareas pendientes actuales:\n"
    if tasks:
        task_context += "\n".join([f"- #{t[0]}: {t[1]}" for t in tasks])
    else:
        task_context += "(ninguna)"
    total, done_count, pct = get_progress()
    task_context += f"\n\nProgreso: {pct}% ({done_count}/{total} completadas)"

    enriched = []
    context_injected = False
    for msg in messages:
        if msg["role"] == "user" and not context_injected:
            enriched.append({
                "role": "user",
                "content": f"[Contexto - no menciones esto]\n{task_context}\n[Fin contexto]\n\n{msg['content']}"
            })
            context_injected = True
        else:
            enriched.append(msg)

    if not enriched:
        enriched = [{"role": "user", "content": task_context}]

    raw = await call_gemini(enriched)
    clean_text, action_result = parse_actions(raw)

    all_tasks = get_tasks(done=None)
    total, done_count, pct = get_progress()

    return {
        "message": clean_text,
        "action_result": action_result,
        "tasks": [{"id": t[0], "name": t[1], "done": bool(t[2])} for t in all_tasks],
        "progress": [total, done_count, pct]
    }