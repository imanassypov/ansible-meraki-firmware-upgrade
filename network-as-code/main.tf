# =============================================================================
# Network as Code — Meraki SSID + PSK Management
# =============================================================================
#
# HOW THIS WORKS (start here if you are new to this methodology)
# --------------------------------------------------------------
# "Network as Code" means your network configuration lives in YAML files
# (the data model) rather than in manual Dashboard clicks. Terraform is the
# engine that reads those YAML files and calls the Meraki API to make the
# network match what the files declare.
#
# The execution chain on every `terraform apply`:
#
#   terraform apply
#     │
#     ├─ reads this file (main.tf)
#     │
#     ├─ executes module "meraki" { source = "netascode/nac-meraki/meraki" }
#     │    └─ "source" is a Terraform Registry address. `terraform init`
#     │       downloaded this module to .terraform/modules/meraki/.
#     │       That downloaded code is what actually runs.
#     │
#     ├─ the module reads every *.yaml file under yaml_directories:
#     │    - ../data-model/nac/    ← your network intent (SSIDs, PSKs, tags)
#     │    - defaults/             ← org-wide defaults merged into every object
#     │
#     │  NOTE: ../data-model/firmware/ is NOT in yaml_directories — Terraform
#     │  never reads firmware_targets.yaml. That file belongs to Ansible/Python.
#     │  Directory isolation is the mechanism that keeps the two data models
#     │  from interfering with each other.
#     │
#     ├─ the module merges defaults + data model into a single config tree,
#     │  written to .merged_defaults.yaml for inspection (gitignored)
#     │
#     └─ the module calls the CiscoDevNet/meraki Terraform provider, which
#        translates the config tree into Meraki Dashboard API calls
#        (GET to read current state, PUT/POST only where a diff exists)
#
# PSK SECURITY
# ------------
# PSK values use the !env YAML tag in the data model — they are never stored
# in any file. At runtime the module substitutes each !env reference with the
# corresponding environment variable value before making any API calls.
#
# LOCAL DEVELOPMENT — direnv (.envrc)
# ------------------------------------
# The recommended local workflow uses direnv. Copy .envrc.example to .envrc,
# populate the values, then run `direnv allow`. direnv automatically injects
# all env vars whenever you cd into this directory — no manual export needed.
#
#   cp .envrc.example .envrc   # populate MERAKI_API_KEY, PSKs, etc.
#   direnv allow
#   terraform plan
#
# CI/CD — HashiCorp Vault + envconsul
# ------------------------------------
# In CI/CD pipelines there is no shell session to source .envrc from.
# Use vault-envconsul.sh to pull PSK values from Vault at runtime so they
# never appear in pipeline config or logs.
#
#   export VAULT_ADDR=https://vault.example.com
#   export VAULT_TOKEN=<token>
#   export MERAKI_API_KEY=<key>
#   ./vault-envconsul.sh terraform plan
# =============================================================================

module "meraki" {
  # Registry address: registry.terraform.io/netascode/nac-meraki/meraki
  # `terraform init` downloads this module to .terraform/modules/meraki/.
  # The downloaded code is the NaC engine — do not edit it directly.
  source = "netascode/nac-meraki/meraki"

  # Directories the module scans for *.yaml data model files.
  # Add more directories here if you split the data model across folders.
  # Do NOT include data-model/firmware/ — that directory is for Ansible/Python only.
  yaml_directories = ["../data-model/nac"]

  # Writes the fully-merged defaults+data model to disk after each plan/apply.
  # Useful for debugging — inspect this file to see exactly what Terraform sent
  # to the API. This file is gitignored.
  write_model_file = ".merged_defaults.yaml"
}
