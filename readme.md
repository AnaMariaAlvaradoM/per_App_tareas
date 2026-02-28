# ✦ Aria — Agente web de tareas con IA

Interfaz web dark mode con IA conversacional (Gemini) para gestionar tareas.

## Archivos
- `main.py` — Backend FastAPI con integración Gemini
- `database.py` — Base de datos SQLite
- `index.html` — Interfaz web
- `requirements.txt` — Dependencias

---

## 🚀 Deploy en Render

1. Sube los 4 archivos a un repo de GitHub
2. En Render: **New → Web Service** → conecta el repo
3. Configura:
   - **Runtime:** Python 3.11
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. En **Environment Variables** agrega:
   - `GEMINI_API_KEY` = tu key de Google AI Studio
5. Agrega el archivo `.python-version` con contenido `3.11.9`
6. Deploy ✅

La URL de Render será tu app web. Ábrela en el navegador del PC.

---

## 🧪 Probar local

```bash
pip install -r requirements.txt
export GEMINI_API_KEY="tu_key"
uvicorn main:app --reload
```

Abre http://localhost:8000