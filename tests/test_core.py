"""
Tests for the stealth modules — timing, comment engine, rate limiter.
"""

import pytest
import math
import asyncio

from instaflow.stealth.comment_engine import get_comment_text
from instaflow.stealth.timing import _lognormal_delay
from instaflow.core.health_monitor import HealthMonitor, OperationMode


class TestCommentEngine:
    def test_always_returns_link(self):
        assert get_comment_text() == "link"

    def test_returns_string(self):
        result = get_comment_text()
        assert isinstance(result, str)
        assert len(result) > 0


class TestLognormalDelay:
    def test_floor_respected(self):
        for _ in range(50):
            delay = _lognormal_delay(mean=5.0, sigma=1.5, floor=2.0)
            assert delay >= 2.0

    def test_positive_values(self):
        for _ in range(50):
            delay = _lognormal_delay(mean=90.0, sigma=30.0)
            assert delay > 0

    def test_reasonable_range(self):
        """Sanity check: 1000 samples should average close to the mean."""
        samples = [_lognormal_delay(mean=10.0, sigma=2.0, floor=0.1) for _ in range(1000)]
        avg = sum(samples) / len(samples)
        # Log-normal mean should be within 50% of configured mean
        assert 5.0 < avg < 20.0


class TestHealthMonitor:
    def test_normal_mode(self):
        assert HealthMonitor.get_mode(100) == OperationMode.NORMAL
        assert HealthMonitor.get_mode(70) == OperationMode.NORMAL

    def test_conservative_mode(self):
        assert HealthMonitor.get_mode(69) == OperationMode.CONSERVATIVE
        assert HealthMonitor.get_mode(40) == OperationMode.CONSERVATIVE

    def test_quarantine_mode(self):
        assert HealthMonitor.get_mode(39) == OperationMode.QUARANTINE
        assert HealthMonitor.get_mode(0) == OperationMode.QUARANTINE

    def test_signal_decrement(self):
        score = HealthMonitor.apply_signal(100, "checkpoint_challenge")
        assert score == 80  # -20

    def test_signal_increment(self):
        score = HealthMonitor.apply_signal(90, "clean_session")
        assert score == 92  # +2

    def test_score_clamped_at_100(self):
        score = HealthMonitor.apply_signal(100, "clean_session")
        assert score == 100

    def test_score_clamped_at_0(self):
        score = HealthMonitor.apply_signal(5, "captcha_triggered")
        assert score == 0  # -25, clamped to 0

    def test_rate_multiplier_normal(self):
        assert HealthMonitor.rate_limit_multiplier(80) == 1.0

    def test_rate_multiplier_conservative(self):
        assert HealthMonitor.rate_limit_multiplier(50) == 0.5

    def test_rate_multiplier_quarantine(self):
        assert HealthMonitor.rate_limit_multiplier(20) == 0.0

    def test_should_quarantine(self):
        assert HealthMonitor.should_quarantine(39) is True
        assert HealthMonitor.should_quarantine(40) is False


class TestCTADetector:
    def setup_method(self):
        from instaflow.instagram.dm_monitor import CTADetector
        self.detector = CTADetector()

    def test_detects_link_with_url(self):
        msg = "Hey! Click here: https://example.com/offer"
        result = self.detector.score_message(msg)
        assert result["is_cta"] is True
        assert result["confidence"] >= 0.7
        assert "https://example.com/offer" in result["urls"]

    def test_no_cta_plain_text(self):
        msg = "Thanks for following! Hope you enjoy the content."
        result = self.detector.score_message(msg)
        assert result["is_cta"] is False

    def test_url_only_high_confidence(self):
        msg = "Check this out https://bit.ly/abc123"
        result = self.detector.score_message(msg)
        # URL present gives 0.45, "check" is primary keyword (0.4) → 0.85
        assert result["is_cta"] is True

    def test_url_extraction(self):
        msg = "Go to https://landing.example.com/r?ref=ig for more"
        result = self.detector.score_message(msg)
        assert len(result["urls"]) == 1
        assert "landing.example.com" in result["urls"][0]
