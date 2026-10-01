import os

from miscrits_hud.log_watcher import Activity, LocationChanged, LogWatcher, TokenSeen, jwt_exp, parse_line

LOCATION_LINE = '=== Nakama : DEBUG === Sending async request: http_key: <null>, id: update_location, payload: {"areaId":1,"locationId":7}, '


def bearer_line(token):
    return ('=== Nakama : DEBUG === Sending request [ID: 4, Method: POST, Uri: https://worldofmiscrits.com:443/v2/rpc/get_inventory, '
            f'Headers: {{ "Authorization": "Bearer {token}" }}, Body: Body, Timeout: 30, Retries: 3, Backoff base: 10 ms]')


def test_parse_location():
    assert parse_line(LOCATION_LINE) == LocationChanged(location_id=7, area_id=1)


def test_parse_location_malformed_payload():
    assert parse_line('id: update_location, payload: {"areaId":"x"}, ') is None


def test_parse_bearer_token(make_jwt):
    token = make_jwt(1790000000)
    assert parse_line(bearer_line(token)) == TokenSeen(token=token, exp=1790000000.0)


def test_basic_auth_is_ignored():
    line = '=== Nakama : DEBUG === Sending request [ID: 1, Method: POST, Uri: x, Headers: { "Authorization": "Basic YWJjOmRlZg==" }, Body: Body]'
    assert parse_line(line) is None


def test_garbage_lines():
    assert parse_line("Spawning battle") is None
    assert parse_line("") is None


def test_jwt_exp_invalid():
    assert jwt_exp("not-a-jwt") is None
    assert jwt_exp("a.!!!.c") is None


def write(path, text, mode="a"):
    with open(path, mode, encoding="utf-8", newline="") as f:
        f.write(text)


def test_initial_read_and_incremental(tmp_path, make_jwt):
    log = tmp_path / "godot.log"
    token = make_jwt(2000000000)
    write(log, f"Godot Engine v4.6\n{bearer_line(token)}\n{LOCATION_LINE}\n", "w")
    watcher = LogWatcher(log)
    assert watcher.poll() == [Activity(), TokenSeen(token, 2000000000.0), LocationChanged(7, 1)]
    assert watcher.poll() == []
    write(log, 'id: update_location, payload: {"areaId":2,"locationId":2}, \n')
    assert watcher.poll() == [Activity(), LocationChanged(2, 2)]


def test_partial_line_waits_for_newline(tmp_path):
    log = tmp_path / "godot.log"
    write(log, LOCATION_LINE[:40], "w")
    watcher = LogWatcher(log)
    assert watcher.poll() == [Activity()]
    write(log, LOCATION_LINE[40:] + "\n")
    assert watcher.poll() == [Activity(), LocationChanged(7, 1)]


def test_rotation_reads_new_file_from_start(tmp_path):
    log = tmp_path / "godot.log"
    write(log, "x" * 500 + "\n", "w")
    watcher = LogWatcher(log)
    watcher.poll()
    os.replace(log, tmp_path / "godot2026-10-01T10.00.00.log")
    write(log, LOCATION_LINE + "\n", "w")
    assert watcher.poll() == [Activity(), LocationChanged(7, 1)]


def test_missing_file(tmp_path):
    assert LogWatcher(tmp_path / "nope.log").poll() == []
