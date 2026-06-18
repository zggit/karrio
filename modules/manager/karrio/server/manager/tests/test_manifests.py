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
                "print_format": None,
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


class TestManifestPartialDrop(TestManifestFixture):
    """B1 — the partial-drop guard must report the exact missing shipment ids."""

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
        # shipment #2 is already manifested elsewhere -> its id must be reported
        # missing. The realistic case the old len()-based guard could not catch:
        # one shipment survives (self.shipment), one is dropped (other).
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
        # the surviving shipment id must NOT be reported as missing.
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
        # A manifest-capable connection of a DIFFERENT carrier (usps_international)
        # resolves fine, but the shipment was purchased with usps -> the id is
        # dropped by the carrier_code filter and must be reported missing (400),
        # not silently manifested under the wrong carrier.
        providers.CarrierConnection.objects.create(
            carrier_code="usps_international",
            carrier_id="usps_intl",
            test_mode=True,
            active=True,
            created_by=self.user,
            credentials=dict(
                client_id="test",
                client_secret="test",
                account_number="000000000",
                account_type="EPS",
            ),
        )
        manifest_url = reverse("karrio.server.manager:manifest-list")
        manifest_data = {
            "carrier_name": "usps_international",
            "shipment_ids": [self.shipment.id],
            "address": self.usps_address(),
        }
        with patch("karrio.server.core.gateway.utils.identity") as mock:
            response = self.client.post(manifest_url, manifest_data)
            mock.assert_not_called()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(self.shipment.id, json.dumps(json.loads(response.content)))


class TestManifestCarrierSelector(TestManifestFixture):
    """B3 — optional carrier_id selects a specific connection on multi-account."""

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

    def _post_with_carrier_id(self, carrier_id: str):
        manifest_url = reverse("karrio.server.manager:manifest-list")
        manifest_data = {
            "carrier_name": "usps",
            "carrier_id": carrier_id,
            "shipment_ids": [self.shipment.id],
            "address": self.usps_address(),
        }
        response_value = (
            ManifestDetailsModel(
                carrier_id=carrier_id,
                carrier_name="usps",
                doc=dict(manifest=VALID_PDF_B64),
                meta=dict(manifestNumber="42708517000099"),
            ),
            [],
        )
        with patch("karrio.server.core.gateway.utils.identity") as mock:
            mock.return_value = response_value
            response = self.client.post(manifest_url, manifest_data)
        return response

    def test_create_manifest_targets_specific_connection(self):
        # Two USPS connections exist. carrier_id selects the FIRST-created one
        # ("usps"), which is NOT the connection the unfiltered fallback would
        # resolve (the fallback returns "usps_secondary" first). Targeting the
        # non-default connection makes this a genuine discriminator that fails
        # when carrier_id is dropped by the serializer.
        response = self._post_with_carrier_id("usps")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        manifest = models.Manifest.objects.get(pk=json.loads(response.content)["id"])
        self.assertEqual(manifest.carrier.get("carrier_id"), "usps")

    def test_create_manifest_carrier_id_selects_secondary(self):
        # The symmetric case: carrier_id of the second connection selects it.
        response = self._post_with_carrier_id("usps_secondary")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        manifest = models.Manifest.objects.get(pk=json.loads(response.content)["id"])
        self.assertEqual(manifest.carrier.get("carrier_id"), "usps_secondary")

    def test_create_manifest_without_carrier_id_still_works(self):
        # Omitting carrier_id preserves current behavior: the manifest still
        # resolves a USPS connection and is created successfully.
        manifest = self.create_manifest()
        self.assertIn("id", manifest)
        self.assertEqual(
            models.Manifest.objects.get(pk=manifest["id"]).carrier.get("carrier_code"),
            "usps",
        )


class TestManifestDownloadGuards(TestManifestFixture):
    """B2 — _decode_pdf guards both download routes: 200 for a real PDF,
    422 for a present-but-non-PDF payload, never a blank/garbage 200."""

    def test_download_manifest_pdf_file(self):
        # The untested file-stream route (manifest.pdf) happy path.
        manifest = self.create_manifest()
        url = reverse(
            "karrio.server.manager:manifest-docs",
            kwargs=dict(pk=manifest["id"], doc="manifest", format="pdf"),
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response["Content-Type"].startswith("application/pdf"))
        body = (
            b"".join(response.streaming_content)
            if getattr(response, "streaming", False)
            else response.content
        )
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

        # 422 or 404 are both acceptable (row found, payload missing) — never a
        # 0-byte 200.
        self.assertIn(
            response.status_code,
            (status.HTTP_404_NOT_FOUND, status.HTTP_422_UNPROCESSABLE_ENTITY),
        )
        self.assertNotEqual(response.status_code, status.HTTP_200_OK)


class TestManifestTenantIsolation(TestManifestFixture):
    """B7 — a manifest cannot be created against, nor downloaded from, another
    org's shipment/manifest. The serializer scopes via Shipment.access_by and
    the JSON download route via Manifest.access_by; these guard tests lock that
    isolation against regressions from B1/B2/B3. No source change expected.

    The OSS default access method is ``WideAccess`` (``Q()`` — every row visible
    to every authenticated user; the existing ``test_superuser_can_delete_any_
    connection`` documents this). Real org isolation is supplied by the EE/orgs
    layer overriding ``KARRIO_ENTITY_ACCESS_METHOD``. To exercise the genuine
    ``access_by`` isolation path deterministically — independent of which access
    method the deployment configures — these tests pin the resolved access
    filter to ``CreatorAccess`` (scope by ``created_by``). ``get_access_filter``
    is bound once at import in ``core.models.base``, so ``@override_settings``
    alone cannot swap it; the module attribute is patched directly instead."""

    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        from django.contrib.auth import get_user_model
        from karrio.server.user.models import Token

        # A second user/org — no carrier connections of their own.
        cls.other_user = get_user_model().objects.create_user("other@example.com", "test")
        cls.other_token = Token.objects.create(user=cls.other_user, test_mode=True)

    def setUp(self) -> None:
        super().setUp()
        # Scope access_by by created_by for the duration of each test so the
        # isolation path is actually exercised (default WideAccess would not).
        from karrio.server.core.middleware import CreatorAccess

        patcher = patch(
            "karrio.server.core.models.base.get_access_filter",
            new=CreatorAccess(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

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

        # The other user has no USPS connection -> carrier resolution 404, or the
        # cross-org shipment is dropped by access_by -> 400. Crucially never 201,
        # and the shipment is never manifested.
        self.assertIn(
            response.status_code,
            (status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND),
        )
        self.assertFalse(models.Shipment.objects.get(pk=self.shipment.id).manifest_id)

    def test_download_manifest_cross_org_404(self):
        # Manifest created as cls.user; the other org must not download it.
        manifest = self.create_manifest()
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.other_token.key)
        url = reverse(
            "karrio.server.manager:manifest-document-download",
            kwargs=dict(pk=manifest["id"]),
        )
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
