import random
from dataclasses import dataclass

from telethon.tl.functions.channels import (
    CreateChannelRequest,
    ToggleForumRequest,
)
from telethon.tl.functions.messages import (
    CreateForumTopicRequest,
    GetForumTopicsRequest,
)
from telethon.tl.types import MessageActionTopicCreate

from telegram_client import safe_call


@dataclass
class Topic:
    id: int
    title: str
    icon_color: int | None
    icon_emoji_id: int | None


async def ensure_is_forum(client, entity) -> None:
    if not getattr(entity, "forum", False):
        raise RuntimeError(
            "El grupo origen no tiene el modo Temas (Forum) activado. "
            "Este script asume que el origen usa Topics nativos."
        )


async def get_source_topics(client, entity) -> list[Topic]:
    await ensure_is_forum(client, entity)

    topics: list[Topic] = []
    offset_date = None
    offset_id = 0
    offset_topic = 0

    while True:
        result = await safe_call(
            client,
            GetForumTopicsRequest(
                peer=entity,
                offset_date=offset_date,
                offset_id=offset_id,
                offset_topic=offset_topic,
                limit=100,
            ),
        )
        if not result.topics:
            break

        for t in result.topics:
            if not hasattr(t, "title"):
                # ForumTopicDeleted: topic borrado, no tiene contenido que respaldar
                continue
            topics.append(
                Topic(
                    id=t.id,
                    title=t.title,
                    icon_color=getattr(t, "icon_color", None),
                    icon_emoji_id=getattr(t, "icon_emoji_id", None),
                )
            )

        last = result.topics[-1]
        offset_topic = last.id
        offset_id = getattr(last, "top_message", last.id)
        offset_date = getattr(last, "date", None)

        if len(result.topics) < 100:
            break

    return topics


async def create_dest_channel(client, title: str):
    result = await safe_call(
        client,
        CreateChannelRequest(
            title=title, about=f"Backup de {title}", megagroup=True, forum=True
        ),
    )
    dest_channel = result.chats[0]
    if not getattr(dest_channel, "forum", False):
        await safe_call(
            client, ToggleForumRequest(channel=dest_channel, enabled=True, tabs=False)
        )
    return dest_channel


async def create_dest_topic(client, dest_entity, topic: Topic) -> int:
    try:
        updates = await safe_call(
            client,
            CreateForumTopicRequest(
                peer=dest_entity,
                title=topic.title,
                icon_color=topic.icon_color,
                icon_emoji_id=topic.icon_emoji_id,
                random_id=random.randint(0, 2**63 - 1),
            ),
        )
    except Exception as e:
        print(f"  [aviso] fallo creando topic con icono '{topic.title}' ({e}); reintentando sin icono")
        updates = await safe_call(
            client,
            CreateForumTopicRequest(
                peer=dest_entity,
                title=topic.title,
                random_id=random.randint(0, 2**63 - 1),
            ),
        )

    return _extract_topic_id(updates)


def _extract_topic_id(updates) -> int:
    for update in getattr(updates, "updates", []):
        message = getattr(update, "message", None)
        if message is None:
            continue
        action = getattr(message, "action", None)
        if isinstance(action, MessageActionTopicCreate):
            return message.id
    # fallback: primer mensaje de servicio en la respuesta
    for update in getattr(updates, "updates", []):
        message = getattr(update, "message", None)
        if message is not None:
            return message.id
    raise RuntimeError("No se pudo extraer el topic_id del resultado de CreateForumTopicRequest")
