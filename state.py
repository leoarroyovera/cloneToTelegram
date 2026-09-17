import json
import os
import re

import config


def state_path(source_name: str) -> str:
    os.makedirs(config.STATE_DIR, exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", source_name)
    return os.path.join(config.STATE_DIR, f"{safe_name}.json")


def load_state(source_name: str) -> dict:
    path = state_path(source_name)
    if not os.path.exists(path):
        return {
            "source_chat": source_name,
            "dest_chat_id": None,
            "topics": {},
            "skipped": [],
        }
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(source_name: str, state: dict) -> None:
    path = state_path(source_name)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def get_topic_state(state: dict, origin_topic_id: int) -> dict:
    key = str(origin_topic_id)
    if key not in state["topics"]:
        state["topics"][key] = {
            "origin_title": None,
            "dest_topic_id": None,
            "last_processed_msg_id": 0,
            "status": "pending",
        }
    return state["topics"][key]


def record_skipped(state: dict, msg_id: int, topic_id: int, reason: str, **extra) -> None:
    entry = {"msg_id": msg_id, "topic_id": topic_id, "reason": reason}
    entry.update(extra)
    state["skipped"].append(entry)
