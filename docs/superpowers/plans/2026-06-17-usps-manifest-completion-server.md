No existing USPS carrier set — I'll define a module-level constant in the manifest serializer. I now have complete, verified context. Let me write the TDD task list.

A few final confirmations baked into the plan:
- `serializers.ValidationError` → DRF ValidationError → HTTP 400 (confirmed via exceptions.py:110-111).
- Gateway `Connections.first(..., raise_not_found=True)` raises `NotFound` → 404 (exceptions.py:134). So an unknown `carrier_id`/`carrier_name` yields 404; a wrong-but-existing carrier with no matching purchased shipments yields the partial-drop 400.
- 422 only in the two download views via `Response(status=...)`.
- B4 ordering goes in `gateway.py` at the `carriers = list(carrier_queryset.distinct())` / `brokered = list(...)` construction (lines ~198-201).

Here is the SERVER workstream TDD plan.

---

# SERVER Workstream (B) — TDD Implementation Plan

**Worktree:** `/Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest` (branch `feat/usps-scanform-manifest`)
**Run server tests:** `./bin/run-server-tests` (the `.venv/karrio` SDK env lacks `karrio.server`; server tests run via this bin script / the karrio container). Single module while iterating: `karrio test --failfast karrio.server.manager.tests.test_manifests`
**Files in scope:**
- `modules/manager/karrio/server/manager/serializers/manifest.py` (B1, B3, B5, B6)
- `modules/manager/karrio/server/manager/views/manifests.py` (B2)
- `modules/core/karrio/server/core/serializers.py` (B3 — `ManifestData.carrier_id`)
- `modules/core/karrio/server/core/gateway.py` (B4 — deterministic ordering)
- `modules/manager/karrio/server/manager/tests/test_manifests.py` (all server tests + repair)

**Cross-workstream dependencies:**
- **B3 (`carrier_id` schema field)** is the upstream gate for CONNECTOR-independent dashboard work **C2** (DX workstream "Connection `<select>`") and **E2** (TS-type regen). C2 must not start until B3 lands and TS types are regenerated.
- No dependency on the CONNECTOR workstream (A-items) — server tests mock the gateway response, never the live USPS multipart parser. The "repair test_manifests.py" task (B0) is a prerequisite for **every** other server test task here.
- The cap constant key (`usps`/`usps_international` → 1000) is the locked decision; no connector code reads it.

Execute tasks in listed order (B0 → B1 → B6 → B5 → B3 → B4 → B2). Each task is one red→green→commit cycle. Commits are drafted but **must not be run without user permission** per project rules — the commit line is the message to use when permission is granted.

---

### Task: B0 — Repair the manifest test fixture (DB-direct purchased shipment) — P0

The existing `TestManifestDocumentDownload.create_manifest()` chains two `gateway.utils.identity` mocks (shipment purchase) then a third for the manifest. The side-effect count is unstable and the purchase path now diverges, leaving an empty body → `json.loads` blows up. Rebuild the purchased shipment **directly in the DB** (mirroring `TestShipmentPurchase.setUp` / `test_cancel_purchased_shipment`), then mock only the manifest gateway call. This becomes the shared base class for all subsequent server tests.

**Files**
- Modify: `modules/manager/karrio/server/manager/tests/test_manifests.py` (full rewrite of lines 1–85, class `TestManifestDocumentDownload`)
- Test: same file (this task IS test code; "implementation" here = the rebuilt fixture/helpers, verified by the two repaired tests going green)

- [ ] **Step 1: Write the repaired fixture base + the two repaired tests (failing).**
  Replace the entire current contents of `modules/manager/karrio/server/manager/tests/test_manifests.py` with:

```python
import base64
import json
from unittest.mock import ANY, patch

import karrio.server.manager.models as models
import karrio.server.providers.models as providers
from django.urls import reverse
from karrio.core.models import ManifestDetails as ManifestDetailsModel
from karrio.server.core.tests import APITestCase
from karrio.server.core.utils import create_carrier_snapshot
from rest_framework import status

# A real, minimal base64-encoded PDF ("%PDF-1.4\n" + EOF marker).
VALID_PDF_B64 = base64.b64encode(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n").decode("ascii")
# Base64 of a non-PDF byte string (decodes cleanly, but is not a PDF).
NON_PDF_B64 = base64.b64encode(b"this is not a pdf at all").decode("ascii")


class TestManifestFixture(APITestCase):
    """Shared fixture: a USPS connection + a purchased USPS shipment built directly in DB."""

    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        cls.usps_carrier = providers.CarrierConnection.objects.create(
            carrier_code="usps",
            carrier_id="usps",
            test_mode=True,
            active=True,
            created_by=cls.user,
            credentials=dict(
                client_id="test",
                client_secret="test",
                account_number="000000000",
                account_type="EPS",
            ),
        )

    def setUp(self) -> None:
        super().setUp()
        # Build a purchased USPS shipment directly in the DB (mirrors
        # TestShipmentPurchase / test_cancel_purchased_shipment), so manifest
        # creation does not depend on the unstable purchase-mock chain.
        self.shipment: models.Shipment = models.Shipment.objects.create(
            created_by=self.user,
            test_mode=True,
            status="created",
            shipment_identifier="9400100000000000000000",
            tracking_number="9400100000000000000000",
            label_type="PDF",
            shipper={
                "person_name": "John Poop",
                "company_name": "A corp.",
                "address_line1": "123 Main St",
                "city": "Charlotte",
                "state_code": "NC",
                "postal_code": "28202",
                "country_code": "US",
                "phone_number": "704 000 0000",
            },
            recipient={
                "person_name": "Jane Doe",
                "company_name": "B corp.",
                "address_line1": "456 Oak Ave",
                "city": "Atlanta",
                "state_code": "GA",
                "postal_code": "30301",
                "country_code": "US",
                "phone_number": "404 000 0000",
            },
            parcels=[{"weight": 1.0, "weight_unit": "LB", "packaging_type": "your_packaging"}],
            selected_rate={
                "id": "rat_usps_test",
                "carrier_id": "usps",
                "carrier_name": "usps",
                "service": "usps_ground_advantage",
                "currency": "USD",
                "total_charge": 8.5,
                "test_mode": True,
            },
            carrier=create_carrier_snapshot(self.usps_carrier),
        )

    def usps_address(self) -> dict:
        return {
            "address_line1": "123 Main St",
            "city": "Charlotte",
            "country_code": "US",
            "postal_code": "28202",
            "state_code": "NC",
        }

    def create_manifest(self, manifest_doc: str = VALID_PDF_B64) -> dict:
        """Create a manifest via the API with the shared purchased shipment, mocking the gateway."""
        manifest_url = reverse("karrio.server.manager:manifest-list")
        manifest_data = {
            "carrier_name": "usps",
            "shipment_ids": [self.shipment.id],
            "address": self.usps_address(),
        }
        response_value = (
            ManifestDetailsModel(
                carrier_id="usps",
                carrier_name="usps",
                doc=dict(manifest=manifest_doc),
                meta=dict(manifestNumber="42708517000001"),
            ),
            [],
        )
        with patch("karrio.server.core.gateway.utils.identity") as mock:
            mock.return_value = response_value
            response = self.client.post(manifest_url, manifest_data)
        return json.loads(response.content)


class TestManifestDocumentDownload(TestManifestFixture):
    """Repaired manifest document download tests (DB-direct purchased shipment)."""

    def test_download_manifest_document(self):
        manifest = self.create_manifest()

        url = reverse(
            "karrio.server.manager:manifest-document-download",
            kwargs=dict(pk=manifest["id"]),
        )
        response = self.client.post(url)
        response_data = json.loads(response.content)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertDictEqual(
            response_data,
            {
                "category": "manifest",
                "format": "PDF",
                "base64": VALID_PDF_B64,
                "url": ANY,
            },
        )

    def test_download_manifest_not_found(self):
        url = reverse(
            "karrio.server.manager:manifest-document-download",
            kwargs=dict(pk="manf_non_existent_id"),
        )
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
```

- [ ] **Step 2: Run — expect FAIL.**
  `./bin/run-server-tests` (or `karrio test --failfast karrio.server.manager.tests.test_manifests`).
  Expected failure mode BEFORE the source is touched: `test_download_manifest_document` currently passes against the OLD fixture but the NEW test file references `TestManifestFixture` and the new `create_manifest()` — the failure to expect here is none at the source layer (no source change yet); this task's red is structural: it will pass only once the fixture rebuild is correct. To force a genuine red first, temporarily set `VALID_PDF_B64 = "not-base64-at-all-@@@"` at the top, run, and observe `test_download_manifest_document` FAIL on the `assertDictEqual` (base64 mismatch) / a `binascii.Error` in the view. This proves the test exercises the real download path.

- [ ] **Step 3: Implementation — restore the correct constant.**
  Revert the temporary sabotage: set `VALID_PDF_B64` back to `base64.b64encode(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n").decode("ascii")`. No production source changes in B0 — the repair is entirely in the test fixture. (The current `ManifestDoc.get_file` / `ManifestDocumentDownload.post` already handle a valid base64 PDF; B0 only proves the rebuilt fixture drives them.)

- [ ] **Step 4: Run — expect PASS.**
  `karrio test --failfast karrio.server.manager.tests.test_manifests` → both `test_download_manifest_document` and `test_download_manifest_not_found` PASS.

- [ ] **Step 5: Commit.**
  `test(server): rebuild manifest test fixture with DB-direct purchased USPS shipment`

---

### Task: B6 — USPS US-origin address completeness → 400 — P1

USPS carriers require `state_code` + a 5-digit `postal_code` on the warehouse address; today a missing `state_code` degrades to `state=None`/`ZIPCode=''` and fails late as a USPS 424. Pre-flight in the serializer: for USPS carriers, raise a `ValidationError` (→ 400) naming the missing field, before any gateway call.

**Files**
- Modify: `modules/manager/karrio/server/manager/serializers/manifest.py` (add `USPS_CARRIERS` constant near line 9; insert validation block after the `carrier` resolution at line 22, before the shipment query at line 24–25)
- Test: `modules/manager/karrio/server/manager/tests/test_manifests.py` (new class `TestManifestValidation`)

- [ ] **Step 1: Failing test.**
  Append to `test_manifests.py`:

```python
class TestManifestValidation(TestManifestFixture):
    def test_create_manifest_missing_state_400(self):
        manifest_url = reverse("karrio.server.manager:manifest-list")
        address = self.usps_address()
        address.pop("state_code")
        manifest_data = {
            "carrier_name": "usps",
            "shipment_ids": [self.shipment.id],
            "address": address,
        }
        with patch("karrio.server.core.gateway.utils.identity") as mock:
            response = self.client.post(manifest_url, manifest_data)
            mock.assert_not_called()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("state_code", json.dumps(json.loads(response.content)))

    def test_create_manifest_missing_zip_400(self):
        manifest_url = reverse("karrio.server.manager:manifest-list")
        address = self.usps_address()
        address["postal_code"] = "ABC"
        manifest_data = {
            "carrier_name": "usps",
            "shipment_ids": [self.shipment.id],
            "address": address,
        }
        with patch("karrio.server.core.gateway.utils.identity") as mock:
            response = self.client.post(manifest_url, manifest_data)
            mock.assert_not_called()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("postal_code", json.dumps(json.loads(response.content)))
```

- [ ] **Step 2: Run — expect FAIL.**
  `karrio test --failfast karrio.server.manager.tests.test_manifests.TestManifestValidation`
  Expected: both FAIL — without the guard the request reaches the gateway (so `mock.assert_not_called()` fails) and/or returns 424/500 instead of 400.

- [ ] **Step 3: Implementation.**
  In `modules/manager/karrio/server/manager/serializers/manifest.py`, add the constant and `re` import at the top, and a validation block after carrier resolution.

  Change the imports/constant region (current lines 1–9):

```python
import re
import typing

import karrio.server.core.gateway as gateway
import karrio.server.core.serializers as core
import karrio.server.manager.models as models
import karrio.server.serializers as serializers
from karrio.server.core.utils import create_carrier_snapshot

DEFAULT_CARRIER_FILTER: typing.Any = dict(active=True, capability="manifest")

# USPS carriers require a complete US origin address (state + 5-digit ZIP)
# pre-flight; otherwise the request degrades to a late USPS 424.
USPS_CARRIERS = {"usps", "usps_international"}
ZIP5 = re.compile(r"^\d{5}(-\d{4})?$")
```

  Then insert the guard immediately after the `carrier = gateway.Connections.first(...)` block (after current line 22), before the `# Filter shipments by carrier_code` comment:

```python
        if carrier_name in USPS_CARRIERS:
            address = validated_data.get("address") or {}
            missing = [
                field
                for field in ("state_code", "postal_code")
                if not (address.get(field) or "").strip()
            ]
            if missing:
                raise serializers.ValidationError(
                    {field: "This field is required for USPS manifests." for field in missing}
                )
            if not ZIP5.match((address.get("postal_code") or "").strip()):
                raise serializers.ValidationError(
                    {"postal_code": "A valid 5-digit US ZIP code is required for USPS manifests."}
                )
```

- [ ] **Step 4: Run — expect PASS.**
  `karrio test --failfast karrio.server.manager.tests.test_manifests` → `TestManifestValidation` green, no regression in `TestManifestDocumentDownload`.

- [ ] **Step 5: Commit.**
  `fix(server): pre-flight USPS manifest origin-address completeness (state + ZIP) -> 400`

---

### Task: B5 — Shipment-count cap → 400 before DB query — P1

USPS caps shipments per SCAN form. Reject `len(shipment_ids) > cap` with a 400 **before** the DB query / gateway round-trip. Cap is per-carrier (`usps:1000`, `usps_international:1000`) as a documented assumption with per-call override deferred to config.

**Files**
- Modify: `modules/manager/karrio/server/manager/serializers/manifest.py` (add `MANIFEST_MAX_SHIPMENTS` constant; insert cap check after `shipment_ids = list(set(...))` at current line 16, before carrier resolution at line 18)
- Test: `modules/manager/karrio/server/manager/tests/test_manifests.py` (`TestManifestValidation`)

- [ ] **Step 1: Failing test.**
  Append to class `TestManifestValidation`:

```python
    def test_create_manifest_over_cap_400(self):
        manifest_url = reverse("karrio.server.manager:manifest-list")
        manifest_data = {
            "carrier_name": "usps",
            "shipment_ids": [f"shp_{i:08d}" for i in range(1001)],
            "address": self.usps_address(),
        }
        with patch("karrio.server.core.gateway.utils.identity") as mock:
            response = self.client.post(manifest_url, manifest_data)
            mock.assert_not_called()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("1000", json.dumps(json.loads(response.content)))
```

- [ ] **Step 2: Run — expect FAIL.**
  `karrio test --failfast karrio.server.manager.tests.test_manifests.TestManifestValidation.test_create_manifest_over_cap_400`
  Expected: FAIL — without the cap the request hits the carrier-resolution/DB query (so it returns 400 for "invalid ids" only after a DB query, or 404, and `mock.assert_not_called()` may still pass but the `"1000"` assertion fails because the message is the generic invalid-ids text).

- [ ] **Step 3: Implementation.**
  In `manifest.py`, add the cap constant under `USPS_CARRIERS`:

```python
# Documented per-carrier max shipments per SCAN form (v3 OpenAPI unconfirmed;
# overridable per-connection later). Reject over-cap before any DB/OAuth round-trip.
MANIFEST_MAX_SHIPMENTS = {"usps": 1000, "usps_international": 1000}
```

  Then, inside `create`, immediately after `shipment_ids = list(set(data.pop("shipment_ids")))` (current line 16) and before `carrier_name = data["carrier_name"]`, insert:

```python
        carrier_name = data["carrier_name"]
        cap = MANIFEST_MAX_SHIPMENTS.get(carrier_name)
        if cap is not None and len(shipment_ids) > cap:
            raise serializers.ValidationError(
                {
                    "shipment_ids": (
                        f"Too many shipments for a single {carrier_name} manifest: "
                        f"{len(shipment_ids)} provided, maximum is {cap}."
                    )
                }
            )
```

  Note: this introduces `carrier_name = data["carrier_name"]` before the existing line 17 `carrier_name = data["carrier_name"]` — **remove the now-duplicate** assignment at the original line 17 so `carrier_name` is read once. The final ordering inside `create` is: pop shipment_ids → read carrier_name → cap check → carrier resolution → USPS address guard (B6) → shipment query.

- [ ] **Step 4: Run — expect PASS.**
  `karrio test --failfast karrio.server.manager.tests.test_manifests` → `test_create_manifest_over_cap_400` green; full module still green.

- [ ] **Step 5: Commit.**
  `fix(server): cap USPS manifest shipment count (1000) and reject before DB query`

---

### Task: B1 — Catch silent partial-drop → 400 listing missing ids — P0

Current guard `len(found) > len(req) or len(found) == 0` cannot detect the realistic case: caller passes 5 ids, 2 are already manifested or wrong-carrier, 3 survive → silent 201 missing 2 packages. Replace with a missing-id set diff that raises a 400 **naming the exact missing ids**.

**Files**
- Modify: `modules/manager/karrio/server/manager/serializers/manifest.py` (replace the guard at current lines 30–40)
- Test: `modules/manager/karrio/server/manager/tests/test_manifests.py` (new class `TestManifestPartialDrop`)

- [ ] **Step 1: Failing test.**
  Append to `test_manifests.py`:

```python
class TestManifestPartialDrop(TestManifestFixture):
    def _second_purchased_shipment(self) -> models.Shipment:
        return models.Shipment.objects.create(
            created_by=self.user,
            test_mode=True,
            status="created",
            shipment_identifier="9400100000000000000001",
            tracking_number="9400100000000000000001",
            label_type="PDF",
            shipper=self.shipment.shipper,
            recipient=self.shipment.recipient,
            parcels=self.shipment.parcels,
            selected_rate=self.shipment.selected_rate,
            carrier=self.shipment.carrier,
        )

    def test_create_manifest_partial_drop_400(self):
        # shipment #2 is already manifested elsewhere -> its id must be reported missing.
        other = self._second_purchased_shipment()
        other.manifest = models.Manifest.objects.create(
            created_by=self.user,
            test_mode=True,
            carrier=create_carrier_snapshot(self.usps_carrier),
            manifest=VALID_PDF_B64,
        )
        other.save()

        manifest_url = reverse("karrio.server.manager:manifest-list")
        manifest_data = {
            "carrier_name": "usps",
            "shipment_ids": [self.shipment.id, other.id],
            "address": self.usps_address(),
        }
        with patch("karrio.server.core.gateway.utils.identity") as mock:
            response = self.client.post(manifest_url, manifest_data)
            mock.assert_not_called()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        body = json.dumps(json.loads(response.content))
        self.assertIn(other.id, body)
        self.assertNotIn(self.shipment.id, body.replace(other.id, ""))

    def test_create_manifest_unpurchased_shipment_400(self):
        draft = models.Shipment.objects.create(
            created_by=self.user,
            test_mode=True,
            status="draft",
        )
        manifest_url = reverse("karrio.server.manager:manifest-list")
        manifest_data = {
            "carrier_name": "usps",
            "shipment_ids": [draft.id],
            "address": self.usps_address(),
        }
        with patch("karrio.server.core.gateway.utils.identity") as mock:
            response = self.client.post(manifest_url, manifest_data)
            mock.assert_not_called()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(draft.id, json.dumps(json.loads(response.content)))

    def test_create_manifest_wrong_carrier_400(self):
        # canadapost carrier exists, but the shipment was purchased with usps.
        manifest_url = reverse("karrio.server.manager:manifest-list")
        manifest_data = {
            "carrier_name": "canadapost",
            "shipment_ids": [self.shipment.id],
            "address": {
                "address_line1": "125 Church St",
                "city": "Moncton",
                "country_code": "CA",
                "postal_code": "E1C4Z8",
                "state_code": "NB",
            },
        }
        with patch("karrio.server.core.gateway.utils.identity") as mock:
            response = self.client.post(manifest_url, manifest_data)
            mock.assert_not_called()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(self.shipment.id, json.dumps(json.loads(response.content)))

    def test_create_manifest_unknown_carrier_404(self):
        manifest_url = reverse("karrio.server.manager:manifest-list")
        manifest_data = {
            "carrier_name": "usps",
            "carrier_id": "no_such_connection",
            "shipment_ids": [self.shipment.id],
            "address": self.usps_address(),
        }
        with patch("karrio.server.core.gateway.utils.identity") as mock:
            response = self.client.post(manifest_url, manifest_data)
            mock.assert_not_called()

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
```

  Note: `test_create_manifest_unknown_carrier_404` exercises the `carrier_id` selector landing in **B3**; it will only pass once B3 is implemented. Mark it as expected-to-pass after B3 — until then it returns 200/400 (the `carrier_id` field is ignored). To keep B1's red/green clean, run B1's green check against the first three methods only, and let `test_create_manifest_unknown_carrier_404` go green in the B3 task. (Listed here so the partial-drop class is authored once.)

- [ ] **Step 2: Run — expect FAIL.**
  `karrio test --failfast karrio.server.manager.tests.test_manifests.TestManifestPartialDrop.test_create_manifest_partial_drop_400`
  Expected: FAIL — current guard returns 201 (the surviving shipment `self.shipment` makes `len(found)==1`, not 0, and `1 > 2` is false), so status is 201, not 400.

- [ ] **Step 3: Implementation.**
  In `manifest.py`, replace the existing guard (current lines 30–40):

```python
        shipment_identifiers = [_.shipment_identifier for _ in shipments]

        if len(shipment_identifiers) > len(shipment_ids) or len(shipment_identifiers) == 0:
            raise serializers.ValidationError(
                {
                    "shipment_ids": (
                        "One or more shipment ids are invalid or not found. "
                        "Please make sure that the shipments referenced exist and have been purchased with the same carrier."
                    )
                }
            )
```

  with a missing-id diff:

```python
        found_ids = {_.id for _ in shipments}
        missing_ids = [_id for _id in shipment_ids if _id not in found_ids]

        if missing_ids:
            raise serializers.ValidationError(
                {
                    "shipment_ids": (
                        "The following shipment ids could not be manifested "
                        "(not found, already manifested, or purchased with a different carrier): "
                        f"{', '.join(missing_ids)}."
                    )
                }
            )

        shipment_identifiers = [_.shipment_identifier for _ in shipments]
```

  (`shipments` is the queryset filtered by `id__in`, `manifest__isnull=True`, `carrier__carrier_code=carrier_name` at the current lines 25–29 — unchanged. Iterating it once for `found_ids` and once for `shipment_identifiers` is fine; the queryset caches after first evaluation.)

- [ ] **Step 4: Run — expect PASS.**
  `karrio test --failfast karrio.server.manager.tests.test_manifests.TestManifestPartialDrop` → `partial_drop`, `unpurchased`, `wrong_carrier` green (the `unknown_carrier_404` goes green in B3). Full `TestManifestDocumentDownload` still green.

- [ ] **Step 5: Commit.**
  `fix(server): manifest partial-drop guard reports exact missing shipment ids -> 400`

---

### Task: B3 — Optional `carrier_id` selector for multi-account — P0 (gates DX C2 + E2)

Add an optional top-level `carrier_id` to `ManifestData` (mirror `ShipmentCancelData` at `core/serializers.py:1914`), build a `carrier_filter` from it, pass to `Connections.first` (mirror `PickupData` at `pickup.py:205-207`), and pop `carrier_id` before `ManifestRequest.map`. Gateway already resolves id-or-name (`gateway.py:104-119`) — no gateway change. **This schema change gates dashboard C2 and the TS-type regen E2.**

**Files**
- Modify: `modules/core/karrio/server/core/serializers.py` (`ManifestData`, current lines 1973–1977 — add `carrier_id` field)
- Modify: `modules/manager/karrio/server/manager/serializers/manifest.py` (build `carrier_filter`, pass to `Connections.first`, pop `carrier_id` from `data`)
- Test: `modules/manager/karrio/server/manager/tests/test_manifests.py` (new class `TestManifestCarrierSelector` + un-skip `TestManifestPartialDrop.test_create_manifest_unknown_carrier_404`)

- [ ] **Step 1: Failing test.**
  Append to `test_manifests.py`:

```python
class TestManifestCarrierSelector(TestManifestFixture):
    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        # A second USPS connection (different carrier_id) on the same account.
        cls.usps_carrier_two = providers.CarrierConnection.objects.create(
            carrier_code="usps",
            carrier_id="usps_secondary",
            test_mode=True,
            active=True,
            created_by=cls.user,
            credentials=dict(
                client_id="test2",
                client_secret="test2",
                account_number="111111111",
                account_type="EPS",
            ),
        )

    def test_create_manifest_targets_specific_connection(self):
        manifest_url = reverse("karrio.server.manager:manifest-list")
        manifest_data = {
            "carrier_name": "usps",
            "carrier_id": "usps_secondary",
            "shipment_ids": [self.shipment.id],
            "address": self.usps_address(),
        }
        response_value = (
            ManifestDetailsModel(
                carrier_id="usps_secondary",
                carrier_name="usps",
                doc=dict(manifest=VALID_PDF_B64),
                meta=dict(manifestNumber="42708517000099"),
            ),
            [],
        )
        with patch("karrio.server.core.gateway.utils.identity") as mock:
            mock.return_value = response_value
            response = self.client.post(manifest_url, manifest_data)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        manifest = models.Manifest.objects.get(pk=json.loads(response.content)["id"])
        self.assertEqual(manifest.carrier.get("carrier_id"), "usps_secondary")

    def test_create_manifest_without_carrier_id_still_works(self):
        manifest = self.create_manifest()
        self.assertIn("id", manifest)
        self.assertEqual(
            models.Manifest.objects.get(pk=manifest["id"]).carrier.get("carrier_code"),
            "usps",
        )
```

  Also: this task makes `TestManifestPartialDrop.test_create_manifest_unknown_carrier_404` (authored in B1) pass.

- [ ] **Step 2: Run — expect FAIL.**
  `karrio test --failfast karrio.server.manager.tests.test_manifests.TestManifestCarrierSelector.test_create_manifest_targets_specific_connection`
  Expected: FAIL — `carrier_id` is not yet a field on `ManifestData` (silently dropped by the serializer), so `Connections.first` matches by `carrier_name="usps"` and returns the **first** USPS connection (`usps`, ordering currently arbitrary) → snapshot `carrier_id` is `"usps"`, not `"usps_secondary"`. The `assertEqual(..., "usps_secondary")` fails.

- [ ] **Step 3: Implementation.**
  (a) In `modules/core/karrio/server/core/serializers.py`, change `ManifestData` (current lines 1973–1977) from:

```python
class ManifestData(ManifestRequestData):
    shipment_ids = serializers.StringListField(
        required=True,
        help_text="""The list of existing shipment object ids with label purchased.""",
    )
```

  to:

```python
class ManifestData(ManifestRequestData):
    shipment_ids = serializers.StringListField(
        required=True,
        help_text="""The list of existing shipment object ids with label purchased.""",
    )
    carrier_id = serializers.CharField(
        required=False,
        help_text="The manifest carrier_id for specific connection selection.",
    )
```

  (b) In `modules/manager/karrio/server/manager/serializers/manifest.py`, replace the carrier-resolution block (current lines 17–22, now reading `carrier_name` after the B5 cap insertion) so it builds a `carrier_filter` from `carrier_id` and pops it from `data`:

```python
        carrier_id = data.pop("carrier_id", None)
        carrier_filter = {"carrier_id": carrier_id} if carrier_id else {}
        carrier = gateway.Connections.first(
            context=context,
            carrier_name=carrier_name,
            **{"raise_not_found": True, **DEFAULT_CARRIER_FILTER, **carrier_filter},
        )
```

  (`data` is `validated_data.copy()`; popping `carrier_id` here ensures it is not forwarded into `ManifestRequest.map(data={**data, ...})` at the current line 42–52. The gateway `carrier_id` filter at `gateway.py:104-110` matches `Q(id=value) | Q(carrier_id=value)`, so `"usps_secondary"` resolves the second connection; an unknown value yields zero connections → `NotFound` → 404, satisfying `test_create_manifest_unknown_carrier_404`.)

- [ ] **Step 4: Run — expect PASS.**
  `karrio test --failfast karrio.server.manager.tests.test_manifests` → `TestManifestCarrierSelector` green, `TestManifestPartialDrop.test_create_manifest_unknown_carrier_404` green, full module green.

- [ ] **Step 5: Commit.**
  `feat(server): optional carrier_id selector on manifest create for multi-account USPS`
  *(After this lands, hand off to E2: regenerate TS types from the updated OpenAPI schema; that unblocks DX workstream C2. Note the schema addition in the PR body per E1.)*

---

### Task: B4 — Deterministic Connections fallback ordering — P1

When `carrier_id` is omitted, `Connections.list` builds `carriers = list(carrier_queryset.distinct())` with no `.order_by()` → arbitrary "first" account across DB backends. Add a stable `.order_by("-created_at")` so the no-`carrier_id` path is deterministic (newest connection wins, matching the model `Meta.ordering = ["-created_at"]` convention).

**Files**
- Modify: `modules/core/karrio/server/core/gateway.py` (the `COMBINE RESULTS` block, current lines ~196–201)
- Test: `modules/manager/karrio/server/manager/tests/test_manifests.py` (`TestManifestCarrierSelector`)

- [ ] **Step 1: Failing test.**
  Append to class `TestManifestCarrierSelector`:

```python
    def test_create_manifest_without_carrier_id_is_deterministic(self):
        # Two USPS connections exist; omitting carrier_id must resolve the
        # most-recently-created one deterministically (-created_at).
        manifest = self.create_manifest()
        snapshot = models.Manifest.objects.get(pk=manifest["id"]).carrier
        self.assertEqual(snapshot.get("carrier_id"), "usps_secondary")
```

  (`usps_secondary` is created in `setUpTestData` *after* `usps`, so with `-created_at` ordering it is the newest USPS connection and must be the deterministic pick.)

- [ ] **Step 2: Run — expect FAIL (or flaky).**
  `karrio test --failfast karrio.server.manager.tests.test_manifests.TestManifestCarrierSelector.test_create_manifest_without_carrier_id_is_deterministic`
  Expected: FAIL/non-deterministic — without `.order_by()` the queryset returns connections in DB-insertion/PK order, so `carrier_id` resolves to `"usps"` (first-created), not `"usps_secondary"`.

- [ ] **Step 3: Implementation.**
  In `modules/core/karrio/server/core/gateway.py`, change the `COMBINE RESULTS` block. Current (lines ~194–201):

```python
        if system_only:
            # Only brokered connections (system connection enablements)
            connections = list(brokered_queryset.distinct())
        else:
            # Combine both types
            carriers = list(carrier_queryset.distinct())
            brokered = list(brokered_queryset.distinct())
            connections = carriers + brokered
```

  to apply a stable order before materializing:

```python
        if system_only:
            # Only brokered connections (system connection enablements)
            connections = list(brokered_queryset.order_by("-created_at").distinct())
        else:
            # Combine both types — order deterministically so the no-carrier_id
            # fallback picks a stable connection (newest wins) across DB backends.
            carriers = list(carrier_queryset.order_by("-created_at").distinct())
            brokered = list(brokered_queryset.order_by("-created_at").distinct())
            connections = carriers + brokered
```

  Verify `CarrierConnection` and `BrokeredConnection` both expose `created_at` (they inherit the timestamped base — confirm via `grep -n "created_at" modules/core/karrio/server/providers/models/*.py modules/core/karrio/server/core/models.py` before applying; if `BrokeredConnection` lacks `created_at`, use its own model `Meta.ordering` field instead for the brokered queryset, leaving the carrier ordering as `-created_at`).

- [ ] **Step 4: Run — expect PASS.**
  `karrio test --failfast karrio.server.manager.tests.test_manifests.TestManifestCarrierSelector` green. Then run the **full** server suite `./bin/run-server-tests` to confirm the global ordering change does not regress pickup/shipment/rate/tracking connection-resolution tests (they should not depend on undefined ordering, but the broad blast radius of this gateway change demands the full run).

- [ ] **Step 5: Commit.**
  `fix(server): deterministic connection ordering (-created_at) for no-carrier_id fallback`

---

### Task: B2 — `_decode_pdf` helper + 422 on both download routes — P0

Both download routes blindly `base64.b64decode(self.document or "")` and stream it. A NULL/empty/non-PDF stored doc yields a 0-byte/garbage "PDF" (or a 500 on bad base64). Add a module-level `_decode_pdf(value)` (stdlib `base64`/`binascii`): None/empty/whitespace → "missing"; bad base64 (`binascii.Error`) → "invalid"; decoded content not starting with `b"%PDF-"` → "invalid". Use it in the file-stream route (`ManifestDoc.get()` — decode once, reuse in `get_file()`) and the JSON route (`ManifestDocumentDownload.post()`). On missing → 404; on invalid → **422**.

**Files**
- Modify: `modules/manager/karrio/server/manager/views/manifests.py` (add helper near current lines 1–24; rework `ManifestDoc.get`/`get_file` at lines 99–132; rework `ManifestDocumentDownload.post` at lines 148–172)
- Test: `modules/manager/karrio/server/manager/tests/test_manifests.py` (new class `TestManifestDownloadGuards`)

- [ ] **Step 1: Failing test.**
  Append to `test_manifests.py`:

```python
class TestManifestDownloadGuards(TestManifestFixture):
    def test_download_manifest_pdf_file(self):
        manifest = self.create_manifest()
        url = reverse(
            "karrio.server.manager:manifest-docs",
            kwargs=dict(pk=manifest["id"], doc="manifest", format="pdf"),
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/pdf")
        body = b"".join(response.streaming_content) if response.streaming else response.content
        self.assertTrue(body.startswith(b"%PDF"))

    def test_download_manifest_document_invalid_pdf_json_route(self):
        manifest = self.create_manifest(manifest_doc=NON_PDF_B64)
        url = reverse(
            "karrio.server.manager:manifest-document-download",
            kwargs=dict(pk=manifest["id"]),
        )
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_download_manifest_document_invalid_pdf_file_route(self):
        manifest = self.create_manifest(manifest_doc=NON_PDF_B64)
        url = reverse(
            "karrio.server.manager:manifest-docs",
            kwargs=dict(pk=manifest["id"], doc="manifest", format="pdf"),
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_download_manifest_empty_document_json_route(self):
        manifest = self.create_manifest()
        # Force the stored doc empty (simulates a NULL/blank manifest row).
        obj = models.Manifest.objects.get(pk=manifest["id"])
        obj.manifest = ""
        obj.save()
        url = reverse(
            "karrio.server.manager:manifest-document-download",
            kwargs=dict(pk=manifest["id"]),
        )
        response = self.client.post(url)

        self.assertIn(
            response.status_code,
            (status.HTTP_404_NOT_FOUND, status.HTTP_422_UNPROCESSABLE_ENTITY),
        )
        self.assertNotEqual(response.status_code, status.HTTP_200_OK)
```

  Note: `test_download_manifest_empty_document_json_route` sets `manifest=""`. The view filter `manifest__isnull=False` lets `""` through (empty string is not NULL), so the row is found but `_decode_pdf("")` is "missing" → 404. Either 404 or 422 is acceptable per spec ("422/404, not 0-byte PDF"); the assertion accepts both and rejects 200.

- [ ] **Step 2: Run — expect FAIL.**
  `karrio test --failfast karrio.server.manager.tests.test_manifests.TestManifestDownloadGuards`
  Expected: `test_download_manifest_pdf_file` may already pass (valid PDF), but both `invalid_pdf` tests FAIL — current code streams the non-PDF bytes with HTTP 200, and `empty_document` FAILs by returning a 200 0-byte file.

- [ ] **Step 3: Implementation.**
  In `modules/manager/karrio/server/manager/views/manifests.py`, add `binascii` import and the helper after the existing imports (after current line 21), and rework both routes.

  Add near the top (after `from karrio.server.core.utils import validate_resource_token`, current line 21):

```python
import binascii


def _decode_pdf(value: typing.Optional[str]) -> typing.Tuple[typing.Optional[bytes], typing.Optional[str]]:
    """Decode a stored base64 manifest document and validate it is a PDF.

    Returns (content, error) where error is:
      - "missing" when the value is None/empty/whitespace
      - "invalid" when base64 is undecodable or the bytes are not a PDF
      - None on success (content is the decoded PDF bytes)
    """
    if value is None or not str(value).strip():
        return None, "missing"
    try:
        content = base64.b64decode(value)
    except (binascii.Error, ValueError):
        return None, "invalid"
    if not content[:5] == b"%PDF-":
        return None, "invalid"
    return content, None
```

  Add `import typing` at the top alongside the existing stdlib imports (`import base64` / `import io` at current lines 1–2):

```python
import base64
import binascii
import io
import typing
```

  Rework `ManifestDoc` (current lines 99–132) — decode once in `get`, reuse in `get_file`, return 422 on invalid:

```python
class ManifestDoc(AccessMixin, django_downloadview.VirtualDownloadView):
    @openapi.extend_schema(exclude=True)
    def get(self, req: request.Request, pk: str, doc: str = "manifest", format: str = "pdf", **kwargs):
        """Retrieve a manifest file."""
        error = validate_resource_token(req, "manifest", [pk], "manifest")
        if error:
            return error

        query_params = req.GET.dict()

        self.manifest = models.Manifest.objects.filter(pk=pk, manifest__isnull=False).first()

        if self.manifest is None:
            return response.Response(
                {"errors": [{"message": f"Manifest '{pk}' not found or has no document"}]},
                status=status.HTTP_404_NOT_FOUND,
            )

        content, decode_error = _decode_pdf(getattr(self.manifest, doc, None))
        if decode_error == "missing":
            return response.Response(
                {"errors": [{"message": f"Manifest '{pk}' has no document"}]},
                status=status.HTTP_404_NOT_FOUND,
            )
        if decode_error == "invalid":
            return response.Response(
                {"errors": [{"message": f"Manifest '{pk}' document is not a valid PDF"}]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        self._content = content
        self.name = f"{doc}_{self.manifest.id}.{format}"

        self.preview = "preview" in query_params
        self.attachment = "download" in query_params

        resp = super().get(req, pk, doc, format, **kwargs)
        resp["X-Frame-Options"] = "ALLOWALL"
        return resp

    def get_file(self):
        buffer = io.BytesIO()
        buffer.write(self._content)

        return base.ContentFile(buffer.getvalue(), name=self.name)
```

  Rework `ManifestDocumentDownload.post` (current lines 148–172) to add the 422 invalid branch (keep the 404 not-found branch):

```python
    def post(self, req: request.Request, pk: str):
        """
        Retrieve a manifest document as base64 encoded content.
        """
        manifest = models.Manifest.access_by(req).filter(pk=pk, manifest__isnull=False).first()

        if manifest is None:
            return response.Response(
                {"errors": [{"message": f"Manifest '{pk}' not found or has no document"}]},
                status=status.HTTP_404_NOT_FOUND,
            )

        _content, decode_error = _decode_pdf(manifest.manifest)
        if decode_error == "missing":
            return response.Response(
                {"errors": [{"message": f"Manifest '{pk}' not found or has no document"}]},
                status=status.HTTP_404_NOT_FOUND,
            )
        if decode_error == "invalid":
            return response.Response(
                {"errors": [{"message": f"Manifest '{pk}' document is not a valid PDF"}]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        # Build the GET URL for the document
        doc_url = f"/v1/manifests/{pk}/manifest.pdf"

        return response.Response(
            ShippingDocument(
                {
                    "category": "manifest",
                    "format": "PDF",
                    "base64": manifest.manifest,
                    "url": doc_url,
                }
            ).data
        )
```

  Confirm `status.HTTP_422_UNPROCESSABLE_ENTITY` exists in the imported `rest_framework.status` (it does in DRF ≥3.x). The `Content-Type: application/pdf` on the file route comes from `VirtualDownloadView` inferring from the `.pdf` filename — unchanged from the working path; the `test_download_manifest_pdf_file` test locks it.

- [ ] **Step 4: Run — expect PASS.**
  `karrio test --failfast karrio.server.manager.tests.test_manifests.TestManifestDownloadGuards` green; `TestManifestDocumentDownload.test_download_manifest_document` (valid PDF JSON route) still green; full module green.

- [ ] **Step 5: Commit.**
  `fix(server): guard manifest document downloads — _decode_pdf, 422 on invalid PDF`

---

### Task: B7 — Tenant isolation (cross-org) regression tests — P0

Lock that a manifest cannot be created against, nor downloaded from, another org's shipment/manifest. The serializer already scopes via `models.Shipment.access_by(context)` and the views via `Manifest.access_by(req)` — these tests prove it and guard against regressions from B1/B2/B3.

**Files**
- Test only: `modules/manager/karrio/server/manager/tests/test_manifests.py` (new class `TestManifestTenantIsolation`)
- No source change expected (existing `access_by` scoping). If a test fails, that is a real isolation bug → escalate per 3-Strikes before "fixing" by widening scope.

- [ ] **Step 1: Failing/guard test.**
  Append to `test_manifests.py`:

```python
class TestManifestTenantIsolation(TestManifestFixture):
    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        from django.contrib.auth import get_user_model
        from karrio.server.user.models import Token

        cls.other_user = get_user_model().objects.create_user("other@example.com", "test")
        cls.other_token = Token.objects.create(user=cls.other_user, test_mode=True)

    def test_create_manifest_cross_org_shipment_400(self):
        # self.shipment belongs to cls.user; the other user must not manifest it.
        manifest_url = reverse("karrio.server.manager:manifest-list")
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.other_token.key)
        manifest_data = {
            "carrier_name": "usps",
            "shipment_ids": [self.shipment.id],
            "address": self.usps_address(),
        }
        with patch("karrio.server.core.gateway.utils.identity") as mock:
            response = self.client.post(manifest_url, manifest_data)
            mock.assert_not_called()

        # Other user has no USPS connection -> 404 (no manifest-capable carrier);
        # crucially never 201, and the shipment is never manifested.
        self.assertIn(
            response.status_code,
            (status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND),
        )
        self.assertFalse(models.Shipment.objects.get(pk=self.shipment.id).manifest_id)

    def test_download_manifest_cross_org_404(self):
        manifest = self.create_manifest()  # created as cls.user
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.other_token.key)
        url = reverse(
            "karrio.server.manager:manifest-document-download",
            kwargs=dict(pk=manifest["id"]),
        )
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
```

  Note: `ManifestDoc.get` (file-stream route) filters `Manifest.objects.filter(...)` *without* `access_by` and relies on `validate_resource_token`. If `test_download_manifest_cross_org_404` against the **file** route is desired, add a variant hitting `manifest-docs` and assert it does not return the PDF to the other org; if it leaks, that is a real finding — report it (do not silently patch the token logic without confirming the intended trust model, since `manifest.pdf` may be deliberately token-gated rather than org-gated). The JSON route (`ManifestDocumentDownload.post`) uses `access_by(req)` and must 404 cross-org.

- [ ] **Step 2: Run — expect PASS (these are guard tests).**
  `karrio test --failfast karrio.server.manager.tests.test_manifests.TestManifestTenantIsolation`
  Expected: PASS immediately (scoping already in place). If `test_download_manifest_cross_org_404` FAILS → genuine isolation regression; stop and report per 3-Strikes, do not widen scope blindly.

- [ ] **Step 3: Implementation.**
  None expected. If a guard test fails, the fix is to confirm `access_by` is used on the failing route and restore it — but only after confirming the intended trust model with the user.

- [ ] **Step 4: Run — expect PASS.**
  `karrio test --failfast karrio.server.manager.tests.test_manifests` → entire module green.

- [ ] **Step 5: Commit.**
  `test(server): cross-org isolation guards for manifest create + download`

---

### Final gate (run before claiming B complete)

- [ ] **Full server suite:** `./bin/run-server-tests` → all green (B4's gateway-ordering change has broad blast radius; this run is mandatory, not optional).
- [ ] **Regression check:** the existing Canada Post / Australia Post manifest-create flows in the wider server suite remain green (the optional `carrier_id` schema field is additive; `ManifestData` consumers that omit it are unaffected).
- [ ] **Live-path sanity:** the real USPS end-to-end path still returns 201 + valid `%PDF` (the `_decode_pdf` `b"%PDF-"` check matches the real captured doc; the 93688-byte real manifest from the committed fix passes the guard).
- [ ] Hand off to **E2** (TS-type regen from the B3 schema change) which unblocks DX **C2**.

**Open coordination notes for the orchestrator**
- B3 must land and TS types regenerate (E2) **before** DX C2 starts — strict sequence per spec Risks.
- 422 is a new status code on the manifest-document endpoints — flag for reviewer in the PR body (E1), semantically correct (row found, payload unrenderable).
- The cap key map (`usps`/`usps_international` → 1000) is a documented assumption (v3 OpenAPI unconfirmed) — note in PR body; no connector reads it.
- `_decode_pdf` and the partial-drop diff are intentionally USPS-path-scoped here but generalize to CP #757 — out of scope per spec, do not back-apply.