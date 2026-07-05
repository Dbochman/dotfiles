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
- **MacBook Pro IP**: `192.168.165.110` (`mac.lan`)

## Known Devices at Crosstown

| IP | Hostname | Device |
|---|---|---|
| .1 | amplifi.lan | AmpliFi router |
| .110 | mac.lan | MacBook Pro (this machine) |
| .117 | movie-room.lan | Apple TV (Movie Room) |
| .119 | ys-l16030313e8.lan | Yeelight / smart light |
| .124 | dylans-iphone.lan | Dylan's iPhone (MAC: `${CROSSTOWN_DYLAN_MAC}`) |
| .129 | huesyncbox.lan | Philips Hue Sync Box |
| .142, .162, .164, .178, .236 | espressif.lan | ESP32 smart home devices |
| .4 | irobot-81039f...lan | iRobot Roomba Combo 10 Max |
| .3 | irobot-195efa...lan | iRobot Roomba J5 (scoomba) |
| .132 | ? | Petlibro Granary Smart Feeder (MAC: ${PETLIBRO_FEEDER_MAC}) |
| .225 | ? | Petlibro Dockstream 2 Fountain (MAC: ${PETLIBRO_FOUNTAIN_MAC}) |
| .155 | litter-robot4.lan | Litter Robot 4 |
| .171 | mac.lan | Dylan's Mac (desktop) |
| .195 | 001788284a36.lan | Philips Hue Bridge |
| .241 | ringdoorbell-5b.lan | Ring Doorbell |

## Scanning for Devices

ARP scan from the MacBook Pro (ping sweep first to populate ARP table):

```bash
ssh dylans-macbook-pro "for i in \$(seq 1 254); do ping -c1 -W1 192.168.165.\$i >/dev/null 2>&1 & done; wait; arp -a | grep -v incomplete | grep '192.168.165'"
```

Check a specific device:

```bash
ssh dylans-macbook-pro "ping -c3 -W2 192.168.165.124; arp -anl | grep '^192.168.165.124 '; arp -a | grep 192.168.165.124"
```

## Presence Detection

Track phone presence by probing the LAN and requiring live receive-side
reachability from `arp -anl` before matching a MAC or exact hostname:

| Person | Hostname | MAC (Crosstown WiFi) | IP |
|---|---|---|---|
| Dylan | `dylans-iphone` | `${CROSSTOWN_DYLAN_MAC}` | `192.168.165.124` |
| Julia | `julias-iphone` | `${CROSSTOWN_JULIA_MAC}` | `192.168.165.248` |

Matching priority: MAC → exact hostname (mDNS `.lan` name joined to the same
fresh IP/MAC/interface row). IP alone is not accepted as identity. Hostname is
the durable fallback when iOS rotates its per-network MAC.

iPhones in sleep mode may ignore ICMP while still answering ARP. The presence
scanner therefore ignores ping exit status and trusts only a live inbound
reachability timer; cached complete rows with expired inbound reachability do
not count.

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
