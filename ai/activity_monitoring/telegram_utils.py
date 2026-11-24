"""Deprecated Telegram helpers.

The Expo app receives alerts from the backend. These compatibility functions
remain so older call sites do not fail while Telegram is no longer configured.
"""


def send_message(text):
    """Retained for compatibility; notifications are handled by the backend."""
    return None


def send_photo(path, caption=None):
    """Retained for compatibility; snapshots are served by the backend."""
    return None


def get_updates():
    """Telegram command polling has been removed."""
    return None
