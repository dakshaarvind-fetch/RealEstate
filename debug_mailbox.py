"""
debug_mailbox.py - Tests mailbox endpoint with both auth methods to find which works.
Run: venv\Scripts\python debug_mailbox.py  (or .venv311\Scripts\python)
"""

import asyncio
import os
from datetime import datetime, timezone
from secrets import token_bytes

import aiohttp
from dotenv import load_dotenv
from uagents_core.identity import Identity
from uagents_core.storage import compute_attestation

load_dotenv()

SEED = os.environ["AGENT_SEED"]
API_KEY = os.environ["AGENTVERSE_API_KEY"]
BASE = "https://agentverse.ai"

identity = Identity.from_seed(SEED, 0)
address = identity.address
print(f"Agent address: {address}")


def make_attestation() -> str:
    now = datetime.now(timezone.utc)
    return compute_attestation(
        identity=identity,
        validity_start=now,
        validity_secs=30000,
        nonce=token_bytes(32),
    )


async def test():
    mailbox_url = f"{BASE}/v2/agents/{address}/mailbox"

    async with aiohttp.ClientSession() as session:
        # Test 1: Agent attestation (what the library uses)
        print(f"\n--- Test 1: Agent attestation ---")
        attestation = make_attestation()
        async with session.get(
            mailbox_url,
            headers={"Authorization": f"Agent {attestation}"},
        ) as resp:
            print(f"Status: {resp.status}")
            print(f"Body:   {await resp.text()}")

        # Test 2: Bearer API key
        print(f"\n--- Test 2: Bearer API key ---")
        async with session.get(
            mailbox_url,
            headers={"Authorization": f"Bearer {API_KEY}"},
        ) as resp:
            print(f"Status: {resp.status}")
            print(f"Body:   {await resp.text()}")

        # Test 3: Check if agent exists at all
        print(f"\n--- Test 3: GET agent profile ---")
        async with session.get(
            f"{BASE}/v2/agents/{address}",
            headers={"Authorization": f"Bearer {API_KEY}"},
        ) as resp:
            print(f"Status: {resp.status}")
            print(f"Body:   {await resp.text()[:500]}")


if __name__ == "__main__":
    asyncio.run(test())
