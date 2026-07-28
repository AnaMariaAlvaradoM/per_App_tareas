import os
import re
import logging
from datetime import datetime, timedelta

import pytz
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from database import (
    init_db, add_item, get_items, toggle_item,
    set_due, delete_item, get_due_today_count,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TZ = pytz.timezone("America/Bogota")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

init_db()

DAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def today():
    return datetime.now(TZ).date()


# ─── DETECTOR DE FECHAS EN ESPAÑOL ────────────────────────────────────────────
# Devuelve (texto_limpio, fecha_iso_o_None). Nunca es obligatorio:
# si no encuentra ninguna fecha, retorna el texto tal cual y None.

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
    """Convierte 'YYYY-MM-DD' en etiqueta amigable: hoy, mañana, viernes, 12 ago."""
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
    """Agrega la etiqueta legible y una bandera de urgencia al item."""
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
    count = get_due_today_count()
    if count == 0:
        linea = "No tienes nada con fecha para hoy. ¿Qué traes en la cabeza?"
    elif count == 1:
        linea = "Tienes 1 cosa con fecha para hoy. ¿Qué más?"
    else:
        linea = f"Tienes {count} cosas con fecha para hoy. ¿Qué más?"
    fecha = f"{DAYS_ES[n.weekday()]}, {n.strftime('%H:%M')}"
    return {"saludo": f"{saludo}, Ana.", "linea": linea, "fecha": fecha}


# ─── API ──────────────────────────────────────────────────────────────────────

@app.get("/api/state")
async def state():
    items = [enrich(i) for i in get_items()]
    return {"greeting": greeting(), "items": items}


@app.post("/api/add")
async def add(request: Request):
    data = await request.json()
    raw = (data.get("text") or "").strip()
    if not raw:
        return JSONResponse({"error": "vacío"}, status_code=400)
    clean, due = detect_date(raw)
    item = add_item(clean, due)
    return enrich(item)


@app.post("/api/toggle/{item_id}")
async def toggle(item_id: int):
    item = toggle_item(item_id)
    if not item:
        return JSONResponse({"error": "no existe"}, status_code=404)
    return enrich(item)


@app.post("/api/due/{item_id}")
async def due(item_id: int, request: Request):
    data = await request.json()
    when = data.get("when")  # 'today' | 'tomorrow' | 'clear'
    if when == "today":
        val = today().isoformat()
    elif when == "tomorrow":
        val = (today() + timedelta(days=1)).isoformat()
    else:
        val = None
    item = set_due(item_id, val)
    if not item:
        return JSONResponse({"error": "no existe"}, status_code=404)
    return enrich(item)


@app.delete("/api/item/{item_id}")
async def remove(item_id: int):
    delete_item(item_id)
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def home():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/health")
async def health():
    return {"ok": True}
