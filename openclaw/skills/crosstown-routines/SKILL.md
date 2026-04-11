---
name: crosstown-routines
description: Run Crosstown (Boston) routines like goodnight, away, and welcome home. Use when the user says goodnight, good night, bedtime, going to bed, leaving, heading out, we're home, or asks to run a routine AND they are at Crosstown (Boston).
allowed-tools: Bash(hue:*) Bash(nest:*) Bash(speaker:*) Bash(crosstown-roomba:*) Bash(cielo:*) Bash(august:*) Bash(samsung-tv:*)
metadata: {"openclaw":{"emoji":"H","requires":{"bins":["hue","nest","crosstown-roomba","cielo","august","samsung-tv"]}}}
---

# Crosstown Routines (Boston)

Predefined routines for the **Crosstown (Boston — 19 Crosstown Ave)** home. These only apply to Crosstown — do NOT run these for the Cabin (Philly). For Cabin routines, see the `cabin-routines` skill.

**Important:** All `hue` commands in this skill use the `--crosstown` flag (or no flag, since Crosstown is the default). Crosstown speakers are controlled via the `speaker` CLI.

**Seasonal note:** Skip Cielo AC commands in winter months (Nov-Mar) unless the user specifically requests AC.

## Goodnight

**Triggers:** "goodnight", "good night", "going to bed", "bedtime", "time for bed"

Steps:
1. Turn off all lights except bedroom: `hue --crosstown all-off`
2. Set bedroom to dim warm light: `hue --crosstown on bedroom 5` then `hue --crosstown color bedroom warm`
3. Set thermostat to eco: `nest eco crosstown on`
4. Stop speakers: `speaker stop bedroom` and `speaker stop living`
5. Turn off non-bedroom AC units: `cielo off living` (leave bedroom Cielo as-is)
6. Turn off TV: `samsung-tv power off`
7. Lock front door: `august lock`

Confirm: "Goodnight! Lights off, thermostat in eco, speakers stopped, TV off, door locked. Bedroom has a dim warm light."

## Away / Leaving

**Triggers:** "we're leaving", "heading out", "leaving the house", "away mode", "gone for the day"

Steps:
1. Turn off all lights: `hue --crosstown all-off`
2. Set thermostat to eco: `nest eco crosstown on`
3. Stop speakers: `speaker stop bedroom` and `speaker stop living`
4. Lock front door: `august lock`
5. Turn off all AC units: `cielo off all`
6. Start both Roombas: `crosstown-roomba start all`

Confirm: "Away mode set. All lights off, thermostat in eco, audio stopped, door locked, AC off. Both Roombas are cleaning."

## Welcome Home

**Triggers:** "we're home", "I'm home", "just got home", "welcome home"

Steps:
1. Turn on main lights: `hue --crosstown on entryway 100` then `hue --crosstown on kitchen 80` then `hue --crosstown on living 60`
2. Set warm color: `hue --crosstown color kitchen warm` then `hue --crosstown color living warm`
3. Disable eco: `nest eco crosstown off`
4. Set comfortable temperature: `nest set crosstown 70`
5. Dock both Roombas: `crosstown-roomba dock all`
6. Restore AC to comfortable: `cielo on living --mode cool --temp 72` (summer) or skip (winter)

Confirm: "Welcome home! Entryway, kitchen, and living room lights on, thermostat set to 70F. Roombas docking."

## Movie Night

**Triggers:** "movie night", "movie mode", "watching a movie", "film time"

Steps:
1. Dim movie room: `hue --crosstown on "movie room" 10` then `hue --crosstown color "movie room" warm`
2. Turn off other lights: `hue --crosstown off kitchen` then `hue --crosstown off office` then `hue --crosstown off entryway`
3. Wake TV and set input: `samsung-tv power on` then `samsung-tv input HDMI1`
4. Set speaker volume low: `speaker volume living 20`

Confirm: "Movie mode set. Movie room dimmed, other lights off, TV on."

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

| Routine | Lights | Thermostats | Audio | Roombas | AC (Cielo) | Lock | TV |
|---------|--------|-------------|-------|---------|------------|------|-----|
| Goodnight | All off, bedroom dim | Eco on | Stop all | -- | Off (non-bedroom) | Lock | Off |
| Away | All off | Eco on | Stop all | Start all | Off all | Lock | -- |
| Welcome Home | Entryway+kitchen+living on | Eco off, 70F | -- | Dock all | Cool 72 (summer) | -- | -- |
| Movie Night | Movie room dim, others off | -- | Volume low | -- | -- | -- | On, HDMI1 |
| Morning | Kitchen+living+entryway on daylight | Eco off | -- | -- | -- | -- | -- |

## Skill Boundaries

This skill runs **multi-device routines at Crosstown only** (lights + thermostats + audio + Roombas + AC + lock + TV).

For related tasks, switch to:
- **cabin-routines**: Same routines but for the Cabin (Philly) — different devices, different commands
- **crosstown-roomba**: Direct Roomba control at Crosstown (start/stop/dock individual robots without running a full routine)
- **hue-lights**: Direct light control at any location without running a full routine
- **nest-thermostat**: Direct thermostat control without running a full routine
- **cielo-ac**: Direct AC control without running a full routine
- **august-lock**: Direct lock control (lock/unlock/status) without running a full routine
- **samsung-tv**: Direct TV control (power/input/volume) without running a full routine
- **presence**: Check who is home before deciding which routine to run
- **dog-walk**: Automated dog walk detection auto-starts/docks Roombas at Crosstown independently of routines
- Vacancy automation (`com.openclaw.vacancy-actions`) triggers away-like actions automatically when Crosstown becomes `confirmed_vacant` — this runs independently and should NOT be duplicated by routines
