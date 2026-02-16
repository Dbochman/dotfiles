---
name: sag
description: Speak text aloud using ElevenLabs TTS voices. Use when asked to say something out loud, announce something, read text aloud, speak a message, or use text-to-speech. Like the macOS say command but with high-quality AI voices.
allowed-tools: Bash(sag-wrapper:*)
metadata: {"openclaw":{"emoji":"🔊","requires":{"bins":["sag-wrapper"]}}}
---

# Sag (Text-to-Speech)

Speak text aloud via ElevenLabs TTS using the `sag-wrapper` CLI. Like macOS `say` but with modern AI voices.

## Speak Text

```bash
sag-wrapper "Hello, how are you today?"
sag-wrapper "Good morning Dylan"
```

Default voice is Roger (laid-back, casual, resonant).

## Choose a Voice

```bash
sag-wrapper -v Sarah "This is Sarah speaking"
sag-wrapper -v Brian "This is Brian speaking"
sag-wrapper -v Lily "This is Lily speaking"
```

## List Available Voices

```bash
sag-wrapper voices
```

## Adjust Speed

```bash
# Words per minute (default ~150)
sag-wrapper --rate 200 "Speaking faster now"
sag-wrapper --rate 100 "Speaking slower now"
```

## Save to File

```bash
sag-wrapper -o /tmp/greeting.mp3 "Hello world"
```

## Pipe Input

```bash
echo "Text from a pipe" | sag-wrapper
```

## Popular Voices

| Voice | Style |
|-------|-------|
| Roger | Laid-back, casual, resonant (default) |
| Sarah | Mature, reassuring, confident |
| Brian | Deep, resonant, comforting |
| Lily | Velvety actress |
| Alice | Clear, engaging educator |
| Daniel | Steady broadcaster |
| Chris | Charming, down-to-earth |
| Jessica | Playful, bright, warm |
| Eric | Smooth, trustworthy |

## Models

- `eleven_v3` — Default, highest quality
- `eleven_flash_v2_5` — Fastest, cheapest
- `eleven_turbo_v2_5` — Balanced speed/quality
- `eleven_multilingual_v2` — Best for non-English

```bash
sag-wrapper --model-id eleven_flash_v2_5 "Quick response"
```

## Notes

- Binary at `/opt/homebrew/bin/sag-wrapper` (wraps `/opt/homebrew/bin/sag`)
- API key read from `~/.cache/openclaw-gateway/elevenlabs_api_key`
- 1Password item: "ElevenLabs API Key" in "OpenClaw" vault
- Audio plays through Mac Mini speakers (or connected Bluetooth/AirPlay output)
