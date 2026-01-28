import os, json, csv, zipfile, re, time, random, asyncio
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, FileResponse
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

# ===================== CONFIG =====================
BOT_TOKEN = os.getenv("BOT_TOKEN")  # opsiyonel
UPLOAD_DIR = "uploads"
BIG_DIR = "big_results"
DATA_FILE = "data.json"
LOG_FILE = "logs.json"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(BIG_DIR, exist_ok=True)

app = FastAPI()

# ===================== UTILS =====================
def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

DATA = load_json(DATA_FILE, [])
LOGS = load_json(LOG_FILE, [])

# ===================== RATE LIMIT (IP) =====================
RATE = {}
PER_MIN = 60  # dakika başı istek

def allow(ip):
    now = int(time.time())
    RATE.setdefault(ip, [])
    RATE[ip] = [t for t in RATE[ip] if now - t < 60]
    if len(RATE[ip]) >= PER_MIN:
        return False
    RATE[ip].append(now)
    return True

# ===================== CACHE =====================
CACHE = {}
CACHE_TTL = 60

def cache_get(k):
    v = CACHE.get(k)
    if v and time.time() - v["t"] < CACHE_TTL:
        return v["d"]
    return None

def cache_set(k, d):
    CACHE[k] = {"t": time.time(), "d": d}

# ===================== PARSER =====================
def parse_file(path):
    out = []
    if path.endswith(".txt"):
        with open(path, errors="ignore") as f:
            out += f.read().splitlines()
    elif path.endswith(".csv"):
        with open(path, errors="ignore", newline="") as f:
            for r in csv.reader(f):
                out.append(" | ".join(r))
    elif path.endswith(".json"):
        with open(path, errors="ignore") as f:
            out.append(json.dumps(json.load(f), ensure_ascii=False))
    return out

def parse_zip(path):
    res = []
    with zipfile.ZipFile(path) as z:
        z.extractall(UPLOAD_DIR)
        for n in z.namelist():
            p = os.path.join(UPLOAD_DIR, n)
            if os.path.isfile(p):
                res += parse_file(p)
    return res

# ===================== TELEGRAM BOT (DOSYA YÜKLEME) =====================
async def handle_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    name = doc.file_name.lower()
    if not any(name.endswith(x) for x in (".txt", ".csv", ".json", ".zip")):
        await update.message.reply_text("❌ Desteklenmeyen dosya")
        return

    tg = await doc.get_file()
    path = os.path.join(UPLOAD_DIR, doc.file_name)
    await tg.download_to_drive(path)

    new = parse_zip(path) if path.endswith(".zip") else parse_file(path)
    DATA.extend(new)
    save_json(DATA_FILE, DATA)

    await update.message.reply_text(
        f"✅ {len(new)} veri alındı\n🌐 API: https://zordoxflexapi.onrender.com/api"
    )

async def start_bot():
    if not BOT_TOKEN:
        return
    app_bot = Application.builder().token(BOT_TOKEN).build()
    app_bot.add_handler(MessageHandler(filters.Document.ALL, handle_doc))
    await app_bot.initialize()
    await app_bot.start()
    await app_bot.updater.start_polling()

@app.on_event("startup")
async def startup():
    asyncio.create_task(start_bot())

# ===================== TEK API =====================
@app.get("/api")
def api(
    request: Request,
    mode: str = Query("search"),
    query: str = Query(""),
    output: str = Query("json"),   # json | txt | csv
    limit: int = Query(50),
    file: str = Query("")          # download için
):
    ip = request.client.host
    if not allow(ip):
        return JSONResponse({"error": "rate_limit"}, status_code=429)

    cache_key = f"{mode}:{query}:{output}:{limit}:{file}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    # ---- DOWNLOAD (aynı endpoint) ----
    if mode == "download":
        path = os.path.join(BIG_DIR, file)
        if os.path.exists(path):
            return FileResponse(path, media_type="text/plain")
        return JSONResponse({"error": "file_not_found"}, status_code=404)

    # ---- STATS ----
    if mode == "stats":
        res = {
            "total_data": len(DATA),
            "cache_items": len(CACHE),
            "logs": len(LOGS)
        }
        cache_set(cache_key, res)
        return res

    results = []

    # ---- SEARCH MODES ----
    if mode in ("search", "smart"):
        q = query.lower()
        for x in DATA:
            if q in x.lower():
                results.append(x)

    elif mode == "strict":
        results = [x for x in DATA if x == query]

    elif mode == "regex":
        try:
            r = re.compile(query, re.I)
            results = [x for x in DATA if r.search(x)]
        except re.error:
            return JSONResponse({"error": "bad_regex"}, status_code=400)

    elif mode == "random":
        results = random.sample(DATA, min(limit, len(DATA)))

    elif mode == "count":
        return {"count": len(results)}

    # ---- LOG ----
    LOGS.append({
        "ip": ip,
        "mode": mode,
        "query": query,
        "count": len(results),
        "ts": int(time.time())
    })
    save_json(LOG_FILE, LOGS)

    # ---- BIG RESULT -> TXT ----
    if len(results) > 1000:
        fname = f"result_{int(time.time())}.txt"
        fpath = os.path.join(BIG_DIR, fname)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write("\n".join(results))
        res = {
            "count": len(results),
            "download": f"/api?mode=download&file={fname}"
        }
        cache_set(cache_key, res)
        return res

    results = results[:limit]

    # ---- OUTPUT ----
    if output == "txt":
        return PlainTextResponse("\n".join(results))
    if output == "csv":
        return PlainTextResponse("\n".join(results))

    res = {"count": len(results), "results": results}
    cache_set(cache_key, res)
    return res
