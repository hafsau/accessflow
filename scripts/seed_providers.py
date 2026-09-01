#!/usr/bin/env python3
"""
Seed the provider directory in DynamoDB.

These are DEMO FIXTURES, not real accessibility vendors. The README must make
this clear. Do not let Devpost or demo materials imply the agent is contacting
real providers.

Usage:
    CORE_TABLE=accessflow-core python scripts/seed_providers.py
"""
from __future__ import annotations

import os
import sys

import boto3

# Add backend to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.models.domain import Provider

PROVIDERS = [
    Provider(
        provider_id="prov_pacific",
        name="Pacific Interpreting",
        service_types=["ASL_INTERPRETER", "CART"],
        jurisdictions=["seattle", "kingcounty"],
        approved=True,
        rating=4.8,
    ),
    Provider(
        provider_id="prov_signon",
        name="SignOn Services",
        service_types=["ASL_INTERPRETER"],
        jurisdictions=["seattle", "oakland", "sanjose"],
        approved=True,
        rating=4.2,
    ),
    Provider(
        provider_id="prov_linguava",
        name="Linguava Interpreters",
        service_types=["SPANISH_INTERPRETER", "OTHER_LANGUAGE"],
        jurisdictions=["seattle", "oakland", "alameda", "sanjose"],
        approved=True,
        rating=4.5,
    ),
    Provider(
        provider_id="prov_unapproved",
        name="QuickSign LLC",
        service_types=["ASL_INTERPRETER"],
        jurisdictions=["seattle"],
        approved=False,
        rating=3.0,
    ),
    Provider(
        provider_id="prov_accesstech",
        name="AccessTech Solutions",
        service_types=["ASSISTIVE_LISTENING", "LARGE_PRINT", "REMOTE_ACCESS"],
        jurisdictions=["seattle", "kingcounty", "oakland"],
        approved=True,
        rating=4.6,
    ),
    Provider(
        provider_id="prov_captionpro",
        name="Caption Pro Services",
        service_types=["CART", "REMOTE_ACCESS"],
        jurisdictions=["seattle", "sanjose"],
        approved=True,
        rating=4.4,
    ),
]


def main() -> None:
    table_name = os.environ.get("CORE_TABLE")
    if not table_name:
        print("Error: CORE_TABLE environment variable not set", file=sys.stderr)
        print("Usage: CORE_TABLE=accessflow-core python scripts/seed_providers.py", file=sys.stderr)
        sys.exit(1)

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    print(f"Seeding {len(PROVIDERS)} providers to {table_name}...")

    for provider in PROVIDERS:
        table.put_item(
            Item={
                "PK": f"PROVIDER#{provider.provider_id}",
                "SK": "META",
                "entity": "PROVIDER",
                "data": provider.model_dump_json(),
                "GSI1PK": "PROVIDER",
                "GSI1SK": provider.provider_id,
            }
        )
        print(f"  - {provider.provider_id}: {provider.name}")

    print("Done.")


if __name__ == "__main__":
    main()
