import asyncio
import glob
import json
import os
import sys
from datetime import datetime

from telethon import Button, TelegramClient, events
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import BotCommand, BotCommandScopeDefault

import config
import state as state_mod
from backup import retry_skipped, run_backup
from telegram_client import build_client

LOG_PATH = os.path.join(config.LOG_DIR, "bot.log")
BACKUP_LOG_PATH = os.path.join(config.LOG_DIR, "backup.log")

_user_client: TelegramClient | None = None
_busy_lock = asyncio.Lock()
_current_task: str | None = None
_current_job: asyncio.Task | None = None

# Comandos que aparecen en el menu "/" del cliente de Telegram.
BOT_COMMANDS = [
    ("menu", "menu con botones"),
    ("status", "estado de topics y saltados"),
    ("run", "corre el backup de <source>"),
    ("update", "respalda solo lo nuevo de <source>"),
    ("retry", "reintenta saltados de <source>"),
    ("cancel", "cancela la tarea en curso"),
    ("logs", "ultimas n lineas del log del bot"),
    ("backuplog", "ultimas n lineas del log de backup"),
    ("help", "lista de comandos"),
]


def _log(line: str) -> None:
    os.makedirs(config.LOG_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {line}\n")
    print(line)


class _TeeToFile:
    """Duplica cada linea escrita a stdout tambien hacia un archivo,
    para poder exponer el output de run_backup/retry_skipped (que usan
    print()) via el comando /backuplog."""

    def __init__(self, path: str, original):
        self._path = path
        self._original = original

    def write(self, data: str) -> int:
        self._original.write(data)
        if data.strip():
            os.makedirs(config.LOG_DIR, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(f"[{stamp}] {data}\n" if not data.endswith("\n") else f"[{stamp}] {data}")
        return len(data)

    def flush(self) -> None:
        self._original.flush()


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
    return sorted(sources)


async def _run_locked(coro_fn, task_name: str, event, *args, **kwargs):
    global _current_task, _current_job
    if _busy_lock.locked():
        await event.respond(f"Ya hay una tarea en curso: {_current_task}. Espera a que termine o usa /cancel.")
        return

    async with _busy_lock:
        _current_task = task_name
        await event.respond(f"Iniciando: {task_name}")
        _log(f"INICIO {task_name}")
        original_stdout = sys.stdout
        sys.stdout = _TeeToFile(BACKUP_LOG_PATH, original_stdout)
        # Se corre como Task aparte para que /cancel pueda cancelarla sin
        # cancelar el handler que la lanzo.
        _current_job = asyncio.create_task(coro_fn(*args, **kwargs))
        try:
            await _current_job
            await event.respond(f"Completado: {task_name}")
            _log(f"OK {task_name}")
        except asyncio.CancelledError:
            # El checkpoint se guarda tras cada mensaje, asi que un /run
            # posterior retoma desde el ultimo mensaje completado.
            await event.respond(f"Cancelado: {task_name}")
            _log(f"CANCELADO {task_name}")
        except Exception as e:
            await event.respond(f"Error en {task_name}: {e}")
            _log(f"ERROR {task_name}: {e!r}")
        finally:
            sys.stdout = original_stdout
            _current_task = None
            _current_job = None


async def _cancel_current(event) -> None:
    if _current_job is None or _current_job.done():
        await event.respond("No hay ninguna tarea en curso.")
        return
    _log(f"CANCEL solicitado para {_current_task}")
    await event.respond(f"Cancelando: {_current_task}...")
    _current_job.cancel()


async def _respond_status(event, sources: list[str]) -> None:
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


def _main_menu() -> list:
    return [
        [Button.inline("Status", b"status"), Button.inline("Cancelar run", b"cancel")],
        [Button.inline("Run", b"pick:run"), Button.inline("Update", b"pick:update")],
        [Button.inline("Retry", b"pick:retry")],
        [Button.inline("Logs", b"logs"), Button.inline("Backup log", b"backuplog")],
    ]


async def _start_action(action: str, source: str, event) -> None:
    if action == "run":
        await _run_locked(run_backup, f"run_backup({source})", event, _user_client, source=source)
    elif action == "update":
        await _run_locked(
            run_backup, f"update({source})", event, _user_client, source=source, update=True
        )
    else:
        await _run_locked(retry_skipped, f"retry_skipped({source})", event, _user_client, source=source)


def _menu_text() -> str:
    current = f"Tarea en curso: {_current_task}" if _current_task else "Sin tareas en curso."
    return f"{current}\n\nElige una opcion (para una fuente nueva usa /run <source>):"


def _sources_menu(action: str) -> list:
    # callback_data tiene limite de 64 bytes, asi que se usa el indice en
    # la lista ordenada de fuentes en vez del nombre.
    rows = [
        [Button.inline(source, f"{action}:{i}".encode())]
        for i, source in enumerate(_known_sources())
    ]
    rows.append([Button.inline("<< Volver", b"menu")])
    return rows


async def _respond_tail(event, path: str, n: int) -> None:
    if not os.path.exists(path):
        await event.respond("Todavia no hay logs.")
        return
    with open(path, "r", encoding="utf-8") as f:
        tail = f.readlines()[-n:]
    text = "".join(tail) or "(log vacio)"
    if len(text) > 3500:
        text = text[-3500:]
    await event.respond(f"```\n{text}\n```")


def register_handlers(bot: TelegramClient) -> None:
    @bot.on(events.NewMessage(pattern=r"/status(?:\s+(.+))?"))
    @_require_owner
    async def status_handler(event):
        arg = event.pattern_match.group(1)
        sources = [arg.strip()] if arg else _known_sources()
        await _respond_status(event, sources)

    @bot.on(events.NewMessage(pattern=r"/(run|update|retry)\s+(\S+)"))
    @_require_owner
    async def action_handler(event):
        action, source = event.pattern_match.group(1), event.pattern_match.group(2)
        await _start_action(action, source, event)

    @bot.on(events.NewMessage(pattern=r"/(run|update|retry)$"))
    @_require_owner
    async def pick_source_handler(event):
        # /run, /update o /retry sin argumento (p.ej. elegido desde el menu "/"):
        # se ofrecen las fuentes con checkpoint como botones.
        action = event.pattern_match.group(1)
        if not _known_sources():
            await event.respond(f"No hay fuentes guardadas. Usa /{action} <source>.")
            return
        await event.respond(f"Elige la fuente para {action}:", buttons=_sources_menu(action))

    @bot.on(events.NewMessage(pattern=r"/logs(?:\s+(\d+))?"))
    @_require_owner
    async def logs_handler(event):
        n = int(event.pattern_match.group(1) or 30)
        await _respond_tail(event, LOG_PATH, n)

    @bot.on(events.NewMessage(pattern=r"/backuplog(?:\s+(\d+))?"))
    @_require_owner
    async def backuplog_handler(event):
        n = int(event.pattern_match.group(1) or 30)
        await _respond_tail(event, BACKUP_LOG_PATH, n)

    @bot.on(events.NewMessage(pattern=r"/cancel$"))
    @_require_owner
    async def cancel_handler(event):
        await _cancel_current(event)

    @bot.on(events.NewMessage(pattern=r"/(?:menu|start)$"))
    @_require_owner
    async def menu_handler(event):
        await event.respond(_menu_text(), buttons=_main_menu())

    @bot.on(events.CallbackQuery())
    @_require_owner
    async def callback_handler(event):
        data = event.data.decode()
        await event.answer()

        if data == "menu":
            await event.edit(_menu_text(), buttons=_main_menu())
        elif data == "status":
            await _respond_status(event, _known_sources())
        elif data == "cancel":
            await _cancel_current(event)
        elif data == "logs":
            await _respond_tail(event, LOG_PATH, 30)
        elif data == "backuplog":
            await _respond_tail(event, BACKUP_LOG_PATH, 30)
        elif data.startswith("pick:"):
            action = data.split(":", 1)[1]
            if not _known_sources():
                await event.respond("No hay fuentes guardadas. Usa /run <source>.")
                return
            await event.edit(f"Elige la fuente para {action}:", buttons=_sources_menu(action))
        elif data.startswith(("run:", "update:", "retry:")):
            action, idx = data.split(":", 1)
            sources = _known_sources()
            if not idx.isdigit() or int(idx) >= len(sources):
                await event.respond("Fuente no encontrada, vuelve a abrir /menu.")
                return
            await event.edit(_menu_text(), buttons=_main_menu())
            await _start_action(action, sources[int(idx)], event)

    @bot.on(events.NewMessage(pattern="/help"))
    @_require_owner
    async def help_handler(event):
        await event.respond(
            "Comandos disponibles:\n"
            "/menu - menu con botones\n"
            "/status [source] - estado de topics y saltados\n"
            "/run <source> - corre el backup completo\n"
            "/update <source> - respalda solo lo nuevo, incluso en topics ya completados\n"
            "/retry <source> - reintenta mensajes saltados\n"
            "/cancel - cancela la tarea en curso (el proximo /run retoma desde el checkpoint)\n"
            "/logs [n] - ultimas n lineas de log del bot (default 30)\n"
            "/backuplog [n] - ultimas n lineas del log de backup (default 30)"
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
        await bot(
            SetBotCommandsRequest(
                scope=BotCommandScopeDefault(),
                lang_code="",
                commands=[BotCommand(command=c, description=d) for c, d in BOT_COMMANDS],
            )
        )
        _log("Bot iniciado")
        await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
