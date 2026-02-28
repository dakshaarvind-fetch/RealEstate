"""monitor_mailbox.py - Watch the mailbox in real-time to see if messages arrive."""
import asyncio
import os
import sys
import aiohttp
from dotenv import load_dotenv
load_dotenv()

ADDRESS = "agent1qt784g97735re27r6dtqa7wasptrld5757n6xs5c0nfekhwff0eccy3qqvj"
API_KEY = os.environ["AGENTVERSE_API_KEY"]
URL = f"https://agentverse.ai/v2/agents/{ADDRESS}/mailbox"

async def monitor():
    print(f"Watching mailbox for {ADDRESS}")
    print("Send a message on ASI:One now — press Ctrl+C to stop\n")
    count = 0
    async with aiohttp.ClientSession() as session:
        while True:
            async with session.get(URL, headers={"Authorization": f"Bearer {API_KEY}"}) as r:
                if r.status == 200:
                    items = await r.json()
                    if items:
                        print(f"\n[{count}] GOT {len(items)} message(s)!")
                        for item in items:
                            print("  uuid:", item.get("uuid"))
                            env = item.get("envelope", {})
                            print("  sender:", env.get("sender"))
                            print("  schema_digest:", env.get("schema_digest"))
                    else:
                        print(".", end="", flush=True)
                else:
                    print(f"\nHTTP {r.status}: {await r.text()}")
            count += 1
            await asyncio.sleep(0.5)

if __name__ == "__main__":
    asyncio.run(monitor())
