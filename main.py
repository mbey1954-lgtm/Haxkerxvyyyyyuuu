import os, json, csv, zipfile, re, time, random, asyncio
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, FileResponse
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")

app = FastAPI()

UPLOAD_DIR = "uploads"
BIG_DIR = "big_results"
DATA_FILE = "data.json"
LOG_FILE = "logs.json"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(BIG_DIR, exist_ok=True)

# ---------------- STORAGE ----------------
def load(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default

def save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

DATA = load(DATA_FILE, [])
LOGS = load(LOG_FILE, [])

# ---------------- RATE LIMIT ----------------
RATE_LIMIT = {}
LIMIT = 60  # dakika başı istek

def check_rate(ip):
    now = int(time.time())
    RATE_LIMIT.setdefault(ip, [])
    RATE_LIMIT[ip] = [t for t in RATE_LIMIT[ip] if now - t < 60]
    if len(RATE_LIMIT[ip]) >= LIMIT:
        return False
    RATE_LIMIT[ip].append(now)
    return True

# ---------------- CACHE ----------------
CACHE = {}
CACHE_TTL = 60

def cache_get(key):
    val = CACHE.get(key)
    if val and time.time() - val["time"] < CACHE_TTL:
        return val["data"]
    return None

def cache_set(key, data):
    CACHE[key] = {"time": time.time(), "data": data}

# ---------------- PARSER ----------------
def parse_file(path):
    out = []
    if path.endswith(".txt"):
        with open(path, errors="ignore") as f:
            out += f.read().splitlines()
    elif path.endswith(".csv"):
        with open(path, errors="ignore") as f:
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

# ---------------- TELEGRAM BOT ----------------
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    name = doc.file_name.lower()

    if not any(name.endswith(x) for x in [".txt", ".csv", ".json", ".zip"]):
        await update.message.reply_text("❌ Desteklenmeyen dosya")
        return

    file = await doc.get_file()
    path = f"{UPLOAD_DIR}/{doc.file_name}"
    await file.download_to_drive(path)

    new = parse_zip(path) if path.endswith(".zip") else parse_file(path)
    DATA.extend(new)
    save(DATA_FILE, DATA)

    await update.message.reply_text(
        f"✅ {len(new)} veri alındı\n🌐 API: /api"
    )

async def start_bot():
    bot = Application.builder().token(BOT_TOKEN).build()
    bot.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    await bot.initialize()
    await bot.start()
    await bot.updater.start_polling()

@app.on_event("startup")
async def startup():
    if BOT_TOKEN:
        asyncio.create_task(start_bot())

# ---------------- TEK API ----------------
@app.get("/api")
def api(
    request: Request,
    mode: str = Query("search"),
    query: str = Query(""),
    output: str = Query("json"),
    limit: int = Query(50)
):
    ip = request.client.host
    if not check_rate(ip):
        return JSONResponse({"error": "rate limit"}, status_code=429)

    cache_key = f"{mode}:{query}:{output}:{limit}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    results = []

    if mode == "stats":
        res = {
            "total_data": len(DATA),
            "cache_size": len(CACHE),
            "logs": len(LOGS)
        }
        cache_set(cache_key, res)
        return res

    if mode in ["search", "smart"]:
        for x in DATA:
            if query.lower() in x.lower():
                results.append(x)

    if mode == "strict":
        results = [x for x in DATA if x == query]

    if mode == "regex":
        r = re.compile(query, re.I)
        results = [x for x in DATA if r.search(x)]

    if mode == "random":
        results = random.sample(DATA, min(limit, len(DATA)))

    if mode == "count":
        return {"count": len(results)}

    # LOG
    LOGS.append({
        "ip": ip,
        "mode": mode,
        "query": query,
        "count": len(results),
        "time": int(time.time())
    })
    save(LOG_FILE, LOGS)

    if len(results) > 1000:
        fname = f"{BIG_DIR}/result_{int(time.time())}.txt"
        with open(fname, "w", encoding="utf-8") as f:
            f.write("\n".join(results))
        return {
            "count": len(results),
            "download": "/api?mode=download&query=" + os.path.basename(fname)
        }

    results = results[:limit]

    if output == "txt":
        return PlainTextResponse("\n".join(results))
    if output == "csv":
        return PlainTextResponse("\n".join(results))

    res = {"count": len(results), "results": results}
    cache_set(cache_key, res)
    return res

@app.get("/api", include_in_schema=False)
def download(mode: str = Query(""), query: str = Query("")):
    if mode == "download":
        path = f"{BIG_DIR}/{query}"
        return FileResponse(path)
