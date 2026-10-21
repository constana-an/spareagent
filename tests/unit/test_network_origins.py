"""One server, several networks: the dashboard origin list (LAN + Tailscale)."""

import pytest

from apps.api.config import Settings


def test_extra_origins_are_accepted_alongside_the_primary():
    s = Settings(
        friends_auth_enabled=True,
        friends_web_origin="https://10.0.0.16:13443",
        friends_web_extra_origins=" https://100.87.82.71:13443 ,,https://10.0.0.16:13443",
    )
    assert s.friends_web_origins == ("https://10.0.0.16:13443", "https://100.87.82.71:13443")
    assert Settings().friends_web_origins == ("http://localhost:3000",)


def test_every_extra_origin_is_validated():
    with pytest.raises(ValueError, match="FRIENDS_WEB_ORIGIN"):
        Settings(
            friends_auth_enabled=True,
            friends_web_origin="https://10.0.0.16:13443",
            friends_web_extra_origins="https://100.87.82.71:13443/path",
        )
