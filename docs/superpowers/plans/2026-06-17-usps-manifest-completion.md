# USPS SCAN-Form Manifest Completion (Scope B) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Task detail (full TDD steps + code) lives in the three workstream files referenced below; this master file is the **execution order + dependency index**. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Harden the already-working USPS SCAN-form manifest (connector + server + dashboard + tests) to upstream-PR quality, closing the four Canada-Post-#757 failure classes for USPS.

**Architecture:** Three workstreams. **Connector** (usps + usps_international, byte-identical): robust multipart parsing + hard-fail date validation. **Server**: download blank-PDF 422 guard, partial-drop 400, multi-account `carrier_id`, validation, and repair of the broken manifest test. **Dashboard**: per-connection `manifest_required` opt-in (default OFF), success confirmation, connection selector.

**Tech Stack:** Python (`karrio.lib`, `unittest`, Django REST), TypeScript/React (Next.js dashboard). Worktree `feat/usps-scanform-manifest` off `production`.

**Spec:** `docs/superpowers/specs/2026-06-17-usps-manifest-completion-design.md`. Decisions locked there: mailingDate out-of-window = hard-fail 400; `manifest_required` = per-connection opt-in default OFF; download error = 422; connection selector = top-level `carrier_id`; real captured fixture = `/tmp/real_scanform.txt`; cap mechanism with `usps:1000` assumption.

**Task detail files (read the matching section for each task's full code):**
- `2026-06-17-usps-manifest-completion-connector.md` (tasks A0–A7)
- `2026-06-17-usps-manifest-completion-server.md` (tasks B0–B7)
- `2026-06-17-usps-manifest-completion-dashboard.md` (tasks C0–C3)

---

## Execution Order

**Each connector task (A*) lands in BOTH `usps` and `usps_international`, byte-identical.** Commits are gated on user permission. After each task: spec-compliance review → code-quality review (subagent-driven-development).

### Phase P0 — the four #757 failure classes + foundations

| # | Task | File | Why first / deps |
|---|---|---|---|
| 1 | **A0** fixture date → relative | connector | Foundation; **blocks A4**; spec's #1 risk |
| 2 | **A1** unify boundary regex (`+/=_` bug) | connector | P0 parse correctness |
| 3 | **A2** `manifest_parse_error` Message on unparseable 2xx (`details=None`) | connector | P0; server relies on it to block blank rows |
| 4 | **B0** repair manifest test fixture (DB-direct purchased shipment) | server | Foundation; **blocks all new server tests** |
| 5 | **B1** partial-drop → 400 listing missing ids | server | #757 class (silent missing packages) |
| 6 | **B2** `_decode_pdf` + 422 on both download routes | server | #757 class (blank PDF) |
| 7 | **B3** optional `carrier_id` selector | server | #757 class (wrong account); **gates C2 + TS regen** |
| 8 | **B7** tenant-isolation (cross-org) tests | server | Security gate; after B0 |
| 9 | **C0** per-connection `manifest_required` opt-in (default OFF) | dashboard | **Gates C1/C2/C3**; keeps Tongtool auto-flow untouched |

### Phase P1 — hardening

| # | Task | File | Deps |
|---|---|---|---|
| 10 | **A4** mailingDate today..+7 hard-fail → 400 | connector | **after A0** |
| 11 | **A3** `%PDF`/`JVBER` sniff for `has_doc` | connector | — |
| 12 | **A5** fix `parse_error_response` non-JSON `AttributeError` | connector | — |
| 13 | **A7** replace fixture with captured real multipart (`/tmp/real_scanform.txt`) | connector | — |
| 14 | **B6** USPS US-address completeness → 400 | server | after B0 |
| 15 | **B5** shipment-count cap (`usps:1000`) → 400 | server | after B0 |
| 16 | **B4** deterministic Connections fallback ordering | server | — |
| 17 | **E2** regenerate TS types after B3 schema change | (bin/run-generate / sdk codegen) | **after B3, before C2** |
| 18 | **C1** success toast + `manifest_url` surfacing | dashboard | after C0 |
| 19 | **C2** connection `<select>` sending `carrier_id` | dashboard | **after B3 + E2** |

### Phase P2 — nice-to-have

| # | Task | File | |
|---|---|---|---|
| 20 | **A6** case-insensitive / quoting-tolerant header matching | connector | |
| 21 | **C3** create→print handoff | dashboard | after C1 |

---

## Cross-task Consistency (reconciled — drafts followed one spec)

- **`carrier_id`** (B3) is the field C2 sends and E2 regenerates types for — single name, top-level, mirrors `ShipmentCancelData`.
- **`_decode_pdf(value)`** is server-only (B2): stdlib `base64`/`binascii`, returns decoded bytes or None, asserts `b'%PDF-'` prefix; reused by both download routes.
- **`MANIFEST_MAX_SHIPMENTS = {"usps":1000,"usps_international":1000}`** (B5), per-connection override.
- **`manifest_required`** (C0) = connection-config flag (default OFF); USPS `shipment/create.py` sets `shipment.meta.manifest_required` from it.
- **A2 Message shape**: set only `carrier_id`/`carrier_name`/`code="manifest_parse_error"`/`message` (no `level`/`details`) so `lib.to_dict` renders a predictable dict (matches existing error-fixture convention).

## Self-Review

**Spec coverage:** A1→T2, A2→T3, A3→T11, A4→T10, A5→T12, A6→T20 ✓ ; B1→T5, B2→T6, B3→T7, B4→T16, B5→T15, B6→T14 ✓ ; C0→T9, C1→T18, C2→T19, C3→T21 ✓ ; E2→T17, E3 (contract doc) folded into A7/E1, E1 (PR body) at finish. Extras beyond the spec items (good): A0 fixture-date, A7 real fixture, B0 test-repair, B7 tenant tests. **All spec items mapped.**

**Placeholder scan:** task detail files contain full code (the drafters were required to produce real code against the read source); no TBDs. The one documented assumption (cap=1000) is explicit + config-overridable.

**Type consistency:** reconciled above; the three drafts derived signatures from the same spec + the same source files, so no divergence found.

**Sequencing invariants:** A0 < A4 ; B0 < {B1,B2,B3,B5,B6,B7} ; B3 < E2 < C2 ; C0 < {C1,C2,C3}. Encoded in the order above.
