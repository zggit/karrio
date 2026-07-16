import karrio.lib as lib
import karrio.server.conf as conf
import karrio.server.core.utils as utils
import karrio.server.tracing.models as models
import karrio.server.serializers as serializers
from karrio.server.core.logging import logger


@utils.error_wrapper
def save_tracing_records(context, tracer: lib.Tracer = None, schema: str = None):
    if conf.settings.PERSIST_SDK_TRACING is False:
        return

    tracer = tracer or getattr(context, "tracer", lib.Tracer())

    # Process Karrio SDK tracing records to persist records of interest.
    @utils.async_wrapper
    @utils.tenant_aware
    def persist_records(**kwarg):
        actor = getattr(context, "user", None)
        if len(tracer.records) == 0 or getattr(actor, "id", None) is None:
            return

        try:
            records = []
            exists = lib.identity(
                models.TracingRecord.access_by(context)
                .filter(
                    meta__request_log_id__isnull=False,
                    meta__request_log_id=tracer.context.get("request_log_id"),
                )
                .exists()
            )

            if exists:
                return

            for record in tracer.records:
                connection: dict = record.metadata.get("connection")

                records.append(
                    models.TracingRecord(
                        key=record.key,
                        record=record.data,
                        timestamp=record.timestamp,
                        created_by_id=getattr(actor, "id", None),
                        test_mode=connection.get("test_mode", False),
                        meta=lib.to_dict(
                            {
                                "tracer_id": tracer.id,
                                "request_id": tracer.context.get("request_id"),
                                "object_id": tracer.context.get("object_id"),
                                "carrier_account_id": connection.get("id"),
                                "carrier_id": connection.get("carrier_id"),
                                "carrier_name": connection.get("carrier_name"),
                                "request_log_id": tracer.context.get("request_log_id"),
                            }
                        ),
                    )
                )

            saved_records = models.TracingRecord.objects.bulk_create(records)

            if getattr(context, "org", None) is not None:
                serializers.bulk_link_org(saved_records, context)

            logger.info("Tracing records saved successfully", record_count=len(saved_records))
        except Exception as e:
            logger.error("Failed to save tracing records", error=str(e))

    persist_records(schema=schema)


@utils.error_wrapper
def bulk_save_tracing_records(tracer: lib.Tracer, context=None):
    if conf.settings.PERSIST_SDK_TRACING is False:
        return

    if len(tracer.records) == 0 or context is None:
        return

    # Dedupe records already persisted by an earlier call with the same tracer.
    # The tracker-update task calls this once per batch while the carrier-task
    # gateway (and its tracer) is shared across all batches and never cleared,
    # so batch k re-persisted batches 1..k-1 — quadratic row amplification
    # (measured ~29x: 32.5k tracing rows per cycle vs 1.2k real HTTP calls
    # squid-side, 2026-07-16). Record objects are stable across `.records`
    # accesses (future results are cached), so identity is a safe dedupe key;
    # the set lives on the tracer and dies with the task.
    persisted_ids = getattr(tracer, "_persisted_record_ids", None)
    if persisted_ids is None:
        persisted_ids = set()
        tracer._persisted_record_ids = persisted_ids

    records = []
    new_records = []

    for record in tracer.records:
        if id(record) in persisted_ids:
            continue
        logger.debug("Processing tracing record", record_key=record.key, metadata=record.metadata)
        new_records.append(record)
        records.append(
            models.TracingRecord(
                key=record.key,
                record=record.data,
                timestamp=record.timestamp,
                test_mode=getattr(context, "test_mode", False),
                created_by_id=getattr(context.user, "id", None),
                meta=lib.to_dict({"tracer_id": tracer.id, **(record.metadata or {})}),
            )
        )

    if len(records) == 0:
        return

    saved_records = models.TracingRecord.objects.bulk_create(records)
    persisted_ids.update(id(record) for record in new_records)

    if getattr(context, "org", None) is not None:
        serializers.bulk_link_org(saved_records, context)

    logger.info("Tracing records saved successfully", record_count=len(saved_records))


def set_tracing_context(**kwargs):

    from karrio.server.core import middleware

    request = middleware.SessionContext.get_current_request()
    request.tracer.add_context(kwargs)

    _propagate_to_sentry(kwargs)


_SENTRY_TAG_KEYS = {"shipment_id", "tracking_number", "object_id"}


def _propagate_to_sentry(context: dict):
    """Propagate shipment-related context to Sentry tags and structured context."""
    try:
        import sentry_sdk
    except ImportError:
        return

    try:
        for key in _SENTRY_TAG_KEYS:
            value = context.get(key)
            if value:
                sentry_sdk.set_tag(key, value)

        shipment_id = context.get("shipment_id")
        tracking_number = context.get("tracking_number")
        if shipment_id or tracking_number:
            sentry_sdk.set_context(
                "shipment",
                lib.to_dict(
                    {
                        "shipment_id": shipment_id,
                        "tracking_number": tracking_number,
                    }
                ),
            )
    except Exception:
        pass
