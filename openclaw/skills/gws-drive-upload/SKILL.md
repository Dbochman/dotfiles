---
name: gws-drive-upload
description: Upload one local file to Google Drive with the gws helper. Use when the user asks to place a specific file in Drive with an optional destination folder or target filename; use gws-drive for sharing, moving, copying, or permission changes.
---

# Upload one file to Google Drive

Use the pinned `gws drive +upload` helper. Read the [Drive gws reference](../gws-drive/references/gws-cli.md) before choosing an account or executing the write.

## Command shape

The local file is a positional argument; there is no `--file` flag.

```bash
gws drive +upload '<LOCAL_FILE>' [--parent '<FOLDER_ID>'] [--name '<DRIVE_FILENAME>']
```

## Required write gate

1. Verify that the requested local path is a regular file and resolve the target account, parent folder, filename, and file size. Do not read or summarize file contents unless the request requires it.
2. Run the exact upload command with `--dry-run`.
3. Show the user the source path, size, target account, destination folder, and final filename.
4. Ask for explicit confirmation immediately before running the command without `--dry-run`.
5. Verify the returned Drive file ID, filename, and parent. Do not claim success from the process exit alone.

Never broaden an upload from one named file to a directory or wildcard without a new confirmation. Use [gws-drive](../gws-drive/SKILL.md) for permissions or other Drive mutations.
