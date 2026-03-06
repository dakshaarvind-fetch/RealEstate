"""
test_payment_flow.py — End-to-end test for the Stripe payment gate.

Two modes:

  Full flow (search → pay → get sheet):
    python test_payment_flow.py

  Skip search, commit an existing Stripe session directly:
    python test_payment_flow.py commit cs_test_51AbcXxx...

Steps in full-flow mode:
  1. Sends SearchRequest to the real estate agent
  2. Receives RequestPayment → prints the Stripe checkout URL / session ID
  3. You pay at https://dashboard.stripe.com/test/checkout/...
     OR use test card 4242 4242 4242 4242 at the URL in the logs
  4. Press Enter → script sends CommitPayment
  5. Agent verifies → creates Google Sheet → sends SearchResponse

NOTE: Uses Agentverse mailbox so the Docker agent can reply back.
      Requires AGENTVERSE_API_KEY in .env (same key the main agent uses).
"""

import asyncio
import os
import sys
import types

import aiohttp
import stripe as _stripe_sdk
from dotenv import load_dotenv
from pydantic import UUID4
from uagents import Agent, Context, Model
from uagents.mailbox import StoredEnvelope
from uagents_core.config import AgentverseConfig
from uagents_core.contrib.protocols.payment import (
    CommitPayment,
    CompletePayment,
    Funds,
    RejectPayment,
    RequestPayment,
)
from uagents_core.identity import Identity
from uagents_core.registration import ChallengeResponse, IdentityProof, RegistrationRequest
from uagents_core.types import AgentEndpoint

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────

RECIPIENT = "agent1qt784g97735re27r6dtqa7wasptrld5757n6xs5c0nfekhwff0eccy3qqvj"
QUERY     = "condo in miami from 400k to 800k"
USER_ID   = "daksh_payment_test"

# ── Message models (must match uagent_bridge.py) ───────────────────────────────

class SearchRequest(Model):
    query: str
    user_id: str = ""


class SearchResponse(Model):
    sheet_url: str = ""
    summary: str = ""
    num_results: int = 0
    session_id: str = ""
    error: str = ""


# ── Sender agent (mailbox so Docker agent can reply) ──────────────────────────

_api_key = os.getenv("AGENTVERSE_API_KEY", "").strip()
if not _api_key:
    print("ERROR: AGENTVERSE_API_KEY not set in .env — mailbox won't work.")
    sys.exit(1)

sender = Agent(
    name="payment_test_sender",
    seed="payment_test_sender_seed_42_unique",
    mailbox=True,
    network=os.getenv("AGENT_NETWORK", "mainnet"),
)


def _patch_mailbox_bearer(agent: Agent, api_key: str) -> None:
    """Same Bearer-token patch used by the main agent (fixes Agentverse v2 auth)."""
    client = agent.mailbox_client
    if client is None:
        return

    async def _check_mailbox_loop(self):
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{self._agentverse.agents_api}/{self._identity.address}/mailbox"
                    async with session.get(
                        url, headers={"Authorization": f"Bearer {api_key}"}
                    ) as resp:
                        if resp.status == 200:
                            for item in await resp.json():
                                await self._handle_envelope(StoredEnvelope.model_validate(item))
                        elif resp.status == 404:
                            if not self._missing_mailbox_warning_logged:
                                print("[mailbox] Not registered yet — will retry")
                                self._missing_mailbox_warning_logged = True
                        else:
                            print(f"[mailbox] Error {resp.status}: {await resp.text()}")
            except aiohttp.ClientConnectorError as ex:
                print(f"[mailbox] Connection error: {ex}")
            except Exception as ex:
                print(f"[mailbox] Unexpected error: {ex}")
            await asyncio.sleep(self._poll_interval)

    async def _delete_envelope(self, uuid: UUID4):
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self._agentverse.agents_api}/{self._identity.address}/mailbox/{uuid}"
                async with session.delete(
                    url, headers={"Authorization": f"Bearer {api_key}"}
                ) as resp:
                    if resp.status >= 300:
                        print(f"[mailbox] Delete failed: {await resp.text()}")
        except Exception as ex:
            print(f"[mailbox] Delete error: {ex}")

    client._check_mailbox_loop = types.MethodType(_check_mailbox_loop, client)
    client._delete_envelope    = types.MethodType(_delete_envelope,    client)


_patch_mailbox_bearer(sender, _api_key)

print(f"Test sender address : {sender.address}")
print(f"Recipient           : {RECIPIENT}")
print(f"Mode                : {'commit only' if len(sys.argv) > 1 else 'full flow'}\n")

# ── Shared state ───────────────────────────────────────────────────────────────

_state: dict = {}  # checkout_session_id, agent_address, amount


# ── Mailbox auto-registration ──────────────────────────────────────────────────

async def _register_mailbox() -> bool:
    """Register this test agent's mailbox with Agentverse (same flow as register_mailbox.py).
    Returns True on success."""
    agentverse = AgentverseConfig()
    headers = {"Authorization": f"Bearer {_api_key}", "Content-Type": "application/json"}
    identity = Identity.from_seed("payment_test_sender_seed_42_unique", 0)
    address = identity.address

    async with aiohttp.ClientSession() as session:
        # Step 1: get challenge
        async with session.get(
            f"{agentverse.identity_api}/{address}/challenge", headers=headers
        ) as resp:
            if resp.status != 200:
                print(f"[register] Challenge failed: {resp.status} {await resp.text()}")
                return False
            challenge = ChallengeResponse.model_validate_json(await resp.text())

        # Step 2: prove identity
        proof = IdentityProof(
            address=address,
            challenge=challenge.challenge,
            challenge_response=identity.sign(challenge.challenge.encode()),
        )
        async with session.post(
            agentverse.identity_api, data=proof.model_dump_json(), headers=headers
        ) as resp:
            if resp.status != 200:
                print(f"[register] Identity proof failed: {resp.status} {await resp.text()}")
                return False

        # Step 3: register mailbox
        registration = RegistrationRequest(
            address=address,
            name="payment_test_sender",
            agent_type="uagent",
            endpoints=[AgentEndpoint(url=agentverse.mailbox_endpoint, weight=1)],
        )
        async with session.post(
            agentverse.agents_api, data=registration.model_dump_json(), headers=headers
        ) as resp:
            body = await resp.text()
            if resp.status == 200:
                print("[register] Mailbox registered successfully.")
                return True
            print(f"[register] Registration failed: {resp.status} {body}")
            return False


# ── Startup ────────────────────────────────────────────────────────────────────

@sender.on_event("startup")
async def startup(ctx: Context):
    # "commit" mode: skip the search, directly commit a known session ID
    if len(sys.argv) > 1 and sys.argv[1] == "commit":
        if len(sys.argv) < 3:
            print("Usage: python test_payment_flow.py commit <checkout_session_id>")
            raise SystemExit(1)
        session_id = sys.argv[2]
        # Still register so we can receive the reply
        await _register_mailbox()
        print(f"Sending CommitPayment for session: {session_id}")
        await ctx.send(
            RECIPIENT,
            CommitPayment(
                transaction_id=session_id,
                funds=Funds(currency="USD", amount="1.99", payment_method="stripe"),
            ),
        )
        return

    # Register mailbox so the Docker agent can reply to us
    print("Registering test agent mailbox...")
    ok = await _register_mailbox()
    if not ok:
        print("WARNING: Mailbox registration failed — replies may not arrive.")

    # Full flow: start with a search
    print(f"Sending SearchRequest: \"{QUERY}\"")
    await ctx.send(RECIPIENT, SearchRequest(query=QUERY, user_id=USER_ID))


# ── Auto-confirm payment via Stripe API (test mode only) ──────────────────────

async def _auto_confirm_payment(amount: str) -> str | None:
    """Create and confirm a Stripe PaymentIntent with a test card.
    Returns the PaymentIntent ID (pi_...) on success, None on failure.
    Works only in test mode (sk_test_...). No browser required."""
    _stripe_sdk.api_key = os.getenv("STRIPE_SECRET_KEY")

    try:
        amount_cents = round(float(amount) * 100)
        pi = await asyncio.to_thread(
            _stripe_sdk.PaymentIntent.create,
            amount=amount_cents,
            currency="usd",
            payment_method_types=["card"],
            payment_method="pm_card_visa",
            confirm=True,
        )
        if pi.status in ("succeeded", "processing"):
            print(f"[auto-pay] PaymentIntent {pi.id} confirmed (status: {pi.status})")
            return pi.id
        print(f"[auto-pay] Unexpected status: {pi.status}")
        return None

    except Exception as exc:
        print(f"[auto-pay] Failed: {exc}")
        return None


# ── Handle payment request ─────────────────────────────────────────────────────

@sender.on_message(model=RequestPayment)
async def on_payment_request(ctx: Context, agent_addr: str, msg: RequestPayment):
    stripe_meta = (msg.metadata or {}).get("stripe", {})
    session_id  = stripe_meta.get("checkout_session_id") or stripe_meta.get("id")
    checkout_url = stripe_meta.get("url", "")
    amount      = msg.accepted_funds[0].amount if msg.accepted_funds else "1.99"

    _state["session_id"] = session_id
    _state["agent_addr"] = agent_addr
    _state["amount"]     = amount

    print("\n" + "=" * 60)
    print("PAYMENT REQUESTED")
    print("=" * 60)
    print(f"  Amount      : ${amount} USD")
    print(f"  Description : {msg.description}")
    print(f"  Session ID  : {session_id}")
    if checkout_url:
        print(f"  Checkout URL: {checkout_url}")
    print()
    print("Auto-confirming payment with Stripe test card...")

    pi_id = await _auto_confirm_payment(amount)

    # transaction_id: use the confirmed PI if auto-pay succeeded, else the checkout session
    transaction_id = pi_id if pi_id else session_id

    if not pi_id:
        # Fallback: let the user pay manually via the hosted checkout URL
        print()
        print("Auto-confirm failed. Pay manually:")
        if checkout_url:
            print(f"  Visit: {checkout_url}")
            print("  Use test card: 4242 4242 4242 4242 | expiry: 12/34 | CVC: 123")
        else:
            print(f"  Stripe Dashboard → https://dashboard.stripe.com/test/payments")
            print(f"  Find session {session_id} and confirm with test card.")
        await asyncio.to_thread(input, "Press Enter after paying... ")

    print("\nSending CommitPayment...")
    await ctx.send(
        agent_addr,
        CommitPayment(
            transaction_id=transaction_id,
            funds=Funds(currency="USD", amount=amount, payment_method="stripe"),
            recipient=agent_addr,
        ),
    )


# ── Handle payment outcome ─────────────────────────────────────────────────────

@sender.on_message(model=CompletePayment)
async def on_complete(_ctx: Context, _addr: str, msg: CompletePayment):
    print(f"\nPayment confirmed by agent (tx: {msg.transaction_id})")
    print("Waiting for your Google Sheet...\n")


@sender.on_message(model=RejectPayment)
async def on_reject(_ctx: Context, _addr: str, msg: RejectPayment):
    print(f"\nPayment rejected: {msg.reason}")


# ── Handle final search response ───────────────────────────────────────────────

@sender.on_message(model=SearchResponse)
async def on_response(_ctx: Context, _addr: str, msg: SearchResponse):
    print("=" * 60)
    if msg.error:
        print(f"Error: {msg.error}")
    else:
        print("SHEET READY")
        print(f"  Results   : {msg.num_results} listings")
        print(f"  Sheet URL : {msg.sheet_url}")
        print()
        print(msg.summary)
    print("=" * 60)


if __name__ == "__main__":
    sender.run()
