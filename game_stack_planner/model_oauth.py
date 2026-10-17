"""Official Sketchfab OAuth with optional user-scoped Windows persistence."""
from __future__ import annotations

import os
import math
import secrets
import threading
import time
from urllib.parse import urlencode, urlsplit

from .model_sources import ModelError, ModelHTTP


class SketchfabOAuth:
    def __init__(self, http=None, store=None):
        self.http = http or ModelHTTP()
        self.lock = threading.RLock()
        self.pending = {}
        self.return_paths = {}
        self.access_token = ""
        self.refresh_token = ""
        self.expires_at = 0.0
        self.store = store
        self.local_config = {}
        self.remember = False
        self.storage_error = ""
        self.environment_disabled = False
        if store:
            try:
                saved = store.load()
                if saved:
                    cfg = saved.get("config", {})
                    self.validate_config(cfg)
                    for key in ("access_token", "refresh_token"):
                        if not isinstance(saved.get(key), str) or len(saved[key]) > 16000:
                            raise ModelError("Invalid saved sign-in. Clear the connection and reconnect.")
                    expiry = saved.get("expires_at")
                    if type(expiry) not in (int, float) or not math.isfinite(expiry):
                        raise ModelError("Invalid saved sign-in. Clear the connection and reconnect.")
                    self.local_config = cfg
                    self.environment_disabled = True
                    self.access_token = saved["access_token"]
                    self.refresh_token = saved["refresh_token"]
                    self.expires_at = time.monotonic() + max(0, min(expiry-time.time(), 366*86400))
                    self.remember = True
            except (ModelError, AttributeError):
                self.storage_error = "Saved sign-in could not be restored. Clear the connection and set it up again."
        self._store_stamp = self._saved_stamp()

    def _saved_stamp(self):
        try:
            stat = self.store.path.stat()
            return (stat.st_mtime_ns, stat.st_size)
        except (AttributeError, OSError):
            return None

    def reload_saved_connection(self):
        """Reload only FAFNIR's own supported store after another FAFNIR process signs in/out."""
        with self.lock:
            stamp = self._saved_stamp()
            if self.pending or stamp == self._store_stamp:
                return
            self._store_stamp = stamp
            restored = SketchfabOAuth(self.http, self.store)
            if restored.storage_error:
                self.storage_error = restored.storage_error
                return
            if restored.local_config or self.remember:
                for field in ("access_token", "refresh_token", "expires_at", "local_config", "remember", "storage_error"):
                    setattr(self, field, getattr(restored, field))
                self.environment_disabled = True

    def storage_status(self):
        return {"remember_available": bool(self.store and self.store.supported),
                "remembered": self.remember, "error": self.storage_error,
                "source": "local_setup" if self.local_config else "environment" if self.configured() else "none"}

    def configured(self):
        return bool(self.local_config) or (not self.environment_disabled and all(os.getenv(k) for k in ["FAFNIR_SKETCHFAB_CLIENT_ID", "FAFNIR_SKETCHFAB_CLIENT_SECRET", "FAFNIR_SKETCHFAB_REDIRECT_URI"]))

    def authenticated(self):
        with self.lock:
            return bool((not self.environment_disabled and os.getenv("FAFNIR_SKETCHFAB_ACCESS_TOKEN")) or
                        (self.access_token and (self.expires_at > time.monotonic() or self.refresh_token)))

    def config(self):
        if not self.configured():
            raise ModelError("Register a Sketchfab OAuth app, then configure FAFNIR_SKETCHFAB_CLIENT_ID, CLIENT_SECRET and REDIRECT_URI (each with the FAFNIR_SKETCHFAB_ prefix).")
        config = self.local_config or {"client_id": os.environ["FAFNIR_SKETCHFAB_CLIENT_ID"],
                "client_secret": os.environ["FAFNIR_SKETCHFAB_CLIENT_SECRET"], "redirect_uri": os.environ["FAFNIR_SKETCHFAB_REDIRECT_URI"]}
        self.validate_config(config)
        return dict(config)

    @staticmethod
    def validate_config(config):
        if not isinstance(config, dict) or set(config) != {"client_id", "client_secret", "redirect_uri"}:
            raise ModelError("OAuth setup requires client ID, client secret and callback URL.")
        if any(not isinstance(v, str) or not v.strip() or len(v) > 2000 or any(ord(c)<32 for c in v) for v in config.values()):
            raise ModelError("Invalid OAuth setup fields.")
        redirect = config["redirect_uri"]
        parsed = urlsplit(redirect)
        try:
            port = parsed.port
        except ValueError:
            port = None
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path != "/api/models/oauth_callback"
                or not port or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ModelError("OAuth redirect must be http://127.0.0.1:<FAFNIR-port>/api/models/oauth_callback.")

    def setup(self, config, remember=False):
        self.validate_config(config)
        if type(remember) is not bool:
            raise ModelError("Remember must be boolean.")
        if remember and not (self.store and self.store.supported):
            raise ModelError("Secure persistence unavailable; use session-only connection.")
        with self.lock:
            saved = {"config": config, "access_token": "", "refresh_token": "", "expires_at": 0}
            if remember:
                self.store.save(saved)
            elif self.store:
                self.store.clear()
            self.local_config = dict(config)
            self.remember = remember
            self.environment_disabled = True
            self.storage_error = ""
            self.pending.clear()
            self.access_token = self.refresh_token = ""
            self.expires_at = 0
        return {"configured": True, "remembered": remember}

    def disconnect(self):
        with self.lock:
            if self.store:
                self.store.clear()
            self.local_config.clear()
            self.pending.clear()
            self.access_token = self.refresh_token = ""
            self.expires_at = 0
            self.remember = False
            self.environment_disabled = True
            self.storage_error = ""
        return {"authenticated": False, "notice": "Local sign-in cleared. Environment configuration, if present, is used again after server restart. Remote grants can be revoked on Sketchfab."}

    def start(self, return_to="/models.html"):
        if return_to not in {"/models.html", "/hub.html"}:
            raise ModelError("Unsupported sign-in return page.")
        state = secrets.token_urlsafe(32)
        with self.lock:
            config = self.config()
            self.pending = {k: v for k, v in self.pending.items() if v > time.monotonic()}
            if len(self.pending) >= 10:
                raise ModelError("Too many pending sign-ins. Wait for them to expire.")
            self.pending[state] = time.monotonic() + 600
            self.return_paths = {k: v for k, v in self.return_paths.items() if k in self.pending}
            self.return_paths[state] = return_to
        return {"authorize_url": "https://sketchfab.com/oauth2/authorize/?" + urlencode({
            "response_type": "code", "client_id": config["client_id"], "redirect_uri": config["redirect_uri"], "state": state})}

    def _exchange(self, form):
        value = self.http.json("https://sketchfab.com/oauth2/token/", {"sketchfab.com"}, form={**self.config(), **form})
        token = value.get("access_token")
        refresh = value.get("refresh_token", self.refresh_token)
        lifetime = value.get("expires_in", 3600)
        if (not isinstance(token, str) or not token or not isinstance(refresh, str)
                or type(lifetime) not in (int, float) or not 0 < lifetime <= 366 * 86400):
            raise ModelError("Sketchfab returned an invalid OAuth response.")
        self.access_token = token
        self.refresh_token = refresh
        self.expires_at = time.monotonic() + lifetime
        if self.remember:
            try:
                self.store.save({"config": self.config(), "access_token": token,
                    "refresh_token": refresh, "expires_at": time.time() + lifetime})
                self.storage_error = ""
            except ModelError:
                self.storage_error = "Connected for this session, but sign-in could not be saved. Reconnect after restart."

    def finish(self, code, state):
        if not code or not state:
            raise ModelError("Sketchfab sign-in was cancelled or incomplete.")
        with self.lock:
            expiry = self.pending.pop(state, 0)
            self.return_paths.pop(state, None)
            if expiry <= time.monotonic():
                raise ModelError("Sign-in request expired or does not match. Start sign-in again.")
            self._exchange({"grant_type": "authorization_code", "code": code})
        return {"authenticated": True}

    def token(self):
        self.reload_saved_connection()
        with self.lock:
            if not self.access_token:
                return "" if self.environment_disabled else os.getenv("FAFNIR_SKETCHFAB_ACCESS_TOKEN", "")
            if self.expires_at < time.monotonic() + 30:
                if not self.refresh_token:
                    self.access_token = ""
                    raise ModelError("Sketchfab sign-in expired. Sign in again.")
                self._exchange({"grant_type": "refresh_token", "refresh_token": self.refresh_token})
            return self.access_token
