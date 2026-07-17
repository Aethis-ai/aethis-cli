# Staging integration lane (`-m staging`)

These tests acquire a real developer API key the way a self-serve user does
(sign a fenced e2e user in via the dev-tools Clerk instance, then mint a key
with the server's default scopes) and drive the CLI core loop plus the negative
paths against **deployed staging** — never a laptop backend, never production.

They are excluded from the default suite (`addopts = -m 'not manual and not
staging'`) and run report-only in CI (`.github/workflows/staging-integration.yml`,
nightly + manual dispatch). They never gate a merge.

## Run it locally (fresh clone)

```bash
uv sync --extra dev

CLERK_SECRET_KEY_DEV_TOOLS="<dev-tools Clerk backend secret>" \
CLERK_E2E_DX_USER_ID="$(gh variable get CLERK_E2E_DX_USER_ID --repo Aethis-ai/aethis-cli)" \
AETHIS_CONTRACT_URL="https://staging.api.aethis.ai/api/v1/public/diagnostics/contract" \
uv run pytest tests/integration -m staging -o addopts="" -v
```

- `CLERK_SECRET_KEY_DEV_TOOLS` — dev-tools Clerk backend secret (staff pull it
  from Secret Manager). Signs the fenced e2e user in. The same value is wired
  into CI as a repository secret.
- `CLERK_E2E_DX_USER_ID` — the fenced e2e user's id (repository variable).
- `AETHIS_CONTRACT_URL` — where the machine-readable public-API contract lives.
  Defaults to the staging diagnostics endpoint. Until that endpoint is live you
  can point it at a local checkout, e.g.
  `file:///path/to/aethis-core/aethis_core/public/contracts/public-api-contract.json`.

Without the two Clerk creds the tests **skip** (local ergonomic). In CI a
missing secret or an unreachable staging/contract is a **hard red**, never a
skip-green — a lane that cannot actually run must report red.

## What it never logs

The Clerk secret, the sign-in ticket, the session JWT, and any minted
`full_key` are treated as write-only — they never reach stdout/stderr. Every
key minted here is named `e2e-dx-cli-*`, revoked in teardown, and any stray
`e2e-dx-cli-*` key a crashed run leaked is swept on the next run.
