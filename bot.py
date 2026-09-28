import asyncio
import glob
import json
import os
from datetime import datetime

from telethon import TelegramClient, events

import config
import state as state_mod
from backup import retry_skipped, run_backup
from telegram_client import build_client

LOG_PATH = os.path.join(config.LOG_DIR, "bot.log")

_user_client: TelegramClient | None = None
_busy_lock = asyncio.Lock()
_current_task: str | None = None


def _log(line: str) -> None:
    os.makedirs(config.LOG_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {line}\n")
    print(line)


def _require_owner(handler):
    async def wrapped(event):
        if config.BOT_OWNER_ID is not None and event.sender_id != config.BOT_OWNER_ID:
            _log(f"Comando rechazado de sender_id={event.sender_id}")
            return
        await handler(event)

    return wrapped


def _known_sources() -> list[str]:
    sources = []
    for path in glob.glob(os.path.join(config.STATE_DIR, "*.json")):
        with open(path, "r", encoding="utf-8") as f:
            st = json.load(f)
        sources.append(st.get("source_chat", os.path.basename(path)))
    return sources


async def _run_locked(coro_fn, task_name: str, event, *args, **kwargs):
    global _current_task
    if _busy_lock.locked():
        await event.respond(f"Ya hay una tarea en curso: {_current_task}. Espera a que termine.")
        return

    async with _busy_lock:
        _current_task = task_name
        await event.respond(f"Iniciando: {task_name}")
        _log(f"INICIO {task_name}")
        try:
            await coro_fn(*args, **kwargs)
            await event.respond(f"Completado: {task_name}")
            _log(f"OK {task_name}")
        except Exception as e:
            await event.respond(f"Error en {task_name}: {e}")
            _log(f"ERROR {task_name}: {e!r}")
        finally:
            _current_task = None


def register_handlers(bot: TelegramClient) -> None:
    @bot.on(events.NewMessage(pattern=r"/status(?:\s+(.+))?"))
    @_require_owner
    async def status_handler(event):
        arg = event.pattern_match.group(1)
        sources = [arg.strip()] if arg else _known_sources()
        if not sources:
            await event.respond("No hay estado guardado todavia.")
            return

        for source in sources:
            st = state_mod.load_state(source)
            lines = [f"Estado de '{source}':"]
            for topic_id, t in st.get("topics", {}).items():
                lines.append(f"  [{topic_id}] {t.get('origin_title')}: {t.get('status')}")
            skipped = st.get("skipped", [])
            lines.append(f"  saltados: {len(skipped)}")
            await event.respond("\n".join(lines))

    @bot.on(events.NewMessage(pattern=r"/run\s+(\S+)"))
    @_require_owner
    async def run_handler(event):
        source = event.pattern_match.group(1)
        await _run_locked(run_backup, f"run_backup({source})", event, _user_client, source=source)

    @bot.on(events.NewMessage(pattern=r"/retry\s+(\S+)"))
    @_require_owner
    async def retry_handler(event):
        source = event.pattern_match.group(1)
        await _run_locked(retry_skipped, f"retry_skipped({source})", event, _user_client, source=source)

    @bot.on(events.NewMessage(pattern=r"/logs(?:\s+(\d+))?"))
    @_require_owner
    async def logs_handler(event):
        n = int(event.pattern_match.group(1) or 30)
        if not os.path.exists(LOG_PATH):
            await event.respond("Todavia no hay logs.")
            return
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            tail = f.readlines()[-n:]
        text = "".join(tail) or "(log vacio)"
        if len(text) > 3500:
            text = text[-3500:]
        await event.respond(f"```\n{text}\n```")

    @bot.on(events.NewMessage(pattern="/help"))
    @_require_owner
    async def help_handler(event):
        await event.respond(
            "Comandos disponibles:\n"
            "/status [source] - estado de topics y saltados\n"
            "/run <source> - corre el backup completo\n"
            "/retry <source> - reintenta mensajes saltados\n"
            "/logs [n] - ultimas n lineas de log (default 30)"
        )


async def main() -> None:
    global _user_client

    if not config.BOT_TOKEN:
        raise RuntimeError("TG_BOT_TOKEN debe estar definido en .env")
    if config.BOT_OWNER_ID is None:
        raise RuntimeError("TG_OWNER_ID debe estar definido en .env para restringir el bot a tu cuenta")

    _user_client = build_client()
    bot = TelegramClient(config.BOT_SESSION_NAME, config.API_ID, config.API_HASH)

    async with _user_client, bot:
        await bot.start(bot_token=config.BOT_TOKEN)
        register_handlers(bot)
        _log("Bot iniciado")
        await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
