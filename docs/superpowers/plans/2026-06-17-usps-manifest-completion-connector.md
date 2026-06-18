Confirmed. The current `parse_response` regex `--[a-zA-Z0-9\-]+` drops the trailing `+` of the real boundary — that's exactly the A1 bug (it would mis-split the real multipart). The unified regex fixes it. PDF sniff via `JVBER` prefix or decoded `%PDF-` both work.

Now I have everything needed. Let me write the full TDD task list. I'll structure it so the connector tests drive each connector change, both modules byte-identical, with the fixture-date-relative task first (P0, non-negotiable per the spec's top risk).

A final detail to confirm: how the parsed response renders `level`/`details` in `lib.to_dict` (so the test's expected Message dict matches). Looking at the existing `ParsedManifestErrorResponse`, the Message renders as `{carrier_id, carrier_name, code, message, details}` — `level` is dropped because it's `None` (lib.to_dict strips None). For A2's `manifest_parse_error` Message, I'll set only `carrier_id`, `carrier_name`, `code`, `message` so the rendered dict is predictable. Let me verify `lib.to_dict` strips None and empty by checking one expected dict — the error fixture's Message has no `level` key, confirming None is stripped.

I have all the facts. Here is the plan.

---

# Workstream A — Connector (USPS + USPS International, byte-identical) — TDD Tasks

**Scope:** Items A1–A6 from the spec, landed in BOTH `modules/connectors/usps` and `modules/connectors/usps_international`, byte-identical (only module name / `carrier_name` differ). Plus the connector test set from the Test Plan.

**Shared conventions for every task below:**
- `import karrio.lib as lib`; functional style; `unittest` only (never pytest).
- Every change to a `providers/usps/*.py` file is mirrored into `providers/usps_international/*.py` in the **same step** — the only differences permitted are the literal substrings `usps_international` vs `usps` in import paths and `carrier_name`. The `usps` and `usps_international` `utils.py`/`manifest.py` bodies for the functions touched here are currently identical (verified), so the edits are literal.
- Run commands assume repo root `/Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest` with `source bin/activate-env` already done.
- Connector test runner: `python -m unittest discover -v -f modules/connectors/usps/tests` and `... usps_international/tests`.
- Commit lines are provided; **do not commit without user permission** — they are the intended atomic boundaries.

**Cross-workstream dependencies:**
- These tasks have **no dependency** on workstream B/C tasks.
- Workstream B's `test_create_usps_scan_form_manifest` (server) depends on A2/A3/A4 being landed (it relies on the connector returning `details=None` + a Message so the server blocks creation). Note that downstream, but do not block on it here.
- Workstream B's `_decode_pdf` 422 guard (B2) is the server-side complement to A3; independent code, no shared file.

---

### Task A0: Make the hardcoded `2024-07-28` fixture date RELATIVE — P0 (BLOCKS A4; spec's #1 risk)

This MUST land before A4, or A4's window validation rejects the static past date `2024-07-28` and breaks `test_create_tracking_request` (and `test_create_manifest`, `test_parse_*` which all build from `ManifestPayload`). We replace the static date with a date computed at module import inside today..+7, and assert the request echoes that same computed date.

**Files**
- Modify: `modules/connectors/usps/tests/usps/test_manifest.py` (`ManifestPayload` line 70; `ManifestRequest` `mailingDate` line 100)
- Modify: `modules/connectors/usps_international/tests/usps_international/test_manifest.py` (same lines 70, 100)
- Test paths: the two files above (they are the tests)

- [ ] **Step 1: Write the failing change — relative fixture date.**
  Add a module-level computed date and reference it in both fixtures. In `modules/connectors/usps/tests/usps/test_manifest.py`, add near the other top-of-module imports (after line 9 `from .fixture import gateway`):

  ```python
  import datetime

  # Manifest mailingDate must be within USPS's today..+7 window (A4). Use a
  # deterministic in-window date (today + 2) computed at import so the fixture
  # never rots into the past and never trips the window validation.
  MAILING_DATE = (datetime.date.today() + datetime.timedelta(days=2)).strftime("%Y-%m-%d")
  ```

  Change `ManifestPayload`'s options (line 70) from:

  ```python
      "options": {"shipment_date": "2024-07-28"},
  ```
  to:
  ```python
      "options": {"shipment_date": MAILING_DATE},
  ```

  Change `ManifestRequest`'s `mailingDate` (line 100) from:

  ```python
      "mailingDate": "2024-07-28",
  ```
  to:
  ```python
      "mailingDate": MAILING_DATE,
  ```

  Apply the **identical** change to `modules/connectors/usps_international/tests/usps_international/test_manifest.py`.

- [ ] **Step 2: Run — expect PASS (this is a pure refactor of the fixture; existing impl already echoes the input date).**
  ```
  python -m unittest discover -v -f modules/connectors/usps/tests 2>&1 | tail -20
  python -m unittest discover -v -f modules/connectors/usps_international/tests 2>&1 | tail -20
  ```
  Expected: all 5 existing tests in each module pass (`test_create_tracking_request`, `test_create_manifest`, `test_parse_manifest_response`, `test_parse_manifest_multipart_response`, `test_parse_manifest_error_response`). This step is intentionally a green-stays-green refactor — it is the prerequisite mechanical change, not a red/green pair. Confirm by eye that `mailingDate` in the asserted `ManifestRequest` now equals `today+2` (proving the relative wiring works end-to-end through `lib.fdate`).

- [ ] **Step 3: Commit.**
  ```
  test(usps): make manifest fixture mailingDate relative (today+2) for both connectors
  ```

---

### Task A1: Unify the multipart boundary regex (drops `+/=_` bug) — P0

`utils.parse_response` (line 114) uses `--[a-zA-Z0-9\-]+` which silently truncates the real USPS boundary `--okuYKZJGhVgsoUrYz1NtyzN+` at the `+`, mis-splitting the body. `normalize_multipart_response` (line 57) already uses the correct `--[a-zA-Z0-9\+/=_-]+`. Unify `parse_response` onto the same character class.

**Files**
- Modify: `modules/connectors/usps/karrio/providers/usps/utils.py` (line 114)
- Modify: `modules/connectors/usps_international/karrio/providers/usps_international/utils.py` (line 120)
- Test paths: `modules/connectors/usps/tests/usps/test_manifest.py`, `modules/connectors/usps_international/tests/usps_international/test_manifest.py`

- [ ] **Step 1: Write the failing test (special-boundary).**
  Append to `modules/connectors/usps/tests/usps/test_manifest.py` — a new test method inside `TestUSPSManifest` (place after `test_parse_manifest_error_response`, before the `if __name__` guard), plus two module-level fixtures at the bottom:

  In the class:
  ```python
      def test_parse_manifest_multipart_response_with_special_boundary(self):
          with patch("karrio.mappers.usps.proxy.lib.request") as mock:
              mock.return_value = SpecialBoundaryMultipartResponse
              parsed_response = karrio.Manifest.create(self.ManifestRequest).from_(gateway).parse()
              logger.debug(lib.to_dict(parsed_response))
              self.assertListEqual(lib.to_dict(parsed_response), ParsedSpecialBoundaryMultipartResponse)
  ```

  At module bottom:
  ```python
  # Boundary containing +/=_ (real USPS scan-forms/v3 boundaries do, e.g. "...NtyzN+").
  # The old parse_response regex --[a-zA-Z0-9\-]+ truncated it at the first + and mis-split.
  SpecialBoundaryMultipartResponse = (
      "--okuYKZJGhVgsoUrYz1NtyzN+\r\n"
      "Content-Type: application/json\r\n"
      'Content-Disposition: form-data; name="SCANFormMetaData"\r\n'
      "\r\n"
      '{"manifestNumber": "9234567890", "trackingNumbers": ["794947717776"]}\r\n'
      "--okuYKZJGhVgsoUrYz1NtyzN+\r\n"
      "Content-Type: application/pdf\r\n"
      'Content-Disposition: form-data; filename="SCANFormImage.pdf"; name="SCANFormImage"\r\n'
      "\r\n"
      "JVBERi0xLjQgU0NBTiBGb3Jt\r\n"
      "--okuYKZJGhVgsoUrYz1NtyzN+--\r\n"
  )

  ParsedSpecialBoundaryMultipartResponse = [
      {
          "carrier_id": "usps",
          "carrier_name": "usps",
          "doc": {"manifest": "JVBERi0xLjQgU0NBTiBGb3Jt"},
          "meta": {"manifestNumber": "9234567890", "trackingNumbers": ["794947717776"]},
      },
      [],
  ]
  ```

  Mirror into `usps_international`'s test file with `"usps"` → `"usps_international"` in the two `carrier_*` values and `karrio.mappers.usps.proxy` → `karrio.mappers.usps_international.proxy`.

- [ ] **Step 2: Run — expect FAIL.**
  ```
  python -m unittest discover -v -f modules/connectors/usps/tests -k test_parse_manifest_multipart_response_with_special_boundary 2>&1 | tail -25
  ```
  Expected: FAIL — the `+`-truncated boundary mis-splits, so `doc`/`meta` come back empty (assertion mismatch: `details` is `None` or fields missing).

- [ ] **Step 3: Implement — unify the regex.**
  In `modules/connectors/usps/karrio/providers/usps/utils.py`, change line 114 from:
  ```python
      boundary_match = re.search(r"--[a-zA-Z0-9\-]+", normalized_response)
  ```
  to:
  ```python
      boundary_match = re.search(r"--[a-zA-Z0-9\+/=_-]+", normalized_response)
  ```
  Apply the identical edit in `modules/connectors/usps_international/karrio/providers/usps_international/utils.py` (its line 120).

- [ ] **Step 4: Run — expect PASS (and no regression).**
  ```
  python -m unittest discover -v -f modules/connectors/usps/tests 2>&1 | tail -20
  python -m unittest discover -v -f modules/connectors/usps_international/tests 2>&1 | tail -20
  ```
  Expected: the new test plus all prior tests pass in both modules.

- [ ] **Step 5: Commit.**
  ```
  fix(usps): unify multipart boundary regex to keep +/=_ chars in scan-form parse
  ```

---

### Task A2: Emit `manifest_parse_error` Message (with `details=None`) on unparseable 2xx multipart — P0

Today, a 2xx body that yields no `SCANFormImage`/`label` and no carrier error returns `(None, [])` — the server then has no Message to block on and could create a NULL-doc manifest. We emit a `models.Message(code="manifest_parse_error")` and keep `details=None`, so the operator sees an error and the server blocks creation.

**Files**
- Modify: `modules/connectors/usps/karrio/providers/usps/manifest.py` (lines 12–22)
- Modify: `modules/connectors/usps_international/karrio/providers/usps_international/manifest.py` (lines 12–22)
- Test paths: the two `test_manifest.py` files

- [ ] **Step 1: Write the failing test (unparseable multipart).**
  In `modules/connectors/usps/tests/usps/test_manifest.py`, add to the class:
  ```python
      def test_parse_manifest_unparseable_multipart(self):
          with patch("karrio.mappers.usps.proxy.lib.request") as mock:
              mock.return_value = UnparseableMultipartResponse
              parsed_response = karrio.Manifest.create(self.ManifestRequest).from_(gateway).parse()
              logger.debug(lib.to_dict(parsed_response))
              self.assertListEqual(lib.to_dict(parsed_response), ParsedUnparseableMultipartResponse)
  ```
  And at module bottom:
  ```python
  # A 2xx body that is neither JSON nor a recognizable multipart with a doc part:
  # no SCANFormImage/label, no carrier error. Must NOT silently succeed with a NULL doc.
  UnparseableMultipartResponse = "this is not a parseable multipart or json body"

  ParsedUnparseableMultipartResponse = [
      None,
      [
          {
              "carrier_id": "usps",
              "carrier_name": "usps",
              "code": "manifest_parse_error",
              "message": "Unable to parse USPS scan-form manifest response.",
          }
      ],
  ]
  ```
  Mirror into `usps_international` (carrier values + proxy path).

  Note for the implementer: a non-multipart string flows through `parse_response` → boundary not found → returns `{"error": {"code": "SHIPPING_SDK_ERROR", "message": "Failed to parse multipart response"}}`, which `error.parse_error_response` already turns into a Message — so *this specific* string would already produce a message. To exercise the genuine "2xx, looks like multipart, but no doc part and no error" gap, use the empty-doc case in A3 instead for the strict gap. Keep this test focused on the contract: **any 2xx that yields no doc must produce at least one Message and `details=None`.** The assertion above is written against the new explicit `manifest_parse_error` path; see Step 3 for why the SDK-error string and the no-doc string both converge on a non-empty message list. If the SDK-error message differs, assert instead with the relaxed form below.

  Relaxed assertion variant (use this if the deserializer emits the SDK-error Message for the raw string — it still locks "details is None and at least one message"):
  ```python
      def test_parse_manifest_unparseable_multipart(self):
          with patch("karrio.mappers.usps.proxy.lib.request") as mock:
              mock.return_value = UnparseableMultipartResponse
              details, messages = karrio.Manifest.create(self.ManifestRequest).from_(gateway).parse()
              self.assertIsNone(details)
              self.assertTrue(len(messages) >= 1)
  ```

- [ ] **Step 2: Run — expect FAIL.**
  ```
  python -m unittest discover -v -f modules/connectors/usps/tests -k test_parse_manifest_unparseable_multipart 2>&1 | tail -25
  ```
  Expected: FAIL on the empty-doc path that A3 introduces; for the raw-string path the relaxed variant passes only once A2's explicit Message exists for the genuine no-doc/no-error case. (The strict assertion fails because no `manifest_parse_error` Message is emitted yet.)

- [ ] **Step 3: Implement A2 in `manifest.py`.**
  Replace lines 12–22 of `modules/connectors/usps/karrio/providers/usps/manifest.py`:
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
  ```
  with:
  ```python
  def parse_manifest_response(
      _response: lib.Deserializable[dict],
      settings: provider_utils.Settings,
  ) -> tuple[models.ManifestDetails, list[models.Message]]:
      response = _response.deserialize()

      messages = error.parse_error_response(response, settings)
      has_doc = _has_valid_doc(response)
      details = _extract_details(response, settings, _response.ctx) if (has_doc and not any(messages)) else None

      # 2xx multipart that yields no doc part and no carrier error must not look
      # like a success: emit an explicit error so the operator is informed and the
      # server blocks creating a NULL-document manifest row.
      if details is None and not any(messages):
          messages = [
              models.Message(
                  carrier_id=settings.carrier_id,
                  carrier_name=settings.carrier_name,
                  code="manifest_parse_error",
                  message="Unable to parse USPS scan-form manifest response.",
              )
          ]

      return details, messages
  ```
  (`_has_valid_doc` is added in A3 below. For this task land it as a temporary helper that preserves current behavior; A3 hardens it. To keep A2 self-contained and green on its own, add this helper immediately above `parse_manifest_response`:)
  ```python
  def _has_valid_doc(response: dict) -> bool:
      return (response.get("SCANFormImage") or response.get("label")) is not None
  ```
  Apply the identical edits to `modules/connectors/usps_international/karrio/providers/usps_international/manifest.py`.

- [ ] **Step 4: Run — expect PASS.**
  ```
  python -m unittest discover -v -f modules/connectors/usps/tests 2>&1 | tail -20
  python -m unittest discover -v -f modules/connectors/usps_international/tests 2>&1 | tail -20
  ```
  Expected: new test + all prior pass in both modules. Verify the existing `test_parse_manifest_multipart_response` (real doc present) still returns `details != None` and `[]` messages (no spurious `manifest_parse_error`).

- [ ] **Step 5: Commit.**
  ```
  fix(usps): emit manifest_parse_error with no details when scan-form 2xx has no doc
  ```

---

### Task A3: `%PDF`/`JVBER` sniff for `has_doc` (reject non-PDF parts) — P1

A part literally named `SCANFormImage` but whose content is not base64-of-`%PDF` must not be stored as a manifest doc. Gate `_has_valid_doc` on the content sniffing to `JVBER` (base64 of `%PDF`) or decoding to bytes starting with `%PDF-`. This also makes the A2 empty-doc/garbage-doc case fall into the `manifest_parse_error` branch.

**Files**
- Modify: `modules/connectors/usps/karrio/providers/usps/manifest.py` (`_has_valid_doc` helper from A2)
- Modify: `modules/connectors/usps_international/karrio/providers/usps_international/manifest.py` (same)
- Test paths: the two `test_manifest.py` files

- [ ] **Step 1: Write the failing test (empty-doc + non-PDF-doc).**
  In `modules/connectors/usps/tests/usps/test_manifest.py`, add to the class:
  ```python
      def test_parse_manifest_empty_document_response(self):
          with patch("karrio.mappers.usps.proxy.lib.request") as mock:
              mock.return_value = EmptyDocMultipartResponse
              details, messages = karrio.Manifest.create(self.ManifestRequest).from_(gateway).parse()
              logger.debug(lib.to_dict([details, messages]))
              self.assertIsNone(details)
              self.assertEqual(messages[0].code, "manifest_parse_error")

      def test_parse_manifest_non_pdf_document_response(self):
          with patch("karrio.mappers.usps.proxy.lib.request") as mock:
              mock.return_value = NonPdfDocMultipartResponse
              details, messages = karrio.Manifest.create(self.ManifestRequest).from_(gateway).parse()
              logger.debug(lib.to_dict([details, messages]))
              self.assertIsNone(details)
              self.assertEqual(messages[0].code, "manifest_parse_error")
  ```
  At module bottom:
  ```python
  # SCANFormImage part present but empty -> not a valid doc.
  EmptyDocMultipartResponse = (
      "--uspsboundary123\r\n"
      "Content-Type: application/json\r\n"
      'Content-Disposition: form-data; name="SCANFormMetaData"\r\n'
      "\r\n"
      '{"manifestNumber": "9234567890", "trackingNumbers": ["794947717776"]}\r\n'
      "--uspsboundary123\r\n"
      "Content-Type: application/pdf\r\n"
      'Content-Disposition: form-data; filename="SCANFormImage.pdf"; name="SCANFormImage"\r\n'
      "\r\n"
      "\r\n"
      "--uspsboundary123--\r\n"
  )

  # SCANFormImage part present but content is not base64-of-%PDF -> not a valid doc.
  NonPdfDocMultipartResponse = (
      "--uspsboundary123\r\n"
      "Content-Type: application/json\r\n"
      'Content-Disposition: form-data; name="SCANFormMetaData"\r\n'
      "\r\n"
      '{"manifestNumber": "9234567890", "trackingNumbers": ["794947717776"]}\r\n'
      "--uspsboundary123\r\n"
      "Content-Type: application/pdf\r\n"
      'Content-Disposition: form-data; filename="SCANFormImage.pdf"; name="SCANFormImage"\r\n'
      "\r\n"
      "bm90LWEtcGRm\r\n"  # base64("not-a-pdf")
      "--uspsboundary123--\r\n"
  )
  ```
  Mirror into `usps_international`.

- [ ] **Step 2: Run — expect FAIL.**
  ```
  python -m unittest discover -v -f modules/connectors/usps/tests -k "empty_document or non_pdf_document" 2>&1 | tail -30
  ```
  Expected: FAIL — current `_has_valid_doc` only checks truthiness/presence; the empty part is falsy (so empty-doc may already pass), but the `bm90LWEtcGRm` non-PDF part is truthy → currently stored as doc → `details != None` (assertion fails). Confirm at least `test_parse_manifest_non_pdf_document_response` is red.

- [ ] **Step 3: Implement the sniff in `_has_valid_doc`.**
  Add `import binascii` and `import base64` to the top of `manifest.py` (after `import time`). Replace the temporary `_has_valid_doc` from A2 in `modules/connectors/usps/karrio/providers/usps/manifest.py` with:
  ```python
  def _looks_like_pdf(value: str) -> bool:
      """True when value is base64 that decodes to a %PDF- document."""
      if not value or not str(value).strip():
          return False

      candidate = str(value).strip()
      # base64 of "%PDF-" starts with "JVBER"; cheap prefix check before decoding.
      if candidate.startswith("JVBER"):
          return True

      try:
          decoded = base64.b64decode(candidate, validate=True)
      except (binascii.Error, ValueError):
          return False

      return decoded[:5] == b"%PDF-"


  def _has_valid_doc(response: dict) -> bool:
      return _looks_like_pdf(response.get("SCANFormImage") or response.get("label"))
  ```
  Apply the identical edits to `usps_international`.

- [ ] **Step 4: Run — expect PASS.**
  ```
  python -m unittest discover -v -f modules/connectors/usps/tests 2>&1 | tail -20
  python -m unittest discover -v -f modules/connectors/usps_international/tests 2>&1 | tail -20
  ```
  Expected: empty-doc and non-PDF-doc tests pass; the real-PDF tests (`test_parse_manifest_response` with `JVBER...`, `test_parse_manifest_multipart_response` with `JVBERi0xLjQgU0NBTiBGb3Jt`, and A1's special-boundary) still return `details != None`. Verify `JVBERi0xLjQgU0NBTiBGb3Jt` decodes to `%PDF-1.4 SCAN Form` (starts `JVBER` → fast-path true).

- [ ] **Step 5: Commit.**
  ```
  fix(usps): sniff %PDF/JVBER for scan-form doc so non-PDF parts are not stored
  ```

---

### Task A4: mailingDate today..+7 window — HARD-FAIL (raise → 400, no auto-overwrite) — P1

**Depends on A0** (relative fixture date) — without it, the existing static-date tests would already break under this validation. When `shipment_date` is outside `today..today+7`, raise a clear error from `manifest_request`. Decision locked: do **NOT** auto-set `overwriteMailingDate`. The raise occurs in `gateway.mapper.create_manifest_request(...)` (interface.py:598, outside `@fail_safe`), so it propagates and the server surfaces it as a 400.

**Files**
- Modify: `modules/connectors/usps/karrio/providers/usps/manifest.py` (`manifest_request`, around lines 67–71 where `mailingDate` is computed)
- Modify: `modules/connectors/usps_international/karrio/providers/usps_international/manifest.py` (same)
- Test paths: the two `test_manifest.py` files

- [ ] **Step 1: Write the failing test (reject out-of-window, accept in-window).**
  In `modules/connectors/usps/tests/usps/test_manifest.py`, add to the class:
  ```python
      def test_create_manifest_request_rejects_out_of_window_date(self):
          past = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
          too_far = (datetime.date.today() + datetime.timedelta(days=10)).strftime("%Y-%m-%d")

          for bad_date in (past, too_far):
              payload = models.ManifestRequest(
                  **{**ManifestPayload, "options": {"shipment_date": bad_date}}
              )
              with self.assertRaises(ValueError):
                  gateway.mapper.create_manifest_request(payload)

      def test_create_manifest_request_accepts_in_window_date(self):
          today = datetime.date.today().strftime("%Y-%m-%d")
          plus7 = (datetime.date.today() + datetime.timedelta(days=7)).strftime("%Y-%m-%d")

          for ok_date in (today, plus7):
              payload = models.ManifestRequest(
                  **{**ManifestPayload, "options": {"shipment_date": ok_date}}
              )
              request = gateway.mapper.create_manifest_request(payload)
              self.assertEqual(request.serialize()["mailingDate"], ok_date)
  ```
  Mirror into `usps_international` (only the proxy/import differences — these tests use `gateway` + `models`, so the bodies are byte-identical).

- [ ] **Step 2: Run — expect FAIL.**
  ```
  python -m unittest discover -v -f modules/connectors/usps/tests -k "out_of_window or in_window" 2>&1 | tail -25
  ```
  Expected: `test_create_manifest_request_rejects_out_of_window_date` FAILS (no `ValueError` raised — bad dates currently pass straight through). The accept-test passes already.

- [ ] **Step 3: Implement the window validation in `manifest_request`.**
  In `modules/connectors/usps/karrio/providers/usps/manifest.py`, replace the `mailingDate` computation. Current lines 67–71 inside the `request = usps.ScanFormRequestType(` block read:
  ```python
      request = usps.ScanFormRequestType(
          form="5630",
          imageType="PDF",
          labelType="8.5x11LABEL",
          mailingDate=lib.fdate(options.shipment_date.state or time.strftime("%Y-%m-%d")),
  ```
  Change to: compute and validate the date *before* building the request, then reference it. Insert immediately after the `options = lib.units.Options(...)` block closes (after line 64, before the `# map data ...` comment on line 66):
  ```python
      mailing_date = _validate_mailing_date(
          options.shipment_date.state or time.strftime("%Y-%m-%d")
      )

  ```
  and change the `mailingDate=` line to:
  ```python
          mailingDate=mailing_date,
  ```
  Then add the helper above `manifest_request` (below the `_extract_details` function):
  ```python
  def _validate_mailing_date(value: str) -> str:
      """USPS scan-form mailingDate must be within today..today+7. Hard-fail
      (no silent overwriteMailingDate auto-snap) so the caller corrects the date."""
      import datetime

      mailing = lib.to_date(value)
      if mailing is None:
          raise ValueError(f"Invalid manifest mailingDate: {value!r}")

      today = datetime.datetime.now().date()
      mailing_day = mailing.date()
      if mailing_day < today or mailing_day > today + datetime.timedelta(days=7):
          raise ValueError(
              "Manifest mailingDate "
              f"{lib.fdate(value)} must be within today..today+7 "
              f"({today.isoformat()}..{(today + datetime.timedelta(days=7)).isoformat()})."
          )

      return lib.fdate(value)
  ```
  Apply the identical edits to `usps_international`.

- [ ] **Step 4: Run — expect PASS (and no regression).**
  ```
  python -m unittest discover -v -f modules/connectors/usps/tests 2>&1 | tail -20
  python -m unittest discover -v -f modules/connectors/usps_international/tests 2>&1 | tail -20
  ```
  Expected: the two new date tests pass; `test_create_tracking_request` / `test_create_manifest` / `test_parse_*` still pass *because A0 made the base fixture date `today+2`* (in-window). This confirms the A0→A4 ordering held.

- [ ] **Step 5: Commit.**
  ```
  fix(usps): hard-fail manifest mailingDate outside today..+7 (no auto-overwrite)
  ```

---

### Task A5: Fix `parse_error_response` non-JSON branch `AttributeError` — P1

In `utils.parse_error_response` (lines 161–175 usps / 167–181 intl), the function reads `content = response.read()` first, then in the non-JSON branch calls `response.code` and `response.strip()` on the **raw http error object** — but `response` is the wrapper, not a string, so `response.strip()` raises `AttributeError`, masking the actual error on the exact malformed-error path this function exists to handle. Fix: decode/strip the already-read `content`, and read the status code defensively.

**Files**
- Modify: `modules/connectors/usps/karrio/providers/usps/utils.py` (lines 161–175)
- Modify: `modules/connectors/usps_international/karrio/providers/usps_international/utils.py` (lines 167–181)
- Test paths: the two `test_manifest.py` files (this proxy-level decoder is reachable via a small direct unit test)

- [ ] **Step 1: Write the failing test (direct on `parse_error_response`).**
  This decoder takes a file-like http error object (it calls `.read()`). Add a tiny fake and a test. In `modules/connectors/usps/tests/usps/test_manifest.py`, add to the class:
  ```python
      def test_parse_error_response_non_json_plain_text(self):
          import karrio.providers.usps.utils as provider_utils

          class _FakeHttpError:
              code = 500
              def __init__(self, body):
                  self._body = body
              def read(self):
                  return self._body

          result = provider_utils.parse_error_response(
              _FakeHttpError(b"  Internal Server Error  ")
          )
          self.assertEqual(result["error"]["code"], 500)
          self.assertEqual(result["error"]["message"], "Internal Server Error")
  ```
  Mirror into `usps_international` with `import karrio.providers.usps_international.utils as provider_utils`.

- [ ] **Step 2: Run — expect FAIL.**
  ```
  python -m unittest discover -v -f modules/connectors/usps/tests -k test_parse_error_response_non_json_plain_text 2>&1 | tail -25
  ```
  Expected: FAIL with `AttributeError: '_FakeHttpError' object has no attribute 'strip'` (current code calls `response.strip()`).

- [ ] **Step 3: Implement.**
  In `modules/connectors/usps/karrio/providers/usps/utils.py`, replace lines 161–175:
  ```python
  def parse_error_response(response) -> dict:
      # Check if the response is JSON
      content = lib.failsafe(lambda: response.read())
      json_data = lib.failsafe(lambda: lib.to_dict(content))

      if json_data:
          return json_data

      # the response is plain text
      return dict(
          error=dict(
              code=response.code,
              message=response.strip(),
          )
      )
  ```
  with:
  ```python
  def parse_error_response(response) -> dict:
      # Check if the response is JSON
      content = lib.failsafe(lambda: response.read())
      json_data = lib.failsafe(lambda: lib.to_dict(content))

      if json_data:
          return json_data

      # the response is plain text; decode the bytes we already read instead of
      # calling .strip()/.code on the raw http error object (which has neither).
      text = content.decode("utf-8", errors="replace") if isinstance(content, (bytes, bytearray)) else (content or "")
      code = getattr(response, "code", None) or getattr(response, "status", None) or "SHIPPING_SDK_ERROR"

      return dict(
          error=dict(
              code=code,
              message=text.strip(),
          )
      )
  ```
  Apply the identical edits to `usps_international` (its lines 167–181).

- [ ] **Step 4: Run — expect PASS.**
  ```
  python -m unittest discover -v -f modules/connectors/usps/tests 2>&1 | tail -20
  python -m unittest discover -v -f modules/connectors/usps_international/tests 2>&1 | tail -20
  ```
  Expected: new test passes; all existing tests still pass (JSON branch is unchanged).

- [ ] **Step 5: Commit.**
  ```
  fix(usps): decode content in parse_error_response non-JSON branch (no AttributeError)
  ```

---

### Task A6: Case-insensitive / quoting-tolerant multipart header matching — P2

`parse_response` matches `"Content-Type" in header` and `"Content-Disposition" in header` literally, and the `name=` regex requires a leading space (`r' name="([^"]+)"'`). Real servers sometimes send lowercase headers (`content-type:`) or `;name=` without a space. Make matching case-insensitive and relax the `name=` regex. Behavior-preserving for current fixtures.

**Files**
- Modify: `modules/connectors/usps/karrio/providers/usps/utils.py` (lines 139, 141, 143, 147, 148)
- Modify: `modules/connectors/usps_international/karrio/providers/usps_international/utils.py` (lines 145, 147, 149, 153, 154)
- Test paths: the two `test_manifest.py` files

- [ ] **Step 1: Write the failing test (lowercase headers + no-space name).**
  In `modules/connectors/usps/tests/usps/test_manifest.py`, add to the class:
  ```python
      def test_parse_manifest_multipart_lowercase_headers(self):
          with patch("karrio.mappers.usps.proxy.lib.request") as mock:
              mock.return_value = LowercaseHeaderMultipartResponse
              parsed_response = karrio.Manifest.create(self.ManifestRequest).from_(gateway).parse()
              logger.debug(lib.to_dict(parsed_response))
              self.assertListEqual(lib.to_dict(parsed_response), ParsedLowercaseHeaderMultipartResponse)
  ```
  At module bottom (note lowercase `content-type`/`content-disposition` and `;name=` with no leading space):
  ```python
  LowercaseHeaderMultipartResponse = (
      "--uspsboundary123\r\n"
      "content-type: application/json\r\n"
      'content-disposition: form-data;name="SCANFormMetaData"\r\n'
      "\r\n"
      '{"manifestNumber": "9234567890", "trackingNumbers": ["794947717776"]}\r\n'
      "--uspsboundary123\r\n"
      "content-type: application/pdf\r\n"
      'content-disposition: form-data;filename="SCANFormImage.pdf";name="SCANFormImage"\r\n'
      "\r\n"
      "JVBERi0xLjQgU0NBTiBGb3Jt\r\n"
      "--uspsboundary123--\r\n"
  )

  ParsedLowercaseHeaderMultipartResponse = [
      {
          "carrier_id": "usps",
          "carrier_name": "usps",
          "doc": {"manifest": "JVBERi0xLjQgU0NBTiBGb3Jt"},
          "meta": {"manifestNumber": "9234567890", "trackingNumbers": ["794947717776"]},
      },
      [],
  ]
  ```
  Mirror into `usps_international`.

- [ ] **Step 2: Run — expect FAIL.**
  ```
  python -m unittest discover -v -f modules/connectors/usps/tests -k test_parse_manifest_multipart_lowercase_headers 2>&1 | tail -25
  ```
  Expected: FAIL — `normalize_multipart_response` only keeps lines `startswith("Content-")` (capital C) and `parse_response` matches `"Content-Type"`/`"Content-Disposition"` case-sensitively and `name=` requires a leading space, so the lowercase/no-space part is dropped → `details` is `None`/incomplete.

- [ ] **Step 3: Implement case-insensitive + relaxed matching.**
  Two functions in `modules/connectors/usps/karrio/providers/usps/utils.py` need touching:

  (a) In `normalize_multipart_response`, the header-collection loop at line 87 keeps only `line.startswith("Content-")`. Change line 87 from:
  ```python
                  if line.startswith("Content-"):
  ```
  to:
  ```python
                  if line.lower().startswith("content-"):
  ```
  And the part-has-headers gate at line 76 `if "Content-Type" in part:` — change to:
  ```python
          if "content-type" in part.lower():
  ```

  (b) In `parse_response`, change the header dispatch block (lines 138–150). Replace:
  ```python
          for header in headers:
              if "Content-Type" in header:
                  part_data["content_type"] = header.split(":")[1].strip()
              elif "Content-Disposition" in header:
                  disposition = header.split(":")[1].strip()
                  if "filename=" in disposition:
                      filename = re.search(r'filename="([^"]+)"', disposition)
                      if filename:
                          part_data["filename"] = filename.group(1)
                  if "name=" in disposition:
                      name = re.search(r' name="([^"]+)"', disposition)
                      if name:
                          part_data["name"] = name.group(1)
  ```
  with:
  ```python
          for header in headers:
              header_lower = header.lower()
              if "content-type" in header_lower:
                  part_data["content_type"] = header.split(":", 1)[1].strip()
              elif "content-disposition" in header_lower:
                  disposition = header.split(":", 1)[1].strip()
                  if "filename=" in disposition:
                      filename = re.search(r'filename="([^"]+)"', disposition)
                      if filename:
                          part_data["filename"] = filename.group(1)
                  if "name=" in disposition:
                      name = re.search(r'(?:^|[\s;])name="([^"]+)"', disposition)
                      if name:
                          part_data["name"] = name.group(1)
  ```
  Note: `header.split(":", 1)` (maxsplit=1) is used so header values containing `:` are not truncated, and the `name=` regex now accepts a leading space, `;`, or start-of-string. The `content_type` comparison downstream at `parse_response` line 153 (`== "application/json"`) is unaffected because USPS sends lowercase `application/json` already; the value is taken verbatim from the header.

  Apply the identical edits to `usps_international` (`normalize_multipart_response` lines ~82/93, `parse_response` lines 144–156).

- [ ] **Step 4: Run — expect PASS (and full regression).**
  ```
  python -m unittest discover -v -f modules/connectors/usps/tests 2>&1 | tail -25
  python -m unittest discover -v -f modules/connectors/usps_international/tests 2>&1 | tail -25
  ```
  Expected: the lowercase test passes; all prior multipart tests (A1 special-boundary, real-doc, etc.) still pass.

- [ ] **Step 5: Commit.**
  ```
  fix(usps): case-insensitive multipart headers + relaxed name= matching
  ```

---

### Task A7: Replace hand-built fixture with the captured real `scan-forms/v3` multipart — P1

**Depends on A1 (boundary regex), A0 (relative date).** The real captured multipart at `/tmp/real_scanform.txt` has a boundary ending in `+` (`--okuYKZJGhVgsoUrYz1NtyzN+`), an `application/pdf` part with PDF magic, and a `SCANFormMetaData` JSON part with `manifestNumber: "92750902795406000000207217"`. Add a verify-then-code test against it so the connector is locked to the *real* contract, not the synthetic 12-char fixture. Its `mailingDate` is `2026-06-17` (today at capture); since we don't pass it back through `manifest_request` here (we parse a response, not build a request), no date-window concern applies.

**Files**
- Create: `modules/connectors/usps/tests/usps/real_scanform.txt` (copied verbatim from `/tmp/real_scanform.txt`)
- Create: `modules/connectors/usps_international/tests/usps_international/real_scanform.txt` (same bytes)
- Modify: `modules/connectors/usps/tests/usps/test_manifest.py`, `modules/connectors/usps_international/tests/usps_international/test_manifest.py`
- Test paths: the two `test_manifest.py` files

- [ ] **Step 1: Copy the captured fixture into both test trees.**
  ```
  cp /tmp/real_scanform.txt modules/connectors/usps/tests/usps/real_scanform.txt
  cp /tmp/real_scanform.txt modules/connectors/usps_international/tests/usps_international/real_scanform.txt
  ```
  (No code yet — this is the captured artifact the next step asserts against.)

- [ ] **Step 2: Write the failing test (parse the real multipart).**
  In `modules/connectors/usps/tests/usps/test_manifest.py`, add `import os` and `import base64` near the top imports, then add to the class:
  ```python
      def test_parse_real_scan_form_multipart(self):
          fixture_path = os.path.join(os.path.dirname(__file__), "real_scanform.txt")
          with open(fixture_path, "r") as f:
              real_response = f.read()

          with patch("karrio.mappers.usps.proxy.lib.request") as mock:
              mock.return_value = real_response
              details, messages = karrio.Manifest.create(self.ManifestRequest).from_(gateway).parse()

          self.assertListEqual(lib.to_dict(messages), [])
          self.assertIsNotNone(details)
          self.assertEqual(details.meta["manifestNumber"], "92750902795406000000207217")
          # doc is base64 of a real PDF (starts with %PDF-).
          self.assertTrue(base64.b64decode(details.doc.manifest)[:5] == b"%PDF-")
  ```
  Mirror into `usps_international` (`karrio.mappers.usps_international.proxy`). The `manifestNumber` value is identical in both (same captured trace).

- [ ] **Step 3: Run — expect PASS (this is the verify-then-code lock; A1+A3 already make it green).**
  ```
  python -m unittest discover -v -f modules/connectors/usps/tests -k test_parse_real_scan_form_multipart 2>&1 | tail -25
  python -m unittest discover -v -f modules/connectors/usps_international/tests -k test_parse_real_scan_form_multipart 2>&1 | tail -25
  ```
  Expected: PASS — the boundary-with-`+` parses (A1), the `application/pdf` part is recognized, the PDF magic passes the A3 sniff, and `manifestNumber` is extracted from the `SCANFormMetaData` JSON part. If it FAILS, that signals an A1/A3 regression — fix there, not by loosening this assertion (this is the production-contract gate). Note: because A7 depends on A1+A3, this task is a green-lock, not a red→green pair; its value is catching future regressions against the real bytes.

- [ ] **Step 4: Full connector regression both modules.**
  ```
  python -m unittest discover -v -f modules/connectors/usps/tests 2>&1 | tail -25
  python -m unittest discover -v -f modules/connectors/usps_international/tests 2>&1 | tail -25
  ```
  Expected: every test in both modules green (the 5 original + special-boundary + unparseable + empty-doc + non-pdf-doc + lowercase-headers + out/in-window dates + parse-error-non-json + real-scan-form). Confirm `manifest.py` and `utils.py` for `usps` vs `usps_international` differ only by the `usps_international`/`usps` identifiers (e.g. `git diff` or `diff` the two files), satisfying the parity decision.

- [ ] **Step 5: Commit.**
  ```
  test(usps): lock scan-form parse to captured real scan-forms/v3 multipart fixture
  ```

---

## Notes / hand-off for the executing subagents

- **Parity verification gate (run before declaring A done):** the touched function bodies in `usps` vs `usps_international` must be identical modulo the carrier identifier. Quick check:
  ```
  diff <(sed 's/usps_international/usps/g' modules/connectors/usps_international/karrio/providers/usps_international/manifest.py) modules/connectors/usps/karrio/providers/usps/manifest.py
  diff <(sed 's/usps_international/usps/g' modules/connectors/usps_international/karrio/providers/usps_international/utils.py) modules/connectors/usps/karrio/providers/usps/utils.py
  ```
  Both diffs should be empty for the functions changed here (the pre-existing `ConnectionConfig.price_type` divergence in `usps_international/utils.py` lines 51–57 is expected and out of scope — it is not one of the functions A-items touch).

- **A2/A3 interaction:** A2 introduces `_has_valid_doc` as a presence check, then A3 hardens it into a PDF sniff via `_looks_like_pdf`. If the implementer lands A2 and A3 in one sitting, fold the helper directly into the A3 form and skip the temporary presence-only version — but each task's tests must still pass at its own commit boundary (TDD per-task green).

- **A4 surfacing rationale (why a bare `ValueError` → 400):** `gateway.mapper.create_manifest_request(payload)` runs at `modules/sdk/karrio/api/interface.py:598`, *outside* the `@fail_safe(gateway)` wrapper (which only wraps `deserialize()` at :601–603). The raised exception therefore propagates to the server's `gateway.Manifests.create(...)` call (`serializers/manifest.py:42`), where karrio's server gateway converts SDK exceptions into a 400-class API error. The connector tests only need to assert the `ValueError` is raised by `create_manifest_request` (Step 1 of A4) — the 400 mapping is covered by workstream B's server test `test_create_manifest_*_400`.

- **Files referenced (all absolute):**
  - `/Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest/modules/connectors/usps/karrio/providers/usps/manifest.py`
  - `/Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest/modules/connectors/usps/karrio/providers/usps/utils.py`
  - `/Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest/modules/connectors/usps/karrio/providers/usps/error.py` (read-only; `parse_error_response` there is the one called by `parse_manifest_response`, unchanged)
  - `/Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest/modules/connectors/usps/karrio/mappers/usps/proxy.py` (read-only; `create_manifest` uses `provider_utils.parse_error_response` as `on_error` — A5 fixes that path)
  - `/Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest/modules/connectors/usps/tests/usps/test_manifest.py`
  - `/Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest/modules/connectors/usps_international/karrio/providers/usps_international/manifest.py`
  - `/Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest/modules/connectors/usps_international/karrio/providers/usps_international/utils.py`
  - `/Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest/modules/connectors/usps_international/tests/usps_international/test_manifest.py`

- **Task ordering / dependency summary:** A0 (P0) → A4 (P1) is a hard sequence. A1 (P0) → A7 (P1) is a hard sequence. A2 (P0) → A3 (P1) share the `_has_valid_doc` helper. A5 (P1) and A6 (P2) are independent of the others. Recommended execution order: **A0, A1, A2, A3, A4, A5, A6, A7**.