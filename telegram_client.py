import asyncio

from telethon import TelegramClient
from telethon.errors import FloodWaitError

import config


def build_client() -> TelegramClient:
    return TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)


async def safe_call(client, request, max_retries=5):
    """Ejecuta client(request) reintentando ante FloodWaitError."""
    for attempt in range(1, max_retries + 1):
        try:
            return await client(request)
        except FloodWaitError as e:
            wait_s = e.seconds + 1
            print(f"  [FloodWait] esperando {wait_s}s (intento {attempt}/{max_retries})")
            await asyncio.sleep(wait_s)
    raise RuntimeError(f"Excedido max_retries ({max_retries}) tras FloodWait repetido")


async def safe_run(coro_fn, *args, max_retries=5, **kwargs):
    """Ejecuta una corrutina arbitraria (no request crudo) reintentando ante FloodWaitError."""
    for attempt in range(1, max_retries + 1):
        try:
            return await coro_fn(*args, **kwargs)
        except FloodWaitError as e:
            wait_s = e.seconds + 1
            print(f"  [FloodWait] esperando {wait_s}s (intento {attempt}/{max_retries})")
            await asyncio.sleep(wait_s)
    raise RuntimeError(f"Excedido max_retries ({max_retries}) tras FloodWait repetido")
