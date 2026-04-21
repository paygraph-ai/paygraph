from datetime import date
from unittest.mock import patch

from paygraph.policy import PolicyEngine, SpendPolicy


class TestAmountCap:
    def test_under_limit_passes(self):
        engine = PolicyEngine(SpendPolicy(max_transaction=50.0))
        result = engine.evaluate(49.99, "vendor", "reason")
        assert result.approved
        assert "amount_cap" in result.checks_passed

    def test_exact_limit_passes(self):
        engine = PolicyEngine(SpendPolicy(max_transaction=50.0))
        result = engine.evaluate(50.0, "vendor", "reason")
        assert result.approved

    def test_over_limit_denied(self):
        engine = PolicyEngine(SpendPolicy(max_transaction=50.0))
        result = engine.evaluate(50.01, "vendor", "reason")
        assert not result.approved
        assert "exceeds limit" in result.denial_reason
        assert "$50.01" in result.denial_reason
        assert "$50.00" in result.denial_reason

    def test_amount_cap_is_second_check(self):
        engine = PolicyEngine(
            SpendPolicy(max_transaction=10.0, blocked_vendors=["vendor"])
        )
        result = engine.evaluate(100.0, "vendor", "reason")
        assert not result.approved
        assert "exceeds limit" in result.denial_reason
        assert result.checks_passed == ["positive_amount"]


class TestPositiveAmount:
    def test_positive_amount_passes(self):
        engine = PolicyEngine(SpendPolicy())
        result = engine.evaluate(5.0, "vendor", "reason")
        assert result.approved
        assert "positive_amount" in result.checks_passed

    def test_zero_amount_denied(self):
        engine = PolicyEngine(SpendPolicy())
        result = engine.evaluate(0.0, "vendor", "reason")
        assert not result.approved
        assert "must be positive" in result.denial_reason
        assert result.checks_passed == []

    def test_negative_amount_denied(self):
        engine = PolicyEngine(SpendPolicy())
        result = engine.evaluate(-10.0, "vendor", "reason")
        assert not result.approved
        assert "must be positive" in result.denial_reason

    def test_positive_amount_is_first_check(self):
        # Even if amount exceeds cap, positive check runs first
        engine = PolicyEngine(SpendPolicy(max_transaction=5.0))
        result = engine.evaluate(-10.0, "vendor", "reason")
        assert "must be positive" in result.denial_reason
        assert result.checks_passed == []


class TestVendorAllowlist:
    def test_none_allows_any(self):
        engine = PolicyEngine(SpendPolicy(allowed_vendors=None))
        result = engine.evaluate(5.0, "anything", "reason")
        assert result.approved

    def test_match_passes(self):
        engine = PolicyEngine(SpendPolicy(allowed_vendors=["anthropic"]))
        result = engine.evaluate(5.0, "Anthropic API", "reason")
        assert result.approved
        assert "vendor_allowlist" in result.checks_passed

    def test_no_match_denied(self):
        engine = PolicyEngine(SpendPolicy(allowed_vendors=["anthropic"]))
        result = engine.evaluate(5.0, "OpenAI", "reason")
        assert not result.approved
        assert "not in the allowed list" in result.denial_reason

    def test_case_insensitive(self):
        engine = PolicyEngine(SpendPolicy(allowed_vendors=["Anthropic"]))
        result = engine.evaluate(5.0, "ANTHROPIC API", "reason")
        assert result.approved

    def test_substring_match(self):
        engine = PolicyEngine(SpendPolicy(allowed_vendors=["anthropic"]))
        result = engine.evaluate(5.0, "Anthropic API Credits", "reason")
        assert result.approved


class TestVendorBlocklist:
    def test_none_blocks_nothing(self):
        engine = PolicyEngine(SpendPolicy(blocked_vendors=None))
        result = engine.evaluate(5.0, "anything", "reason")
        assert result.approved

    def test_blocked_vendor_denied(self):
        engine = PolicyEngine(SpendPolicy(blocked_vendors=["doordash"]))
        result = engine.evaluate(5.0, "DoorDash", "reason")
        assert not result.approved
        assert "blocked" in result.denial_reason

    def test_unblocked_vendor_passes(self):
        engine = PolicyEngine(SpendPolicy(blocked_vendors=["doordash"]))
        result = engine.evaluate(5.0, "Anthropic", "reason")
        assert result.approved
        assert "vendor_blocklist" in result.checks_passed

    def test_case_insensitive(self):
        engine = PolicyEngine(SpendPolicy(blocked_vendors=["DoorDash"]))
        result = engine.evaluate(5.0, "doordash delivery", "reason")
        assert not result.approved


class TestDailyBudget:
    def test_single_spend_under_budget(self):
        engine = PolicyEngine(SpendPolicy(max_transaction=100.0, daily_budget=100.0))
        result = engine.evaluate(99.0, "vendor", "reason")
        assert result.approved
        assert "daily_budget" in result.checks_passed

    def test_accumulation_exceeds_budget(self):
        engine = PolicyEngine(SpendPolicy(max_transaction=100.0, daily_budget=100.0))
        r1 = engine.evaluate(60.0, "vendor", "reason")
        assert r1.approved
        engine.commit_spend(60.0)
        r2 = engine.evaluate(50.0, "vendor", "reason")
        assert not r2.approved
        assert "Daily budget exhausted" in r2.denial_reason
        assert "$60.00" in r2.denial_reason
        assert "$100.00" in r2.denial_reason

    def test_exact_budget_passes(self):
        engine = PolicyEngine(SpendPolicy(max_transaction=100.0, daily_budget=100.0))
        result = engine.evaluate(100.0, "vendor", "reason")
        assert result.approved

    def test_denied_spend_does_not_count(self):
        engine = PolicyEngine(SpendPolicy(max_transaction=50.0, daily_budget=100.0))
        # This is denied by amount cap, should not count toward budget
        r1 = engine.evaluate(999.0, "vendor", "reason")
        assert not r1.approved
        # Budget should still be full
        r2 = engine.evaluate(50.0, "vendor", "reason")
        assert r2.approved

    def test_resets_on_new_day(self):
        engine = PolicyEngine(SpendPolicy(max_transaction=100.0, daily_budget=100.0))
        engine.evaluate(80.0, "vendor", "reason")
        engine.commit_spend(80.0)

        # Simulate next day
        tomorrow = date(2099, 1, 2)
        with patch("paygraph.policy.date") as mock_date:
            mock_date.today.return_value = tomorrow
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = engine.evaluate(80.0, "vendor", "reason")
            assert result.approved


class TestCommitSpend:
    def test_evaluate_does_not_increment_before_commit(self):
        engine = PolicyEngine(SpendPolicy(max_transaction=100.0, daily_budget=100.0))
        result = engine.evaluate(60.0, "vendor", "reason")
        assert result.approved
        # Without commit, budget is still 0 — second call still passes
        result2 = engine.evaluate(60.0, "vendor", "reason")
        assert result2.approved

    def test_commit_spend_increments_budget(self):
        engine = PolicyEngine(SpendPolicy(max_transaction=100.0, daily_budget=100.0))
        engine.evaluate(60.0, "vendor", "reason")
        engine.commit_spend(60.0)
        # Now budget is consumed — next call should fail
        result = engine.evaluate(50.0, "vendor", "reason")
        assert not result.approved
        assert "Daily budget exhausted" in result.denial_reason


class TestJustification:
    def test_required_and_present(self):
        engine = PolicyEngine(SpendPolicy(require_justification=True))
        result = engine.evaluate(5.0, "vendor", "I need this")
        assert result.approved
        assert "justification" in result.checks_passed

    def test_required_and_missing(self):
        engine = PolicyEngine(SpendPolicy(require_justification=True))
        result = engine.evaluate(5.0, "vendor", "")
        assert not result.approved
        assert "Justification is required" in result.denial_reason

    def test_required_and_none(self):
        engine = PolicyEngine(SpendPolicy(require_justification=True))
        result = engine.evaluate(5.0, "vendor", None)
        assert not result.approved

    def test_not_required_and_missing(self):
        engine = PolicyEngine(SpendPolicy(require_justification=False))
        result = engine.evaluate(5.0, "vendor", "")
        assert result.approved


class TestCheckOrdering:
    def test_all_checks_pass(self):
        engine = PolicyEngine(SpendPolicy())
        result = engine.evaluate(5.0, "vendor", "reason")
        assert result.approved
        assert result.checks_passed == [
            "positive_amount",
            "amount_cap",
            "vendor_allowlist",
            "vendor_blocklist",
            "mcc_filter",
            "daily_budget",
            "justification",
        ]

    def test_first_failure_stops_checks(self):
        engine = PolicyEngine(
            SpendPolicy(max_transaction=1.0, blocked_vendors=["vendor"])
        )
        result = engine.evaluate(10.0, "vendor", "reason")
        # Amount cap fails second (after positive_amount), so blocked vendor check never runs
        assert "exceeds limit" in result.denial_reason
        assert result.checks_passed == ["positive_amount"]
