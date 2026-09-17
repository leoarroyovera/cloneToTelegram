import argparse
import asyncio

from backup import run_backup
from telegram_client import build_client


def parse_args():
    parser = argparse.ArgumentParser(description="Respalda media y links de un grupo de Telegram con Topics")
    parser.add_argument("--source", required=True, help="username o link t.me/... del grupo origen")
    parser.add_argument(
        "--dest-title",
        help="titulo del supergrupo destino a crear (default: '<source>Respaldo')",
    )
    parser.add_argument("--dest-chat-id", type=int, default=None, help="id de un chat destino ya existente")
    parser.add_argument("--only-topic", type=int, default=None, help="limitar a un topic origen especifico")
    parser.add_argument("--limit-per-topic", type=int, default=None, help="limitar cantidad de mensajes por topic")
    parser.add_argument("--dry-run", action="store_true", help="solo listar topics, no escribir nada")
    return parser.parse_args()


async def main():
    args = parse_args()

    client = build_client()
    async with client:
        me = await client.get_me()
        print(f"Sesion iniciada como: {me.first_name} (@{me.username})")

        await run_backup(
            client,
            source=args.source,
            dest_title=args.dest_title,
            dest_chat_id=args.dest_chat_id,
            only_topic=args.only_topic,
            limit_per_topic=args.limit_per_topic,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    asyncio.run(main())
