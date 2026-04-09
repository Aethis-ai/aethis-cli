# IAM CLI Developer Guide

This guide is for developers extending or operating IAM flows through `aethis-cli`.

## Command surface

- `aethis iam users-list --org-id <org>`
- `aethis iam grant-role <user_id> --org-id <org> --domain <domain> --role <role>`
- `aethis iam revoke-role <user_id> <role> --org-id <org> --domain <domain>`
- `aethis iam groups-list --org-id <org>`
- `aethis iam groups-create <group_id> --name <name> --org-id <org>`
- `aethis iam groups-delete <group_id> --org-id <org>`
- `aethis iam groups-add-user <group_id> <user_id> --org-id <org>`
- `aethis iam groups-remove-user <group_id> <user_id> --org-id <org>`
- `aethis iam relationships-list --org-id <org> [--domain <domain>] [--subject-id <id>]`
- `aethis iam relationships-add --org-id <org> --domain <domain> --subject-id <id> --relation <rel> --object-type <type> --object-id <id>`
- `aethis iam relationships-remove --org-id <org> --domain <domain> --subject-id <id> --relation <rel> --object-type <type> --object-id <id>`
- `aethis iam audit --org-id <org> [--event-type <type>] [--actor-id <id>]`

## Authentication behavior

IAM commands authenticate with Clerk bearer token.

- Default: interactive browser login.
- CI/automation: set `AETHIS_ACCESS_TOKEN` to bypass browser flow.

```bash
export AETHIS_ACCESS_TOKEN="<clerk-oauth-access-token>"
```

## Error diagnostics

Structured 403 payloads from API are rendered in CLI output, including:

- `reason_code`
- `action`
- `missing_permissions`

Typical admin failure:

- `reason=denied_missing_permission` with `action=iam.admin`

## Test commands

Run full suite (coverage-gated):

```bash
pytest -q
```

Run IAM-focused tests:

```bash
pytest -q tests/test_iam_cmd.py tests/test_account_cmd.py tests/test_main.py
```
