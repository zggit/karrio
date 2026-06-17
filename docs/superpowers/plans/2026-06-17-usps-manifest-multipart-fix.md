# USPS Manifest Multipart Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `POST /v1/manifests` (carriers `usps` and `usps_international`) succeed by decoding the USPS multipart SCAN Form success response instead of crashing on a JSON-only decoder.

**Architecture:** Mirror the already-working `create_shipment` path: swap the manifest proxy decoder from `lib.to_dict` to the multipart-aware `provider_utils.parse_response`, add `on_error=provider_utils.parse_error_response`, and rewrite the provider extractor to read the multipart part-name keys defensively (base64 PDF passed through untouched — it is already base64; the download path base64-decodes it). Same change applied to `usps_international`, which also still needs the `state_code` fix.

**Tech Stack:** Python, karrio SDK (`karrio.lib`), `unittest` (NEVER pytest), `karrio.sdk.Manifest`.

**Conventions (do not violate):**
- `import karrio.lib as lib` only — never `DP`/`SF`/`NF`.
- `mapper.py` and `karrio/schemas/**` are generated — do NOT edit.
- Tests: `unittest` only. Run from repo root after `source bin/activate-env`.
- Match existing connector style (`lib.identity`, `lib.failsafe`). Surgical edits only — touch nothing adjacent.
- **Commits are GATED**: do NOT `git commit` without explicit user permission (project rule). Implement + run tests; pause for permission before any commit/deploy step.

**Pre-flight (run once before Task 1):**
```bash
cd /Users/moon/Documents/OMS/karrio && source bin/activate-env
python -m unittest discover -v -f modules/connectors/usps/tests 2>&1 | tail -20
```
Expected: existing manifest tests PASS (baseline green). If the env/python is missing, STOP and report — do not guess.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `modules/connectors/usps/karrio/mappers/usps/proxy.py` | HTTP client; `create_manifest` decode | Modify `create_manifest` (~277-290) |
| `modules/connectors/usps/karrio/providers/usps/manifest.py` | request build + response parse | Modify `parse_manifest_response`, `_extract_details` (13-40), `manifest_request` return (90) |
| `modules/connectors/usps/tests/usps/test_manifest.py` | manifest tests | Add 2 tests + fixtures; keep existing test unchanged |
| `modules/connectors/usps_international/karrio/mappers/usps_international/proxy.py` | intl HTTP client | Modify `create_manifest` (271-284) |
| `modules/connectors/usps_international/karrio/providers/usps_international/manifest.py` | intl parse + request | Modify same 3 spots + `state` (80) → `state_code` |
| `modules/connectors/usps_international/tests/usps_international/test_manifest.py` | intl manifest tests | Add 2 tests + fixtures (mirror domestic) |

---

## Task 1: USPS domestic — multipart success + error parsing

**Files:**
- Modify: `modules/connectors/usps/karrio/mappers/usps/proxy.py` (`create_manifest`)
- Modify: `modules/connectors/usps/karrio/providers/usps/manifest.py` (`parse_manifest_response`, `_extract_details`, `manifest_request`)
- Test: `modules/connectors/usps/tests/usps/test_manifest.py`

- [ ] **Step 1: Add the two new failing tests + fixtures**

Append the two test methods inside `class TestUSPSManifest` (after `test_parse_manifest_response`), and append the new module-level fixtures at the bottom of the file. Do NOT modify the existing `test_parse_manifest_response`, `ManifestPayload`, `ManifestRequest`, `ParsedManifestResponse`, or `ManifestResponse`.

```python
    def test_parse_manifest_multipart_response(self):
        with patch("karrio.mappers.usps.proxy.lib.request") as mock:
            mock.return_value = MultipartManifestResponse
            parsed_response = karrio.Manifest.create(self.ManifestRequest).from_(gateway).parse()
            logger.debug(lib.to_dict(parsed_response))
            self.assertListEqual(lib.to_dict(parsed_response), ParsedMultipartManifestResponse)

    def test_parse_manifest_error_response(self):
        with patch("karrio.mappers.usps.proxy.lib.request") as mock:
            mock.return_value = ManifestErrorResponse
            parsed_response = karrio.Manifest.create(self.ManifestRequest).from_(gateway).parse()
            logger.debug(lib.to_dict(parsed_response))
            self.assertListEqual(lib.to_dict(parsed_response), ParsedManifestErrorResponse)
```

Fixtures appended at module bottom (the multipart body mirrors USPS's label multipart structure: a
JSON part named `SCANFormMetadata` and a base64-PDF part named `SCANFormImage` with a `filename=`;
boundary is plain alphanumeric so it matches `parse_response`'s `--[a-zA-Z0-9\-]+`):

```python
MultipartManifestResponse = (
    "--uspsboundary123\r\n"
    'Content-Type: application/json\r\n'
    'Content-Disposition: form-data; name="SCANFormMetadata"\r\n'
    "\r\n"
    '{"manifestNumber": "9234567890", "trackingNumbers": ["794947717776"]}\r\n'
    "--uspsboundary123\r\n"
    "Content-Type: application/pdf\r\n"
    'Content-Disposition: form-data; name="SCANFormImage"; filename="scanform.pdf"\r\n'
    "\r\n"
    "JVBERi0xLjQgU0NBTiBGb3Jt\r\n"
    "--uspsboundary123--\r\n"
)

ParsedMultipartManifestResponse = [
    {
        "carrier_id": "usps",
        "carrier_name": "usps",
        "doc": {"manifest": "JVBERi0xLjQgU0NBTiBGb3Jt"},
        "meta": {"manifestNumber": "9234567890", "trackingNumbers": ["794947717776"]},
    },
    [],
]

ManifestErrorResponse = """{
  "apiVersion": "/scan-forms/v3",
  "error": {
    "code": "400",
    "message": "Bad Request",
    "errors": [
      {"title": "Bad Request", "detail": "cannot be in the past or more than 7 days in the future", "code": "160001", "source": {"parameter": "mailingDate"}}
    ]
  }
}"""

ParsedManifestErrorResponse = [
    None,
    [
        {
            "carrier_id": "usps",
            "carrier_name": "usps",
            "code": "400",
            "message": "cannot be in the past or more than 7 days in the future",
            "details": {"parameter": "mailingDate"},
        }
    ],
]
```

> NOTE: `ParsedManifestErrorResponse` mirrors how `error.parse_error_response` shapes messages. If the
> live `error.py` formats `code`/`details` differently, adjust the EXPECTED to match the actual parser
> output (read `modules/connectors/usps/karrio/providers/usps/error.py` first) — do not change the parser
> to fit the test.

- [ ] **Step 2: Run the new tests — verify they FAIL**

```bash
cd /Users/moon/Documents/OMS/karrio && source bin/activate-env
python -m unittest discover -v -f modules/connectors/usps/tests 2>&1 | tail -30
```
Expected: `test_parse_manifest_multipart_response` FAILS (currently `lib.to_dict` crashes on multipart →
`SHIPPING_SDK_INTERNAL_ERROR`, or details is None). `test_parse_manifest_error_response` may pass or fail
depending on current shaping. Existing `test_parse_manifest_response` still PASSES.

- [ ] **Step 3: Apply the proxy decoder swap**

In `modules/connectors/usps/karrio/mappers/usps/proxy.py`, `create_manifest`, add `on_error` to the
`lib.request(...)` call and change the return:

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

(`provider_utils` is already imported in proxy.py.)

- [ ] **Step 4: Rewrite the provider extractor**

In `modules/connectors/usps/karrio/providers/usps/manifest.py`, replace `parse_manifest_response` and
`_extract_details` (lines 13-40) with:

```python
def parse_manifest_response(
    _response: lib.Deserializable[dict],
    settings: provider_utils.Settings,
) -> tuple[models.ManifestDetails, list[models.Message]]:
    response = _response.deserialize()

    messages = error.parse_error_response(response, settings)
    has_doc = (response.get("SCANFormImage") or response.get("label")) is not None
    details = _extract_details(response, settings, _response.ctx) if (has_doc and not any(messages)) else None

    return details, messages


def _extract_details(
    data: dict,
    settings: provider_utils.Settings,
    ctx: dict = None,
) -> models.ManifestDetails:
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

- [ ] **Step 5: Attach ctx in `manifest_request`**

In the same file, change the final return of `manifest_request` (line ~90):

```python
    return lib.Serializable(request, lib.to_dict, dict(shipment_identifiers=payload.shipment_identifiers))
```

- [ ] **Step 6: Run all USPS tests — verify PASS (incl. existing regression test)**

```bash
cd /Users/moon/Documents/OMS/karrio && source bin/activate-env
python -m unittest discover -v -f modules/connectors/usps/tests 2>&1 | tail -30
```
Expected: `test_parse_manifest_response` (legacy JSON) PASS, `test_parse_manifest_multipart_response`
PASS, `test_parse_manifest_error_response` PASS, all other USPS tests PASS. If the error test's expected
shape was wrong, fix the EXPECTED fixture (not the parser) and re-run.

- [ ] **Step 7: Commit — GATED (requires user permission)**

Do NOT run without explicit user "yes". When permitted:
```bash
git add modules/connectors/usps/karrio/mappers/usps/proxy.py \
        modules/connectors/usps/karrio/providers/usps/manifest.py \
        modules/connectors/usps/tests/usps/test_manifest.py
git commit -m "fix(usps): decode multipart SCAN Form manifest response"
```

---

## Task 2: USPS international — same fix + state_code

**Files:**
- Modify: `modules/connectors/usps_international/karrio/mappers/usps_international/proxy.py` (`create_manifest`, 271-284)
- Modify: `modules/connectors/usps_international/karrio/providers/usps_international/manifest.py` (same 3 spots + `state` 80)
- Test: `modules/connectors/usps_international/tests/usps_international/test_manifest.py`

- [ ] **Step 1: Inspect the intl test file + error parser**

```bash
sed -n '1,60p' modules/connectors/usps_international/tests/usps_international/test_manifest.py
sed -n '1,60p' modules/connectors/usps_international/karrio/providers/usps_international/error.py
```
Confirm the intl test mirrors the domestic structure (it does) and note the intl `carrier_name`/`carrier_id`
value to use in expected fixtures (likely `"usps_international"`).

- [ ] **Step 2: Add the two failing tests + fixtures (mirror Task 1)**

Add `test_parse_manifest_multipart_response` and `test_parse_manifest_error_response` methods to the intl
test class (patching `karrio.mappers.usps_international.proxy.lib.request`), and append the same
`MultipartManifestResponse` / `ParsedMultipartManifestResponse` / `ManifestErrorResponse` /
`ParsedManifestErrorResponse` fixtures — but set `carrier_id`/`carrier_name` to the intl gateway's value
(from Step 1) instead of `"usps"`. Use the SAME multipart body string as Task 1.

```python
    def test_parse_manifest_multipart_response(self):
        with patch("karrio.mappers.usps_international.proxy.lib.request") as mock:
            mock.return_value = MultipartManifestResponse
            parsed_response = karrio.Manifest.create(self.ManifestRequest).from_(gateway).parse()
            logger.debug(lib.to_dict(parsed_response))
            self.assertListEqual(lib.to_dict(parsed_response), ParsedMultipartManifestResponse)

    def test_parse_manifest_error_response(self):
        with patch("karrio.mappers.usps_international.proxy.lib.request") as mock:
            mock.return_value = ManifestErrorResponse
            parsed_response = karrio.Manifest.create(self.ManifestRequest).from_(gateway).parse()
            logger.debug(lib.to_dict(parsed_response))
            self.assertListEqual(lib.to_dict(parsed_response), ParsedManifestErrorResponse)
```

- [ ] **Step 3: Run the new intl tests — verify FAIL**

```bash
cd /Users/moon/Documents/OMS/karrio && source bin/activate-env
python -m unittest discover -v -f modules/connectors/usps_international/tests 2>&1 | tail -30
```
Expected: multipart test FAILS (same root cause). Existing intl manifest test still PASSES.

- [ ] **Step 4: Apply the intl proxy decoder swap**

In `modules/connectors/usps_international/karrio/mappers/usps_international/proxy.py`, `create_manifest`
(271-284): add `on_error=provider_utils.parse_error_response,` to the `lib.request(...)` call and change
the return to `return lib.Deserializable(response, provider_utils.parse_response, request.ctx)`.
Confirm `provider_utils` is imported at the top of the file; if it is imported under a different alias,
use that alias.

- [ ] **Step 5: Rewrite the intl provider extractor + ctx + state_code**

In `modules/connectors/usps_international/karrio/providers/usps_international/manifest.py`:
(a) Replace `parse_manifest_response` + `_extract_details` (13-40) with the SAME bodies as Task 1 Step 4.
(b) Change `manifest_request` return (line ~90) to
`return lib.Serializable(request, lib.to_dict, dict(shipment_identifiers=payload.shipment_identifiers))`.
(c) In `manifest_request`, change `state=address.state,` (line ~80) → `state=address.state_code,`.

- [ ] **Step 6: Run all intl tests — verify PASS**

```bash
cd /Users/moon/Documents/OMS/karrio && source bin/activate-env
python -m unittest discover -v -f modules/connectors/usps_international/tests 2>&1 | tail -30
```
Expected: all intl manifest tests PASS (legacy + multipart + error), no other intl test regressed.

- [ ] **Step 7: Full USPS-family regression**

```bash
cd /Users/moon/Documents/OMS/karrio && source bin/activate-env
python -m unittest discover -v -f modules/connectors/usps/tests 2>&1 | tail -5
python -m unittest discover -v -f modules/connectors/usps_international/tests 2>&1 | tail -5
```
Expected: both suites green.

- [ ] **Step 8: Commit — GATED (requires user permission)**

```bash
git add modules/connectors/usps_international/karrio/mappers/usps_international/proxy.py \
        modules/connectors/usps_international/karrio/providers/usps_international/manifest.py \
        modules/connectors/usps_international/tests/usps_international/test_manifest.py
git commit -m "fix(usps_international): decode multipart SCAN Form manifest response + state_code"
```

---

## Post-implementation (GATED — separate, after tests green + user permission)

1. **Deploy** the patched `proxy.py` + `manifest.py` (domestic + intl) via the karrio bind-mount patch
   mechanism (same as the deployed `state_code` fix). Snapshot patched files into the deploy bind-mount.
2. **Live verification (9am batch):** a real same-day label → manifest returns a downloadable PDF (no
   `SHIPPING_SDK_INTERNAL_ERROR`). Capture trace `Content-Type` + multipart part names; if names differ
   from `SCANFormMetadata`/`SCANFormImage`, the PDF is still captured — only update the `meta` mapping
   + fixtures, then re-run tests.

---

## Self-Review

**Spec coverage:** Edit 1 → Task1/Step3. Edit 2 → Task1/Step4. Edit 3 → Task1/Step5. Edits 4-6 →
Task2/Steps4-5. Edit 7 (state_code) → Task2/Step5c. Tests (keep legacy + add multipart + error, ×2) →
Task1/Step1, Task2/Step2. Deployment + 9am gate → Post-implementation. All spec sections mapped.

**Placeholder scan:** No TBD/TODO. Every code step shows full code. The one judgement call (error-message
expected shape) is explicitly flagged with the instruction to read `error.py` and match the parser output.

**Type consistency:** `parse_manifest_response(_response, settings)`, `_extract_details(data, settings, ctx=None)`,
`models.ManifestDetails`, `models.ManifestDocument(manifest=...)`, `lib.to_object(manifest.ScanFormResponseType, container)`
— consistent across both tasks. `request.ctx` populated by `manifest_request` (Step5) before being read in
`_extract_details` (Step4). Decoder signature `lib.Deserializable(value, decoder, ctx)` matches `create_shipment`.
