"""
uagent_bridge.py — Fetch.ai uAgent bridge.

Protocol:
  SearchRequest  — new search  (query + optional user_id)
  FollowUpRequest — follow-up  (uses saved session for user_id)
  SearchResponse — result      (sheet_url, summary, num_results, session_id)

The session_id is returned in SearchResponse so the sender can include it
in FollowUpRequests, enabling conversational multi-turn searches:
  "3 bed in Austin TX under 700k"  →  session saved
  "now find similar ones under 500k"  →  session resumed, Claude remembers context
"""

import asyncio
from uagents import Agent, Context, Model
from workflow import WorkflowInput, run_workflow, resume_workflow


# ── Message schemas ──────────────────────────────────────────────────────────

class SearchRequest(Model):
    query: str                  # Natural language search
    user_id: str = "default"    # Keyed to sender address for session tracking


class FollowUpRequest(Model):
    query: str                  # Follow-up in natural language
    user_id: str = "default"    # Must match the user_id from the original search


class SearchResponse(Model):
    sheet_url: str
    summary: str
    num_results: int
    session_id: str = ""        # Returned so sender can confirm session was saved
    error: str = ""


# ── Agent setup ──────────────────────────────────────────────────────────────

agent = Agent(
    name="real_estate_finder",
    seed="real_estate_finder_seed_phrase_change_this",  # Change for production!
    port=8001,
    endpoint=["http://localhost:8001/submit"],
)


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info("🏠 Real Estate Finder Agent started")
    ctx.logger.info(f"   Address : {agent.address}")
    ctx.logger.info("   Send SearchRequest to start, FollowUpRequest to continue")


@agent.on_message(model=SearchRequest)
async def handle_search(ctx: Context, sender: str, msg: SearchRequest):
    """New search — starts a fresh agent session, saves session_id for follow-ups."""
    # Use sender address as user_id if caller didn't set one
    user_id = msg.user_id if msg.user_id != "default" else sender
    ctx.logger.info(f"📩 New search from {sender}: '{msg.query}'")

    try:
        result = await run_workflow(WorkflowInput(
            user_request=msg.query,
            user_id=user_id,
        ))
        response = SearchResponse(
            sheet_url=result.sheet_url,
            summary=result.summary,
            num_results=result.num_results,
            session_id=result.session_id or "",
        )
        ctx.logger.info(f"✅ {result.num_results} listings → {result.sheet_url}")

    except Exception as e:
        ctx.logger.error(f"❌ Error: {e}")
        response = SearchResponse(
            sheet_url="", summary=f"Error: {e}", num_results=0, error=str(e)
        )

    await ctx.send(sender, response)


@agent.on_message(model=FollowUpRequest)
async def handle_follow_up(ctx: Context, sender: str, msg: FollowUpRequest):
    """Follow-up search — resumes saved session so Claude remembers previous context."""
    user_id = msg.user_id if msg.user_id != "default" else sender
    ctx.logger.info(f"🔄 Follow-up from {sender}: '{msg.query}'")

    try:
        result = await resume_workflow(WorkflowInput(
            user_request=msg.query,
            user_id=user_id,
        ))
        response = SearchResponse(
            sheet_url=result.sheet_url,
            summary=result.summary,
            num_results=result.num_results,
            session_id=result.session_id or "",
        )
        ctx.logger.info(f"✅ {result.num_results} listings → {result.sheet_url}")

    except Exception as e:
        ctx.logger.error(f"❌ Error: {e}")
        response = SearchResponse(
            sheet_url="", summary=f"Error: {e}", num_results=0, error=str(e)
        )

    await ctx.send(sender, response)


if __name__ == "__main__":
    agent.run()
