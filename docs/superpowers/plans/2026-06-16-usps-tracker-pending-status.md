# USPS Pre-Shipment Tracker Pending-Status Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make USPS pre-shipment labels report tracker status `pending` (not `in_transit`) so the shipment stays `created` and remains cancellable (no HTTP 409).

**Architecture:** Single connector-enum change — add a `pending` member to the USPS `TrackingStatus` enum so pre-shipment status strings stop falling through to the `in_transit` default. Core already maps `pending` → preserves shipment `created`. Mirrors merged upstream PR #1050 (Landmark, identical bug class). One commit: enum + tests together (the enum change shifts one assertion in an existing fixture, so they must land atomically to stay green).

**Tech Stack:** Python, karrio SDK connector framework, `unittest` (NEVER pytest), `import karrio.lib as lib`.

**Spec:** `docs/superpowers/specs/2026-06-16-usps-tracker-pending-status-design.md`

**Environment:** Run from repo root `/Users/moon/Documents/OMS/karrio` after `source bin/activate-env`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `modules/connectors/usps/karrio/providers/usps/units.py` | `TrackingStatus` enum | Add `pending` member (1 line) |
| `modules/connectors/usps/tests/usps/test_tracking.py` | Connector tracking tests + fixtures | Add 1 raw fixture + 1 expected fixture + 1 test method; update GX event in existing `ParsedTrackingResponse` |

Do **not** touch `mapper.py`, `karrio/schemas/usps/*` (generated), `tracking.py`, or any `modules/core` / `modules/manager` file — the fix needs only the enum; the core/manager layers already handle `pending` correctly (verified in the spec).

---

## Task 1: Add `pending` to USPS `TrackingStatus` (TDD)

**Files:**
- Modify: `modules/connectors/usps/karrio/providers/usps/units.py:299-307`
- Test: `modules/connectors/usps/tests/usps/test_tracking.py`

### Step 1: Add the failing test + its fixtures

- [ ] **Add two module-level fixtures** at the end of `modules/connectors/usps/tests/usps/test_tracking.py` (after the existing `ParsedAuthErrorResponse` block). The raw response is a pure pre-shipment payload — top-level `statusCategory: "Pre-Shipment"` with a single `GX` "Shipping Label Created, USPS Awaiting Item" event:

```python
PreShipmentResponse = """{
  "trackingNumber": "9400100000000000000000",
  "mailClass": "USPS Ground Advantage<SUP>&#153;</SUP>",
  "originCity": "SPRINGFIELD GARDENS",
  "originState": "NY",
  "originZIP": "11413",
  "destinationZIP": "34442",
  "status": "Pre-Shipment, USPS Awaiting Item",
  "statusCategory": "Pre-Shipment",
  "statusSummary": "A shipping label has been prepared. USPS is awaiting the item.",
  "trackingEvents": [
    {
      "eventType": "Shipping Label Created, USPS Awaiting Item",
      "eventTimestamp": "2024-11-15T11:32:00",
      "GMTTimestamp": "2024-11-15T16:32:33Z",
      "GMTOffset": "-05:00",
      "eventCountry": null,
      "eventCity": "SPRINGFIELD GARDENS",
      "eventState": "NY",
      "eventZIP": "11413",
      "firm": null,
      "name": null,
      "authorizedAgent": "false",
      "eventCode": "GX",
      "additionalProp": null
    }
  ]
}
"""

ParsedPreShipmentResponse = [
    [
        {
            "carrier_id": "usps",
            "carrier_name": "usps",
            "delivered": False,
            "events": [
                {
                    "code": "GX",
                    "date": "2024-11-15",
                    "description": "Shipping Label Created, USPS Awaiting Item",
                    "location": "SPRINGFIELD GARDENS, 11413, NY",
                    "status": "pending",
                    "time": "11:32 AM",
                    "timestamp": "2024-11-15T11:32:00.000Z",
                }
            ],
            "info": {
                "carrier_tracking_link": "https://tools.usps.com/go/TrackConfirmAction?tLabels=9400100000000000000000",
                "shipment_destination_postal_code": "34442",
                "shipment_origin_postal_code": "11413",
                "shipment_service": "USPS Ground Advantage<SUP>&#153;</SUP>",
            },
            "status": "pending",
            "tracking_number": "9400100000000000000000",
        }
    ],
    [],
]
```

- [ ] **Add the test method** inside `class TestUSPSTracking` (place it right after `test_parse_tracking_response`, ~line 38):

```python
    def test_parse_pre_shipment_pending_response(self):
        with patch("karrio.mappers.usps.proxy.lib.request") as mock:
            mock.return_value = PreShipmentResponse
            parsed_response = karrio.Tracking.fetch(self.TrackingRequest).from_(gateway).parse()
            logger.debug(lib.to_dict(parsed_response))
            self.assertListEqual(lib.to_dict(parsed_response), ParsedPreShipmentResponse)
```

### Step 2: Run the new test — verify it FAILS

- [ ] Run:

```bash
source bin/activate-env
python -m unittest discover -v -f modules/connectors/usps/tests 2>&1 | tail -25
```

Expected: `test_parse_pre_shipment_pending_response` FAILS. The diff shows the actual tracker `status` is `"in_transit"` (not `"pending"`) and the GX event has no `"status": "pending"` — this is the bug, reproduced.

> **Reconcile note (standard karrio fixture practice):** if the ONLY differences vs. `ParsedPreShipmentResponse` are incidental formatting (an extra/absent `info` key, a date/time render), copy the ACTUAL parsed dict from the failure output into `ParsedPreShipmentResponse`, **keeping `delivered: False`** and leaving `status` as whatever it currently is. The invariant this test must end up asserting is: tracker `status == "pending"` and the GX event `status == "pending"`. Do not weaken those two.

### Step 3: Add the `pending` enum member

- [ ] In `modules/connectors/usps/karrio/providers/usps/units.py`, change the `TrackingStatus` class (lines 299-307) to add `pending` as the **first** member (matching the Landmark/Asendia convention where `pending` leads):

```python
class TrackingStatus(lib.Enum):
    pending = ["pre-shipment", "shipping label created", "usps awaiting item"]
    on_hold = ["on hold"]
    delivered = ["delivered"]
    in_transit = ["in transit"]
    delivery_failed = ["delivery failed"]
    delivery_delayed = ["delivery delayed"]
    out_for_delivery = ["out for delivery"]
    ready_for_pickup = ["ready for pickup"]
    picked_up = ["PICKED_UP", "PU", "picked up", "origin scan", "acceptance"]
```

Why exactly these three strings (do not change them):
- `"pre-shipment"` matches the top-level `statusCategory` `"Pre-Shipment"`.
- `"shipping label created"` / `"usps awaiting item"` match the GX event text and the top-level `status`.
- NOT bare `"created"` (too broad) or `"awaiting"` (collides with "awaiting delivery scan" on a moved package).
- The member MUST be named `pending` — core `TrackerStatus` has no `pre_transit`, so a `pre_transit` member would map to `None` and silently no-op.

### Step 4: Run the new test — verify it PASSES

- [ ] Run:

```bash
python -m unittest discover -v -f modules/connectors/usps/tests 2>&1 | tail -25
```

Expected: `test_parse_pre_shipment_pending_response` now PASSES (tracker `status == "pending"`, GX event `status == "pending"`).

### Step 5: Run the FULL USPS suite — observe the existing delivered fixture now fails

- [ ] The same command above also runs `test_parse_tracking_response` (the delivered fixture). Adding `pending` makes that fixture's GX event (eventType "Shipping Label Created, USPS Awaiting Item") now resolve to event-level `status == "pending"`, which the expected `ParsedTrackingResponse` does not yet include.

Expected: `test_parse_tracking_response` FAILS with a diff showing the GX event gained `"status": "pending"`. (The tracker-level status stays `"delivered"` — `statusCategory: "Delivered"` still matches `delivered` first; only that one event dict changes.)

### Step 6: Update the existing delivered fixture's GX event

- [ ] In `modules/connectors/usps/tests/usps/test_tracking.py`, inside `ParsedTrackingResponse`, find the GX event (around line 159-166) and add `"status": "pending"` (keys stay alphabetical — between `location` and `time`):

```python
                {
                    "code": "GX",
                    "date": "2024-11-15",
                    "description": "Shipping Label Created, USPS Awaiting Item",
                    "location": "SPRINGFIELD GARDENS, 11413, NY",
                    "status": "pending",
                    "time": "11:32 AM",
                    "timestamp": "2024-11-15T11:32:00.000Z",
                },
```

This is the only edit to `ParsedTrackingResponse` — leave the code-03 "USPS in possession of item" event and all others unchanged (minimal scope does not touch `picked_up`).

### Step 7: Run the FULL USPS suite — verify everything PASSES

- [ ] Run:

```bash
python -m unittest discover -v -f modules/connectors/usps/tests 2>&1 | tail -15
```

Expected: all USPS tracking tests pass (the previously-passing ~34 plus the new one), `OK`. If any OTHER fixture unexpectedly changed, STOP and report — only the GX event should have shifted.

### Step 8: Commit

- [ ] Commit enum + tests together (atomic), no AI co-author / footer (project rule):

```bash
git add modules/connectors/usps/karrio/providers/usps/units.py \
        modules/connectors/usps/tests/usps/test_tracking.py
git commit -m "fix(usps): reclassify pre-shipment labels as pending to allow cancellation"
```

---

## Self-Review

**1. Spec coverage:**
- Root cause / fix (add `pending` member) → Task 1 Step 3. ✅
- Keyword set + rationale + `pending`-not-`pre_transit` → Step 3 notes. ✅
- New pre-shipment test → Steps 1-4. ✅
- Existing delivered-fixture GX update → Steps 5-6. ✅
- Full suite green → Step 7. ✅
- No-regression (only GX event shifts) → Steps 5, 7 guard. ✅
- Verify-then-code (live confirmation) → deferred to deployment, called out in the spec; not an implementation-task item. ✅
- Out-of-scope items (secondary `picked_up`, event-code helper, core guard) → explicitly untouched (File Structure note). ✅

**2. Placeholder scan:** No TBD/TODO. The one judgment step (Step 2 reconcile note) is bounded by an explicit invariant (`status == "pending"` at tracker + GX-event level), not an open "fill in details." ✅

**3. Type/name consistency:** `pending`, `PreShipmentResponse`, `ParsedPreShipmentResponse`, `test_parse_pre_shipment_pending_response`, `ParsedTrackingResponse` used consistently across all steps. Enum member name `pending` matches the core `TrackerStatus.pending` it maps to. ✅

---

## Execution Handoff

Single atomic task. Execute via **superpowers:subagent-driven-development** (user already chose this). After the implementer commits, run the two-stage review (spec compliance → code quality). Deployment (rebuild/redeploy the Karrio image, self-heal) is a separate ops step after the branch is green — not part of this plan.
