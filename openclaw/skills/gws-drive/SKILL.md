---
name: gws-drive
description: Search, upload, download, share, and manage Google Drive files and folders for Dylan, Julia, or other accounts. Use when asked about Drive files, documents, sharing, uploads, downloads, storage, or finding files.
allowed-tools: Bash(drive:*)
metadata: {"openclaw":{"emoji":"D","requires":{"bins":["gws"]}}}
---

# Google Drive (gws)

Access Google Drive via the `gws` CLI at `/opt/homebrew/bin/gws`. Credentials are AES-256-GCM encrypted at `~/.config/gws/`.

## Accounts

| Account | Owner | Flag |
|---------|-------|------|
| dylanbochman@gmail.com | Dylan | Default (no flag needed) |
| julia.joy.jennings@gmail.com | Julia | `--account julia.joy.jennings@gmail.com` |
| bochmanspam@gmail.com | Dylan (spam) | `--account bochmanspam@gmail.com` |
| clawdbotbochman@gmail.com | OpenClaw | `--account clawdbotbochman@gmail.com` |

When Dylan asks about "my Drive", use default. When he says "Julia's Drive", use her account.

## Command Pattern

All commands follow: `gws drive <resource> <method> [--params '<JSON>'] [--json '<JSON>'] [--account <email>]`

- `--params` = URL/query parameters (fileId, q, pageSize, etc.)
- `--json` = request body (for create, update, etc.)
- `--account` = target account

## Search / List Files

```bash
# List recent files
gws drive files list --params '{
  "pageSize": 10,
  "orderBy": "modifiedTime desc",
  "fields": "files(id,name,mimeType,modifiedTime,size,parents)"
}'

# Search by name
gws drive files list --params '{
  "q": "name contains '\''budget'\''",
  "fields": "files(id,name,mimeType,modifiedTime)"
}'

# Search by type
gws drive files list --params '{
  "q": "mimeType = '\''application/vnd.google-apps.spreadsheet'\''",
  "fields": "files(id,name,modifiedTime)"
}'

# Files in a specific folder
gws drive files list --params '{
  "q": "'\''<folderId>'\'' in parents",
  "fields": "files(id,name,mimeType,modifiedTime)"
}'

# Shared with me
gws drive files list --params '{
  "q": "sharedWithMe = true",
  "pageSize": 10,
  "fields": "files(id,name,mimeType,modifiedTime,sharingUser)"
}'

# Julia's recent files
gws drive files list --params '{
  "pageSize": 10,
  "orderBy": "modifiedTime desc",
  "fields": "files(id,name,mimeType,modifiedTime)"
}' --account julia.joy.jennings@gmail.com
```

### Common Query Syntax

| Query | Meaning |
|-------|---------|
| `name contains 'budget'` | Name contains substring |
| `name = 'Report.pdf'` | Exact name match |
| `mimeType = 'application/pdf'` | PDF files |
| `mimeType = 'application/vnd.google-apps.spreadsheet'` | Google Sheets |
| `mimeType = 'application/vnd.google-apps.document'` | Google Docs |
| `mimeType = 'application/vnd.google-apps.folder'` | Folders |
| `'<folderId>' in parents` | Files in folder |
| `sharedWithMe = true` | Shared with me |
| `trashed = true` | In trash |
| `starred = true` | Starred files |
| `modifiedTime > '2026-01-01T00:00:00'` | Modified after date |
| `createdTime > '2026-01-01T00:00:00'` | Created after date |

Combine with `and`/`or`: `name contains 'report' and mimeType = 'application/pdf'`

### Google MIME Types

| Type | MIME Type |
|------|-----------|
| Folder | `application/vnd.google-apps.folder` |
| Document | `application/vnd.google-apps.document` |
| Spreadsheet | `application/vnd.google-apps.spreadsheet` |
| Presentation | `application/vnd.google-apps.presentation` |
| Form | `application/vnd.google-apps.form` |
| Drawing | `application/vnd.google-apps.drawing` |

## Get File Metadata

```bash
gws drive files get --params '{
  "fileId": "<fileId>",
  "fields": "id,name,mimeType,modifiedTime,size,parents,webViewLink,permissions"
}'
```

## Download a File

```bash
# Download binary file (PDF, image, etc.)
gws drive files get --params '{
  "fileId": "<fileId>",
  "alt": "media"
}' --output /tmp/downloaded-file.pdf

# Export Google Docs/Sheets/Slides to a format
gws drive files export --params '{
  "fileId": "<fileId>",
  "mimeType": "application/pdf"
}' --output /tmp/exported.pdf
```

### Export MIME Types

| Source | Export To | MIME Type |
|--------|----------|-----------|
| Google Doc | PDF | `application/pdf` |
| Google Doc | Word | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| Google Doc | Plain text | `text/plain` |
| Google Sheet | PDF | `application/pdf` |
| Google Sheet | Excel | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| Google Sheet | CSV | `text/csv` |
| Google Slides | PDF | `application/pdf` |
| Google Slides | PowerPoint | `application/vnd.openxmlformats-officedocument.presentationml.presentation` |

## Upload a File

```bash
# Simple upload (auto-detects name and MIME type)
gws drive +upload /path/to/file.pdf

# Upload to a specific folder
gws drive +upload /path/to/file.pdf --parent <folderId>

# Upload with custom name
gws drive +upload /path/to/data.csv --name 'Sales Data.csv'

# Upload with metadata via files.create + --upload flag
gws drive files create --json '{
  "name": "Report.pdf",
  "parents": ["<folderId>"],
  "description": "Monthly report"
}' --upload /path/to/report.pdf
```

## Create a Folder

```bash
gws drive files create --json '{
  "name": "New Folder",
  "mimeType": "application/vnd.google-apps.folder",
  "parents": ["<parentFolderId>"]
}'
```

## Move a File

```bash
# Move file to a different folder
gws drive files update --params '{
  "fileId": "<fileId>",
  "addParents": "<newFolderId>",
  "removeParents": "<oldFolderId>"
}'
```

## Rename a File

```bash
gws drive files update --params '{"fileId": "<fileId>"}' --json '{
  "name": "New Name.pdf"
}'
```

## Copy a File

```bash
gws drive files copy --params '{"fileId": "<fileId>"}' --json '{
  "name": "Copy of Document",
  "parents": ["<folderId>"]
}'
```

## Share a File

```bash
# Share with a specific user (editor)
gws drive permissions create --params '{
  "fileId": "<fileId>",
  "sendNotificationEmail": true,
  "emailMessage": "Here is the file you requested"
}' --json '{
  "role": "writer",
  "type": "user",
  "emailAddress": "recipient@example.com"
}'

# Share with a specific user (viewer)
gws drive permissions create --params '{
  "fileId": "<fileId>"
}' --json '{
  "role": "reader",
  "type": "user",
  "emailAddress": "recipient@example.com"
}'

# Share with anyone who has the link
gws drive permissions create --params '{
  "fileId": "<fileId>"
}' --json '{
  "role": "reader",
  "type": "anyone"
}'
```

### Permission Roles

| Role | Access |
|------|--------|
| `owner` | Full ownership |
| `organizer` | Shared drive organizer |
| `writer` | Can edit |
| `commenter` | Can comment |
| `reader` | Can view |

## List Permissions

```bash
gws drive permissions list --params '{
  "fileId": "<fileId>",
  "fields": "permissions(id,role,type,emailAddress,displayName)"
}'
```

## Remove Sharing

```bash
gws drive permissions delete --params '{
  "fileId": "<fileId>",
  "permissionId": "<permissionId>"
}'
```

## Trash / Delete

```bash
# Trash (recoverable)
gws drive files update --params '{"fileId": "<fileId>"}' --json '{
  "trashed": true
}'

# Restore from trash
gws drive files update --params '{"fileId": "<fileId>"}' --json '{
  "trashed": false
}'

# Permanent delete (irreversible!)
gws drive files delete --params '{"fileId": "<fileId>"}'

# Empty trash
gws drive files emptyTrash
```

## Storage Info

```bash
gws drive about get --params '{
  "fields": "storageQuota,user"
}'
```

## Auto-Pagination

For large result sets, use `--page-all` to auto-paginate:

```bash
gws drive files list --params '{
  "q": "modifiedTime > '\''2026-01-01T00:00:00'\''",
  "pageSize": 100,
  "fields": "files(id,name,mimeType)"
}' --page-all --page-limit 5
```

## API Schema

For any endpoint, check available parameters:
```bash
gws schema drive.files.list
gws schema drive.files.create
gws schema drive.permissions.create
```

## Notes

- Default account: dylanbochman@gmail.com
- **When sharing files or changing permissions, confirm with the user first**
- `fields` parameter controls which metadata fields are returned — always specify to reduce response size
- Google Workspace files (Docs, Sheets, Slides) use `export` to download; regular files use `get` with `alt: media`
- File IDs are stable and permanent — safe to reference across sessions
- `gws` outputs JSON by default — parse directly or pipe through `jq`
- The `+upload` helper is the easiest way to upload files (auto-detects MIME type)
