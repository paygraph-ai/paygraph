"""Comprehensive tests for time-based spending policies.

This module tests the enhanced PolicyEngine with hourly, weekly, and monthly
spending limits, including rollover functionality and edge cases.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, Mock

from paygraph.policy import PolicyEngine, SpendPolicy
from paygraph.time_periods import (
    PeriodTracker, TimePeriod,
    get_hourly_period, get_daily_period, get_weekly_period, get_monthly_period,
)


class TestPeriodCalculator:
    """Test period boundary calculations."""
    
    def test_hourly_period_boundaries(self):
        """Test hourly period calculation."""
        dt = datetime(2024, 1, 15, 14, 30, 45)
        start, end = get_hourly_period(dt)
        
        assert start == datetime(2024, 1, 15, 14, 0, 0)
        assert end == datetime(2024, 1, 15, 15, 0, 0)
    
    def test_daily_period_boundaries(self):
        """Test daily period calculation."""
        dt = datetime(2024, 1, 15, 14, 30, 45)
        start, end = get_daily_period(dt)
        
        assert start == datetime(2024, 1, 15, 0, 0, 0)
        assert end == datetime(2024, 1, 16, 0, 0, 0)
    
    def test_weekly_period_boundaries(self):
        """Test weekly period calculation (Monday start)."""
        # Test Wednesday (2024-01-17 is a Wednesday)
        dt = datetime(2024, 1, 17, 14, 30, 45)
        start, end = get_weekly_period(dt)

        # Should start on Monday (2024-01-15)
        assert start == datetime(2024, 1, 15, 0, 0, 0)
        assert end == datetime(2024, 1, 22, 0, 0, 0)

        # Test Monday itself
        dt = datetime(2024, 1, 15, 10, 0, 0)
        start, end = get_weekly_period(dt)
        
        assert start == datetime(2024, 1, 15, 0, 0, 0)
        assert end == datetime(2024, 1, 22, 0, 0, 0)
    
    def test_monthly_period_boundaries(self):
        """Test monthly period calculation."""
        # Test middle of January
        dt = datetime(2024, 1, 15, 14, 30, 45)
        start, end = get_monthly_period(dt)

        assert start == datetime(2024, 1, 1, 0, 0, 0)
        assert end == datetime(2024, 2, 1, 0, 0, 0)

        # Test December (year rollover)
        dt = datetime(2024, 12, 15, 14, 30, 45)
        start, end = get_monthly_period(dt)
        
        assert start == datetime(2024, 12, 1, 0, 0, 0)
        assert end == datetime(2025, 1, 1, 0, 0, 0)


class TestTimePeriod:
    """Test TimePeriod dataclass functionality."""
    
    def test_effective_budget_calculation(self):
        """Test effective budget includes rollover."""
        period = TimePeriod(
            period_type="hourly",
            start_time=datetime(2024, 1, 15, 14, 0, 0),
            end_time=datetime(2024, 1, 15, 15, 0, 0),
            budget_limit=100.0,
            rollover_amount=25.0,
            spent_amount=30.0
        )
        
        assert period.effective_budget == 125.0  # 100 + 25
        assert period.remaining_budget == 95.0   # 125 - 30
    
    def test_remaining_budget_never_negative(self):
        """Test remaining budget is never negative."""
        period = TimePeriod(
            period_type="hourly",
            start_time=datetime(2024, 1, 15, 14, 0, 0),
            end_time=datetime(2024, 1, 15, 15, 0, 0),
            budget_limit=100.0,
            rollover_amount=0.0,
            spent_amount=150.0  # Over budget
        )
        
        assert period.remaining_budget == 0.0
    
    def test_period_id_generation(self):
        """Test period ID generation."""
        period = TimePeriod(
            period_type="hourly",
            start_time=datetime(2024, 1, 15, 14, 0, 0),
            end_time=datetime(2024, 1, 15, 15, 0, 0),
            budget_limit=100.0
        )
        
        assert period.period_id == "hourly_20240115_14"


class TestPeriodTracker:
    """Test PeriodTracker functionality."""
    
    def test_get_current_period_creates_new(self):
        """Test getting current period creates new period."""
        tracker = PeriodTracker()
        current_time = datetime(2024, 1, 15, 14, 30, 0)
        
        period = tracker.get_current_period("hourly", 100.0, current_time)
        
        assert period.period_type == "hourly"
        assert period.budget_limit == 100.0
        assert period.start_time == datetime(2024, 1, 15, 14, 0, 0)
        assert period.end_time == datetime(2024, 1, 15, 15, 0, 0)
    
    def test_get_current_period_reuses_existing(self):
        """Test getting current period reuses existing period."""
        tracker = PeriodTracker()
        current_time = datetime(2024, 1, 15, 14, 30, 0)
        
        period1 = tracker.get_current_period("hourly", 100.0, current_time)
        period2 = tracker.get_current_period("hourly", 100.0, current_time)
        
        assert period1 is period2
    
    def test_is_period_expired(self):
        """Test period expiration detection."""
        tracker = PeriodTracker()
        
        period = TimePeriod(
            period_type="hourly",
            start_time=datetime(2024, 1, 15, 14, 0, 0),
            end_time=datetime(2024, 1, 15, 15, 0, 0),
            budget_limit=100.0
        )
        
        # Before expiration
        assert not tracker.is_period_expired(period, datetime(2024, 1, 15, 14, 30, 0))
        
        # At expiration
        assert tracker.is_period_expired(period, datetime(2024, 1, 15, 15, 0, 0))
        
        # After expiration
        assert tracker.is_period_expired(period, datetime(2024, 1, 15, 16, 0, 0))
    
    def test_calculate_rollover_amount(self):
        """Test rollover amount calculation."""
        tracker = PeriodTracker()
        
        # Period with unused budget
        period = TimePeriod(
            period_type="hourly",
            start_time=datetime(2024, 1, 15, 14, 0, 0),
            end_time=datetime(2024, 1, 15, 15, 0, 0),
            budget_limit=100.0,
            rollover_amount=0.0,
            spent_amount=75.0
        )
        
        rollover = tracker.calculate_rollover_amount(period)
        assert rollover == 25.0  # 100 - 75
        
        # Period with no unused budget
        period.spent_amount = 100.0
        rollover = tracker.calculate_rollover_amount(period)
        assert rollover == 0.0
        
        # Period over budget
        period.spent_amount = 120.0
        rollover = tracker.calculate_rollover_amount(period)
        assert rollover == 0.0
    
    def test_record_spending(self):
        """Test recording spending in a period."""
        tracker = PeriodTracker()
        
        period = TimePeriod(
            period_type="hourly",
            start_time=datetime(2024, 1, 15, 14, 0, 0),
            end_time=datetime(2024, 1, 15, 15, 0, 0),
            budget_limit=100.0,
            spent_amount=20.0
        )
        
        tracker.record_spending(period, 15.0)
        assert period.spent_amount == 35.0
    
    def test_apply_rollover(self):
        """Test applying rollover to a period."""
        tracker = PeriodTracker()
        
        period = TimePeriod(
            period_type="hourly",
            start_time=datetime(2024, 1, 15, 15, 0, 0),
            end_time=datetime(2024, 1, 15, 16, 0, 0),
            budget_limit=100.0,
            rollover_amount=0.0
        )
        
        tracker.apply_rollover(period, 25.0)
        assert period.rollover_amount == 25.0
        assert period.effective_budget == 125.0
    
    def test_cleanup_old_periods(self):
        """Test cleanup of old periods."""
        tracker = PeriodTracker()
        current_time = datetime(2024, 2, 15, 14, 0, 0)
        
        # Create some old periods manually
        old_time = datetime(2024, 1, 1, 14, 0, 0)
        recent_time = datetime(2024, 2, 10, 14, 0, 0)
        
        old_period = tracker.get_current_period("hourly", 100.0, old_time)
        recent_period = tracker.get_current_period("hourly", 100.0, recent_time)
        
        # Should have 2 periods
        assert len(tracker._periods) == 2
        
        # Cleanup with 10 day retention
        tracker.cleanup_old_periods(current_time, keep_days=10)
        
        # Should only have the recent period
        assert len(tracker._periods) == 1
        assert recent_period.period_id in tracker._periods


class TestTimeBudgetPolicyBasics:
    """Test basic time-based budget functionality."""
    
    def test_hourly_budget_enforcement(self):
        """Test hourly budget is enforced."""
        policy = SpendPolicy(
            max_transaction=100.0,
            daily_budget=1000.0,
            hourly_budget=50.0
        )
        engine = PolicyEngine(policy)
        
        # First transaction should pass
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 14, 30, 0)
            mock_policy_dt.now.return_value = datetime(2024, 1, 15, 14, 30, 0)
            result = engine.evaluate(30.0, "vendor", "reason")
            assert result.approved
            assert "hourly_budget" in result.checks_passed
            engine.commit_spend(30.0)
        
        # Second transaction should fail (would exceed hourly budget)
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 14, 45, 0)
            mock_policy_dt.now.return_value = datetime(2024, 1, 15, 14, 45, 0)
            result = engine.evaluate(25.0, "vendor", "reason")
        
        assert not result.approved
        assert "Hourly budget exhausted" in result.denial_reason
        assert "hourly_budget" not in result.checks_passed
    
    def test_weekly_budget_enforcement(self):
        """Test weekly budget is enforced."""
        policy = SpendPolicy(
            max_transaction=200.0,
            daily_budget=1000.0,
            weekly_budget=200.0
        )
        engine = PolicyEngine(policy)
        
        # First transaction should pass
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 14, 30, 0)  # Monday
            mock_policy_dt.now.return_value = datetime(2024, 1, 15, 14, 30, 0)
            result = engine.evaluate(150.0, "vendor", "reason")
            assert result.approved
            assert "weekly_budget" in result.checks_passed
            engine.commit_spend(150.0)
        
        # Second transaction should fail (would exceed weekly budget)
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = datetime(2024, 1, 17, 10, 0, 0)  # Wednesday
            mock_policy_dt.now.return_value = datetime(2024, 1, 17, 10, 0, 0)
            result = engine.evaluate(75.0, "vendor", "reason")
        
        assert not result.approved
        assert "Weekly budget exhausted" in result.denial_reason
    
    def test_monthly_budget_enforcement(self):
        """Test monthly budget is enforced."""
        policy = SpendPolicy(
            max_transaction=1500.0,
            daily_budget=2000.0,
            monthly_budget=1500.0
        )
        engine = PolicyEngine(policy)
        
        # First transaction should pass
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 14, 30, 0)
            mock_policy_dt.now.return_value = datetime(2024, 1, 15, 14, 30, 0)
            result = engine.evaluate(1200.0, "vendor", "reason")
            assert result.approved
            assert "monthly_budget" in result.checks_passed
            engine.commit_spend(1200.0)
        
        # Second transaction should fail (would exceed monthly budget)
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = datetime(2024, 1, 25, 10, 0, 0)
            mock_policy_dt.now.return_value = datetime(2024, 1, 25, 10, 0, 0)
            result = engine.evaluate(400.0, "vendor", "reason")
        
        assert not result.approved
        assert "Monthly budget exhausted" in result.denial_reason
    
    def test_no_time_budget_configured_passes(self):
        """Test that unconfigured time budgets are skipped."""
        policy = SpendPolicy(
            max_transaction=100.0,
            daily_budget=1000.0
            # No time-based budgets configured
        )
        engine = PolicyEngine(policy)
        
        result = engine.evaluate(50.0, "vendor", "reason")
        
        assert result.approved
        # All time-based checks should pass (they're skipped)
        assert "hourly_budget" in result.checks_passed
        assert "weekly_budget" in result.checks_passed
        assert "monthly_budget" in result.checks_passed


class TestRolloverFunctionality:
    """Test rollover functionality."""
    
    def test_simple_rollover_daily_to_weekly(self):
        """Test simple rollover from one period to next."""
        policy = SpendPolicy(
            max_transaction=600.0,
            daily_budget=1000.0,
            weekly_budget=500.0,
            enable_rollover=True,
            rollover_periods=["weekly"]
        )
        engine = PolicyEngine(policy)
        
        # Spend 60 in first week
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 14, 0, 0)  # Monday
            mock_policy_dt.now.return_value = datetime(2024, 1, 15, 14, 0, 0)
            result = engine.evaluate(60.0, "vendor", "reason")
            assert result.approved
            engine.commit_spend(60.0)
        
        # Move to next week - should have rollover from previous week
        # 440 rollover + 500 new budget = 940 total
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = datetime(2024, 1, 22, 10, 0, 0)  # Next Monday
            mock_policy_dt.now.return_value = datetime(2024, 1, 22, 10, 0, 0)
            result = engine.evaluate(530.0, "vendor", "reason")
            assert result.approved
            engine.commit_spend(530.0)
        
        # 940 - 530 = 410 remaining, so 420 should fail
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = datetime(2024, 1, 22, 11, 0, 0)
            mock_policy_dt.now.return_value = datetime(2024, 1, 22, 11, 0, 0)
            result = engine.evaluate(420.0, "vendor", "reason")
        
        assert not result.approved
        assert "Weekly budget exhausted" in result.denial_reason
    
    def test_rollover_disabled(self):
        """Test that rollover doesn't happen when disabled."""
        policy = SpendPolicy(
            max_transaction=600.0,
            daily_budget=1000.0,
            weekly_budget=500.0,
            enable_rollover=False
        )
        engine = PolicyEngine(policy)
        
        # Spend 60 in first week
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 14, 0, 0)
            mock_policy_dt.now.return_value = datetime(2024, 1, 15, 14, 0, 0)
            result = engine.evaluate(60.0, "vendor", "reason")
            assert result.approved
            engine.commit_spend(60.0)
        
        # Move to next week - should not have rollover, only 500 budget
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = datetime(2024, 1, 22, 10, 0, 0)
            mock_policy_dt.now.return_value = datetime(2024, 1, 22, 10, 0, 0)
            result = engine.evaluate(510.0, "vendor", "reason")
        
        assert not result.approved
        assert "Weekly budget exhausted" in result.denial_reason
    
    def test_rollover_period_not_in_list(self):
        """Test rollover doesn't happen for periods not in rollover_periods."""
        policy = SpendPolicy(
            max_transaction=600.0,
            daily_budget=1000.0,
            weekly_budget=500.0,
            enable_rollover=True,
            rollover_periods=["monthly"]  # Only monthly, not weekly
        )
        engine = PolicyEngine(policy)
        
        # Spend 60 in first week
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 14, 0, 0)
            mock_policy_dt.now.return_value = datetime(2024, 1, 15, 14, 0, 0)
            result = engine.evaluate(60.0, "vendor", "reason")
            assert result.approved
            engine.commit_spend(60.0)
        
        # Move to next week - should not have rollover for weekly
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = datetime(2024, 1, 22, 10, 0, 0)
            mock_policy_dt.now.return_value = datetime(2024, 1, 22, 10, 0, 0)
            result = engine.evaluate(510.0, "vendor", "reason")
        
        assert not result.approved


class TestBackwardCompatibility:
    """Test that existing functionality still works."""
    
    def test_existing_daily_budget_still_works(self):
        """Test that existing daily budget functionality is unchanged."""
        policy = SpendPolicy(
            max_transaction=200.0,
            daily_budget=200.0
        )
        engine = PolicyEngine(policy)
        
        # First transaction should pass
        result = engine.evaluate(150.0, "vendor", "reason")
        assert result.approved
        assert "daily_budget" in result.checks_passed
        engine.commit_spend(150.0)
        
        # Second transaction should fail (would exceed daily budget)
        result = engine.evaluate(75.0, "vendor", "reason")
        assert not result.approved
        assert "Daily budget exhausted" in result.denial_reason
    
    def test_existing_policy_fields_unchanged(self):
        """Test that all existing policy fields work as before."""
        policy = SpendPolicy(
            max_transaction=50.0,
            daily_budget=200.0,
            allowed_vendors=["openai"],
            blocked_vendors=["gambling"],
            require_justification=True
        )
        engine = PolicyEngine(policy)
        
        # Test transaction limit
        result = engine.evaluate(75.0, "openai", "reason")
        assert not result.approved
        assert "exceeds limit" in result.denial_reason
        
        # Test blocked vendor (note: allowlist is checked first, so this will fail allowlist)
        result = engine.evaluate(25.0, "gambling", "reason")
        assert not result.approved
        assert ("is not in the allowed list" in result.denial_reason or "is blocked" in result.denial_reason)
        
        # Test allowed vendor works
        result = engine.evaluate(25.0, "openai", "reason")
        assert result.approved
        
        # Test justification required
        result = engine.evaluate(25.0, "openai", None)
        assert not result.approved
        assert "Justification is required" in result.denial_reason
    
    def test_check_ordering_maintained(self):
        """Test that check ordering is maintained for backward compatibility."""
        policy = SpendPolicy(
            max_transaction=10.0,
            daily_budget=100.0,
            blocked_vendors=["vendor"],
            hourly_budget=50.0
        )
        engine = PolicyEngine(policy)
        
        # Transaction that fails amount cap should fail there, not at vendor check
        result = engine.evaluate(100.0, "vendor", "reason")
        assert not result.approved
        assert "exceeds limit" in result.denial_reason
        assert result.checks_passed == ["positive_amount"]  # Only first check passed


class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_period_boundary_transitions(self):
        """Test spending across period boundaries."""
        policy = SpendPolicy(
            max_transaction=100.0,
            daily_budget=1000.0,
            hourly_budget=100.0
        )
        engine = PolicyEngine(policy)
        
        # Spend at end of hour
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 14, 59, 0)
            mock_policy_dt.now.return_value = datetime(2024, 1, 15, 14, 59, 0)
            result = engine.evaluate(80.0, "vendor", "reason")
            assert result.approved
            engine.commit_spend(80.0)
        
        # Spend in next hour - should have fresh budget
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 15, 1, 0)
            mock_policy_dt.now.return_value = datetime(2024, 1, 15, 15, 1, 0)
            result = engine.evaluate(90.0, "vendor", "reason")
        assert result.approved
    
    def test_multiple_time_budgets_interaction(self):
        """Test interaction between different time-based budgets."""
        policy = SpendPolicy(
            max_transaction=200.0,
            daily_budget=1000.0,
            hourly_budget=100.0,
            weekly_budget=500.0
        )
        engine = PolicyEngine(policy)
        
        current_time = datetime(2024, 1, 15, 14, 30, 0)
        
        # First transaction - should pass all checks
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = current_time
            mock_policy_dt.now.return_value = current_time
            result = engine.evaluate(80.0, "vendor", "reason")
            assert result.approved
            engine.commit_spend(80.0)
        
        # Second transaction - should fail hourly budget
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = current_time
            mock_policy_dt.now.return_value = current_time
            result = engine.evaluate(30.0, "vendor", "reason")
        assert not result.approved
        assert "Hourly budget exhausted" in result.denial_reason
    
    def test_very_large_rollover_amounts(self):
        """Test handling of large rollover amounts."""
        policy = SpendPolicy(
            max_transaction=10000.0,
            daily_budget=15000.0,
            weekly_budget=5000.0,
            enable_rollover=True,
            rollover_periods=["weekly"]
        )
        engine = PolicyEngine(policy)
        
        # Spend very little in first week
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 14, 0, 0)
            mock_policy_dt.now.return_value = datetime(2024, 1, 15, 14, 0, 0)
            result = engine.evaluate(100.0, "vendor", "reason")
            assert result.approved
            engine.commit_spend(100.0)
        
        # Next week should have large rollover (4900) + new budget (5000) = 9900
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = datetime(2024, 1, 22, 10, 0, 0)
            mock_policy_dt.now.return_value = datetime(2024, 1, 22, 10, 0, 0)
            result = engine.evaluate(9800.0, "vendor", "reason")
        assert result.approved
    
    def test_zero_budget_periods(self):
        """Test periods with zero budget."""
        policy = SpendPolicy(
            max_transaction=100.0,
            daily_budget=1000.0,
            hourly_budget=0.0  # Zero budget
        )
        engine = PolicyEngine(policy)
        
        # Any spending should fail hourly budget
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 14, 30, 0)
            mock_policy_dt.now.return_value = datetime(2024, 1, 15, 14, 30, 0)
            result = engine.evaluate(1.0, "vendor", "reason")
        
        assert not result.approved
        assert "Hourly budget exhausted" in result.denial_reason


class TestPolicyEngineIntegration:
    """Integration tests with PolicyEngine and time-based policies."""
    
    def test_policy_engine_summary_includes_time_periods(self):
        """Test that we can get summaries of time-based periods."""
        policy = SpendPolicy(
            max_transaction=100.0,
            daily_budget=1000.0,
            hourly_budget=50.0,
            weekly_budget=300.0
        )
        engine = PolicyEngine(policy)
        
        current_time = datetime(2024, 1, 15, 14, 30, 0)
        
        # Make a transaction to initialize periods and commit
        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = current_time
            mock_policy_dt.now.return_value = current_time
            result = engine.evaluate(30.0, "vendor", "reason")
            assert result.approved
            engine.commit_spend(30.0)
        
        # Get summaries
        hourly_summary = engine._period_tracker.get_period_summary("hourly", current_time)
        weekly_summary = engine._period_tracker.get_period_summary("weekly", current_time)
        
        assert hourly_summary["status"] == "active"
        assert hourly_summary["spent_amount"] == 30.0
        assert hourly_summary["remaining_budget"] == 20.0
        
        assert weekly_summary["status"] == "active"
        assert weekly_summary["spent_amount"] == 30.0
        assert weekly_summary["remaining_budget"] == 270.0
    
    def test_periodic_cleanup_called(self):
        """Test that periodic cleanup is called."""
        policy = SpendPolicy(hourly_budget=100.0)
        engine = PolicyEngine(policy)
        engine._period_tracker.cleanup_old_periods = Mock()

        # Set counter to 99 so next evaluate() triggers cleanup at 100
        engine._eval_count = 99

        with patch('paygraph.time_periods.datetime') as mock_dt, \
             patch('paygraph.policy.datetime') as mock_policy_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 14, 30, 0)
            mock_policy_dt.now.return_value = datetime(2024, 1, 15, 14, 30, 0)
            engine.evaluate(30.0, "vendor", "reason")

        engine._period_tracker.cleanup_old_periods.assert_called_once()