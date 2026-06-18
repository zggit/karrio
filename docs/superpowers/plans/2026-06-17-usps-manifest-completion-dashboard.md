I now have a complete, accurate picture. The connector C0 test needs a gateway with `config={"manifest_required": True}` to exercise the connection-config path. The default-OFF case uses the existing `gateway` (no config). Let me do one final check: confirm the exact line in usps create.py `_extract_details` `meta=dict(...)` block (lines 112-117) and the usps_international equivalent (lines 83-86) so the C0 edit is byte-precise, and verify `settings.connection_config` is accessible in `_extract_details` (it receives `settings`).

I have all needed context (`_extract_details(data, settings, ctx)` receives `settings`; `settings.connection_config.manifest_required.state` reads the bool config). Now I'll write the workstream C task plan.

```markdown
# Workstream C — Dashboard (USPS SCAN-Form Manifest Completion)

Branch: `feat/usps-scanform-manifest` · Worktree: `/Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest`

**Scope:** One Python connector change (C0, in BOTH `usps` + `usps_international`) + TS/React dashboard changes (C1–C3). C0 gates the whole workstream because nothing surfaces in the "Pending Shipments" tab unless a shipment carries `meta.manifest_required=true` (the tab query at `create_manifests.tsx:43-49` filters `meta_key:"manifest_required", meta_value:true, has_manifest:false`).

**Cross-workstream dependencies:**
- **C2 depends on server task B3** (adds optional `carrier_id` to `ManifestData` in `modules/core/karrio/server/core/serializers.py:1973`) **AND on the TS-type regen step (E2)** which propagates `carrier_id` into `packages/types/rest/api.ts` `ManifestData` (interface at `api.ts:3394-3415`, currently has no `carrier_id`). Strict order: **B3 → regen TS types → C2.** Do NOT start C2 until both are merged into this branch.
- C0/C1/C3 have **no** cross-workstream dependency and can proceed immediately.

**Verification reality:** React modal/page behavior has no unit-test harness in this repo (no jest/vitest for `packages/ui`). So C1/C2/C3 use **typecheck-as-failing-test** (`npx tsc --noEmit` in the package) plus an explicit **manual-verification** script. C0 is real Python TDD against the connector `unittest` suite.

---

### Task: C0 — Per-connection `manifest_required` opt-in (default OFF) [P0 — gates C1/C2/C3]

Adds a boolean connection-config flag `manifest_required` to BOTH USPS connectors. When enabled on a connection, USPS shipment-create stamps `shipment.meta.manifest_required=True`, which makes those shipments appear in the dashboard "Pending Shipments" manifest tab. Default OFF (key absent → `.state` is falsy) so the automated Tongtool flow is unaffected. The connection-config UI (`packages/ui/components/carrier-connection-dialog.tsx:531,558-587`) auto-renders any boolean `ConnectionConfig` member as a `Switch` from `references.connection_configs[carrier]` — **no dashboard UI code is needed for the toggle**; the schema addition surfaces it automatically.

**Files**
- Modify: `modules/connectors/usps/karrio/providers/usps/utils.py` (ConnectionConfig enum, lines 46–51)
- Modify: `modules/connectors/usps/karrio/providers/usps/shipment/create.py` (`_extract_details` meta block, lines 112–117)
- Modify: `modules/connectors/usps_international/karrio/providers/usps_international/utils.py` (ConnectionConfig enum, lines 46–57)
- Modify: `modules/connectors/usps_international/karrio/providers/usps_international/shipment/create.py` (`_extract_details` meta block, lines 83–86)
- Test: `modules/connectors/usps/tests/usps/test_shipment.py` (add 2 tests + fixtures)
- Test: `modules/connectors/usps_international/tests/usps_international/test_shipment.py` (mirror — add 2 tests + fixtures)

- [ ] **Step 1: Write failing test — default OFF and opt-in ON (usps).**
  Append to `modules/connectors/usps/tests/usps/test_shipment.py` (inside `class TestUSPSShipping`, before the `if __name__` guard). The default-OFF test reuses the module `gateway` (no `config`); the opt-in test builds a gateway with `config={"manifest_required": True}` and asserts `meta.manifest_required is True`.

  ```python
    def test_parse_shipment_response_manifest_not_required_by_default(self):
        with patch("karrio.mappers.usps.proxy.lib.request") as mock:
            mock.return_value = ShipmentResponse
            parsed_response = karrio.Shipment.create(self.ShipmentRequest).from_(gateway).parse()
            shipment = lib.to_dict(parsed_response)[0]
            self.assertNotIn("manifest_required", shipment["meta"])

    def test_parse_shipment_response_manifest_required_when_configured(self):
        manifest_gateway = karrio.gateway["usps"].create(
            dict(
                client_id="client_id",
                client_secret="client_secret",
                account_number="Your Account Number",
                config=dict(manifest_required=True),
            ),
            cache=lib.Cache(**cached_auth),
        )
        with patch("karrio.mappers.usps.proxy.lib.request") as mock:
            mock.return_value = ShipmentResponse
            parsed_response = karrio.Shipment.create(self.ShipmentRequest).from_(manifest_gateway).parse()
            shipment = lib.to_dict(parsed_response)[0]
            self.assertTrue(shipment["meta"]["manifest_required"])
  ```

  Add `cached_auth` to the imports at the top of the test file (it currently imports only `gateway`):

  ```python
  from .fixture import gateway, cached_auth
  ```

- [ ] **Step 2: Run — expect FAIL.**
  ```bash
  cd /Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest && source bin/activate-env && python -m unittest -v modules.connectors.usps.tests.usps.test_shipment 2>&1 | tail -25
  ```
  Expected: `test_parse_shipment_response_manifest_required_when_configured` FAILS with `KeyError: 'manifest_required'` (the connector does not yet stamp it). `test_parse_shipment_response_manifest_not_required_by_default` PASSES already (proves no regression to the off path).

- [ ] **Step 3: Implement — add the config enum member + stamp meta (usps).**
  In `modules/connectors/usps/karrio/providers/usps/utils.py`, add the flag to `ConnectionConfig` (lines 46–51) as a `bool` OptionEnum:

  ```python
  class ConnectionConfig(lib.Enum):
      permit_ZIP = lib.OptionEnum("permit_ZIP")
      permit_number = lib.OptionEnum("permit_number")
      price_type = lib.OptionEnum("price_type")
      shipping_options = lib.OptionEnum("shipping_options", list)
      shipping_services = lib.OptionEnum("shipping_services", list)
      manifest_required = lib.OptionEnum("manifest_required", bool)
  ```

  In `modules/connectors/usps/karrio/providers/usps/shipment/create.py`, the `meta=dict(...)` block at lines 112–117 of `_extract_details`. `settings` is already a parameter. Add the conditional key (omit when off so `lib.to_dict` keeps meta clean and the default-OFF test holds):

  ```python
        meta=dict(
            SKU=details.labelMetadata.SKU,
            postage=details.labelMetadata.postage,
            routingInformation=details.labelMetadata.routingInformation,
            labelBrokerID=details.labelMetadata.labelBrokerID,
            **(
                dict(manifest_required=True)
                if settings.connection_config.manifest_required.state
                else {}
            ),
        ),
  ```

- [ ] **Step 4: Run — expect PASS (usps).**
  ```bash
  cd /Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest && source bin/activate-env && python -m unittest -v modules.connectors.usps.tests.usps.test_shipment 2>&1 | tail -25
  ```
  Expected: both new tests PASS; the 5 pre-existing tests (`test_parse_shipment_response`, `..._sample2`, `..._return_...`, request tests) still PASS (meta block unchanged when config absent).

- [ ] **Step 5: Mirror the failing test in usps_international.**
  Append to `modules/connectors/usps_international/tests/usps_international/test_shipment.py` the same two test methods, swapping `usps` → `usps_international` in the patch target, gateway key, and using that module's existing `ShipmentResponse` fixture + `cached_auth`. (First confirm `cached_auth` is exported from `modules/connectors/usps_international/tests/usps_international/fixture.py`; if its `gateway` create-dict differs, copy that file's exact create-dict and add `config=dict(manifest_required=True)`.)

  ```python
    def test_parse_shipment_response_manifest_not_required_by_default(self):
        with patch("karrio.mappers.usps_international.proxy.lib.request") as mock:
            mock.return_value = ShipmentResponse
            parsed_response = karrio.Shipment.create(self.ShipmentRequest).from_(gateway).parse()
            shipment = lib.to_dict(parsed_response)[0]
            self.assertNotIn("manifest_required", shipment["meta"])

    def test_parse_shipment_response_manifest_required_when_configured(self):
        manifest_gateway = karrio.gateway["usps_international"].create(
            dict(
                client_id="client_id",
                client_secret="client_secret",
                account_number="Your Account Number",
                config=dict(manifest_required=True),
            ),
            cache=lib.Cache(**cached_auth),
        )
        with patch("karrio.mappers.usps_international.proxy.lib.request") as mock:
            mock.return_value = ShipmentResponse
            parsed_response = karrio.Shipment.create(self.ShipmentRequest).from_(manifest_gateway).parse()
            shipment = lib.to_dict(parsed_response)[0]
            self.assertTrue(shipment["meta"]["manifest_required"])
  ```

- [ ] **Step 6: Run — expect FAIL (usps_international).**
  ```bash
  cd /Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest && source bin/activate-env && python -m unittest -v modules.connectors.usps_international.tests.usps_international.test_shipment 2>&1 | tail -25
  ```
  Expected: `..._when_configured` FAILS with `KeyError: 'manifest_required'`.

- [ ] **Step 7: Implement — mirror byte-identically in usps_international.**
  In `modules/connectors/usps_international/karrio/providers/usps_international/utils.py`, add to `ConnectionConfig` (after `price_type`, lines 46–57):

  ```python
      manifest_required = lib.OptionEnum("manifest_required", bool)
  ```

  In `modules/connectors/usps_international/karrio/providers/usps_international/shipment/create.py`, the `meta=dict(...)` block at lines 83–86 of `_extract_details`:

  ```python
        meta=dict(
            SKU=details.labelMetadata.SKU,
            postage=details.labelMetadata.postage,
            **(
                dict(manifest_required=True)
                if settings.connection_config.manifest_required.state
                else {}
            ),
        ),
  ```

- [ ] **Step 8: Run BOTH connector suites — expect PASS.**
  ```bash
  cd /Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest && source bin/activate-env \
    && python -m unittest discover -v -f modules/connectors/usps/tests 2>&1 | tail -15 \
    && python -m unittest discover -v -f modules/connectors/usps_international/tests 2>&1 | tail -15
  ```
  Expected: both suites green (new tests pass, no regressions).

- [ ] **Step 9: Commit (only with user permission).**
  ```
  feat(usps): add per-connection manifest_required opt-in flag (default off) for both usps and usps_international
  ```

---

### Task: C1 — Success toast + `manifest_url` surfacing in create modal [P1]

Today `handleSubmit` (`create-manifest-modal.tsx:68-79`) discards the `mutateAsync` return, never fires a success notification, and auto-closes after 1s — exactly the Canada-Post-#757 "no success confirmation" failure. Capture the returned `Manifest` (REST type `api.ts:3340-3393`, has `id` + `manifest_url`), fire `NotificationType.success` (plain-string message — `notify`'s `message` type at `base.ts:112` does NOT accept JSX), store it in state, and render a "Print SCAN form" link in the modal body that calls `useDocumentPrinter().openManifest(manifest.id)` (hook at `resource-token.ts:80-95`). Keep the existing error branch. Delay auto-close so the operator can click the link.

**Files**
- Modify: `packages/ui/core/modals/create-manifest-modal.tsx` (imports lines 1–12; `ManifestFormComponent` state + `handleSubmit` lines 45–79; render success branch in the footer region ~lines 158–181)
- Manual-verify only (no unit harness).

- [ ] **Step 1: Add a typecheck guard as the failing gate.**
  There is no jest in `packages/ui`. Use the TS compiler as the failing test: write the new code referencing `created.manifest_url` / `created.id` and `documentPrinter.openManifest`, then before importing the hook, run typecheck and confirm it FAILS (unresolved symbol). Concretely, FIRST add only this state + a reference with no import:

  ```tsx
    const [created, setCreated] = React.useState<any>(null);
  ```
  and in the render add a block referencing `documentPrinter.openManifest(created.id)` WITHOUT importing `useDocumentPrinter`. Then run Step 2.

- [ ] **Step 2: Run typecheck — expect FAIL.**
  ```bash
  cd /Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest/packages/ui && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -i "create-manifest-modal" | head
  ```
  Expected: error `Cannot find name 'documentPrinter'` in `create-manifest-modal.tsx`. (This proves the test gate is live before wiring the implementation.)

- [ ] **Step 3: Implement — wire the hook, success toast, and print link.**
  Add the import (after line 9, alphabetic-ish with the other `@karrio` hooks):

  ```tsx
  import { useDocumentPrinter } from "@karrio/hooks/resource-token";
  ```

  Inside `ManifestFormComponent` add the printer hook + created state (near line 47–50):

  ```tsx
      const loader = useLoader();
      const { close } = useModal();
      const notifier = useNotifier();
      const mutation = useManifestMutation();
      const documentPrinter = useDocumentPrinter();
      const [created, setCreated] = React.useState<any>(null);
  ```

  Replace `handleSubmit` (lines 68–79) with:

  ```tsx
      const handleSubmit = async (e: React.MouseEvent) => {
        e.preventDefault();
        const { ...payload } = manifest;
        try {
          loader.setLoading(true);
          const result = await mutation.createManifest.mutateAsync(payload);
          setCreated(result);
          notifier.notify({
            type: NotificationType.success,
            message: "Manifest created successfully!",
          });
          setTimeout(() => close(), 4000);
        } catch (message: any) {
          notifier.notify({ type: NotificationType.error, message });
        }
        loader.setLoading(false);
      };
  ```

  In the footer region (between the reference `InputField` block ending at line 156 and the footer `<div className="form-floating-footer ...">` at line 160), insert the print-link block:

  ```tsx
            {created?.id && (
              <div className="notification is-success is-light p-3 my-3">
                <p className="has-text-weight-semibold mb-2">
                  SCAN form created.
                </p>
                <a
                  className={
                    "button is-small is-success is-light" +
                    (documentPrinter.isLoading ? " is-loading" : "")
                  }
                  onClick={(ev) => {
                    ev.preventDefault();
                    documentPrinter.openManifest(created.id);
                  }}
                >
                  <span>Print SCAN form</span>
                </a>
              </div>
            )}
  ```

- [ ] **Step 4: Run typecheck — expect PASS.**
  ```bash
  cd /Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest/packages/ui && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -i "create-manifest-modal"; echo "exit-clean=$?"
  ```
  Expected: no errors referencing `create-manifest-modal.tsx`.

- [ ] **Step 5: Manual verification (record evidence in the PR).**
  1. Connection prerequisite: a USPS carrier connection with the `manifest_required` toggle ON (C0) and ≥1 purchased USPS shipment.
  2. Run dashboard: `cd /Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest && npm run dev --workspace=apps/dashboard` (or the repo's `./bin/start` + dashboard).
  3. Navigate to **Manifests → Pending Shipments**, select the shipment, click **Create Manifests**.
  4. Submit. **Expect:** green "Manifest created successfully!" toast, a "Print SCAN form" link rendered in the modal, modal stays open ~4s. Click the link → a new tab opens the manifest PDF. Confirm the PDF starts with `%PDF` (the live-verified path).

- [ ] **Step 6: Commit (only with user permission).**
  ```
  feat(dashboard): surface manifest success toast and Print SCAN form link in create-manifest modal
  ```

---

### Task: C2 — Connection `<select>` sending `carrier_id` [P1 — depends on server B3 + TS-type regen (E2)]

For multi-account orgs, the modal currently submits no account selector; the server picks an arbitrary USPS connection. After **B3** adds optional `carrier_id` to the server `ManifestData` serializer and **E2** regenerates `packages/types/rest/api.ts` so the `ManifestData` interface (`api.ts:3394-3415`) gains `'carrier_id'?: string | null;`, add a connection `<select>` to the modal populated from the org's USPS connections, dispatching `carrier_id`. The hook already forwards arbitrary fields (`manifests.ts:110-115` spreads `data as any` into `karrio.manifests.create`), so no hook change is needed. The owning page (`create_manifests.tsx:135-150` `computeManifestData`) should seed the default `carrier_id` from the selected shipment so single-account orgs keep working unchanged.

**BLOCKING PRECONDITION — verify before any code:**
```bash
cd /Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest && grep -n "carrier_id" packages/types/rest/api.ts | sed -n '1,5p'
# Must show carrier_id inside the ManifestData interface (around line 3394-3415). If absent → STOP: B3/E2 not yet landed in this branch.
```
If the precondition is not met, do NOT proceed — report "C2 blocked on B3 + E2" and stop.

**Files**
- Modify: `packages/core/modules/Manifests/create_manifests.tsx` (`computeManifestData`, lines 135–150)
- Modify: `packages/ui/core/modals/create-manifest-modal.tsx` (add props for connection options + a `<select>` near the shipment-ids field, lines 92–114)
- Test gate: typecheck. Manual-verify behavior.

- [ ] **Step 1: Failing gate — reference `carrier_id` on `ManifestData` before it exists in the modal's typed payload.**
  In `create_manifests.tsx` `computeManifestData` return (lines 144–149), add `carrier_id`:

  ```tsx
      return {
        address,
        shipment_ids: selection,
        reference: shipment?.reference as string,
        carrier_name: shipment?.carrier_name as string,
        carrier_id: shipment?.carrier_id as string,
      };
  ```
  (If B3/E2 are NOT landed, `ManifestData` has no `carrier_id` and this line FAILS typecheck — which is the gate. If it already typechecks, the precondition was met.)

- [ ] **Step 2: Run typecheck — expect FAIL when unblocked-but-unimplemented / confirm baseline.**
  ```bash
  cd /Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest/packages/core && npx tsc --noEmit 2>&1 | grep -i "create_manifests" | head
  ```
  Expected (B3+E2 landed): clean — `carrier_id` is now a valid `ManifestData` field. (If it errors `'carrier_id' does not exist in type ManifestData`, STOP — types not regenerated.)

- [ ] **Step 3: Implement — connection selector in the modal.**
  Pass connection options from the page into the modal. In `create-manifest-modal.tsx`, extend `CreateManifestModalProps` (lines 14–17):

  ```tsx
  type CreateManifestModalProps = {
    header?: string;
    manifest: ManifestData;
    connectionOptions?: { carrier_id: string; label: string }[];
  };
  ```

  Destructure it in `ManifestFormComponent` (line 45):

  ```tsx
      const { manifest: defaultValue, header, connectionOptions } = props;
  ```

  Add the `<select>` immediately after the shipment-ids `<div className="field mb-2">` block (after line 114), gated on having >1 option so single-account orgs see no change:

  ```tsx
            {(connectionOptions || []).length > 1 && (
              <div className="field mb-2">
                <label className="label is-size-7">Carrier connection</label>
                <div className="control">
                  <div className="select is-small is-fullwidth">
                    <select
                      name="carrier_id"
                      value={manifest.carrier_id || ""}
                      onChange={handleChange}
                    >
                      {(connectionOptions || []).map((opt) => (
                        <option key={opt.carrier_id} value={opt.carrier_id}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              </div>
            )}
  ```
  (`handleChange` at lines 57–67 already dispatches `{name:"carrier_id", value}` into the reducer's default branch — no reducer change needed.)

  In `create_manifests.tsx`, build the options from the org's USPS connections already available via `useCarrierConnections()` (`user_connections`, line 41) and pass them to the modal at line 215–230:

  ```tsx
                        <CreateManifestModal
                          manifest={computeManifestData(selection, shipments)}
                          connectionOptions={(user_connections || [])
                            .filter((c) => c.carrier_name === "usps" || c.carrier_name === "usps_international")
                            .map((c) => ({
                              carrier_id: c.carrier_id as string,
                              label: `${c.carrier_id} (${c.carrier_name})`,
                            }))}
                          trigger={
  ```

- [ ] **Step 4: Run typecheck both packages — expect PASS.**
  ```bash
  cd /Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest/packages/ui && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -iE "create-manifest-modal" | head; \
  cd /Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest/packages/core && npx tsc --noEmit 2>&1 | grep -iE "create_manifests" | head; echo "done"
  ```
  Expected: no errors for the two changed files.

- [ ] **Step 5: Manual verification.**
  1. Org with TWO USPS connections (distinct `carrier_id`s), both `manifest_required` ON, with purchased shipments.
  2. Pending Shipments → select → Create Manifests. **Expect:** a "Carrier connection" dropdown listing both. Pick #2, submit.
  3. In the network tab confirm the `POST /v1/manifests` body includes `"carrier_id": "<connection #2 id>"`. **Expect** 201, and the created manifest's account matches #2 (B3 behavior). Companion check: an org with a SINGLE USPS connection shows NO dropdown and still submits successfully (no `carrier_id` regression — server falls back to the single connection).

- [ ] **Step 6: Commit (only with user permission).**
  ```
  feat(dashboard): add carrier connection selector to create-manifest modal (sends carrier_id)
  ```

---

### Task: C3 — create→print handoff [P2]

Tighten the handoff so the operator lands on a printable artifact after creation. Two acceptable implementations; ship whichever the reviewer prefers — both are covered by C1's print link, so C3 is the redirect-to-Ready-tab convenience. After a successful create, redirect to the Ready tab (`/manifests`, `index.tsx`) where the new row's "Print Manifest" action (`index.tsx:296-306`, already calls `documentPrinter.openManifest(manifest.id)`) is available.

**Files**
- Modify: `packages/ui/core/modals/create-manifest-modal.tsx` (success branch of `handleSubmit`)
- Manual-verify only.

- [ ] **Step 1: Failing gate — reference the router push before importing it.**
  Add `router.push("/manifests")` in the success branch without importing `useRouter`, then typecheck.

- [ ] **Step 2: Run typecheck — expect FAIL.**
  ```bash
  cd /Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest/packages/ui && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -i "create-manifest-modal" | head
  ```
  Expected: `Cannot find name 'router'`.

- [ ] **Step 3: Implement — redirect after the print link, on auto-close.**
  Add the import at top of `create-manifest-modal.tsx`:

  ```tsx
  import { useRouter } from "next/navigation";
  ```

  In `ManifestFormComponent`, get the router (near the other hooks):

  ```tsx
      const router = useRouter();
  ```

  Update the success `setTimeout` in `handleSubmit` (from C1) to redirect to the Ready tab on auto-close:

  ```tsx
          setTimeout(() => {
            close();
            router.push("/manifests");
          }, 4000);
  ```

- [ ] **Step 4: Run typecheck — expect PASS.**
  ```bash
  cd /Users/moon/Documents/OMS/karrio/.worktrees/usps-scanform-manifest/packages/ui && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -i "create-manifest-modal"; echo "clean"
  ```
  Expected: no errors for the file.

- [ ] **Step 5: Manual verification.**
  Create a manifest from Pending Shipments. **Expect:** success toast + print link, then after ~4s the modal closes and the app navigates to **Manifests → Ready**, where the new manifest row shows with an enabled "Print Manifest" action (since `manifest_url` is non-null). Clicking it opens the same `%PDF`.

- [ ] **Step 6: Commit (only with user permission).**
  ```
  feat(dashboard): redirect to Ready tab after manifest creation
  ```
```

---

## Notes / facts that shaped this plan (verified against source)

**File paths & line anchors (real, current source):**
- `create-manifest-modal.tsx`: `handleSubmit` lines 68–79 (discards `mutateAsync` return, no success notify, `setTimeout(close,1000)`); props type lines 14–17; hooks lines 45–50; footer lines 158–181.
- `create_manifests.tsx`: tab query `meta_key:"manifest_required", meta_value:true, has_manifest:false` lines 43–49 — this is WHY C0 gates everything; `computeManifestData` lines 135–150; `<CreateManifestModal>` lines 215–230; `user_connections` from `useCarrierConnections()` line 41.
- `index.tsx` (Ready tab): "Print Manifest" already calls `documentPrinter.openManifest(manifest.id)` lines 296–306 — C3 reuses it.
- `manifests.ts`: `createManifest` spreads `data as any` into `karrio.manifests.create` (lines 110–115) → forwards `carrier_id` with no hook change; returns the `Manifest` from `.then(({data})=>data)`.
- REST `Manifest` (create return) has `id` (3344) + `manifest_url` (3388); `ManifestData` (3394–3415) has **no** `carrier_id` yet → C2 blocked on B3+E2.
- `useDocumentPrinter().openManifest(manifestId)` (`resource-token.ts:80-95`) opens the print URL in a new tab; takes the manifest id.
- `NotificationType.success = "is-success"` (`base.ts:103-108`); `notify` `message` is `string | Error | RequestError | MessageType[] | ErrorType[]` (`base.ts:112`) — **no JSX** → success message is a plain string; the print link lives in the modal body, not the toast.

**C0 connector facts:**
- `ConnectionConfig` lives in `utils.py` for both connectors (usps `:46-51`, intl `:46-57`); bool flags use `lib.OptionEnum("name", bool)` (proven pattern: canadapost `transmit_shipment_by_default`). With `bool` type, absent key → `.state` falsy (default OFF); `true` → `.state is True` (`enum.py:142-144`).
- `_extract_details(data, settings, ctx)` already receives `settings`, so `settings.connection_config.manifest_required.state` is reachable. The `meta=dict(...)` blocks are usps `:112-117`, intl `:83-86`.
- The connection-config UI (`carrier-connection-dialog.tsx:531,558-587`) auto-renders any boolean `ConnectionConfig` member from `references.connection_configs[carrier]` as a `Switch` — **no dashboard code needed for the toggle itself.** (Confirm the boolean appears after the connector schema is picked up by the metadata/references endpoint; a server restart / references refresh may be needed for the new config to show in `connection_configs`.)
- Parity: every connector edit lands in BOTH `usps` and `usps_international`, byte-identical (Decision: parity lockstep).

**Workstream-dependency summary:** C0 (P0) has no external dep and gates the dashboard surface. C1 (P1) and C3 (P2) have no external dep. **C2 (P1) hard-depends on server B3 (carrier_id on ManifestData serializer) AND on E2 (TS-type regen of `api.ts`)** — strict order B3 → regen → C2; C2 includes a blocking precondition grep that STOPs if `carrier_id` is missing from the regenerated `ManifestData`.

**One open verification for the executor (not blocking C0 code):** confirm `cached_auth` is exported from `modules/connectors/usps_international/tests/usps_international/fixture.py` and that its `gateway` create-dict matches the usps one before reusing the Step-5 test verbatim; if the intl fixture's create-dict differs, copy its exact dict and add `config=dict(manifest_required=True)`.