"""
workflow.py — Core agent workflow using the Claude Agent SDK.

SDK features used:
  ┌─────────────────┬────────────────────────────────────────────────────────┐
  │ query()         │ Main agent loop — Claude calls tools autonomously      │
  │ Sessions        │ session_id captured from init message, saved to disk.  │
  │                 │ resume_workflow() lets users continue previous searches │
  │ Hooks           │ Two hooks wired into ClaudeAgentOptions:               │
  │  • PreToolUse   │   Rate-limiter on search_listings (HomeHarvest cooldown)│
  │  • PostToolUse  │   Audit logger — writes every tool call to agent.log   │
  └─────────────────┴────────────────────────────────────────────────────────┘

Architecture:
  parse_search_intent()  → plain Anthropic Messages API  (single-turn JSON extraction)
  run_agent_loop()       → Claude Agent SDK query() loop (agentic tool use)
  run_workflow()         → new search  (captures + saves session_id)
  resume_workflow()      → follow-up   (resumes from saved session_id)
"""

import asyncio
import json
import re
import os
import time
from datetime import datetime
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

import anthropic
from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    HookMatcher,
    AssistantMessage,
    ResultMessage,
)

from scraper import SearchInput, fetch_listings
from sheets import create_listings_sheet

load_dotenv()

# File where session IDs are persisted between runs
SESSIONS_FILE = "sessions.json"
LOG_FILE = "agent.log"


# ─────────────────────────────────────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WorkflowInput:
    user_request: str           # e.g. "3 bed house in Austin TX under 600k"
    user_id: str = "default"    # used to key sessions per Fetch.ai sender address


@dataclass
class WorkflowResult:
    sheet_url: str
    summary: str
    num_results: int
    session_id: Optional[str] = None   # returned so uagent_bridge can store it


# ─────────────────────────────────────────────────────────────────────────────
# Session helpers
# Docs ref: capture message.subtype == "init" → message.data["session_id"]
#           resume with ClaudeAgentOptions(resume=session_id)
# ─────────────────────────────────────────────────────────────────────────────

def load_sessions() -> dict:
    if os.path.exists(SESSIONS_FILE):
        with open(SESSIONS_FILE) as f:
            return json.load(f)
    return {}


def save_session(user_id: str, session_id: str) -> None:
    sessions = load_sessions()
    sessions[user_id] = {"session_id": session_id, "saved_at": datetime.now().isoformat()}
    with open(SESSIONS_FILE, "w") as f:
        json.dump(sessions, f, indent=2)
    print(f"💾 Session saved  → {session_id}  (user: {user_id})")


def get_saved_session(user_id: str) -> Optional[str]:
    sessions = load_sessions()
    entry = sessions.get(user_id)
    return entry["session_id"] if entry else None


# ─────────────────────────────────────────────────────────────────────────────
# Hooks
# Docs ref: HookMatcher(matcher="...", hooks=[callback])
#           passed via ClaudeAgentOptions(hooks={...})
# ─────────────────────────────────────────────────────────────────────────────

# Tracks the last time search_listings was called, to enforce a cooldown.
# HomeHarvest gets rate-limited if you call it repeatedly in quick succession.
_last_search_time: float = 0.0
SEARCH_COOLDOWN_SECONDS = 10


async def hook_rate_limit_search(input_data: dict, tool_use_id, context) -> dict:
    """
    PreToolUse hook — fires before search_listings is called.
    Blocks the call if it's within SEARCH_COOLDOWN_SECONDS of the last one,
    telling Claude why so it can inform the user rather than silently failing.
    """
    global _last_search_time
    now = time.time()
    elapsed = now - _last_search_time

    if _last_search_time > 0 and elapsed < SEARCH_COOLDOWN_SECONDS:
        wait = round(SEARCH_COOLDOWN_SECONDS - elapsed, 1)
        print(f"⏳ [Hook] Rate limit — search blocked, {wait}s remaining on cooldown")
        return {
            "hookSpecificOutput": {
                "hookEventName": input_data["hook_event_name"],
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Search rate limit: please wait {wait} more seconds "
                    f"before searching again to avoid being blocked by the data source."
                ),
            }
        }

    _last_search_time = now
    return {}   # allow the call


async def hook_audit_logger(input_data: dict, tool_use_id, context) -> dict:
    """
    PostToolUse hook — fires after every tool call completes.
    Appends a structured log line to agent.log for debugging and demos.
    Docs show PostToolUse receives tool_name + tool_response in input_data.
    """
    tool_name     = input_data.get("tool_name", "unknown")
    tool_response = input_data.get("tool_response", {})
    timestamp     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Extract a short summary depending on which tool ran
    if tool_name == "search_listings":
        detail = f"found {tool_response.get('num_results', '?')} results in {tool_response.get('location', '?')}"
    elif tool_name == "create_sheet":
        detail = f"sheet created → {tool_response.get('sheet_url', '?')}"
    else:
        detail = str(tool_response)[:120]

    log_line = f"[{timestamp}]  tool={tool_name:<20}  {detail}\n"

    with open(LOG_FILE, "a") as f:
        f.write(log_line)

    print(f"📝 [Hook] Logged  : {log_line.strip()}")
    return {}   # never blocks — just observes


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Parse natural language → structured SearchInput
# Plain Anthropic Messages API — single-turn JSON extraction, not agentic.
# ─────────────────────────────────────────────────────────────────────────────

async def parse_search_intent(user_request: str) -> SearchInput:
    client = anthropic.Anthropic()

    prompt = f"""
You are a real estate search assistant. Parse the following user request into a structured JSON object.

User request: "{user_request}"

Return ONLY valid JSON with these fields (omit fields not mentioned):
{{
  "location": "City, State OR zip code (required)",
  "listing_type": "for_sale | for_rent | sold  (default: for_sale)",
  "min_price": integer or null,
  "max_price": integer or null,
  "min_beds": integer or null,
  "max_beds": integer or null,
  "min_sqft": integer or null,
  "max_sqft": integer or null,
  "property_type": ["single_family","condo","townhouse","multi_family"] or null,
  "past_days": integer (default: 30)
}}

Examples:
- "3 bed house in Austin TX under 600k"
  → {{"location":"Austin, TX","listing_type":"for_sale","min_beds":3,"max_price":600000}}
- "rent apartment NYC 2 bed"
  → {{"location":"New York, NY","listing_type":"for_rent","min_beds":2,"max_beds":2}}

Return only the JSON, no explanation.
"""

    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```json\s*|^```\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    parsed = json.loads(raw)

    return SearchInput(
        location=parsed.get("location", ""),
        listing_type=parsed.get("listing_type", "for_sale"),
        min_price=parsed.get("min_price"),
        max_price=parsed.get("max_price"),
        min_beds=parsed.get("min_beds"),
        max_beds=parsed.get("max_beds"),
        min_sqft=parsed.get("min_sqft"),
        max_sqft=parsed.get("max_sqft"),
        property_type=parsed.get("property_type"),
        past_days=parsed.get("past_days", 30),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Custom tool implementations (called by Claude inside the query() loop)
# ─────────────────────────────────────────────────────────────────────────────

_tool_state: dict = {}

def tool_search_listings(params: dict) -> str:
    search = _tool_state.get("search")
    if not search:
        return json.dumps({"error": "No search parameters available."})

    print("\n🔍 [Tool] search_listings — scraping HomeHarvest...")
    df = fetch_listings(search)
    _tool_state["df"] = df

    if df.empty:
        return json.dumps({
            "status": "no_results",
            "message": f"No listings found in {search.location} with the given filters.",
            "num_results": 0,
        })

    preview = df.head(5).to_dict(orient="records")
    return json.dumps({
        "status": "success",
        "num_results": len(df),
        "location": search.location,
        "listing_type": search.listing_type,
        "price_min": int(df["Price ($)"].min()) if "Price ($)" in df.columns else None,
        "price_max": int(df["Price ($)"].max()) if "Price ($)" in df.columns else None,
        "price_avg": int(df["Price ($)"].mean()) if "Price ($)" in df.columns else None,
        "sample_listings": preview,
    })


def tool_create_sheet(params: dict) -> str:
    df = _tool_state.get("df")
    search = _tool_state.get("search")

    if df is None:
        return json.dumps({"error": "No listings data — run search_listings first."})

    print("\n📊 [Tool] create_sheet — writing to Google Sheets...")
    sheet_url = create_listings_sheet(df, search.location, search.listing_type)
    _tool_state["sheet_url"] = sheet_url

    return json.dumps({"status": "success", "sheet_url": sheet_url, "num_rows": len(df)})


TOOL_HANDLERS = {
    "search_listings": tool_search_listings,
    "create_sheet": tool_create_sheet,
}


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Agent loop — query() + Sessions + Hooks
# ─────────────────────────────────────────────────────────────────────────────

async def run_agent_loop(
    search: SearchInput,
    user_id: str,
    resume_session_id: Optional[str] = None,
) -> WorkflowResult:
    """
    Core agent loop. Wires together:
      - query()      from SDK quickstart
      - Sessions     capture init message → save session_id for follow-ups
      - Hooks        rate limiter (PreToolUse) + audit logger (PostToolUse)
    """
    _tool_state["search"] = search

    system_prompt = (
        "You are a real estate assistant agent. "
        "Find property listings and organise them into a Google Sheet. "
        "Always call search_listings first, review results, then call create_sheet. "
        "Finish with a concise summary: number of results, price range, average price, sheet URL."
    )

    user_prompt = (
        f"Find real estate listings matching these criteria and create a Google Sheet:\n"
        f"- Location     : {search.location}\n"
        f"- Listing type : {search.listing_type}\n"
        f"- Price range  : ${search.min_price or 'any'} – ${search.max_price or 'any'}\n"
        f"- Beds         : {search.min_beds or 'any'}+\n"
        f"- Size         : {search.min_sqft or 'any'}+ sqft\n\n"
        f"Use search_listings to fetch, then create_sheet to save."
    )

    # ── Hooks config (from docs) ──────────────────────────────────────────
    hooks = {
        # PreToolUse: rate-limit search_listings only
        "PreToolUse": [
            HookMatcher(matcher="search_listings", hooks=[hook_rate_limit_search])
        ],
        # PostToolUse: audit-log all tool calls
        "PostToolUse": [
            HookMatcher(hooks=[hook_audit_logger])
        ],
    }

    # ── Options — include resume if this is a follow-up ──────────────────
    options_kwargs = dict(
        allowed_tools=["search_listings", "create_sheet"],
        permission_mode="acceptEdits",
        system_prompt=system_prompt,
        hooks=hooks,
    )
    if resume_session_id:
        options_kwargs["resume"] = resume_session_id
        print(f"🔄 Resuming session: {resume_session_id}")

    options = ClaudeAgentOptions(**options_kwargs)

    # ── Agent loop ────────────────────────────────────────────────────────
    captured_session_id: Optional[str] = None
    final_summary = ""
    num_results = 0

    async for message in query(prompt=user_prompt, options=options):

        # ── Sessions: capture session_id from the init message (docs pattern) ──
        if hasattr(message, "subtype") and message.subtype == "init":
            captured_session_id = message.data.get("session_id") if hasattr(message, "data") else None
            if captured_session_id:
                print(f"🆔 Session started : {captured_session_id}")
                save_session(user_id, captured_session_id)

        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text") and block.text:
                    print(f"\n🤖 Claude: {block.text}")
                elif hasattr(block, "name") and block.name in TOOL_HANDLERS:
                    tool_name   = block.name
                    tool_params = block.input if hasattr(block, "input") else {}
                    print(f"\n🔧 Tool call: {tool_name}")
                    result_str  = TOOL_HANDLERS[tool_name](tool_params)
                    result_data = json.loads(result_str)
                    if tool_name == "search_listings":
                        num_results = result_data.get("num_results", 0)

        elif isinstance(message, ResultMessage):
            print(f"\n✅ Agent loop complete: {message.subtype}")
            if hasattr(message, "result") and message.result:
                final_summary = message.result
    # ─────────────────────────────────────────────────────────────────────

    sheet_url = _tool_state.get("sheet_url", "")

    if not final_summary:
        if sheet_url and num_results > 0:
            final_summary = f"✅ Found {num_results} listings in {search.location}. Sheet: {sheet_url}"
        else:
            final_summary = f"⚠️ No listings found for your criteria in {search.location}."

    return WorkflowResult(
        sheet_url=sheet_url,
        summary=final_summary,
        num_results=num_results,
        session_id=captured_session_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points (called by uagent_bridge.py)
# ─────────────────────────────────────────────────────────────────────────────

async def run_workflow(input_data: WorkflowInput) -> WorkflowResult:
    """Start a fresh search. Saves session_id so the user can follow up later."""
    print(f"\n🏠 Real Estate Agent — new search")
    print(f"   Request : \"{input_data.user_request}\"")
    print(f"   User    : {input_data.user_id}")

    search = await parse_search_intent(input_data.user_request)
    print(f"   Parsed  : {search.location} | {search.listing_type} | "
          f"${search.min_price or 0:,}–${search.max_price or '∞'} | {search.min_beds or 'any'}+ beds")

    if not search.location:
        return WorkflowResult(
            sheet_url="",
            summary="❌ Could not determine location. Please include a city or zip code.",
            num_results=0,
        )

    return await run_agent_loop(search, user_id=input_data.user_id)


async def resume_workflow(input_data: WorkflowInput) -> WorkflowResult:
    """
    Resume a previous session for this user_id.
    If no saved session exists, falls back to a fresh search.
    Example use: user sends "now find similar ones under 500k" —
    Claude already knows the location and context from the last search.
    """
    session_id = get_saved_session(input_data.user_id)

    if session_id:
        print(f"\n🔄 Resuming previous session for user '{input_data.user_id}'")
        search = await parse_search_intent(input_data.user_request)
        return await run_agent_loop(search, user_id=input_data.user_id, resume_session_id=session_id)
    else:
        print(f"\n⚠️  No saved session for '{input_data.user_id}' — starting fresh")
        return await run_workflow(input_data)


# ─────────────────────────────────────────────────────────────────────────────
# Quick test
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    async def test():
        # First search — saves session
        print("=" * 60)
        print("TEST 1: Fresh search")
        print("=" * 60)
        result = await run_workflow(WorkflowInput(
            user_request="3 bedroom house in Austin TX under 700000 for sale",
            user_id="test_user",
        ))
        print(f"\n🏁 Result 1:")
        print(f"   Results   : {result.num_results}")
        print(f"   Session   : {result.session_id}")
        print(f"   Sheet URL : {result.sheet_url}")

        # Follow-up — resumes session, Claude remembers the context
        print("\n" + "=" * 60)
        print("TEST 2: Follow-up (resume session)")
        print("=" * 60)
        result2 = await resume_workflow(WorkflowInput(
            user_request="now find similar ones but under 500000",
            user_id="test_user",
        ))
        print(f"\n🏁 Result 2:")
        print(f"   Results   : {result2.num_results}")
        print(f"   Sheet URL : {result2.sheet_url}")

    asyncio.run(test())
