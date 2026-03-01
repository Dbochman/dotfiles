---
name: crosstown-network
description: Access the 19 Crosstown Ave local network via the MacBook Pro. Use when needing to reach Crosstown LAN devices, run commands at Crosstown, scan the local network, interact with the AmpliFi router, Hue Bridge, Ring Doorbell, or any device on the 192.168.165.x subnet. Also use for presence detection at Crosstown.
allowed-tools: Bash(ssh:*)
metadata: {"openclaw":{"emoji":"N"}}
---

# Crosstown Network Access

The OpenClaw gateway runs on the **cabin Mac Mini** (`dylans-mac-mini`). To reach devices on the **19 Crosstown Ave** local network, SSH into the MacBook Pro there.

## SSH Access

```bash
ssh dylans-macbook-pro "<command>"
```

- **Host**: `dylans-macbook-pro` (Tailscale, resolves to `100.107.209.85`)
- **User**: `dbochman`
- **OS**: macOS 26.3 (arm64)
- **Python**: `/usr/bin/python3` (system)
- **No Homebrew or Node.js installed**

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
| .124 | dylans-iphone.lan | Dylan's iPhone (MAC: `6c:3a:ff:5f:fc:ba`) |
| .129 | huesyncbox.lan | Philips Hue Sync Box |
| .142, .162, .164, .178, .236 | espressif.lan | ESP32 smart home devices |
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
ssh dylans-macbook-pro "ping -c3 -W2 192.168.165.124; arp -a | grep 192.168.165.124"
```

## Presence Detection

Track phone presence by pinging known MAC addresses:

| Person | MAC (Crosstown WiFi) | Notes |
|---|---|---|
| Dylan | `6c:3a:ff:5f:fc:ba` | Real Apple MAC (private WiFi address off) |
| Julia | TBD | Needs identification |

iPhones in sleep mode may not respond to the first ping — use `ping -c3` for reliability.

## Bonjour Discovery

Find Apple devices broadcasting on the Crosstown LAN:

```bash
ssh dylans-macbook-pro "dns-sd -B _companion-link._tcp local. & PID=\$!; sleep 5; kill \$PID 2>/dev/null"
```

Find Google/Nest speakers:

```bash
ssh dylans-macbook-pro "dns-sd -B _googlecast._tcp local. & PID=\$!; sleep 5; kill \$PID 2>/dev/null"
```

## Limitations

- **No Homebrew or Node.js** — only system Python and standard macOS tools
- **No LaunchAgents configured** — the MacBook Pro doesn't run scheduled tasks yet
- **SSH required** — all commands must be wrapped in `ssh dylans-macbook-pro "..."`
- **MacBook Pro may sleep** — if SSH fails, the machine may be asleep or off the network
