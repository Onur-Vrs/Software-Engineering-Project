from datetime import datetime

from auth import current_user_id
from database import execute


def add_activity(message):
    execute(
        "INSERT INTO activities (user_id, message, created_at) VALUES (?, ?, ?)",
        (current_user_id(), message, datetime.now().strftime("%Y-%m-%d %H:%M")),
    )
