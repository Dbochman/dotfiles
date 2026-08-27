---
name: crosstown-network
description: Access the Crosstown residence local network via the MacBook Pro. Use when needing to reach Crosstown LAN devices, run commands at Crosstown, scan the local network, interact with the AmpliFi router, Hue Bridge, Ring Doorbell, or any device on the 192.168.165.x subnet. Also use for presence detection at Crosstown.
allowed-tools: Bash(ssh:*)
metadata: {"openclaw":{"emoji":"N"}}
---

# Crosstown Network Access

The OpenClaw gateway runs on the **cabin Mac Mini** (`dylans-mac-mini`). To reach devices on the **Crosstown residence** local network, SSH into the MacBook Pro there.

## SSH Access

```bash
ssh dylans-macbook-pro "<command>"
```

- **Host**: `dylans-macbook-pro` (Tailscale, resolves to `100.107.209.85`)
- **Auth**: Dedicated key `~/.ssh/id_mini_to_mbp` (auto-selected via `Match originalhost` — bypasses 1Password agent which hangs under launchd)
- **User**: `dbochman`
- **OS**: macOS 26.3 (arm64)
- **Python**: `/usr/bin/python3` (system)
- **Homebrew**: `/opt/homebrew/bin/brew`
- **Node.js**: `/opt/homebrew/bin/node` (v25.6.1)
- **npm**: `/opt/homebrew/bin/npm` (v11.9.0)
- **sudo**: Passwordless for `dbochman`

## Network

- **Subnet**: `192.168.165.0/24`
- **Router**: AmpliFi (Ubiquiti) at `192.168.165.1` (`amplifi.lan`)
- **MacBook Pro IP**: DHCP; observed at `192.168.165.109` on 2026-08-27
  (`mac.lan` is advertised by multiple Apple clients and is not a unique
  identity). Always connect through the `dylans-macbook-pro` Tailscale alias.

## Operational Device Inventory

Last refreshed from the Crosstown MacBook Pro on 2026-08-27 with a sanitized
neighbor sweep and targeted service probes. Except for the router's gateway
address, these are observed or application-pinned addresses; they are not
confirmed DHCP reservations. Verify reservations in the AmpliFi control plane
before relying on an address as permanent.

| IP | Hostname | Device | Address basis / latest check |
|---|---|---|---|
| .1 | amplifi.lan | AmpliFi router | Gateway; HTTP live 2026-08-27 |
| .109 | mac.lan | MacBook Pro jump host | Current DHCP observation; use Tailscale alias |
| .117 | movie-room.lan | Apple TV (Movie Room) | Live neighbor and AirPlay service 2026-08-27 |
| .119 | ys-l16030313e8.lan | Yeelight / smart light | Live neighbor 2026-08-27 |
| .129 | reachy-mini.lan | Reachy Mini | Application-pinned; SSH live 2026-08-27; DHCP reservation unverified |
| .142, .162, .164, .178, .236 | espressif.lan | ESP32 smart home devices | Live neighbors 2026-08-27 |
| .4 | irobot-81039f...lan | iRobot Roomba Combo 10 Max | Live neighbor 2026-08-27 |
| .3 | irobot-195efa...lan | iRobot Roomba J5 (scoomba) | Last known; no live neighbor on 2026-08-27 |
| .132 | — | Petlibro Granary Smart Feeder | Live neighbor 2026-08-27 |
| .225 | — | Petlibro Dockstream 2 Fountain | Last known; no fresh receive-side reachability on 2026-08-27 |
| .155 | litter-robot4.lan | Litter Robot 4 | Live neighbor 2026-08-27 |
| .195 | 001788284a36.lan | Philips Hue Bridge (not a Hue Sync Box) | HTTP and HTTPS live 2026-08-27 |
| .241 | ringdoorbell-5b.lan | Ring Doorbell | Live neighbor 2026-08-27 |

Transient phones and ambiguous `mac.lan` clients are intentionally omitted.
Resident presence must use the protected bindings below, never this inventory.

## Scanning for Devices

Run a sanitized operational-endpoint scan from the MacBook Pro. The ping sweep
populates the neighbor table, but the output deliberately excludes MAC
addresses, phones, and unknown clients:

```bash
ssh dylans-macbook-pro 'for i in $(seq 1 254); do ping -c1 -W200 192.168.165.$i >/dev/null 2>&1 & done; wait; arp -a | awk '\''BEGIN{IGNORECASE=1} /amplifi|movie-room|ys-l|irobot|reachy-mini|litter-robot|001788|ringdoorbell|espressif/{name=$1; ip=$2; gsub(/[()]/, "", ip); print name, ip}'\'' | sort -u'
```

Check a specific known device without printing its link-layer identity:

```bash
ssh dylans-macbook-pro 'target=192.168.165.129; ping -c1 -W500 "$target" >/dev/null 2>&1 || true; arp -anl | awk -v target="$target" '\''$1 == target {if ($2 == "(incomplete)") state="not-live"; else if ($4 == "expired") state="stale"; else state="live"; print target, "neighbor=" state; found=1} END {if (!found) print target, "neighbor=absent"}'\'''
```

## Presence Detection

Track phone presence by probing the LAN and requiring live receive-side
reachability from `arp -anl` before matching each resident's exact protected
site-private MAC:

| Person | Presence identity |
|---|---|
| Dylan | Exact MAC in the protected Crosstown binding file |
| Julia | Exact MAC in the protected Crosstown binding file |

The bindings live only in the owner-only mode-`0600`
`~/.openclaw/presence-devices.json` on the MacBook Pro. Never print or copy
that file. Hostnames, display names, and IP addresses are not identity
fallbacks; an absent, insecure, wrong-site, duplicate, or malformed binding
fails closed.

iPhones in sleep mode may ignore ICMP while still answering ARP. The presence
scanner therefore ignores ping exit status and trusts only a live inbound
reachability timer; cached complete rows with expired inbound reachability do
not count.

Only after the deployed scanner's strict `validate-config` command succeeds,
use its sanitized `observe` path rather than exposing raw ARP identity data:

```bash
ssh dylans-macbook-pro \
  '~/.openclaw/workspace/scripts/presence-detect.sh validate-config crosstown'
ssh dylans-macbook-pro \
  '~/.openclaw/workspace/scripts/presence-detect.sh observe crosstown'
```

Before strict activation, report cached canonical presence only. The preserved
legacy observer can include raw device, address, and network identifiers and
must not be printed or relayed through the agent.

## Bonjour Discovery

Find Apple devices broadcasting on the Crosstown LAN:

```bash
ssh dylans-macbook-pro "dns-sd -B _companion-link._tcp local. & PID=\$!; sleep 5; kill \$PID 2>/dev/null"
```

Find Google/Nest speakers:

```bash
ssh dylans-macbook-pro "dns-sd -B _googlecast._tcp local. & PID=\$!; sleep 5; kill \$PID 2>/dev/null"
```

## Remote Maintenance Safety

Do not run `diskutil verifyVolume /System/Volumes/Data` or another full APFS check over the
MacBook Pro's only SSH/Tailscale management path. A live verification of the 648 GiB Data
volume made the host stop answering Tailscale traffic on 2026-06-22, which also interrupted
presence scans and Crosstown device bridges.

For unattended remote checks, limit storage inspection to SMART status, free space, panic
reports, and service health. Run startup/Data-volume First Aid only while physically on site
or when a second independent management path is available. Keep and wait on the original
command session; never start a second filesystem verification because streamed output ended
before the final status arrived.

## Limitations

- **SSH required** — all commands must be wrapped in `ssh dylans-macbook-pro "..."`
- **PATH note** — Homebrew's `/opt/homebrew/bin` is in the system PATH via `/etc/paths.d/homebrew`, but for npm use `PATH=/opt/homebrew/bin:$PATH npm ...`
- **Sleep disabled** — `pmset disablesleep 1` is set so the machine stays reachable
