# USPS Manifest (SCAN Form) Multipart Response Fix — Design

**Date:** 2026-06-17
**Status:** Approved (codex-scored: robustness 6/10 with TOP RISK neutralized; base64 contract `pass-through-correct`)
**Scope:** `modules/connectors/usps` + `modules/connectors/usps_international`

---

## Goal

Make `POST /v1/manifests` (carrier `usps` / `usps_international`) succeed. Today it crashes with
`SHIPPING_SDK_INTERNAL_ERROR: "Expecting value: line 1 column 1 (char 0)"` on the **success** path.

## Root Cause (already diagnosed — 3 codex agents 8/8/9 + USPS official examples + live calls)

USPS `scan-forms/v3/scan-form` returns the SCAN Form as **`multipart/*` on success** (a JSON metadata
part + a base64-PDF part). The proxy decodes with `lib.Deserializable(response, lib.to_dict)` (JSON-only),
so `json.loads("--boundary…")` fails at char 0. USPS **error** responses are JSON, so only the success
path breaks. Full diagnosis: memory `project-karrio-manifest-multipart-bug.md`.

## Data Contract (codex-verified — the part that de-risks the fix)

| Fact | Evidence |
|---|---|
| `ManifestDocument.manifest` is **base64-decoded at download** | `manager/views/manifests.py:128` (`base64.b64decode`) |
| `Documents.label` uses the **same** base64-decode contract | `manager/views/shipments.py` (same pattern) |
| USPS multipart image parts arrive **already base64** (`Content-Transfer-Encoding: base64`, body `JVBER…`) | label fixture `usps/tests/usps/test_shipment.py:~330`; manifest fixture `test_manifest.py:119` |
| ⟹ **Pass `pdf` straight into `ManifestDocument(manifest=pdf)`** — re-encoding would double-encode → corrupt | codex verdict `pass-through-correct` |

This neutralizes the robustness reviewer's TOP RISK: the part is base64 **text**, so
`normalize_multipart_response`'s `\r\n`→`\n` + `.strip()` cannot corrupt the PDF (base64 decoders ignore
whitespace). This is exactly why the **label** path already works in production.

## Existing Code Analysis

| File | What it does today | Reuse / Change |
|---|---|---|
| `usps/mappers/usps/proxy.py:277-290` `create_manifest` | `lib.Deserializable(response, lib.to_dict)`, no `on_error` | **CHANGE** — mirror `create_shipment` (156-179) |
| `usps/providers/usps/utils.py:104-158` `parse_response` | multipart-aware; JSON-first then boundary parse | **REUSE as-is** (already the helper) |
| `usps/providers/usps/utils.py:161-175` `parse_error_response` | content-type-aware error decoder | **REUSE as-is** |
| `usps/providers/usps/manifest.py:13-40` | reads `SCANFormImage` only; `carrier_name=carrier_id` bug; no `failsafe`/ctx | **CHANGE** — defensive extraction |
| `usps/providers/usps/manifest.py:90` `manifest_request` | `lib.Serializable(request, lib.to_dict)` — **no ctx** | **CHANGE** — attach ctx |
| `usps/tests/usps/test_manifest.py` | mocks JSON-string `SCANFormImage` success | **KEEP green** + ADD multipart + error tests |

`usps_international` mirrors all of the above **and still has the un-fixed `state=address.state` bug**
(`manifest.py:80`) that domestic already fixed → international needs **3** edits, not 2.

## Data Flow (after fix)

```
                          USPS scan-forms/v3/scan-form
                                     │
                   ┌─────────────────┴──────────────────┐
              SUCCESS (200)                          ERROR (4xx)
              multipart/*                            application/json
                   │                                     │
       lib.request (decode_bytes → str)        lib.request on_error →
                   │                            provider_utils.parse_error_response
                   │                                     │  (dict)
                   └──────────────┬──────────────────────┘
                                  ▼
                   provider_utils.parse_response          ◄── decoder (was lib.to_dict)
                   ├─ JSON?  → to_dict  (error dict, legacy JSON success)
                   └─ multipart? → {partName: jsonObj|pdfStr}
                                  ▼
                   parse_manifest_response / _extract_details
                   ├─ pdf  = data["SCANFormImage"] | data["label"]      → ManifestDocument(manifest=pdf)  (base64 pass-through)
                   ├─ meta = to_object(ScanFormResponseType, container).SCANFormMetadata  (failsafe)
                   └─ trackingNumbers fallback → ctx["shipment_identifiers"]
```

The body shape varies by how USPS names the multipart parts; `_extract_details` handles **all three**:

| Shape | `data` keys | Source |
|---|---|---|
| Parts named after schema fields (likely — matches label endpoint) | `SCANFormMetadata`, `SCANFormImage` | live |
| Parts named `"Scan Form Response"` / `"label"` (alt USPS doc wording) | `Scan Form Response`, `label` | live |
| Legacy single JSON object | `SCANFormMetadata`, `SCANFormImage` | existing test |

## The Fix — Exact Edits

### USPS (domestic)

**Edit 1 — `usps/mappers/usps/proxy.py` `create_manifest`**
```python
        response = lib.request(
            url=f"{self.settings.server_url}/scan-forms/v3/scan-form",
            data=lib.to_json(request.serialize()),
            trace=self.trace_as("json"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {access_token}",
            },
            on_error=provider_utils.parse_error_response,
        )

        return lib.Deserializable(response, provider_utils.parse_response, request.ctx)
```

**Edit 2 — `usps/providers/usps/manifest.py` `parse_manifest_response` + `_extract_details`**
```python
def parse_manifest_response(_response, settings):
    response = _response.deserialize()
    messages = error.parse_error_response(response, settings)
    has_doc = (response.get("SCANFormImage") or response.get("label")) is not None
    details = _extract_details(response, settings, _response.ctx) if (has_doc and not any(messages)) else None
    return details, messages


def _extract_details(data, settings, ctx=None):
    container = data.get("Scan Form Response") or data
    pdf = data.get("SCANFormImage") or data.get("label")
    details = lib.to_object(manifest.ScanFormResponseType, container)
    return models.ManifestDetails(
        carrier_id=settings.carrier_id,
        carrier_name=settings.carrier_name,
        doc=models.ManifestDocument(manifest=pdf),
        meta=dict(
            manifestNumber=lib.failsafe(lambda: details.SCANFormMetadata.manifestNumber),
            trackingNumbers=lib.failsafe(lambda: details.SCANFormMetadata.trackingNumbers)
            or (ctx or {}).get("shipment_identifiers"),
        ),
    )
```

**Edit 3 — `usps/providers/usps/manifest.py:90` `manifest_request` return**
```python
    return lib.Serializable(request, lib.to_dict, dict(shipment_identifiers=payload.shipment_identifiers))
```

### USPS International — same 3 edits, plus the state fix

**Edits 4-6 — `usps_international/.../proxy.py` `create_manifest`, `usps_international/.../manifest.py`
`parse_manifest_response`/`_extract_details` + `manifest_request` ctx** — identical to Edits 1-3.

**Edit 7 — `usps_international/.../manifest.py:80`** `state=address.state` → `state=address.state_code`.

## Testing Strategy (unittest — `karrio test`/`python -m unittest`, never pytest)

| Test | Purpose | Notes |
|---|---|---|
| `test_parse_manifest_response` (existing JSON) | regression — proves decoder swap keeps legacy green | UNCHANGED; passes because `parse_response` is JSON-first |
| `test_parse_manifest_multipart_response` (NEW) | success path — multipart body → `ManifestDetails` with base64 doc + meta | fixture mirrors label multipart (`test_shipment.py`); part names provisional, **live-verified at gate** |
| `test_parse_manifest_error_response` (NEW) | error path — JSON 400 `mailingDate` → `Message`, `details=None` | confirms no error-path regression |
| Same 3 for `usps_international` | parity | international has separate provider/proxy files |

**Honest limit (per "Think Before Coding"):** the NEW multipart fixture bakes in *unverified* part names.
It guards the parse machinery but is **not** proof of the live contract. The 9am batch is the real gate;
if part names differ, only the `meta` mapping changes (the defensive code still captures the PDF).

## Deployment

Bind-mount patch (official image + `patches/usps/*.py`), same mechanism as the deployed `state_code` fix.
Snapshot the patched files into the deploy bind-mount and the fork commit. Instantly revertible.

## Verification Gate (before claiming "done")

1. All connector tests green: `python -m unittest discover -v -f modules/connectors/usps/tests` (+ international).
2. **Live (9am batch):** a real same-day label → manifest returns a downloadable PDF (no `SHIPPING_SDK_INTERNAL_ERROR`);
   capture the trace `Content-Type` + part names to confirm/repair the `meta` mapping.

## Out of Scope (noted, not done — Surgical Changes)

- No shared `lib`-level multipart helper (FedEx generality) — `parse_response` already serves; lift later.
- No fix for upstream serializer partial-invalid-ID silent-proceed (`serializers/manifest.py:31-41`).
- No change to `Accept` header / `imageType`.
