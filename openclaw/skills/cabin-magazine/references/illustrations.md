# Photos and instructional illustrations

Use the backend adapters in `scripts/providers/` and the source contracts in
`scripts/lib/illustrations.js` and `scripts/lib/prepare-illustrations.js`.

## Generated monthly drafts

Online generation requests zero to five useful single-step details from the
selected recipe, project, Punchlist, or skill. Briefs bind to the exact selected
source ID and one-based step; the focus/caption must come from that instruction.
The look is pencil/ink with light watercolor on warm cream paper. No lettering
in images; captions remain editable HTML. Do not invent an entire task diagram
when one detail needs explanation.

The current adapter uses `openai/gpt-image-2`, medium quality, and
`openai/gpt-6-astra` for the accuracy, single-detail, watercolor, and no-text
review. It uses existing OpenClaw authentication; do not print credentials.
The normal path makes one generation and one review call per requested image,
without automatic retries. Rejected/failed images are omitted and recorded;
a successful draft is not evidence that all illustrations passed.

Accepted PNGs and `illustrations.json` live in both `content/issues/<issue>/`
and `site/<issue>/`. Manifests record source steps, models, hashes, and review.
Preview, approval, and rolling refresh reuse assets without model calls, and
retain an image only for an exact matching source step. A new recipe does not
inherit the old recipe's diagram. `--no-illustrations`,
`MAGAZINE_ILLUSTRATIONS=off`, and `--offline` suppress generation.

Overrides: `MAGAZINE_ILLUSTRATION_MODEL`,
`MAGAZINE_ILLUSTRATION_REVIEW_MODEL`, `MAGAZINE_OPENCLAW_BIN`, and
`MAGAZINE_OPENCLAW_NODE_BIN`. The illustration adapter now falls back to its
running Node when its old pinned runtime is absent. Use an executable Node
compatible with the installed OpenClaw; do not copy the obsolete pin into skills.

## November's authored shell

`scripts/generate-v2-illustrations.js` is a **November-specific batch**, not the
monthly generator. Its six hand-picked briefs cover rose mulch, leek sauté,
granola, peanut soup, tool shadows, and snow stakes. It reuses accepted on-disk
assets, retries a brief once, has a specific peanut-soup fallback, and stops on
auth/quota errors. These rules differ from the ordinary monthly path.

Inspect and deliberately adapt briefs before invoking this script for a new
request. Running it does not automatically place every resulting figure into
HTML. November's accepted figures were placed in `this-month.html`,
`cooking.html`, and `gardening.html`; verify those exact pages, relative PNG
paths, captions, current recipe steps, and `draft.css`. Its stock-pot sketch was
removed after the recipe changed; the leek sketch was corrected to show the
skillet step rather than knife work. Do not regenerate accepted artwork simply
to synchronize a checkout or update a skill.

## Documentary cover photographs

The private photo catalog validates schema, permissions, IDs, tags, privacy
flags, and JPEG bytes. Online cover evaluation reviews a bounded contact sheet
of at most twelve eligible photos; future and identifiable-person photos are
excluded from automatic selection. A promoted cover must satisfy the versioned
score, margin, dimension, and concern policy; otherwise use the curated/legacy
fallback. `cover-decision.json` records the rationale without private paths.
Generated art never belongs in this documentary catalog.
