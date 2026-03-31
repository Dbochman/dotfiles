---
name: skill-creator
description: >
  Create new OpenClaw skills, modify and improve existing skills, and measure
  skill performance with evals. Use when users want to create a skill from
  scratch, update or optimize an existing skill, run evals to test a skill,
  benchmark skill performance with variance analysis, or optimize a skill's
  description for better triggering accuracy. Also use when asked to "make a
  skill", "turn this into a skill", "improve this skill", or "test this skill".
---

# Skill Creator

A skill for creating new OpenClaw skills and iteratively improving them with
eval-driven development.

## Overview

The skill creation loop:

1. Capture intent — understand what the skill should do
2. Interview — nail down edge cases, formats, dependencies
3. Write SKILL.md draft — following OpenClaw conventions
4. Run test cases — with-skill vs baseline, in parallel via subagents
5. Grade and benchmark — quantitative evals while runs execute
6. Review results — static HTML viewer (headless Mini, no browser)
7. Iterate — improve based on feedback, repeat until satisfied
8. Optimize description — automated trigger accuracy tuning

Jump in wherever the user is in this process. If they already have a draft,
skip to eval/iterate. If they say "just vibe with me", skip the formal evals.

## Environment

This skill runs on a **headless Mac Mini** via OpenClaw. Key constraints:

- **No browser/display** — use `--static` mode for all viewers, save HTML files
  for the user to download or view via screen share
- **`claude -p` requires keychain access** — the eval/optimization scripts
  (`run_eval.py`, `run_loop.py`, `improve_description.py`) use `claude -p`
  which needs macOS keychain auth. These scripts **will not work** from the
  OpenClaw gateway LaunchAgent session. Use the core workflow (capture intent
  → write SKILL.md → iterate via conversation) instead. The eval scripts are
  available for manual use via Terminal or screen share.
- **Skills deploy to `~/.openclaw/skills/`** — real directory copies, not symlinks
  (OpenClaw rejects symlinks via `fs.realpathSync()`)
- **`claude` CLI available** at `/opt/homebrew/opt/node@22/bin/claude`
- **Python 3** at `/opt/homebrew/bin/python3`
- **Subagents available** — spawn test runs in parallel
- **Skill template** at `openclaw/skills/TEMPLATE/SKILL.md` in the dotfiles repo

## Communicating with the user

Adapt your language to context cues. Default: "evaluation" and "benchmark" are
fine; explain "JSON" or "assertion" before using them unless the user signals
technical fluency.

---

## Creating a skill

### Capture Intent

1. What should this skill enable the agent to do?
2. When should this skill trigger? (trigger words, contexts)
3. What's the expected output format?
4. Should we set up test cases? Skills with objectively verifiable outputs
   (file transforms, API calls, device control) benefit from test cases.
   Skills with subjective outputs (writing, conversation) often don't.

If the current conversation already contains a workflow to capture ("turn this
into a skill"), extract answers from conversation history first.

### Interview and Research

Ask about edge cases, formats, dependencies. Check available MCPs for research.
Wait to write test prompts until this is solid.

### Write the SKILL.md

Follow OpenClaw conventions (see the TEMPLATE skill for reference):

**Frontmatter (required):**
- `name`: kebab-case, under 64 chars
- `description`: Primary triggering mechanism. Include what it does AND when to
  use it. Be slightly "pushy" — Claude undertriggers skills. All "when to use"
  info goes here, not in the body.
- `allowed-tools`: Restrict tool access if the skill uses CLI wrappers
- `metadata`: OpenClaw-specific metadata (emoji, required bins)

**Body:** Instructions for using the skill and its bundled resources.

### Skill Writing Guide

#### Anatomy of a Skill

```
skill-name/
├── SKILL.md (required)
│   ├── YAML frontmatter (name, description required)
│   └── Markdown instructions
└── Bundled Resources (optional)
    ├── scripts/    - Executable code for deterministic/repetitive tasks
    ├── references/ - Docs loaded into context as needed
    └── assets/     - Files used in output (templates, icons, fonts)
```

#### Progressive Disclosure

Skills use a three-level loading system:
1. **Metadata** (name + description) — always in context (~100 words)
2. **SKILL.md body** — loaded when skill triggers (<500 lines ideal)
3. **Bundled resources** — loaded as needed (unlimited; scripts can execute
   without loading into context)

Keep SKILL.md under 500 lines. If approaching this limit, split into reference
files with clear pointers about when to read them.

#### Set Appropriate Degrees of Freedom

Match specificity to task fragility:

- **High freedom** (prose instructions): multiple valid approaches, context-dependent
- **Medium freedom** (pseudocode/scripts with params): preferred pattern exists
- **Low freedom** (specific scripts, few params): fragile operations, consistency critical

#### Writing Patterns

Use imperative form. Explain the **why** behind instructions — today's LLMs are
smart and respond better to reasoning than rigid MUSTs. If you find yourself
writing ALWAYS or NEVER in caps, reframe as an explanation.

Include examples where helpful:
```markdown
## Commit message format
**Example:**
Input: Added user authentication with JWT tokens
Output: feat(auth): implement JWT-based authentication
```

### Test Cases

After writing the skill draft, create 2-3 realistic test prompts. Share them
with the user for feedback. Save to `evals/evals.json`:

```json
{
  "skill_name": "example-skill",
  "evals": [
    {
      "id": 1,
      "prompt": "User's task prompt",
      "expected_output": "Description of expected result",
      "files": []
    }
  ]
}
```

See `references/schemas.md` for the full schema including assertions.

---

## Running and evaluating test cases

This section is one continuous sequence. Put results in
`<skill-name>-workspace/` as a sibling to the skill directory. Organize by
iteration (`iteration-1/`, `iteration-2/`, etc.) with each test case in its
own directory (`eval-0/`, `eval-1/`, etc.).

### Step 1: Spawn all runs in the same turn

For each test case, spawn two subagents simultaneously — one with the skill,
one baseline. Launch everything at once.

**With-skill run:**
```
Execute this task:
- Skill path: <path-to-skill>
- Task: <eval prompt>
- Input files: <eval files if any, or "none">
- Save outputs to: <workspace>/iteration-<N>/eval-<ID>/with_skill/outputs/
```

**Baseline run** (same prompt, no skill or old skill version):
- **New skill**: no skill at all → save to `without_skill/outputs/`
- **Improving existing**: snapshot the old version, point baseline at snapshot →
  save to `old_skill/outputs/`

Write `eval_metadata.json` for each test case with descriptive names.

### Step 2: While runs execute, draft assertions

Use the wait time to draft quantitative assertions. Good assertions are
objectively verifiable with descriptive names. Don't force assertions onto
subjective outputs.

Update `eval_metadata.json` and `evals/evals.json` with assertions.

### Step 3: Capture timing data

When each subagent completes, the notification contains `total_tokens` and
`duration_ms`. Save immediately to `timing.json` — this data isn't persisted
elsewhere.

### Step 4: Grade, aggregate, and generate viewer

Once all runs complete:

1. **Grade each run** — spawn a grader subagent using `agents/grader.md`.
   Save to `grading.json`. Use fields `text`, `passed`, `evidence` (the viewer
   depends on these exact names). For programmatically checkable assertions,
   write and run a script.

2. **Aggregate** — run from the skill-creator directory:
   ```bash
   python3 -m scripts.aggregate_benchmark <workspace>/iteration-N --skill-name <name>
   ```
   Produces `benchmark.json` and `benchmark.md`.

3. **Analyst pass** — read benchmark data, surface patterns. See
   `agents/analyzer.md` ("Analyzing Benchmark Results" section).

4. **Generate static viewer:**
   ```bash
   python3 <skill-creator-path>/eval-viewer/generate_review.py \
     <workspace>/iteration-N \
     --skill-name "my-skill" \
     --benchmark <workspace>/iteration-N/benchmark.json \
     --static <workspace>/iteration-N/review.html
   ```
   For iteration 2+, add `--previous-workspace <workspace>/iteration-<N-1>`.

5. **Tell the user** the viewer HTML is at the path — they can view it via
   screen share or download it.

### Step 5: Read feedback

When the user provides feedback (inline or via `feedback.json`), focus
improvements on test cases with specific complaints. Empty feedback = looked fine.

---

## Improving the skill

### How to think about improvements

1. **Generalize from feedback.** Don't overfit to the test examples — make
   changes that would help across many different prompts. Rather than fiddly
   constraints, try different metaphors or patterns.

2. **Keep the prompt lean.** Read transcripts, not just outputs. If the skill
   makes the model waste time on unproductive steps, trim those instructions.

3. **Explain the why.** Transmit understanding into instructions. If you find
   yourself writing rigid constraints, reframe as reasoning.

4. **Look for repeated work.** If all test runs independently wrote similar
   helper scripts, bundle that script in `scripts/` so future invocations
   don't reinvent the wheel.

### The iteration loop

1. Apply improvements to the skill
2. Rerun all test cases into `iteration-<N+1>/`
3. Generate viewer with `--previous-workspace` pointing at previous iteration
4. Wait for user review
5. Read feedback, improve again, repeat

Keep going until the user is happy, feedback is empty, or progress stalls.

---

## Blind comparison (advanced, optional)

For rigorous A/B comparison between skill versions, read `agents/comparator.md`
and `agents/analyzer.md`. Give two outputs to an independent agent without
revealing which skill produced which.

---

## Description Optimization

After creating or improving a skill, offer to optimize the description for
better triggering accuracy.

### Step 1: Generate trigger eval queries

Create 20 eval queries — mix of should-trigger and should-not-trigger:

```json
[
  {"query": "the user prompt", "should_trigger": true},
  {"query": "another prompt", "should_trigger": false}
]
```

Queries must be realistic with concrete details (file paths, personal context,
abbreviations, typos). For should-not-trigger queries, focus on **near-misses**
— adjacent domains that share keywords but need something different.

### Step 2: Review with user

Present the eval set using the HTML template:

1. Read `assets/eval_review.html`
2. Replace placeholders: `__EVAL_DATA_PLACEHOLDER__` → JSON array,
   `__SKILL_NAME_PLACEHOLDER__` → name, `__SKILL_DESCRIPTION_PLACEHOLDER__` → description
3. Write to workspace and tell the user where to find it

### Step 3: Run the optimization loop

```bash
cd <skill-creator-path> && \
python3 -m scripts.run_loop \
  --eval-set <path-to-trigger-eval.json> \
  --skill-path <path-to-skill> \
  --model <model-id> \
  --max-iterations 5 \
  --report none \
  --verbose
```

Use `--report none` since we're headless. The loop handles train/test split
(60/40), runs each query 3 times, and uses Claude with extended thinking to
propose improvements.

### Step 4: Apply the result

Take `best_description` from the JSON output and update the skill's frontmatter.
Report before/after and scores to the user.

---

## Reference files

Agent instructions (read when spawning the relevant subagent):
- `agents/grader.md` — evaluate assertions against outputs
- `agents/comparator.md` — blind A/B comparison
- `agents/analyzer.md` — analyze why one version beat another / benchmark patterns

Reference documentation:
- `references/schemas.md` — JSON schemas for evals.json, grading.json, etc.
