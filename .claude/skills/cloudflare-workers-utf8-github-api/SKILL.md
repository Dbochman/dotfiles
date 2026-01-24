---
name: cloudflare-workers-utf8-github-api
description: |
  Fix UTF-8 encoding corruption when Cloudflare Workers send content to GitHub API.
  Use when: (1) Non-ASCII characters like arrows (→), emojis, or accented letters
  become corrupted (e.g., → becomes Ã¢ÂÂ or â), (2) YAML parsing fails with
  "non-printable characters" error, (3) Content looks correct locally but corrupted
  after GitHub commit. Covers charset header and YAML string quoting.
author: Claude Code
version: 1.0.0
date: 2026-01-23
---

# Cloudflare Workers UTF-8 Encoding with GitHub API

## Problem

When sending content from Cloudflare Workers to GitHub's API (especially the
Trees/Blobs API), non-ASCII characters get corrupted. The UTF-8 bytes are
interpreted as Latin-1/ISO-8859-1, causing multi-byte characters to become
garbled sequences like `Ã¢ÂÂ` instead of `→`.

## Context / Trigger Conditions

- Cloudflare Worker making POST requests to GitHub API
- Content contains non-ASCII characters (arrows, emojis, accented text)
- After commit, characters appear corrupted in the file
- YAML/gray-matter parsing fails with "non-printable characters" error
- Character sequences like `â`, `Ã¢ÂÂ`, `Ã©` appear where Unicode should be

## Solution

### 1. Add charset to Content-Type header

```typescript
const response = await fetch(`${GITHUB_API}${endpoint}`, {
  ...options,
  headers: {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'Content-Type': 'application/json; charset=utf-8',  // ADD charset=utf-8
    'User-Agent': 'my-worker',
    ...options.headers,
  },
});
```

### 2. Quote non-ASCII strings in YAML

When serializing YAML, detect and quote strings with non-ASCII characters:

```typescript
function escapeYamlString(value: string): string {
  // Check for non-ASCII characters (any character > 127)
  const hasNonAscii = /[^\x00-\x7F]/.test(value);

  if (
    hasNonAscii ||
    value.includes(':') ||
    value.includes('#') ||
    // ... other YAML special chars
  ) {
    // Use double quotes and escape internal quotes/newlines
    return `"${value
      .replace(/\\/g, '\\\\')
      .replace(/"/g, '\\"')
      .replace(/\n/g, '\\n')}"`;
  }
  return value;
}
```

### 3. Use utf-8 encoding for blobs (if creating separately)

```typescript
const response = await fetch(`${GITHUB_API}/repos/.../git/blobs`, {
  method: 'POST',
  headers: { /* ... with charset=utf-8 */ },
  body: JSON.stringify({
    content: fileContent,
    encoding: 'utf-8',  // Explicitly specify encoding
  }),
});
```

## Verification

1. Create a file with non-ASCII content (arrows, emojis, accented characters)
2. Commit via your Worker
3. Fetch the raw file from GitHub
4. Verify characters are preserved correctly

Test string: `Create migration script (JSON → markdown files)`

## Example

Before fix:
```yaml
text: "Create migration script (JSON Ã¢ÂÂ markdown files)"
```

After fix:
```yaml
text: "Create migration script (JSON → markdown files)"
```

## Notes

- This affects the GitHub Trees API, Blobs API, and Contents API
- The corruption happens because JSON.stringify produces valid UTF-8, but without
  the charset header, the receiving end may interpret it differently
- Always use `encoding: 'utf-8'` when creating blobs, not `encoding: 'base64'`
  unless you're explicitly base64-encoding the content yourself
- If content was already corrupted and saved, you need to restore from a clean
  commit and re-save with the fix in place

## References

- [GitHub REST API: Create a blob](https://docs.github.com/en/rest/git/blobs#create-a-blob)
- [MDN: Content-Type](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Type)
