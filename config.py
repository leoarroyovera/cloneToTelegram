import os

from dotenv import load_dotenv

load_dotenv()

API_ID = os.environ.get("TG_API_ID")
API_HASH = os.environ.get("TG_API_HASH")

if not API_ID or not API_HASH:
    raise RuntimeError("TG_API_ID y TG_API_HASH deben estar definidos en .env")

API_ID = int(API_ID)

SESSION_NAME = "backup_session"
STATE_DIR = "state"
TMP_MEDIA_DIR = "tmp_media"
LOG_DIR = "logs"

BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
BOT_SESSION_NAME = "control_bot_session"

_owner_id = os.environ.get("TG_OWNER_ID")
BOT_OWNER_ID = int(_owner_id) if _owner_id else None
