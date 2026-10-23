"""DIA_DONATE_URL: one server-side setting (default: Ikenna's PayPal payment link), exposed at /api/config.
Empty or non-https means no Donate item."""
from __future__ import annotations

import pytest

import app


@pytest.mark.parametrize("value", ["", "   ", "http://example.org/give", "javascript:alert(1)", "https://", "https://x.org/a b"])
def test_unset_or_unsafe_values_hide_donate(monkeypatch, value):
    monkeypatch.setenv("DIA_DONATE_URL", value)
    assert app.public_config() == {"donateUrl": None}


def test_default_is_the_paypal_payment_link(monkeypatch):
    monkeypatch.delenv("DIA_DONATE_URL", raising=False)
    assert app.public_config() == {"donateUrl": "https://www.paypal.com/ncp/payment/PQGR9B44BEYLU"}


def test_env_overrides_the_default(monkeypatch):
    monkeypatch.setenv("DIA_DONATE_URL", "https://donate.example.invalid/dia")
    assert app.public_config() == {"donateUrl": "https://donate.example.invalid/dia"}
