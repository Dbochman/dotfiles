---
name: spotify-speakers
description: Play music via Spotify and control Google Home speakers. Use when asked to play music, songs, artists, playlists, podcasts, or control speakers/volume/audio.
allowed-tools: Bash(spogo:*) Bash(catt:*)
metadata: {"openclaw":{"emoji":"M","requires":{"bins":["spogo","catt"]}}}
---

# Spotify & Speaker Control

Control Spotify playback via `spogo` and Google Home speakers via `catt`.

## Spotify Commands (spogo)

### Playback
```bash
spogo status                       # what's playing now
spogo play                         # resume playback
spogo pause                        # pause
spogo next                         # skip track
spogo prev                         # previous track
spogo volume 50                    # set volume (0-100)
spogo shuffle on                   # enable shuffle
spogo repeat track                 # repeat modes: off, context, track
```

### Search and play
```bash
spogo search track "bohemian rhapsody"    # search for a track
spogo play spotify:track:<id>             # play a specific track by URI
spogo search artist "miles davis"         # search artists
spogo search playlist "chill vibes"       # search playlists
spogo play spotify:playlist:<id>          # play a playlist by URI
```

### Devices
```bash
spogo device list                  # list Spotify Connect devices
spogo device set "Kitchen speaker" # transfer playback to a device
```

### Queue
```bash
spogo queue show                   # show upcoming tracks
spogo queue add spotify:track:<id> # add track to queue
```

## Speaker Commands (catt)

### Available speakers (Cabin — Philly)
- **Kitchen speaker** — Nest Audio (192.168.1.66)
- **Bedroom speaker** — Nest Mini (192.168.1.163)

These speakers are at the **Cabin (Philly)** only. For Crosstown (Boston) speakers, use the `google-speakers` skill.

### Control
```bash
catt -d "Kitchen speaker" volume 50       # set volume (0-100)
catt -d "Kitchen speaker" stop            # stop playback
catt -d "Kitchen speaker" pause           # pause
catt -d "Kitchen speaker" play            # resume
catt -d "Kitchen speaker" status          # what's playing
catt -d "Bedroom speaker" volume 30       # control other speaker
catt scan                                 # discover speakers
```

### Cast audio
```bash
catt -d "Kitchen speaker" cast <url>      # cast an audio URL
catt -d "Kitchen speaker" cast_site <url> # cast a website
```

## Common Workflows

### "Play jazz in the kitchen"
1. Search: `spogo search playlist "jazz"`
2. Play: `spogo play spotify:playlist:<id>`
3. Transfer: `spogo device set "Kitchen speaker"`

### "Play something chill on all speakers"
Note: Google Home speakers appear as Spotify Connect devices only when actively playing. Start playback on one device first, then the others may appear.

### "What's playing?"
```bash
spogo status
```

### "Turn it down"
```bash
spogo volume 30
```

## Notes

- Spotify requires Premium for most playback features
- Speaker names must be quoted in catt commands
- If speakers don't show in `spogo device list`, they may need to be actively playing first
- Use `spogo --json` for machine-readable output when parsing results
- Always check `spogo status` before trying to control playback to see if anything is active
