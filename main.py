import os
import re
import logging
from datetime import datetime, timedelta

import pytz
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from database import (
    init_db, add_item, get_items, toggle_item,
    set_due, delete_item, get_today_count,
    edit_item, reorder_items, toggle_today, toggle_important,
    count_today_active, TODAY_LIMIT,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TZ = pytz.timezone("America/Bogota")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

init_db()

# Íconos de la PWA. manifest.json y sw.js se sirven aparte, en la raíz,
# para que el service worker controle todo el sitio sin cabeceras extra.
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

DAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def today():
    return datetime.now(TZ).date()


# ─── DETECTOR DE FECHAS EN ESPAÑOL ────────────────────────────────────────────
# Devuelve (texto_limpio, fecha_iso_o_None). Nunca es obligatorio:
# si no encuentra ninguna fecha, retorna el texto tal cual y None.
# Esto es lo único que "organiza" una tarea al capturarla, y lo hace leyendo
# el texto que ya ibas a escribir de todos modos: no es un campo ni una
# pregunta adicional.

WEEKDAYS = {
    "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2,
    "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5, "domingo": 6,
}


def _next_weekday(target_wd: int):
    d = today()
    ahead = (target_wd - d.weekday()) % 7
    if ahead == 0:
        ahead = 7  # "el lunes" siempre apunta al próximo, no a hoy
    return d + timedelta(days=ahead)


def detect_date(text: str):
    """
    Busca una expresión de fecha en el texto.
    Retorna (texto_sin_la_expresion, 'YYYY-MM-DD') o (texto_original, None).
    """
    low = text.lower()

    patterns = [
        (r"\bpasado\s+mañana\b", lambda: today() + timedelta(days=2)),
        (r"\bmañana\b", lambda: today() + timedelta(days=1)),
        (r"\bhoy\b", lambda: today()),
        (r"\bel\s+lunes\b|\blunes\b", lambda: _next_weekday(0)),
        (r"\bel\s+martes\b|\bmartes\b", lambda: _next_weekday(1)),
        (r"\bel\s+miércoles\b|\bel\s+miercoles\b|\bmiércoles\b|\bmiercoles\b", lambda: _next_weekday(2)),
        (r"\bel\s+jueves\b|\bjueves\b", lambda: _next_weekday(3)),
        (r"\bel\s+viernes\b|\bviernes\b", lambda: _next_weekday(4)),
        (r"\bel\s+sábado\b|\bel\s+sabado\b|\bsábado\b|\bsabado\b", lambda: _next_weekday(5)),
        (r"\bel\s+domingo\b|\bdomingo\b", lambda: _next_weekday(6)),
        (r"\besta\s+semana\b", lambda: _next_weekday(4)),  # apunta al viernes
    ]

    for pat, fn in patterns:
        m = re.search(pat, low)
        if m:
            d = fn()
            # Quita la expresión detectada y palabras conectoras sueltas
            clean = re.sub(pat, "", text, flags=re.IGNORECASE)
            clean = re.sub(r"\s+(para|el|este|esta)\s*$", "", clean, flags=re.IGNORECASE)
            clean = re.sub(r"\s{2,}", " ", clean).strip(" ,.-")
            return (clean or text.strip()), d.isoformat()

    return text.strip(), None


def human_due(due_iso: str):
    """Convierte 'YYYY-MM-DD' en etiqueta amigable: mañana, viernes, 12 ago, vencida."""
    if not due_iso:
        return None
    d = datetime.fromisoformat(due_iso).date()
    t = today()
    delta = (d - t).days
    if delta < 0:
        return "vencida"
    if delta == 0:
        return "hoy"
    if delta == 1:
        return "mañana"
    if 2 <= delta <= 6:
        return DAYS_ES[d.weekday()]
    return f"{d.day} {MONTHS_ES[d.month - 1][:3]}"


def enrich(item: dict):
    """Agrega la etiqueta legible de fecha y una bandera de urgencia al item."""
    if not item:
        return item
    label = human_due(item.get("due"))
    item["due_label"] = label
    item["urgent"] = label in ("hoy", "mañana", "vencida")
    return item


def greeting():
    n = datetime.now(TZ)
    hora = n.hour
    if hora < 12:
        saludo = "Buenos días"
    elif hora < 19:
        saludo = "Buenas tardes"
    else:
        saludo = "Buenas noches"
    count = get_today_count()
    if count == 0:
        linea = "Todavía no eliges tus prioridades de hoy."
    elif count == 1:
        linea = "Tienes 1 prioridad elegida para hoy."
    else:
        linea = f"Tienes {count} prioridades elegidas para hoy."
    fecha = f"{DAYS_ES[n.weekday()]}, {n.day} de {MONTHS_ES[n.month - 1]}"
    return {"saludo": f"{saludo}, Ana.", "linea": linea, "fecha": fecha}


def classify(all_items):
    """
    Reparte los items ya enriquecidos en las cuatro vistas del producto.
    La regla es siempre sobre los datos, nunca sobre una acción manual de
    'archivar': Inbox es simplemente lo que no tiene fecha ni está en Hoy.
    """
    completed = sorted(
        (i for i in all_items if i["done"]),
        key=lambda i: i["completed_at"] or "",
        reverse=True,
    )
    active = [i for i in all_items if not i["done"]]
    today_list = [i for i in active if i["today"]]
    upcoming = sorted(
        (i for i in active if not i["today"] and i["due"]),
        key=lambda i: i["due"],
    )
    inbox = [i for i in active if not i["today"] and not i["due"]]
    return inbox, today_list, upcoming, completed


# ─── API ──────────────────────────────────────────────────────────────────────

@app.get("/api/state")
async def state():
    all_items = [enrich(i) for i in get_items()]
    inbox, today_list, upcoming, completed = classify(all_items)
    return {
        "greeting": greeting(),
        "inbox": inbox,
        "today": today_list,
        "upcoming": upcoming,
        "completed": completed,
        "today_limit": TODAY_LIMIT,
    }


@app.post("/api/add")
async def add(request: Request):
    data = await request.json()
    raw = (data.get("text") or "").strip()
    if not raw:
        return JSONResponse({"error": "vacío"}, status_code=400)

    # El "Deshacer" de borrar reenvía el item completo (texto, fecha,
    # today, important) para restaurarlo tal cual estaba, en vez de
    # pasar solo el texto por la detección automática de fechas.
    restoring = any(k in data for k in ("due", "today", "important"))
    if restoring:
        clean = raw
        due = data.get("due")
        today_flag = bool(data.get("today"))
        important_flag = bool(data.get("important"))
    else:
        clean, detected = detect_date(raw)
        if detected == today().isoformat():
            due, today_flag = None, True
        elif detected:
            due, today_flag = detected, False
        else:
            due, today_flag = None, False
        important_flag = False

    capped = False
    if today_flag and count_today_active() >= TODAY_LIMIT:
        # Nunca bloqueamos la captura: si Hoy ya está lleno, la tarea
        # igual se guarda, solo que cae en Inbox en vez de en Hoy.
        today_flag = False
        capped = True

    item = add_item(clean, due, today_flag, important_flag)
    result = enrich(item)
    result["capped"] = capped
    return result


@app.post("/api/toggle/{item_id}")
async def toggle(item_id: int):
    item = toggle_item(item_id)
    if not item:
        return JSONResponse({"error": "no existe"}, status_code=404)
    return enrich(item)


@app.post("/api/today/{item_id}")
async def today_route(item_id: int):
    result = toggle_today(item_id)
    if result is None:
        return JSONResponse({"error": "no existe"}, status_code=404)
    if result.get("limit_reached"):
        return JSONResponse(
            {"error": "limite", "limit": TODAY_LIMIT},
            status_code=409,
        )
    return enrich(result)


@app.post("/api/important/{item_id}")
async def important_route(item_id: int):
    item = toggle_important(item_id)
    if not item:
        return JSONResponse({"error": "no existe"}, status_code=404)
    return enrich(item)


@app.post("/api/due/{item_id}")
async def due(item_id: int, request: Request):
    data = await request.json()
    when = data.get("when")  # 'tomorrow' | 'clear'
    val = (today() + timedelta(days=1)).isoformat() if when == "tomorrow" else None
    item = set_due(item_id, val)
    if not item:
        return JSONResponse({"error": "no existe"}, status_code=404)
    return enrich(item)


@app.post("/api/edit/{item_id}")
async def edit(item_id: int, request: Request):
    data = await request.json()
    text = (data.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "vacío"}, status_code=400)
    item = edit_item(item_id, text)
    if not item:
        return JSONResponse({"error": "no existe"}, status_code=404)
    return enrich(item)


@app.post("/api/reorder")
async def reorder(request: Request):
    data = await request.json()
    ids = data.get("ids") or []
    reorder_items(ids)
    return {"ok": True}


@app.delete("/api/item/{item_id}")
async def remove(item_id: int):
    delete_item(item_id)
    return {"ok": True}


# ─── App shell y PWA ───────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/manifest.json")
async def manifest():
    return FileResponse("manifest.json", media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker():
    # Servido en la raíz (no en /static) para que el scope por defecto
    # del service worker sea "/" sin necesitar la cabecera Service-Worker-Allowed.
    return FileResponse("sw.js", media_type="application/javascript")


@app.get("/favicon.ico")
async def favicon():
    # El navegador la pide solo por costumbre; sin esta ruta queda un 404
    # inofensivo pero ruidoso en la consola. Reusa el ícono de la PWA.
    path = "static/icons/icon-192.png"
    if os.path.isfile(path):
        return FileResponse(path, media_type="image/png")
    return JSONResponse({"error": "no favicon"}, status_code=404)


@app.get("/health")
async def health():
    return {"ok": True}
