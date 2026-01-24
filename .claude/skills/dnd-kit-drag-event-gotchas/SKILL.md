---
name: dnd-kit-drag-event-gotchas
description: |
  Fix duplicate state updates when using dnd-kit for drag-and-drop. Use when:
  (1) State changes fire multiple times during a single drag operation,
  (2) History/audit entries are duplicated for intermediate positions,
  (3) handleDragOver is updating state that should only change on drop.
  Covers the difference between onDragOver (continuous) and onDragEnd (final).
author: Claude Code
version: 1.0.0
date: 2026-01-23
---

# dnd-kit Drag Event Gotchas

## Problem

When using dnd-kit's `onDragOver` handler to track state changes (like movement
history), you get duplicate entries for every column/container the dragged item
passes through, not just the final destination.

## Context / Trigger Conditions

- Using @dnd-kit/core for drag-and-drop functionality
- State updates (history, audit logs, analytics) in `handleDragOver`
- Seeing duplicate entries in state after a single drag operation
- History shows rapid oscillating changes (A→B→A→B→A→B)
- Timestamps on duplicate entries are milliseconds apart

## Solution

1. **Move final state changes to `handleDragEnd`**, not `handleDragOver`
2. **Track the starting position** in `handleDragStart` using a ref
3. **Compare start vs end** in `handleDragEnd` before recording changes
4. **Add deduplication** to prevent consecutive same-value entries

```typescript
// Track starting position
const dragStartRef = useRef<string | null>(null);

const handleDragStart = (event: DragStartEvent) => {
  const startContainer = findContainerByItemId(event.active.id);
  dragStartRef.current = startContainer?.id || null;
};

const handleDragOver = (event: DragOverEvent) => {
  // Only move items between containers here
  // Do NOT record history or analytics
};

const handleDragEnd = (event: DragEndEvent) => {
  const startId = dragStartRef.current;
  const endContainer = findContainerByItemId(event.active.id);
  dragStartRef.current = null; // Reset for next drag

  // Only record if actually moved to different container
  if (startId && endContainer && startId !== endContainer.id) {
    // Record history ONCE here
    recordMovement(event.active.id, startId, endContainer.id);
  }
};
```

## Verification

After implementing:
1. Drag an item from Container A to Container C (passing through B)
2. Check state/history - should show only one entry: A → C
3. Timestamps should not show rapid consecutive changes

## Example

Before fix (problematic):
```
history: [
  { from: "ideas", to: "todo", timestamp: "...42.716Z" },
  { from: "todo", to: "ideas", timestamp: "...42.720Z" },
  { from: "ideas", to: "todo", timestamp: "...42.727Z" },
  { from: "todo", to: "in-progress", timestamp: "...42.757Z" },
  // ... many more oscillating entries
]
```

After fix (correct):
```
history: [
  { from: "ideas", to: "in-progress", timestamp: "...42.800Z" }
]
```

## Notes

- `onDragOver` fires continuously as the drag passes over drop targets
- This is by design for visual feedback during drag
- Only use `onDragOver` for visual state (hover effects, placeholder positioning)
- Use `onDragEnd` for persistent state changes (database updates, history)
- Consider debouncing if you must update state in `onDragOver`

## References

- [dnd-kit Events Documentation](https://docs.dndkit.com/api-documentation/context-provider#events)
