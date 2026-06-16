# USPS Pre-Shipment Tracker Status → `pending` — Design

**Date:** 2026-06-16
**Type:** Bug fix (connector status mapping)
**Decision:** Minimal scope · normal deploy + self-heal · fork only.

## Problem

When a USPS shipping label is purchased, Karrio auto-creates a tracker. USPS returns a
**pre-shipment** tracking response — top-level `statusCategory` "Pre-Shipment" and a single
event `GX` "Shipping Label Created, USPS Awaiting Item" — meaning USPS has generated the
label but does **not** yet physically hold the package.

The USPS connector mis-labels this as `in_transit`, which propagates to the shipment and
**blocks label cancellation with HTTP 409** (the cancel state machine allows only
`created`/`draft`). It also corrupts pickup reporting (everything looks "picked up").

## Root Cause (verified end-to-end in live code)

```
USPS pre-shipment label
  statusCategory = "Pre-Shipment", event GX "Shipping Label Created, USPS Awaiting Item"
    │
    ▼  modules/connectors/usps/karrio/providers/usps/tracking.py:31-39
       next((s.name for s in TrackingStatus if <substring match of status/statusCategory>),
            TrackingStatus.in_transit.name)
       → NO member matches  → defaults to in_transit          ◄── ROOT CAUSE
    │      (units.py:299-307 TrackingStatus has no `pending` member)
    ▼  modules/core/karrio/server/core/utils.py:383
       CREATED-guard only fires for ≤1 event coded "CREATED"; USPS uses "GX" → bypassed
    ▼  utils.py:386-389  connector said in_transit → trusted → tracker.status = in_transit
    ▼  modules/manager/.../serializers/tracking.py:211-212
       propagation else-branch → shipment.status = in_transit
    ▼  modules/manager/.../serializers/shipment.py:813-816
       can_mutate_shipment allows cancel only for [created, draft] → HTTP 409 ❌
```

**Why the core layer doesn't save us:** `compute_tracking_status` (utils.py:376-389) only
returns `pending` when there are 0 events or exactly 1 event coded `"CREATED"`. A USPS
pre-shipment response's event is coded `"GX"` (not `"CREATED"`), so even a single-event
response fails the `code == "CREATED"` check, bypasses the guard, and trusts the connector's
`in_transit`.

## Existing Code Analysis

| File / line | Role | Touched? |
|---|---|---|
| `modules/connectors/usps/karrio/providers/usps/units.py:299-307` | `TrackingStatus` enum — **missing `pending`** | **YES** (add member) |
| `modules/connectors/usps/karrio/providers/usps/tracking.py:31-39, 68-75` | status matching (top-level + per-event), defaults `in_transit` | no (works once enum has `pending`) |
| `modules/core/karrio/server/core/serializers.py:29-41` | core `TrackerStatus` — **has `pending`, no `pre_transit`** | no (consumed) |
| `modules/core/karrio/server/core/utils.py:386-389` | `compute_tracking_status` trusts non-None mapped status | no (consumed) |
| `modules/manager/.../serializers/tracking.py:198-199` | `pending` tracker **preserves** shipment `created` | no (consumed) |
| `modules/manager/.../serializers/shipment.py:813-816` | cancel guard `[created, draft]` | no (consumed) |
| `modules/connectors/usps/tests/usps/test_tracking.py` | fixtures; already carries the GX event (line 408) | **YES** (1 new test + 1 fixture line) |

**Reference precedent (reuse):** GitHub `karrioapi/karrio` **PR #1050 (MERGED)** —
`fix(landmark): reclassify early fulfillment events as pending to allow label cancellation`.
Identical bug class and motivation; touched only the connector enum + tests. This design
mirrors it for USPS.

## Fix

Add `pending` as the **first** member of `TrackingStatus` in `units.py` (mirrors the
Landmark/Asendia convention where `pending` leads):

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

**Keyword rationale (panel-vetted, dual-front matching):**
- `"pre-shipment"` → matches top-level `statusCategory` "Pre-Shipment".
- `"shipping label created"` / `"usps awaiting item"` → match the GX event text **and** the
  top-level `status` (which mirrors the latest event). Matching on two independent fields
  means the fix lands even if one string varies across USPS products.
- Deliberately **not** bare `"created"` or `"awaiting"` — `"awaiting"` collides with
  "awaiting delivery scan" (a moved package); `"created"` is too broad.

**Member name MUST be `pending`, never `pre_transit`:** core `TrackerStatus`
(serializers.py:29-41) has no `pre_transit`; `TrackerStatus.map("pre_transit").value is None`,
so a `pre_transit` member would silently fall through to `in_transit` (a no-op "fix").

**Resulting data flow:**
```
pre-shipment → connector "pending"
  → utils.py:386-387  TrackerStatus.map("pending") non-None → tracker.status = pending
  → tracking.py:198-199  pending PRESERVES shipment "created"
  → shipment.py:813-816  created → cancel ALLOWED ✓
```

**No regression risk:** if a keyword fails to match a real pre-shipment string, the result
is the *current* behavior (`in_transit`) — never worse.

## Testing (TDD, `unittest` — never pytest)

1. **New** `test_parse_pre_shipment_pending_response`: a pure pre-shipment USPS payload
   (`statusCategory: "Pre-Shipment"`, single `GX` "Shipping Label Created, USPS Awaiting Item"
   event) → assert tracker-level `status == "pending"`, that event's `status == "pending"`,
   and `delivered == False`. Add the matching `PreShipment...` raw + parsed fixtures.
2. **Update** the existing delivered fixture's expected `ParsedResponse`
   (`test_tracking.py` ~line 159-166): its `GX` event gains `"status": "pending"`. This is the
   only existing assertion that shifts — the tracker-level status stays `delivered`
   (`statusCategory: "Delivered"` still matches `delivered` first). Mirrors the exact fixture
   line PR #1050 updated.
3. Full connector suite green:
   `python -m unittest discover -v -f modules/connectors/usps/tests`.

## Verify-then-code

The TDD fixture is built to the documented USPS pre-shipment shape (vendor spec
`usps-tracking.yaml:881-886` defines `status`/`statusCategory` as free strings deferring to
USPS Pub 199 G-4/G-5 — no inline enum to assert against). The **definitive live verification
is the rollout**: after deploy, confirm one currently-stuck pre-shipment tracker flips
`in_transit → pending` on its next refresh and becomes cancellable. If it does not flip,
capture that tracker's raw USPS top-level `status`/`statusCategory` and adjust the keyword
list (no regression while iterating).

## Rollout

- Branch `fix/usps-pending-status` off the fork.
- Commit (enum + tests) → rebuild Karrio image → redeploy `karrio.api` + `karrio.worker`.
- Existing stuck trackers self-heal on the next `TRACKING_PULSE` (≤ 2h):
  pre-shipment → `pending` → cancellable. No manual data backfill.
- **Fork only** (`zggit/karrio`); no upstream PR.

## Out of Scope (deferred to separate specs)

- **Secondary defect (cosmetic):** `picked_up` contains `"acceptance"`, which does not
  substring-match USPS's real `"Accepted"` / `"USPS in possession of item"` (code `03`). An
  accepted-only package falls to the `in_transit` default. No operational impact — once USPS
  holds the item it is correctly non-cancellable, and the Tongtool pickup report already
  counts `in_transit` as picked-up.
- Event-code-based matching (GX→pending, 03→picked_up) defense-in-depth in `tracking.py`.
- A core `compute_tracking_status` PRE_TRANSIT guard spanning all connectors.

## Success Criteria

- A USPS pre-shipment tracker reports `status == "pending"`; its shipment stays `created`.
- That shipment's label can be cancelled (no HTTP 409).
- All USPS connector tests pass; no other connector/test regresses.
- A real prod pre-shipment tracker self-heals `in_transit → pending` after deploy.
