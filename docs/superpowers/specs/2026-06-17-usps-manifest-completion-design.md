# USPS SCAN-Form Manifest — Completion to Upstream-PR Quality (Scope B) — Design

**Date:** 2026-06-17
**Status:** Approved scope (B), decisions locked; awaiting spec review → writing-plans
**Repo:** karrio fork (`zggit/karrio`), new worktree off `production`
**Backed by:** design-investigation workflow `wf_5d9d5bf6-ed5` (5 deep-dives + synthesis) + GitHub/docs research

---

## Goal

Harden the already-working USPS SCAN-form manifest path (connector + server + dashboard + tests) to upstream-PR quality, eliminating the four Canada-Post-Discussion-#757 failure classes for USPS — blank/corrupt PDF, no success confirmation, wrong account on multi-account, silent partial drops — with surgical changes that match karrio's existing patterns.

## Background (where we are)

The core USPS scan-form manifest **already works** in our fork (fixed + deployed + verified end-to-end today: `POST /v1/manifests` → 201, 93688-byte `%PDF` stored, `manifestNumber` extracted). Already committed to `production`: multipart parse (`SCANFormMetaData` part + `SCANFormImage` PDF), `manifestNumber` extraction, the server `ManifestSerializer` address-dict fix. Research confirmed USPS scan-form is real-but-undocumented upstream (no docs/changelog/issues); the only public manifest bugs are Canada Post's (Discussion #757, unresolved). This spec completes + hardens what remains.

## Decisions (locked)

| # | Decision | Choice |
|---|---|---|
| Surface | How SCAN forms are printed | **Both** — hardened API **and** karrio dashboard manual flow |
| `manifest_required` | Dashboard eligibility mechanism | **Per-connection opt-in config, default OFF** — does NOT change completion semantics for the automated Tongtool flow; operator enables it on the connection they manifest manually from |
| mailingDate out-of-window | today..+7 violated | **Hard-fail 400** (explicit ValidationError), NOT silent `overwriteMailingDate` auto-snap |
| Real test fixture | verify-then-code | **Use the captured real `scan-forms/v3` multipart** (`/tmp/real_scanform.txt`, captured from live trace today) verbatim as the connector fixture |
| Shipment cap | USPS per-SCAN-form max | Ship the **cap mechanism**; `usps:1000` as a **documented assumption + per-connection config override** (v3 OpenAPI unconfirmed) |
| Download error code | present-but-unrenderable doc | **422 Unprocessable** (row found, payload bad) — distinct from 404 |
| Connection selector field | multi-account | top-level **`carrier_id`** (matches shipment-cancel; schema-discoverable), not pickup's `options.connection_id` |
| Blank-doc defense | where caught | **Defense-in-depth**: connector prevents bad writes (A2/A3) **and** server guards bad reads (B2). Connector also **blocks `Manifest.objects.create` when doc is NULL** (no blank rows) |
| Parity | usps vs usps_international | Every connector change lands in **both**, byte-identical (lockstep; shared-module refactor deferred) |

## Workstreams

### A — Connector (`modules/connectors/usps` + `usps_international`, byte-identical)

| Item | Change | Location | Pri |
|---|---|---|---|
| A1 | Unify boundary regex — `parse_response` uses `--[a-zA-Z0-9\-]+` (drops `+/=_`), `normalize_multipart_response` uses `--[a-zA-Z0-9\+/=_-]+`. Extract the boundary once / make identical. | `utils.py:114` (intl `:120`) | **P0** |
| A2 | 2xx multipart that yields no `SCANFormImage`/`label` and no message → emit `models.Message(code="manifest_parse_error")` and return `details=None` so the operator gets an error (and the server blocks creation), not a 201 with a NULL doc. | `manifest.py:13-23` (both) | **P0** |
| A3 | `%PDF`/`JVBER` sniff for `has_doc` — gate the doc on looking like base64-of-PDF, so a non-PDF part named `SCANFormImage` is not stored as a manifest doc. | `manifest.py:19` (both) | P1 |
| A4 | **mailingDate window validation → hard-fail.** When caller `shipment_date` is outside today..+7, raise a clear error (surfaced as 400). Do NOT auto-set `overwriteMailingDate`. (Decision locked.) | `manifest.py:68-72` (both) | P1 |
| A5 | Fix `parse_error_response` non-JSON branch — it calls `response.strip()`/`response.code` on the raw http error object → `AttributeError` on the exact malformed-error path it handles. Decode via the already-read `content`. | `utils.py:161-175` (intl `:167-181`) | P1 |
| A6 | Case/quoting hardening — case-insensitive `Content-Type`/`Content-Disposition`; relax `name=` regex's required leading space. Behavior-preserving for current fixtures. | `utils.py:82,87,139,145,149` | P2 |

### B — Server (`modules/manager/.../serializers/manifest.py`, `views/manifests.py`, `modules/core/.../serializers.py`)

| Item | Change | Location | Pri |
|---|---|---|---|
| B1 | Catch silent partial-drop — current `len(found)>len(req) or len(found)==0` cannot catch the realistic case (some ids dropped, ≥1 survives → silent 201 missing packages). Replace with missing-id diff (`set(shipment_ids) - {s.id…}`) → `ValidationError` listing exact missing ids → 400. | `serializers/manifest.py:30-40` | **P0** |
| B2 | **Blank-PDF download guard** — module-level `_decode_pdf(value)` (stdlib `base64`/`binascii`): None/empty/whitespace → missing; `b64decode` in try/except `binascii.Error`; assert `content[:5]==b'%PDF-'`. Use in BOTH download routes (file-stream `ManifestDoc.get()`/`get_file()` — decode once in `get()`, reuse — and JSON `ManifestDocumentDownload.post()`). On failure → clean **422** (not blank file, not 500). | `views/manifests.py:1-24` (helper), `:117-132`, `:152-172` | **P0** |
| B3 | **`carrier_id` selector** — add optional `carrier_id` to `ManifestData` (mirror `ShipmentCancelData` `serializers.py:1914-1916`); build `carrier_filter`, pass to `Connections.first` (mirror `PickupSerializer` `pickup.py:172,181-208`). Gateway resolves id-or-name (`gateway.py:105-119`) — no gateway change. Pop `carrier_id` before `ManifestRequest.map`. | `core/serializers.py:1930`, `serializers/manifest.py:18-22` | **P0** |
| B4 | Deterministic fallback ordering — until `carrier_id` supplied, `Connections.list` has no `.order_by()` → arbitrary account. Add stable `.order_by('-created')`. | gateway list path | P1 |
| B5 | Shipment-count cap — `MANIFEST_MAX_SHIPMENTS = {"usps":1000,"usps_international":1000}`; reject `>cap` with 400 BEFORE the DB query (saves an OAuth+424 round-trip). Per-connection config override. | `serializers/manifest.py:16-17` | P1 |
| B6 | US-origin address completeness — USPS carriers require `state_code` + 5-digit postal pre-flight (currently degrade to `state=None`/`ZIPCode=''` → late 424). 400 naming the missing field. Gate on USPS carrier set. | `serializers/manifest.py:14-23` | P1 |

### C — Dashboard (`packages/core/modules/Manifests/*`, `packages/ui/core/modals/create-manifest-modal.tsx`)

| Item | Change | Location | Pri |
|---|---|---|---|
| C0 | **Per-connection `manifest_required` opt-in (default OFF)** — add the connection-config flag; USPS shipment create sets `shipment.meta.manifest_required` from it. Default off → the automated Tongtool flow is unaffected; operator enables it on the connection used for manual dashboard manifesting. (Replaces the blunt "always set manifest_required" approach.) | `usps/.../shipment/create.py`, USPS connection config | **P0 (gates C)** |
| C1 | Success toast + `manifest_url` surfacing — `handleSubmit` only notifies on error + `setTimeout(close,1000)`. Capture `mutateAsync` return, fire `NotificationType.success`, render a "Print SCAN form" link before auto-close. | `create-manifest-modal.tsx:68-79` | P1 |
| C2 | Connection `<select>` in modal — populated from manifest-capable USPS connections, sends `carrier_id` (B3). Blocked on B3 + TS-type regen. | `create_manifests.tsx:135-150` + modal | P1 |
| C3 | create→print handoff — after success, redirect to Ready tab or render the print link inline. | modal success branch / `index.tsx` | P2 |

### D — Tests — **P0** (see Test Plan)

### E — Docs

| Item | Change | Pri |
|---|---|---|
| E1 | PR body (Fix-PR format per `git-workflow.md`): Bug / Root Cause / Fix / Tests. Note per-carrier cap assumption + 422 semantics + the `manifest_required` opt-in. | P1 |
| E2 | Regenerate TS types after the `carrier_id` schema change; note the schema addition. | P1 |
| E3 | Document the real USPS scan-form contract (multipart: `SCANFormMetaData` JSON part + `SCANFormImage` PDF part) in the connector + a reference note (it's undocumented upstream). | P1 |

## Test Plan

### Connector — `modules/connectors/usps/tests/usps/test_manifest.py` (mirror ALL in `usps_international`)

Keep the 5 existing green tests. **First make the hardcoded `2024-07-28` fixture date RELATIVE** (it's now past; A4's window validation will otherwise reject it and break `test_create_tracking_request`). Add:
- `test_parse_manifest_multipart_response_with_special_boundary` — boundary with `+/=_` → still parses (locks A1).
- `test_parse_manifest_unparseable_multipart` — undecodable 2xx body → `(None, [Message code=manifest_parse_error])`, not `(None, [])` (locks A2).
- `test_parse_manifest_empty_document_response` — empty `SCANFormImage` → details None + Message (A3).
- `test_parse_manifest_multipart_lowercase_headers` — `content-type:`/`;name=` no-space → still parse (A6).
- `test_create_manifest_request_rejects_out_of_window_date` — past / today+10 → ValidationError; today..+7 → ok (A4).
- **Replace the hand-built 12-char-PDF fixture with the captured real `scan-forms/v3` multipart** (`/tmp/real_scanform.txt`): real boundary, `application/pdf` part, PDF magic, `SCANFormMetaData` part — asserts manifestNumber + PDF.

### Server — `modules/manager/karrio/server/manager/tests/test_manifests.py`

**Repair first:** the existing `test_download_manifest_document` / `test_download_manifest_not_found` depend on the unstable single-call create-mock (`gateway.utils.identity` side_effect count no longer matches → empty body → `json.loads` fails). **Rebuild the shipment fixture directly in the DB** (`status=created`, `shipment_identifier`, `selected_rate`, `carrier=create_carrier_snapshot(...)`) — mirror `TestShipmentPurchase.setUp`. Add:
- `test_create_usps_scan_form_manifest` — USPS conn + purchased shipment, POST → 201 + manifestNumber.
- `test_download_usps_manifest_document` — base64 + `%PDF`.
- `test_download_manifest_pdf_file` — GET `manifest.pdf` (untested file-stream route): 200, `application/pdf`, body `b'%PDF'`.
- `test_download_manifest_document_invalid_pdf` — non-`%PDF` stored → **422** on both routes.
- `test_download_manifest_empty_document` — `manifest=''` → 422/404, not 0-byte PDF.
- `test_create_manifest_partial_drop_400` — valid + already-manifested ids → 400 listing missing ids (B1).
- `test_create_manifest_unknown_carrier_404`, `test_create_manifest_wrong_carrier_400`, `test_create_manifest_unpurchased_shipment_400`.
- `test_create_manifest_over_cap_400` — `>1000` ids → 400 (B5).
- `test_create_manifest_missing_state_400` — USPS address missing `state_code` → 400 (B6).
- `test_create_manifest_targets_specific_connection` — two USPS connections, POST with `carrier_id` of #2 → snapshot == requested; companion: omitting `carrier_id` preserves current behavior (B3).
- `test_create_manifest_cross_org_shipment_400`, `test_download_manifest_cross_org_404` — tenant isolation (`access_by`).

Keep Canada Post / Australia Post create flows green (regression on the optional `carrier_id` schema change).

**Run:** `python -m unittest discover -v -f modules/connectors/usps/tests` (+ `usps_international`); `./bin/run-server-tests` for the server tests (the `.venv/karrio` env lacks `karrio.server`; server tests run in the karrio container or via the bin script).

## Out of Scope (deliberate — small, reviewable diff)

- ebay_integration batch auto-generation (separate Frappe stack).
- GraphQL create-manifest mutation (REST-only).
- Non-USPS carriers' blank-PDF/validation hardening (`_decode_pdf` + partial-drop COULD generalize to help CP #757 — deferred; USPS-only here).
- `ShipmentDocs.get_file()` back-apply (same latent risk, out of USPS-manifest scope).
- Shared USPS multipart-parser module (de-dupe usps/usps_international) — lockstep now, refactor later.
- 20-records/page dashboard cap (benign for USPS — selection persists across pages).

## Risks

- **Fixture date rot (top test risk):** make the `2024-07-28` fixture relative BEFORE/with A4, or A4 breaks `test_create_tracking_request`. Non-negotiable.
- **TS-type regen ordering:** B3 (server schema) → regen TS types → C2 (dashboard selector). Strict sequence.
- **422 is a new code on this endpoint** — semantically correct; note for reviewer.
- **Parity debt:** every A-item in BOTH connectors (the `SCANFormMetaData` casing bug hit both).
- **`manifest_required` opt-in must default OFF** — otherwise the automated Tongtool flow gets marked incomplete-until-manifested. The per-connection config is the safety mechanism.

## Worktree

New git worktree off `production`, branch **`feat/usps-scanform-manifest`** (confirm name at writing-plans). Implementation via subagent-driven-development; commits gated on user permission; PR within the fork (no upstream PR unless requested).

## Verification Gates

1. Connector suites green: `usps` + `usps_international` unittest.
2. Server manifest tests green (`./bin/run-server-tests` or container `karrio test`).
3. Dashboard: manual verification of create→success-toast→print on a USPS connection with `manifest_required` enabled.
4. No regression to the live-verified end-to-end path (real manifest still 201 + valid %PDF).
