"""Tests for the LangGraph interrupt integration.

Skipped automatically when `langgraph` is not installed (it's an optional
extra). Install with: `pip install paygraph[langgraph]`.
"""

import json
import tempfile
from typing import TypedDict
from unittest.mock import patch

import pytest

pytest.importorskip("langgraph")

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import Command  # noqa: E402

from paygraph.gateways.mock import MockGateway  # noqa: E402
from paygraph.gateways.slack import SlackApprovalGateway  # noqa: E402
from paygraph.policy import SpendPolicy  # noqa: E402
from paygraph.wallet import AgentWallet  # noqa: E402


class SpendState(TypedDict, total=False):
    amount: float
    vendor: str
    justification: str
    result: str


def _make_wallet_with_slack():
    f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    f.close()
    gateway = SlackApprovalGateway(
        webhook_url="https://hooks.slack.com/test",
        inner_gateway=MockGateway(auto_approve=True),
    )
    wallet = AgentWallet(
        gateways={"default": gateway},
        policy=SpendPolicy(require_human_approval_above=20.0),
        log_path=f.name,
        verbose=False,
    )
    return wallet, f.name


def _build_graph(wallet):
    """Build a minimal one-node graph that invokes the langgraph spend tool."""
    tool = wallet.langgraph_spend_tool

    def spend_node(state: SpendState) -> dict:
        result = tool.invoke(
            {
                "amount": state["amount"],
                "vendor": state["vendor"],
                "justification": state["justification"],
            }
        )
        return {"result": result}

    builder = StateGraph(SpendState)
    builder.add_node("spend", spend_node)
    builder.add_edge(START, "spend")
    builder.add_edge("spend", END)
    return builder.compile(checkpointer=InMemorySaver())


def _read_audit(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


class TestLangGraphSpendTool:
    def test_below_threshold_returns_card_without_interrupt(self):
        """Spend below the approval threshold runs through without pausing."""
        wallet, _ = _make_wallet_with_slack()
        graph = _build_graph(wallet)
        config = {"configurable": {"thread_id": "t-below"}}

        final = graph.invoke(
            {"amount": 10.0, "vendor": "Anthropic", "justification": "tokens"},
            config=config,
        )

        assert "Card approved" in final["result"]
        # No __interrupt__ key means the graph finished in one shot
        assert final.get("__interrupt__") is None or not final.get("__interrupt__")

    def test_above_threshold_pauses_with_request_id_payload(self):
        """Spend above threshold pauses via interrupt() with a checkpointed request_id."""
        wallet, _ = _make_wallet_with_slack()
        graph = _build_graph(wallet)
        config = {"configurable": {"thread_id": "t-pause"}}

        with patch("httpx.post"):
            state = graph.invoke(
                {"amount": 50.0, "vendor": "Anthropic", "justification": "tokens"},
                config=config,
            )

        interrupts = state.get("__interrupt__")
        assert interrupts, "graph should have paused with an __interrupt__"
        payload = interrupts[0].value
        assert payload["type"] == "paygraph_approval_required"
        assert payload["amount"] == 50.0
        assert payload["vendor"] == "Anthropic"
        assert payload["request_id"]
        assert payload["gateway_name"] == "default"

        # The checkpointer has the interrupt persisted — confirm it survives a
        # fresh state read (simulates a cold-start resume).
        checkpoint_state = graph.get_state(config)
        checkpoint_interrupts = checkpoint_state.tasks[0].interrupts
        assert checkpoint_interrupts[0].value["request_id"] == payload["request_id"]

    def test_resume_approved_returns_card_and_logs_approved(self):
        """Resuming with approved=True produces a card and an approved audit record."""
        wallet, audit_path = _make_wallet_with_slack()
        graph = _build_graph(wallet)
        config = {"configurable": {"thread_id": "t-approve"}}

        with patch("httpx.post"):
            graph.invoke(
                {
                    "amount": 50.0,
                    "vendor": "Anthropic",
                    "justification": "need tokens",
                },
                config=config,
            )
            final = graph.invoke(Command(resume={"approved": True}), config=config)

        assert "Card approved" in final["result"]
        records = _read_audit(audit_path)
        assert any(r["policy_result"] == "approved" for r in records)
        approved = next(r for r in records if r["policy_result"] == "approved")
        assert approved["vendor"] == "Anthropic"
        assert approved["amount"] == 50.0

    def test_resume_denied_returns_denial_message_and_logs_denied(self):
        """Resuming with approved=False returns a denial string and logs denial."""
        wallet, audit_path = _make_wallet_with_slack()
        graph = _build_graph(wallet)
        config = {"configurable": {"thread_id": "t-deny"}}

        with patch("httpx.post"):
            graph.invoke(
                {
                    "amount": 50.0,
                    "vendor": "Anthropic",
                    "justification": "need tokens",
                },
                config=config,
            )
            final = graph.invoke(Command(resume={"approved": False}), config=config)

        assert "Spend denied" in final["result"]
        records = _read_audit(audit_path)
        denied = [r for r in records if r["policy_result"] == "denied"]
        assert denied, "expected at least one denied audit record"
        assert "Human denied" in denied[-1]["denial_reason"]

    def test_policy_violation_returns_denial_message_without_interrupt(self):
        """A spend that fails policy checks (over max_transaction) returns a denial string."""
        f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        f.close()
        wallet = AgentWallet(
            gateways={
                "default": SlackApprovalGateway(
                    webhook_url="https://hooks.slack.com/test",
                    inner_gateway=MockGateway(auto_approve=True),
                ),
            },
            policy=SpendPolicy(
                max_transaction=25.0,
                require_human_approval_above=20.0,
            ),
            log_path=f.name,
            verbose=False,
        )
        graph = _build_graph(wallet)
        config = {"configurable": {"thread_id": "t-policy"}}

        final = graph.invoke(
            {"amount": 500.0, "vendor": "Anthropic", "justification": "tokens"},
            config=config,
        )

        assert "Spend denied" in final["result"]
        # Graph should have finished — no interrupt was issued
        assert final.get("__interrupt__") is None or not final.get("__interrupt__")
        records = _read_audit(f.name)
        assert any(r["policy_result"] == "denied" for r in records)

    def test_resume_with_bare_bool_is_accepted(self):
        """Command(resume=True) also works — the tool coerces to approved=True."""
        wallet, _ = _make_wallet_with_slack()
        graph = _build_graph(wallet)
        config = {"configurable": {"thread_id": "t-bool"}}

        with patch("httpx.post"):
            graph.invoke(
                {
                    "amount": 50.0,
                    "vendor": "Anthropic",
                    "justification": "need tokens",
                },
                config=config,
            )
            final = graph.invoke(Command(resume=True), config=config)

        assert "Card approved" in final["result"]
