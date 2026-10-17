"""Asset Server compatibility and purchase-source metadata; no account-cookie adapters."""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from urllib.parse import urlencode, urlsplit, urlunsplit

from .model_sources import ModelError, ModelHTTP

KINDS = {"model", "material", "texture", "hdri", "animation", "world", "audio", "sfx", "music", "voice", "pack", "script", "sprite", "ui", "font", "other"}

# Purchase access is deliberately stated separately from public catalogue access.
STORES = {
    "asset_store": ("Unity Asset Store", "https://assetstore.unity.com", "editor_bridge", ("assetstore.unity.com", "marketplace.unity.com")),
    "booth": ("BOOTH", "https://booth.pm", "owned_import", ("booth.pm",)),
    "fab": ("Fab / Unreal Engine Marketplace", "https://www.fab.com", "launcher_and_owned_import", ("fab.com", "unrealengine.com")),
    "humble": ("Humble Bundle", "https://www.humblebundle.com", "owned_import", ("humblebundle.com",)),
    "gumroad": ("Gumroad", "https://gumroad.com", "owned_import", ("gumroad.com",)),
    "itchio": ("itch.io", "https://itch.io", "owned_import", ("itch.io",)),
    "artstation": ("ArtStation Marketplace", "https://www.artstation.com/marketplace", "owned_import", ("artstation.com",)),
    "cgtrader": ("CGTrader", "https://www.cgtrader.com", "owned_import", ("cgtrader.com",)),
    "turbosquid": ("TurboSquid", "https://www.turbosquid.com", "owned_import", ("turbosquid.com",)),
    "superhive": ("Superhive / Blender Market", "https://superhivemarket.com", "owned_import", ("superhivemarket.com", "blendermarket.com")),
    "blenderkit": ("BlenderKit", "https://www.blenderkit.com", "owned_import", ("blenderkit.com", "blendkit.com")),
    "fanatical": ("Fanatical", "https://www.fanatical.com", "owned_import", ("fanatical.com",)),
    "gamedevmarket": ("GameDev Market", "https://www.gamedevmarket.net", "owned_import", ("gamedevmarket.net",)),
    "sonniss": ("Sonniss", "https://sonniss.com", "owned_import", ("sonniss.com",)),
    "asoundeffect": ("A Sound Effect", "https://www.asoundeffect.com", "owned_import", ("asoundeffect.com",)),
    "audiojungle": ("AudioJungle / Envato", "https://audiojungle.net", "owned_import", ("audiojungle.net", "elements.envato.com")),
    "pond5": ("Pond5", "https://www.pond5.com", "owned_import", ("pond5.com",)),
    "sketchfab": ("Sketchfab", "https://sketchfab.com", "oauth_and_owned_import", ("sketchfab.com",)),
}

GENERATORS = {
    "tripo": {"name": "Tripo", "kinds": ["model"], "access": "official_api", "key_env": "TRIPO_API_KEY", "docs": "https://platform.tripo3d.ai/docs/quick-start", "url": "https://www.tripo3d.ai"},
    "elevenlabs": {"name": "ElevenLabs", "kinds": ["sfx", "music", "voice"], "access": "official_api", "key_env": "ELEVENLABS_API_KEY", "docs": "https://elevenlabs.io/docs/api-reference", "url": "https://elevenlabs.io"},
    "marble": {"name": "World Labs Marble", "kinds": ["world"], "access": "official_api", "key_env": "WORLDLABS_API_KEY", "docs": "https://docs.worldlabs.ai/api", "url": "https://marble.worldlabs.ai"},
    "suno": {"name": "Suno", "kinds": ["music"], "access": "generated_file_import", "key_env": None, "docs": "https://help.suno.com/en/articles/2746945", "url": "https://suno.com", "note": "公式公開APIの仕様は未確認です。自分で書き出した音源を登録し、作成時の契約プラン・利用条件を残します。"},
}


def bounded(value, name, maximum=2000, required=False):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ModelError(f"Invalid {name}.")
    if required and not value.strip():
        raise ModelError(f"{name} is required.")
    return value.strip()


def public_url(value, *, resolve=False):
    """Validate untrusted download URLs, including redirects, without forwarding credentials."""
    bounded(value, "URL", 8000, True)
    try:
        u = urlsplit(value)
        if u.scheme != "https" or not u.hostname or u.username or u.password or u.port not in (None, 443) or u.fragment:
            raise ValueError()
        host = u.hostname.rstrip(".")
        if host.casefold() == "localhost" or host.casefold().endswith((".localhost", ".local")):
            raise ValueError()
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip and not ip.is_global:
            raise ValueError()
        if resolve:
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(v[4][0]).is_global for v in addresses):
                raise ValueError()
    except (ValueError, OSError):
        raise ModelError("A public HTTPS URL is required.") from None
    return value


def canonical_url(value):
    public_url(value)
    u = urlsplit(value)
    return urlunsplit(("https", u.hostname.lower(), u.path.rstrip("/") or "/", "", ""))


def store_url(source, value):
    if source not in STORES and source not in GENERATORS:
        raise ModelError("Unsupported purchase or generation source.")
    url = canonical_url(value)
    host = urlsplit(url).hostname
    domains = STORES[source][3] if source in STORES else ({"tripo": "tripo3d.ai", "elevenlabs": "elevenlabs.io", "marble": "worldlabs.ai", "suno": "suno.com"}[source],)
    if not any(host == d or host.endswith("." + d) for d in domains):
        raise ModelError("Product URL does not match the selected source.")
    if re.search(r"/(?:downloads?|orders?|purchases?|receipts?|checkout|settings|account|library|home)(?:/|$)", urlsplit(url).path, re.I):
        raise ModelError("Use a public product URL, not a private order/download/account URL.")
    return url


def asset_identity(source, url):
    """Use stable store product IDs across locale/slug/shop URL variations when documented in URLs."""
    path = urlsplit(url).path
    patterns = {"booth": r"/items/(\d+)(?:/|$)",
                "fab": r"/listings/([a-fA-F0-9-]{32,36})(?:/|$)",
                "asset_store": r"-(\d+)(?:/reviews)?/?$",
                "sketchfab": r"([a-fA-F0-9]{32})/?$"}
    match = re.search(patterns[source], path) if source in patterns else None
    if match:
        return source + ":" + match[1].lower()
    import hashlib
    return source + ":owned-" + hashlib.sha256(url.encode()).hexdigest()[:24]


def cc0(value):
    license_value = value.get("license") or {}
    name = license_value.get("name", "") if isinstance(license_value, dict) else str(license_value)
    slug = re.sub(r"[\s_-]+", "", name).lower()
    if slug not in {"cc0", "cc01.0", "cc0.1.0", "cc0universal", "cc01.0universal"}:
        return False
    return not isinstance(license_value, dict) or license_value.get("attributionRequired") is not True


class AssetServer:
    """Uses the upstream REST contract; no Node install or vendor code execution required."""
    def __init__(self, http=None):
        self.http = http or ModelHTTP()
        self.base = os.getenv("FAFNIR_ASSET_SERVER_URL", "https://3d.shep.bot").rstrip("/")
        u = urlsplit(self.base)
        self.local = u.scheme == "http" and u.hostname in {"127.0.0.1", "localhost", "::1"}
        if u.query or u.fragment or u.path not in ("", "/"):
            raise ModelError("Asset Server URL must be an origin.")
        ModelHTTP.validate_url(self.base, {u.hostname}, local=self.local)
        self.host = u.hostname

    def request(self, path, params=None):
        url = self.base + path + ("?" + urlencode(params) if params else "")
        return self.http.json(url, {self.host}, local=self.local)

    def providers(self):
        result = self.request("/v1/providers")
        return result["providers"] if isinstance(result, dict) else result

    def search(self, query, kinds, sources, limit, offset):
        upstream_kinds = {"audio" if k in {"sfx", "music", "voice"} else "model" if k == "world" else k for k in kinds}
        return self.request("/v1/search", {"q": query, "type": ",".join(sorted(upstream_kinds)), "providers": ",".join(sources), "free": "true", "limit": limit, "offset": offset})

    def detail(self, asset_id, format="", resolution="2k"):
        from urllib.parse import quote
        asset = self.request("/v1/assets/" + quote(asset_id, safe=":"))
        selection = self.request("/v1/assets/" + quote(asset_id, safe=":") + "/files", {"format": format, "resolution": resolution})
        return asset, selection

    def bundle(self, asset_id, format="", resolution="2k"):
        from urllib.parse import quote
        return self.base + "/v1/assets/" + quote(asset_id, safe=":") + "/download?" + urlencode({"format": format, "resolution": resolution})


def provider_cards():
    rows = [{"id": k, "name": v[0], "url": v[1], "access": v[2],
             "ownership_sync": "browser_full_library" if k in {"booth", "fab", "humble"} else "existing_unity_bridge" if k == "asset_store" else "explicit_purchase_export_or_manifest",
             "account_authenticated": None,
             "note": "ブラウザー拡張の「購入サイトを全件同期」で購入ライブラリを走査。公式サイトにログインし、進捗・再開・定期同期を管理できます。" if k in {"booth", "fab", "humble"} else "購入履歴JSON/CSV・明示したローカル素材・拡張機能からの自己申告登録。公開APIによるアカウント自動同期とは区別します。"}
            for k, v in STORES.items()]
    rows += [{"id": k, **v}
             for k, v in GENERATORS.items()]
    return rows
