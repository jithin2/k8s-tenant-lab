"""Check every tenant file before it can be merged.

Runs in the pull request pipeline. Fails with a clear message, so a typo in a
tenant file never reaches ArgoCD.
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ["name", "domain", "size", "version", "ring", "ready"]
NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,40}[a-z0-9])?$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

sizes = set(yaml.safe_load((ROOT / "chart/tenant-app/values.yaml").read_text())["sizes"])
errors = []
names, domains = {}, {}

files = sorted((ROOT / "tenants").glob("*.yaml"))
if not files:
    errors.append("No tenant files found in tenants/")

for f in files:
    t = yaml.safe_load(f.read_text()) or {}
    where = f"tenants/{f.name}"
    for key in REQUIRED:
        if key not in t:
            errors.append(f"{where}: missing '{key}'")
    name = str(t.get("name", ""))
    if name and not NAME_RE.match(name):
        errors.append(f"{where}: name '{name}' must be lowercase letters, digits and dashes")
    if name and f.stem != name:
        errors.append(f"{where}: file name must match name '{name}'")
    if "size" in t and t["size"] not in sizes:
        errors.append(f"{where}: size '{t['size']}' is not one of {sorted(sizes)}")
    if "version" in t and not VERSION_RE.match(str(t["version"])):
        errors.append(f"{where}: version '{t['version']}' must look like 1.4.2")
    if "ready" in t and t["ready"] not in ("true", "false"):
        errors.append(f"{where}: ready must be the text \"true\" or \"false\" in quotes, not {t['ready']!r}")
    if "ring" in t and str(t["ring"]) not in ("0", "1", "2"):
        errors.append(f"{where}: ring must be 0, 1 or 2")
    if name in names:
        errors.append(f"{where}: name '{name}' is also used in {names[name]}")
    names[name] = where
    dom = t.get("domain")
    if dom in domains:
        errors.append(f"{where}: domain '{dom}' is also used in {domains[dom]}")
    domains[dom] = where

if errors:
    print("Tenant file check FAILED:")
    for e in errors:
        print("  - " + e)
    sys.exit(1)
print(f"Tenant file check passed: {len(files)} files, sizes allowed: {sorted(sizes)}")
