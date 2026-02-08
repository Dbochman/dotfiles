---
name: crosstown-routines
description: Run Crosstown (Boston) routines like goodnight, away, and welcome home. Use when the user says goodnight, good night, bedtime, going to bed, leaving, heading out, we're home, or asks to run a routine AND they are at Crosstown (Boston).
allowed-tools: Bash(hue:*) Bash(nest:*) Bash(speaker:*)
metadata: {"openclaw":{"emoji":"H","requires":{"bins":["hue","nest"]}}}
---

# Crosstown Routines (Boston)

Predefined routines for the **Crosstown (Boston — 19 Crosstown Ave)** home. These only apply to Crosstown — do NOT run these for the Cabin (Philly). For Cabin routines, see the `cabin-routines` skill.

**Important:** All `hue` commands in this skill use the `--crosstown` flag (or no flag, since Crosstown is the default). Crosstown speakers are controlled via the `speaker` CLI.

## Goodnight

**Triggers:** "goodnight", "good night", "going to bed", "bedtime", "time for bed"

Steps:
1. Turn off all lights except bedroom: `hue --crosstown all-off`
2. Set bedroom to dim warm light: `hue --crosstown on bedroom 5` then `hue --crosstown color bedroom warm`
3. Set thermostat to eco: `nest eco crosstown on`
4. Stop speakers: `speaker stop bedroom` and `speaker stop living`

Confirm: "Goodnight! Lights off, thermostat in eco mode, speakers stopped. Bedroom has a dim warm light."

## Away / Leaving

**Triggers:** "we're leaving", "heading out", "leaving the house", "away mode", "gone for the day"

Steps:
1. Turn off all lights: `hue --crosstown all-off`
2. Set thermostat to eco: `nest eco crosstown on`
3. Stop speakers: `speaker stop bedroom` and `speaker stop living`

Confirm: "Away mode set. All lights off, thermostat in eco, audio stopped."

## Welcome Home

**Triggers:** "we're home", "I'm home", "just got home", "welcome home"

Steps:
1. Turn on main lights: `hue --crosstown on entryway 100` then `hue --crosstown on kitchen 80` then `hue --crosstown on living 60`
2. Set warm color: `hue --crosstown color kitchen warm` then `hue --crosstown color living warm`
3. Disable eco: `nest eco crosstown off`
4. Set comfortable temperature: `nest set crosstown 70`

Confirm: "Welcome home! Entryway, kitchen, and living room lights on, thermostat set to 70F."

## Movie Night

**Triggers:** "movie night", "movie mode", "watching a movie", "film time"

Steps:
1. Dim movie room: `hue --crosstown on "movie room" 10` then `hue --crosstown color "movie room" warm`
2. Turn off other lights: `hue --crosstown off kitchen` then `hue --crosstown off office` then `hue --crosstown off entryway`
3. Set speaker volume low: `speaker volume living 20`

Confirm: "Movie mode set. Movie room dimmed, other lights off."

## Morning

**Triggers:** "good morning", "morning routine", "wake up", "rise and shine"

Steps:
1. Turn on kitchen and living room lights: `hue --crosstown on kitchen 100` then `hue --crosstown on living 80`
2. Set daylight color: `hue --crosstown color kitchen daylight` then `hue --crosstown color living daylight`
3. Turn on entryway: `hue --crosstown on entryway 100`
4. Disable eco: `nest eco crosstown off`
5. Check thermostat status: `nest status`

Confirm with current temperature readings from `nest status`.

## Custom Adjustments

The user may ask to modify routines:
- "Goodnight but leave the downstairs on" — run goodnight, then `hue --crosstown on downstairs 30`
- "Away mode but keep heat on" — run away but skip the eco steps
- "Welcome home but it's late" — use dimmer lights (30% instead of 80%)

Always adapt to the user's specific request. These routines are starting points, not rigid scripts.

## Quick Reference

| Routine | Lights | Thermostats | Audio |
|---------|--------|-------------|-------|
| Goodnight | All off, bedroom dim | Eco on | Stop all |
| Away | All off | Eco on | Stop all |
| Welcome Home | Entryway+kitchen+living on | Eco off, 70F | -- |
| Movie Night | Movie room dim, others off | -- | Volume low |
| Morning | Kitchen+living+entryway on daylight | Eco off | -- |
