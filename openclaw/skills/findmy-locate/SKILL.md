---
name: findmy-locate
description: Privately locate Dylan, Julia, the Mac Mini account, or both people through Apple Find My using exact accessibility-name verification. Use only for an explicit location request in a one-to-one conversation with the authorized direct user. Never run or disclose results in group chats, shared channels, or for a third party. Report only a coarse area by default; provide precise location only when the direct user explicitly requests it.
allowed-tools: Bash(findmy-locate:*)
metadata: {"openclaw":{"emoji":"P","requires":{"bins":["peekaboo"]}}}
---

# Find My Locate

Use Find My through Peekaboo to capture a short-lived, precise map image for
one explicitly requested person. Treat every capture and inferred location as
highly sensitive personal data.

## Privacy boundary

- Run this skill only in a direct one-to-one session with the authorized user.
- Never run it from a group chat, shared channel, forwarded request, or request
  made on someone else's behalf.
- Never attach, quote, or summarize a capture into a group conversation.
- Report only a coarse result by default, such as home, a city, neighborhood,
  or general area.
- Give a street-level or otherwise precise result only when the direct user
  explicitly asks for precision in the current request.
- Do not retain coordinates, addresses, screenshots, or inferred travel
  history in memory or operational notes.

## Commands

```bash
findmy-locate dylan
findmy-locate julia
findmy-locate me
findmy-locate both
findmy-locate cleanup
```

Single-person success:

```json
{"success":true,"person":"Dylan Bochman","capture":"/protected/path.png","size":285432,"delete_after_seconds":300}
```

`both` returns two result objects and exits nonzero if either person could not
be selected, verified, or captured.

## Required workflow

1. Confirm that the request is from the authorized user in a direct session.
2. Run the narrowest requested command. Do not locate both people when only one
   was requested.
3. Continue only when the command returns `success: true` for the requested
   person. Never infer identity from sidebar position or a visually plausible
   map.
4. Inspect the capture privately and answer at coarse granularity unless the
   current request explicitly asks for a precise result.
5. Immediately after interpreting the image, run:

   ```bash
   findmy-locate cleanup
   ```

6. Do not reproduce the capture path after cleanup or place it in messages,
   logs, notes, or memory.

## Identity verification

The script:

1. Opens Find My and takes a Peekaboo accessibility snapshot.
2. Requires exactly one selected **People** tab.
3. Finds exactly one row whose accessibility name exactly matches the requested
   person; list position and keyboard order are ignored.
4. Clicks the fresh accessibility element ID.
5. Takes another snapshot and requires that same exact row ID and name to be
   selected before capture.
6. Fails closed on tab, name, duplicate-name, element-ID, or selected-row
   mismatch.

Do not replace this sequence with arrow-key counts, fuzzy text, coordinates, or
an unverified screenshot.

## Capture lifetime

Captures are created under `~/.openclaw/findmy-locate/` with directory mode
`0700` and file mode `0600`. Each successful capture schedules deletion after
five minutes by default. `FINDMY_CAPTURE_TTL_SECONDS` may be set from 30 to
3600 seconds. Expired captures are also pruned on each invocation, and
`cleanup` removes captures immediately after use.

TTL cleanup is a fallback, not a reason to retain the image until expiry.

## Failure handling

- `person_not_verified`: the People tab or exact name was unavailable. Do not
  capture or guess.
- `selection_mismatch`: the selected row did not exactly match the requested
  person. Do not use any image from that attempt.
- `accessibility_unavailable`: Peekaboo could not inspect Find My. Check local
  Accessibility permission without weakening verification.
- `capture_failed` or `capture_incomplete`: no usable image was retained.
- A failed `both` result is partial and nonzero; use only a successful person's
  result if that person was explicitly requested, then clean up every capture.

## Requirements

- Peekaboo with Screen Recording and Accessibility permission in the local GUI
  session
- Find My signed into the intended account
- The People tab visible and accessibility-readable

This skill does not continuously track anyone, determine household occupancy,
or trigger automation. Use `presence` for coarse home/away state and
`fi-collar` only for the dog's location.
