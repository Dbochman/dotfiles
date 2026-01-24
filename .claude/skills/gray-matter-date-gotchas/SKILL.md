---
name: gray-matter-date-gotchas
description: |
  Handle gray-matter's automatic Date parsing in YAML frontmatter. Use when:
  (1) Zod validation fails with "expected string, received Date" on date fields,
  (2) Building content collection systems with markdown frontmatter,
  (3) Dates in frontmatter become Date objects instead of strings,
  (4) Migration/precompile scripts process YAML with date fields.
  Covers z.preprocess coercion and ensuring dates are written as strings.
author: Claude Code
version: 1.0.0
date: 2026-01-23
---

# Gray-Matter Date Parsing Gotchas

## Problem

The `gray-matter` library automatically parses YAML date values as JavaScript `Date` objects. This breaks validation when using Zod schemas that expect strings, and can cause silent data corruption in migration pipelines.

## Context / Trigger Conditions

- Zod validation fails with "expected string, received object" on date fields
- `frontmatter.createdAt instanceof Date` returns `true` unexpectedly
- Dates work when hardcoded but fail when read from markdown files
- Building content collection systems (like Astro, but custom)
- Migration scripts that read/write YAML frontmatter

## Root Cause

YAML 1.1 (which gray-matter uses) auto-detects dates in these formats:
- `2026-01-23` (date only)
- `2026-01-23T00:00:00.000Z` (ISO 8601)

Gray-matter parses these as `Date` objects, not strings.

## Solution

### 1. Add Zod Preprocessing

Coerce Date objects to ISO strings before validation:

```typescript
const isoDateString = z.preprocess(
  (val) => {
    if (val instanceof Date) return val.toISOString();
    return val;
  },
  z.string().refine(
    (val) => {
      const fullIso = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{3})?Z?$/;
      const dateOnly = /^\d{4}-\d{2}-\d{2}$/;
      return fullIso.test(val) || dateOnly.test(val);
    },
    { message: 'Invalid date format. Expected ISO 8601' }
  )
);
```

### 2. Ensure Strings When Writing

When writing frontmatter, explicitly convert dates:

```javascript
function toISOString(value) {
  if (!value) return value;
  if (value instanceof Date) return value.toISOString();
  if (typeof value === 'string') return value;
  return String(value);
}

// When building frontmatter object:
frontmatter.createdAt = toISOString(card.createdAt) || new Date().toISOString();
```

### 3. Quote Dates in YAML (Alternative)

Quoted strings aren't parsed as dates:

```yaml
createdAt: "2026-01-23T00:00:00.000Z"  # String
createdAt: 2026-01-23                   # Becomes Date object!
```

## Verification

```javascript
const { data } = matter(content);
console.log(typeof data.createdAt);  // Should be 'string'
console.log(data.createdAt);         // Should be ISO string, not [object Date]
```

## Example

Before (breaks):
```typescript
const schema = z.object({
  createdAt: z.string()  // Fails! gray-matter returns Date
});
```

After (works):
```typescript
const schema = z.object({
  createdAt: z.preprocess(
    (val) => val instanceof Date ? val.toISOString() : val,
    z.string()
  )
});
```

## Notes

- This affects any YAML parser following YAML 1.1 spec, not just gray-matter
- The preprocessing approach is more robust than quoting (handles existing content)
- Always apply preprocessing in both the main schema AND any inline copies (e.g., precompile scripts)
- Consider adding the same preprocessing to history arrays with timestamp fields

## Related

- gray-matter docs: https://github.com/jonschlinkert/gray-matter
- YAML 1.1 timestamp type: https://yaml.org/type/timestamp.html
- Zod preprocessing: https://zod.dev/?id=preprocess
