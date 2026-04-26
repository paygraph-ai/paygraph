import json
import tempfile
from unittest.mock import MagicMock, patch

import httpx
import pytest

from paygraph.exceptions import GatewayError, HumanApprovalRequired, SpendDeniedError
from paygraph.gateways.base import VirtualCard
from paygraph.gateways.mock import MockGateway
from paygraph.gateways.slack import SlackApprovalGateway
from paygraph.policy import SpendPolicy
from paygraph.wallet import AgentWallet

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_slack_wallet(
    webhook_url: str = "https://hooks.slack.com/test",
    inner_gateway=None,
    policy=None,
    **kwargs,
) -> tuple[AgentWallet, str]:
    """Create a wallet backed by a SlackApprovalGateway. Returns (wallet, audit_path)."""
    f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    f.close()
    gateway = SlackApprovalGateway(
        webhook_url=webhook_url,
        inner_gateway=inner_gateway or MockGateway(auto_approve=True),
    )
    wallet = AgentWallet(
        gateways=gateway,
        policy=policy or SpendPolicy(require_human_approval_above=20.0),
        log_path=f.name,
        verbose=False,
        **kwargs,
    )
    return wallet, f.name


def _read_audit(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


# ---------------------------------------------------------------------------
# SlackApprovalGateway unit tests
# ---------------------------------------------------------------------------


class TestSlackApprovalGateway:
    def test_request_approval_raises_human_approval_required(self):
        """Posting to webhook raises HumanApprovalRequired with correct fields."""
        gateway = SlackApprovalGateway(
            webhook_url="https://hooks.slack.com/test",
            inner_gateway=MockGateway(auto_approve=True),
        )
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            with pytest.raises(HumanApprovalRequired) as exc_info:
                gateway.request_approval(5000, "Anthropic", "need tokens")

        err = exc_info.value
        assert err.amount == 50.0
        assert err.vendor == "Anthropic"
        assert err.request_id  # non-empty string

    def test_request_approval_posts_to_slack(self):
        """Slack webhook is called with correct URL and payload."""
        gateway = SlackApprovalGateway(
            webhook_url="https://hooks.slack.com/test",
            inner_gateway=MockGateway(auto_approve=True),
        )
        with patch("httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            with pytest.raises(HumanApprovalRequired):
                gateway.request_approval(5000, "Anthropic", "need tokens")

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[0][0] == "https://hooks.slack.com/test"
        payload = call_kwargs[1]["json"]
        assert "Anthropic" in payload["text"]
        assert "$50.00" in payload["text"]
        # No interactive attachments — incoming webhooks can't receive callbacks
        assert "attachments" not in payload

    def test_request_approval_wraps_webhook_error_as_gateway_error(self):
        """If Slack webhook POST fails, raises GatewayError not raw httpx exception."""
        from paygraph.exceptions import GatewayError

        gateway = SlackApprovalGateway(
            webhook_url="https://hooks.slack.com/test",
            inner_gateway=MockGateway(auto_approve=True),
        )
        with patch("httpx.post", side_effect=httpx.ConnectError("connection refused")):
            with pytest.raises(GatewayError, match="Slack webhook POST failed"):
                gateway.request_approval(5000, "Anthropic", "need tokens")

    def test_complete_spend_approved_returns_card(self):
        """approved=True delegates to inner gateway and returns a VirtualCard."""
        gateway = SlackApprovalGateway(
            webhook_url="https://hooks.slack.com/test",
            inner_gateway=MockGateway(auto_approve=True),
        )
        with patch("httpx.post"):
            with pytest.raises(HumanApprovalRequired) as exc_info:
                gateway.request_approval(5000, "Anthropic", "need tokens")

        request_id = exc_info.value.request_id
        card = gateway.complete_spend(request_id, approved=True)
        assert isinstance(card, VirtualCard)
        assert card.pan == "4111111111111111"

    def test_complete_spend_denied_raises_spend_denied_error(self):
        """approved=False raises SpendDeniedError."""
        gateway = SlackApprovalGateway(
            webhook_url="https://hooks.slack.com/test",
            inner_gateway=MockGateway(auto_approve=True),
        )
        with patch("httpx.post"):
            with pytest.raises(HumanApprovalRequired) as exc_info:
                gateway.request_approval(5000, "Anthropic", "need tokens")

        request_id = exc_info.value.request_id
        with pytest.raises(SpendDeniedError):
            gateway.complete_spend(request_id, approved=False)

    def test_unknown_request_id_raises_key_error(self):
        """complete_spend with an unknown ID raises KeyError."""
        gateway = SlackApprovalGateway(
            webhook_url="https://hooks.slack.com/test",
            inner_gateway=MockGateway(auto_approve=True),
        )
        with pytest.raises(KeyError):
            gateway.complete_spend("nonexistent_id", approved=True)

    def test_request_id_consumed_after_complete_spend(self):
        """Completing a spend removes it from pending — cannot resume twice."""
        gateway = SlackApprovalGateway(
            webhook_url="https://hooks.slack.com/test",
            inner_gateway=MockGateway(auto_approve=True),
        )
        with patch("httpx.post"):
            with pytest.raises(HumanApprovalRequired) as exc_info:
                gateway.request_approval(5000, "Anthropic", "need tokens")

        request_id = exc_info.value.request_id
        gateway.complete_spend(request_id, approved=True)
        with pytest.raises(KeyError):
            gateway.complete_spend(request_id, approved=True)


# ---------------------------------------------------------------------------
# AgentWallet integration tests
# ---------------------------------------------------------------------------


class TestWalletSlackFlow:
    def test_above_threshold_raises_human_approval_required(self):
        """request_spend above threshold fires Slack and raises HumanApprovalRequired."""
        wallet, _ = _make_slack_wallet()
        with patch("httpx.post"):
            with pytest.raises(HumanApprovalRequired) as exc_info:
                wallet.request_spend(50.0, "Anthropic", "need tokens")

        assert exc_info.value.amount == 50.0
        assert exc_info.value.vendor == "Anthropic"
        assert exc_info.value.request_id

    def test_below_threshold_skips_slack_approval(self):
        """request_spend at or below threshold goes straight to gateway — no HumanApprovalRequired."""
        wallet, _ = _make_slack_wallet()
        # threshold is 20.0, spend 10.0 — should succeed without Slack
        result = wallet.request_spend(10.0, "Anthropic", "cheap call")
        assert "Card approved" in result

    def test_above_threshold_audit_records_pending(self):
        """Audit log records policy_result='pending_approval' for Slack-gated spends."""
        wallet, path = _make_slack_wallet()
        with patch("httpx.post"):
            with pytest.raises(HumanApprovalRequired):
                wallet.request_spend(50.0, "Anthropic", "need tokens")

        records = _read_audit(path)
        assert len(records) == 1
        assert records[0]["policy_result"] == "pending_approval"

    def test_complete_spend_approved_returns_card_string(self):
        """complete_spend(approved=True) returns card details string."""
        wallet, _ = _make_slack_wallet()
        with patch("httpx.post"):
            with pytest.raises(HumanApprovalRequired) as exc_info:
                wallet.request_spend(50.0, "Anthropic", "need tokens")

        result = wallet.complete_spend(exc_info.value.request_id, approved=True)
        assert "Card approved" in result

    def test_complete_spend_denied_raises_spend_denied(self):
        """complete_spend(approved=False) raises SpendDeniedError."""
        wallet, _ = _make_slack_wallet()
        with patch("httpx.post"):
            with pytest.raises(HumanApprovalRequired) as exc_info:
                wallet.request_spend(50.0, "Anthropic", "need tokens")

        with pytest.raises(SpendDeniedError):
            wallet.complete_spend(exc_info.value.request_id, approved=False)

    def test_complete_spend_denial_writes_audit_record(self):
        """complete_spend(approved=False) writes a denial audit record with real metadata."""
        wallet, path = _make_slack_wallet()
        with patch("httpx.post"):
            with pytest.raises(HumanApprovalRequired) as exc_info:
                wallet.request_spend(50.0, "Anthropic", "need tokens for task")

        with pytest.raises(SpendDeniedError):
            wallet.complete_spend(exc_info.value.request_id, approved=False)

        records = _read_audit(path)
        denied = next(r for r in records if r["policy_result"] == "denied")
        assert denied["vendor"] == "Anthropic"
        assert denied["justification"] == "need tokens for task"
        assert denied["amount"] == 50.0
        assert "Human denied" in denied["denial_reason"]

    def test_complete_spend_without_slack_gateway_raises_gateway_error(self):
        """complete_spend raises GatewayError if gateway is not SlackApprovalGateway."""
        f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        f.close()
        wallet = AgentWallet(
            gateways=MockGateway(auto_approve=True),
            log_path=f.name,
            verbose=False,
        )
        with pytest.raises(GatewayError, match="SlackApprovalGateway"):
            wallet.complete_spend("some_id", approved=True)

    def test_no_threshold_configured_skips_slack(self):
        """Without require_human_approval_above, SlackApprovalGateway acts as pass-through."""
        wallet, _ = _make_slack_wallet(
            policy=SpendPolicy(require_human_approval_above=None)
        )
        # No threshold — should execute directly via inner gateway
        result = wallet.request_spend(50.0, "Anthropic", "need tokens")
        assert "Card approved" in result

    def test_complete_spend_commits_budget(self):
        """complete_spend(approved=True) must commit the spend to the daily budget."""
        from paygraph.exceptions import PolicyViolationError

        policy = SpendPolicy(
            max_transaction=100.0,
            daily_budget=60.0,
            require_human_approval_above=20.0,
        )
        wallet, _ = _make_slack_wallet(policy=policy)

        with patch("httpx.post"):
            with pytest.raises(HumanApprovalRequired) as exc_info:
                wallet.request_spend(50.0, "Anthropic", "need tokens")

        wallet.complete_spend(exc_info.value.request_id, approved=True)

        # Budget is now 50.0 / 60.0 — a second 50.0 exceeds the daily budget
        # and should be rejected by policy before even reaching Slack
        assert wallet.policy_engine._daily_spend == 50.0
        with pytest.raises(PolicyViolationError, match="Daily budget exhausted"):
            wallet.request_spend(50.0, "Anthropic", "need tokens again")

    def test_complete_spend_audit_has_correct_vendor_and_justification(self):
        """Audit record from complete_spend uses real vendor/justification, not placeholders."""
        wallet, path = _make_slack_wallet()

        with patch("httpx.post"):
            with pytest.raises(HumanApprovalRequired) as exc_info:
                wallet.request_spend(50.0, "Anthropic", "need tokens for task")

        wallet.complete_spend(exc_info.value.request_id, approved=True)

        records = _read_audit(path)
        approved_record = next(r for r in records if r["policy_result"] == "approved")
        assert approved_record["vendor"] == "Anthropic"
        assert approved_record["justification"] == "need tokens for task"
        assert approved_record["amount"] == 50.0


# ---------------------------------------------------------------------------
# TTL / expiry tests
# ---------------------------------------------------------------------------


class TestPendingTTL:
    def test_default_ttl_is_24_hours(self):
        gateway = SlackApprovalGateway(
            webhook_url="https://hooks.slack.com/test",
            inner_gateway=MockGateway(auto_approve=True),
        )
        assert gateway.pending_ttl_seconds == 24 * 60 * 60

    def test_expired_request_raises_spend_denied(self):
        """complete_spend on an expired request raises SpendDeniedError with timeout reason."""
        gateway = SlackApprovalGateway(
            webhook_url="https://hooks.slack.com/test",
            inner_gateway=MockGateway(auto_approve=True),
            pending_ttl_seconds=60,
        )
        with patch("httpx.post"):
            with pytest.raises(HumanApprovalRequired) as exc_info:
                gateway.request_approval(5000, "Anthropic", "need tokens")

        # Backdate the entry so it appears stale
        gateway._pending[exc_info.value.request_id]["created_at"] -= 120

        with pytest.raises(SpendDeniedError, match="timed out"):
            gateway.complete_spend(exc_info.value.request_id, approved=True)

    def test_ttl_none_disables_expiry(self):
        gateway = SlackApprovalGateway(
            webhook_url="https://hooks.slack.com/test",
            inner_gateway=MockGateway(auto_approve=True),
            pending_ttl_seconds=None,
        )
        with patch("httpx.post"):
            with pytest.raises(HumanApprovalRequired) as exc_info:
                gateway.request_approval(5000, "Anthropic", "need tokens")

        # Even with a very old created_at, no expiry triggers
        gateway._pending[exc_info.value.request_id]["created_at"] -= 10**9
        card = gateway.complete_spend(exc_info.value.request_id, approved=True)
        assert card.pan == "4111111111111111"

    def test_purge_expired_drops_stale_entries(self):
        gateway = SlackApprovalGateway(
            webhook_url="https://hooks.slack.com/test",
            inner_gateway=MockGateway(auto_approve=True),
            pending_ttl_seconds=60,
        )
        with patch("httpx.post"):
            with pytest.raises(HumanApprovalRequired) as fresh:
                gateway.request_approval(1000, "FreshVendor", "still valid")
            with pytest.raises(HumanApprovalRequired) as stale:
                gateway.request_approval(2000, "StaleVendor", "old entry")

        gateway._pending[stale.value.request_id]["created_at"] -= 120

        purged = gateway.purge_expired()
        assert purged == 1
        assert fresh.value.request_id in gateway._pending
        assert stale.value.request_id not in gateway._pending

    def test_purge_expired_noop_when_ttl_none(self):
        gateway = SlackApprovalGateway(
            webhook_url="https://hooks.slack.com/test",
            inner_gateway=MockGateway(auto_approve=True),
            pending_ttl_seconds=None,
        )
        with patch("httpx.post"):
            with pytest.raises(HumanApprovalRequired):
                gateway.request_approval(1000, "Anthropic", "memo")
        assert gateway.purge_expired() == 0
        assert len(gateway._pending) == 1

    def test_wallet_complete_spend_audits_timeout_with_real_metadata(self):
        """When wallet.complete_spend hits an expired request, audit captures the timeout reason."""
        gateway = SlackApprovalGateway(
            webhook_url="https://hooks.slack.com/test",
            inner_gateway=MockGateway(auto_approve=True),
            pending_ttl_seconds=60,
        )
        f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        f.close()
        wallet = AgentWallet(
            gateways=gateway,
            policy=SpendPolicy(require_human_approval_above=20.0),
            log_path=f.name,
            verbose=False,
        )

        with patch("httpx.post"):
            with pytest.raises(HumanApprovalRequired) as exc_info:
                wallet.request_spend(50.0, "Anthropic", "need tokens")

        request_id = exc_info.value.request_id
        gateway._pending[request_id]["created_at"] -= 120

        with pytest.raises(SpendDeniedError, match="timed out"):
            wallet.complete_spend(
                request_id, approved=True, gateway=exc_info.value.gateway_name
            )

        records = _read_audit(f.name)
        denied = next(r for r in records if r["policy_result"] == "denied")
        assert denied["vendor"] == "Anthropic"
        assert denied["justification"] == "need tokens"
        assert denied["amount"] == 50.0
        assert "timed out" in denied["denial_reason"]
