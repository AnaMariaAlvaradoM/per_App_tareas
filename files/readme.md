# Nova — tu caja para vaciar la cabeza

Una sola caja. Escribes lo que sea (tarea, idea, pendiente) y queda guardado.
Si escribes "mañana" o "el viernes", Nova le pone fecha sola. Nunca es obligatorio.

## Qué hay aquí

- `main.py` — backend FastAPI + detector de fechas en español
- `database.py` — base de datos PostgreSQL (Supabase)
- `index.html` — toda la interfaz (HTML + CSS + JS en un archivo)
- `requirements.txt` — dependencias
- `render.yaml` — configura el deploy solo
- `.python-version` — fija Python 3.11.9

---

## Cómo subirlo y ejecutarlo

### 1. Subir al repo
Reemplaza los archivos viejos de tu repo por estos y haz push:

```
git add .
git commit -m "Nova v2 - caja unica"
git push
```

### 2. Configurar en Render
Si ya tienes el servicio: solo se redeploya solo con el push.
Si lo creas de nuevo: **New → Web Service** → conecta el repo. El `render.yaml`
llena todo automático. Solo revisa que el Start Command sea:

```
uvicorn main:app --host 0.0.0.0 --port $PORT
```

### 3. La variable que necesita
En Render → Environment, agrega:

- `DATABASE_URL` = la cadena de conexión de tu Supabase
  (Supabase → Project Settings → Database → Connection string → URI)

Con eso arranca.

---

## Probar en tu compu (opcional)

```
pip install -r requirements.txt
export DATABASE_URL="tu_cadena_de_supabase"
uvicorn main:app --reload
```

Abre http://localhost:8000

---

© 2026 Ana Alvarado
