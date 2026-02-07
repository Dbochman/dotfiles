---
name: bluetooth
description: Control Bluetooth devices and audio routing on the Mac Mini. Use when asked about Bluetooth, connecting speakers, headphones, audio output, switching audio, or listing paired devices.
allowed-tools: Bash(bluetooth:*)
metadata: {"openclaw":{"emoji":"🔵","requires":{"bins":["system_profiler","SwitchAudioSource","blueutil"]}}}
---

# Bluetooth & Audio Control

Control Bluetooth devices and audio routing on the Mac Mini (macOS Tahoe 26.x).

## Known Devices

| Name | MAC Address | Type |
|------|-------------|------|
| Kitchen speaker | DC:E5:5B:C0:91:1A | Audio |
| Magic Keyboard with Numeric Keypad | 80:4A:14:76:DA:A6 | Keyboard |
| Magic Trackpad 2 | AC:88:FD:EF:5F:7F | Trackpad |

## Important: macOS Tahoe Workaround

On macOS 26.x, the IOBluetooth API reports wrong power/connection state over SSH. All connect/disconnect operations must run via `open /tmp/bt_op.command` which launches in the GUI (Aqua) session where IOBluetooth works correctly.

Read operations (list devices, check status, audio switching) work directly over SSH via `system_profiler` and `SwitchAudioSource`.

## Connect a Bluetooth Device

```bash
ssh dylans-mac-mini "echo 'connect DC:E5:5B:C0:91:1A' > /tmp/bt_command.txt && rm -f /tmp/bt_result.txt /tmp/bt_exit.txt && open /tmp/bt_op.command && sleep 12 && cat /tmp/bt_exit.txt"
```
Exit code 0 = success. Wait 12 seconds for the Bluetooth connection to establish.

### Connect + Switch Audio (Full Pattern)

```bash
ssh dylans-mac-mini "echo 'connect DC:E5:5B:C0:91:1A' > /tmp/bt_command.txt && rm -f /tmp/bt_result.txt /tmp/bt_exit.txt && open /tmp/bt_op.command"
sleep 12
ssh dylans-mac-mini "export PATH=/opt/homebrew/bin:\$PATH && SwitchAudioSource -s 'Kitchen speaker' -t output"
```

## Disconnect a Bluetooth Device

```bash
ssh dylans-mac-mini "echo 'disconnect DC:E5:5B:C0:91:1A' > /tmp/bt_command.txt && rm -f /tmp/bt_result.txt /tmp/bt_exit.txt && open /tmp/bt_op.command && sleep 12 && cat /tmp/bt_exit.txt"
```

### Disconnect + Reset Audio

```bash
ssh dylans-mac-mini "export PATH=/opt/homebrew/bin:\$PATH && SwitchAudioSource -s 'Mac mini Speakers' -t output"
ssh dylans-mac-mini "echo 'disconnect DC:E5:5B:C0:91:1A' > /tmp/bt_command.txt && rm -f /tmp/bt_result.txt /tmp/bt_exit.txt && open /tmp/bt_op.command"
```

## List Paired Devices

```bash
ssh dylans-mac-mini "system_profiler SPBluetoothDataType -json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
bt = data.get('SPBluetoothDataType', [{}])[0]
for section in ['device_connected', 'device_not_connected']:
    devices = bt.get(section, [])
    if isinstance(devices, list):
        for group in devices:
            if isinstance(group, dict):
                for name, props in group.items():
                    connected = 'Yes' if section == 'device_connected' else 'No'
                    addr = props.get('device_address', '?')
                    print(f'{name}: {addr} connected={connected}')
"
```

## Check Device Connection Status

```bash
ssh dylans-mac-mini "system_profiler SPBluetoothDataType 2>&1 | grep -B2 'Kitchen speaker'"
```
If it appears under "Connected:" it's connected. Under "Not Connected:" it's not.

## Audio Output Control

These work directly over SSH (no GUI workaround needed).

### List Audio Outputs
```bash
ssh dylans-mac-mini "export PATH=/opt/homebrew/bin:\$PATH && SwitchAudioSource -a -t output"
```

### Check Current Audio Output
```bash
ssh dylans-mac-mini "export PATH=/opt/homebrew/bin:\$PATH && SwitchAudioSource -c -t output"
```

### Switch Audio Output
```bash
# To Kitchen speaker
ssh dylans-mac-mini "export PATH=/opt/homebrew/bin:\$PATH && SwitchAudioSource -s 'Kitchen speaker' -t output"

# To Mac Mini speakers
ssh dylans-mac-mini "export PATH=/opt/homebrew/bin:\$PATH && SwitchAudioSource -s 'Mac mini Speakers' -t output"
```

### Mute / Unmute
```bash
ssh dylans-mac-mini "export PATH=/opt/homebrew/bin:\$PATH && SwitchAudioSource -m toggle"
ssh dylans-mac-mini "export PATH=/opt/homebrew/bin:\$PATH && SwitchAudioSource -m mute"
ssh dylans-mac-mini "export PATH=/opt/homebrew/bin:\$PATH && SwitchAudioSource -m unmute"
```

## Check Bluetooth Power
```bash
ssh dylans-mac-mini "system_profiler SPBluetoothDataType 2>&1 | grep 'State:'"
```

## How It Works

The `/tmp/bt_op.command` file is a shell script that:
1. Reads the command from `/tmp/bt_command.txt`
2. Runs `blueutil --connect` or `--disconnect` with the MAC address
3. Writes exit code to `/tmp/bt_exit.txt`
4. Auto-closes its Terminal window

Using `open` to launch it ensures it runs in the macOS GUI session where `blueutil` has proper IOBluetooth API access and TCC (Bluetooth) permission.

## Troubleshooting

- **Connect/disconnect hangs or fails over direct SSH**: Use the `open /tmp/bt_op.command` pattern instead. Direct SSH calls to blueutil fail on Tahoe because IOBluetooth reports power=0.
- **blueutil --paired returns empty**: Use `BLUEUTIL_USE_SYSTEM_PROFILER=1 blueutil --paired` or `system_profiler SPBluetoothDataType`.
- **Audio not switching after connect**: Wait 5-10 seconds after connect for the audio device to register in CoreAudio, then call SwitchAudioSource.
- **Speaker not connecting**: Make sure the speaker is powered on and in range. Check system_profiler for RSSI signal strength.

## Files on Mac Mini

| Path | Purpose |
|------|---------|
| `/tmp/bt_op.command` | Reusable GUI-session launcher for blueutil |
| `/tmp/bt_command.txt` | Input: command + MAC address |
| `/tmp/bt_result.txt` | Output: command result |
| `/tmp/bt_exit.txt` | Output: exit code |
| `~/.openclaw/bin/bt_connect` | Swift IOBluetooth helper (used by launchd agent) |
| `~/Library/LaunchAgents/com.openclaw.bt-connect.plist` | LaunchAgent for Bluetooth ops |
