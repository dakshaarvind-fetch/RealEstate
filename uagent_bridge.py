"""uagent_bridge.py - uAgents bridge for Agentverse mailbox deployment."""

import asyncio
import os
import types

import aiohttp
from dotenv import load_dotenv
from pydantic import UUID4
from uagents import Agent, Context, Model, Protocol
from uagents.mailbox import StoredEnvelope
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)

from sheets import get_google_auth_message
from workflow import WorkflowInput, run_workflow, resume_workflow

load_dotenv()


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


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _build_agent() -> Agent:
    seed = _require_env("AGENT_SEED")
    name = os.getenv("AGENT_NAME", "real_estate_agent")
    network = os.getenv("AGENT_NETWORK", "testnet")
    use_mailbox = _bool_env("AGENT_MAILBOX", True)
    port = int(os.getenv("AGENT_PORT", "8000"))
    endpoint = os.getenv("AGENT_ENDPOINT", "").strip()

    kwargs = {
        "name": name,
        "seed": seed,
        "network": network,
        "port": port,
    }

    if endpoint:
        kwargs["endpoint"] = [endpoint]

    if use_mailbox:
        kwargs["mailbox"] = True
    else:
        if endpoint:
            kwargs["endpoint"] = [endpoint]

    return Agent(**kwargs)


agent = _build_agent()


def _patch_mailbox_bearer(api_key: str) -> None:
    """Replace attestation-based auth with Bearer token in the mailbox client.

    Agentverse v2 API now requires 'Authorization: Bearer <api_key>' for mailbox
    polling, but uAgents 0.23.x still sends the old 'Agent <attestation>' header.
    """
    client = agent.mailbox_client
    if client is None:
        return

    async def _check_mailbox_loop(self):
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{self._agentverse.agents_api}/{self._identity.address}/mailbox"
                    async with session.get(
                        url,
                        headers={"Authorization": f"Bearer {api_key}"},
                    ) as resp:
                        if resp.status == 200:
                            for item in await resp.json():
                                await self._handle_envelope(StoredEnvelope.model_validate(item))
                        elif resp.status == 404:
                            if not self._missing_mailbox_warning_logged:
                                self._logger.warning(
                                    "Agent mailbox not found: run register_mailbox.py"
                                )
                                self._missing_mailbox_warning_logged = True
                        else:
                            self._logger.error(
                                f"Failed to retrieve messages: {resp.status}:{await resp.text()}"
                            )
            except aiohttp.ClientConnectorError as ex:
                self._logger.warning(f"Failed to connect to mailbox server: {ex}")
            except Exception as ex:
                self._logger.exception(f"Got exception while checking mailbox: {ex}")
            await asyncio.sleep(self._poll_interval)

    async def _delete_envelope(self, uuid: UUID4):
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self._agentverse.agents_api}/{self._identity.address}/mailbox/{uuid}"
                async with session.delete(
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                ) as resp:
                    if resp.status >= 300:
                        self._logger.warning(
                            f"Failed to delete envelope: {await resp.text()}"
                        )
        except aiohttp.ClientConnectorError as ex:
            self._logger.warning(f"Failed to connect to mailbox server: {ex}")
        except Exception as ex:
            self._logger.exception(f"Got exception while deleting message: {ex}")

    client._check_mailbox_loop = types.MethodType(_check_mailbox_loop, client)
    client._delete_envelope = types.MethodType(_delete_envelope, client)


_agentverse_api_key = os.getenv("AGENTVERSE_API_KEY", "").strip()
if _agentverse_api_key:
    _patch_mailbox_bearer(_agentverse_api_key)


def _resolve_user_id(message_user_id: str, sender: str) -> str:
    value = (message_user_id or "").strip()
    return value if value else sender


@agent.on_event("startup")
async def on_startup(ctx: Context):
    ctx.logger.info(f"Agent started: {agent.name}")
    ctx.logger.info(f"Address: {agent.address}")
    ctx.logger.info(f"Network: {os.getenv('AGENT_NETWORK', 'testnet')}")


_chat_proto = Protocol(spec=chat_protocol_spec)


@_chat_proto.on_message(model=ChatMessage)
async def handle_chat(ctx: Context, sender: str, msg: ChatMessage):
    """Handle messages from ASI:One and other chat-protocol clients."""
    await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))

    query = msg.text().strip()
    if not query:
        await ctx.send(sender, ChatMessage(content=[TextContent(text="Please send a search query, e.g. '3 bed house for sale in Austin TX under $400k'")]))
        return

    user_id = sender
    try:
        result = await run_workflow(WorkflowInput(user_request=query, user_id=user_id))
        if result.sheet_url:
            reply = f"{result.summary}\n\nResults sheet: {result.sheet_url}"
        else:
            reply = result.summary or "No results found."
    except Exception as exc:
        ctx.logger.exception("Chat workflow failed")
        reply = f"Sorry, something went wrong: {exc}"

    await ctx.send(sender, ChatMessage(content=[TextContent(text=reply)]))


@_chat_proto.on_message(model=ChatAcknowledgement)
async def handle_chat_ack(_ctx: Context, _sender: str, _msg: ChatAcknowledgement):
    pass  # acknowledgements received from ASI:One — no action needed


agent.include(_chat_proto, publish_manifest=True)


@agent.on_message(model=SearchRequest)
async def handle_search(ctx: Context, sender: str, msg: SearchRequest):
    user_id = _resolve_user_id(msg.user_id, sender)
    ctx.logger.info(f"Search request from {sender} (user_id={user_id})")

    normalized_query = (msg.query or "").strip().lower()
    if normalized_query in {"/google-auth", "google auth", "connect google"}:
        instructions = get_google_auth_message(user_id)
        await ctx.send(
            sender,
            SearchResponse(
                summary=instructions,
                session_id=user_id,
                error="" if instructions.startswith("Google is already connected") else instructions,
            ),
        )
        return

    try:
        result = await run_workflow(WorkflowInput(user_request=msg.query, user_id=user_id))
        error = ""
        if not result.sheet_url and "Google authorization required" in result.summary:
            error = result.summary
        await ctx.send(
            sender,
            SearchResponse(
                sheet_url=result.sheet_url,
                summary=result.summary,
                num_results=result.num_results,
                session_id=result.session_id or user_id,
                error=error,
            ),
        )
    except Exception as exc:
        ctx.logger.exception("Search workflow failed")
        await ctx.send(
            sender,
            SearchResponse(
                error=str(exc),
                session_id=user_id,
            ),
        )


@agent.on_message(model=FollowUpRequest)
async def handle_followup(ctx: Context, sender: str, msg: FollowUpRequest):
    user_id = _resolve_user_id(msg.user_id, sender)
    ctx.logger.info(f"Follow-up request from {sender} (user_id={user_id})")

    try:
        result = await resume_workflow(WorkflowInput(user_request=msg.query, user_id=user_id))
        await ctx.send(
            sender,
            SearchResponse(
                sheet_url=result.sheet_url,
                summary=result.summary,
                num_results=result.num_results,
                session_id=result.session_id or user_id,
                error="",
            ),
        )
    except Exception as exc:
        ctx.logger.exception("Follow-up workflow failed")
        await ctx.send(
            sender,
            SearchResponse(
                error=str(exc),
                session_id=user_id,
            ),
        )


if __name__ == "__main__":
    agent.run()
