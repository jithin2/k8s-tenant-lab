"""Render the Helm chart once per tenant, the same way ArgoCD will.

Writes rendered/<tenant>.yaml for kubeconform and Kyverno, plus
rendered/kyverno-values.yaml, which tells the Kyverno CLI that every
tenant-<name> namespace has the label tenant: "true", like in the cluster.
"""
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "rendered"
OUT.mkdir(exist_ok=True)
CLUSTER_SCOPED = {"Namespace", "ClusterRole", "ClusterRoleBinding", "ClusterPolicy",
                  "CustomResourceDefinition", "PriorityClass", "StorageClass"}

namespaces = []
for f in sorted((ROOT / "tenants").glob("*.yaml")):
    t = yaml.safe_load(f.read_text())
    ns = f"tenant-{t['name']}"
    cmd = ["helm", "template", f"tenant-{t['name']}", str(ROOT / "chart/tenant-app"),
           "--namespace", ns,
           "--set-string", f"tenant.name={t['name']}",
           "--set-string", f"tenant.domain={t['domain']}",
           "--set-string", f"version={t['version']}",
           "--set-string", f"size={t['size']}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"helm template failed for {t['name']}:\n{result.stderr}")
        sys.exit(1)
    docs = [d for d in yaml.safe_load_all(result.stdout) if d]
    for d in docs:
        if d.get("kind") not in CLUSTER_SCOPED:
            d.setdefault("metadata", {})["namespace"] = ns
    (OUT / f"{t['name']}.yaml").write_text(yaml.safe_dump_all(docs, sort_keys=False))
    namespaces.append(ns)
    print(f"Rendered {t['name']}: {len(docs)} objects")

values = {
    "apiVersion": "cli.kyverno.io/v1alpha1",
    "kind": "Value",
    "metadata": {"name": "values"},
    "namespaceSelector": [{"name": ns, "labels": {"tenant": "true"}} for ns in namespaces],
}
(OUT / "kyverno-values.yaml").write_text(yaml.safe_dump(values, sort_keys=False))
