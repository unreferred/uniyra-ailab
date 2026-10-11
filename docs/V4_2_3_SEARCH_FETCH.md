# V4.2.3 Search + Fetch Foundation & Extensibility Guide

This document describes the design, authorization model, inheritance chain, and developer extensibility seams for the Search and Fetch Foundation introduced in OmniLab V4.2.3.

---

## 1. Architectural Overview

OmniLab V4.2.3 adds first-class, provider-agnostic search and fetch capabilities while preserving core OmniLab invariants:
- **Canonical Execution Funnel**: All model interactions proceed through `execute_model()` in `backend/app/services/execution.py`.
- **Strict Authorization**: `search.execute` and `fetch.execute` adhere to **DEFAULT DENY**. They are never auto-granted.
- **Provider Abstraction**: Decoupled registries allow plugging in arbitrary external providers (TinyFish, Tavily, Brave, Serper, Playwright, etc.) without altering the LLM's canonical tool schema.
- **Security by Design**: SSRF prevention blocks private IPs, loopback addresses, metadata endpoints, and re-validates every redirect hop.

```
+-------------------------------------------------------------+
|                      Composer Toolbar                       |
|   [Tools: N]  [Search: On/Off]  [Fetch: On/Off]  [Knowledge] |
+------------------------------+------------------------------+
                               |
                               v
                     MessageRequest Payload
        { search_enabled, search_provider_id,
          fetch_enabled, fetch_provider_id }
                               |
                               v
                     Workspace Router / Run
               (Resolves Effective Inheritance)
                               |
                               v
               Canonical Tools Auto-Wired into Run:
         - tool-search-0001 (perm: search.execute)
         - tool-fetch-0001  (perm: fetch.execute)
                               |
                               v
            Execution Service (services/execution.py)
          Passes context={search_provider_id, ...}
                               |
                               v
                 Async Builtin Tools Dispatcher
                   - run_search() -> SearchProviderRegistry
                   - run_fetch()  -> FetchProviderRegistry
                               |
                +--------------+--------------+
                |                             |
                v                             v
      Search Providers               Fetch Providers
     - TinyFishSearchProvider        - StandardFetchProvider (SSRF guarded)
     - (Custom Extensible)           - TinyFishFetchProvider
                                     - (Custom Extensible)
```

---

## 2. Canonical Builtin Tools

Two immutable system tools are seeded into the database (`builtin_tools.py`):

1. **`tool-search-0001` (`web_search`)**
   - **Permissions**: `["search.execute"]`
   - **Risk Level**: `low`
   - **Input Schema**:
     - `query` (string, required): Search query keywords
     - `max_results` (integer, optional, default: 5, max: 10)
   - **Output Format**: Array of `{ title, url, snippet }`

2. **`tool-fetch-0001` (`web_fetch`)**
   - **Permissions**: `["fetch.execute"]`
   - **Risk Level**: `low`
   - **Input Schema**:
     - `url` (string, required): Full HTTP/HTTPS URL
     - `max_length` (integer, optional, default: 20000, max: 50000)
   - **Output Format**: `{ url, title, text, status_code, content_type }`

---

## 3. Authorization Closure: Default Deny

In accordance with OmniLab security principles:
- Neither `search.execute` nor `fetch.execute` is included in `SAFE_BUILTIN_PERMISSIONS`.
- Invoking `web_search` or `web_fetch` without explicit enablement in the workspace or conversation results in `TOOL_PERMISSION_DENIED`.
- When the user toggles Search or Fetch **On** in the composer toolbar:
  - `tool-search-0001` or `tool-fetch-0001` is added to `enabled_tool_ids` for the request and conversation.
  - The permission evaluator (`resolve_effective_permission`) grants execution rights solely because the tool is explicitly selected.

---

## 4. Configuration Inheritance Chain

Search and fetch settings follow OmniLab's five-tier inheritance chain:

1. **Global Default**:
   - Search: `disabled`, provider `prov-search-tinyfish`
   - Fetch: `disabled`, provider `prov-fetch-standard`
2. **Project Level** (`Project` doc):
   - `default_search_enabled`, `default_search_provider_id`
   - `default_fetch_enabled`, `default_fetch_provider_id`
3. **Conversation Level** (`Conversation` doc):
   - `search_enabled`, `search_provider_id`
   - `fetch_enabled`, `fetch_provider_id`
4. **Request Level** (`MessageRequest` / `RegenerateRequest`):
   - Explicit overrides passed from composer state
5. **Run Snapshot** (`Run` doc):
   - Pinned into the immutable Run metadata: `search_enabled`, `search_provider_id`, `fetch_enabled`, `fetch_provider_id`.

Resolution is implemented in `app/services/project_manager.py::resolve_project_defaults()` and `app/routers/workspace.py::_resolve_effective()`.

---

## 5. Security & SSRF Protection

The `StandardFetchProvider` implements multi-layer defense against Server-Side Request Forgery (SSRF):
- **Scheme Validation**: Only `http://` and `https://` are permitted. Schemes like `file://`, `gopher://`, `ftp://` are rejected immediately.
- **Private & Reserved IP Blocking**: Hostnames are resolved to IP addresses and verified against Python's `ipaddress.is_private`, `is_loopback`, `is_reserved`, `is_link_local`, `is_multicast`.
- **Cloud Metadata Protection**: Cloud metadata endpoints (e.g., `169.254.169.254`, `metadata.google.internal`) are blocked.
- **Multi-Hop Redirect Re-Validation**: Client redirects are not followed blindly. Each redirect URL is intercepted and undergoes full re-resolution and IP check before following.
- **Payload Limits**: Max response size defaults to 512 KB to avoid memory exhaustion attacks.
- **HTML Sanitization**: HTML tags, scripts, styles, and comments are stripped via `html.parser`, extracting only readable text content and page title.

---

## 6. How to Add a New Search or Fetch Provider

The architecture is designed for trivial extension without modifying tool definitions or execution pipelines.

### Step 1: Implement the Provider Class

#### A. Custom Search Provider
Subclass `BaseSearchProvider` in `backend/app/services/search_providers.py`:

```python
from app.services.search_providers import BaseSearchProvider, SearchResult

class TavilySearchProvider(BaseSearchProvider):
    provider_type = "tavily"

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        api_key = self.get_decrypted_credential("api_key")
        if not api_key:
            raise ValueError("Tavily API key not configured")

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={"query": query, "api_key": api_key, "max_results": max_results}
            )
            data = resp.json()
            return [
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", "")
                )
                for item in data.get("results", [])
            ]

    async def test_connection(self) -> dict[str, Any]:
        api_key = self.get_decrypted_credential("api_key")
        if not api_key:
            return {"ok": False, "status": "NOT_CONFIGURED", "message": "API key missing"}
        # Execute lightweight ping
        return {"ok": True, "status": "CONNECTED", "message": "Tavily connection ready"}
```

#### B. Custom Fetch Provider
Subclass `BaseFetchProvider` in `backend/app/services/fetch_providers.py`:

```python
from app.services.fetch_providers import BaseFetchProvider, FetchResult

class BrowserlessFetchProvider(BaseFetchProvider):
    provider_type = "browserless"

    async def fetch(self, url: str, max_length: int = 20000) -> FetchResult:
        # Validate URL via security guard before dispatching
        # Implement headless browser fetch...
        ...
```

### Step 2: Register in the Provider Registry

In `backend/app/services/search_providers.py`:
```python
SearchProviderRegistry.register_adapter("tavily", TavilySearchProvider)
```

In `backend/app/services/fetch_providers.py`:
```python
FetchProviderRegistry.register_adapter("browserless", BrowserlessFetchProvider)
```

### Step 3: Seed or Create Provider Record

Add seed configuration in `ensure_default_search_providers()` / `ensure_default_fetch_providers()` or create via `POST /api/search-providers` (or database seed script):

```json
{
  "id": "prov-search-tavily",
  "name": "Tavily Web Search",
  "provider_type": "tavily",
  "enabled": true,
  "capabilities": { "search": "SUPPORTED" },
  "configuration": { "base_url": "https://api.tavily.com" }
}
```

The frontend `<SearchControl />` and `<FetchControl />` will automatically detect the new provider via `GET /api/search-providers` and `GET /api/fetch-providers`, displaying it in the workspace dropdown without requiring any UI changes.

---

## 7. V4.2.3.6 Remote Fetch Trust-Boundary Closure

### 7.1 LOCAL vs REMOTE egress

`backend/app/urlguard.py` now defines two explicit egress trust boundaries:

| Constant | Value | Meaning |
| --- | --- | --- |
| `LOCAL_EGRESS` | `"LOCAL"` | OmniLab itself opens the outbound connection: the target host is resolved locally and every resolved address is SSRF-checked. |
| `REMOTE_EGRESS` | `"REMOTE"` | A remote provider (TinyFish) opens the connection: OmniLab only performs syntactic/policy validation and **must not** resolve ordinary public hostnames locally. |

`BaseFetchProvider.egress_mode` defaults to `LOCAL`; `StandardFetchProvider` is
`LOCAL` (unchanged), `TinyFishFetchProvider` is `REMOTE`. Search is `REMOTE`
(`BaseSearchProvider.egress_mode`).

**Standard HTTP Fetch is unchanged**: it still uses
`async_validate_provider_url()` (full local DNS) for the request URL and for
*every* redirect hop, and still blocks loopback/private/link-local/metadata
targets.

### 7.2 Remote target validation

`TinyFishFetchProvider.fetch()` now performs two separate validations:

1. **Provider endpoint** — `async_validate_provider_url(TINYFISH_FETCH_ENDPOINT)`
   (full local validation) before OmniLab contacts it.
2. **Target URL** — `async_validate_remote_target_url(target)` which:
   - reuses the existing `_inspect()` syntax pass (http/https only, no embedded
     credentials, host required, valid port, fragment stripped, **query
     preserved exactly**);
   - still runs `_check_addresses()` for **literal IPs** and blocks
     loopback/private/link-local/metadata/multicast/reserved (respecting
     `allow_local`/`allow_private`/allowlist exactly as before);
   - blocks `localhost` / `*.localhost` names (unless `allow_local`);
   - **accepts ordinary public hostnames without any local DNS resolution** and
     never raises `DNS_RESOLUTION_FAILED`.

This means a hostname OmniLab cannot resolve locally (for example
`www.forexfactory.com`) is still handed to TinyFish unchanged. A failure to
contact the TinyFish endpoint is a provider error, never a target DNS error.

### 7.3 Canonical, adapter-owned endpoints

The TinyFish endpoints are adapter-owned constants and are **not**
user-configurable:

- `fetch_providers.TINYFISH_FETCH_ENDPOINT = "https://api.fetch.tinyfish.ai"`
- `search_providers.TINYFISH_SEARCH_ENDPOINT = "https://api.search.tinyfish.ai"`

The provider doc's `configuration.base_url` (writable through the provider
update API) is deliberately **ignored** so it can never redirect OmniLab's
authenticated provider call. Only a narrowly-scoped env override
(`TINYFISH_FETCH_BASE_URL` / `TINYFISH_BASE_URL`,
`TINYFISH_SEARCH_BASE_URL` / `TINYFISH_BASE_URL`) may change the endpoint for
tests/ops, and that value is still fully SSRF-validated before use. `base_url`
is not exposed as a valve.

### 7.4 Search location / language

`TinyFishSearchProvider.search(query, *, max_results=None, location=None,
language=None)` accepts optional regional hints, sent to TinyFish as the
`location`/`language` query parameters. Resolution precedence is
**tool-call argument > project valve value > default** (`location="ID"`,
`language="id"`). The defaults live in the `tinyfish` search valve schema so a
project can change them; nothing else hardcodes them. The actually resolved
values are preserved in the provider result (`location`, `language`) and carried
into the Run's tool rounds and trace metadata. `tool-search-0001` exposes
optional `location`/`language` inputs and threads them through `run_search()`.

### 7.5 Trace / tool metadata

Provider results are threaded (secret-free) into the `TOOL_RESULT` span
metadata and `Run.tool_rounds`:

- fetch: `provider`/`fetch_provider`, `egress_mode` (`LOCAL`|`REMOTE`),
  `target_url`, `provider_endpoint_host` (host only, never a URL with
  credentials), `provider_status`, `duration_ms`.
- search: `provider`/`search_provider`, `egress_mode` (`REMOTE`), `query`,
  `location`, `language`, `provider_status`, `duration_ms`.

### 7.6 Official TinyFish SDK decision (V4.2.3.6 audit)

Decision: **keep the raw `httpx` adapter; do not adopt the official
`tinyfish` Python SDK at this time.**

Rationale:

- The `tinyfish` package is **not installed** and is not in
  `requirements.txt`/`requirements-optional.txt`; adopting it would add a new
  runtime dependency and supply-chain surface for no functional gain now.
- The existing raw-HTTP adapters already give explicit control over per-request
  timeouts, structured upstream error extraction, cancellation (the call runs on
  the application's event loop and is awaited normally), secret handling
  (server-side decryption, never logged) and deterministic offline testability
  (the adapters are trivially patched in tests). The SDK would not reduce
  telemetry or observability, but it would hide the request shape and make the
  endpoint/contract less explicit.
- The current TinyFish API contract expected by OmniLab
  (`POST {endpoint}` with `{"urls": [...], "format", "ttl"}` for fetch;
  `GET {endpoint}?query=&location=&language=` for search) is already encoded and
  covered by tests. There is no evidence the SDK exposes a materially better
  contract.

If TinyFish later ships a stable, versioned SDK whose tested behavior reduces
risk (typed responses, forward-compatible contract, equal or better error
handling) it can be adopted behind the same `BaseFetchProvider`/
`BaseSearchProvider` seam without changing callers.
