---
name: <skill-name>
description: >-
  <Describe what this skill does and the specific requests that should trigger
  it. Include nearby cases that should use another skill.>
allowed-tools: Bash(<skill-name>:*)
metadata: {"openclaw":{"emoji":"<single-char>","requires":{"bins":["<cli-name>"]}}}
---

# <Skill title>

Control **<device or service>** at **<location>** through <API or protocol>.

## Commands

### Check status

```bash
<cli-name> status
```

### <Action>

```bash
<cli-name> <action> <args>
```

## Safety

- Define confirmation boundaries for actions with physical, financial,
  privacy, account, or messaging consequences.
- Validate inputs before sending commands and verify the resulting state.
- Fail closed when authentication, network data, or response parsing is
  unavailable or malformed.

## Architecture

```text
<Device> <--protocol--> <API or service> <--transport--> <cli-name> (<host>)
```

## Troubleshooting

### <Common error>

<Explain a safe diagnostic and recovery sequence.>
