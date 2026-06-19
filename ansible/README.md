# Meraki Firmware Upgrade — Ansible Playbook Collection

Automates Cisco Meraki firmware upgrades across one or more networks using the
Meraki Dashboard REST API. Networks are targeted by **tag** so you never have to
list individual network IDs; any network carrying the tag you specify is
automatically included, even across multiple Meraki organizations.

---

## Table of Contents

1. [What This Collection Does](#what-this-collection-does)
2. [How It Works — Architecture Overview](#how-it-works--architecture-overview)
3. [Prerequisites](#prerequisites)
4. [Directory Structure](#directory-structure)
5. [Galaxy Collection — cisco.meraki](#galaxy-collection--ciscomeraki)
6. [Installation](#installation)
7. [Configuration](#configuration)
8. [Protecting Your API Key with Ansible Vault](#protecting-your-api-key-with-ansible-vault)
9. [Running the Playbooks](#running-the-playbooks)
10. [Variable Reference](#variable-reference)
11. [How Each Playbook Works — Step by Step](#how-each-playbook-works--step-by-step)
12. [Expected Output](#expected-output)
13. [Playbook Ordering and Workflow](#playbook-ordering-and-workflow)
14. [Troubleshooting](#troubleshooting)

---

## What This Collection Does

| Playbook | Purpose |
|---|---|
| `1.0_check_available_firmware_by_tag.yml` | **Read-only.** Discovers all networks matching your tag(s), queries the Meraki API for current and available firmware versions per device type, and prints a formatted summary with version IDs you need for scheduling. |
| `2.0_schedule_firmware_upgrade_by_tag.yml` | **Schedules upgrades.** For every tagged network, compares the current firmware version against the target you specify. Only schedules an upgrade when the current version differs — it is safe to run repeatedly. |

> **Rule of thumb:** always run playbook 1.0 first to discover the correct
> version IDs, then pass those IDs to playbook 2.0.

---

## How It Works — Architecture Overview

These playbooks do **not** SSH into any devices. All communication goes through
the Meraki Dashboard REST API over HTTPS from your local machine (or CI server).
Ansible connects only to `localhost` and issues HTTP calls on your behalf.

```
Your machine (localhost)
        │
        │  HTTPS (port 443)
        ▼
Meraki Dashboard API  ──►  Your Meraki Organization(s)
(api.meraki.com)               └── Networks tagged "my-tag"
                                       └── MX / MS / MR devices
```

The playbooks follow this sequence for both 1.0 and 2.0:

```
1. Authenticate (API key in every request header)
2. GET  /organizations          → find all orgs the key can see
3. GET  /organizations/{id}/networks  → find all networks per org
4. Filter networks by tag (Ansible, no extra API call)
5. GET  /networks/{id}/firmwareUpgrades  → get current + available versions
6. [2.0 only] PUT  /networks/{id}/firmwareUpgrades  → schedule the upgrade
```

---

## Prerequisites

### Software versions

| Component | Minimum version | Tested with |
|---|---|---|
| Python | 3.9 | 3.10.4 |
| Ansible Core | 2.15 | 2.17.6 |
| cisco.meraki Galaxy collection | 2.18.0 | 2.18.3 |

### What you need before starting

- A **Meraki Dashboard API key** — generated in the Meraki Dashboard under
  *Organization → Settings → Dashboard API access → Generate API key*
- Your networks must be **tagged** in the Meraki Dashboard
  (*Network → Settings → Tags*). Tags are case-sensitive.
- Internet access to `api.meraki.com:443` from the machine running Ansible

### Installing Ansible

If you do not yet have Ansible installed, the simplest approach on macOS/Linux is:

```bash
pip3 install --user ansible
```

Verify the installation:

```bash
ansible --version
# ansible [core 2.17.x]
```

---

## Directory Structure

```
ansible-meraki-firmware-upgrade/
│
├── data-model/
│   └── firmware_targets.yaml                    ← Desired firmware end state (edit this)
│
├── ansible/
│   ├── README.md                               ← you are here
│   │
│   ├── 1.0_check_available_firmware_by_tag.yml     ← Step 1: discover firmware versions
│   ├── 2.0_schedule_firmware_upgrade_by_tag.yml    ← Step 2: schedule upgrades
│   │
│   ├── collections/
│   │   └── requirements.yml                    ← Galaxy collection dependency
│   │
│   ├── inventory/
│   │   └── meraki.yml                          ← Ansible inventory (localhost only)
│   │
│   └── group_vars/
│       └── all/
│           ├── vars.yml                        ← Non-sensitive shared variables
│           └── vault.yml                       ← API key (must be vault-encrypted)
│
└── python/
    ├── 1.0_check_available_firmware_by_tag.py  ← Python equivalent of playbook 1.0
    ├── 2.0_schedule_firmware_upgrade_by_tag.py ← Python equivalent of playbook 2.0
    └── requirements.txt                        ← pip dependencies (meraki, PyYAML)
```

---

## Galaxy Collection — cisco.meraki

### What is an Ansible Galaxy collection?

Ansible ships with a set of built-in modules (such as `ansible.builtin.debug`),
but vendor-specific functionality is distributed as **collections** — packages
hosted on [Ansible Galaxy](https://galaxy.ansible.com) that add new modules,
plugins, and roles to Ansible.

### Why cisco.meraki?

The `cisco.meraki` collection provides Ansible modules that wrap the Meraki
Dashboard REST API. Instead of writing raw `curl` commands or Python scripts,
you call a module like:

```yaml
- name: Get firmware info
  cisco.meraki.networks_firmware_upgrades_info:
    meraki_api_key: "{{ meraki_api_key }}"
    networkId: "{{ network_id }}"
```

Ansible handles authentication headers, JSON serialization, error checking, and
retry logic — the same way it manages SSH-based device modules.

### Modules used in this collection

| Module | What it does |
|---|---|
| `cisco.meraki.organizations_info` | Lists all Meraki organizations the API key has access to |
| `cisco.meraki.networks_info` | Lists all networks in a given organization |
| `cisco.meraki.networks_firmware_upgrades_info` | Returns current and available firmware for each device type in a network |
| `cisco.meraki.networks_firmware_upgrades` | Schedules a firmware upgrade for specified device types |

> **Note:** All modules in this collection require `cisco.meraki >= 2.18.0`.
> Earlier versions used a different, now-deprecated module naming scheme
> (`meraki_*` prefix). Those deprecated modules are **not** used here.

---

## Installation

### Step 1 — Clone the repository

```bash
git clone <your-repo-url>
cd ansible-meraki-firmware-upgrade
```

### Step 2 — Install the Ansible Galaxy collection

Run this command from the repository root:

```bash
ansible-galaxy collection install -r ansible/collections/requirements.yml
```

Expected output:

```
Starting galaxy collection install process
Process install dependency map
Starting collection install process
Downloading https://galaxy.ansible.com/...
Installing 'cisco.meraki:2.18.x' to '~/.ansible/collections/...'
cisco.meraki:2.18.x was installed successfully
```

If the collection is already installed, Ansible reports:

```
Nothing to do. All requested collections are already installed.
```

To force a reinstall (e.g., to upgrade):

```bash
ansible-galaxy collection install -r ansible/collections/requirements.yml --force
```

Verify the installed version:

```bash
ansible-galaxy collection list cisco.meraki
```

### Step 3 — Add your Meraki API key

Edit `ansible/group_vars/all/vault.yml` and replace the placeholder:

```bash
# Open the file in your editor
nano ansible/group_vars/all/vault.yml
```

Change:

```yaml
meraki_api_key: "REPLACE_WITH_YOUR_MERAKI_API_KEY"
```

To your actual key:

```yaml
meraki_api_key: "your-actual-meraki-api-key-here"
```

**Then encrypt the file immediately** (see [Protecting Your API Key](#protecting-your-api-key-with-ansible-vault) below).

---

## Configuration

### `group_vars/all/vars.yml` — shared non-sensitive variables

| Variable | Default | Description |
|---|---|---|
| `meraki_base_url` | `https://api.meraki.com/api/v1` | Meraki Dashboard API base URL. Override only if you are behind a proxy or using a private cloud instance. |

### `group_vars/all/vault.yml` — sensitive variables (must be vault-encrypted)

| Variable | Description |
|---|---|
| `meraki_api_key` | Your Meraki Dashboard API key. Generated in *Organization → Settings → Dashboard API access*. |

### Environment variable alternative

If you prefer not to store the key in a file at all, export it as an environment
variable before running any playbook. The `cisco.meraki` collection checks this
variable automatically:

```bash
export MERAKI_API_KEY="your-actual-meraki-api-key-here"
```

When the environment variable is set, you can omit `meraki_api_key` from
`vault.yml` entirely and skip vault encryption.

---

## Protecting Your API Key with Ansible Vault

**Your Meraki API key grants full read/write access to your organization.**
Never commit it in plain text to any version control system.

### What is Ansible Vault?

Ansible Vault is a built-in feature that encrypts files (or individual
variables) so they can be safely committed to Git. When you run a playbook,
Ansible decrypts them in memory — your key never touches the disk unencrypted
at runtime.

### Setup

#### 1. Create a vault password file

This file holds the password used to encrypt/decrypt your vault. It should
exist only on machines that need to run these playbooks.

```bash
# Create the file with a strong password
echo "your-strong-vault-password-here" > .vault_pass

# Restrict read access to your user only
chmod 600 .vault_pass
```

> Add `.vault_pass` to `.gitignore` so it is never committed:
> ```bash
> echo ".vault_pass" >> .gitignore
> ```

#### 2. Encrypt vault.yml

```bash
ansible-vault encrypt ansible/group_vars/all/vault.yml \
  --vault-password-file .vault_pass
```

The file now looks like this (safe to commit):

```
$ANSIBLE_VAULT;1.1;AES256
61333930346430363938...
```

#### 3. Edit the vault later

```bash
ansible-vault edit ansible/group_vars/all/vault.yml \
  --vault-password-file .vault_pass
```

#### 4. Decrypt temporarily (only if necessary)

```bash
ansible-vault decrypt ansible/group_vars/all/vault.yml \
  --vault-password-file .vault_pass
# Re-encrypt when done!
ansible-vault encrypt ansible/group_vars/all/vault.yml \
  --vault-password-file .vault_pass
```

---

## Running the Playbooks

All commands below assume you are in the repository root
(`ansible-meraki-firmware-upgrade/`).

### Playbook 1.0 — Check available firmware

```bash
ansible-playbook ansible/1.0_check_available_firmware_by_tag.yml \
  -i ansible/inventory/meraki.yml \
  -e '{"network_tags": ["Cisco-Lab"]}' \
  --vault-password-file .vault_pass
```

Target networks matching **any** of multiple tags:

```bash
ansible-playbook ansible/1.0_check_available_firmware_by_tag.yml \
  -i ansible/inventory/meraki.yml \
  -e '{"network_tags": ["branch", "remote-office"]}' \
  --vault-password-file .vault_pass
```

> **Important:** Always pass `network_tags` using JSON dict format (`-e '{"network_tags": [...]}'`).
> The `key=value` shorthand (`-e 'network_tags=[...]'`) treats the value as a plain string
> and the tag filter will not match anything.

### Playbook 2.0 — Schedule firmware upgrade

**Upgrade wireless (MR) only, scheduled for a specific time:**

```bash
ansible-playbook ansible/2.0_schedule_firmware_upgrade_by_tag.yml \
  -i ansible/inventory/meraki.yml \
  -e '{"network_tags": ["Cisco-Lab"]}' \
  -e 'wireless_version_id=15763' \
  -e 'upgrade_datetime=2026-07-01T02:00:00Z' \
  --vault-password-file .vault_pass
```

**Upgrade all three device types at once:**

```bash
ansible-playbook ansible/2.0_schedule_firmware_upgrade_by_tag.yml \
  -i ansible/inventory/meraki.yml \
  -e '{"network_tags": ["Cisco-Lab"]}' \
  -e 'appliance_version_id=15806' \
  -e 'switch_version_id=6016' \
  -e 'wireless_version_id=15763' \
  -e 'upgrade_datetime=2026-07-01T02:00:00Z' \
  --vault-password-file .vault_pass
```

**Schedule at next maintenance window** (omit `upgrade_datetime` — Meraki uses the network's configured maintenance window):

```bash
ansible-playbook ansible/2.0_schedule_firmware_upgrade_by_tag.yml \
  -i ansible/inventory/meraki.yml \
  -e '{"network_tags": ["Cisco-Lab"]}' \
  -e 'wireless_version_id=15763' \
  --vault-password-file .vault_pass
```

> **`upgrade_datetime` behaviour:**
> - **Empty / omitted** → Meraki queues the upgrade at the next scheduled maintenance window configured on the network (*Network-wide → General → Scheduled upgrades*). If no maintenance window is configured, Meraki runs the upgrade during the next low-traffic period it detects — behaviour varies by network type.
> - **Explicit UTC timestamp** (e.g. `2026-07-01T02:00:00Z`) → Meraki schedules the upgrade for that exact time, regardless of any configured maintenance window.

**Dry run — show the upgrade plan without making any changes:**

```bash
ansible-playbook ansible/2.0_schedule_firmware_upgrade_by_tag.yml \
  -i ansible/inventory/meraki.yml \
  -e '{"network_tags": ["Cisco-Lab"]}' \
  -e 'wireless_version_id=15763' \
  --vault-password-file .vault_pass \
  --check
```

### Using an environment variable instead of vault

If you exported `MERAKI_API_KEY`, omit `--vault-password-file`:

```bash
export MERAKI_API_KEY="your-key-here"

ansible-playbook ansible/1.0_check_available_firmware_by_tag.yml \
  -i ansible/inventory/meraki.yml \
  -e '{"network_tags": ["Cisco-Lab"]}'
```

---

## Variable Reference

### Playbook 1.0

| Variable | Required | Type | Description |
|---|---|---|---|
| `network_tags` | **Yes** | List of strings | Tag names to match. Networks carrying **any** of these tags are included. Case-sensitive. Example: `["branch","lab"]` |

### Playbook 2.0

| Variable | Required | Type | Description |
|---|---|---|---|
| `network_tags` | **Yes** | List of strings | Same as 1.0 — selects networks by tag. |
| `appliance_version_id` | No* | String | Target firmware version ID for MX appliances. Obtain from playbook 1.0 output. |
| `switch_version_id` | No* | String | Target firmware version ID for MS switches. Obtain from playbook 1.0 output. |
| `wireless_version_id` | No* | String | Target firmware version ID for MR access points. Obtain from playbook 1.0 output. |
| `upgrade_datetime` | No | String | ISO 8601 UTC timestamp, e.g. `2026-07-01T02:00:00Z`. When omitted, Meraki schedules the upgrade at the next configured maintenance window (*Network-wide → General → Scheduled upgrades*). If no maintenance window exists, Meraki chooses the next low-traffic period. |

> *At least one of `appliance_version_id`, `switch_version_id`, or
> `wireless_version_id` must be provided. Device types not specified are never
> touched.

### Shared variables (group_vars)

| Variable | File | Description |
|---|---|---|
| `meraki_api_key` | `group_vars/all/vault.yml` | Meraki Dashboard API key (vault-encrypted). |
| `meraki_base_url` | `group_vars/all/vars.yml` | API base URL. Default: `https://api.meraki.com/api/v1`. |

---

## How Each Playbook Works — Step by Step

### Playbook 1.0 — Check Available Firmware

| Step | Task name | What happens |
|---|---|---|
| Pre-task | Validate network_tags | Fails immediately if `network_tags` is empty, with a helpful error message. |
| 1 | Get all Meraki organizations | Calls `GET /organizations`. Returns a list of every org the API key has access to. |
| 2 | Get all networks per org | Calls `GET /organizations/{id}/networks` once per org. Results are looped and flattened into a single list. |
| 3 | Filter networks by tag | Pure Ansible — no API call. Uses the `intersect` filter to find networks whose tag list overlaps with `network_tags`. |
| 4 | Get firmware info per network | Calls `GET /networks/{id}/firmwareUpgrades` for each tagged network. Returns current version and available versions for MX, MS, and MR. |
| 5 | Display firmware summary | Prints a formatted block per network: current version and all available versions with their IDs and release types (`stable`, `candidate`, etc.). |

### Playbook 2.0 — Schedule Firmware Upgrade

| Step | Task name | What happens |
|---|---|---|
| Pre-tasks | Validate inputs | Checks `network_tags` is non-empty, at least one version ID is provided, and `upgrade_datetime` matches ISO 8601 format (if given). |
| 1–3 | Org + network discovery | Identical to playbook 1.0 steps 1–3. |
| 4 | Get current firmware | Calls `GET /networks/{id}/firmwareUpgrades` to read the current running version per device type. Used for idempotency. |
| 5 | Evaluate upgrade requirements | For each network and each device type, compares `currentVersion.id` against the supplied target version ID. Builds a `products` dict containing **only** device types that actually need upgrading. Networks already on the target are marked as skipped. |
| 6 | Show upgrade plan | Prints the planned action for every network before touching anything. Each entry shows current version → target ID, or "SKIPPED". |
| 7 | Schedule upgrades | Calls `PUT /networks/{id}/firmwareUpgrades` only for networks where `any_upgrade_needed` is true. The `products` dict sent to the API contains only the device types that differ. |
| 8 | Summary | Prints a count of networks scheduled vs. skipped. |

#### Idempotency explained

The playbook is **safe to run multiple times**. On the first run it schedules
the upgrade. On subsequent runs it compares the current version ID against the
target — if they already match, it skips that network and device type entirely.
This means re-running the playbook after a successful upgrade does nothing.

---

## Expected Output

### Playbook 1.0 — sample output (one network)

```
TASK [Display firmware info for Branch-Office-01] ****
ok: [localhost] => {
    "msg": "═══════════════════════════════════════════════════════════════\nNetwork : Branch-Office-01\nOrg     : Acme Corp\nTags    : branch, lab\n───────────────────────────────────────────────────────────────\nAPPLIANCE (MX)\n  Current   : MX 18.211.5.2 (ID: 4625)\n  Available :\n    - MX 19.2.8 [stable]  →  ID: 15806\n    - MX 26.1.4 [candidate]  →  ID: 15572\n───────────────────────────────────────────────────────────────\nSWITCH (MS)\n  Current   : MS 15.21.1 (ID: 3101)\n  Available :\n    - MS 16.0.3 [stable]  →  ID: 6016\n───────────────────────────────────────────────────────────────\nWIRELESS (MR)\n  Current   : MR 30.7.1 (ID: 11440)\n  Available :\n    - MR 32.1.7 [stable]  →  ID: 15763\n    - MR 31.5.2 [stable]  →  ID: 13875\n═══════════════════════════════════════════════════════════════"
}
```

From this output you know:
- To upgrade MX to stable: use `appliance_version_id=15806`
- To upgrade MS to stable: use `switch_version_id=6016`
- To upgrade MR to stable: use `wireless_version_id=15763`

### Playbook 2.0 — upgrade plan output (before changes are applied)

```
TASK [Show upgrade plan for Branch-Office-01] ****
ok: [localhost] => {
    "msg": "Network : Branch-Office-01\nAction  : UPGRADE WILL BE SCHEDULED\n  Wireless  : MR 30.7.1 (ID: 11440) → target ID: 15763\nScheduled : 2026-07-01T02:00:00Z"
}
```

### Playbook 2.0 — summary

```
TASK [Upgrade scheduling summary] ****
ok: [localhost] => {
    "msg": "═══════════════════════════════════════════════════════════════\nSUMMARY\n═══════════════════════════════════════════════════════════════\nTotal networks matched     : 3\nUpgrades scheduled         : 2\nSkipped (already on target): 1\n═══════════════════════════════════════════════════════════════"
}
```

---

## Playbook Ordering and Workflow

```
┌──────────────────────────────────────────────────┐
│  1.0_check_available_firmware_by_tag.yml          │
│                                                  │
│  READ ONLY — no changes made to Meraki           │
│  Output: version IDs per device type             │
└───────────────────────┬──────────────────────────┘
                        │
                        │  Copy version IDs from output
                        ▼
┌──────────────────────────────────────────────────┐
│  2.0_schedule_firmware_upgrade_by_tag.yml         │
│                                                  │
│  WRITES — schedules upgrades via API             │
│  Idempotent: safe to re-run                      │
└──────────────────────────────────────────────────┘
```

---

## Troubleshooting

| Symptom | Likely Cause | Resolution |
|---|---|---|
| `ERROR! A required module is missing: cisco.meraki` | Galaxy collection not installed | Run `ansible-galaxy collection install -r ansible/collections/requirements.yml` |
| `fatal: network_tags is empty` | `MERAKI_NETWORK_TAGS` not set and no `-e` override | Check `.envrc` has `export MERAKI_NETWORK_TAGS=YourTag` and run `direnv allow` |
| `No networks found matching tags [...]` | Tag does not exist, is misspelled, or `network_tags` was passed as a string | Tags are case-sensitive. Use JSON dict format: `-e '{"network_tags": ["MyTag"]}'`. The `key=value` form passes a plain string, not a list. |
| `ERROR! Attempting to decrypt but no vault secrets found` | `--vault-password-file` missing | Add `--vault-password-file .vault_pass` to the command |
| `Authentication error (401)` | API key is invalid or expired | Regenerate the key in Meraki Dashboard → Organization → Settings → API access. Update and re-encrypt `vault.yml`. |
| `403 Forbidden` on a specific organization | API key does not have access to that org | Check organization-level API key permissions in the Meraki Dashboard. |
| `upgrade_datetime must be ISO 8601 UTC format` | Datetime string uses wrong format | Use format `YYYY-MM-DDTHH:MM:SSZ`, e.g. `2026-07-01T02:00:00Z` |
| All networks show "SKIPPED" in 2.0 | Networks are already on the target version | This is correct behaviour — no action needed. Run 1.0 to confirm current version. |
| `At least one version ID must be provided` | No `*_version_id` variable passed | Add at least one of `-e 'appliance_version_id=...'`, `-e 'switch_version_id=...'`, or `-e 'wireless_version_id=...'` |
| `cisco.meraki` module not found after install | Two Python environments; collection installed in wrong one | Run `ansible-galaxy collection list cisco.meraki` to verify path, then check `ansible --version` for the Python interpreter in use. |
