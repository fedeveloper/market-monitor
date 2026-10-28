import base64
import http.client
import json
import threading

import pytest

from conftest import FakeProvider
from market_monitor.models import Quote
from market_monitor.providers import ProviderError
from market_monitor.storage import Storage
from market_monitor.web.server import WebApp, make_server, password_problem

PASSWORD = "segreta-123"
FAKE_TOKEN = "123456789:AAH_fake-token-for-tests-0123456789"
NOW = 1_793_181_600  # 2026-10-28 10:00 Europe/Rome


def basic(password=PASSWORD):
    return "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()


class FakeNotifier:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return self.ok


@pytest.fixture(autouse=True)
def _no_env_secrets(monkeypatch):
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "WEB_PASSWORD"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def env_file(config_dir):
    path = config_dir.path.parent / ".env"
    path.write_text(f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\nTELEGRAM_CHAT_ID=42\nWEB_PASSWORD={PASSWORD}\n", encoding="utf-8")
    return path


@pytest.fixture
def notifier():
    return FakeNotifier()


@pytest.fixture
def quotes():
    return {"ENI.MI": Quote("ENI.MI", NOW - 900, 14.52, prev_close=14.08, currency="EUR")}


@pytest.fixture
def app(config_dir, env_file, notifier, quotes):
    providers = {"yahoo": FakeProvider("yahoo", quotes), "binance": FakeProvider("binance", quotes)}
    return WebApp(
        config_dir.path, env_file, password=PASSWORD,
        providers_factory=lambda config: providers,
        notifier_factory=lambda config, secrets: notifier,
        clock=lambda: NOW, auth_failure_delay=0,
    )


def call(app, method, path, body=None, auth=True, csrf=True, content_type="application/json"):
    headers = {}
    if auth:
        headers["Authorization"] = basic()
    raw = b""
    if body is not None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        if content_type:
            headers["Content-Type"] = content_type
        if csrf:
            headers["X-MM-Request"] = "1"
    response = app.handle(method, path, headers, raw)
    data = json.loads(response.body) if response.content_type.startswith("application/json") else response.body
    return response.status, data


# --- authentication and request checks -----------------------------------------


def test_requires_password(app):
    response = app.handle("GET", "/", {}, b"")
    assert response.status == 401 and "Basic" in response.headers["WWW-Authenticate"]
    response = app.handle("GET", "/", {"Authorization": basic("wrong-pass")}, b"")
    assert response.status == 401
    response = app.handle("GET", "/", {"authorization": "Basic !!!"}, b"")
    assert response.status == 401
    assert app.handle("GET", "/", {"Authorization": basic()}, b"").status == 200


def test_no_password_allowed_only_on_localhost(config_dir, env_file):
    app = WebApp(config_dir.path, env_file, password=None, auth_failure_delay=0)
    assert app.handle("GET", "/", {}, b"").status == 200
    assert password_problem("127.0.0.1", None) is None
    assert "WEB_PASSWORD mancante" in password_problem("0.0.0.0", None)
    assert "troppo corta" in password_problem("0.0.0.0", "short")
    assert password_problem("0.0.0.0", PASSWORD) is None


def test_static_files(app):
    status, body = call(app, "GET", "/")
    assert status == 200 and b"<title>Market Monitor</title>" in body
    assert app.handle("GET", "/app.js", {"Authorization": basic()}, b"").content_type.startswith("text/javascript")
    assert call(app, "GET", "/static/../server.py")[0] == 404


def test_changes_require_csrf_header_and_json(app):
    data = {"data": {}, "version": None}
    assert call(app, "PUT", "/api/files/watchlist", data, csrf=False)[0] == 403
    assert call(app, "PUT", "/api/files/watchlist", data, content_type="text/plain")[0] == 403
    assert call(app, "POST", "/api/test-telegram", {}, csrf=False)[0] == 403


def test_bad_json_and_size_limit(app):
    assert call(app, "POST", "/api/check-symbol", b"{not json")[0] == 400
    assert call(app, "POST", "/api/check-symbol", b"x" * (256 * 1024 + 1))[0] == 413


def test_unknown_route(app):
    assert call(app, "GET", "/api/nothing")[0] == 404
    assert call(app, "DELETE", "/api/files/watchlist")[0] == 404


# --- state and saving ------------------------------------------------------------


def test_state_without_secrets(app):
    status, data = call(app, "GET", "/api/state")
    assert status == 200
    assert data["files"]["watchlist"]["data"]["assets"][0]["symbol"] == "ENI.MI"
    assert data["files"]["config"]["version"]
    assert data["secrets"] == {"telegram_token": True, "telegram_chat_id": True}
    assert data["validation"] == {"errors": [], "warnings": []}
    text = json.dumps(data)
    assert FAKE_TOKEN not in text and PASSWORD not in text and '"42"' not in text


def test_save_valid_and_invalid(app, config_dir):
    _, state = call(app, "GET", "/api/state")
    wl = state["files"]["watchlist"]
    wl["data"]["assets"][0]["levels"] = {"above": [16.0]}
    status, data = call(app, "PUT", "/api/files/watchlist", {"data": wl["data"], "version": wl["version"]})
    assert status == 200 and data["ok"] and data["version"] != wl["version"]
    assert "above:\n    - 16.0" in (config_dir.path / "watchlist.yaml").read_text(encoding="utf-8")

    wl["data"]["assets"][0]["class"] = "bond"
    status, data = call(app, "PUT", "/api/files/watchlist", {"data": wl["data"], "version": data["version"]})
    assert status == 422
    assert any("valore 'bond' non ammesso" in e for e in data["errors"])


def test_save_conflict(app, config_dir):
    _, state = call(app, "GET", "/api/state")
    path = config_dir.path / "alerts.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# hand edit\n", encoding="utf-8")
    status, data = call(app, "PUT", "/api/files/alerts", {"data": state["files"]["alerts"]["data"], "version": state["files"]["alerts"]["version"]})
    assert status == 409 and data["conflict"] is True


@pytest.mark.parametrize("name", ["../.env", "..%2F.env", "secrets", ""])
def test_only_the_three_files_are_editable(app, name):
    assert call(app, "PUT", f"/api/files/{name}", {"data": {}, "version": None})[0] == 404


def test_save_requires_data(app):
    assert call(app, "PUT", "/api/files/watchlist", {"version": "x"})[0] == 400


def test_invalid_current_config_reported(app, config_dir):
    config_dir.write_text("alerts", "defaults: [\n")
    status, data = call(app, "GET", "/api/state")
    assert status == 200 and data["files"]["alerts"]["error"].startswith("alerts.yaml: YAML non valido")
    assert data["validation"]["errors"]
    status, data = call(app, "GET", "/api/status")
    assert status == 409 and data["errors"]


# --- status and actions ------------------------------------------------------------


def test_status_without_and_with_database(app, config_dir):
    status, data = call(app, "GET", "/api/status")
    assert status == 200 and data["status"] is None and "database non ancora creato" in data["message"]
    db = Storage(config_dir.path.parent / "data" / "market.db")
    db.save_quote(Quote("ENI.MI", NOW - 900, 14.52, prev_close=14.08, currency="EUR"))
    db.commit()
    db.close()
    status, data = call(app, "GET", "/api/status")
    eni = data["status"]["assets"][0]
    assert eni["symbol"] == "ENI.MI" and eni["price"] == 14.52


def test_check_symbol(app, quotes):
    status, data = call(app, "POST", "/api/check-symbol", {"symbol": "ENI.MI", "provider": "yahoo", "class": "stock"})
    assert status == 200 and data["ok"]
    assert data["price_text"] == "14,52 €" and data["change_text"] == "+3,1%" and data["delay_minutes"] == 15
    quotes["NOPE.MI"] = ProviderError("yahoo NOPE.MI: No data found, symbol may be delisted")
    status, data = call(app, "POST", "/api/check-symbol", {"symbol": "NOPE.MI", "provider": "yahoo"})
    assert status == 200 and data["ok"] is False and "delisted" in data["error"]


@pytest.mark.parametrize("symbol", ["../x", "ENI MI", "", "A?b=1"])
def test_check_symbol_rejects_bad_symbols(app, symbol):
    assert call(app, "POST", "/api/check-symbol", {"symbol": symbol, "provider": "yahoo"})[0] == 400


def test_check_symbol_unknown_provider(app):
    assert call(app, "POST", "/api/check-symbol", {"symbol": "X", "provider": "stooq"})[0] == 400


def test_test_telegram(app, notifier):
    status, data = call(app, "POST", "/api/test-telegram", {})
    assert status == 200 and data["ok"]
    assert "messaggio di prova" in notifier.sent[0]
    notifier.ok = False
    assert call(app, "POST", "/api/test-telegram", {})[0] == 502


def test_test_telegram_without_secrets(app, env_file):
    env_file.write_text(f"WEB_PASSWORD={PASSWORD}\n", encoding="utf-8")
    status, data = call(app, "POST", "/api/test-telegram", {})
    assert status == 409 and "Telegram non configurato" in data["error"]


def test_digest(app, notifier, config_dir):
    assert call(app, "POST", "/api/digest", {})[0] == 409  # no database yet
    db = Storage(config_dir.path.parent / "data" / "market.db")
    db.save_quote(Quote("ENI.MI", NOW - 900, 14.52, prev_close=14.08, currency="EUR"))
    db.commit()
    db.close()
    status, data = call(app, "POST", "/api/digest", {})
    assert status == 200 and "Riepilogo" in notifier.sent[0] and "Eni (ENI.MI)" in notifier.sent[0]


# --- real HTTP server on localhost ---------------------------------------------------


def test_http_server_end_to_end(app):
    server = make_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/", headers={"Authorization": basic()})
        response = conn.getresponse()
        body = response.read()
        assert response.status == 200 and b"Market Monitor" in body
        assert "default-src 'self'" in response.getheader("Content-Security-Policy")
        assert response.getheader("X-Frame-Options") == "DENY"
        assert response.getheader("Cache-Control") == "no-store"

        conn.request("POST", "/api/check-symbol", body=json.dumps({"symbol": "ENI.MI", "provider": "yahoo"}),
                     headers={"Authorization": basic(), "Content-Type": "application/json", "X-MM-Request": "1"})
        response = conn.getresponse()
        assert response.status == 200 and json.loads(response.read())["ok"]

        conn.request("GET", "/api/state")
        response = conn.getresponse()
        response.read()
        assert response.status == 401
        conn.close()
    finally:
        server.shutdown()
        server.server_close()


def test_cli_refuses_network_bind_without_password(config_dir, capsys):
    from market_monitor.cli import main

    env = config_dir.path.parent / ".env-empty"
    env.write_text("", encoding="utf-8")
    assert main(["--config-dir", str(config_dir.path), "--env-file", str(env), "web"]) == 78
    out = capsys.readouterr().out
    assert "WEB_PASSWORD mancante" in out and "systemctl restart market-monitor-web" in out


def test_check_config_reports_web_password(config_dir, capsys):
    from market_monitor.cli import main

    env = config_dir.path.parent / ".env-web"
    env.write_text("WEB_PASSWORD=short\n", encoding="utf-8")
    assert main(["--config-dir", str(config_dir.path), "--env-file", str(env), "check-config"]) == 0
    assert "Interfaccia web (comando web): 0.0.0.0:8080, non partirebbe: WEB_PASSWORD troppo corta" in capsys.readouterr().out
    env.write_text(f"WEB_PASSWORD={PASSWORD}\n", encoding="utf-8")
    main(["--config-dir", str(config_dir.path), "--env-file", str(env), "check-config"])
    assert "0.0.0.0:8080, password ok" in capsys.readouterr().out
