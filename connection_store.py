"""Per-connection secrets encrypted for the current Windows user."""
import json
import os
from pathlib import Path
import tempfile
from fastapi import HTTPException
from spotify_store import crypt

DIRECTORY = Path(__file__).resolve().parent / "data" / "connections"
KINDS = {"google_config", "google_tokens", "github_tokens"}


def path(kind):
    if kind not in KINDS:
        raise ValueError("Unknown credential store.")
    return DIRECTORY / (kind + ".bin")


def load(kind):
    try:
        file = path(kind)
        if not file.exists():
            return None
        data = json.loads(crypt(file.read_bytes(), decrypt=True))
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (OSError, ValueError):
        raise HTTPException(500, "Cannot read this connection's saved credentials. Reconfigure it on the setup page.") from None


def save(kind, data):
    temporary = None
    try:
        encrypted = crypt(json.dumps(data).encode())
        DIRECTORY.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".credential-", dir=DIRECTORY)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encrypted)
        temporary.replace(path(kind))
    except OSError:
        raise HTTPException(500, "Cannot save connection credentials on this computer.") from None
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def delete(kind):
    path(kind).unlink(missing_ok=True)
