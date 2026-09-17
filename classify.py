import re

from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeSticker,
    MessageEntityTextUrl,
    MessageEntityUrl,
)

URL_REGEX = re.compile(r"https?://\S+")


def is_relevant_media(msg) -> bool:
    """Fotos, videos, documentos y audio. Excluye stickers y notas de voz."""
    if not msg.media:
        return False

    if msg.photo:
        return True

    if msg.video:
        return True

    if msg.document:
        attrs = msg.document.attributes
        for attr in attrs:
            if isinstance(attr, DocumentAttributeSticker):
                return False
            if isinstance(attr, DocumentAttributeAudio) and attr.voice:
                return False
        return True

    return False


def extract_links(msg) -> list[str]:
    text = msg.message or ""
    links: list[str] = []

    entities = msg.entities or []
    for entity in entities:
        if isinstance(entity, MessageEntityTextUrl):
            links.append(entity.url)
        elif isinstance(entity, MessageEntityUrl):
            start = entity.offset
            end = entity.offset + entity.length
            links.append(text[start:end])

    for match in URL_REGEX.findall(text):
        if match not in links:
            links.append(match)

    return links
