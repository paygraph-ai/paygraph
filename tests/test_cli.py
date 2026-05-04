import pytest

from paygraph.cli import _resolve_stripe_billing_address


class TestResolveStripeBillingAddress:
    def test_no_env_vars_returns_none(self):
        """No STRIPE_BILLING_* variables set → None (gateway uses US default)."""
        assert _resolve_stripe_billing_address(env={}) is None

    def test_unrelated_env_vars_returns_none(self):
        """Variables outside the STRIPE_BILLING_* set are ignored."""
        env = {"STRIPE_API_KEY": "sk_test_x", "PATH": "/usr/bin"}
        assert _resolve_stripe_billing_address(env=env) is None

    def test_full_us_address(self):
        """All required fields set + state → full US dict."""
        env = {
            "STRIPE_BILLING_LINE1": "1 Market St",
            "STRIPE_BILLING_CITY": "San Francisco",
            "STRIPE_BILLING_STATE": "CA",
            "STRIPE_BILLING_POSTAL_CODE": "94105",
            "STRIPE_BILLING_COUNTRY": "US",
        }
        assert _resolve_stripe_billing_address(env=env) == {
            "line1": "1 Market St",
            "city": "San Francisco",
            "state": "CA",
            "postal_code": "94105",
            "country": "US",
        }

    def test_full_french_address_no_state(self):
        """French address — no state, all four required fields present."""
        env = {
            "STRIPE_BILLING_LINE1": "10 rue de Rivoli",
            "STRIPE_BILLING_CITY": "Paris",
            "STRIPE_BILLING_POSTAL_CODE": "75001",
            "STRIPE_BILLING_COUNTRY": "FR",
        }
        result = _resolve_stripe_billing_address(env=env)
        assert result == {
            "line1": "10 rue de Rivoli",
            "city": "Paris",
            "postal_code": "75001",
            "country": "FR",
        }
        assert "state" not in result

    def test_line2_is_optional(self):
        """LINE2 is included when set, omitted when not."""
        env = {
            "STRIPE_BILLING_LINE1": "Hauptstrasse 1",
            "STRIPE_BILLING_LINE2": "Apt 5B",
            "STRIPE_BILLING_CITY": "Berlin",
            "STRIPE_BILLING_POSTAL_CODE": "10115",
            "STRIPE_BILLING_COUNTRY": "DE",
        }
        result = _resolve_stripe_billing_address(env=env)
        assert result["line2"] == "Apt 5B"

    def test_legacy_country_only_raises_systemexit(self):
        """The old ``STRIPE_BILLING_COUNTRY=FR`` shorthand is rejected loudly.

        The pre-fix CLI silently produced ``{line1: 'N/A', city: 'N/A',
        postal_code: '00000', country: 'FR'}`` which Stripe rejects for
        most non-US countries. Failing fast with a clear instruction is
        better than silently calling a broken Stripe request.
        """
        env = {"STRIPE_BILLING_COUNTRY": "FR"}
        with pytest.raises(SystemExit):
            _resolve_stripe_billing_address(env=env)

    def test_partial_address_lists_missing_vars(self, capsys):
        """Error output names every missing required variable."""
        env = {
            "STRIPE_BILLING_LINE1": "10 rue de Rivoli",
            "STRIPE_BILLING_COUNTRY": "FR",
        }
        with pytest.raises(SystemExit):
            _resolve_stripe_billing_address(env=env)
        captured = capsys.readouterr()
        assert "STRIPE_BILLING_CITY" in captured.out
        assert "STRIPE_BILLING_POSTAL_CODE" in captured.out
        # Already-set fields must not appear in the missing list
        missing_section = captured.out.split("Missing:", 1)[1]
        assert "STRIPE_BILLING_LINE1" not in missing_section
        assert "STRIPE_BILLING_COUNTRY" not in missing_section

    def test_optional_only_is_partial(self):
        """Setting only LINE2 or STATE without the required fields is partial."""
        env = {"STRIPE_BILLING_STATE": "CA"}
        with pytest.raises(SystemExit):
            _resolve_stripe_billing_address(env=env)

    def test_empty_string_treated_as_unset(self):
        """An empty string env var counts as unset (matches ``os.environ.get``)."""
        env = {
            "STRIPE_BILLING_LINE1": "1 Market St",
            "STRIPE_BILLING_CITY": "",
            "STRIPE_BILLING_POSTAL_CODE": "94105",
            "STRIPE_BILLING_COUNTRY": "US",
        }
        with pytest.raises(SystemExit):
            _resolve_stripe_billing_address(env=env)
