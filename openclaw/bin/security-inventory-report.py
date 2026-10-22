#!/usr/bin/env python3
"""Normalize an attended private survey; does not collect or remediate anything."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


def canonical(product, ecosystem):
    return re.sub(r"[-_.]+", "-", product).lower() if ecosystem == "pypi" else product.lower()


def build(directory):
    scope = json.loads((directory / "scope.json").read_text())
    report = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
              "coverage": "partial", "vulnerability_clearance": False,
              "assets": [], "components": [], "unverified_assets": scope["unverified_assets"],
              "exclusions": scope["exclusions"], "followups": scope["followups"],
              "daily_monitor_design": scope["daily_monitor_design"]}
    def add(asset, product, version, context, evidence, observed_at, state="installed", ecosystem="product"):
        identity = f"{asset}|{context}|{ecosystem}|{product}|{version}"
        report["components"].append({"id": hashlib.sha256(identity.encode()).hexdigest()[:20],
            "asset_id": asset, "product": product, "match_name": canonical(product, ecosystem),
            "version": version, "ecosystem": ecosystem, "context": context, "state": state,
            "evidence": evidence, "observed_at": observed_at, "applicability": "not_assessed"})
    for asset in scope["hosts"]:
        path = directory / asset["file"]
        try:
            host = json.loads(path.read_text())
            if host.get("schema_version") != 1:
                raise ValueError("unsupported host schema")
        except (OSError, ValueError):
            report["assets"].append({**asset, "coverage": "unavailable"})
            continue
        observed = host["observed_at"]
        report["assets"].append({**asset, "coverage": "observed_partial", "observed_at": observed,
                                  "evidence_file": asset["file"]})
        aid = asset["id"]
        add(aid, "macOS" if host["os"] == "Darwin" else host.get("os_release", {}).get("PRETTY_NAME", host["os"]),
            host["os_version"].get("text") if host["os"] == "Darwin" else host.get("os_release", {}).get("VERSION_ID"),
            "host", "os_version", observed, "running")
        if host["os"] == "Linux":
            add(aid, "Linux kernel", host.get("kernel"), "host", "uname", observed, "running")
        procs = host.get("processes", {}).get("processes", [])
        mapped = {p for proc in procs for p in proc.get("mapped_executables", [])}
        mapped.update(proc["mapped_executable"] for proc in procs if proc.get("mapped_executable"))
        for binary in host.get("node_binaries", []):
            state = "running" if binary["resolved"] in mapped else "installed_not_observed_running"
            if binary.get("executable") is False and state != "running":
                state = "disabled_non_executable"
            add(aid, "Node.js", binary["version"].get("text"), binary["resolved"], "binary_version_probe",
                observed, state)
        known_nodes = {b["resolved"] for b in host.get("node_binaries", [])}
        for path in sorted(mapped - known_nodes):
            if Path(path).name == "node":
                add(aid, "Node.js", None, path, "mapped_executable_missing_on_disk", observed, "running")
        for app in host.get("apps", {}).get("applications", []):
            app_path = app.get("path", "")
            running = bool(app_path) and any(proc["executable"].startswith(app_path + "/") for proc in procs)
            add(aid, app["name"], app.get("version"), app_path, "app_bundle_metadata", observed,
                "running_app_metadata" if running else "installed_not_observed_running")
            for bundle in app.get("bundles", []):
                add(aid, bundle["product"], bundle.get("version"), app_path, "embedded_metadata_not_launch_probe", observed)
                if bundle["product"] == "Electron":
                    add(aid, "Node.js", None, app_path, "electron_bundled_node_version_unverified", observed, "unknown")
        brew = host.get("homebrew", {})
        if brew.get("status") == "observed":
            for line in brew["text"].splitlines():
                parts = line.split()
                if len(parts) > 1:
                    for version in parts[1:]:
                        add(aid, parts[0], version, "Homebrew", "brew_list_versions", observed, ecosystem="homebrew")
        for package in host.get("node_packages", []):
            if package.get("name"):
                add(aid, package["name"], package.get("version"), package["path"], "installed_package_json", observed, ecosystem="npm")
        for env in host.get("python_environments", []):
            add(aid, "Python", env.get("python_version"), env["path"], "venv_metadata_not_process_mapping", observed)
            for package in env["packages"]:
                add(aid, package["name"], package.get("version"), env["path"], "installed_dist_info", observed, ecosystem="pypi")
        for binary in host.get("go_binaries", []):
            header = binary.get("build_header") or ""
            match = re.search(r"\bgo(\d+\.\d+(?:\.\d+)?)", header)
            add(aid, "Go runtime", match.group(1) if match else None, binary["path"],
                "embedded_build_info" if match else "build_info_unavailable", observed, "installed" if match else "unknown")
    for project in scope["projects"]:
        root = Path(project["path"])
        observed = report["generated_at"]
        lock = root / "package-lock.json"
        manifest = root / "package.json"
        if lock.is_file():
            data = json.loads(lock.read_text())
            for location, package in data.get("packages", {}).items():
                if not location or not package.get("version"):
                    continue
                name = package.get("name") or location.split("node_modules/")[-1]
                add(project["id"], name, package["version"], location, "source_lockfile_not_deployment", observed, "source_locked", "npm")
        elif manifest.is_file():
            data = json.loads(manifest.read_text())
            for group in ["dependencies", "devDependencies"]:
                for name, version in data.get(group, {}).items():
                    add(project["id"], name, version, group, "source_manifest_range_not_deployment", observed, "source_declared", "npm")
        requirements = root / "requirements.txt"
        if requirements.is_file():
            for line in requirements.read_text().splitlines():
                match = re.fullmatch(r"([a-zA-Z0-9_.-]+)([<>=!~].*)?", line.strip())
                if match:
                    add(project["id"], match.group(1), match.group(2), "requirements.txt", "source_requirement_not_installed", observed, "source_declared", "pypi")
        report["assets"].append({**project, "coverage": "source_only", "observed_at": observed})
    # Multiple symlink paths can refer to the same executable; keep one identity.
    report["components"] = list({row["id"]: row for row in report["components"]}.values())
    return report


def atomic_write(path, text):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".survey-")
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve(strict=True)
    if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise ValueError("survey directory must be owner-only")
    os.umask(0o077)
    report = build(directory)
    atomic_write(directory / "inventory.json", json.dumps(report, indent=2) + "\n")
    lines = ["Infrastructure survey — " + report["generated_at"],
             "PARTIAL COVERAGE — no vulnerability clearance; no services changed.",
             f"Normalized component observations: {len(report['components'])}", "", "Observed / source-only assets:"]
    lines += [f"- {a['id']}: {a['coverage']}" for a in report["assets"]]
    lines += ["", "Explicit coverage gaps:"]
    lines += [f"- {a['id']}: {a['coverage']}. {a['evidence']}" for a in report["unverified_assets"]]
    lines += ["", "Follow-ups:"] + ["- " + value for value in report["followups"]]
    lines += ["", "Daily briefing integration (proposed, not deployed):"]
    lines += [f"- {key}: {value}" for key, value in report["daily_monitor_design"].items()]
    lines += ["", "Excluded:"] + ["- " + value for value in report["exclusions"]]
    atomic_write(directory / "coverage-report.txt", "\n".join(lines) + "\n")
    print(json.dumps({"assets": len(report["assets"]), "components": len(report["components"]),
                      "unverified_assets": len(report["unverified_assets"]), "coverage": "partial"}))


if __name__ == "__main__":
    main()
