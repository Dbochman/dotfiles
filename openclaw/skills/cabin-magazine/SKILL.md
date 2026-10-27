---
name: cabin-magazine
description: Create, revise, and troubleshoot The Cabin magazine for Julia and Dylan, including monthly issues, project and recipe catalogs, journal persistence, documentary photos, and instructional illustrations. Use for cabin magazine or issue requests, not cabin device routines.
metadata: {"openclaw":{"requires":{"bins":["node","npm","git"]}}}
---

# The Cabin magazine

The backend and editorial source live at `/Users/dbochman/repos/cabin` on the
Mac mini. Read its `README.md`, `docs/MAGAZINE_STYLE_GUIDE.md`, and relevant
source before a generation or backend change. Keep this skill in
`~/dotfiles/openclaw/skills/cabin-magazine`; the installed copy belongs under
`~/.openclaw/skills/cabin-magazine`.

## Establish which issue and workflow

Check repository status, branch, and the requested issue before editing.
The live server is `ai.openclaw.cabin-magazine` on port 8555. Its root URL is
historical Volume One; use the explicit issue URL. As of October 26, 2026,
November's working issue is `/vol-1-no-03-draft-v2/`, with eight HTML pages.
October's multi-page issue is `/vol-1-no-02-draft-v2/`. Do not choose an issue
solely from a default CLI argument or the root page.

There are two distinct authoring paths:

- **Existing multi-page `-v2` issue:** Julia's authored pages live directly in
  `site/<issue>/`, with issue CSS, photos, and illustrations. Edit those pages
  for a requested revision. Preserve page/issue IDs, checkbox IDs,
  `data-observation` keys, capture forms, and shared scripts. Source catalog
  changes belong in `content/` too; compiling a catalog does not rewrite these
  authored pages. Preserve the layout and editorial choices already there.
- **New schema-driven issue:** `npm run issue -- new --issue=vol-1-no-NN-draft
  --date=YYYY-MM-DD` generates `content/issues/<issue>/` sections and a
  single-page `site/<issue>/index.html`. Substitute the requested date and an
  unused two-digit issue number. The CLI only accepts `vol-N-no-NN-draft`;
  it cannot generate or approve a `-v2` shell. `npm run build` is not a
  replacement for monthly generation. `--offline` is a deterministic fixture,
  not a finished editorial issue; never silently fall back to it after a model
  failure. `--force` replaces existing generated work—use only when the user
  requested regeneration and existing edits have been preserved.

Read [data and editing](references/data-and-editing.md) for backend state,
catalog APIs, persistence, and refresh boundaries. Read
[illustrations](references/illustrations.md) when adding or changing artwork.

## Produce a useful issue

Ground copy in current catalog records, dated journal evidence, and the
requested season. Follow the style guide: specific observations, finite useful
steps, and room for enjoyment; distinguish observed, confirmed, proposed, and
unknown. A checked box or photo alone does not mark an entire project complete.
Quote journal words only when explicitly marked quoteable.

Check Julia's latest recipe and project preferences in source records. For
example, lentil shepherd's pie is Julia's primary recipe; lamb shepherd's pie
is Dylan's companion, not its replacement. Honor recipe `pair` metadata and
editorial body notes. The generic weekly selector currently rotates by season
and sorted IDs; it does not enforce that pairing preference. Review its output
before presenting it as satisfying the request. Use the current vegetarian
peanut soup and matching illustration when editing November; do not restore
its superseded chicken-stock illustration.

Use actual documentary photographs from the curated catalog for places and
observations. Generated illustrations teach a specific step; they are not
photographic evidence. Review the illustration manifest and the rendered page,
not just a successful image-generation response.

## Verify and hand off

- Confirm the intended issue URL, selected content, image placement, image URLs,
  and absence of stale recipe artwork. Check all affected pages, not only the cover.
- For interactive changes, test typing, immediate reload/navigation, save failures,
  successful submission, and readback in another browser context. Use an isolated
  runtime for synthetic notes; do not insert them into the household journal.
- `npm test` includes tests that rebuild checked-in output. Run it in a disposable
  checkout. Some older tests hardcode catalog choices that drift as content changes;
  compare failures with the base branch and report them rather than claiming green.
- Read-only live checks: `curl -fsS http://127.0.0.1:8555/api/health` and the exact
  issue page. Static assets are read from disk; backend module changes require a
  deliberate reload of the existing service after testing. Do not start a second
  production server or stop someone else's tmux agent.
- Generation, editorial approval, Git commit/push/merge, and publication are
  separate actions. Carry out those the user requested; do not infer editorial
  approval from a request to synchronize code. For schema drafts, `issue approve`
  and `issue veto` are explicit local editorial actions and do not publish.

Report what changed, the exact preview URL, saved-state/illustration verification,
and any remaining failures. Keep private runtime databases, spool files, photo
catalog projections, and credentials out of Git.
