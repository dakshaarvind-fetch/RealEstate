# Real Estate Agent

Real estate assistant built on `uAgents` that:

1. Parses natural-language housing requests with Anthropic.
2. Fetches listings using HomeHarvest (Zillow, Realtor.com, Redfin).
3. Creates a Google Sheet in the end user's own Google Drive using per-user OAuth device flow.

## Features

- Mailbox-ready `uAgents` bridge for Agentverse deployment.
- Per-user Google OAuth (`/google-auth`) with token persistence by `user_id`.
- Search + follow-up flow (`run_workflow` / `resume_workflow`) with in-memory session context.
- Uses the official Anthropic Python SDK (Claude SDK) via `client.messages.create(...)`.
- Sheet output with formatted headers, sorted rows, and shareable link.
- Helper scripts for local validation and mailbox debugging.

## Architecture

- `uagent_bridge.py`: network entrypoint (`SearchRequest`, `FollowUpRequest`, chat protocol handlers).
- `workflow.py`: parse intent with Claude, execute tools, summarize results.
- `scraper.py`: listing retrieval and filtering via HomeHarvest.
- `sheets.py`: Google OAuth device flow + Google Sheets creation.
- `send_search_request.py`: test sender agent.
- `register_mailbox.py` / `monitor_mailbox.py` / `debug_mailbox.py`: mailbox setup and diagnostics.

## Claude SDK Integration

This project uses the Anthropic Python SDK directly (not `claude_agent_sdk`).

- SDK package: `anthropic` (see `requirements.txt`)
- Client setup: `client = anthropic.Anthropic()`
- API surface used: `client.messages.create(...)`
- Parsing model: `claude-haiku-4-5-20251001` for `parse_search_intent()`
- Agent loop model: `claude-sonnet-4-5-20250929` for tool-use orchestration
- Tool calling: Claude emits `tool_use` blocks, then the app executes:
  - `search_listings` (HomeHarvest fetch/filter)
  - `create_sheet` (Google Sheets write)
- Loop stop conditions:
  - `stop_reason == "tool_use"`: execute tools and continue
  - `stop_reason == "end_turn"`: finalize user summary

## Requirements

- Python 3.11+
- Anthropic API key (Claude SDK)
- Google OAuth client config (Desktop/Web app JSON)
- Agent seed for `uAgents`
- Agentverse API key (recommended for mailbox mode, required for registration/debug scripts)

## Quick Start (Local)

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Create environment file:

```bash
cp .env.example .env
```

PowerShell:

```powershell
Copy-Item .env.example .env
```

3. Fill `.env` with your keys (see variable reference below).

4. Start the agent:

```bash
python uagent_bridge.py
```

## Quick Start (Docker)

```bash
docker compose up --build
```

The container runs `python uagent_bridge.py` and reads variables from `.env`.

## Environment Variables

Required:

- `AGENT_SEED`: private seed for your agent identity.
- `ANTHROPIC_API_KEY`: used for request parsing and tool-use loop.
- `GOOGLE_OAUTH_CLIENT_JSON` or `GOOGLE_OAUTH_CLIENT_FILE`: OAuth client config (one is required).

Common defaults:

- `AGENT_NAME` (default: `real_estate_agent`)
- `AGENT_NETWORK` (default: `testnet`)
- `AGENT_MAILBOX` (default: `true`)
- `AGENT_PORT` (default: `8000`)
- `AGENT_ENDPOINT` (only needed for non-mailbox local transport)

Optional:

- `AGENTVERSE_API_KEY`: enables bearer-token mailbox patch and required by mailbox utility scripts.
- `GOOGLE_SHEET_SHARE_EMAIL`: extra editor to share each created sheet with.
- `GOOGLE_OAUTH_TOKEN_STORE_FILE` (default: `google_user_tokens.json`)
- `GOOGLE_OAUTH_DEVICE_STORE_FILE` (default: `google_device_flows.json`)

## Per-User Google OAuth Flow

Use this once per `user_id`:

1. Send query `/google-auth`.
2. Agent replies with Google verification URL + user code.
3. User approves Sheets/Drive access in browser.
4. Re-send the actual real estate query.
5. Agent exchanges device code for tokens and creates the sheet in that user's Drive.

If already connected, `/google-auth` returns a connected status message.

## Testing and Validation

### 1) Local workflow test (no Fetch.ai layer)

```bash
python test_workflow.py
```

This runs `run_workflow()` directly with a sample query.

### 2) Agent-to-agent test

Edit constants in `send_search_request.py`:

- `RECIPIENT`: deployed agent address
- `QUERY`: search text (`"/google-auth"` for first auth step)
- `USER_ID`: stable user identifier
- `REQUEST_TYPE`: `"search"` or `"followup"`

Then run:

```bash
python send_search_request.py
```

### 3) Mailbox utilities

- Register mailbox agent:

```bash
python register_mailbox.py
```

- Monitor mailbox traffic:

```bash
python monitor_mailbox.py
```

- Debug mailbox auth path:

```bash
python debug_mailbox.py
```

## Typical Request Lifecycle

1. User sends search text.
2. `workflow.py` parses intent into structured search criteria.
3. Agent calls `search_listings` tool and fetches listings.
4. Agent calls `create_sheet` tool.
5. User receives summary + sheet URL.
6. Follow-up message can reuse prior session context via `resume_workflow()`.

## Troubleshooting

- `Google authorization required for sheet creation`:
  Run `/google-auth` for that `user_id`, approve access, then resend the query.
- Mailbox returns 404/not found:
  Run `python register_mailbox.py` once, then restart the agent.
- Mailbox polling/auth failures:
  Set `AGENTVERSE_API_KEY` and use `debug_mailbox.py`.
- No listings returned:
  Broaden location/price filters or increase date window (`past_days`).
