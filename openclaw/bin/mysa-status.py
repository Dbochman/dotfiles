#!/usr/bin/env python3
"""Read Mysa thermostat state via REST API and output JSON.

Uses mysotherm's auth module for Cognito token management.
Calls GET /devices, /devices/state, /devices/firmware directly.

Usage:
  ~/.openclaw/mysa/venv/bin/python3 ~/.openclaw/bin/mysa-status.py
"""

import json
import sys
import os

# Add mysotherm to path
sys.path.insert(0, os.path.expanduser('~/.openclaw/mysa/mysotherm'))

# Suppress boto3 EC2 metadata probe
os.environ['AWS_EC2_METADATA_DISABLED'] = 'true'

import boto3
import requests
from mysotherm.auth import authenticate, CONFIG_FILE
from mysotherm.mysa_stuff import BASE_URL, CLIENT_HEADERS, REGION
from mysotherm.mysa_stuff import auther
from mysotherm.util import slurpy


def c_to_f(c):
    return round(32 + c * 9 / 5, 1) if c and c != -1 else None


def get_val(entry):
    """Extract value from device state entry (sometimes bare, sometimes {v, t})."""
    if isinstance(entry, dict):
        return entry.get('v')
    return entry


def main():
    try:
        bsess = boto3.session.Session(region_name=REGION)
        u = authenticate(user=None, cf=CONFIG_FILE, bsess=bsess)

        sess = requests.Session()
        sess.auth = auther(u)
        sess.headers.update(CLIENT_HEADERS)

        devices = sess.get(f'{BASE_URL}/devices').json(object_hook=slurpy).DevicesObj
        states = sess.get(f'{BASE_URL}/devices/state').json(object_hook=slurpy).DeviceStatesObj
        firmware = sess.get(f'{BASE_URL}/devices/firmware').json(object_hook=slurpy).Firmware

        result = {"devices": []}

        for did in devices:
            d = devices[did]
            s = states.get(did, {})
            fw = firmware.get(did, {})
            mac = ':'.join(did[n:n+2].upper() for n in range(0, len(did), 2))

            corrected_c = get_val(s.get('CorrectedTemp'))
            sensor_c = get_val(s.get('SensorTemp'))
            setpoint_c = get_val(s.get('SetPoint'))
            heatsink_c = get_val(s.get('HeatSink'))

            device_info = {
                "name": d.Name,
                "model": d.Model,
                "mac": mac,
                "device_id": did,
                "firmware": getattr(fw, 'InstalledVersion', None),
                "timezone": d.TimeZone,
                "format": d.Format,
                "temp_c": corrected_c,
                "temp_f": c_to_f(corrected_c),
                "sensor_temp_c": sensor_c,
                "sensor_temp_f": c_to_f(sensor_c),
                "setpoint_c": setpoint_c,
                "setpoint_f": c_to_f(setpoint_c),
                "humidity": get_val(s.get('Humidity')),
                "duty_pct": get_val(s.get('Duty')),
                "current_a": get_val(s.get('Current')),
                "line_voltage": get_val(s.get('LineVoltage')) or get_val(s.get('Voltage')),
                "heatsink_c": heatsink_c,
                "heatsink_f": c_to_f(heatsink_c),
                "rssi_dbm": get_val(s.get('Rssi')),
                "brightness_pct": get_val(s.get('Brightness')),
                "lock": bool(get_val(s.get('Lock'))) if get_val(s.get('Lock')) in (0, 1) else get_val(s.get('Lock')),
            }

            # Convert duty to percentage if it's a ratio
            if device_info['duty_pct'] is not None and device_info['duty_pct'] <= 1:
                device_info['duty_pct'] = round(device_info['duty_pct'] * 100)

            result["devices"].append(device_info)

        print(json.dumps(result, indent=2))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
