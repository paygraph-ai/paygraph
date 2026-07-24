"""LangGraph interrupt integration for PayGraph's Slack approval flow.

The default PayGraph Slack approval path raises ``HumanApprovalRequired`` when a
spend needs a human decision. That works in any Python runtime, but inside a
LangGraph agent it blows past the node boundary — the checkpointer never
persists the pause, so a cold restart can't resume cleanly.

Two integration styles are provided:

``build_langgraph_spend_tool`` (single-node)
    Maps ``HumanApprovalRequired`` onto LangGraph's ``interrupt()`` inside one
    tool call. Simple to wire into existing ReAct agents, but LangGraph re-runs
    the node body on resume so ``request_spend`` executes twice — a second Slack
    post and an orphaned ``request_id``. Acceptable for demos; not recommended
    for production Slack flows.

``build_langgraph_spend_graph`` (two-node, recommended)
    Splits the flow into ``request`` and ``complete`` nodes with
    ``interrupt_before=["complete"]``. ``request_spend`` runs exactly once;
    the graph pauses before ``complete_spend``, so resume does not re-check
    policy or post a second Slack message. Use this with ``SlackListener`` or
    ``Command(resume=...)``.

Resume payload shape::

    Command(resume={"approved": True})   # or False to deny
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from paygraph.exceptions import (
    GatewayError,
    HumanApprovalRequired,
    PolicyViolationError,
    SpendDeniedError,
    UnknownApprovalError,
)

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver


class PaygraphSpendState(TypedDict, total=False):
    """State schema for :func:`build_langgraph_spend_graph`."""

    amount: float
    vendor: str
    justification: str
    request_id: str
    gateway_name: str
    needs_approval: bool
    result: str


def _coerce_approved(human_decision) -> bool:
    """Return True only for explicit boolean approval in the resume payload."""
    if isinstance(human_decision, bool):
        return human_decision
    if isinstance(human_decision, dict):
        return human_decision.get("approved") is True
    return False


def _approval_interrupt_payload(
    *,
    request_id: str,
    gateway_name: str,
    amount: float,
    vendor: str,
    justification: str,
) -> dict:
    return {
        "type": "paygraph_approval_required",
        "request_id": request_id,
        "gateway_name": gateway_name,
        "amount": amount,
        "vendor": vendor,
        "justification": justification,
    }


def build_langgraph_spend_nodes(wallet):
    """Build request/complete node callables for a two-node approval graph.

    Use with :func:`build_langgraph_spend_graph` or wire into a larger graph.
    The ``request`` node calls ``wallet.request_spend()`` once. When human
    approval is required it stores ``request_id`` in state instead of raising.
    The ``complete`` node calls ``interrupt()`` then ``wallet.complete_spend()``.

    Args:
        wallet: An ``AgentWallet`` instance.

    Returns:
        ``(request_node, complete_node)`` callables typed for
        :class:`PaygraphSpendState`.

    Raises:
        ImportError: If ``langgraph`` is not installed.
    """
    try:
        from langgraph.types import interrupt
    except ImportError as e:
        raise ImportError(
            "LangGraph interrupt integration requires langgraph. "
            "Install with: pip install paygraph[langgraph]"
        ) from e

    def request_node(state: PaygraphSpendState) -> dict:
        # Paused mid-flow — complete node owns the next step.
        if state.get("needs_approval") and not state.get("result"):
            return {}
        try:
            result = wallet.request_spend(
                state["amount"], state["vendor"], state["justification"]
            )
            return {"result": result, "needs_approval": False}
        except HumanApprovalRequired as e:
            return {
                "request_id": e.request_id,
                "gateway_name": e.gateway_name,
                "amount": e.amount,
                "vendor": e.vendor,
                "justification": state["justification"],
                "needs_approval": True,
            }
        except (PolicyViolationError, SpendDeniedError, GatewayError) as err:
            return {"result": f"Spend denied: {err}", "needs_approval": False}

    def complete_node(state: PaygraphSpendState) -> dict:
        if not state.get("needs_approval") or state.get("result"):
            return {}
        human_decision = interrupt(
            _approval_interrupt_payload(
                request_id=state["request_id"],
                gateway_name=state["gateway_name"],
                amount=state["amount"],
                vendor=state["vendor"],
                justification=state["justification"],
            )
        )
        approved = _coerce_approved(human_decision)
        try:
            result = wallet.complete_spend(
                state["request_id"],
                approved=approved,
                gateway=state["gateway_name"],
            )
            return {"result": result, "needs_approval": False}
        except UnknownApprovalError:
            # SlackListener (or another path) already resolved this request_id.
            return {
                "result": "Spend already completed.",
                "needs_approval": False,
            }
        except (SpendDeniedError, GatewayError, PolicyViolationError) as err:
            return {"result": f"Spend denied: {err}", "needs_approval": False}

    return request_node, complete_node


def build_langgraph_spend_graph(
    wallet,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Build a checkpointed two-node graph for Slack approval flows.

  ``request_spend`` runs in the first node; the graph pauses before
    ``complete_spend`` via ``interrupt_before``. This avoids the re-run
    caveats of :func:`build_langgraph_spend_tool`.

    Args:
        wallet: An ``AgentWallet`` instance.
        checkpointer: LangGraph checkpointer (required for pause/resume).
            Defaults to ``InMemorySaver()`` when ``None``.

    Returns:
        A compiled ``StateGraph`` ready to ``invoke()``.

    Raises:
        ImportError: If ``langgraph`` is not installed.
    """
    try:
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, StateGraph
    except ImportError as e:
        raise ImportError(
            "LangGraph interrupt integration requires langgraph. "
            "Install with: pip install paygraph[langgraph]"
        ) from e

    request_node, complete_node = build_langgraph_spend_nodes(wallet)

    def route_after_request(state: PaygraphSpendState) -> str:
        if state.get("needs_approval") and not state.get("result"):
            return "complete"
        return END

    builder = StateGraph(PaygraphSpendState)
    builder.add_node("request", request_node)
    builder.add_node("complete", complete_node)
    builder.add_edge(START, "request")
    builder.add_conditional_edges(
        "request", route_after_request, {"complete": "complete", END: END}
    )
    builder.add_edge("complete", END)

    if checkpointer is None:
        checkpointer = InMemorySaver()

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["complete"],
    )


def build_langgraph_spend_tool(wallet):
    """Build a LangGraph-native spend tool that pauses via ``interrupt()``.

    Parallel to ``wallet.spend_tool`` but integrates with LangGraph's
    checkpointer instead of surfacing ``HumanApprovalRequired`` as a string to
    the LLM. Prefer :func:`build_langgraph_spend_graph` for production Slack
    flows — this single-node helper re-runs ``request_spend`` on resume.

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
                _approval_interrupt_payload(
                    request_id=e.request_id,
                    gateway_name=e.gateway_name,
                    amount=e.amount,
                    vendor=e.vendor,
                    justification=justification,
                )
            )
            approved = _coerce_approved(human_decision)
            try:
                return wallet.complete_spend(
                    e.request_id, approved=approved, gateway=e.gateway_name
                )
            except (SpendDeniedError, GatewayError, PolicyViolationError) as err:
                return f"Spend denied: {err}"
        except (PolicyViolationError, SpendDeniedError, GatewayError) as e:
            return f"Spend denied: {e}"

    return mint_virtual_card
