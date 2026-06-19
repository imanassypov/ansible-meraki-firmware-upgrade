terraform {
  required_version = ">= 1.8.0"

  required_providers {
    meraki = {
      # Provider moved from cisco-open/meraki to CiscoDevNet/meraki as of v1.12+
      source  = "CiscoDevNet/meraki"
      version = "~> 1.12"
    }
  }
}

provider "meraki" {
  # API key is read from the MERAKI_API_KEY environment variable.
  # Set this in .envrc (local dev) or inject via Vault/envconsul (CI/CD).
  # See .envrc.example for the full list of required variables.
}
