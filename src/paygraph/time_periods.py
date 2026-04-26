"""Time period management utilities for time-based spending policies.

This module provides classes and utilities for managing different time periods
(hourly, daily, weekly, monthly) and calculating spending limits with rollover support.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class TimePeriod:
    """Represents a specific time period with boundaries and budget information.

    Attributes:
        period_type: Type of period ("hourly", "daily", "weekly", "monthly")
        start_time: Start of the period (inclusive)
        end_time: End of the period (exclusive)
        budget_limit: Base budget limit for this period
        rollover_amount: Additional budget from previous period rollover
        spent_amount: Amount spent in this period so far
    """
    period_type: str
    start_time: datetime
    end_time: datetime
    budget_limit: float
    rollover_amount: float = 0.0
    spent_amount: float = 0.0

    @property
    def effective_budget(self) -> float:
        """Total available budget including rollover."""
        return self.budget_limit + self.rollover_amount

    @property
    def remaining_budget(self) -> float:
        """Remaining budget in this period."""
        return max(0.0, self.effective_budget - self.spent_amount)

    @property
    def period_id(self) -> str:
        """Unique identifier for this period."""
        return f"{self.period_type}_{self.start_time.strftime('%Y%m%d_%H')}"


def get_hourly_period(dt: datetime) -> tuple[datetime, datetime]:
    """Get the hourly period containing the given datetime.

    Args:
        dt: The datetime to find the period for

    Returns:
        Tuple of (period_start, period_end)
    """
    start = dt.replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=1)
    return start, end


def get_daily_period(dt: datetime) -> tuple[datetime, datetime]:
    """Get the daily period containing the given datetime.

    Args:
        dt: The datetime to find the period for

    Returns:
        Tuple of (period_start, period_end)
    """
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start, end


def get_weekly_period(dt: datetime) -> tuple[datetime, datetime]:
    """Get the weekly period containing the given datetime.

    Week starts on Monday (ISO 8601 standard).

    Args:
        dt: The datetime to find the period for

    Returns:
        Tuple of (period_start, period_end)
    """
    day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    days_since_monday = day_start.weekday()
    start = day_start - timedelta(days=days_since_monday)
    end = start + timedelta(days=7)
    return start, end


def get_monthly_period(dt: datetime) -> tuple[datetime, datetime]:
    """Get the monthly period containing the given datetime.

    Args:
        dt: The datetime to find the period for

    Returns:
        Tuple of (period_start, period_end)
    """
    start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


class PeriodTracker:
    """Tracks spending across multiple time periods with rollover support.

    This class manages the state of different time periods and handles
    rollover calculations when periods expire.
    """

    def __init__(self):
        """Initialize the period tracker."""
        self._periods: dict[str, TimePeriod] = {}

    def get_current_period(self, period_type: str, budget_limit: float,
                          current_time: datetime | None = None) -> TimePeriod:
        """Get or create the current period for the given type.

        Args:
            period_type: Type of period ("hourly", "daily", "weekly", "monthly")
            budget_limit: Base budget limit for this period type
            current_time: Current time (defaults to datetime.now())

        Returns:
            TimePeriod object for the current period

        Raises:
            ValueError: If period_type is not supported
        """
        if current_time is None:
            current_time = datetime.now()

        start_time, end_time = self._get_period_boundaries(period_type, current_time)
        period_id = f"{period_type}_{start_time.strftime('%Y%m%d_%H')}"

        if period_id in self._periods:
            return self._periods[period_id]

        period = TimePeriod(
            period_type=period_type,
            start_time=start_time,
            end_time=end_time,
            budget_limit=budget_limit,
            rollover_amount=0.0,
            spent_amount=0.0
        )

        self._periods[period_id] = period
        return period

    def is_period_expired(self, period: TimePeriod,
                         current_time: datetime | None = None) -> bool:
        """Check if a period has expired.

        Args:
            period: The period to check
            current_time: Current time (defaults to datetime.now())

        Returns:
            True if the period has expired, False otherwise
        """
        if current_time is None:
            current_time = datetime.now()
        return current_time >= period.end_time

    def calculate_rollover_amount(self, expired_period: TimePeriod) -> float:
        """Calculate the rollover amount from an expired period.

        Simple rollover rule: unused budget carries over to the next period.

        Args:
            expired_period: The expired period to calculate rollover from

        Returns:
            Amount that should roll over to the next period
        """
        return max(0.0, expired_period.remaining_budget)

    def process_period_rollover(self, period_type: str, budget_limit: float,
                              current_time: datetime | None = None) -> float | None:
        """Process rollover for a period type and return rollover amount.

        This method checks if there's an expired period that needs rollover
        processing and returns the rollover amount for the new period.

        Args:
            period_type: Type of period to check for rollover
            budget_limit: Base budget limit for the period type
            current_time: Current time (defaults to datetime.now())

        Returns:
            Rollover amount to apply to the new period, or None if no rollover
        """
        if current_time is None:
            current_time = datetime.now()

        expired_periods = [
            period for period in self._periods.values()
            if (period.period_type == period_type and
                self.is_period_expired(period, current_time))
        ]

        if not expired_periods:
            return None

        most_recent = max(expired_periods, key=lambda p: p.end_time)
        rollover = self.calculate_rollover_amount(most_recent)
        return rollover if rollover > 0 else None

    def record_spending(self, period: TimePeriod, amount: float) -> None:
        """Record spending in a period.

        Args:
            period: The period to record spending in
            amount: Amount spent
        """
        period.spent_amount += amount

    def apply_rollover(self, period: TimePeriod, rollover_amount: float) -> None:
        """Apply rollover amount to a period.

        Args:
            period: The period to apply rollover to
            rollover_amount: Amount to roll over
        """
        period.rollover_amount += rollover_amount

    def cleanup_old_periods(self, current_time: datetime | None = None,
                           keep_days: int = 30) -> None:
        """Clean up old period records to prevent memory bloat.

        Args:
            current_time: Current time (defaults to datetime.now())
            keep_days: Number of days of old periods to keep
        """
        if current_time is None:
            current_time = datetime.now()

        cutoff_time = current_time - timedelta(days=keep_days)

        to_remove = [
            period_id for period_id, period in self._periods.items()
            if period.end_time < cutoff_time
        ]

        for period_id in to_remove:
            del self._periods[period_id]

    def _get_period_boundaries(self, period_type: str,
                              dt: datetime) -> tuple[datetime, datetime]:
        """Get period boundaries for a given type and datetime.

        Args:
            period_type: Type of period
            dt: Datetime to get boundaries for

        Returns:
            Tuple of (start_time, end_time)

        Raises:
            ValueError: If period_type is not supported
        """
        if period_type == "hourly":
            return get_hourly_period(dt)
        elif period_type == "daily":
            return get_daily_period(dt)
        elif period_type == "weekly":
            return get_weekly_period(dt)
        elif period_type == "monthly":
            return get_monthly_period(dt)
        else:
            raise ValueError(f"Unsupported period type: {period_type}")

    def get_period_summary(self, period_type: str,
                          current_time: datetime | None = None) -> dict:
        """Get a summary of the current period state.

        Args:
            period_type: Type of period to summarize
            current_time: Current time (defaults to datetime.now())

        Returns:
            Dictionary with period summary information
        """
        if current_time is None:
            current_time = datetime.now()

        current_periods = [
            period for period in self._periods.values()
            if (period.period_type == period_type and
                period.start_time <= current_time < period.end_time)
        ]

        if not current_periods:
            return {
                "period_type": period_type,
                "status": "no_active_period",
                "current_time": current_time.isoformat()
            }

        period = current_periods[0]

        return {
            "period_type": period_type,
            "status": "active",
            "period_id": period.period_id,
            "start_time": period.start_time.isoformat(),
            "end_time": period.end_time.isoformat(),
            "budget_limit": period.budget_limit,
            "rollover_amount": period.rollover_amount,
            "effective_budget": period.effective_budget,
            "spent_amount": period.spent_amount,
            "remaining_budget": period.remaining_budget,
            "current_time": current_time.isoformat()
        }
