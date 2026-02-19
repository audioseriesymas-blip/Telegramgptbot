import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Forbidden
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# =========================
# CONFIG
# =========================
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise SystemExit("Falta BOT_TOKEN. Configúralo como variable de entorno (Railway -> Variables).")

# ✅ Supergrupo privado de administración (CONTROL)
CONTROL_CHAT_ID = -1003756200229

# ✅ Donde se registran (log) los usuarios que usan el bot
# - Si quieres OTRO grupo solo para logs, pon aquí el chat_id de ese grupo.
# - Si lo quieres en el mismo grupo de admins, déjalo igual a CONTROL_CHAT_ID.
LOG_CHAT_ID = CONTROL_CHAT_ID

# ✅ Canales/grupos donde el bot hará su trabajo (invites + ban/unban)
CHANNELS = [
    {"chat_id": -1003877705833, "name": "Canal 1"},
    {"chat_id": -1002998097334, "name": "Canal 2"},
    {"chat_id": -1003854066318, "name": "Canal 3"},
    {"chat_id": -1003221448109, "name": "Canal 4"},
    {"chat_id": -1003264357260, "name": "Canal 5"},
    {"chat_id": -1003324985431, "name": "Canal 6"},
    {"chat_id": -1003370421522, "name": "Canal 7"},
    {"chat_id": -1003407490516, "name": "Canal 8"},
    {"chat_id": -1003491698527, "name": "Canal 9"},
    {"chat_id": -1003690502177, "name": "Canal 10"},
]

TZ = ZoneInfo("America/Mexico_City")
DB_PATH = "members.db"
LINK_EXPIRE_HOURS = 24


# =========================
# UTIL
# =========================
MESES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"
}

def ahora() -> datetime:
    return datetime.now(TZ)

def fecha_es(dt: datetime) -> str:
    return f"{dt.day} de {MESES[dt.month]} de {dt.year}"

def hora_es(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S")

def nombre_completo(first: str | None, last: str | None) -> str:
    first = first or ""
    last = last or ""
    full = (first + " " + last).strip()
    return full if full else "Usuario"

async def is_control_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Solo obedece comandos si:
       1) vienen del grupo CONTROL_CHAT_ID
       2) el usuario es admin/owner de ese grupo
    """
    if not update.effective_chat or not update.effective_user:
        return False
    if update.effective_chat.id != CONTROL_CHAT_ID:
        return False
    try:
        member = await context.bot.get_chat_member(CONTROL_CHAT_ID, update.effective_user.id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

async def log_usuario(context: ContextTypes.DEFAULT_TYPE, user, accion: str) -> None:
    """Manda al grupo de logs la info del usuario + fecha/hora."""
    if not LOG_CHAT_ID:
        return
    try:
        dt = ahora()
        uname = f"@{user.username}" if getattr(user, "username", None) else "(sin username)"
        full = nombre_completo(getattr(user, "first_name", None), getattr(user, "last_name", None))
        msg = (
            f"📌 Registro ({accion})\n"
            f"Nombre: {full}\n"
            f"ID: {user.id}\n"
            f"Username: {uname}\n"
            f"Fecha: {fecha_es(dt)}\n"
            f"Hora: {hora_es(dt)}"
        )
        await context.bot.send_message(chat_id=LOG_CHAT_ID, text=msg)
    except Exception:
        # Si el bot no está en el grupo de logs o no tiene permisos, no lo detengas
        pass


# =========================
# DB
# =========================
def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id           INTEGER PRIMARY KEY,
            username          TEXT,
            first_name        TEXT,
            last_name         TEXT,
            first_contact_at  TEXT,
            last_contact_at   TEXT
        );
    """)
    conn.commit()
    return conn

def upsert_user(conn: sqlite3.Connection, user) -> None:
    now_iso = ahora().isoformat(timespec="seconds")
    username = f"@{user.username}" if user.username else None
    conn.execute("""
        INSERT INTO users(user_id, username, first_name, last_name, first_contact_at, last_contact_at)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name,
            last_name=excluded.last_name,
            last_contact_at=excluded.last_contact_at;
    """, (user.id, username, user.first_name, user.last_name, now_iso, now_iso))
    conn.commit()


# =========================
# INVITES / LINKS
# =========================
async def generar_enlaces_unicos(bot, user_id: int) -> list[tuple[str, str]]:
    """Links 1 persona por enlace, expira en LINK_EXPIRE_HOURS."""
    links = []
    expira = int((datetime.utcnow() + timedelta(hours=LINK_EXPIRE_HOURS)).timestamp())

    for ch in CHANNELS:
        invite = await bot.create_chat_invite_link(
            chat_id=ch["chat_id"],
            name=f"user{user_id}-{int(datetime.utcnow().timestamp())}",
            member_limit=1,
            expire_date=expira,
        )
        links.append((ch["name"], invite.invite_link))
    return links

async def enviar_links_por_dm(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    links = await generar_enlaces_unicos(context.bot, user_id)
    msg = f"✅ Aquí están tus accesos (1 persona por enlace, expiran en {LINK_EXPIRE_HOURS}h):\n\n"
    for name, link in links:
        msg += f"- {name}: {link}\n"
    await context.bot.send_message(chat_id=user_id, text=msg)


# =========================
# USER FLOW
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.effective_message:
        return

    conn: sqlite3.Connection = context.application.bot_data["db"]
    user = update.effective_user
    upsert_user(conn, user)
    await log_usuario(context, user, "START")

    full_name = nombre_completo(user.first_name, user.last_name)
    uname = f"@{user.username}" if user.username else "(sin username)"
    dt = ahora()

    texto = (
        f"Hola {full_name}\n"
        f"Tu ID es {user.id}\n"
        f"Tu User Name es {uname}\n"
        f"La fecha de hoy es {fecha_es(dt)}\n"
        f"Hora: {hora_es(dt)}"
    )

    bot_username = context.application.bot_data.get("bot_username", "TuBot")
    deep_link = f"https://t.me/{bot_username}?start=canales"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Adquirir Canales", callback_data="BUY")],
        [InlineKeyboardButton("Abrir chat privado", url=deep_link)],
    ])

    await update.effective_message.reply_text(texto, reply_markup=kb)

async def on_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return

    await query.answer()

    conn: sqlite3.Connection = context.application.bot_data["db"]
    user = query.from_user
    upsert_user(conn, user)
    await log_usuario(context, user, "BUY")

    try:
        await enviar_links_por_dm(context, user.id)
        if query.message:
            await query.message.reply_text("✅ Listo: te envié los enlaces por mensaje privado.")
    except Forbidden:
        bot_username = context.application.bot_data.get("bot_username", "TuBot")
        deep_link = f"https://t.me/{bot_username}?start=canales"
        if query.message:
            await query.message.reply_text(
                "Para enviarte los enlaces por privado primero debes abrir el bot en chat privado y presionar START.\n"
                f"Abre aquí: {deep_link}"
            )
    except Exception:
        if query.message:
            await query.message.reply_text(
                "No pude generar enlaces. Revisa que:\n"
                "- El bot es admin en todos esos canales/grupos\n"
                "- Tiene permiso de invitar y banear\n"
            )


# =========================
# ADMIN (SOLO CONTROL CHAT)
# =========================
async def admin_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_control_admin(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /ban <user_id>")
        return

    user_id = int(context.args[0])
    ok, fail = 0, 0
    for ch in CHANNELS:
        try:
            await context.bot.ban_chat_member(chat_id=ch["chat_id"], user_id=user_id)
            ok += 1
        except Exception:
            fail += 1
    await update.effective_message.reply_text(f"BAN listo. OK: {ok} | Fallos: {fail}")

async def admin_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_control_admin(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /unban <user_id>")
        return

    user_id = int(context.args[0])
    ok, fail = 0, 0
    for ch in CHANNELS:
        try:
            await context.bot.unban_chat_member(chat_id=ch["chat_id"], user_id=user_id)
            ok += 1
        except Exception:
            fail += 1
    await update.effective_message.reply_text(f"UNBAN listo. OK: {ok} | Fallos: {fail}")

async def admin_enlaces(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_control_admin(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /enlaces <user_id>")
        return

    user_id = int(context.args[0])
    try:
        await enviar_links_por_dm(context, user_id)
        await update.effective_message.reply_text("✅ Enlaces enviados (si el usuario tiene el bot abierto en privado).")
    except Forbidden:
        await update.effective_message.reply_text("No pude enviar DM: ese usuario no ha iniciado chat con el bot.")
    except Exception:
        await update.effective_message.reply_text("Error generando enlaces. Revisa permisos del bot en los canales.")

async def admin_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_control_admin(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /status <user_id>")
        return

    user_id = int(context.args[0])
    conn: sqlite3.Connection = context.application.bot_data["db"]
    row = conn.execute("""
        SELECT user_id, username, first_name, last_name, first_contact_at, last_contact_at
        FROM users WHERE user_id = ?
    """, (user_id,)).fetchone()

    if not row:
        await update.effective_message.reply_text("No tengo registrado ese usuario.")
        return

    uid, username, first_name, last_name, first_contact, last_contact = row
    full = nombre_completo(first_name, last_name)
    username = username or "(sin username)"

    await update.effective_message.reply_text(
        f"Usuario: {full}\n"
        f"ID: {uid}\n"
        f"Username: {username}\n"
        f"Primera vez: {first_contact}\n"
        f"Última vez: {last_contact}"
    )

async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat and update.effective_message:
        await update.effective_message.reply_text(f"chat_id = {update.effective_chat.id}")

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and update.effective_message:
        await update.effective_message.reply_text(f"Tu user_id = {update.effective_user.id}")


async def post_init(app: Application) -> None:
    me = await app.bot.get_me()
    app.bot_data["bot_username"] = me.username


def main() -> None:
    conn = init_db()
    application = Application.builder().token(TOKEN).post_init(post_init).build()
    application.bot_data["db"] = conn

    # User
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(on_buy, pattern="^BUY$"))

    # Tools
    application.add_handler(CommandHandler("chatid", chatid))
    application.add_handler(CommandHandler("id", myid))

    # Admin (solo en tu grupo CONTROL)
    application.add_handler(CommandHandler("ban", admin_ban))
    application.add_handler(CommandHandler("unban", admin_unban))
    application.add_handler(CommandHandler("enlaces", admin_enlaces))
    application.add_handler(CommandHandler("status", admin_status))

    application.run_polling()


if __name__ == "__main__":
    main()
