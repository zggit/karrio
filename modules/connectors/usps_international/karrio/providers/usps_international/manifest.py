"""Karrio USPS manifest API implementation."""

import time
import base64
import binascii
import datetime

import karrio.core.models as models
import karrio.lib as lib
import karrio.providers.usps_international.error as error
import karrio.providers.usps_international.utils as provider_utils
import karrio.schemas.usps_international.scan_form_request as usps


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


def _extract_details(
    data: dict,
    settings: provider_utils.Settings,
    ctx: dict = None,
) -> models.ManifestDetails:
    # USPS multipart success: JSON part "SCANFormMetaData" (note the capital D) holds the
    # metadata (manifestNumber, trackingNumbers) as flat fields; PDF part is "SCANFormImage".
    metadata = data.get("SCANFormMetaData") or data.get("SCANFormMetadata") or {}
    pdf = data.get("SCANFormImage") or data.get("label")

    return models.ManifestDetails(
        carrier_id=settings.carrier_id,
        carrier_name=settings.carrier_name,
        doc=models.ManifestDocument(manifest=pdf),
        meta=dict(
            manifestNumber=metadata.get("manifestNumber"),
            trackingNumbers=metadata.get("trackingNumbers") or (ctx or {}).get("shipment_identifiers"),
        ),
    )


def _validate_mailing_date(value: str) -> str:
    """USPS scan-form mailingDate must be within today..today+7. Hard-fail
    (no silent overwriteMailingDate auto-snap) so the caller corrects the date."""
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


def manifest_request(
    payload: models.ManifestRequest,
    settings: provider_utils.Settings,
) -> lib.Serializable:
    address = lib.to_address(payload.address)
    options = lib.units.Options(
        payload.options,
        option_type=lib.units.create_enum(
            "ManifestOptions",
            # fmt: off
            {
                "shipment_date": lib.OptionEnum("shipment_date"),
                "usps_ignore_bad_address": lib.OptionEnum("ignoreBadAddress", bool),
                "usps_overwrite_mailing_date": lib.OptionEnum("overwriteMailingDate", bool),
                "usps_destination_entry_facility_type": lib.OptionEnum("destinationEntryFacilityType", str),
            },
            # fmt: on
        ),
    )

    mailing_date = _validate_mailing_date(
        options.shipment_date.state or time.strftime("%Y-%m-%d")
    )

    # map data to convert karrio model to usps specific type
    request = usps.ScanFormRequestType(
        form="5630",
        imageType="PDF",
        labelType="8.5x11LABEL",
        mailingDate=mailing_date,
        overwriteMailingDate=options.usps_overwrite_mailing_date.state or False,
        entryFacilityZIPCode=address.postal_code,
        destinationEntryFacilityType=lib.identity(options.usps_destination_entry_facility_type.state or "NONE"),
        shipment=usps.ShipmentType(
            trackingNumbers=payload.shipment_identifiers,
        ),
        fromAddress=usps.FromAddressType(
            ignoreBadAddress=options.usps_ignore_bad_address.state or False,
            streetAddress=address.address_line1,
            secondaryAddress=address.address_line2,
            city=address.city,
            state=address.state_code,
            ZIPCode=lib.to_zip5(address.postal_code) or "",
            ZIPPlus4=lib.to_zip4(address.postal_code) or "",
            urbanization=None,
            firstName=lib.identity(lib.failsafe(lambda: (address.person_name or "").split(" ")[0]) or ""),
            lastName=lib.failsafe(lambda: (address.person_name or "").split(" ")[1]),
            firm=address.company_name,
        ),
    )

    return lib.Serializable(request, lib.to_dict, dict(shipment_identifiers=payload.shipment_identifiers))
