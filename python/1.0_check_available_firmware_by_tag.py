#!/usr/bin/env python3
"""
1.0_check_available_firmware_by_tag.py
---------------------------------------
Discovers all Meraki networks matching the given tags across every organization
the API key has access to, then displays current and available firmware versions
(ID + shortName) for Appliance (MX), Switch (MS), and Wireless (MR).

The Version IDs shown in the output are the exact values to copy into
data-model/firmware/firmware_targets.yaml as appliance_version_id, switch_version_id,
and wireless_version_id when running script 2.0.

Authentication
--------------
    export MERAKI_API_KEY=your-api-key-here

Optional overrides
------------------
    export MERAKI_BASE_URL=https://api.meraki.com/api/v1   # default

Usage
-----
    # Auto-derive tags from data-model/firmware/firmware_targets.yaml (recommended):
    python python/1.0_check_available_firmware_by_tag.py

    # Override tags explicitly on the command line:
    python python/1.0_check_available_firmware_by_tag.py --tags Cisco-Lab
    python python/1.0_check_available_firmware_by_tag.py --tags Cisco-Lab branch

    # Fallback: via environment variable (comma-separated):
    MERAKI_NETWORK_TAGS=Cisco-Lab python python/1.0_check_available_firmware_by_tag.py

Tag resolution priority:
    1. --tags CLI flag
    2. network_tag fields in data-model/firmware/firmware_targets.yaml  (auto, no flag needed)
    3. MERAKI_NETWORK_TAGS environment variable

Mirrors: ansible/1.0_check_available_firmware_by_tag.yml
"""

import argparse
import os
import sys
from pathlib import Path

import meraki
import yaml


SEPARATOR = "═" * 65
SUB_SEP   = "─" * 65

DEFAULT_TARGETS_FILE = (
    Path(__file__).resolve().parent.parent / "data-model" / "firmware" / "firmware_targets.yaml"
)


# ---------------------------------------------------------------------------
# CLI and environment
# ---------------------------------------------------------------------------

def _tags_from_data_model(targets_file: Path) -> list[str]:
    """Return unique tags from meraki.domains[].organizations[].networks[].tags."""
    if not targets_file.exists():
        return []
    with open(targets_file) as fh:
        data = yaml.safe_load(fh)
    seen: list[str] = []
    for domain in (data or {}).get("meraki", {}).get("domains") or []:
        for org in domain.get("organizations") or []:
            for network in org.get("networks") or []:
                for tag in network.get("tags") or []:
                    tag = str(tag).strip()
                    if tag and tag not in seen:
                        seen.append(tag)
    return seen


def parse_args() -> list[str]:
    parser = argparse.ArgumentParser(
        description=(
            "Check available Meraki firmware versions for networks matching given tags."
        )
    )
    parser.add_argument(
        "--tags",
        nargs="+",
        metavar="TAG",
        help=(
            "One or more network tags to filter by (case-sensitive). "
            "When omitted, tags are read from data-model/firmware/firmware_targets.yaml, "
            "then from MERAKI_NETWORK_TAGS env var as a final fallback."
        ),
    )
    parser.add_argument(
        "--targets-file",
        metavar="PATH",
        default=str(DEFAULT_TARGETS_FILE),
        help=f"Path to firmware_targets.yaml (default: {DEFAULT_TARGETS_FILE})",
    )
    args = parser.parse_args()

    # Priority 1: explicit --tags flag
    tags: list[str] = args.tags or []

    # Priority 2: data model network_tag fields
    if not tags:
        tags = _tags_from_data_model(Path(args.targets_file))
        if tags:
            print(f"  (tags derived from {args.targets_file}: {tags})")

    # Priority 3: MERAKI_NETWORK_TAGS env var
    if not tags:
        env = os.environ.get("MERAKI_NETWORK_TAGS", "")
        tags = [t.strip() for t in env.split(",") if t.strip()]

    if not tags:
        parser.error(
            "No network tags provided. Options:\n"
            "  1. Use --tags TAG [TAG ...]\n"
            "  2. Add networks with tags to data-model/firmware/firmware_targets.yaml\n"
            "  3. Set MERAKI_NETWORK_TAGS=tag1,tag2 as an environment variable."
        )
    return tags


def get_dashboard() -> meraki.DashboardAPI:
    api_key = os.environ.get("MERAKI_API_KEY", "")
    if not api_key:
        sys.exit("ERROR: MERAKI_API_KEY environment variable is not set.")
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
    """Return (orgs, flat_networks) across all organizations the key can access."""
    orgs = dashboard.organizations.getOrganizations()
    print(f"\nFound {len(orgs)} organization(s): {[o['name'] for o in orgs]}")

    all_networks: list[dict] = []
    for org in orgs:
        try:
            nets = dashboard.organizations.getOrganizationNetworks(org["id"])
            all_networks.extend(nets)
        except meraki.exceptions.APIError as exc:
            print(f"  WARNING: could not fetch networks for org '{org['name']}': {exc}")

    print(f"Discovered {len(all_networks)} total network(s) across all organizations.")
    return orgs, all_networks


# ---------------------------------------------------------------------------
# Step 2 — Filter networks by tag
# ---------------------------------------------------------------------------

def filter_networks_by_tags(networks: list[dict], tags: list[str]) -> list[dict]:
    """Return networks whose tag list intersects the supplied tags (case-sensitive)."""
    tag_set = set(tags)
    return [n for n in networks if tag_set & set(n.get("tags") or [])]


# ---------------------------------------------------------------------------
# Step 3 — Query firmware and display results
# ---------------------------------------------------------------------------

def display_firmware_summary(
    network: dict,
    fw_info: dict,
    org_name: str,
) -> None:
    products = fw_info.get("products", {})
    print(SEPARATOR)
    print(f"Network : {network['name']}")
    print(f"Org     : {org_name}")
    print(f"Tags    : {', '.join(network.get('tags') or [])}")
    print(SUB_SEP)
    for family, label in [
        ("appliance", "APPLIANCE (MX)"),
        ("switch",    "SWITCH (MS)"),
        ("wireless",  "WIRELESS (MR)"),
    ]:
        pdata     = products.get(family, {})
        current   = pdata.get("currentVersion") or {}
        available = pdata.get("availableVersions") or []
        print(f"  {label}")
        print(
            f"    Current   : "
            f"{current.get('shortName', 'N/A')}  (ID: {current.get('id', 'N/A')})"
        )
        print("    Available :")
        if available:
            for v in available:
                print(
                    f"      - {v.get('shortName')}  [{v.get('releaseType', '?')}]"
                    f"  →  ID: {v.get('id')}"
                )
        else:
            print("      (none reported)")
        print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    tags = parse_args()
    print(f"\nSearching for networks with tags: {tags}")

    # --- pre-task: validate tags (mirrors ansible.builtin.assert in pre_tasks) ---
    # Already enforced by parse_args(); tags guaranteed non-empty at this point.

    dashboard = get_dashboard()

    # --- Step 1: discover orgs and networks ---
    orgs, all_networks = get_all_orgs_and_networks(dashboard)
    org_by_id = {o["id"]: o["name"] for o in orgs}

    # --- Step 2: filter networks by tag ---
    tagged_networks = filter_networks_by_tags(all_networks, tags)
    if not tagged_networks:
        sys.exit(
            f"\nERROR: No networks found matching tags {tags}.\n"
            "Tag matching is case-sensitive. Verify tags exist in the Meraki Dashboard."
        )
    print(f"\nFound {len(tagged_networks)} network(s) matching tags {tags}:")
    print(f"  {[n['name'] for n in tagged_networks]}\n")

    # --- Step 3: query firmware info and display ---
    for network in tagged_networks:
        try:
            fw_info = dashboard.networks.getNetworkFirmwareUpgrades(network["id"])
            display_firmware_summary(
                network, fw_info, org_by_id.get(network["organizationId"], "unknown")
            )
        except meraki.exceptions.APIError as exc:
            print(
                f"\nWARNING: Could not fetch firmware for network "
                f"'{network['name']}': {exc}"
            )

    print(SEPARATOR)
    print(
        "\nNext step: copy the Version IDs above into data-model/firmware/firmware_targets.yaml,\n"
        "then run:  python python/2.0_schedule_firmware_upgrade_by_tag.py"
    )


if __name__ == "__main__":
    main()
