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

# Documented per-carrier max shipments per SCAN form. 1000 is an assumption
# (the USPS scan-forms/v3 OpenAPI does not publish the limit); revisit if USPS
# confirms a different cap, and override per-connection later if needed. Reject
# over-cap before any DB query or OAuth/gateway round-trip.
MANIFEST_MAX_SHIPMENTS = {"usps": 1000, "usps_international": 1000}


@serializers.owned_model_serializer
class ManifestSerializer(core.ManifestData):
    def create(self, validated_data: dict, context: serializers.Context, **kwargs) -> models.Manifest:
        data = validated_data.copy()
        shipment_ids = list(set(data.pop("shipment_ids")))
        carrier_name = data["carrier_name"]

        # Reject over-cap requests before any DB query / gateway round-trip.
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

        # Optional carrier_id targets a specific connection on multi-account
        # setups; the gateway resolves it by id or carrier_id (id-or-name).
        # Pop it from data so it is not forwarded into ManifestRequest.map.
        carrier_id = data.pop("carrier_id", None)
        carrier_filter = {"carrier_id": carrier_id} if carrier_id else {}
        carrier = gateway.Connections.first(
            context=context,
            carrier_name=carrier_name,
            **{"raise_not_found": True, **DEFAULT_CARRIER_FILTER, **carrier_filter},
        )

        # USPS scan-form manifests need a complete US origin address; reject a
        # missing state or non-5-digit ZIP here (400 naming the field) instead of
        # letting it degrade to a late USPS 424.
        if carrier_name in USPS_CARRIERS:
            address = validated_data.get("address") or {}
            missing = [
                field
                for field in ("state_code", "postal_code")
                if not (address.get(field) or "").strip()
            ]
            if missing:
                raise serializers.ValidationError(
                    {
                        field: "This field is required for USPS manifests."
                        for field in missing
                    }
                )
            if not ZIP5.match((address.get("postal_code") or "").strip()):
                raise serializers.ValidationError(
                    {
                        "postal_code": "A valid 5-digit US ZIP code is required for USPS manifests."
                    }
                )

        # Filter shipments by carrier_code in carrier JSON snapshot
        shipments = models.Shipment.access_by(context).filter(
            id__in=shipment_ids,
            manifest__isnull=True,
            carrier__carrier_code=carrier_name,
        )
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

        response = gateway.Manifests.create(
            payload=core.ManifestRequest.map(
                data={
                    **data,
                    "shipment_identifiers": shipment_identifiers,
                    "options": {
                        **data.get("options", {}),
                        "shipments": core.Shipment(shipments, many=True).data,
                    },
                }
            ).data,
            carrier=carrier,
        )

        payload = {
            key: value
            for key, value in core.Manifest(response.manifest).data.items()
            if key in models.Manifest.DIRECT_PROPS
        }
        # Manifest.address is an embedded JSON dict (like Pickup); store the validated
        # dict directly. save_one_to_one_data returns an Address model instance, which is
        # not JSON-serializable into the JSONField (TypeError on Manifest.objects.create).
        address = validated_data.get("address")

        # Merge request_id into meta for request correlation
        from karrio.server.core.middleware import get_request_id

        _request_id = get_request_id()
        _manifest_meta = {
            **(payload.get("meta") or {}),
            **({"request_id": _request_id} if _request_id else {}),
        }

        manifest = models.Manifest.objects.create(
            **{
                **payload,
                "address": address,
                "created_by": context.user,
                "carrier": create_carrier_snapshot(carrier),
                "options": data.get("options", {}),
                "meta": _manifest_meta,
                "test_mode": response.manifest.test_mode,
                "manifest": response.manifest.doc.manifest,
            }
        )
        manifest.shipments.set(shipments)

        return manifest
