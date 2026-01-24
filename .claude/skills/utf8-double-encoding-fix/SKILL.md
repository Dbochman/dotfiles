---
name: utf8-double-encoding-fix
description: |
  Fix UTF-8 double-encoding corruption where special characters like arrows (→, ↔)
  become garbled sequences like "Ã¢ÂÂ" or "Ã¢Â†Â". Use when: (1) Non-ASCII
  characters display as mojibake after migration/serialization, (2) Arrows, emojis,
  or accented characters become Ã-prefixed garbage, (3) Content looks correct in
  source but corrupted after processing through gray-matter, YAML, or text pipelines.
  Covers detection via hex inspection and fix via latin-1 decode chain.
author: Claude Code
version: 1.0.0
date: 2026-01-23
---

# UTF-8 Double-Encoding Corruption Fix

## Problem

UTF-8 characters become garbled after passing through text processing pipelines.
For example, the arrow `→` (U+2192) becomes `Ã¢ÂÂ` or similar mojibake sequences.

This happens when UTF-8 bytes are incorrectly interpreted as Latin-1 (ISO-8859-1)
and then re-encoded as UTF-8, sometimes multiple times.

## Context / Trigger Conditions

- Non-ASCII characters (arrows, emojis, accented letters) display incorrectly
- Text shows patterns like `Ã¢ÂÂ`, `Ã©`, `Ã¼` instead of `→`, `é`, `ü`
- Corruption appeared after:
  - Migration scripts processing markdown/YAML
  - Cloudflare Workers handling content
  - Text pipelines with mixed encoding handling
  - gray-matter parsing YAML frontmatter
- Original content was correct (verified in git history or source)

## Solution

### Step 1: Diagnose the Encoding

Check the raw bytes to understand the corruption level:

```python
with open('corrupted-file.md', 'rb') as f:
    content = f.read()

# Find corrupted section
pos = content.find(b'55 ')  # or other known text near corruption
print("Bytes:", content[pos:pos+20].hex())
print("As UTF-8:", content[pos:pos+20].decode('utf-8', errors='replace'))
```

**Corruption patterns:**
- `c3 a2 c2 86 c2 92` = double-encoded `→` (one decode needed)
- `c3 83 c2 a2 c3 82 c2 86 c3 82 c2 92` = triple-encoded `→` (two decodes needed)

### Step 2: Apply the Fix

The fix is to decode as Latin-1 then re-encode as UTF-8, repeating until clean:

```python
import os

files_to_fix = ['file1.md', 'file2.md']

for filepath in files_to_fix:
    with open(filepath, 'rb') as f:
        content = f.read()

    text = content.decode('utf-8')

    # Check if it contains double-encoded UTF-8 (Ã pattern)
    while 'Ã' in text:
        try:
            text = text.encode('latin-1').decode('utf-8')
        except (UnicodeDecodeError, UnicodeEncodeError):
            break  # Can't decode further

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(text)

    print(f"Fixed: {filepath}")
```

### Step 3: Verify the Fix

```python
with open('fixed-file.md', 'r', encoding='utf-8') as f:
    content = f.read()

# Check for proper arrow character
if '→' in content:
    print("✓ Arrow character restored")
elif 'Ã' in content:
    print("✗ Still corrupted - may need another decode pass")
```

## Verification

After fixing:
1. The file should display correctly in editors
2. `grep "→"` should find the arrows (not `grep "Ã"`)
3. Any build/precompile process should pass without encoding errors

## Example

**Before (corrupted):**
```
55 Ã¢ÂÂ 98 Lighthouse, system fonts
Blog LCP 5.6s Ã¢ÂÂ 3.1s (45% faster)
```

**After (fixed):**
```
55 → 98 Lighthouse, system fonts
Blog LCP 5.6s → 3.1s (45% faster)
```

## Notes

### Why This Happens

The encoding chain that causes this:

1. Original: `→` stored as UTF-8 bytes `e2 86 92`
2. **Mistake**: Code reads bytes as Latin-1 characters: `â`, `†`, `'`
3. **Re-encode**: Those Latin-1 characters encoded as UTF-8: `c3 a2 c2 86 c2 92`
4. Result: `Ã¢ÂÂ` when displayed

If this happens twice (triple-encoding), you need two decode passes.

### Common Causes

- **Cloudflare Workers**: Missing `charset=utf-8` in Content-Type header when sending to GitHub API
- **gray-matter**: Writing YAML without proper string quoting for non-ASCII
- **Migration scripts**: Reading files without specifying encoding, defaulting to system locale
- **Shell pipelines**: Commands that don't preserve UTF-8 (e.g., some sed versions)

### Prevention

1. Always specify `encoding='utf-8'` when reading/writing files in Python
2. In Node.js, use `fs.readFileSync(path, 'utf-8')` explicitly
3. In Cloudflare Workers, set `Content-Type: application/json; charset=utf-8`
4. Quote strings containing non-ASCII in YAML frontmatter
5. Test with non-ASCII characters in CI to catch encoding issues early

### Related Issues

- If corruption shows as `\xe2\x86\x92` (escaped bytes), it's a different issue -
  the file was written in binary mode or bytes weren't decoded at all
- If corruption shows as `?` or `�`, the data was actually lost (replacement character)
  and may not be recoverable

## References

- [Python Unicode HOWTO](https://docs.python.org/3/howto/unicode.html)
- [The Absolute Minimum Every Developer Must Know About Unicode](https://www.joelonsoftware.com/2003/10/08/the-absolute-minimum-every-software-developer-absolutely-positively-must-know-about-unicode-and-character-sets-no-excuses/)
