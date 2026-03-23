---
name: <skill-name>
description: <When to use this skill. Be specific about trigger words, device names, and what it controls. Include "NOT for X" disambiguation if needed.>
allowed-tools: Bash(<skill-name>:*)
metadata: {"openclaw":{"emoji":"<single-char>","requires":{"bins":["<cli-name>"]}}}
---

# <Skill Title>

Control **<device>** at **<location>** via <API type>.

## Devices

| Name | Model | Notes |
|------|-------|-------|
| <name> | <model> | <notes> |

## Commands

### Check status
```bash
<cli-name> status
```

### <Action>
```bash
<cli-name> <action> <args>
```

## Architecture

```
<Device> <--protocol--> <API/Service> <--transport--> <cli-name> (<host>)
```

## Disambiguation

- "<trigger phrase>" → this skill
- "<other trigger>" → <other-skill> skill

## Troubleshooting

### "<common error>"
<Fix steps>
