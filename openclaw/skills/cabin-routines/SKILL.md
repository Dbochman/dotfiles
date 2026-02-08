---
name: cabin-routines
description: Run cabin routines like goodnight, away, and welcome home. Use when the user says goodnight, good night, bedtime, going to bed, leaving, heading out, we're home, or asks to run a routine.
allowed-tools: Bash(hue:*) Bash(nest:*) Bash(catt:*) Bash(spogo:*)
metadata: {"openclaw":{"emoji":"H","requires":{"bins":["hue","nest"]}}}
---

# Cabin Routines (Philly)

Predefined routines for the **Cabin (Philly)** home. These only apply to Cabin — do NOT run these for Crosstown (Boston). For Crosstown routines, see the `crosstown-routines` skill.

**Important:** All `hue` commands in this skill must use the `--cabin` flag. The Cabin speakers are "Kitchen speaker" and "Bedroom speaker" (on the Cabin LAN at `192.168.1.x`).

## Goodnight

**Triggers:** "goodnight", "good night", "going to bed", "bedtime", "time for bed"

Steps:
1. Turn off all lights except bedroom: `hue --cabin all-off`
2. Set bedroom to dim warm light: `hue --cabin on bedroom 5` then `hue --cabin color bedroom warm`
3. Set all thermostats to eco: `nest eco solarium on` then `nest eco "philly living" on` then `nest eco bedroom on`
4. Stop all speakers: `catt -d "Kitchen speaker" stop` and `catt -d "Bedroom speaker" stop`
5. Pause Spotify: `spogo pause`

Confirm: "Goodnight! Lights off, thermostats in eco mode, speakers stopped. Bedroom has a dim warm light."

## Away / Leaving

**Triggers:** "we're leaving", "heading out", "leaving the cabin", "away mode", "gone for the day"

Steps:
1. Turn off all lights: `hue --cabin all-off`
2. Set all thermostats to eco: `nest eco solarium on` then `nest eco "philly living" on` then `nest eco bedroom on`
3. Stop all speakers: `catt -d "Kitchen speaker" stop` and `catt -d "Bedroom speaker" stop`
4. Pause Spotify: `spogo pause`

Confirm: "Away mode set. All lights off, thermostats in eco, audio stopped."

## Welcome Home

**Triggers:** "we're home", "I'm home", "just got home", "back at the cabin", "welcome home"

Steps:
1. Turn on main lights: `hue --cabin on kitchen 80` then `hue --cabin on living 60` then `hue --cabin on hallway 100`
2. Set warm color: `hue --cabin color kitchen warm` then `hue --cabin color living warm`
3. Disable eco on main rooms: `nest eco "philly living" off` then `nest eco bedroom off`
4. Set comfortable temperature: `nest set "philly living" 70` then `nest set bedroom 68`

Confirm: "Welcome home! Kitchen and living room lights on, thermostats set to 70°F/68°F."

## Movie Night

**Triggers:** "movie night", "movie mode", "watching a movie", "film time"

Steps:
1. Dim living room: `hue --cabin on living 10` then `hue --cabin color living warm`
2. Turn off other lights: `hue --cabin off kitchen` then `hue --cabin off hallway` then `hue --cabin off office`
3. Set speaker volume low: `catt -d "Kitchen speaker" volume 20`
4. Pause any music: `spogo pause`

Confirm: "Movie mode set. Living room dimmed, other lights off."

## Morning

**Triggers:** "good morning", "morning routine", "wake up", "rise and shine"

Steps:
1. Turn on kitchen and living room lights: `hue --cabin on kitchen 100` then `hue --cabin on living 80`
2. Set daylight color: `hue --cabin color kitchen daylight` then `hue --cabin color living daylight`
3. Turn on hallway: `hue --cabin on hallway 100`
4. Disable eco on all rooms: `nest eco solarium off` then `nest eco "philly living" off` then `nest eco bedroom off`
5. Check thermostat status: `nest status`

Confirm with current temperature readings from `nest status`.

## Custom Adjustments

The user may ask to modify routines:
- "Goodnight but leave the staircase on" — run goodnight, then `hue --cabin on staircase 30`
- "Away mode but keep heat on" — run away but skip the eco steps
- "Welcome home but it's late" — use dimmer lights (30% instead of 80%)

Always adapt to the user's specific request. These routines are starting points, not rigid scripts.

## Quick Reference

| Routine | Lights | Thermostats | Audio |
|---------|--------|-------------|-------|
| Goodnight | All off, bedroom dim | All eco | Stop all |
| Away | All off | All eco | Stop all |
| Welcome Home | Kitchen+living+hallway on | Eco off, 70°F/68°F | — |
| Movie Night | Living dim, others off | — | Pause music |
| Morning | Kitchen+living+hallway on daylight | Eco off | — |
