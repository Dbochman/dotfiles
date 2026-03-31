#!/usr/bin/env python3
"""Improve a skill description based on eval results.

Takes eval results (from run_eval.py) and generates an improved description
using Claude via `claude -p` subprocess (no API key needed — inherits CLI auth).

Adapted from Anthropic's version which used the anthropic SDK directly.
On the Mac Mini, Claude Code uses OAuth (Claude Max), not API keys, so we
shell out to `claude -p` instead.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from scripts.utils import parse_skill_md


def improve_description(
    skill_name: str,
    skill_content: str,
    current_description: str,
    eval_results: dict,
    history: list[dict],
    model: str,
    test_results: dict | None = None,
    log_dir: Path | None = None,
    iteration: int | None = None,
) -> str:
    """Call Claude via CLI to improve the description based on eval results."""
    failed_triggers = [
        r for r in eval_results["results"]
        if r["should_trigger"] and not r["pass"]
    ]
    false_triggers = [
        r for r in eval_results["results"]
        if not r["should_trigger"] and not r["pass"]
    ]

    # Build scores summary
    train_score = f"{eval_results['summary']['passed']}/{eval_results['summary']['total']}"
    if test_results:
        test_score = f"{test_results['summary']['passed']}/{test_results['summary']['total']}"
        scores_summary = f"Train: {train_score}, Test: {test_score}"
    else:
        scores_summary = f"Train: {train_score}"

    prompt = f"""You are optimizing a skill description for an OpenClaw/Claude Code skill called "{skill_name}". A "skill" has a title and description that Claude sees when deciding whether to use the skill, and then if it does use the skill, it reads the .md file which has more details and potentially links to other resources.

The description appears in Claude's "available_skills" list. When a user sends a query, Claude decides whether to invoke the skill based solely on the title and on this description. Your goal is to write a description that triggers for relevant queries, and doesn't trigger for irrelevant ones.

Here's the current description:
<current_description>
"{current_description}"
</current_description>

Current scores ({scores_summary}):
<scores_summary>
"""
    if failed_triggers:
        prompt += "FAILED TO TRIGGER (should have triggered but didn't):\n"
        for r in failed_triggers:
            prompt += f'  - "{r["query"]}" (triggered {r["triggers"]}/{r["runs"]} times)\n'
        prompt += "\n"

    if false_triggers:
        prompt += "FALSE TRIGGERS (triggered but shouldn't have):\n"
        for r in false_triggers:
            prompt += f'  - "{r["query"]}" (triggered {r["triggers"]}/{r["runs"]} times)\n'
        prompt += "\n"

    if history:
        prompt += "PREVIOUS ATTEMPTS (do NOT repeat these -- try something structurally different):\n\n"
        for h in history:
            train_s = f"{h.get('train_passed', h.get('passed', 0))}/{h.get('train_total', h.get('total', 0))}"
            test_s = f"{h.get('test_passed', '?')}/{h.get('test_total', '?')}" if h.get('test_passed') is not None else None
            score_str = f"train={train_s}" + (f", test={test_s}" if test_s else "")
            prompt += f'<attempt {score_str}>\n'
            prompt += f'Description: "{h["description"]}"\n'
            if "results" in h:
                prompt += "Train results:\n"
                for r in h["results"]:
                    status = "PASS" if r["pass"] else "FAIL"
                    prompt += f'  [{status}] "{r["query"][:80]}" (triggered {r["triggers"]}/{r["runs"]})\n'
            if h.get("note"):
                prompt += f'Note: {h["note"]}\n'
            prompt += "</attempt>\n\n"

    prompt += f"""</scores_summary>

Skill content (for context on what the skill does):
<skill_content>
{skill_content}
</skill_content>

Based on the failures, write a new and improved description that is more likely to trigger correctly. Generalize from the failures to broader categories of user intent -- don't produce an ever-expanding list of specific queries.

Your description should be about 100-200 words (max 1024 characters). Tips:
- Use imperative form: "Use this skill for" rather than "this skill does"
- Focus on user intent, not implementation details
- Make it distinctive -- it competes with other skills for Claude's attention
- If getting lots of failures, try different sentence structures or wordings

Respond with only the new description text in <new_description> tags, nothing else."""

    # Call claude -p as subprocess
    cmd = ["claude", "-p", prompt, "--output-format", "text"]
    if model:
        cmd.extend(["--model", model])

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        text = result.stdout.strip()
    except subprocess.TimeoutExpired:
        print("Warning: claude -p timed out after 120s", file=sys.stderr)
        return current_description

    if result.returncode != 0:
        print(f"Warning: claude -p failed (exit {result.returncode}): {result.stderr[:200]}", file=sys.stderr)
        return current_description

    # Parse out the <new_description> tags
    match = re.search(r"<new_description>(.*?)</new_description>", text, re.DOTALL)
    description = match.group(1).strip().strip('"') if match else text.strip().strip('"')

    # Log the transcript
    transcript: dict = {
        "iteration": iteration,
        "prompt_length": len(prompt),
        "response": text,
        "parsed_description": description,
        "char_count": len(description),
        "over_limit": len(description) > 1024,
    }

    # If over 1024 chars, ask the model to shorten
    if len(description) > 1024:
        shorten_prompt = f"Your description is {len(description)} characters, which exceeds the hard 1024 character limit. Rewrite it under 1024 characters while preserving the most important trigger words. Respond with only the new description in <new_description> tags."
        shorten_cmd = ["claude", "-p", shorten_prompt, "--output-format", "text"]
        if model:
            shorten_cmd.extend(["--model", model])

        try:
            shorten_result = subprocess.run(
                shorten_cmd,
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
            shorten_text = shorten_result.stdout.strip()
            match = re.search(r"<new_description>(.*?)</new_description>", shorten_text, re.DOTALL)
            shortened = match.group(1).strip().strip('"') if match else shorten_text.strip().strip('"')
            transcript["rewrite_response"] = shorten_text
            transcript["rewrite_description"] = shortened
            transcript["rewrite_char_count"] = len(shortened)
            description = shortened
        except (subprocess.TimeoutExpired, Exception) as e:
            print(f"Warning: shorten attempt failed: {e}", file=sys.stderr)

    transcript["final_description"] = description

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"improve_iter_{iteration or 'unknown'}.json"
        log_file.write_text(json.dumps(transcript, indent=2))

    return description


def main():
    parser = argparse.ArgumentParser(description="Improve a skill description based on eval results")
    parser.add_argument("--eval-results", required=True, help="Path to eval results JSON (from run_eval.py)")
    parser.add_argument("--skill-path", required=True, help="Path to skill directory")
    parser.add_argument("--history", default=None, help="Path to history JSON (previous attempts)")
    parser.add_argument("--model", default=None, help="Model for improvement (uses CLI default if omitted)")
    parser.add_argument("--verbose", action="store_true", help="Print progress to stderr")
    args = parser.parse_args()

    skill_path = Path(args.skill_path)
    if not (skill_path / "SKILL.md").exists():
        print(f"Error: No SKILL.md found at {skill_path}", file=sys.stderr)
        sys.exit(1)

    eval_results = json.loads(Path(args.eval_results).read_text())
    history = []
    if args.history:
        history = json.loads(Path(args.history).read_text())

    name, _, content = parse_skill_md(skill_path)
    current_description = eval_results["description"]

    if args.verbose:
        print(f"Current: {current_description}", file=sys.stderr)
        print(f"Score: {eval_results['summary']['passed']}/{eval_results['summary']['total']}", file=sys.stderr)

    new_description = improve_description(
        skill_name=name,
        skill_content=content,
        current_description=current_description,
        eval_results=eval_results,
        history=history,
        model=args.model,
    )

    if args.verbose:
        print(f"Improved: {new_description}", file=sys.stderr)

    output = {
        "description": new_description,
        "history": history + [{
            "description": current_description,
            "passed": eval_results["summary"]["passed"],
            "failed": eval_results["summary"]["failed"],
            "total": eval_results["summary"]["total"],
            "results": eval_results["results"],
        }],
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
