---
name: pinchtab-react-click-fix
description: |
  Fix Pinchtab/headless browser JavaScript .click() not triggering React event handlers.
  Use when: (1) pinchtab eval element.click() runs but React app doesn't respond,
  (2) clicking links/buttons on React SPAs via headless browser automation has no effect,
  (3) OpenTable or other React sites ignore programmatic clicks but work with real user clicks.
  Solution: use dispatchEvent with full MouseEvent sequence (mousedown/mouseup/click) with
  real coordinates from getBoundingClientRect().
author: Claude Code
version: 1.0.0
date: 2026-03-01
---

# Pinchtab React Click Fix

## Problem
When automating React-based websites with Pinchtab (or any headless browser tool that uses
`element.click()` via JavaScript eval), React's synthetic event system may not process the
click. The element's native click fires but React's event delegation doesn't pick it up,
so no navigation or state change occurs.

## Context / Trigger Conditions
- `pinchtab eval "element.click()"` runs successfully (no error) but nothing happens
- The same click works when done manually in a real browser
- The target site uses React (e.g., OpenTable, Airbnb, many modern SPAs)
- `pinchtab snap -i -c` may not show the target elements in the accessibility tree
  (e.g., `<a href="" role="button">` elements with empty href)

## Solution

Replace `element.click()` with a full MouseEvent dispatch sequence using real coordinates:

```javascript
const rect = element.getBoundingClientRect();
const x = rect.x + rect.width / 2;
const y = rect.y + rect.height / 2;
const opts = { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 };
element.dispatchEvent(new MouseEvent('mousedown', opts));
element.dispatchEvent(new MouseEvent('mouseup', opts));
element.dispatchEvent(new MouseEvent('click', opts));
```

Key requirements:
1. **Full sequence**: mousedown → mouseup → click (React listens for the full sequence)
2. **Real coordinates**: `clientX`/`clientY` from `getBoundingClientRect()` (React uses these for event delegation)
3. **`bubbles: true`**: Events must bubble up to React's root event listener

## Why .click() Fails on React

React uses event delegation — it attaches a single event listener at the root DOM node and
dispatches synthetic events based on the event target and coordinates. A simple `.click()`
call creates a click event but may not include the coordinates or preceding mousedown/mouseup
events that React's event system expects.

## Verification

After dispatching, check that the page navigated or state changed:
```javascript
// Wait a few seconds, then check
const url = window.location.href;
const title = document.title;
```

## Example

From the OpenTable booking script (`opentable-book.sh`):
```javascript
// Find the best timeslot
const els = Array.from(document.querySelectorAll('a[role=button]'));
const timeEls = els.filter(e => /\d:\d\d [AP]M/.test(e.textContent.trim()));
// ... find closest to target time ...

// Click with full MouseEvent sequence
const rect = best.getBoundingClientRect();
const x = rect.x + rect.width/2;
const y = rect.y + rect.height/2;
const opts = {bubbles:true, cancelable:true, clientX:x, clientY:y, button:0};
best.dispatchEvent(new MouseEvent('mousedown', opts));
best.dispatchEvent(new MouseEvent('mouseup', opts));
best.dispatchEvent(new MouseEvent('click', opts));
```

## Notes

- This applies to any headless browser automation (Puppeteer, Playwright, Pinchtab) when
  using JavaScript eval to click elements on React sites
- Pinchtab's native `click <ref>` command should work correctly (it dispatches real
  CDP input events), but some elements don't appear in pinchtab's accessibility tree snapshot
  (e.g., `<a href="" role="button">` with empty href), making native click unusable
- Always prefer `pinchtab click <ref>` when the element IS in the snap tree
- For non-React sites, simple `.click()` usually works fine
