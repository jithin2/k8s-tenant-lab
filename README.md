# Tenant lab: a multi-tenant Kubernetes platform on minikube

This lab tests the Kubernetes parts of the design on your laptop:
Kyverno, tenant isolation, GitOps onboarding from tenant files, and release and rollback.

Azure parts (Front Door, managed PostgreSQL, private endpoints, workload identity, Key Vault) cannot run on a laptop.
Here, each tenant gets a small Postgres pod instead of a managed database. Everything else works like the design.

Total time: about 4 hours. Do the labs in order.

**Before you start (lessons from the first run):**
- Docker Desktop needs at least 8 GB memory. With 7.6 GiB, use `--memory 6144`.
- In PowerShell, keep port-forwards running in their own window (see Lab 2e).
- ArgoCD checks Git every 3 minutes. Click **Refresh** in the UI instead of waiting.
- If you edit a file on github.com, run `git pull` before your next `git push`.

Commands work in PowerShell and bash. In PowerShell, always write `curl.exe`, not `curl`.

---

## 0. Start the cluster (10 min)

You need: Docker Desktop, minikube, kubectl, Helm, git, and a GitHub account.

```
minikube start --cpus 4 --memory 6144 --cni=cilium
minikube addons enable ingress
minikube addons enable metrics-server
kubectl get nodes
```

`--cni=cilium` matters. Without it, NetworkPolicies are created but not enforced.

---

## Lab 1. Kyverno (45 min)

**Goal:** see Kyverno generate the default-deny policy, and block a root pod.

Install Kyverno:

```
helm repo add kyverno https://kyverno.github.io/kyverno/
helm repo update
helm install kyverno kyverno/kyverno -n kyverno --create-namespace
kubectl -n kyverno get pods
```

Wait until all pods are `Running`. Then apply the permissions and the two policies:

```
kubectl apply -f kyverno/rbac.yaml
kubectl apply -f kyverno/tenant-baseline.yaml
kubectl apply -f kyverno/no-root.yaml
kubectl get clusterpolicy
```

Both policies should show `READY: True`.

**Test GENERATE.** Create a tenant namespace by hand:

```
kubectl apply -f tests/test-tenant-ns.yaml
kubectl get networkpolicy,limitrange -n test-tenant
```

**What you should see:** `default-deny` and `default-limits`. You never created them. Kyverno did, because of the label `tenant: "true"`.

**Test VALIDATE.**

```
kubectl apply -f tests/root-pod.yaml -n test-tenant
```

**What you should see:** an error that ends with `Pods in tenant namespaces must not run as root`.

**Test AUDIT mode.** Open `kyverno/no-root.yaml`, change `Enforce` to `Audit`, then:

```
kubectl apply -f kyverno/no-root.yaml
kubectl apply -f tests/root-pod.yaml -n test-tenant
kubectl get policyreport -n test-tenant
```

This time the pod is allowed, and the report shows a `fail`. The report can take a minute to appear.

Put it back:

```
kubectl delete -f tests/root-pod.yaml -n test-tenant
```

Change `Audit` back to `Enforce` in the file, then:

```
kubectl apply -f kyverno/no-root.yaml
kubectl delete namespace test-tenant
```

**What you learned:** generate, validate, audit vs enforce. The cluster enforces the rules, not people.

---

## Lab 2. Tenants from Git with ArgoCD (60 min)

**Goal:** one tenant file in Git becomes one running customer.

### 2a. Put this folder on GitHub

1. On github.com, create a new **public** repository called `k8s-tenant-lab`. Do not add a README.
2. Open `argocd/applicationset.yaml` and replace `REPO_URL` in **both** places with your repo URL, for example `https://github.com/<your-github-user>/k8s-tenant-lab.git`.
3. Push:

```
git init
git add .
git commit -m "Tenant lab"
git branch -M main
git remote add origin https://github.com/<your-github-user>/k8s-tenant-lab.git
git push -u origin main
```

The repo is public so ArgoCD can read it without a password. There are no real secrets in it.

### 2b. Install ArgoCD

```
kubectl create namespace argocd
kubectl apply -n argocd --server-side --force-conflicts -f https://raw.githubusercontent.com/argoproj/argo-cd/v2.13.3/manifests/install.yaml
kubectl -n argocd get pods
```

Wait until all pods are `Running` (2 to 3 minutes).

### 2c. Create the ApplicationSet

```
kubectl apply -f argocd/applicationset.yaml
kubectl -n argocd get applications
```

**What you should see:** `tenant-acme` and `tenant-bolt`. **No `tenant-contoso`**, because its file says `ready: "false"`.

### 2d. Watch the order

In a second terminal:

```
kubectl get pods -n tenant-acme -w
```

**What you should see, in this order:**
1. `postgres` starts (wave 0)
2. `db-migrate` runs and becomes `Completed` (wave 1)
3. `backend` (2 pods) and `frontend` start (wave 2)

This is the same idea as the design: the backend never starts before its database is ready.

Check what Kyverno and the chart created:

```
kubectl get networkpolicy,limitrange,resourcequota -n tenant-acme
```

`default-deny` and `default-limits` come from Kyverno. The `allow-...` rules and `tenant-quota` come from the chart. One owner per object.

### 2e. Call the app through the gateway

Start the port-forward in its own window, so it keeps running:

PowerShell:
```
Start-Process powershell -ArgumentList "-NoExit","-Command","kubectl -n ingress-nginx port-forward svc/ingress-nginx-controller 8080:80"
```

bash:
```
kubectl -n ingress-nginx port-forward svc/ingress-nginx-controller 8080:80 &
```

In the first terminal:

```
curl.exe -H "Host: acme.local" http://localhost:8080/api/version
curl.exe -H "Host: bolt.local" http://localhost:8080/api/version
```

**What you should see:**

```
{"tenant":"acme","version":"1.4.2"}
{"tenant":"bolt","version":"1.4.2"}
```

Same gateway, the host name decides which customer you reach.

---

## Lab 3. Prove that acme cannot reach bolt's database (20 min)

The test pod has the label `app: backend`, so the network rules treat it like acme's backend.

```
kubectl apply -f tests/nettest.yaml
kubectl -n tenant-acme wait --for=condition=Ready pod/nettest --timeout=120s
```

Own database:

```
kubectl -n tenant-acme exec nettest -- pg_isready -h postgres.tenant-acme.svc.cluster.local -p 5432 -t 5
```

**Expect:** `accepting connections`

Bolt's database:

```
kubectl -n tenant-acme exec nettest -- pg_isready -h postgres.tenant-bolt.svc.cluster.local -p 5432 -t 5
```

**Expect:** `no response`. The network lock works.

Also check Kyverno in a real tenant namespace:

```
kubectl apply -f tests/root-pod.yaml -n tenant-acme
```

**Expect:** rejected.

Clean up the test pod:

```
kubectl delete -f tests/nettest.yaml
```

---

## Lab 4. Onboard a new customer (15 min)

Open `tenants/contoso.yaml` and change `ready: "false"` to `ready: "true"`. Then:

```
git commit -am "Contoso is ready"
git push
```

ArgoCD checks Git every 3 minutes. Then:

```
kubectl -n argocd get applications
kubectl get pods -n tenant-contoso
curl.exe -H "Host: contoso.local" http://localhost:8080/api/version
```

**What you should see:** a full new customer from one changed line. Nobody ran kubectl for it.

---

## Lab 5. Release and roll back (20 min)

**Release.** In `tenants/acme.yaml`, change `version: "1.4.2"` to `version: "1.5.0"`.

```
git commit -am "Release 1.5.0 to acme (ring 1)"
git push
```

Wait up to 3 minutes, then:

```
curl.exe -H "Host: acme.local" http://localhost:8080/api/version
curl.exe -H "Host: bolt.local" http://localhost:8080/api/version
```

**Expect:** acme says `1.5.0`, bolt still says `1.4.2`. That is a ring: one customer first.

Watch the migration job run again before the new backend pods start:

```
kubectl get pods -n tenant-acme
```

**Roll back.** Pretend acme's errors went up:

```
git revert --no-edit HEAD
git push
```

Wait up to 3 minutes, then call acme again. **Expect:** `1.4.2`.

The database did not need a rollback, because the migration only **added** a column (`ADD COLUMN IF NOT EXISTS`). That is the "expand" step from the design.

---

## Lab 6. Look at the guard rails (10 min)

```
kubectl get resourcequota,hpa,pdb -n tenant-acme
kubectl get resourcequota,hpa,pdb -n tenant-bolt
kubectl describe resourcequota tenant-quota -n tenant-acme
```

**Notice:** acme is `small` (autoscaler max 3, quota 2 CPU) and bolt is `medium` (max 6, quota 4 CPU). The size in the tenant file decides this.

---

## Lab 7. Stop one Git mistake from deleting customers (20 min, optional)

**Goal:** see the risk from gap 1 in the design, then fix it.

**See the risk.** Contoso is live after Lab 4. Open `tenants/contoso.yaml`, set `ready: "false"`, then:

```
git commit -am "Oops: contoso not ready"
git push
```

Click Refresh on the ApplicationSet, or wait 3 minutes:

```
kubectl -n argocd get applications
kubectl get pods -n tenant-contoso
```

**What you see:** `tenant-contoso` is gone, and so are its pods and its database. One wrong line in Git deleted a customer. With a broken selector, it would be every customer.

**Fix it.** Set `ready: "true"` again, commit and push, and wait until contoso is back. Then allow per-ApplicationSet policies and set "create and update only":

```
kubectl -n argocd patch configmap argocd-cmd-params-cm --type merge -p '{"data":{"applicationsetcontroller.enable.policy.override":"true"}}'
kubectl -n argocd rollout restart deployment argocd-applicationset-controller
kubectl -n argocd patch applicationset tenants --type merge -p '{"spec":{"syncPolicy":{"applicationsSync":"create-update"}}}'
```

In PowerShell, the quotes inside `-p` can break. If they do, run `kubectl -n argocd edit configmap argocd-cmd-params-cm` and `kubectl -n argocd edit applicationset tenants` and add the lines by hand.

**Test again.** Set contoso to `ready: "false"`, commit and push, refresh.

**What you see:** `tenant-contoso` stays. The ApplicationSet no longer deletes apps by itself. Real deletion would happen only through the approved delete workflow.

Put contoso back to `ready: "true"` when you are done.

**What you learned:** the fix for gap 1, tested. In the interview you can say you saw the risk and the fix on a real cluster.

---

## Lab 8. Test the pipeline on your laptop with act (40 min)

**Goal:** run the pull request checks locally before you push, and see a bad change stopped before it reaches the cluster.

The folder now has two workflows:

| File | What it does |
|---|---|
| `.github/workflows/pr-checks.yml` | Checks every tenant file, renders the chart once per tenant, checks the Kubernetes schemas, and runs the same Kyverno rule as the cluster |
| `.github/workflows/onboard.yml` | A **simulation** of onboarding: it lists which tenants would get Terraform and a `ready: "true"` pull request. It does not touch Azure |

`act` runs GitHub Actions workflows on your laptop in Docker containers.

### 8a. Install act

PowerShell:
```
winget install nektos.act
act --version
```

Docker Desktop must be running. The file `.actrc` in this folder already tells act which runner image to use, so it will not ask. The first run downloads that image, which is big (a few GB), so start it on a good connection.

### 8b. List and run the workflows

From the repo folder:
```
act -l
act pull_request
```

**What you should see:** two jobs. `Check tenant files` prints `Tenant file check passed`. `Render chart and test policies` renders 3 tenants, kubeconform reports all resources valid, and Kyverno reports `fail: 0`.

Run the onboarding simulation:
```
act workflow_dispatch -W .github/workflows/onboard.yml
```

**What you should see:** `[simulated]` lines for every tenant that is not ready, for example contoso.

### 8c. Break a tenant file

In `tenants/acme.yaml`, change `size: small` to `size: huge`. In `tenants/bolt.yaml`, change `ready: "true"` to `ready: true` (no quotes). Then:
```
act pull_request -j tenant-files
```

**What you should see:** the job fails with two clear lines: size `huge` is not allowed, and `ready` must be the text "true" in quotes. The second one matters: without quotes, YAML reads a boolean, and the ArgoCD selector would silently skip bolt.

Undo both changes: `git checkout tenants/`

### 8d. Break the chart

In `chart/tenant-app/templates/backend.yaml`, delete the line `runAsNonRoot: true`. Then:
```
act pull_request
```

**What you should see:** `Render chart and test policies` fails at the Kyverno step with `Pods in tenant namespaces must not run as root`, for every tenant. The same rule that blocks the pod in the cluster now blocks the pull request. A developer finds out in minutes, not after a failed sync.

Undo: `git checkout chart/`

### 8e. Optional: run it on GitHub for real

```
git add .
git commit -m "Add pipeline checks"
git push
```

Open the **Actions** tab on GitHub. The same two workflows run there. In **Settings, Branches**, you can add a rule for `main` that requires the `Check tenant files` and `Render chart and test policies` checks. That is the branch protection from the design.

**What you learned:** the checks run the same way on your laptop, in GitHub, and as rules in the cluster. Mistakes are caught at the earliest point.

---

## What you can honestly say after this lab

- "I tested the Kubernetes part of the design on a local cluster: tenant files in Git, an ApplicationSet that only deploys tenants marked ready, and Kyverno generating the default-deny policy."
- "I proved that one tenant's pods could not reach another tenant's database."
- "I did a release to one tenant and rolled it back with a Git revert."
- If you did Lab 8: "I built pull request checks that validate tenant files, render the chart per tenant and run the same Kyverno rule as the cluster, and I tested them locally with act."
- If you did Lab 7: "I saw how one wrong line in Git could delete a tenant, and tested the create-update policy that stops it."

Do **not** say you tested the Azure parts. You did not.

---

## If something goes wrong

- **ArgoCD UI:** `kubectl -n argocd port-forward svc/argocd-server 8443:443`, open https://localhost:8443, user `admin`. Password:
  `kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}"`, then decode it from base64.
- **App stuck:** `kubectl -n argocd describe application tenant-acme` and look at the bottom.
- **Pod not starting:** `kubectl describe pod <name> -n tenant-acme`. A Kyverno message means a policy blocked it. A `quota` message means the namespace quota is full.
- **ArgoCD too slow:** in the UI, click Refresh on the app, instead of waiting 3 minutes.
- **No `tenant-...` applications:** check `REPO_URL` in `argocd/applicationset.yaml`, and that the repo is public.

- **act cannot connect to Docker:** start Docker Desktop and wait until it says it is running.
- **act fails while downloading tools:** the runner container needs internet for GitHub, PyPI and the Helm download. Try again, or run the same job on GitHub (Lab 8e).
- **act says a job is not found:** run `act -l` from the repo folder, not from a sub-folder.

Paste any error into the chat and we fix it together.

---

## Clean up

```
minikube delete
```
