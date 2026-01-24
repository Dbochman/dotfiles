---
name: react-router-setsearchparams-gotcha
description: |
  Fix React Router setSearchParams overwriting existing query parameters. Use when:
  (1) URL query params disappear after navigation, (2) deep links with ?card=X or
  similar params stop working after board/page switch, (3) setSearchParams({ key: value })
  is erasing other params. Covers the functional update pattern to preserve existing params.
author: Claude Code
version: 1.0.0
date: 2026-01-23
---

# React Router setSearchParams Preserving Query Params

## Problem

When using React Router's `useSearchParams` hook, the common pattern of calling
`setSearchParams({ key: value })` with an object **completely replaces** all query
parameters instead of merging. This breaks deep links and loses user context.

## Context / Trigger Conditions

- URL query params disappear after clicking navigation elements
- Deep links like `?board=roadmap&card=my-card` lose the `card` param when switching boards
- State that was encoded in the URL vanishes after user interaction
- Using `setSearchParams({ board: newBoardId })` or similar object syntax

## Solution

Use the **functional update pattern** instead of the object pattern:

```typescript
// BAD - Overwrites all params
setSearchParams({ board: newBoardId });

// GOOD - Preserves existing params
setSearchParams((prev) => {
  const next = new URLSearchParams(prev);
  next.set('board', newBoardId);
  return next;
});
```

### When to Delete Params

If some params should be removed (e.g., card IDs are board-specific):

```typescript
setSearchParams((prev) => {
  const next = new URLSearchParams(prev);
  next.set('board', newBoardId);
  next.delete('card'); // Card IDs are board-specific
  return next;
});
```

### Helper Function (Optional)

For frequent use, create a helper:

```typescript
function updateSearchParams(
  setSearchParams: SetURLSearchParams,
  updates: Record<string, string | null>
) {
  setSearchParams((prev) => {
    const next = new URLSearchParams(prev);
    for (const [key, value] of Object.entries(updates)) {
      if (value === null) {
        next.delete(key);
      } else {
        next.set(key, value);
      }
    }
    return next;
  });
}

// Usage
updateSearchParams(setSearchParams, { board: newBoardId, card: null });
```

## Verification

1. Navigate to a URL with multiple query params: `?board=roadmap&card=my-card&view=grid`
2. Trigger the action that calls setSearchParams
3. Verify that unmodified params are preserved in the URL

## Example

**Before (broken)**:
```typescript
// BoardSelector.tsx
function handleBoardSelect(boardId: string) {
  setSearchParams({ board: boardId }); // Loses ?card=X, ?view=Y, etc.
  setIsOpen(false);
}
```

**After (fixed)**:
```typescript
// BoardSelector.tsx
function handleBoardSelect(boardId: string) {
  setSearchParams((prev) => {
    const next = new URLSearchParams(prev);
    next.set('board', boardId);
    next.delete('card'); // Intentionally clear board-specific params
    return next;
  });
  setIsOpen(false);
}
```

## Notes

- This applies to React Router v6+ (useSearchParams hook)
- The object syntax `setSearchParams({ key: value })` is convenient but dangerous
- Always use functional updates when the URL might have other params
- Consider what params should be intentionally cleared vs preserved
- TypeScript: The callback receives `URLSearchParams` and must return `URLSearchParams`

## References

- [React Router useSearchParams](https://reactrouter.com/en/main/hooks/use-search-params)
- Discovered during Codex PR review of kanban board selector feature
