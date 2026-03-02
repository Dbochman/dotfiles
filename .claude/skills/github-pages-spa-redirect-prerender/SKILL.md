---
name: github-pages-spa-redirect-prerender
description: |
  Fix broken redirects on GitHub Pages SPAs when renaming routes. Use when:
  (1) Old URLs return 404 instead of redirecting after a route rename/rebrand,
  (2) React Router <Navigate> redirects only work after clicking a link but not
  on direct URL access, (3) GitHub Pages serves raw 404 for old routes because
  no prerendered HTML exists. Covers prerendering redirect routes and handling
  trailing-slash variants.
author: Claude Code
version: 1.0.0
date: 2026-02-11
---

# GitHub Pages SPA Redirect Prerendering

## Problem
When renaming routes in a GitHub Pages SPA (e.g., `/projects/andre` to `/projects/echonest`),
adding `<Navigate to="/projects/echonest" replace />` in React Router is not enough. Direct
URL access to the old path returns a raw 404 from GitHub Pages because no `index.html` exists
at that path. The SPA shell never loads, so the client-side redirect never executes.

## Context / Trigger Conditions
- GitHub Pages hosting (or any static host without server-side redirects)
- Route renamed or rebranded in a React Router SPA
- Old URLs still have inbound traffic (bookmarks, search engines, external links)
- `<Navigate>` redirect works when navigating within the SPA but 404s on direct access

## Solution

### 1. Add redirect routes in React Router
```tsx
<Route path="/projects/andre" element={<Navigate to="/projects/echonest" replace />} />
```

### 2. Prerender the old redirect routes
Add old paths to your prerender script so GitHub Pages has an `index.html` at each path.
The prerendered HTML loads the SPA shell, which then performs the client-side redirect.

```javascript
// scripts/prerender.mjs
const redirectRoutes = [
  '/projects/andre',
  '/projects/andre/',
  '/blog/2026-02-04-andre-collaborative-music-queue',
  '/blog/2026-02-04-andre-collaborative-music-queue/',
];

const routes = [
  ...dynamicRoutes,
  ...redirectRoutes,  // Include old routes so they get HTML files
];
```

### 3. Handle trailing-slash variants
Analytics data often shows traffic to both `/path` and `/path/`. Add redirect routes
and prerender entries for **both variants**.

```tsx
<Route path="/projects/andre" element={<Navigate to="/projects/echonest" replace />} />
<Route path="/projects/andre/" element={<Navigate to="/projects/echonest" replace />} />
```

## Verification
1. Build the site with prerendering
2. Check that `dist/projects/andre/index.html` exists (the prerendered file)
3. Deploy and verify direct access to the old URL redirects to the new one
4. Test both with and without trailing slash

## Example
After rebranding Andre to EchoNest:
- `/projects/andre` -> prerendered HTML loads SPA -> `<Navigate>` redirects to `/projects/echonest`
- `/projects/andre/` -> same flow with trailing-slash variant
- `/blog/2026-02-04-andre-collaborative-music-queue` -> redirects to new blog slug

## Notes
- This applies to any static host without server-side redirect support (GitHub Pages, S3, etc.)
- Hosts like Netlify, Vercel, or Cloudflare Pages support `_redirects` files or server-side rules, making prerendering unnecessary
- Check analytics data for trailing-slash variants before assuming only one form exists
- The prerendered HTML is just the SPA shell — it loads JS, which runs React Router, which performs the redirect. The actual redirect is still client-side.
- Consider also updating `sitemap.xml` to use the new URLs (old URLs should not remain in sitemap)
