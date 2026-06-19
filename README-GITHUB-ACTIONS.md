# GitHub Actions + Self-Hosted Runner — Complete Setup Guide
### Automated `terraform apply` on Network as Code Data Model Changes

This document explains, step by step, exactly how this repository wires GitHub
to a **self-hosted runner** on a local machine so that editing the Network as
Code (NaC) data model and pushing to `main` automatically runs `terraform apply`
against the live Meraki environment.

It is written for engineers new to CI/CD. Every command, file, and design
decision is explained — including *why*, not just *what*.

---

## Table of Contents

1. [What We Built and Why](#1-what-we-built-and-why)
2. [Architecture Overview](#2-architecture-overview)
3. [Key Concepts](#3-key-concepts)
4. [Why a Self-Hosted Runner (Not GitHub-Hosted)](#4-why-a-self-hosted-runner-not-github-hosted)
5. [Step 1 — Register the Runner with GitHub](#5-step-1--register-the-runner-with-github)
6. [Step 2 — Configure Runner Credentials and PATH](#6-step-2--configure-runner-credentials-and-path)
7. [Step 3 — The Workflow File Explained Line by Line](#7-step-3--the-workflow-file-explained-line-by-line)
8. [Step 4 — Start the Runner](#8-step-4--start-the-runner)
9. [Step 5 — Trigger and Verify the Pipeline](#9-step-5--trigger-and-verify-the-pipeline)
10. [Running the Runner as a Background Service](#10-running-the-runner-as-a-background-service)
11. [The Terraform State Consideration](#11-the-terraform-state-consideration)
12. [Security Model](#12-security-model)
13. [Troubleshooting — Real Issues We Hit](#13-troubleshooting--real-issues-we-hit)
14. [Maintenance Tasks](#14-maintenance-tasks)
15. [Quick Reference](#15-quick-reference)

---

## 1. What We Built and Why

### The goal

We wanted this outcome: **a team member edits an SSID or PSK in the NaC data
model, commits, and pushes — and the change is applied to the live Meraki
network automatically, with no manual `terraform apply`.**

### The result

When anyone pushes a change to a file under `data-model/nac/` on the `main`
branch:

1. GitHub detects the push and the changed path
2. GitHub queues a job for our self-hosted runner
3. The runner (running on the local laptop) picks up the job
4. The runner checks out the repository and runs `terraform init` → `terraform plan` → `terraform apply`
5. The Meraki Dashboard is updated to match the new data model

All of this happens within seconds, fully automatically.

### The pieces involved

| Piece | Role | Location |
|---|---|---|
| `.github/workflows/nac-apply.yml` | The pipeline definition — what to run and when | In the repository (committed) |
| Self-hosted runner | A background agent that executes the pipeline on the local machine | `~/actions-runner/` (not in the repo) |
| `~/actions-runner/.env` | Credentials + PATH for the runner | On the local machine only (never committed) |
| `network-as-code/` | The Terraform configuration the runner executes | In the repository (committed) |
| `data-model/nac/` | The data model whose changes trigger the pipeline | In the repository (committed) |

---

## 2. Architecture Overview

```
   Developer laptop                          GitHub.com
 ┌─────────────────────┐                ┌──────────────────────┐
 │                     │  git push      │                      │
 │  edit data-model/   │ ─────────────► │  Repository (main)   │
 │  nac/*.yaml         │                │                      │
 │                     │                │  Detects push to     │
 │                     │                │  data-model/nac/**   │
 │                     │                │         │            │
 │                     │                │         ▼            │
 │                     │                │  Queues job for      │
 │                     │                │  "self-hosted" label │
 │                     │                └──────────┬───────────┘
 │                     │                           │
 │  ┌───────────────┐  │   long-poll (outbound     │
 │  │ Self-hosted   │  │   HTTPS, runner initiates) │
 │  │ runner        │ ◄┼───────────────────────────┘
 │  │ ~/actions-    │  │   "Here is a job for you"
 │  │ runner/run.sh │  │
 │  │               │  │
 │  │ Executes:     │  │   terraform → Meraki API
 │  │  checkout     │  │ ───────────────────────────►  api.meraki.com
 │  │  init         │  │                                     │
 │  │  plan         │  │                                     ▼
 │  │  apply        │  │                            Meraki Dashboard
 │  └───────────────┘  │                            (SSIDs updated)
 └─────────────────────┘
```

**Key insight:** the runner makes an *outbound* connection to GitHub and waits
("long-polls") for jobs. GitHub never connects *inbound* to your laptop. This is
why a self-hosted runner works behind a home router or corporate firewall with
no port forwarding required.

---

## 3. Key Concepts

Before the steps, here are the terms you will encounter.

| Term | Plain-English meaning |
|---|---|
| **GitHub Actions** | GitHub's built-in automation system. It runs *workflows* in response to events (like a push). |
| **Workflow** | A YAML file in `.github/workflows/` describing what to run and when. |
| **Job** | A unit of work in a workflow that runs on one runner. Our workflow has one job: `terraform-apply`. |
| **Step** | A single command or action within a job (e.g., `terraform init`). |
| **Runner** | The machine/process that actually executes a job. Can be GitHub-hosted (cloud) or self-hosted (your machine). |
| **Self-hosted runner** | A runner you install and run on your own hardware. It registers with GitHub and waits for jobs. |
| **Trigger / event** | The condition that starts a workflow. Ours triggers on `push` to `main` affecting `data-model/nac/**`, plus manual `workflow_dispatch`. |
| **Label** | A tag that routes jobs to specific runners. We use the built-in `self-hosted` label. |

---

## 4. Why a Self-Hosted Runner (Not GitHub-Hosted)

GitHub offers free cloud-hosted runners. We deliberately chose a self-hosted
runner for three concrete reasons:

1. **Access to local Terraform state.** Our Terraform state file lives on the
   local machine (no remote backend configured). A cloud runner would start with
   empty state every run. The local runner has the existing state available.
   *(See [Section 11](#11-the-terraform-state-consideration) for the full nuance.)*

2. **Credentials stay local.** The Meraki API key and Wi-Fi PSKs never have to be
   uploaded to GitHub as encrypted secrets. They live in a local file
   (`~/actions-runner/.env`) that only the runner reads. Nothing sensitive leaves
   the machine.

3. **Network reachability.** If the Meraki environment were only reachable from a
   specific network (e.g., a management VLAN), a self-hosted runner on that
   network can reach it; a cloud runner could not.

The trade-off: the runner only processes jobs while it is running. If the laptop
is asleep or the runner is stopped, jobs queue until it comes back online.

---

## 5. Step 1 — Register the Runner with GitHub

Registration tells GitHub "this machine is allowed to run jobs for this repo,"
and gives the runner the credentials it needs to long-poll for work.

### 5.1 Download the runner software

On GitHub: **Repository → Settings → Actions → Runners → New self-hosted runner**.

GitHub shows OS-specific download commands. For macOS Apple Silicon (what we
used), it looks like:

```bash
mkdir -p ~/actions-runner && cd ~/actions-runner
curl -o actions-runner-osx-arm64.tar.gz -L \
  https://github.com/actions/runner/releases/download/v2.335.1/actions-runner-osx-arm64-2.335.1.tar.gz
tar xzf actions-runner-osx-arm64.tar.gz
```

This unpacks the runner into `~/actions-runner/`. Key files:

| File | Purpose |
|---|---|
| `config.sh` | One-time registration script |
| `run.sh` | Starts the runner (foreground) |
| `svc.sh` | Installs/manages the runner as a background service |
| `.env` | Environment variables loaded into every job (you create this) |

### 5.2 Run the configuration script

The registration command from the GitHub UI includes a **short-lived token**
(valid ~60 minutes). This is exactly the command we ran:

```bash
./config.sh \
  --url https://github.com/imanassypov/ansible-meraki-firmware-upgrade \
  --token AEVQKF3D77VAQP462OMWJFDKGWCRW
```

`config.sh` prompts for:

| Prompt | What we entered | Notes |
|---|---|---|
| Runner group | *(Enter — default)* | Only relevant for org-level runners |
| Runner name | `IMANASSY-M-YH56` | Defaults to the hostname |
| Additional labels | *(Enter — default)* | Adds `self-hosted`, `macOS`, `ARM64` automatically |
| Work folder | `_work` *(Enter — default)* | Where job checkouts happen |

After success, `config.sh` writes these registration files (all
auto-generated — do not edit or commit):

```
~/actions-runner/.runner               ← agent ID, name, GitHub URLs
~/actions-runner/.credentials          ← runner identity
~/actions-runner/.credentials_rsaparams ← runner private key
```

For reference, our `.runner` looks like:

```json
{
  "agentId": 2,
  "agentName": "IMANASSY-M-YH56",
  "poolName": "Default",
  "gitHubUrl": "https://github.com/imanassypov/ansible-meraki-firmware-upgrade",
  "workFolder": "_work"
}
```

> **If the token expired** (you waited too long), `config.sh` returns a `404`
> error. Just go back to the Runners page, copy a fresh token, and re-run
> `config.sh`. This is the single most common registration failure.

### 5.3 Verify registration

On GitHub: **Settings → Actions → Runners**. You should see your runner listed
with a status of **Offline** (it becomes **Idle**/**Active** once you start it in
Step 4).

---

## 6. Step 2 — Configure Runner Credentials and PATH

The runner executes jobs in a *non-interactive* shell. That shell does **not**
load your `~/.zshrc`, does **not** run direnv, and does **not** inherit the PATH
from your terminal. We must therefore give the runner everything it needs
explicitly, via the `~/actions-runner/.env` file.

Every variable in `.env` is injected into the environment of every job step.

### 6.1 Create the `.env` file

This is the exact content we use (real secret values redacted here):

```bash
# ~/actions-runner/.env
LANG=C.UTF-8
MERAKI_API_KEY=<your-meraki-api-key>
MERAKI_CISCO_LAB_PSK=<psk-for-meraki-core>
MERAKI_FTD_PSK=<psk-for-asusnet>
PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
```

### 6.2 Why each line is there

| Line | Reason |
|---|---|
| `MERAKI_API_KEY` | The CiscoDevNet/meraki Terraform provider reads this to authenticate to the Meraki API. |
| `MERAKI_CISCO_LAB_PSK` | Substituted into `data-model/nac/networks_nac.yaml` via the `!env` tag for the Meraki Core SSID PSK. |
| `MERAKI_FTD_PSK` | Same, for the ASUSNET SSID PSK. |
| `PATH` | **Critical.** Homebrew installs `terraform` to `/opt/homebrew/bin`. The runner's default PATH does *not* include this, so without this line every job fails with `terraform: command not found`. |
| `LANG` | Avoids locale warnings in some tools. |

> **This PATH line was the cause of our first failed run.** The very first
> pipeline execution failed because the runner could not find `terraform`. Adding
> `/opt/homebrew/bin` to PATH in `.env` fixed it. See
> [Section 13](#13-troubleshooting--real-issues-we-hit).

### 6.3 Important — restart after editing `.env`

The runner reads `.env` **once at startup**. If you edit `.env` while the runner
is running, the changes are not picked up until you stop and restart it.

---

## 7. Step 3 — The Workflow File Explained Line by Line

The workflow lives at [`.github/workflows/nac-apply.yml`](.github/workflows/nac-apply.yml).
Here is the full file with every line explained.

```yaml
name: "NaC — Terraform apply"
```
The display name shown in the GitHub **Actions** tab.

```yaml
on:
  workflow_dispatch: {}
  push:
    branches:
      - main
    paths:
      - "data-model/nac/**"
```

This is the **trigger** block — it defines *when* the workflow runs:

| Trigger | Effect |
|---|---|
| `workflow_dispatch: {}` | Adds a **Run workflow** button to the GitHub UI so you can trigger it manually any time. We added this so the pipeline can be tested without editing a data model file. |
| `push` + `branches: [main]` | Runs only on pushes to the `main` branch. |
| `paths: ["data-model/nac/**"]` | **Path filter** — the push only triggers the workflow if it changed at least one file under `data-model/nac/`. A push that only touches the README or Python scripts will *not* trigger Terraform. This scoping is intentional: only NaC data model changes should drive `terraform apply`. |

```yaml
jobs:
  terraform-apply:
    name: "terraform apply"
    runs-on: self-hosted
```

Defines one job, `terraform-apply`. The `runs-on: self-hosted` line is what
routes the job to **our** runner instead of a GitHub cloud runner — it matches
the built-in `self-hosted` label every self-hosted runner carries.

```yaml
    defaults:
      run:
        working-directory: network-as-code
```

Sets the working directory for every `run` step to `network-as-code/`. This is
where `terraform.tf` and `main.tf` live, so all `terraform` commands execute
from there without needing `cd`.

```yaml
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
```

The first step uses the official `actions/checkout` action to clone the
repository into the runner's work folder
(`~/actions-runner/_work/ansible-meraki-firmware-upgrade/...`). Without this, the
runner would have no code to run.

```yaml
      - name: Terraform init
        run: terraform init -input=false
```

Downloads the `netascode/nac-meraki` module and the `CiscoDevNet/meraki`
provider. `-input=false` ensures Terraform never pauses to prompt for input
(there is no human at the keyboard in CI).

```yaml
      - name: Terraform plan
        run: terraform plan -input=false -out=tfplan
```

Computes the changes needed and writes them to a file named `tfplan`. Saving the
plan to a file guarantees the apply step does exactly what the plan showed —
nothing more.

```yaml
      - name: Terraform apply
        run: terraform apply -input=false tfplan
```

Applies the saved `tfplan`. Because we pass a pre-computed plan file, Terraform
does not prompt for the usual `yes` confirmation — the saved plan is treated as
already approved.

---

## 8. Step 4 — Start the Runner

With registration done and `.env` configured, start the runner in the
foreground:

```bash
cd ~/actions-runner
./run.sh
```

Successful startup looks like this:

```
√ Connected to GitHub
Current runner version: '2.335.1'
2026-06-19 19:44:01Z: Listening for Jobs
```

`Listening for Jobs` means the runner is connected and waiting. Leave this
terminal open — closing it (or pressing `Ctrl+C`) stops the runner.

When a job arrives you will see it execute live:

```
2026-06-19 19:44:46Z: Running job: terraform apply
2026-06-19 19:45:08Z: Job terraform apply completed with result: Succeeded
```

---

## 9. Step 5 — Trigger and Verify the Pipeline

There are three ways to trigger the workflow.

### 9.1 By changing the data model (the real use case)

```bash
# Edit any file under data-model/nac/
vim data-model/nac/networks_nac.yaml

git add data-model/nac/networks_nac.yaml
git commit -m "feat: rotate Meraki Core PSK"
git push
```

The push to `main` touching `data-model/nac/**` satisfies the path filter and
triggers the workflow.

### 9.2 Manually from the GitHub UI

Because we added `workflow_dispatch`, you can run it on demand:

**Repository → Actions → "NaC — Terraform apply" → Run workflow → Run workflow**

This is useful for re-applying without making a data model change.

### 9.3 Watching the result

Two places to watch:

- **GitHub:** Repository → **Actions** tab → click the running/most-recent run →
  click the `terraform apply` job to see live step logs.
- **Locally:** the terminal running `./run.sh` prints job start/finish lines.

A healthy run we captured:

```
2026-06-19 19:44:46Z: Running job: terraform apply
2026-06-19 19:45:08Z: Job terraform apply completed with result: Succeeded
2026-06-19 19:46:45Z: Running job: terraform apply
2026-06-19 19:47:02Z: Job terraform apply completed with result: Succeeded
```

---

## 10. Running the Runner as a Background Service

Running `./run.sh` in a terminal works but stops when you close the terminal or
reboot. For an always-available runner, install it as a launchd service (macOS):

```bash
cd ~/actions-runner
./svc.sh install     # registers a launchd service for the current user
./svc.sh start       # starts it now and on every login
```

Manage the service with:

```bash
./svc.sh status      # check if running
./svc.sh stop        # stop it
./svc.sh uninstall   # remove the service (runner stays registered)
```

> **Note:** The service still reads `~/actions-runner/.env`. After editing
> `.env`, run `./svc.sh stop && ./svc.sh start` to reload it.

> **macOS caveat:** launchd user services run after login. For a runner that
> survives reboots without anyone logging in, a dedicated always-on machine or a
> Linux host with a systemd service is more appropriate.

---

## 11. The Terraform State Consideration

This is the most important nuance to understand about this setup.

### The issue

Terraform tracks what it manages in a **state file** (`terraform.tfstate`). Our
configuration uses the default **local backend**, meaning state is stored on
disk next to the Terraform files.

But the runner checks the repository out into a **separate work directory**:

```
~/actions-runner/_work/ansible-meraki-firmware-upgrade/ansible-meraki-firmware-upgrade/network-as-code/
```

This is *not* the same directory as your local development checkout. So the
runner's `terraform apply` starts with **no prior state** and computes a plan as
if creating everything fresh.

### Why it still works for us

Meraki SSID resources are **idempotent** at the API level — applying the same
SSID configuration to an existing SSID simply re-sets the same values. So even
though the runner's plan shows "create," the apply succeeds and the Dashboard
ends up in the correct state. Our captured runs confirm this:
`Apply complete! Resources: 4 added` with no errors.

### Why you should care for production

With local state in a throwaway work directory:

- Terraform cannot detect **drift** (manual Dashboard changes) reliably
- Terraform cannot safely **destroy/replace** resources, only converge values
- Two runs in parallel could conflict (no state locking)

### The production-grade fix

Configure a **remote backend** with state locking so every run — local or on the
runner — shares one authoritative state. For Meraki/Terraform this is typically:

```hcl
# network-as-code/terraform.tf (example — not yet configured in this repo)
terraform {
  backend "s3" {
    bucket         = "my-tfstate-bucket"
    key            = "meraki/nac/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "terraform-locks"   # provides state locking
  }
}
```

Other backend options: HCP Terraform (Terraform Cloud), Azure Blob, GCS, or a
Postgres backend. Any of these makes the runner and local developers operate on
identical state.

> **Current status:** this repo uses local state. The setup works because of
> Meraki idempotency, but a remote backend is the recommended next step before
> relying on this in production.

---

## 12. Security Model

| Concern | How it is handled |
|---|---|
| **API key exposure** | The Meraki API key lives only in `~/actions-runner/.env` on the local machine. It is never committed, never uploaded to GitHub, never printed in logs. |
| **PSK exposure** | PSKs are injected via the `!env` tag at apply time. Terraform marks them as `(sensitive value)` in plan/apply output, so they never appear in GitHub Actions logs. |
| **Inbound attack surface** | None. The runner only makes outbound HTTPS connections to GitHub. No inbound ports are opened. |
| **Who can trigger applies** | Only people with push access to `main`, plus anyone who can click **Run workflow** (repo write access). Branch protection on `main` can add review gates. |
| **Runner trust** | A self-hosted runner executes any workflow code pushed to the repo. **Never enable self-hosted runners on a public repository** — a malicious pull request could run arbitrary code on your machine. This repo should remain private, or restrict runners to specific workflows. |
| **Secret rotation** | Rotate the API key in the Meraki Dashboard, update `~/actions-runner/.env`, restart the runner. No Git changes required. |

---

## 13. Troubleshooting — Real Issues We Hit

These are actual problems encountered during setup and their resolutions.

| Symptom | Root cause | Fix |
|---|---|---|
| `Job terraform apply completed with result: Failed` on the very first run | Runner PATH did not include `/opt/homebrew/bin`, so `terraform` was not found | Added `PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin` to `~/actions-runner/.env`, then restarted the runner |
| `config.sh` returns `404 Not Found` during registration | The registration token expired (valid ~60 seconds–60 minutes) | Get a fresh token from Settings → Actions → Runners → New self-hosted runner, re-run `config.sh` |
| `A session for this runner already exists. Runner connect error: Conflict` | A previous `run.sh` process was still connected | Kill the stale process (`kill <pid>` / `pkill -f Runner.Listener`), then start `run.sh` again |
| Push to `main` did not trigger the workflow | The push did not change any file under `data-model/nac/` | The path filter is working as designed. Touch a NaC file, or use **Run workflow** (`workflow_dispatch`) |
| Changes to `.env` not taking effect | Runner reads `.env` only at startup | Restart the runner (`Ctrl+C` then `./run.sh`, or `./svc.sh stop && ./svc.sh start`) |
| `git push` rejected (`non-fast-forward`) after runner ran | The runner committed/pushed state or other changes, moving `origin/main` ahead | `git pull --rebase` then `git push` |
| Plan shows "4 to add" when you expected "0 to change" | Runner work dir has no local state file | Expected with local state; apply still succeeds due to Meraki idempotency. Configure a remote backend to eliminate this |

### Where to find runner logs

```bash
~/actions-runner/_diag/Runner_*.log    # runner connection/listener logs
~/actions-runner/_diag/Worker_*.log    # per-job execution logs
```

The full, human-readable step output is most easily read in the GitHub **Actions
tab** for each run.

---

## 14. Maintenance Tasks

### Update the runner software

GitHub auto-updates self-hosted runners by default when a new version is
released. To update manually:

```bash
cd ~/actions-runner
./svc.sh stop        # if running as a service
# download the new version per the GitHub Runners page, extract over the folder
./svc.sh start
```

### Rotate the Meraki API key

```bash
# 1. Generate a new key in Meraki Dashboard → Profile → API access
# 2. Update the runner env
vim ~/actions-runner/.env        # replace MERAKI_API_KEY value
# 3. Restart so the new value is loaded
./svc.sh stop && ./svc.sh start  # or Ctrl+C + ./run.sh
```

### Remove the runner entirely

```bash
cd ~/actions-runner
./svc.sh uninstall               # remove the service
./config.sh remove --token <fresh-removal-token>   # deregister from GitHub
```

Get the removal token from Settings → Actions → Runners → (your runner) →
Remove.

---

## 15. Quick Reference

### Files involved

| Path | Committed? | Purpose |
|---|---|---|
| `.github/workflows/nac-apply.yml` | Yes | Pipeline definition |
| `network-as-code/terraform.tf` | Yes | Provider config |
| `network-as-code/main.tf` | Yes | NaC module declaration |
| `data-model/nac/*.yaml` | Yes | Trigger source + SSID/PSK intent |
| `~/actions-runner/.env` | **No** | Credentials + PATH for the runner |
| `~/actions-runner/.runner` | **No** | Auto-generated registration metadata |

### Everyday commands

```bash
# Start the runner (foreground)
cd ~/actions-runner && ./run.sh

# Start/stop as a background service
./svc.sh start
./svc.sh stop

# Trigger by data model change
git add data-model/nac/networks_nac.yaml
git commit -m "feat: update SSID"
git push

# Trigger manually: GitHub → Actions → "NaC — Terraform apply" → Run workflow

# Tail job logs
ls -t ~/actions-runner/_diag/Worker_*.log | head -1 | xargs tail -f
```

### Trigger rules at a glance

| Action | Triggers workflow? |
|---|---|
| Push to `main` changing `data-model/nac/networks_nac.yaml` | ✅ Yes |
| Push to `main` changing `data-model/firmware/firmware_targets.yaml` | ❌ No (wrong path) |
| Push to `main` changing only `README.md` | ❌ No (wrong path) |
| Push to a branch other than `main` | ❌ No |
| Clicking **Run workflow** in the Actions tab | ✅ Yes (`workflow_dispatch`) |

---

## References

- [GitHub Actions — Self-Hosted Runners](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/about-self-hosted-runners)
- [Adding Self-Hosted Runners](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/adding-self-hosted-runners)
- [Workflow Syntax — `on`, `paths`, `workflow_dispatch`](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions)
- [`actions/checkout`](https://github.com/actions/checkout)
- [Terraform — Backend Configuration](https://developer.hashicorp.com/terraform/language/settings/backends/configuration)
- [Security Hardening for GitHub Actions](https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions)
