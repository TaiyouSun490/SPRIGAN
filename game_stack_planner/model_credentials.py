"""FAFNIR-owned, per-catalog OAuth storage. Never reads another application's credentials."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import tempfile
from .model_sources import ModelError


class ProtectedOAuthStore:
    supported = os.name == "nt"

    def __init__(self, path):
        self.path = Path(path)

    @classmethod
    def for_catalog(cls, database):
        identity = hashlib.sha256(str(Path(database).resolve()).encode()).hexdigest()[:24]
        return cls(Path(os.getenv("LOCALAPPDATA", Path.home() / ".local/share")) / "FAFNIR/auth" / (identity + ".dpapi"))

    def _crypt(self, data, decrypt=False):
        if not self.supported:
            raise ModelError("Remembering sign-in currently requires Windows. Use session-only connection on this OS.")
        class Blob(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
        source = ctypes.create_string_buffer(data)
        incoming = Blob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
        outgoing = Blob()
        dll = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        function = dll.CryptUnprotectData if decrypt else dll.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        function.restype = wintypes.BOOL
        # User-scoped protection; UI_FORBIDDEN avoids hidden credential prompts.
        if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
            raise ModelError("Windows could not unlock/save FAFNIR sign-in. Clear the saved connection and reconnect.")
        try:
            return ctypes.string_at(outgoing.data, outgoing.size)
        finally:
            kernel.LocalFree(outgoing.data)

    def load(self):
        if not self.path.exists():
            return None
        try:
            if self.path.stat().st_size > 65536:
                raise ValueError()
            return json.loads(self._crypt(self.path.read_bytes(), decrypt=True))
        except (OSError, ValueError, UnicodeError):
            raise ModelError("Saved FAFNIR sign-in is unreadable. Clear it and reconnect.") from None

    def save(self, value):
        encrypted = self._crypt(json.dumps(value).encode())
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(encrypted)
            os.replace(temporary, self.path)
        except OSError:
            raise ModelError("Could not save FAFNIR sign-in. Use session-only connection or check storage access.") from None
        finally:
            if "temporary" in locals():
                temporary.unlink(missing_ok=True)

    def clear(self):
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            raise ModelError("Could not clear the saved FAFNIR connection.") from None
