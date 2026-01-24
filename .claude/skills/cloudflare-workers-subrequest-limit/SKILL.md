---
name: cloudflare-workers-subrequest-limit
description: |
  Fix "Too many subrequests" error in Cloudflare Workers. Use when: (1) Worker
  returns 500 with message "Too many subrequests", (2) Making multiple fetch
  calls in a loop (e.g., creating blobs for each file), (3) Processing batches
  of items that each require an API call. Covers the 50 subrequest limit and
  strategies to work around it.
author: Claude Code
version: 1.0.0
date: 2026-01-23
---

# Cloudflare Workers Subrequest Limit

## Problem

Cloudflare Workers have a limit of 50 subrequests (fetch calls) per invocation.
When processing multiple items that each require an API call (e.g., creating
GitHub blobs for multiple files), you'll hit this limit and get a 500 error.

## Context / Trigger Conditions

- Cloudflare Worker returns HTTP 500
- Error message: "Too many subrequests" or similar
- Worker is making fetch calls in a loop
- Processing more than ~45-50 items per request
- Common scenarios: batch file uploads, multi-file commits, bulk API operations

## Solution

### Strategy 1: Use batch APIs when available

For GitHub Trees API, use inline content instead of creating separate blobs:

```typescript
// BEFORE: Creates N+1 subrequests (1 per file + 1 for tree)
const blobShas = await Promise.all(
  files.map(file => createBlob(file.content, env))  // ❌ Each is a subrequest
);

// AFTER: Creates only 1 subrequest (tree with inline content)
const treeItems = files.map(file => ({
  path: file.path,
  mode: '100644',
  type: 'blob',
  content: file.content,  // ✅ Inline content, no separate blob
}));

await createTree(baseTreeSha, treeItems, env);  // Single subrequest
```

### Strategy 2: Batch requests within limit

If you must make individual calls, batch them:

```typescript
const BATCH_SIZE = 40;  // Leave headroom below 50

async function processBatches(items: Item[], env: Env) {
  const results = [];

  for (let i = 0; i < items.length; i += BATCH_SIZE) {
    const batch = items.slice(i, i + BATCH_SIZE);
    const batchResults = await Promise.all(
      batch.map(item => processItem(item, env))
    );
    results.push(...batchResults);

    // If more batches remain, you need a new Worker invocation
    // Consider using Durable Objects or queuing
  }

  return results;
}
```

### Strategy 3: Use Durable Objects for large operations

For operations exceeding 50 subrequests, split across multiple Worker invocations
or use Durable Objects which have their own subrequest budget per alarm/request.

### Strategy 4: Pre-aggregate on client

Have the client send already-aggregated data to reduce server-side API calls.

## Verification

1. Count fetch() calls in your Worker code path
2. Ensure the maximum possible calls is under 50
3. Test with the maximum expected payload size
4. Monitor for 500 errors in production

## Example

Atomic multi-file GitHub commit with 60+ files:

```typescript
// This approach works for any number of files
// because createTree accepts inline content
export async function commitFilesAtomic(
  files: Array<{ path: string; content: string }>,
  deletions: string[],
  message: string,
  parentSha: string,
  env: Env
): Promise<string> {
  const baseTreeSha = await getCommitTree(parentSha, env);  // 1 subrequest

  // Build tree items with INLINE content (no blob creation)
  const treeItems = files.map(file => ({
    path: file.path,
    mode: '100644' as const,
    type: 'blob' as const,
    content: file.content,  // Inline!
  }));

  // Add deletions
  for (const path of deletions) {
    treeItems.push({ path, mode: '100644', type: 'blob', sha: null });
  }

  const newTreeSha = await createTree(baseTreeSha, treeItems, env);  // 1 subrequest
  const newCommitSha = await createCommit(newTreeSha, parentSha, message, env);  // 1 subrequest
  await updateRef(newCommitSha, parentSha, env);  // 2 subrequests (get + update)

  return newCommitSha;  // Total: 5 subrequests for ANY number of files
}
```

## Notes

- The 50 limit applies to the total number of fetch() calls, not concurrent ones
- Paid plans have higher limits but still finite
- Durable Objects have separate limits per alarm/request
- Queue consumers have their own limits
- Always leave headroom (aim for <45) to account for error handling retries

## References

- [Cloudflare Workers Limits](https://developers.cloudflare.com/workers/platform/limits/)
- [GitHub Trees API](https://docs.github.com/en/rest/git/trees#create-a-tree)
