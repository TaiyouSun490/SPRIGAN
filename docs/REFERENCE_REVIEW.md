# Reference-based model review

The 3D Models page now has a reference comparison section. Set expectations for any of five dimensions: appearance, era, scale, material/wear, and construction/use. For each model, record what was actually observed and identify whether it was measured, seen, inferred, or unknown.

Jev receives bounded text/JSON evidence and returns a separate typed judgment for each configured criterion. It does not view cited images, fetch URLs, simulate physics, or produce freeform rationales. The displayed reasons come from the recorded expected/observed facts and source IDs. A vision operator or human must supply those observations. Missing evidence and speculative observations stay unresolved even when a model recommends adoption; an explicit failed manual check cannot be cleared by the evaluator. AnyJev remains advisory because its winning-option probability is uncalibrated.

MCP `evaluate_3d_model` accepts `reference_profile`:

```json
{
  "sources": [{"id":"frame117", "source":"reference-video 01:57", "note":"Observed metal housing; exact length cannot be measured from this shot."}],
  "criteria": [{
    "axis":"material", "expected":"Worn metal housing",
    "observed":"Candidate shows metal at scratched paint edges",
    "basis":"observed", "evidence_ids":["frame117"], "result":"unknown"
  }]
}
```

Valid axes: `reference`, `era`, `dimensions`, `material`, `construction`. Basis: `observed`, `measured`, `inferred`, `unknown`. Manual result: `pass`, `fail`, `unknown`. Reference sources are persisted as graph nodes linked by `COMPARED_WITH`; the full profile and its content hash are retained in every evaluation. Changing a condition in the UI invalidates the displayed evaluation. Existing reviews remain immutable historical snapshots.

## Sketchfab connection

Open **接続・保存先 → 初回だけ：Sketchfab接続の設定**. Enter the client ID and secret issued for your registered Sketchfab OAuth application. Register the exact callback URL shown by this running FAFNIR instance. **設定を保存してログイン** opens the official Sketchfab authorization page.

Optional **Windowsで暗号化して、このPCに接続を保存** retains app configuration and OAuth tokens using Windows user-scoped DPAPI, in `%LOCALAPPDATA%/FAFNIR/auth/`, isolated by catalog path. Refresh tokens renew the session on demand. Without this option, setup lasts for the current server process. Other OSes support session-only setup. No browser cookies, Unity credentials, password manager data, or other application credentials are read. Secrets are never returned by status, stored in the model graph, or exposed as MCP setup arguments.

**このPCの接続設定を解除** clears the local saved connection and disables environment fallback until the next process start. Revoke the remote grant separately in Sketchfab account settings if needed. Unreadable storage and expired authorization show a recovery state rather than silently claiming connectivity. An OAuth application registration is still required; FAFNIR cannot issue a client ID on Sketchfab's behalf.

Official contracts: [Jev state](https://docs.typesafe.ai/concepts/state), [typed answers](https://docs.typesafe.ai/api), [Sketchfab authenticated downloads](https://sketchfab.com/developers/download-api/downloading-models).

## Validation (2026-10-05)

31 local tests cover existing discovery/import boundaries, typed reference answers, missing evidence, inferred versus observed facts, conflicting manual checks, content fingerprints, MCP transport, exact-origin OAuth setup, callback matching, state invalidation, restart/refresh/disconnect and actual Windows DPAPI encryption with test-only values. JavaScript syntax checked. Browser GUI QA was attempted but Computer Use stopped because it could not determine the current browser URL with sufficient confidence; no further browser automation was attempted. Real Sketchfab authorization and live Jev evaluation are not verified without user-supplied service configuration.
