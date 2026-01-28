import os
import json
import re
import zipfile
import asyncio
from fastapi import FastAPI, Request, HTTPException
from starlette.responses import Response
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ──────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN")
BASE_URL = os.environ.get("BASE_URL")

if not BOT_TOKEN or not BASE_URL:
    raise RuntimeError("BOT_TOKEN ve BASE_URL ortam değişkenleri tanımlı değil!")

DATA_DIR = "data"
STATE_FILE = os.path.join(DATA_DIR, "state.json")

os.makedirs(DATA_DIR, exist_ok=True)

if not os.path.exists(STATE_FILE):
    with open(STATE_FILE, "w") as f:
        json.dump({}, f)

def load_state() -> dict:
    with open(STATE_FILE, "r") as f:
        return json.load(f)

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def clean_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9_]", "", name)
    return name

# ──────────────────────────────────────────────
app = FastAPI(title="LordApiV3")

application = Application.builder().token(BOT_TOKEN).build()

# ───── Çok seyrek progress güncelleme (hız için) ─────
async def update_progress(msg, percent: int, prefix=""):
    if percent not in (10, 30, 50, 70, 90, 100):
        return
    bar = "█" * (percent // 10) + "░" * (10 - percent // 10)
    try:
        await msg.edit_text(f"{prefix} %{percent}  {bar}", parse_mode="Markdown")
    except:
        pass

# ───── Hızlı birleştirme ─────
def fast_combine_txt(file_paths: list[str]) -> str:
    chunks = []
    for path in file_paths:
        try:
            with open(path, "rb") as f:                     # binary oku → daha hızlı
                content = f.read().decode("utf-8", errors="ignore")
                if content:
                    chunks.append(content.rstrip("\r\n"))
        except:
            continue
    return "\n\n".join(chunks) + "\n"

# ───── Dosya yükleme ─────
async def file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        return

    doc = update.message.document
    fname_lower = doc.file_name.lower()
    base = clean_name(os.path.splitext(doc.file_name)[0] or "data")

    progress_msg = await update.message.reply_text("📥 Başlatılıyor...")

    file_obj = await doc.get_file()
    tmp_path = os.path.join(DATA_DIR, f"t_{doc.file_id[:10]}")

    await update_progress(progress_msg, 10, "İndiriliyor")
    await file_obj.download_to_drive(tmp_path)
    await update_progress(progress_msg, 30, "İndirildi")

    state = load_state()
    api_name = base + "_result"
    final_path = os.path.join(DATA_DIR, f"{api_name}.txt")

    txt_paths = []
    unzip_folder = None

    if fname_lower.endswith(".zip"):
        await update_progress(progress_msg, 40, "ZIP açılıyor")
        unzip_folder = os.path.join(DATA_DIR, f"z_{doc.file_id[:8]}")
        os.makedirs(unzip_folder, exist_ok=True)

        try:
            with zipfile.ZipFile(tmp_path, "r") as zf:
                zf.extractall(unzip_folder)

            for dirpath, _, filenames in os.walk(unzip_folder):
                for fn in filenames:
                    if fn.lower().endswith(".txt"):
                        txt_paths.append(os.path.join(dirpath, fn))

            await update_progress(progress_msg, 70, "Okunuyor")

        except Exception as e:
            await progress_msg.edit_text(f"ZIP hatası: {str(e)[:70]}")
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return

    elif fname_lower.endswith(".txt"):
        txt_paths = [tmp_path]
        await update_progress(progress_msg, 65, "Okunuyor")

    else:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        await progress_msg.edit_text("Sadece .txt veya .zip kabul edilir")
        return

    if not txt_paths:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        await progress_msg.edit_text("Hiç .txt dosyası bulunamadı")
        return

    # Hızlı birleştir + yaz
    combined_text = fast_combine_txt(txt_paths)
    with open(final_path, "w", encoding="utf-8", buffering=32768) as outf:
        outf.write(combined_text)

    # Temizlik (minimum)
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

    if unzip_folder and os.path.exists(unzip_folder):
        try:
            for root, dirs, files in os.walk(unzip_folder, topdown=False):
                for name in files: os.remove(os.path.join(root, name))
                for name in dirs: os.rmdir(os.path.join(root, name))
            os.rmdir(unzip_folder)
        except:
            pass

    state[api_name] = {"active": True}
    save_state(state)

    await update_progress(progress_msg, 100, "Tamamlandı")
    await asyncio.sleep(0.6)

    await progress_msg.edit_text(
        f"✅ API hazır\n"
        f"{BASE_URL}/search/{api_name}?q=kelime\n\n"
        f"{len(txt_paths)} dosya birleştirildi"
    )


# Komutlar (değişmedi)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Sistem aktif\n"
        ".txt veya .zip at → hızlı işlenir\n"
        "/listele   /sil   /kapat   /ac"
    )

async def listele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    if not state:
        await update.message.reply_text("Henüz API yok")
        return
    lines = [f"• {k} → {'🟢' if v.get('active') else '🔴'}" for k,v in state.items()]
    await update.message.reply_text("\n".join(lines) or "Liste boş")

async def kapat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("/kapat <isim>")
    n = clean_name(context.args[0])
    s = load_state()
    if n in s:
        s[n]["active"] = False
        save_state(s)
        await update.message.reply_text(f"{n} kapatıldı")
    else:
        await update.message.reply_text("Bulunamadı")

async def ac(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("/ac <isim>")
    n = clean_name(context.args[0])
    s = load_state()
    if n in s:
        s[n]["active"] = True
        save_state(s)
        await update.message.reply_text(f"{n} açıldı")
    else:
        await update.message.reply_text("Bulunamadı")

async def sil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("/sil <isim>")
    n = clean_name(context.args[0])
    s = load_state()
    if n in s:
        s.pop(n, None)
        save_state(s)
        try: os.remove(os.path.join(DATA_DIR, f"{n}.txt"))
        except: pass
        await update.message.reply_text(f"{n} silindi")
    else:
        await update.message.reply_text("Bulunamadı")


# Handler'lar
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("listele", listele))
application.add_handler(CommandHandler("kapat", kapat))
application.add_handler(CommandHandler("ac", ac))
application.add_handler(CommandHandler("sil", sil))
application.add_handler(MessageHandler(filters.Document.ALL, file_upload))


# ───── Arama – önizleme eklendi ─────
@app.get("/search/{dataset}")
async def search(dataset: str, q: str = ""):
    dataset = clean_name(dataset)
    state = load_state()
    if dataset not in state or not state[dataset].get("active", False):
        raise HTTPException(404, "API kapalı veya mevcut değil")

    path = os.path.join(DATA_DIR, f"{dataset}.txt")
    if not os.path.exists(path):
        raise HTTPException(404, "Veri dosyası yok")

    results = []
    preview_lines = []

    with open(path, "r", encoding="utf-8", errors="ignore", buffering=32768) as f:
        for i, line in enumerate(f):
            stripped = line.strip()
            if stripped and q.lower() in stripped.lower():
                results.append(stripped)
            if i < 15:  # ilk 15 satırı önizleme için sakla
                preview_lines.append(stripped)
            if len(results) >= 1500:
                break

    # Tarayıcıda girildiğinde ilk 15 satırı da göster (önizleme)
    response_data = {
        "count": len(results),
        "results": results if len(results) <= 120 else None,
        "preview": preview_lines,
        "note": "Çok sonuç var → txt olarak indirin" if len(results) > 120 else None
    }

    if len(results) > 120:
        content = "\n".join(results)
        return Response(
            content=content,
            media_type="text/plain",
            headers={"Content-Disposition": "attachment; filename=sonuclar.txt"}
        )

    return response_data


# Webhook & yaşam döngüsü
@app.on_event("startup")
async def on_startup():
    await application.initialize()
    url = f"{BASE_URL.rstrip('/')}/webhook"
    await application.bot.set_webhook(url=url, drop_pending_updates=True)
    print("Webhook ayarlandı:", url)

@app.on_event("shutdown")
async def on_shutdown():
    await application.stop()
    await application.shutdown()

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        if update:
            await application.process_update(update)
    except:
        pass
    return {"ok": True}

@app.get("/")
def root():
    return {"status": "online"}
