#!/usr/bin/env python3
"""Read-only, credential-free host metadata survey; emits private inventory JSON.

No recursive home scan, environment/argv capture, application imports, package
refresh, service changes, network probes or secret/config/database reads.
Run with Python -I -S. Missing evidence is reported, never inferred as absence.
"""
import email.parser
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import subprocess
import time


def command(args, timeout=20):
    env = {"HOME": str(Path.home()), "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
           "HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_ANALYTICS": "1", "LC_ALL": "C"}
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
        if result.returncode:
            return {"status": "unavailable", "exit_code": result.returncode}
        if len(result.stdout) > 4_000_000:
            return {"status": "unavailable", "error": "output_limit"}
        return {"status": "observed", "text": result.stdout.strip()}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"status": "unavailable", "error": type(error).__name__}


def safe_text(value):
    return str(value).replace("\n", " ")[:300]


def metadata(path):
    record = {"path": str(path)}
    try:
        data = json.loads(path.read_text())
        record.update(name=safe_text(data.get("name", path.parent.name)),
                      version=safe_text(data.get("version", "unknown")), evidence="package_json")
    except (OSError, ValueError):
        record["status"] = "unavailable"
    return record


def distributions(root):
    entries = []
    for path in sorted(root.glob("*.dist-info/METADATA"))[:1000]:
        try:
            with path.open() as stream:
                header = email.parser.Parser().parse(stream, headersonly=True)
            entries.append({"name": safe_text(header.get("Name", "unknown")),
                            "version": safe_text(header.get("Version", "unknown"))})
        except OSError:
            entries.append({"name": path.parent.name, "status": "unavailable"})
    config = root.parents[2] / "pyvenv.cfg"
    version = None
    if config.is_file():
        match = re.search(r"^version\s*=\s*(\S+)", config.read_text(), re.M)
        version = match.group(1) if match else None
    return {"path": str(root), "python_version": version, "packages": entries, "coverage": "metadata_only_max_1000"}


def mac_apps():
    apps, gaps = [], []
    for root in [Path("/Applications"), Path.home() / "Applications"]:
        if not root.is_dir():
            gaps.append(str(root))
            continue
        for path in sorted(root.glob("*.app")):
            try:
                with (path / "Contents/Info.plist").open("rb") as stream:
                    info = plistlib.load(stream)
                row = {"name": path.stem, "path": str(path), "version": safe_text(info.get("CFBundleShortVersionString", "unknown")),
                       "build": safe_text(info.get("CFBundleVersion", "unknown")), "evidence": "bundle_metadata", "bundles": []}
                electron = path / "Contents/Frameworks/Electron Framework.framework/Resources/Info.plist"
                if electron.is_file():
                    with electron.open("rb") as stream:
                        framework = plistlib.load(stream)
                    row["bundles"].append({"product": "Electron", "version": safe_text(framework.get("CFBundleVersion", "unknown")),
                                           "node_version": None, "node_patch_status": "unverified"})
                for release in [path / "Contents/jbr/Contents/Home/release", path / "Contents/jbr/release"]:
                    if release.is_file():
                        match = re.search(r'^JAVA_VERSION="([^"\n]+)"', release.read_text(), re.M)
                        row["bundles"].append({"product": "Java", "version": match.group(1) if match else None})
                apps.append(row)
            except (OSError, ValueError, plistlib.InvalidFileException):
                apps.append({"name": path.stem, "status": "unavailable"})
    return {"applications": apps, "missing_roots": gaps, "coverage": "top_level_app_bundles_only"}


def service_definitions():
    records = []
    for path in sorted((Path.home() / "Library/LaunchAgents").glob("*.plist")):
        try:
            with path.open("rb") as stream:
                info = plistlib.load(stream)
            label = info.get("Label", "")
            if not (label.startswith(("ai.openclaw.", "com.openclaw.", "com.dylanbochman."))):
                continue
            args = info.get("ProgramArguments", [])
            records.append({"label": label, "executable": info.get("Program") or (args[0] if args else None),
                            "evidence": "installed_definition_not_loaded_state"})
        except (OSError, ValueError, plistlib.InvalidFileException):
            records.append({"definition": path.name, "status": "unavailable"})
    return records


def processes(mac):
    result = command(["/bin/ps", "-axo", "pid=,comm="])
    if result["status"] != "observed":
        return result
    selected = []
    for line in result["text"].splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid, executable = parts
        name = Path(executable).name.lower()
        if not (re.match(r"^(node|python|openclaw|caddy|redis|postgres|nginx|php|docker|tailscale|pinchtab|codex|claude|ollama)", name)
                or (mac and ".app/Contents/" in executable)):
            continue
        row = {"pid": int(pid), "executable": executable, "evidence": "process_comm_not_argv"}
        if mac and re.match(r"^(node|openclaw|python)", name):
            mapped = command(["/usr/sbin/lsof", "-a", "-p", pid, "-d", "txt", "-Fn"], timeout=4)
            if mapped["status"] == "observed":
                paths = [x[1:] for x in mapped["text"].splitlines() if x.startswith("n/")]
                row["mapped_executables"] = [p for p in paths if re.match(r"^(node|python)(\d|\.|$)", Path(p).name, re.I)]
        if not mac:
            try:
                row["mapped_executable"] = str(Path(f"/proc/{pid}/exe").resolve(strict=True))
            except OSError:
                row["mapping_status"] = "unavailable"
        selected.append(row)
        if len(selected) >= 160:
            break
    return {"status": "observed", "processes": selected, "coverage": "selected_runtime_and_app_names_max_160"}


def main():
    home, mac = Path.home(), platform.system() == "Darwin"
    report = {"schema_version": 1, "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "os": platform.system(), "architecture": platform.machine(), "kernel": platform.release(),
              "coverage": "partial", "vulnerability_clearance": False,
              "limitations": ["No process arguments or environment collected", "No credentials or application databases read",
                              "Installed metadata is not proof of runtime activation or vendor backports",
                              "Missing/truncated/inaccessible observations do not establish absence"]}
    report["os_version"] = command(["/usr/bin/sw_vers", "-productVersion"]) if mac else command(["uname", "-r"])
    report["processes"] = processes(mac)
    if mac:
        report["apps"] = mac_apps()
        report["launchagents"] = service_definitions()
        report["homebrew"] = command(["/opt/homebrew/bin/brew", "list", "--versions"], timeout=45)
    else:
        report["os_release"] = {line.split("=", 1)[0]: line.split("=", 1)[1].strip('"') for line in Path("/etc/os-release").read_text().splitlines()
                                if line.startswith(("ID=", "VERSION_ID=", "PRETTY_NAME="))}
        report["system_packages"] = command(["dpkg-query", "-W", "-f=${Package}\t${Version}\n"], timeout=30)
    bins = [Path("/opt/homebrew/bin/node"), Path("/usr/bin/node"), Path("/usr/local/bin/node"), home / ".local/bin/node"]
    bins += sorted(Path("/opt/homebrew/Cellar").glob("node*/*/bin/node"))
    bins += sorted((home / ".local/share/openclaw/runtimes").glob("node-*/bin/node"))
    for process in report["processes"].get("processes", []):
        bins += [Path(p) for p in process.get("mapped_executables", []) if Path(p).name == "node"]
    report["node_binaries"] = []
    for path in sorted(set(bins)):
        if path.is_file():
            report["node_binaries"].append({"path": str(path), "resolved": str(path.resolve()),
                                            "version": command([str(path), "--version"], timeout=5),
                                            "evidence": "version_probe_not_network_clearance"})
    manifests = []
    for root in [Path("/opt/homebrew/lib/node_modules"), Path("/usr/local/lib/node_modules"), home / ".openclaw/rest980"]:
        manifests += list(root.glob("*/package.json")) + list(root.glob("@*/*/package.json"))
        if (root / "package.json").is_file():
            manifests.append(root / "package.json")
    report["node_packages"] = [metadata(path) for path in sorted(set(manifests))]
    report["go_binaries"] = []
    go = Path("/opt/homebrew/bin/go") if mac else Path("/usr/bin/go")
    if go.is_file():
        for name in ["gh", "tailscale", "pinchtab", "gws", "hcloud", "cloudflared"]:
            path = Path("/opt/homebrew/bin" if mac else "/usr/bin") / name
            if path.is_file():
                result = command([str(go), "version", "-m", str(path)], timeout=8)
                if result["status"] == "observed" and result["text"].strip():
                    lines = result["text"].splitlines()
                    report["go_binaries"].append({"path": str(path), "build_header": lines[0] if lines else None,
                                                  "modules": [line.strip() for line in lines[1:] if line.lstrip().startswith(("mod\t", "dep\t"))],
                                                  "evidence": "embedded_build_info_not_compiler_version"})
                else:
                    report["go_binaries"].append({"path": str(path), "status": "unavailable"})
    # Fixed application/runtime scopes, never the whole home or project contents.
    roots = [home / "repos/financial-dashboard/venv", home / "repos/crypto-tracker/.venv",
             home / ".openclaw/.venv", Path("/venvs/apps_venv")]
    roots += sorted((home / ".openclaw/venvs").glob("*"))
    sites = []
    for root in roots:
        sites += list(root.glob("lib/python*/site-packages"))
    report["python_environments"] = [distributions(path) for path in sorted(set(sites))]
    report["tcp_listeners"] = command(["/usr/sbin/lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpcn"] if mac
                                      else ["ss", "-H", "-lnt"], timeout=10)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
