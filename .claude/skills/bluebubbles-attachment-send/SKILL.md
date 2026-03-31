---
name: bluebubbles-attachment-send
description: |
  Send file attachments (audio, images, documents) via BlueBubbles iMessage API. Use when:
  (1) Need to send an MP3, image, or file via iMessage through BlueBubbles,
  (2) "Chat does not exist!" error when sending attachments via curl -F (semicolons in
  chatGuid get mangled by curl multipart form parsing),
  (3) /api/v1/message/text endpoint ignores the attachment field in multipart mode,
  (4) Building TTS audio briefings or voice messages for iMessage delivery,
  (5) Need to know the correct BB API endpoint for attachments (/api/v1/message/attachment).
  Covers the correct endpoint, required fields, and the curl semicolon bug workaround.
author: Claude Code
version: 1.0.0
date: 2026-03-22
---

# BlueBubbles Attachment Send

## Problem
Sending file attachments (audio, images, etc.) via BlueBubbles iMessage API is not
straightforward. The text endpoint ignores file uploads, and curl's multipart form
handling breaks chat GUIDs containing semicolons.

## Context / Trigger Conditions
- Need to send a file (MP3, image, PDF) via iMessage through BlueBubbles
- `POST /api/v1/message/text` with multipart form data silently drops the attachment
- `curl -F "chatGuid=any;-;user@email.com"` returns "Chat does not exist!" (semicolons parsed as form-data separators)
- Want to send TTS audio or voice briefings via iMessage

## Solution

### Correct Endpoint
Use `POST /api/v1/message/attachment` (NOT `/message/text`).

### Required Fields
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `chatGuid` | string | Yes | BB chat GUID (e.g., `any;-;+1XXXXXXXXXX`) |
| `name` | string | Yes | Filename (e.g., `briefing.mp3`) — API returns 400 without this |
| `attachment` | file | Yes | The file to send (multipart upload) |
| `method` | string | No | `private-api` (recommended) or `apple-script` |
| `tempGuid` | string | No | UUID for dedup |

### Critical: Use Python requests, NOT curl

curl's `-F` flag treats semicolons as field separators, corrupting `chatGuid` values
like `any;-;user@email.com`. This causes "Chat does not exist!" errors even though the
chat exists.

**Python requests handles this correctly:**

```python
import requests, uuid

pw = "your_bb_password"
url = f"http://localhost:1234/api/v1/message/attachment?password={pw}"

files = {"attachment": ("briefing.mp3", open("/tmp/briefing.mp3", "rb"), "audio/mpeg")}
data = {
    "chatGuid": "any;-;+1XXXXXXXXXX",
    "tempGuid": str(uuid.uuid4()).upper(),
    "name": "briefing.mp3",
    "method": "private-api",
}

r = requests.post(url, data=data, files=files, timeout=60)
print(r.json())  # {"status": 200, "message": "Attachment sent!"}
```

### Helper Script
`~/.openclaw/bin/send-audio-briefing` wraps TTS generation + BB attachment send:

```bash
# Generate TTS + send audio + send summary text
send-audio-briefing "any;-;+1XXXXXXXXXX" "Hello Julia!" -m "Short summary" -v Sarah
```

### Key Differences Between Endpoints

| Endpoint | Accepts Files? | Auth |
|----------|---------------|------|
| `/message/text` (JSON body) | No | `?password=` query param |
| `/message/text` (multipart) | Silently ignores file fields | `?password=` query param |
| `/message/attachment` (multipart) | Yes — this is the correct one | `?password=` query param |

## Verification
- Response: `{"status": 200, "message": "Attachment sent!"}`
- Response `data.attachments` array should contain the file metadata
- Check recipient's phone for the delivered attachment

## Example
```python
# Full workflow: generate TTS audio, send as iMessage attachment, follow up with text
import requests, uuid, subprocess, os

# 1. Generate audio
subprocess.run(["sag-wrapper", "-o", "/tmp/briefing.mp3", "--play=false", "-v", "Sarah",
                "Good morning! You have 2 meetings today."])

# 2. Send audio attachment
pw = os.environ["BLUEBUBBLES_PASSWORD"]
files = {"attachment": ("morning-briefing.mp3", open("/tmp/briefing.mp3", "rb"), "audio/mpeg")}
data = {"chatGuid": "any;-;+1XXXXXXXXXX", "name": "morning-briefing.mp3",
        "tempGuid": str(uuid.uuid4()).upper(), "method": "private-api"}
r = requests.post(f"http://localhost:1234/api/v1/message/attachment?password={pw}",
                   data=data, files=files, timeout=60)

# 3. Send summary text
body = {"chatGuid": "any;-;+1XXXXXXXXXX", "message": "2 meetings today",
        "tempGuid": str(uuid.uuid4()).upper(), "method": "private-api"}
requests.post(f"http://localhost:1234/api/v1/message/text?password={pw}",
              json=body, timeout=15)
```

## Notes
- The attachment shows up as a tappable file in iMessage (not an inline voice bubble/waveform)
- Native iMessage "audio messages" use `.caf` format and the voice memo extension — that's different
- BB auth is always via `?password=` query param (not header — header returns 401)
- The `name` field is REQUIRED on `/message/attachment` — omitting it returns 400
- BB server version tested: 1.9.9 with Private API enabled
- MIME types: `audio/mpeg` for MP3, `image/png` for PNG, `application/pdf` for PDF, etc.
