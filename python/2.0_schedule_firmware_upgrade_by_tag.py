#!/usr/bin/env python3
"""
2.0_schedule_firmware_upgrade_by_tag.py
-----------------------------------------
Reads data-model/firmware_targets.yml to determine the desired firmware end state,
then enforces compliance across all matching networks.

For each policy in the data model the script:
  1. Finds all networks in the specified organization whose tags include
     the policy's network_tag.
  2. Compares the current firmware version of each device family against
     the policy target.
  3. Schedules an upgrade only where current != target (idempotent).
     Device families with an empty target version_id are never touched.

Authentication
--------------
    export MERAKI_DASHBOARD_API_KEY=your-api-key-here

Optional overrides
------------------
    export MERAKI_BASE_URL=https://api.meraki.com/api/v1   # default

Usage
-----
    # Normal run — evaluate and schedule:
    python python/2.0_schedule_firmware_upgrade_by_tag.py

    # Dry run — show compliance plan without making any changes:
    python python/2.0_schedule_firmware_upgrade_by_tag.py --check

    # Use an alternate targets file:
    python python/2.0_schedule_firmware_upgrade_by_tag.py --targets-file /path/to/targets.yml

Prerequisites
-------------
    Run 1.0_check_available_firmware_by_tag.py to discover version IDs,
    then populate data-model/firmware_targets.yml with the desired targets.

Mirrors: ansible/2.0_schedule_firmware_upgrade_by_tag.yml
"""

import argparse
import os
import re
import sys
from pathlib import Path

import meraki
import yaml


SEPARATOR  = "═" * 65
SUB_SEP    = "─" * 65

# Default path: ../data-model/firmware_targets.yml relative to this script.
DEFAULT_TARGETS_FILE = (
    Path(__file__).resolve().parent.parent / "data-model" / "firmware_targets.yml"
)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Schedule Meraki firmware upgrades based on the firmware_targets.yml "
            "data model."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Dry run — evaluate compliance and display the plan without making "
            "any API changes."
        ),
    )
    parser.add_argument(
        "--targets-file",
        metavar="PATH",
        default=str(DEFAULT_TARGETS_FILE),
        help=f"Path to firmware_targets.yml (default: {DEFAULT_TARGETS_FILE})",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Pre-task: load and validate the data model
# ---------------------------------------------------------------------------

def load_policies(targets_file: str) -> list[dict]:
    path = Path(targets_file)
    if not path.exists():
        sys.exit(f"ERROR: Targets file not found: {path}")
    with open(path) as fh:
        data = yaml.safe_load(fh)
    policies: list[dict] = (data or {}).get("firmware_policies") or []
    if not policies:
        sys.exit(
            f"ERROR: No firmware_policies found in {path}.\n"
            "Add at least one policy entry and re-run."
        )
    return policies


ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def validate_policies(policies: list[dict]) -> None:
    """Mirrors the three ansible.builtin.assert blocks in the playbook pre_tasks."""
    for policy in policies:
        desc   = policy.get("description", "(unnamed)")
        target = policy.get("target") or {}
        ap = str(target.get("appliance_version_id") or "").strip()
        sw = str(target.get("switch_version_id") or "").strip()
        wr = str(target.get("wireless_version_id") or "").strip()

        if not (ap or sw or wr):
            sys.exit(
                f'ERROR: Policy "{desc}" has no target version IDs set.\n'
                "Set at least one of appliance_version_id, switch_version_id, "
                f"or wireless_version_id in the targets file."
            )

        dt = str(policy.get("upgrade_datetime") or "").strip()
        if dt and not ISO8601_RE.match(dt):
            sys.exit(
                f'ERROR: Policy "{desc}": upgrade_datetime must be ISO 8601 UTC '
                f'format YYYY-MM-DDTHH:MM:SSZ (got "{dt}").'
            )


# ---------------------------------------------------------------------------
# Dashboard client
# ---------------------------------------------------------------------------

def get_dashboard() -> meraki.DashboardAPI:
    api_key = os.environ.get("MERAKI_DASHBOARD_API_KEY", "")
    if not api_key:
        sys.exit("ERROR: MERAKI_DASHBOARD_API_KEY environment variable is not set.")
    base_url = os.environ.get("MERAKI_BASE_URL", "https://api.meraki.com/api/v1")
    return meraki.DashboardAPI(
        api_key=api_key,
        base_url=base_url,
        suppress_logging=True,
        print_console=False,
    )


# ---------------------------------------------------------------------------
# Step 1 — Discover organizations and networks
# ---------------------------------------------------------------------------

def get_all_orgs_and_networks(
    dashboard: meraki.DashboardAPI,
) -> tuple[list[dict], list[dict]]:
    orgs = dashboard.organizations.getOrganizations()
    print(f"\nFound {len(orgs)} organization(s): {[o['name'] for o in orgs]}")

    all_networks: list[dict] = []
    for org in orgs:
        try:
            nets = dashboard.organizations.getOrganizationNetworks(org["id"])
            all_networks.extend(nets)
        except meraki.exceptions.APIError as exc:
            print(f"  WARNING: Could not fetch networks for org '{org['name']}': {exc}")

    return orgs, all_networks


# ---------------------------------------------------------------------------
# Step 2 — Build N:M network-policy work list
# ---------------------------------------------------------------------------

def build_work_list(
    all_networks: list[dict],
    org_by_id: dict[str, str],
    policies: list[dict],
) -> list[dict]:
    """
    Return a flat list of every (network, policy) pair where:
      - the network's tag list contains the policy's network_tag  (case-sensitive), AND
      - the network belongs to the policy's organization  (or policy.organization == 'all')

    One network can match multiple policies; one policy can match multiple networks.
    The result is processed independently per pair, identical to the Ansible work_list.
    """
    work_list: list[dict] = []
    for network in all_networks:
        org_name  = org_by_id.get(network["organizationId"], "")
        net_tags  = set(network.get("tags") or [])
        for policy in policies:
            if policy["network_tag"] in net_tags:
                pol_org = str(policy.get("organization") or "all").strip()
                if pol_org == "all" or pol_org == org_name:
                    work_list.append(
                        {
                            "networkId":   network["id"],
                            "networkName": network["name"],
                            "orgName":     org_name,
                            "policy":      policy,
                        }
                    )
    return work_list


# ---------------------------------------------------------------------------
# Step 3 — Evaluate compliance for each network-policy pair
# ---------------------------------------------------------------------------

def evaluate_compliance(item: dict, fw_info: dict) -> dict:
    """
    Compare current installed version vs target for each device family.
    Builds the products payload required by updateNetworkFirmwareUpgrades.
    Device families whose target is empty are never flagged.
    """
    products = fw_info.get("products") or {}
    policy   = item["policy"]
    target   = policy.get("target") or {}

    upgrade_dt       = str(policy.get("upgrade_datetime") or "").strip()
    upgrade_strategy = str(policy.get("upgrade_strategy") or "").strip()

    target_ap = str(target.get("appliance_version_id") or "").strip()
    target_sw = str(target.get("switch_version_id") or "").strip()
    target_wr = str(target.get("wireless_version_id") or "").strip()

    def _current(family: str) -> tuple[str, str]:
        """Return (current_id, current_shortName) for a device family."""
        cv = (products.get(family) or {}).get("currentVersion") or {}
        return str(cv.get("id") or ""), cv.get("shortName") or "N/A"

    cur_ap_id, cur_ap_name = _current("appliance")
    cur_sw_id, cur_sw_name = _current("switch")
    cur_wr_id, cur_wr_name = _current("wireless")

    upg_ap = bool(target_ap and cur_ap_id != target_ap)
    upg_sw = bool(target_sw and cur_sw_id != target_sw)
    upg_wr = bool(target_wr and cur_wr_id != target_wr)

    def _product_entry(version_id: str) -> dict:
        entry: dict = {"nextUpgrade": {"toVersion": {"id": version_id}}}
        if upgrade_dt:
            entry["nextUpgrade"]["time"] = upgrade_dt
        if upgrade_strategy:
            entry["upgradeStrategy"] = upgrade_strategy
        return entry

    products_payload: dict = {}
    if upg_ap:
        products_payload["appliance"] = _product_entry(target_ap)
    if upg_sw:
        products_payload["switch"] = _product_entry(target_sw)
    if upg_wr:
        products_payload["wireless"] = _product_entry(target_wr)

    return {
        "networkId":           item["networkId"],
        "networkName":         item["networkName"],
        "orgName":             item["orgName"],
        "policyDescription":   policy.get("description", ""),
        "any_upgrade_needed":  upg_ap or upg_sw or upg_wr,
        "needs_appliance":     upg_ap,
        "needs_switch":        upg_sw,
        "needs_wireless":      upg_wr,
        "products_payload":    products_payload,
        "current_appliance":   f"{cur_ap_name} (ID: {cur_ap_id})",
        "current_switch":      f"{cur_sw_name} (ID: {cur_sw_id})",
        "current_wireless":    f"{cur_wr_name} (ID: {cur_wr_id})",
        "target_appliance_id": target_ap,
        "target_switch_id":    target_sw,
        "target_wireless_id":  target_wr,
        "upgrade_datetime":    upgrade_dt,
        "upgrade_strategy":    upgrade_strategy,
    }


# ---------------------------------------------------------------------------
# Step 4 — Display compliance plan
# ---------------------------------------------------------------------------

def print_compliance_entry(entry: dict, dry_run: bool) -> None:
    print(SUB_SEP)
    print(f"Network : {entry['networkName']}")
    print(f"Org     : {entry['orgName']}")
    print(f"Policy  : {entry['policyDescription']}")
    if entry["any_upgrade_needed"]:
        tag = " (--check / no changes made)" if dry_run else ""
        print(f"Action  : UPGRADE WILL BE SCHEDULED{tag}")
        if entry["needs_appliance"]:
            print(
                f"  Appliance : {entry['current_appliance']}"
                f"  →  target ID: {entry['target_appliance_id']}"
            )
        if entry["needs_switch"]:
            print(
                f"  Switch    : {entry['current_switch']}"
                f"  →  target ID: {entry['target_switch_id']}"
            )
        if entry["needs_wireless"]:
            print(
                f"  Wireless  : {entry['current_wireless']}"
                f"  →  target ID: {entry['target_wireless_id']}"
            )
        sched = entry["upgrade_datetime"] or "Next maintenance window (upgrade_datetime not set)"
        strat = entry["upgrade_strategy"] or "minimizeClientDowntime (Meraki default)"
        print(f"  Scheduled : {sched}")
        print(f"  Strategy  : {strat}")
    else:
        print("Action  : COMPLIANT — already on target version for all specified device types")


# ---------------------------------------------------------------------------
# Step 5 — Schedule upgrades
# ---------------------------------------------------------------------------

def schedule_upgrades(
    compliance_list: list[dict],
    dashboard: meraki.DashboardAPI,
) -> int:
    """Submit upgrade requests for non-compliant networks. Returns count scheduled."""
    scheduled = 0
    for entry in compliance_list:
        if entry["any_upgrade_needed"]:
            try:
                dashboard.networks.updateNetworkFirmwareUpgrades(
                    entry["networkId"],
                    products=entry["products_payload"],
                )
                scheduled += 1
                print(
                    f"  ✓ Scheduled: {entry['networkName']}"
                    f" — {entry['policyDescription']}"
                )
            except meraki.exceptions.APIError as exc:
                print(
                    f"  ✗ ERROR scheduling '{entry['networkName']}'"
                    f" — {entry['policyDescription']}: {exc}"
                )
    return scheduled


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args    = parse_args()
    dry_run = args.check

    if dry_run:
        print("\n[DRY RUN] --check mode active: no API changes will be made.\n")

    # --- pre-tasks: load and validate data model ---
    policies = load_policies(args.targets_file)
    validate_policies(policies)

    print(f"Loaded {len(policies)} firmware policy/policies:")
    for p in policies:
        print(f"  - {p.get('description', '(unnamed)')}")

    dashboard = get_dashboard()

    # --- Step 1: discover orgs and all networks ---
    orgs, all_networks = get_all_orgs_and_networks(dashboard)
    org_by_id = {o["id"]: o["name"] for o in orgs}

    # --- Step 2: build N:M work list ---
    work_list = build_work_list(all_networks, org_by_id, policies)
    if not work_list:
        sys.exit(
            "\nERROR: No networks matched any policy.\n"
            "Check that organization names and network tags are correct "
            "(both are case-sensitive)."
        )
    print(f"\n{len(work_list)} network-policy match(es) found:")
    print(f"  {[w['networkName'] for w in work_list]}")

    # --- Step 3: fetch current firmware for each unique network ---
    fw_by_network: dict[str, dict] = {}
    for net_id in {w["networkId"] for w in work_list}:
        try:
            fw_by_network[net_id] = dashboard.networks.getNetworkFirmwareUpgrades(net_id)
        except meraki.exceptions.APIError as exc:
            print(f"  WARNING: Could not fetch firmware for network {net_id}: {exc}")

    # --- Step 4: evaluate compliance per pair ---
    compliance_list = [
        evaluate_compliance(item, fw_by_network[item["networkId"]])
        for item in work_list
        if item["networkId"] in fw_by_network
    ]

    # --- Step 5: display compliance plan ---
    print(f"\n{SEPARATOR}")
    print("COMPLIANCE PLAN")
    for entry in compliance_list:
        print_compliance_entry(entry, dry_run)
    print(SUB_SEP)

    # --- Step 6: schedule upgrades (skipped in dry-run mode) ---
    if not dry_run:
        print()
        schedule_upgrades(compliance_list, dashboard)

    # --- Step 7: summary ---
    upgrades_needed = sum(1 for e in compliance_list if e["any_upgrade_needed"])
    compliant       = sum(1 for e in compliance_list if not e["any_upgrade_needed"])

    print(f"\n{SEPARATOR}")
    print("COMPLIANCE SUMMARY")
    print(SEPARATOR)
    print(f"  Policies evaluated    : {len(policies)}")
    print(f"  Network-policy pairs  : {len(compliance_list)}")
    if dry_run:
        print(f"  Would upgrade         : {upgrades_needed}")
    else:
        print(f"  Upgrades scheduled    : {upgrades_needed}")
    print(f"  Already compliant     : {compliant}")
    print(SEPARATOR)


if __name__ == "__main__":
    main()
