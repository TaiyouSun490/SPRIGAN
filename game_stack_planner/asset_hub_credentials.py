"""Local UI credential entry, isolated from asset metadata, MCP and browser accounts."""
from __future__ import annotations
import os
from .asset_hub_sources import GENERATORS, bounded
from .model_credentials import ProtectedOAuthStore
from .model_sources import ModelError


class HubCredentials:
    def __init__(self, database):
        self.base = ProtectedOAuthStore.for_catalog(database).path
        self.session = {}
        self.disabled_environment = set()

    def store(self, provider):
        if not isinstance(provider, str) or provider not in GENERATORS or not GENERATORS[provider]["key_env"]:
            raise ModelError("This provider does not use a supported official API key.")
        return ProtectedOAuthStore(self.base.with_name(self.base.stem + "." + provider + ".dpapi"))

    def key(self, provider):
        if provider in self.session:
            return self.session[provider]
        saved = self.store(provider).load()
        if saved:
            return bounded(saved.get("key"), "saved provider credential", 16000, True)
        return "" if provider in self.disabled_environment else os.getenv(GENERATORS[provider]["key_env"], "")

    def setup(self, provider, key, remember=False):
        key = bounded(key, "API key", 16000, True)
        if any(c.isspace() for c in key) or type(remember) is not bool:
            raise ModelError("Invalid API key or remember setting.")
        store = self.store(provider)
        if remember:
            store.save({"key": key})
        else:
            store.clear()
        self.session[provider] = key
        return {"provider": provider, "configured": True, "verified": False, "remembered": remember}

    def disconnect(self, provider):
        self.store(provider).clear()
        self.session.pop(provider, None)
        self.disabled_environment.add(provider)
        return {"provider": provider, "configured": False, "verified": False}

    def status(self):
        rows = []
        for provider, config in GENERATORS.items():
            configured, error = False, ""
            if config["key_env"]:
                try:
                    configured = bool(self.key(provider))
                except ModelError:
                    error = "保存された接続設定を読み込めません。設定し直してください。"
            rows.append({"provider": provider, "configured": configured, "verified": False,
                         "remember_available": ProtectedOAuthStore.supported, "error": error,
                         "access": config["access"], "docs": config["docs"]})
        return rows
