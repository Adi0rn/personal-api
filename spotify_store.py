"""Encrypt local Spotify tokens for the current Windows user using DPAPI."""

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile

TOKEN_FILE = Path(__file__).resolve().parent / "data" / "spotify_tokens.bin"


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise OSError("Spotify token storage currently requires Windows.")
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = library.CryptUnprotectData if decrypt else library.CryptProtectData
    operation.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)]
    operation.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = DataBlob()
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))


def load_tokens() -> dict | None:
    try:
        encrypted = TOKEN_FILE.read_bytes()
    except FileNotFoundError:
        return None
    tokens = json.loads(crypt(encrypted, decrypt=True))
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        raise ValueError("Invalid Spotify token data.")
    return tokens


def save_tokens(tokens: dict) -> None:
    encrypted = crypt(json.dumps(tokens).encode())
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".spotify-", suffix=".tmp", dir=TOKEN_FILE.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encrypted)
        temporary.replace(TOKEN_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def delete_tokens() -> None:
    TOKEN_FILE.unlink(missing_ok=True)
