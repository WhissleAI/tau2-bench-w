"""Emit Whissle custom HTTP-tool specs for the appliance_care agent tools.

Each spec binds one tau2 agent tool to ``POST {bridge}/tools/{name}`` on the
public tau2 bridge. The bearer token is NOT written into the spec: it lives in a
stored Whissle connector, referenced by ``credential_id``, so nothing secret ever
lands in a file, a commit, or an API payload body.

Usage:
    uv run python -m tau2.scripts.gen_whissle_tool_specs \
        --out /path/to/tools --credential-id <connector-id>

The bridge URL comes from ``TAU_BRIDGE_PUBLIC_URL`` unless ``--bridge-url`` is
given. Nothing here contacts Whissle; it only writes files.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from tau2.bridge.tool_bridge import BRIDGE_URL_ENV
from tau2.domains.appliance_care.environment import get_environment

# The exact 16 agent-side tools. Listed explicitly rather than derived so that a
# change to the domain cannot silently widen what gets published to Whissle.
EXPECTED_TOOLS = [
    "search_manuals",
    "open_manual_section",
    "lookup_error_code",
    "get_customer_by_phone",
    "get_customer_by_name",
    "list_owned_appliances",
    "get_appliance_details",
    "identify_model",
    "get_model_details",
    "check_warranty",
    "get_service_history",
    "create_support_case",
    "escalate_safety_issue",
    "schedule_service",
    "record_resolution",
    "transfer_to_human_agents",
]


def build_specs(bridge_url: str, credential_id: str | None) -> list[dict]:
    """One Whissle custom HTTP-tool spec per tau2 agent tool."""
    env = get_environment()
    by_name = {}
    for tool in env.get_tools():
        schema = tool.openai_schema
        fn = schema.get("function", schema)
        by_name[fn["name"]] = fn

    missing = [name for name in EXPECTED_TOOLS if name not in by_name]
    if missing:
        raise SystemExit(f"domain is missing expected tool(s): {', '.join(missing)}")
    extra = [name for name in by_name if name not in EXPECTED_TOOLS]
    if extra:
        raise SystemExit(
            f"domain exposes unexpected agent tool(s): {', '.join(sorted(extra))}. "
            "Publishing them was not approved; update EXPECTED_TOOLS deliberately."
        )

    base = bridge_url.rstrip("/")
    specs = []
    for name in EXPECTED_TOOLS:
        fn = by_name[name]
        specs.append(
            {
                "name": name,
                "kind": "http",
                "description": (fn.get("description") or "").strip(),
                "parameters": fn.get("parameters")
                or {"type": "object", "properties": {}},
                "binding": {
                    "url": f"{base}/tools/{name}",
                    "method": "POST",
                },
                # The bearer token is supplied by the stored connector, never here.
                "credential_id": credential_id,
            }
        )
    return specs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="directory to write specs into")
    parser.add_argument(
        "--bridge-url",
        default=os.getenv(BRIDGE_URL_ENV),
        help=f"public HTTPS base URL of the tau2 bridge (default: ${BRIDGE_URL_ENV})",
    )
    parser.add_argument(
        "--credential-id",
        default=None,
        help="Whissle connector id holding the bridge bearer token",
    )
    args = parser.parse_args()

    if not args.bridge_url:
        raise SystemExit(
            f"a bridge URL is required: pass --bridge-url or set {BRIDGE_URL_ENV}"
        )
    if not args.bridge_url.startswith("https://"):
        raise SystemExit(
            "the bridge URL must be https:// — Whissle calls it over the public "
            "internet with a bearer token attached"
        )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = build_specs(args.bridge_url, args.credential_id)
    for spec in specs:
        path = out_dir / f"{spec['name']}.json"
        path.write_text(json.dumps(spec, indent=2) + "\n")
    print(f"wrote {len(specs)} tool spec(s) to {out_dir}")
    if not args.credential_id:
        print(
            "WARNING: no --credential-id given. Create a bearer connector first, "
            "then regenerate; otherwise the tools will call the bridge "
            "unauthenticated and every request will 401."
        )


if __name__ == "__main__":
    main()
