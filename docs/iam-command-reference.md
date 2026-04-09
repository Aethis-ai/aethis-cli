# IAM Command Reference

Quick reference for IAM administration from `aethis-cli`.

## Authentication modes

1. Interactive login (default): browser-based Clerk auth.
2. Non-interactive mode: set `AETHIS_ACCESS_TOKEN`.

```bash
export AETHIS_BASE_URL="https://api.aethis.ai"
export AETHIS_ACCESS_TOKEN="<clerk-oauth-access-token>"
```

## Commands

| Command | Purpose |
|---|---|
| `aethis iam users-list --org-id <org>` | List users in org |
| `aethis iam grant-role <user> --org-id <org> --domain <d> --role <r>` | Grant direct role |
| `aethis iam revoke-role <user> <role> --org-id <org> --domain <d>` | Revoke direct role |
| `aethis iam groups-list --org-id <org>` | List groups |
| `aethis iam groups-create <group> --name <name> --org-id <org>` | Create group |
| `aethis iam groups-delete <group> --org-id <org>` | Delete group |
| `aethis iam groups-add-user <group> <user> --org-id <org>` | Add user to group |
| `aethis iam groups-remove-user <group> <user> --org-id <org>` | Remove user from group |
| `aethis iam relationships-list --org-id <org> [--domain <d>] [--subject-id <s>]` | List relationships |
| `aethis iam relationships-add --org-id <org> --domain <d> --subject-id <s> --relation <r> --object-type <t> --object-id <id>` | Add relationship |
| `aethis iam relationships-remove --org-id <org> --domain <d> --subject-id <s> --relation <r> --object-type <t> --object-id <id>` | Remove relationship |
| `aethis iam audit --org-id <org> [--event-type <t>] [--actor-id <a>]` | Query IAM audit events |

## Common scenarios

### Onboard a user as author

```bash
ORG_ID=org_demo
aethis iam grant-role user_123 --org-id "$ORG_ID" --domain core --role author
```

### Move access to group-based management

```bash
ORG_ID=org_demo
aethis iam groups-create eng --name "Engineering" --org-id "$ORG_ID"
aethis iam groups-add-user eng user_123 --org-id "$ORG_ID"
```

### Offboard a user

```bash
ORG_ID=org_demo
aethis iam revoke-role user_123 author --org-id "$ORG_ID" --domain core
aethis iam groups-remove-user eng user_123 --org-id "$ORG_ID"
```

## Troubleshooting

- `reason=denied_missing_permission` + `action=iam.admin`:
  - caller is not authorized as IAM admin in core runtime config.
- `HTTP 401`:
  - invalid/expired Clerk token.
- `HTTP 404`:
  - wrong org scope or object no longer exists.
