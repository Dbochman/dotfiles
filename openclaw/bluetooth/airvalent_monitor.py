#!/usr/bin/env python3
"""Bounded Airvalent reads with optional owner-provisioned PIN recovery."""
import argparse
import asyncio
import fcntl
import json
import ipaddress
import logging
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys

from airvalent_state import (ORIGIN, ROOM, STRUCTURE, cached_status, dashboard_room,
                            now_iso, private_json, project_status, state_dir, write_private)

SERVICE = "b81c94a4-6b2b-4d41-9357-0c8229ea02df"
CURRENT = "e8694d30-a155-4fb4-befa-548e64c88965"
INTERVAL = "b1c48eea-4f5c-44f7-9797-73e0ce294881"
BRIDGE = "crosstown-ble-01"
# ESP-IDF 5.5.6: ESP_AUTH_SMP_CONN_TOUT = 0x66. This is a lost link,
# not evidence of a rejected PIN. Retry on the next scheduled collection.
AUTH_CONNECTION_TIMEOUT = 102


class ReadError(Exception):
    def __init__(self, category, auth_failure_code=None):
        super().__init__(category)
        self.auth_failure_code = auth_failure_code


def authentication_error(category, code):
    return ReadError("ble_connection_timeout" if code == AUTH_CONNECTION_TIMEOUT else category, code)


def decode_current(raw):
    if len(raw) != 8:
        raise ReadError("invalid_reading")
    co2 = int.from_bytes(raw[:2], "little") & 0x3fff
    if not 250 <= co2 <= 10000:
        raise ReadError("invalid_reading")
    return co2


def load_binding(root):
    try:
        cfg = private_json(root / "connection.json")
        cache = private_json(root / "services.json")
        address = cfg["expected_address"]
        if type(address) is not int or not 0 < address < 2**48 or cache["address"] != address:
            raise ValueError()
        if cfg["host"] != BRIDGE + ".local" or not isinstance(cfg["api_key"], str):
            raise ValueError()
        if "transport_host" in cfg:
            ip = ipaddress.ip_address(cfg["transport_host"])
            if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
                raise ValueError()
        chars = {c["uuid"].lower(): c["handle"] for s in cache["services"]
                 if s["uuid"].lower() == SERVICE for c in s["characteristics"]}
        if any(type(chars.get(k)) is not int or not 0 < chars[k] < 65536 for k in (CURRENT, INTERVAL)):
            raise ValueError()
        return cfg, chars
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise ReadError("binding_invalid") from exc


def load_recovery(root, cfg):
    try:
        recovery = private_json(root / "recovery.json")
    except FileNotFoundError:
        return None
    if (recovery.get("expected_address") != cfg["expected_address"]
            or recovery.get("bridge") != BRIDGE
            or not isinstance(recovery.get("pin"), str)
            or re.fullmatch(r"[0-9]{6}", recovery["pin"]) is None):
        raise ReadError("binding_invalid")
    return recovery


async def authenticate(cli, address, actions, reauth, recovery=None, on_recovery=None):
    auth = asyncio.create_task(cli.bluetooth_device_pair(address, timeout=20))
    requested = asyncio.create_task(reauth.wait())
    try:
        await asyncio.wait({auth, requested}, return_when=asyncio.FIRST_COMPLETED)
        if reauth.is_set() and not auth.done():
            pin_action = next((a for a in actions if a.name == "airvalent_submit_passkey"), None)
            if recovery is None or pin_action is None or on_recovery is None:
                raise ReadError("reauth_required")
            on_recovery()  # Claim the single allowed submission before sending it.
            try:
                await cli.execute_service(pin_action, {"passkey": int(recovery["pin"])})
                paired = await auth
            except Exception as exc:
                raise ReadError("reauth_required") from exc
            if not paired.paired:
                raise authentication_error("reauth_required", getattr(paired, "error", None))
        else:
            paired = await auth
            if not paired.paired:
                raise authentication_error("bond_unavailable", getattr(paired, "error", None))
    finally:
        for task in (auth, requested):
            if not task.done():
                task.cancel()
        await asyncio.gather(auth, requested, return_exceptions=True)


async def read_with_recovery(cfg, chars, recovery, claim):
    try:
        return await read_sensor(cfg, chars, recovery, claim)
    except ReadError as exc:
        # A rejected stale key may be removed by the Bluetooth stack. Reconnect
        # once so the sensor can request its fixed PIN. Never retry a rejected PIN.
        if str(exc) != "bond_unavailable" or recovery is None:
            raise
        await asyncio.sleep(1)
        return await read_sensor(cfg, chars, recovery, claim)


async def read_sensor(cfg, chars, recovery=None, on_recovery=None):
    from aioesphomeapi import APIClient
    from aioesphomeapi.model import LogLevel

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    tunnel = await asyncio.create_subprocess_exec(
        "/usr/bin/ssh", "-N", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=10",
        "-o", "ServerAliveCountMax=2", "-L",
        f"127.0.0.1:{port}:{cfg.get('transport_host', BRIDGE + '.local')}:6053", "crosstown-mac",
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    cli = None
    unsubscribe = None
    connection_unsubscribe = None
    address = cfg["expected_address"]
    connection_started = False
    try:
        for _ in range(50):
            if tunnel.returncode is not None:
                raise ReadError("bridge_unavailable")
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                await writer.wait_closed()
                break
            except OSError:
                await asyncio.sleep(.2)
        else:
            raise ReadError("bridge_unavailable")
        cli = APIClient("127.0.0.1", port, "", noise_psk=cfg["api_key"], expected_name=BRIDGE)
        await asyncio.wait_for(cli.connect(login=True), 20)
        info = await cli.device_info()
        bonds = asyncio.Event()
        reauth = asyncio.Event()
        bond_count = None
        auth_failure_code = None

        def on_log(event):
            nonlocal bond_count, auth_failure_code
            message = bytes(event.message).decode("utf-8", errors="replace")
            if "airvalent_pair" not in message:
                return
            failure = re.search(r"Authentication complete success=0 mode=\d+ failure=(\d+)", message)
            if failure:
                auth_failure_code = int(failure[1])
            match = re.search(r"Stored BLE bonds now: (\d+)", message)
            if match:
                bond_count = int(match[1])
                bonds.set()
            if "Enrollment code requested by target" in message:
                reauth.set()

        cli.subscribe_logs(on_log, log_level=LogLevel.LOG_LEVEL_INFO, dump_config=False)
        _, actions = await cli.list_entities_services()
        bond_action = next((s for s in actions if s.name == "airvalent_bond_status"), None)
        if bond_action is None:
            raise ReadError("bond_unavailable")
        await cli.execute_service(bond_action, {})
        await asyncio.wait_for(bonds.wait(), 5)
        # Recovery is limited to this already-enrolled, explicitly bound sensor.
        if bond_count not in (0, 1) or (bond_count == 0 and recovery is None):
            raise ReadError("bond_unavailable")
        found = asyncio.Event()
        address_type = None

        def on_advertisements(batch):
            nonlocal address_type
            for adv in batch.advertisements:
                if adv.address == address:
                    address_type = adv.address_type
                    found.set()

        unsubscribe = cli.subscribe_bluetooth_le_raw_advertisements(on_advertisements)
        try:
            await asyncio.wait_for(found.wait(), 30)
        except TimeoutError as exc:
            raise ReadError("sensor_unavailable") from exc
        connection_started = True
        connection_unsubscribe = await cli.bluetooth_device_connect(
            address, lambda *args: None, timeout=25, disconnect_timeout=5,
            feature_flags=info.bluetooth_proxy_feature_flags,
            address_type=address_type, has_cache=True,
        )
        try:
            await authenticate(cli, address, actions, reauth, recovery, on_recovery)
        except ReadError as exc:
            code = exc.auth_failure_code if exc.auth_failure_code is not None else auth_failure_code
            raise authentication_error(str(exc), code) from exc
        if reauth.is_set():
            bonds.clear()
            await cli.execute_service(bond_action, {})
            await asyncio.wait_for(bonds.wait(), 5)
            if bond_count != 1:
                raise ReadError("reauth_required")
        raw = await cli.bluetooth_gatt_read(address, chars[CURRENT], timeout=10)
        interval = await cli.bluetooth_gatt_read(address, chars[INTERVAL], timeout=10)
        if len(interval) != 2 or int.from_bytes(interval, "little") not in (60, 120, 300, 600, 900):
            raise ReadError("invalid_reading")
        return {"co2_ppm": decode_current(raw), "read_at": now_iso(), "measurement_at": None,
                "sample_interval_seconds": int.from_bytes(interval, "little")}
    finally:
        try:
            if cli:
                if connection_started:
                    try:
                        await cli.bluetooth_device_disconnect(address, timeout=5)
                    except Exception:
                        pass
                if connection_unsubscribe:
                    connection_unsubscribe()
                if unsubscribe:
                    unsubscribe()
                await cli.disconnect()
        finally:
            if tunnel.returncode is None:
                tunnel.terminate()
                try:
                    await asyncio.wait_for(tunnel.wait(), 5)
                except TimeoutError:
                    tunnel.kill()
                    await tunnel.wait()



def append_sample(sample):
    root = Path(os.environ.get("OPENCLAW_HOME", str(Path.home() / ".openclaw")))
    room = dashboard_room(project_status({"sample": sample, "last_error": None}))
    record = {"timestamp": sample["read_at"], "rooms": [room], "history_origin": ORIGIN}
    result = subprocess.run(
        [str(root / "bin/nest-history-append"), "--history-dir", str(root / "nest-history"),
         "--dedupe-source", "airvalent", "--dedupe-structure", STRUCTURE, "--dedupe-room", ROOM],
        input=json.dumps(record), text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10,
    )
    if result.returncode != 0 or json.loads(result.stdout).get("ok") is not True:
        raise ValueError("history_append_failed")


def collect(root):
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ReadError("binding_invalid")
    fd = os.open(root / ".collector.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ReadError("binding_invalid")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"ok": True, "outcome": "already_running"}
        try:
            cache = private_json(root / "status.json")
        except FileNotFoundError:
            cache = {}
        except (OSError, ValueError):
            raise ReadError("invalid_cache")
        cache["last_attempt_at"] = now_iso()
        if (cache.get("last_error") in {"reauth_required", "bond_unavailable"}
                and cache.get("last_auth_failure_code") != AUTH_CONNECTION_TIMEOUT):
            # Preserve credential-failure latches; recover legacy timeout latches.
            write_private(root / "status.json", cache)
            return {"ok": False, "error": cache["last_error"]}
        try:
            cfg, chars = load_binding(root)
            recovery = load_recovery(root, cfg)
            submitted = False

            def claim_recovery():
                nonlocal submitted
                if submitted:
                    raise ReadError("reauth_required")
                submitted = True
                cache["last_recovery_at"] = now_iso()
                write_private(root / "status.json", cache)

            sample = asyncio.run(asyncio.wait_for(
                read_with_recovery(cfg, chars, recovery, claim_recovery), timeout=110))
        except Exception as exc:
            error = str(exc) if isinstance(exc, ReadError) else (
                "collection_timeout" if isinstance(exc, TimeoutError) else "read_failed")
            cache["last_error"] = error
            cache["last_auth_failure_code"] = getattr(exc, "auth_failure_code", None)
            cache["last_failure_at"] = now_iso()
            write_private(root / "status.json", cache)
            return {"ok": False, "error": error}
        cache.update(sample=sample, last_success_at=sample["read_at"], last_error=None, last_auth_failure_code=None, history_error=None)
        write_private(root / "status.json", cache)
        try:
            append_sample(sample)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            cache["history_error"] = "history_append_failed"
            write_private(root / "status.json", cache)
            return {"ok": False, "error": "history_append_failed"}
        return {"ok": True, "outcome": "collected", "read_at": sample["read_at"]}
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("status", "collect"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)  # Do not put device identities or protocol logs in launchd logs.
    os.umask(0o077)
    try:
        result = cached_status() if args.command == "status" else collect(state_dir())
    except Exception:
        result = {"ok": False, "error": "collector_failed"}
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0 if result["ok"] else 12


if __name__ == "__main__":
    sys.exit(main())
