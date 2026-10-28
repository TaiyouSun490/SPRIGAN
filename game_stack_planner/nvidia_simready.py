"""NVIDIA SimReady dataset index: metadata only, never bulk-download USD assets."""
from __future__ import annotations

import json
import re
import threading
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from .model_sources import ModelError, ModelHTTP, identifier
from .model_graph import timestamp

DATASET = "https://huggingface.co/datasets/nvidia/simready-assets"
API = "https://huggingface.co/api/datasets/nvidia/simready-assets"
HOSTS = {"huggingface.co"}
LICENSE = "https://creativecommons.org/licenses/by/4.0/"


class NvidiaSimReady:
    name = "nvidia_simready"
    download_hosts = set()

    def __init__(self, graph, http=None):
        self.graph = graph
        self.http = http or ModelHTTP()
        self.refresh_lock = threading.Lock()
        with graph.lock, graph.connection:
            graph.connection.execute("CREATE TABLE IF NOT EXISTS nvidia_simready_index (uid TEXT PRIMARY KEY, search_text TEXT NOT NULL)")
            graph.connection.execute("CREATE TABLE IF NOT EXISTS nvidia_simready_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def state(self):
        with self.graph.lock:
            count = self.graph.connection.execute("SELECT count(*) FROM nvidia_simready_index").fetchone()[0]
            values = dict(self.graph.connection.execute("SELECT key,value FROM nvidia_simready_state").fetchall())
        return {"count": count, "revision": values.get("revision", ""), "updated_at": values.get("updated_at", "")}

    def refresh(self):
        # Fetch into memory first. Failed pagination never replaces a working index.
        with self.refresh_lock:
            info = self.http.json(API + "?expand%5B%5D=sha", HOSTS)
            sha = info.get("sha", "")
            if not re.fullmatch(r"[a-f0-9]{40}", sha):
                raise ModelError("NVIDIA dataset revision was unavailable.")
            names, cursor, seen = [], "", set()
            for _ in range(100):
                params = {"recursive": "false", "limit": 1000}
                if cursor:
                    params["cursor"] = cursor
                with self.http.request(API + "/tree/" + sha + "?" + urlencode(params), HOSTS) as response:
                    data = response.read(2 * 1024 * 1024 + 1)
                    link = response.headers.get("Link", "")
                if len(data) > 2 * 1024 * 1024:
                    raise ModelError("NVIDIA index page exceeds the metadata limit.")
                try:
                    page = json.loads(data)
                except (ValueError, UnicodeError):
                    raise ModelError("NVIDIA index page was invalid.") from None
                if not isinstance(page, list):
                    raise ModelError("NVIDIA index page was invalid.")
                for row in page:
                    if not isinstance(row, dict) or row.get("type") != "directory":
                        continue
                    try:
                        uid = identifier(row.get("path"))
                    except ModelError:
                        continue
                    names.append(uid)
                match = re.search(r'<([^>]+)>\s*;\s*rel="next"', link)
                if not match:
                    break
                parsed = urlsplit(match.group(1))
                if parsed.scheme != "https" or parsed.netloc != "huggingface.co" or parsed.path != "/api/datasets/nvidia/simready-assets/tree/" + sha:
                    raise ModelError("Unexpected NVIDIA pagination link.")
                next_cursor = parse_qs(parsed.query).get("cursor", [""])[0]
                if not next_cursor or len(next_cursor) > 1000 or next_cursor in seen:
                    raise ModelError("Invalid NVIDIA index cursor.")
                seen.add(next_cursor)
                cursor = next_cursor
            else:
                raise ModelError("NVIDIA index exceeded the page limit.")
            names = sorted(set(names))
            if not names:
                raise ModelError("NVIDIA returned an empty index; existing index retained.")
            with self.graph.lock, self.graph.connection:
                db = self.graph.connection
                db.execute("DELETE FROM nvidia_simready_index")
                db.executemany("INSERT INTO nvidia_simready_index VALUES(?,?)", [(v, self.search_text(v)) for v in names])
                db.executemany("INSERT OR REPLACE INTO nvidia_simready_state VALUES(?,?)", [("revision", sha), ("updated_at", timestamp())])
            return self.state()

    @staticmethod
    def search_text(uid):
        return re.sub(r"([a-z])([A-Z])", r"\1 \2", uid).replace("_", " ").casefold() + " " + uid.casefold()

    def normalize(self, uid, sha):
        return {"id": f"nvidia_simready:{uid}", "source": self.name, "external_id": uid,
                "title": re.sub(r"([a-z])([A-Z])", r"\1 \2", uid).replace("_", " "),
                "url": f"{DATASET}/tree/{sha}/{uid}", "description": "NVIDIA SimReady OpenUSD package. License and preview require individual metadata review.",
                "author": "NVIDIA", "author_id": "nvidia", "license": "unknown", "license_url": None,
                "tags": uid.split("_")[:20], "thumbnail": None, "triangles": None, "vertices": None,
                "materials": None, "downloadable": False, "formats": ["usd"],
                "external_download": True, "dataset_revision": sha}

    def search(self, query, limit, cursor=""):
        state = self.state()
        if not state["count"]:
            state = self.refresh()
        try:
            start = int(cursor or 0)
            if start < 0 or start > 100000:
                raise ValueError()
        except ValueError:
            raise ModelError("Invalid NVIDIA index cursor.") from None
        aliases = {"木": "wood", "岩": "rock", "石": "stone", "車": "vehicle", "食べ物": "food", "食品": "food", "果物": "fruit"}
        terms = [aliases.get(term, term).casefold() for term in query.split()[:20]]
        where = " AND ".join("instr(search_text,?)>0" for _ in terms) or "1"
        with self.graph.lock:
            rows = self.graph.connection.execute("SELECT uid FROM nvidia_simready_index WHERE " + where + " ORDER BY uid LIMIT ? OFFSET ?", [*terms, limit + 1, start]).fetchall()
        found = [self.normalize(r[0], state["revision"]) for r in rows[:limit]]
        return found, str(start + limit) if len(rows) > limit else ""

    def detail(self, uid):
        uid = identifier(uid)
        state = self.state()
        with self.graph.lock:
            exists = self.graph.connection.execute("SELECT 1 FROM nvidia_simready_index WHERE uid=?", (uid,)).fetchone()
        if not exists or not state["revision"]:
            raise ModelError("NVIDIA asset not indexed; search or refresh first.")
        sha = state["revision"]
        result = self.normalize(uid, sha)
        root = DATASET + "/raw/" + sha + "/" + uid + "/"
        metadata = self.http.json(root + "com.nvidia.simready.packaging.json", HOSTS)
        license_name = metadata.get("license")
        if license_name == "CC-BY-4.0":
            result.update(license="cc-by", license_url=LICENSE,
                          description="CC BY 4.0: attribution is required. OpenUSD package; engine compatibility and performance are unverified. Obtain the complete package through NVIDIA/Hugging Face.")
        elif isinstance(license_name, str) and license_name:
            result["description"] = "Provider license: " + license_name[:120] + "; review the source before use."
        # Discover an actual supplied preview; never invent a thumbnail path.
        folder = f"{uid}/simready_usd/.thumbs/256x256"
        url = API + "/tree/" + sha + "/" + quote(folder, safe="/") + "?limit=100"
        try:
            with self.http.request(url, HOSTS) as response:
                raw = response.read(1024 * 1024 + 1)
            page = json.loads(raw) if len(raw) <= 1024 * 1024 else []
            candidates = sorted(r["path"] for r in page if isinstance(r, dict) and r.get("type") == "file" and isinstance(r.get("path"), str) and r["path"].startswith(folder + "/") and re.fullmatch(r"[A-Za-z0-9_.-]+\.usd\.png", r["path"][len(folder) + 1:]))
            if candidates:
                result["thumbnail"] = DATASET + "/resolve/" + sha + "/" + quote(candidates[0], safe="/")
        except (ModelError, ValueError, TypeError, KeyError):
            pass
        return result

    def files(self, uid, format, resolution):
        raise ModelError("NVIDIA SimReady packages must be obtained from the official source; FAFNIR does not yet download USD dependencies.")
