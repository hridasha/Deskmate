"""Encryption for API keys stored on the server. Keys are encrypted at rest and never sent back to the browser."""
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

DATA_DIR = Path(os.getenv("CASTCALL_DATA_DIR", Path(__file__).parent / "data"))
KEY_FILE = DATA_DIR / ".secret_key"

_fernet: Fernet | None = None


def _get() -> Fernet:
    """Use SECRETS_KEY from the environment if set, otherwise a key generated once and kept next to the database."""
    global _fernet
    if _fernet is None:
        env = os.getenv("SECRETS_KEY", "").strip()
        if env:
            key = env.encode()
        else:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            if not KEY_FILE.exists():
                KEY_FILE.write_bytes(Fernet.generate_key())
            key = KEY_FILE.read_bytes()
        _fernet = Fernet(key)
    return _fernet


def encrypt(value: str) -> str:
    return _get().encrypt(value.encode()).decode()


def decrypt(token: str) -> str | None:
    try:
        return _get().decrypt(token.encode()).decode()
    except InvalidToken:
        return None  # wrong SECRETS_KEY or corrupted row


def mask(value: str) -> str:
    """Show only the last four characters, e.g. '••••••••AbCd'."""
    tail = value[-4:] if len(value) > 8 else ""
    return "•" * 8 + tail
