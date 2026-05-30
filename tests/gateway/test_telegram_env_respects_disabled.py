"""Regression tests for #35555 — TELEGRAM_BOT_TOKEN must not silently re-enable
a profile that explicitly set ``platforms.telegram.enabled: false`` in YAML.

Before the fix, ``_apply_env_overrides`` unconditionally flipped
``config.platforms[Platform.TELEGRAM].enabled = True`` whenever ``TELEGRAM_BOT_TOKEN``
was present in the environment. That broke multi-profile setups where the bot
token is shared via ``.env`` symlinks but individual profiles disable the
Telegram adapter — every disabled profile silently reconnected to the same
bot, producing ``Conflict: terminated by other getUpdates request`` errors and
cross-profile pollution.

The behaviour we want matches the existing WhatsApp branch (lines 1310-1319 of
gateway/config.py): when YAML already declares the platform, respect its
``enabled`` flag. The token still gets stored so non-adapter call sites
(skill-driven outbound sends) can reuse it without activating the gateway
adapter — same rule Slack already follows.
"""

import os
from unittest.mock import patch

import pytest

from gateway.config import (
    GatewayConfig,
    Platform,
    PlatformConfig,
    _apply_env_overrides,
)


# Real-looking bot token shape so future ``has_usable_secret`` placeholder
# checks don't reject it as too-weak in unrelated assertions.
_FAKE_TOKEN = "1234567890:AAAA-bbbbCCCCddddEEEEffffGGGGhhhh"


class TestTelegramEnvRespectsConfigDisabled:
    """``TELEGRAM_BOT_TOKEN`` env var must respect an explicit YAML disable."""

    def test_env_token_does_not_override_explicit_disable(self):
        """YAML ``enabled: false`` wins over env-var presence (#35555)."""
        config = GatewayConfig()
        config.platforms[Platform.TELEGRAM] = PlatformConfig(enabled=False)
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": _FAKE_TOKEN}, clear=False):
            _apply_env_overrides(config)
        assert config.platforms[Platform.TELEGRAM].enabled is False, (
            "Profile-level platforms.telegram.enabled: false must not be "
            "overridden by TELEGRAM_BOT_TOKEN in the environment."
        )

    def test_env_token_still_stored_when_disabled(self):
        """A disabled profile still records the token so outbound-only call
        sites (e.g. skills that push notifications via ``sendMessage`` without
        running the long-poll adapter) can reuse it. Mirrors the Slack rule."""
        config = GatewayConfig()
        config.platforms[Platform.TELEGRAM] = PlatformConfig(enabled=False)
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": _FAKE_TOKEN}, clear=False):
            _apply_env_overrides(config)
        assert config.platforms[Platform.TELEGRAM].token == _FAKE_TOKEN

    def test_env_token_auto_enables_when_no_yaml_block(self):
        """Backwards compat: env-only setup (no YAML platform block at all)
        keeps the existing auto-enable behaviour. Without this branch users
        who configure Hermes purely via ``.env`` lose Telegram entirely."""
        config = GatewayConfig()
        assert Platform.TELEGRAM not in config.platforms
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": _FAKE_TOKEN}, clear=False):
            _apply_env_overrides(config)
        assert Platform.TELEGRAM in config.platforms
        assert config.platforms[Platform.TELEGRAM].enabled is True
        assert config.platforms[Platform.TELEGRAM].token == _FAKE_TOKEN

    def test_env_token_keeps_enabled_when_yaml_enables(self):
        """Symmetric guard: YAML ``enabled: true`` plus env token = enabled.
        Token gets overwritten by the env value (env wins for the secret)."""
        config = GatewayConfig()
        config.platforms[Platform.TELEGRAM] = PlatformConfig(
            enabled=True, token="stale-yaml-token"
        )
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": _FAKE_TOKEN}, clear=False):
            _apply_env_overrides(config)
        assert config.platforms[Platform.TELEGRAM].enabled is True
        assert config.platforms[Platform.TELEGRAM].token == _FAKE_TOKEN

    def test_no_env_token_leaves_disabled_block_untouched(self):
        """Sanity guard: with TELEGRAM_BOT_TOKEN absent, the disabled YAML
        block survives ``_apply_env_overrides`` unchanged."""
        config = GatewayConfig()
        config.platforms[Platform.TELEGRAM] = PlatformConfig(enabled=False)
        env = {k: v for k, v in os.environ.items() if k != "TELEGRAM_BOT_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            _apply_env_overrides(config)
        assert config.platforms[Platform.TELEGRAM].enabled is False
        assert config.platforms[Platform.TELEGRAM].token in (None, "")

    @pytest.mark.parametrize("yaml_enabled", [True, False])
    def test_companion_env_vars_do_not_flip_enabled(self, yaml_enabled):
        """``TELEGRAM_REPLY_TO_MODE`` / ``TELEGRAM_FALLBACK_IPS`` set up their
        own PlatformConfig entry when missing. They must not flip an existing
        ``enabled`` flag in either direction — they only configure transport
        details. Regression guard for the parallel branches below the token
        block in ``_apply_env_overrides``."""
        config = GatewayConfig()
        config.platforms[Platform.TELEGRAM] = PlatformConfig(enabled=yaml_enabled)
        env = {
            "TELEGRAM_REPLY_TO_MODE": "all",
            "TELEGRAM_FALLBACK_IPS": "149.154.167.50",
        }
        # Ensure TELEGRAM_BOT_TOKEN doesn't leak in from the test env.
        env_clean = {k: v for k, v in os.environ.items() if k != "TELEGRAM_BOT_TOKEN"}
        env_clean.update(env)
        with patch.dict(os.environ, env_clean, clear=True):
            _apply_env_overrides(config)
        assert config.platforms[Platform.TELEGRAM].enabled is yaml_enabled
        assert config.platforms[Platform.TELEGRAM].reply_to_mode == "all"
        assert config.platforms[Platform.TELEGRAM].extra.get("fallback_ips") == [
            "149.154.167.50"
        ]
