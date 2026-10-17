"""3D source adapters. Only official APIs; downloads never receive API credentials."""
from __future__ import annotations

import json
import os
import re
from pathlib import PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ModelError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # In particular, do not forward an Authorization header to a CDN.
        return None


class ModelHTTP:
    def __init__(self, opener=None):
        self.opener = opener or build_opener(NoRedirect()).open

    @staticmethod
    def validate_url(url, hosts, *, local=False):
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except (TypeError, ValueError):
            raise ModelError("Provider returned an invalid URL.") from None
        if (parsed.username or parsed.password or parsed.fragment
                or parsed.hostname not in hosts
                or not (parsed.scheme == "https" or
                        (local and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}))):
            raise ModelError("Provider returned an unsupported URL.")
        if not local and port not in (None, 443):
            raise ModelError("Provider returned an unsupported port.")

    def request(self, url, hosts, *, headers=None, payload=None, form=None, local=False):
        self.validate_url(url, hosts, local=local)
        data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
        if form is not None:
            data = urlencode(form).encode()
        request = Request(url, data=data, headers={
            "User-Agent": "FAFNIR/0.5 (3D asset catalog)",
            "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded" if form is not None else "application/json", **(headers or {})})
        try:
            return self.opener(request, timeout=30)
        except HTTPError as exc:
            # Response bodies / signed URLs / auth headers must not reach logs or the graph.
            raise ModelError(f"Provider HTTP {exc.code}; check access, quota, or retry later.") from None
        except (URLError, TimeoutError, OSError):
            raise ModelError("Provider unavailable; retry later.") from None

    def json(self, url, hosts, **kwargs):
        with self.request(url, hosts, **kwargs) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise ModelError("Provider metadata exceeds the response limit.")
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            raise ModelError("Provider returned invalid JSON.") from None
        if not isinstance(value, dict):
            raise ModelError("Provider returned an unexpected response.")
        return value


def safe_path(value):
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or "\\" in value or ":" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or any(part.rstrip(" .") != part for part in path.parts)
            or any(re.fullmatch(r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?", p) for p in path.parts)):
        raise ModelError("Unsafe filename in model package.")
    return str(path)


def identifier(value, *, sketchfab=False):
    pattern = r"[a-f0-9]{32}" if sketchfab else r"[a-zA-Z0-9_-]{1,120}"
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ModelError("Invalid model identifier.")
    return value


class Sketchfab:
    name = "sketchfab"
    hosts = {"api.sketchfab.com"}
    download_hosts = {"sketchfab-prod-media.s3.amazonaws.com", "media.sketchfab.com"}

    def __init__(self, http=None):
        self.http = http or ModelHTTP()
        self.session_token = None

    def headers(self, required=False):
        token = self.session_token() if self.session_token else os.getenv("FAFNIR_SKETCHFAB_ACCESS_TOKEN", "")
        if required and not token:
            raise ModelError("Sketchfab sign-in required: configure FAFNIR_SKETCHFAB_ACCESS_TOKEN from the official OAuth flow.")
        return {"Authorization": "Bearer " + token} if token else {}

    def normalize(self, value):
        uid = identifier(value.get("uid"), sketchfab=True)
        license_info = value.get("license") or {}
        slug = license_info.get("slug") or "unknown"
        license_id = {"by": "cc-by", "by-sa": "cc-by-sa", "by-nc": "cc-by-nc",
                      "by-nc-sa": "cc-by-nc-sa", "by-nd": "cc-by-nd", "by-nc-nd": "cc-by-nc-nd"}.get(slug, slug)
        license_url = license_info.get("url")
        if license_url and license_url.startswith("http://creativecommons.org/"):
            license_url = "https://" + license_url[len("http://"):]
        user = value.get("user") or {}
        images = (value.get("thumbnails") or {}).get("images") or []
        thumbnail = next((i.get("url") for i in sorted(images, key=lambda i: i.get("width", 0), reverse=True)
                          if urlsplit(i.get("url", "")).hostname == "media.sketchfab.com"), None)
        return {
            "id": f"sketchfab:{uid}", "source": self.name, "external_id": uid,
            "title": value.get("name", uid), "url": f"https://sketchfab.com/3d-models/{uid}",
            "description": str(value.get("description") or "")[:8000],
            "author": user.get("displayName") or user.get("username") or "Unknown",
            "author_id": user.get("uid") or user.get("username") or "unknown",
            "license": license_id, "license_url": license_url,
            "tags": [t["slug"] for t in value.get("tags", []) if isinstance(t, dict) and t.get("slug")][:100],
            "thumbnail": thumbnail, "triangles": value.get("faceCount"),
            "vertices": value.get("vertexCount"), "materials": None,
            "downloadable": value.get("isDownloadable") is True,
            "formats": sorted(k for k in (value.get("archives") or {}) if k in {"gltf", "glb", "usdz"}),
        }

    def search(self, query, limit, cursor=""):
        args = {"type": "models", "q": query, "count": limit, "downloadable": "true", "source": "fafnir"}
        if cursor:
            args["cursor"] = cursor
        value = self.http.json("https://api.sketchfab.com/v3/search?" + urlencode(args), self.hosts, headers=self.headers())
        from urllib.parse import parse_qs
        next_cursor = parse_qs(urlsplit(value.get("next") or "").query).get("cursor", [""])[0]
        return [self.normalize(v) for v in value.get("results", [])], next_cursor

    def detail(self, uid):
        uid = identifier(uid, sketchfab=True)
        return self.normalize(self.http.json(f"https://api.sketchfab.com/v3/models/{uid}", self.hosts, headers=self.headers()))

    def files(self, uid, format, resolution):
        uid = identifier(uid, sketchfab=True)
        value = self.http.json(f"https://api.sketchfab.com/v3/models/{uid}/download", self.hosts, headers=self.headers(required=True))
        selected = value.get(format)
        if not isinstance(selected, dict) or not selected.get("url"):
            raise ModelError("This model is unavailable in the selected format.")
        extension = "zip" if format == "gltf" else format
        return [{"path": f"model.{extension}", "url": selected["url"], "size": selected.get("size")}]


class PolyHaven:
    name = "polyhaven"
    hosts = {"api.polyhaven.com"}
    download_hosts = {"dl.polyhaven.org", "dl.polyhaven.com"}

    def __init__(self, http=None):
        self.http = http or ModelHTTP()

    def normalize(self, uid, value):
        uid = identifier(uid)
        authors = value.get("authors") or {}
        return {
            "id": f"polyhaven:{uid}", "source": self.name, "external_id": uid,
            "title": value.get("name", uid), "url": f"https://polyhaven.com/a/{uid}",
            "description": str(value.get("description") or "")[:8000],
            "author": ", ".join(authors) or "Poly Haven", "author_id": ",".join(sorted(authors)) or "polyhaven",
            "license": "cc0", "license_url": "https://polyhaven.com/license",
            "tags": list(dict.fromkeys([*value.get("tags", []), *value.get("categories", [])]))[:100],
            "thumbnail": f"https://cdn.polyhaven.com/asset_img/thumbs/{uid}.png?width=512",
            "triangles": None, "vertices": None, "materials": None,
            "downloadable": True, "formats": [],
        }

    def search(self, query, limit, cursor=""):
        values = self.http.json("https://api.polyhaven.com/assets?t=models", self.hosts)
        terms = query.casefold().split()
        matches = [self.normalize(uid, v) for uid, v in sorted(values.items())
                   if all(term in json.dumps([uid, v.get("name"), v.get("tags"), v.get("categories")]).casefold() for term in terms)]
        try:
            start = int(cursor or 0)
            if start < 0:
                raise ValueError()
        except ValueError:
            raise ModelError("Invalid Poly Haven cursor.") from None
        return matches[start:start + limit], str(start + limit) if len(matches) > start + limit else ""

    def detail(self, uid):
        uid = identifier(uid)
        value = self.http.json(f"https://api.polyhaven.com/info/{uid}", self.hosts)
        if value.get("type") != 2:
            raise ModelError("This Poly Haven asset is not a 3D model.")
        result = self.normalize(uid, value)
        files = self.http.json(f"https://api.polyhaven.com/files/{uid}", self.hosts)
        result["formats"] = sorted(k for k in files if k in {"blend", "gltf", "fbx", "usd", "glb"})
        return result

    def files(self, uid, format, resolution):
        uid = identifier(uid)
        value = self.http.json(f"https://api.polyhaven.com/files/{uid}", self.hosts)
        choices = value.get(format, {}).get(resolution, {})
        if not choices:
            raise ModelError("Selected format/resolution is unavailable; choose another.")
        selected = next(iter(choices.values()))
        path = safe_path(PurePosixPath(urlsplit(selected["url"]).path).name)
        return [{"path": path, **{k: v for k, v in selected.items() if k != "include"}},
                *[{"path": safe_path(p), **v} for p, v in selected.get("include", {}).items()]]
