"""LangGraph interrupt integration for PayGraph's Slack approval flow.

The default PayGraph Slack approval path raises ``HumanApprovalRequired`` when a
spend needs a human decision. That works in any Python runtime, but inside a
LangGraph agent it blows past the node boundary — the checkpointer never
persists the pause, so a cold restart can't resume cleanly.

This module maps the exception onto LangGraph's native ``interrupt()``
primitive so the graph pauses *at a node boundary*, the ``request_id`` lives in
the checkpointed interrupt payload, and resume is the standard
``graph.invoke(Command(resume=...), config)`` pattern.

Resume payload shape::

    Command(resume={"approved": True})   # or False to deny

Caveat — LangGraph re-runs the node body from the top on resume, so any work
done *before* ``interrupt()`` (in our case ``wallet.request_spend``) runs twice.
The second call generates a fresh ``request_id`` and posts a second Slack
message; ``interrupt()`` then returns the human's decision immediately and the
fresh ``request_id`` is the one that gets completed. The first ``request_id``
is orphaned in the gateway's pending dict but not charged. The orphan is bounded
in memory by ``SlackApprovalGateway``'s TTL on pending approvals (24h default),
so it eventually auto-expires. For strict single-post semantics, split the flow
into two nodes with ``interrupt_before`` on the resume node.
"""

from __future__ import annotations

from paygraph.exceptions import (
    GatewayError,
    HumanApprovalRequired,
    PolicyViolationError,
    SpendDeniedError,
)


def build_langgraph_spend_tool(wallet):
    """Build a LangGraph-native spend tool that pauses via ``interrupt()``.

    Parallel to ``wallet.spend_tool`` but integrates with LangGraph's
    checkpointer instead of surfacing ``HumanApprovalRequired`` as a string to
    the LLM. Use this when the wallet's gateway is a ``SlackApprovalGateway``
    (or any gateway that raises ``HumanApprovalRequired``) and the agent runs
    inside a checkpoint-enabled LangGraph graph.

    Args:
        wallet: An ``AgentWallet`` instance.

    Returns:
        A ``langchain_core.tools`` ``@tool``-decorated function that agents
        can invoke. On below-threshold spends it executes normally. On
        above-threshold spends it calls ``interrupt()`` with a payload
        containing the ``request_id``, and resumes with ``complete_spend``
        once a ``Command(resume={"approved": bool})`` arrives.

    Raises:
        ImportError: If ``langchain-core`` or ``langgraph`` are not installed.
    """
    try:
        from langchain_core.tools import tool
        from langgraph.types import interrupt
    except ImportError as e:
        raise ImportError(
            "LangGraph interrupt integration requires langchain-core and "
            "langgraph. Install with: pip install paygraph[langgraph]"
        ) from e

    from pydantic import BaseModel, Field

    class SpendRequest(BaseModel):
        amount: float = Field(
            description="The exact dollar amount to spend (e.g. 4.20 for $4.20)"
        )
        vendor: str = Field(
            description="The name of the vendor or service to pay (e.g. 'Anthropic API')"
        )
        justification: str = Field(
            description="A detailed explanation of why this purchase is necessary to complete your task"
        )

    @tool("mint_virtual_card", args_schema=SpendRequest)
    def mint_virtual_card(amount: float, vendor: str, justification: str) -> str:
        """Use this tool when you need to spend money to complete your task. You must provide the exact dollar amount, the vendor name, and a detailed justification explaining why this purchase is necessary."""
        try:
            return wallet.request_spend(amount, vendor, justification)
        except HumanApprovalRequired as e:
            human_decision = interrupt(
                {
                    "type": "paygraph_approval_required",
                    "request_id": e.request_id,
                    "gateway_name": e.gateway_name,
                    "amount": e.amount,
                    "vendor": e.vendor,
                    "justification": justification,
                }
            )
            approved = (
                bool(human_decision.get("approved", False))
                if isinstance(human_decision, dict)
                else bool(human_decision)
            )
            try:
                return wallet.complete_spend(
                    e.request_id, approved=approved, gateway=e.gateway_name
                )
            except (SpendDeniedError, GatewayError, PolicyViolationError) as err:
                return f"Spend denied: {err}"
        except (PolicyViolationError, SpendDeniedError, GatewayError) as e:
            return f"Spend denied: {e}"

    return mint_virtual_card
