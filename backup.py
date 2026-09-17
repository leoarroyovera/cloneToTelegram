import os
import random
import re
from pathlib import Path

from telethon import errors
from telethon.tl.types import DocumentAttributeFilename

import config
import state as state_mod
from classify import extract_links, is_relevant_media
from fast_upload import upload_file_fast
from progress import Progress
from telegram_client import safe_run
from topics import create_dest_channel, create_dest_topic, get_source_topics

MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2GB (limite cuenta no-premium)


def _default_dest_title(source: str) -> str:
    username = re.sub(r"^https?://t\.me/", "", source).strip("/")
    return f"{username}Respaldo"


async def _resolve_dest_title(client, base_title: str) -> str:
    """Si ya existe un dialogo con ese titulo exacto, agrega un sufijo
    aleatorio de 3 digitos para evitar el choque."""
    async for d in client.iter_dialogs():
        if d.name == base_title:
            suffix = random.randint(100, 999)
            return f"{base_title}{suffix}"
    return base_title


async def run_backup(
    client,
    source: str,
    dest_title: str | None = None,
    dest_chat_id: int | None = None,
    only_topic: int | None = None,
    limit_per_topic: int | None = None,
    dry_run: bool = False,
) -> None:
    source_entity = await client.get_entity(source)
    st = state_mod.load_state(source)

    print(f"Descubriendo topics de '{source}'...")
    source_topics = await get_source_topics(client, source_entity)
    if only_topic is not None:
        source_topics = [t for t in source_topics if t.id == only_topic]
    print(f"  {len(source_topics)} topic(s) encontrados")

    if dry_run:
        for t in source_topics:
            print(f"  - [{t.id}] {t.title}")
        return

    if dest_chat_id:
        st["dest_chat_id"] = dest_chat_id
        dest_entity = await client.get_entity(dest_chat_id)
    elif st.get("dest_chat_id"):
        dest_entity = await client.get_entity(st["dest_chat_id"])
    else:
        base_title = dest_title or _default_dest_title(source)
        final_title = await _resolve_dest_title(client, base_title)
        if final_title != base_title:
            print(f"  '{base_title}' ya existe, se usa '{final_title}'")
        print(f"Creando supergrupo destino '{final_title}'...")
        dest_entity = await create_dest_channel(client, final_title)
        st["dest_chat_id"] = dest_entity.id
        state_mod.save_state(source, st)

    os.makedirs(config.TMP_MEDIA_DIR, exist_ok=True)

    for topic in source_topics:
        topic_state = state_mod.get_topic_state(st, topic.id)
        topic_state["origin_title"] = topic.title

        if topic_state["dest_topic_id"] is None:
            print(f"Creando topic destino para '{topic.title}'...")
            dest_topic_id = await create_dest_topic(client, dest_entity, topic)
            topic_state["dest_topic_id"] = dest_topic_id
            state_mod.save_state(source, st)

        if topic_state["status"] == "done":
            print(f"Topic '{topic.title}' ya completado, se salta")
            continue

        await _process_topic(
            client, source_entity, dest_entity, topic, topic_state, st, source, limit_per_topic
        )


async def _process_topic(
    client, source_entity, dest_entity, topic, topic_state, st, source_name, limit_per_topic
):
    dest_topic_id = topic_state["dest_topic_id"]
    min_id = topic_state["last_processed_msg_id"]
    processed = 0

    print(f"Procesando topic '{topic.title}' (desde msg_id > {min_id})...")

    async for msg in client.iter_messages(
        source_entity, reply_to=topic.id, reverse=True, min_id=min_id
    ):
        if limit_per_topic is not None and processed >= limit_per_topic:
            break

        await _process_message(client, dest_entity, dest_topic_id, msg, st, topic.id)

        topic_state["last_processed_msg_id"] = msg.id
        topic_state["status"] = "in_progress"
        state_mod.save_state(source_name, st)
        processed += 1

    if limit_per_topic is None or processed < limit_per_topic:
        topic_state["status"] = "done"
        state_mod.save_state(source_name, st)

    print(f"  {processed} mensaje(s) procesados en '{topic.title}'")


async def _process_message(client, dest_entity, dest_topic_id, msg, st, origin_topic_id):
    if is_relevant_media(msg):
        await _backup_media(client, dest_entity, dest_topic_id, msg, st, origin_topic_id)

    links = extract_links(msg)
    if links:
        text = "\n".join(links)
        await safe_run(
            client.send_message, dest_entity, text, reply_to=dest_topic_id
        )


async def _backup_media(client, dest_entity, dest_topic_id, msg, st, origin_topic_id):
    file_size = getattr(msg.file, "size", 0) if msg.file else 0
    original_name = getattr(msg.file, "name", None)
    file_name = original_name or f"msg_{msg.id}"
    size_mb = file_size / (1024 * 1024) if file_size else 0

    if file_size and file_size > MAX_UPLOAD_BYTES:
        print(f"  [salto] msg {msg.id} excede el limite de subida ({file_size} bytes)")
        state_mod.record_skipped(st, msg.id, origin_topic_id, "too_large", size_bytes=file_size)
        return

    print(f"  msg {msg.id}: {file_name} ({size_mb:.1f} MB)")

    # Descargar directo al nombre real (no msg_id): upload_file_fast usa
    # path.name como nombre del archivo, y ese es el que ve el usuario.
    tmp_path = os.path.join(config.TMP_MEDIA_DIR, f"{msg.id}_{file_name}")
    try:
        print(f"    descargando...")
        dl_progress = Progress("descarga", file_size)
        downloaded = await safe_run(
            client.download_media,
            msg,
            file=tmp_path,
            progress_callback=dl_progress.update,
        )
        dl_progress.finish()
        if not downloaded:
            state_mod.record_skipped(st, msg.id, origin_topic_id, "download_failed")
            return

        print(f"    subiendo (paralelo)...")
        up_progress = Progress("subida", file_size)
        handle = await safe_run(
            upload_file_fast,
            client,
            Path(downloaded),
            progress_callback=up_progress.update,
        )
        up_progress.finish()
        send_kwargs = dict(
            caption=msg.message or "",
            reply_to=dest_topic_id,
            file_size=file_size,
            # Las fotos que Telegram rechaza como PhotoSaveFileInvalidError
            # (p.ej. >10MB, formato/metadata no soportados) se envian igual
            # como documento, sin perder el archivo.
            force_document=bool(msg.photo),
        )
        if original_name:
            send_kwargs["attributes"] = [DocumentAttributeFilename(original_name)]
        try:
            await safe_run(client.send_file, dest_entity, handle, **send_kwargs)
        except errors.PhotoSaveFileInvalidError:
            print("    foto rechazada por Telegram, reintentando como documento...")
            send_kwargs["force_document"] = True
            await safe_run(client.send_file, dest_entity, handle, **send_kwargs)
        print(f"    listo")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        else:
            for name in os.listdir(config.TMP_MEDIA_DIR):
                if name.startswith(f"{msg.id}_"):
                    os.remove(os.path.join(config.TMP_MEDIA_DIR, name))
