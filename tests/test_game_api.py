import json
import urllib.error

import pytest

from miscrits_hud.game_api import USER_AGENT, AuthError, NetworkError, Player, fetch_player, parse_player

MISCRIT = {"m": 1, "l": 35, "h": 3, "s": 1, "e": 3, "d": 3, "p": 3, "pd": 3}


def response(data) -> str:
    inner = {"success": True, "data": json.dumps(data)}
    return json.dumps({"payload": json.dumps(inner)})


AREA = {"area_id": 1, "location": {"id": 7, "name": "Hidden Forest"}, "name": "Hidden Forest"}


def test_parse_player():
    player = parse_player(response({"area": AREA, "miscrits": [MISCRIT]}))
    assert player == Player("Hidden Forest", 1, [MISCRIT], location_id=7)


def test_parse_player_without_area():
    assert parse_player(response({"area": None, "miscrits": []})) == Player("", 0, [])


def test_parse_player_success_false():
    raw = json.dumps({"payload": json.dumps({"success": False, "data": "{}"})})
    with pytest.raises(NetworkError):
        parse_player(raw)


def test_parse_player_garbage():
    with pytest.raises(NetworkError):
        parse_player("<html>oops</html>")


class FakeResponse:
    def __init__(self, body: str):
        self._body = body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_sends_game_headers():
    seen = {}

    def opener(req, timeout):
        seen["req"], seen["timeout"] = req, timeout
        return FakeResponse(response({"area": AREA, "miscrits": []}))

    assert fetch_player("tok", opener=opener).location_name == "Hidden Forest"
    req = seen["req"]
    assert req.get_method() == "POST"
    assert req.data == b'""'
    assert req.get_header("Authorization") == "Bearer tok"
    assert req.get_header("User-agent") == USER_AGENT
    assert seen["timeout"] == 10.0


@pytest.mark.parametrize("code", [401, 403])
def test_fetch_auth_errors(code):
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, code, "no", None, None)

    with pytest.raises(AuthError):
        fetch_player("tok", opener=opener)


def test_fetch_server_error_is_network():
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 502, "bad gateway", None, None)

    with pytest.raises(NetworkError):
        fetch_player("tok", opener=opener)


def test_fetch_timeout_is_network():
    def opener(req, timeout):
        raise TimeoutError("timed out")

    with pytest.raises(NetworkError):
        fetch_player("tok", opener=opener)
