---
name: karrio-gotchas
description: >
  Hard-won correctness rules for the zggit/karrio FORK — distilled from real
  incidents while building + deploying the USPS SCAN-form manifest feature.
  USE THIS whenever working on this fork's: GraphQL types/resolvers, the manifest
  feature, the bind-mount production deploy (pinned official image + patches),
  the dashboard Docker image build, or debugging prod via SSH/docker exec/curl/
  Playwright. Each rule maps to an error that actually cost time on 2026-06-18.
disable-model-invocation: true
---

# Karrio Fork — Dev & Deploy Gotchas

Not generic Karrio tips. Every rule below traces to a **real failure** on this
fork. When you touch the matching area, follow the ✅ pattern. Severity:
🔴 will-bite-hard · 🟠 medium · 🟡 minor-but-wastes-time.

> Context that frames most of these: **production runs the OFFICIAL pinned image
> (`karrio/server:2026.1.29`) + bind-mount patch files at `/root/karrio/patches/`**
> mounted via `docker-compose.yml`. The dashboard runs a CUSTOM image
> `karrio-dashboard-worldexp:latest`. Server: `root@45.77.203.235`, key
> `~/.ssh/id_ed25519`. Our fork's HEAD is NOT what prod runs.

## Index

| # | Rule | When it applies | Sev |
|---|------|-----------------|-----|
| 1 | GraphQL type field holding a dict needs an explicit `.parse()` resolver | editing `graph/.../types.py` types | 🔴 |
| 2 | One bad GraphQL field empties the WHOLE list (client treats `errors[]` as failure) | debugging an "empty list" dashboard tab | 🔴 |
| 3 | `manifest_required` is opt-in default OFF — Pending stays empty until config ON + labels bought after | manifest Pending tab empty | 🔴 |
| 4 | REST and GraphQL are SEPARATE serialization paths — test the one the dashboard uses | "API returns it but UI is empty" | 🟠 |
| 5 | New bind-mount patch needs a NEW compose mount + `up -d` recreate (not restart) | deploying a server-side .py fix | 🔴 |
| 6 | compose service name ≠ `container_name` (`api`/`worker`, not `karrio.api`) | `docker compose up -d` on prod | 🟠 |
| 7 | Surgical-patch shared files; full-file-mount only when `diff pinned↔fork == 0` | patching core/shared modules | 🔴 |
| 8 | Don't deploy fork-test-only fixes to prod (migrations, latent-import fixes) | deciding deploy scope | 🟠 |
| 9 | Build the dashboard image NATIVE amd64 (Mac is arm64, server is amd64) | rebuilding the dashboard image | 🔴 |
| 10 | `next build` OOMs in Docker — raise Node heap to 4GB | dashboard Docker build | 🟠 |
| 11 | Dashboard API host is RUNTIME-resolved, not build-time inlined → local rebuild is safe | worrying about NEXT_PUBLIC_* | 🟡 |
| 12 | curl `localhost:5002` inside the api container → SSL-redirect/"Invalid HTTP request"; use the public HTTPS URL | testing the API from the server | 🟠 |
| 13 | Capturing a value via `$(docker exec … python)` is polluted by loguru stdout → write to a file, read the file | grabbing a token/id for a test | 🔴 |
| 14 | Container runs as non-root (`karrio`) — can't write host paths; write `/tmp` + `docker cp` | writing a backup/file from inside the container | 🟠 |
| 15 | Use a temp live API Token (create→test→delete) to replay dashboard GraphQL; admin has no token by default (JWT) | reproducing what the dashboard fetches | 🟠 |
| 16 | `bulk_update` (not per-row `.save()`) for backfills — avoids firing N webhook/signal events | mass-updating shipments/orders | 🟠 |
| 17 | Playwright: the dashboard email field is `type="text"` name="email" (NOT `type=email`) | automating dashboard login | 🟡 |

---

## A. GraphQL & the manifest feature

### 1. A GraphQL type field whose value is a dict needs an explicit `.parse()` resolver 🔴
**Symptom:** `'dict' object has no attribute 'id'` (or `.postal_code`, etc.) deep in a GraphQL resolver path; the field resolves to `null` with an error.
**Cause:** A bare strawberry field like `address: AddressType` uses the *default* resolver, which does attribute access (`source.id`) on the source value. If the model stores that value as a **dict (JSONField)**, attribute access fails.
**✅ Fix:** give it a resolver that parses the dict, mirroring sibling fields:
```python
@strawberry.field
def address(self: manager.Manifest) -> typing.Optional[AddressType]:
    return AddressType.parse(self.address)   # NOT a bare `address: AddressType`
```
**Why:** `ManifestType.address` was a bare field while `messages`/`manifest_carrier` had `.parse()` resolvers — so the manifests query crashed on any manifest with an address, emptying the dashboard Ready tab. This bug is in the pinned 2026.1.29 image too (upstream). Fixed in commit `8548acc6f`, deployed as a graph bind-mount patch.

### 2. One bad field empties the WHOLE list — the GraphQL client treats `errors[]` as total failure 🔴
**Symptom:** a dashboard list tab shows its empty state ("No X found") even though the data exists.
**Cause:** when a GraphQL response has *any* `errors[]` (even with partial `data`), `karrio.graphql.request` / the query hook treats it as a failed query → `data` is undefined → the list renders empty.
**✅ Fix:** when a list is mysteriously empty, run the **exact** query the dashboard sends (full field set) against the API and look for an `errors[]` array — not just `data`. A 3-field probe can pass while the full query fails. Isolate the offending field by bisecting the selection set.
**Why:** Ready showed "No manifest found" while REST + a minimal GraphQL query both returned the manifest; only the full field set surfaced the address-resolver error.

### 3. `manifest_required` is opt-in, default OFF — Pending stays empty until the config is ON *and* labels are bought afterward 🔴
**Symptom:** Manifests → Pending Shipments is empty even though you just purchased USPS labels.
**Cause:** the Pending tab filters `status=created, meta.manifest_required=true, has_manifest=false`. The connector only stamps `meta.manifest_required` when the **connection config** `manifest_required` is ON (C0). Default is OFF (by design, to not disturb the Tongtool auto-flow). The flag is stamped at *purchase time* — enabling it later does NOT retroactively flag existing labels.
**✅ Fix:** (a) enable `manifest_required=True` on the USPS `CarrierConnection.config` for future labels; (b) backfill already-bought labels' `meta.manifest_required=True`. The GraphQL meta filter works fine with bool values — verified empirically. Scope the backfill to the day's batch (old already-inducted packages shouldn't go on today's SCAN form).
**Why:** 0/136 of a day's labels were flagged because the toggle had never been turned on; "empty Pending" was the *expected* state, not a bug.

### 4. REST and GraphQL are SEPARATE serialization paths 🟠
**Symptom:** `GET /v1/<x>` returns the object, but the dashboard (GraphQL) shows nothing — or vice versa.
**✅ Fix:** always test the path the failing surface actually uses. The dashboard list tabs use **GraphQL** (`GET_MANIFESTS`, `GET_SHIPMENTS`); REST working tells you the *data* is fine but says nothing about the GraphQL resolver. Instrument each boundary: DB → REST → GraphQL.

---

## B. Production deploy (bind-mount patches onto the pinned image)

### 5. A NEW patch file needs a NEW compose mount + container recreate 🔴
**Symptom:** you scp a new patch file but the change doesn't take effect.
**Cause:** the file must be **volume-mounted** into the container at the site-packages path, AND the container recreated. Updating an *already-mounted* file only needs a restart; a *new* file needs a new `volumes:` entry in `docker-compose.yml` for both `api` and `worker`, then `docker compose up -d api worker` (recreate, not restart).
**✅ Pattern:** patch path `/root/karrio/patches/<rel>` → mount `./patches/<rel>:/karrio/venv/lib/python3.12/site-packages/karrio/<rel>:ro` on api+worker. Always back up `docker-compose.yml` first (`*.bak-pre-<change>-<ts>`).

### 6. compose service name ≠ container_name 🟠
**Symptom:** `docker compose up -d karrio.api` → `no such service: karrio.api`.
**Cause:** `karrio.api` is the `container_name`; the compose **service key** is `api` (likewise `worker`, `dashboard`, `db`, `redis`).
**✅ Fix:** `docker compose up -d api worker`. Use `docker compose config --services` to list real service names.

### 7. Surgical-patch shared files; full-file-mount only when fork == pinned 🔴
**Symptom:** mounting our fork's whole file would carry unrelated fork divergence (e.g., import reordering) into prod.
**✅ Fix:** before mounting a whole fork file, run `diff <pinned-image-file> <fork-file>`. If **0**, mount the fork file (it's pinned + your change). If there's churn (seen in `core/serializers.py`, `core/datatypes.py` — import reorders), build a **surgical** patch = the pinned image's file + ONLY the manifest delta (anchor on a unique multi-line block). `graph/.../types.py` was diff=0 so we mounted the fork file directly.

### 8. Don't deploy fork-test-only fixes to prod 🟠
**Symptom:** tempting to deploy every change on the branch.
**✅ Fix:** prod runs the pinned image, which already has things our fork "fixed" for local testing. We deliberately SKIPPED: migration `0108` no-op (fork-health only; prod has its own migration history), `datatypes.py` B0 ManifestRequest import (prod's `datatypes.py` already resolves it — `grep` proved it), and `usps_international` (prod patches domestic-usps only; intl manifest has wiring risk). Verify "is this needed in prod?" before adding a patch.

---

## C. Dashboard image build

### 9. Build the dashboard image NATIVE amd64 🔴
**Symptom:** the image builds locally but won't run on the server (exec format error), or you waste time on a wrong-arch image.
**Cause:** dev Mac is **arm64** (Apple Silicon); the server is **amd64**.
**✅ Fix:** build on the server (native amd64, fast — it has 15GB RAM/6CPU) by tar'ing the source (no node_modules, ~145MB) → scp → `docker build`. Or `docker buildx build --platform linux/amd64` locally (emulated, slow, OOM-prone). Tag the old image as a `:bak-<date>` before retagging `:latest` so you can roll back.

### 10. `next build` OOMs inside Docker — raise Node heap 🟠
**Symptom:** `FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory` during `npm run build`.
**Cause:** Node defaults to ~2GB old-space heap regardless of VM RAM.
**✅ Fix:** `ENV NODE_OPTIONS=--max-old-space-size=4096` in the builder stage (committed in `docker/dashboard/Dockerfile`, `f55d2e0f9`).

### 11. Dashboard API host is RUNTIME-resolved, not build-time inlined 🟡
**Symptom:** worrying a local rebuild bakes in `localhost` for the API URL.
**Reality:** `getHost()` reads a runtime-injected prop / `metadata.HOST`; the Dockerfile declares **no** URL build-arg; the entrypoint + compose env (`KARRIO_URL`, `NEXT_PUBLIC_KARRIO_PUBLIC_URL`) drive the real URLs at runtime. So a local rebuild passing only `NEXT_PUBLIC_DASHBOARD_VERSION` is safe. (Also: `docker/dashboard/Dockerfile.dockerignore` takes precedence over a root `.dockerignore` in BuildKit.)

---

## D. Debugging & ops tooling (prod via SSH / docker exec / curl / Playwright)

### 12. curl `localhost:5002` inside the api container fails 🟠
**Symptom:** `Invalid HTTP request received` or HTTP `301` for any request to `http://localhost:5002`.
**Cause:** the app does `SECURE_SSL_REDIRECT` (http→https) and TLS is terminated at nginx-proxy; hitting the app's http port directly mis-handles it.
**✅ Fix:** test against the **public HTTPS** endpoint `https://api.ship.worldexp.com/...` (the path the dashboard uses), from the server or your Mac.

### 13. Capturing a value via `$(docker exec … python)` is polluted by loguru stdout 🔴
**Symptom:** an API call with a captured token returns nginx `400 Bad Request`; the captured value is multi-line.
**Cause:** loguru / Django init logs print to stdout, so `TOKEN=$(docker exec … python -c "print(t.key)")` captures `INFO…\n…key` → a multi-line `Authorization: Token …` header → malformed → nginx 400.
**✅ Fix:** write the value to a file inside the container and read just that file:
```bash
docker exec -i … python - <<'PY'  # ... open("/tmp/tok","w").write(t.key)
PY
TOKEN=$(docker exec api cat /tmp/tok)   # clean, single line
```
Also filter exec output with `grep -vE "INFO|Redis|signal|Loguru|gateway|references"`.

### 14. The container runs as non-root (`karrio`) — can't write host paths 🟠
**Symptom:** `PermissionError: [Errno 13] Permission denied: '/root/karrio/...'` from inside `docker exec python`.
**✅ Fix:** write to a container-writable path (`/tmp/...`) then `docker cp <container>:/tmp/file /root/karrio/file` from the host shell. (Lucky side effect: a backup-write that fails here aborts *before* any DB mutation — write the backup first, on purpose.)

### 15. Replay dashboard GraphQL with a TEMP live API token 🟠
**Symptom:** you want to see exactly what the dashboard fetches, but the admin user has no API token (the dashboard authenticates via NextAuth JWT).
**✅ Fix:** create a temporary live `Token` (`Token.objects.create(user=admin, test_mode=False)`), curl the public `/graphql` with `authorization: Token <key>`, then **delete** it. Single-user/single-org here (admin id=2, super), so org/tenant is rarely the cause — check `meta`/resolver/filter first.

### 16. `bulk_update`, not per-row `.save()`, for backfills 🟠
**Symptom:** mass-updating shipments could fire a webhook/signal per row.
**✅ Fix:** mutate in Python then `Model.objects.bulk_update(rows, ["field"])` — it bypasses `post_save` signals (no 136 webhook events). Back up the affected IDs + original values first (reversible).

### 17. Playwright: the dashboard login email field is `type="text"` 🟡
**Symptom:** automated login silently fails — fills nothing, stays on `/signin`.
**Cause:** the email input is `<input type="text" name="email" id="email">` (NOT `type=email`); the password is `type=password`; the only button is "Sign in".
**✅ Fix:** select by `input[name=email]` / `input[type=password]`. Run **headed** (the no-headless preference still stands) and reuse `storageState` to avoid re-login. Pass creds via a gitignored temp file, never on the command line.

---

## Maintaining this skill

When you hit a NEW failure mode on this fork that isn't here → after fixing,
add a row to the Index + a section, with **Symptom / Cause / ✅ Fix / Why**
and the date. This is the closed loop. Source of all current rules: the
2026-06-18 USPS SCAN-form manifest build, deploy, and "Pending+Ready empty"
debugging session.
