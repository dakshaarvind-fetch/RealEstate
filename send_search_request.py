"""
send_search_request.py — Test client for your Agentverse-hosted agent.

Usage:
  1. Replace RECIPIENT with your deployed agent's address from the Agentverse UI
  2. Set QUERY to whatever you want to search for
  3. Run: python send_search_request.py

For follow-up searches (session continuity), change REQUEST_TYPE to "followup"
and make sure user_id matches what you used in the initial search.
"""

from uagents import Agent, Context, Model

# ── Config ────────────────────────────────────────────────────────────────────
# Paste your Agentverse agent address here after deployment
RECIPIENT = "agent1qt784g97735re27r6dtqa7wasptrld5757n6xs5c0nfekhwff0eccy3qqvj"

# Use "/google-auth" once to start Google OAuth for this USER_ID.
QUERY = "2 BHK apartment for rent in San Francisco under 5000 a month"
USER_ID = "daksh_test"

# "search" for a new search, "followup" to continue a previous one
REQUEST_TYPE = "search"


# ── Models (must match uagent_bridge.py exactly) ──────────────────────────────

class SearchRequest(Model):
    query: str
    user_id: str = ""


class FollowUpRequest(Model):
    query: str
    user_id: str = ""


class SearchResponse(Model):
    sheet_url: str = ""
    summary: str = ""
    num_results: int = 0
    session_id: str = ""
    error: str = ""


# ── Sender agent ──────────────────────────────────────────────────────────────

sender = Agent(
    name="real_estate_test_sender",
    seed="real_estate_test_sender_seed_change_this_to_your_own",
    port=8002,
    endpoint=["http://127.0.0.1:8002/submit"],
    network="testnet",
)

print(f"Sender address: {sender.address}")
print(f"Sending {REQUEST_TYPE} to: {RECIPIENT}")
print(f"Query: {QUERY}\n")


@sender.on_event("startup")
async def startup(ctx: Context):
    if REQUEST_TYPE == "followup":
        await ctx.send(RECIPIENT, FollowUpRequest(query=QUERY, user_id=USER_ID))
        ctx.logger.info(f"Sent FollowUpRequest → {RECIPIENT}")
    else:
        await ctx.send(RECIPIENT, SearchRequest(query=QUERY, user_id=USER_ID))
        ctx.logger.info(f"Sent SearchRequest → {RECIPIENT}")


@sender.on_message(model=SearchResponse)
async def on_response(ctx: Context, sender_addr: str, msg: SearchResponse):
    print("\n" + "=" * 60)
    print("RESPONSE")
    print("=" * 60)
    if msg.error:
        print(f"❌ Error     : {msg.error}")
    else:
        print(f"✅ Results   : {msg.num_results}")
        print(f"   Sheet URL : {msg.sheet_url}")
        print(f"   Session   : {msg.session_id}")
        print(f"\nSummary:\n{msg.summary}")


if __name__ == "__main__":
    sender.run()
