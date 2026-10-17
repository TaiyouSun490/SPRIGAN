# FAFNIR 3D Models local plugin

Requires Python 3.12+ and the FAFNIR runtime from the same release/source revision as this package. It contains MCP configuration and workflow instructions, not the Python runtime.

From the FAFNIR checkout:

```powershell
python -m pip install -e .
fafnir --version
fafnir --json models status
```

Make `fafnir` available on the plugin host's PATH. If using a virtual environment, configure `mcp.json`'s `command` to the **absolute** path of that environment's `Scripts/fafnir.exe` (Windows) or `bin/fafnir` (POSIX). No hardcoded developer path is bundled. Restart the MCP connection after configuration changes.

Install this directory through your client's supported local-plugin flow. The package is not automatically installed into an account or published. For cloud Dots, enable the supported connection to the local computer, install/connect this plugin there, and keep that computer and its host app online. Do not expose the loopback HTTP UI on the public internet.

Credentials are supplied to the FAFNIR process through environment variables. Search and Poly Haven downloads require no user credential. `TYPESAFE_API_KEY` enables the hosted Jev evaluator. Sketchfab downloads use a user OAuth token in `FAFNIR_SKETCHFAB_ACCESS_TOKEN`, obtained through Sketchfab's official flow. The browser UI also supports its own memory-only OAuth session; that session is **not shared with a separate stdio MCP process**. Never put real keys or tokens in this package.

For local AnyJev, install FAFNIR with `python -m pip install -e '.[decision-local]'`, start your chosen compatible vLLM backend separately, and configure `FAFNIR_ANYJEV_MODEL` and optionally `FAFNIR_ANYJEV_URL` (loopback only).

Run `get_3d_model_status` after connecting. Live hosted Jev inference, account-level Dots connection, and authenticated Sketchfab downloads require the operator's own configuration; packaging alone does not validate them.
