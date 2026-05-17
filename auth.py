from flask import flash, session


def current_user_id():
    return session.get("user_id")


def require_user():
    if not current_user_id():
        flash("Please sign in first.", "warning")
        return False
    return True
