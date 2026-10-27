# Data and editing contracts

Read the repository's `data/README.md` and `docs/magazine-serve.md` for the live
implementation. Node 22+ is required; use the repository's npm commands. No npm
install is needed for the current dependency-free backend.

## Stores and APIs

| Store | Purpose | Editing route |
| --- | --- | --- |
| `content/projects/*.md`, `content/kitchen/*.md`, skills, aspirations | Editorial source | Reviewed Markdown edits; project catalog API also available |
| `src/generated/content.json` | Compiled projection | `npm run precompile`; never hand-edit as canonical data |
| `~/.openclaw/cabin-magazine/state.sqlite` | Shared checkbox/observation values and unsubmitted text drafts | `/api/state`, keyed by issue, page, and stable element ID |
| `data/spool/` then `data/journal.db` | Explicitly submitted notes, prompts, photos, checkbox evidence | `/api/inputs`; durable acceptance precedes ingest |
| `data/photo-catalog/catalog.json` | Private curated documentary-photo projection | Existing photo-catalog job; not generated art or a tracked editorial file |

`GET /api/state?issue=<id>&page=<id>` reads page values. `POST /api/state`
upserts `{issueId,pageId,elementId,value}`. Checkbox values are strings.
`draft:<article>:note|prompt|caption|quoteable` keys represent unsubmitted forms,
not journal evidence. Text saves while typing, with a local pending copy and a
page-hide flush. A visible waiting/error status is not a successful shared save.

`POST /api/inputs` is the explicit journal submission path. An accepted response
means the record reached the durable spool; browser-queued items are not yet
server receipts. `GET /api/journal?issueId=<id>&pageId=<id>&articleId=<id>` reads
up to 100 recent note/prompt entries from both validated pending records and the
canonical journal. Submitted entries appear beneath the form and remain
readable after reload. It does not expose media, private storage paths, or keys.
Never replace this with direct writes to the journal database or fabricate a
submission from a draft merely because it has been autosaved.

Fields in different issues deliberately have separate state. Preserve stable
page, article, and control identifiers when revising an existing issue. Reload
and cross-device verification must use the same issue and page. A blank field
may mean a different issue, a failed state request, or an unsent local draft;
do not erase the state database to troubleshoot it. Previously submitted text
can be read back; text never sent or retained in the browser may be unrecoverable.

## Catalog and refresh

The live editor is `/catalog/`. Read `/api/catalog/projects`, a project by ID,
`/api/catalog/meta`, or `/api/catalog/trace?date=YYYY-MM-DD`. Project PATCH accepts
only the validated frontmatter fields documented in `docs/magazine-serve.md`;
it preserves unpatched lines and body text. Writes change tracked Markdown but
do not commit. Recipe bodies, structured shopping quantities, pairings, and
source/quality evidence live in `content/kitchen/`.

Compile after catalog edits when the request needs the updated projection.
A change to a catalog record does not automatically revise every authored
`site/*-v2/*.html` page or replace its illustrations. Review both surfaces.

The server polls ingest and rolling refresh independently. Durable ingest
validates explicit `refreshSection`, commits canonical events, materializes
Markdown, and enqueues one idempotent refresh delivery. The consumer leases
work and journals the complete refresh write set for crash recovery. Missing
live issues remain pending; repeated processing failures can become dead letters.
`npm run journal:status` reports aggregate ingest health. Inspect only the
relevant entry or error when investigating private data.

Schema-driven rolling refresh changes Featured, Punchlist, or Kitchen plus
audit records; Journal and Prompt wait for the monthly reset. Weekly Kitchen
rotation can dirty the checkout without a user editing it. Inspect and preserve
those changes before Git operations. Do not run `refresh`, `refresh:consume`,
or `ingest` merely as a read-only health check.
