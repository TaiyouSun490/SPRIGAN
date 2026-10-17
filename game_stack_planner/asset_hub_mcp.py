"""Asset Server tool vocabulary plus ownership, provenance and AI-generation extensions."""
from __future__ import annotations
from typing import Any


def register_asset_hub_tools(server, app):
    from mcp.types import ToolAnnotations
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    network = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

    @server.tool(name="get_asset_hub_status", annotations=read)
    def status() -> dict[str, Any]:
        """Owned/CC0 library, purchase-source workflows, official generation APIs and connection readiness.
        Configured is not authenticated/verified. No API keys, cookies or private order URLs are returned.
        """
        return app.assets("status", {})

    @server.tool(name="list_providers", annotations=network)
    def providers(refresh: bool = False) -> dict[str, Any]:
        """List live 3D Asset Server sources and FAFNIR owned-source/generation adapters with their actual access modes."""
        return app.assets("providers", {"refresh": refresh})

    @server.tool(name="get_owned_library_sync_status", annotations=read)
    def library_sync_status(run_id: str = "") -> dict[str, Any]:
        """Read BOOTH/Fab/Humble browser full-library sync progress and completion evidence.
        Start/resume scans in the FAFNIR browser extension; site login stays in the browser.
        Partial pages are not complete libraries. No cookies, order keys or download tokens are returned.
        """
        return app.assets("library_sync_status", {"run_id": run_id})

    @server.tool(name="search_assets", annotations=network)
    def search(query: str = "", types: list[str] | None = None, providers: list[str] | None = None,
               rights: str = "owned_or_cc0", online: bool = True, limit: int = 20, offset: int = 0,
               free_only: bool = True, downloadable_only: bool = False) -> dict[str, Any]:
        """Search owned assets and CC0 public assets across the Asset Server sources. Includes SFX, music,
        voices, models, animation, materials, HDRIs and worlds. rights: owned/cc0/generated/owned_or_cc0/all.
        Free does not mean CC0. Public non-CC0 listings are excluded; imported ownership is user-declared evidence.
        """
        return app.assets("search", {"query": query, "types": types or [], "providers": providers or [], "rights": rights, "online": online, "limit": limit, "offset": offset, "free_only": free_only, "downloadable_only": downloadable_only})

    @server.tool(name="get_asset", annotations=read)
    def detail(id: str) -> dict[str, Any]:
        """Read one searched/imported asset's licence, ownership evidence, engine restriction and acquired files."""
        return app.assets("asset", {"asset_id": id})

    @server.tool(name="register_owned_asset_folder", annotations=write)
    def register_root(source: str, path: str) -> dict[str, Any]:
        """Register an explicitly user-selected asset folder; never scan a home directory or drive implicitly."""
        return app.assets("register_root", {"source": source, "path": path})

    @server.tool(name="sync_owned_asset_manifest", annotations=write)
    def sync(source: str, owned_confirmation: bool, items: list[dict] | None = None, csv: str = "") -> dict[str, Any]:
        """Idempotently import user-owned purchase exports/manifest rows for BOOTH, Fab, Humble and other stores,
        or exported Suno tracks. Never marks marketplace search results as purchased. No browser cookies,
        passwords, order keys or private download links may be supplied. Does not acquire or execute files.
        """
        payload = {"source": source, "owned_confirmation": owned_confirmation}
        payload.update({"csv": csv} if csv else {"items": items or []})
        return app.assets("import_owned", payload)

    @server.tool(name="prepare_asset_download", annotations=network)
    def prepare(id: str, format: str = "", resolution: str = "2k", max_bytes: int = 33554432) -> dict[str, Any]:
        """Review exact asset, current CC0 licence, selection, byte cap and cache destination before acquisition."""
        return app.assets("prepare_download", {"asset_id": id, "format": format, "resolution": resolution, "max_bytes": max_bytes})

    @server.tool(name="download_asset", annotations=network)
    def download(id: str, format: str = "", resolution: str = "2k", max_bytes: int = 33554432) -> dict[str, Any]:
        """Acquire a user-requested owned/CC0 asset into a new FAFNIR cache folder with checksums/provenance.
        Invoke only when the user authorized acquisition of this asset. Reuses that authorization for its
        scoped plan; no repeated permission round is needed. Never imports/executes vendor code or purchases.
        Store-only downloads use the official UI/Unity bridge instead of browser/session extraction.
        """
        app.assets("asset", {"asset_id": id})
        existing = app._asset_hub.reusable_files(id, format, resolution)
        if existing:
            return existing
        plan = app.assets("prepare_download", {"asset_id": id, "format": format, "resolution": resolution, "max_bytes": max_bytes})
        return app.assets("download", {"operation_id": plan["id"], "approval_nonce": plan["approval_nonce"], "accepted_license": plan["license"]["name"]})

    @server.tool(name="get_asset_operation", annotations=read)
    def operation(operation_id: str) -> dict[str, Any]:
        """Inspect saved acquisition/generation status after a timeout before any retry."""
        return app.assets("operation", {"operation_id": operation_id})

    @server.tool(name="prepare_generated_asset", annotations=write)
    def prepare_generation(provider: str, type: str, prompt: str, entitlement_note: str,
                           options: dict | None = None, max_output_bytes: int = 33554432) -> dict[str, Any]:
        """Prepare an official Tripo, ElevenLabs or Marble request without spending credits.
        Suno exported files are supported via import; no unverified official or cookie-based API is invented.
        Keep subscription/rights notes; generated content is not CC0 by default. Byte limits are not cost caps.
        """
        return app.assets("prepare_generation", {"provider": provider, "type": type, "prompt": prompt,
                         "entitlement_note": entitlement_note, "options": options or {}, "max_output_bytes": max_output_bytes})

    @server.tool(name="generate_asset", annotations=network)
    def generate(operation_id: str, approval_nonce: str, accept_provider_billing: bool) -> dict[str, Any]:
        """Submit the exact prepared request once after explicit authorization of provider billing.
        A timeout leaves submission_unknown and must never trigger an automatic paid resubmission.
        API credentials are configured through the local Connections UI/environment, never MCP arguments.
        """
        return app.assets("generate", {"operation_id": operation_id, "approval_nonce": approval_nonce, "accept_provider_billing": accept_provider_billing})

    @server.tool(name="poll_generated_asset", annotations=network)
    def poll(operation_id: str) -> dict[str, Any]:
        """Read the existing remote job. Does not submit another generation."""
        return app.assets("poll_generation", {"operation_id": operation_id})

    @server.tool(name="collect_generated_asset", annotations=network)
    def collect(operation_id: str) -> dict[str, Any]:
        """Collect completed remote outputs within the original byte cap; retain source/task/provenance."""
        return app.assets("collect_generation", {"operation_id": operation_id})

    @server.tool(name="export_project_asset_manifest", annotations=read)
    def export(asset_ids: list[str], target: str = "unity") -> dict[str, Any]:
        """Portable file/dependency/rights manifest for Unity, Unreal, Blender or web import adapters.
        Unknown licences, missing files and engine-only restrictions block ready=true.
        This tool does not copy into a project or execute vendor code.
        """
        return app.assets("export", {"asset_ids": asset_ids, "target": target})

    @server.tool(name="record_asset_usage_rights", annotations=write)
    def review_rights(asset_id: str, license: str, engine_restriction: str, entitlement_note: str,
                      review_confirmation: bool, license_url: str = "") -> dict[str, Any]:
        """Record the user's actual licence review for owned/generated assets. Does not verify a purchase,
        alter public CC0 source evidence or infer cross-engine rights from ownership alone.
        """
        return app.assets("review_rights", {"asset_id": asset_id, "license": license, "license_url": license_url,
                          "engine_restriction": engine_restriction, "entitlement_note": entitlement_note, "review_confirmation": review_confirmation})
