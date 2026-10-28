"""HTTP server of the web interface (``python -m market_monitor web``).

Standard library only. The interface talks to the monitor through the files it
already uses: it saves the YAML files (picked up by the hot reload), reads the
SQLite database in read-only mode, and never touches the secrets.

Security: HTTP Basic auth with WEB_PASSWORD (required unless bound to localhost),
a custom header on every request that changes something (a cross-site page cannot
send it without a CORS preflight, which is never allowed), a request size limit,
and a strict Content-Security-Policy (no inline scripts).
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from market_monitor.config import (
    ASSET_CLASSES,
    IMPLEMENTED_PROVIDERS,
    RULES,
    SYMBOL_FORMAT,
    Asset,
    AssetRules,
    Config,
    ConfigError,
    DayMoveRule,
    FastMoveRule,
    OpenGapRule,
    PriceLevels,
    Secrets,
    check_secrets,
    load_config,
    load_secrets,
)
from market_monitor.formatting import format_pct, format_price
from market_monitor.logging_setup import register_secret
from market_monitor.messages import test_message
from market_monitor.notifier import Notifier, TelegramNotifier
from market_monitor.providers import ProviderError, QuoteProvider, build_providers
from market_monitor.service import digest_text, status_content
from market_monitor.storage import Storage
from market_monitor.web.store import ConfigStore, ConflictError, UnknownFileError

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}
MAX_BODY_BYTES = 256 * 1024
CSRF_HEADER = "x-mm-request"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
MIN_PASSWORD_LENGTH = 8

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


@dataclass
class Response:
    status: int
    body: bytes = b""
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


def json_response(status: int, payload: Any) -> Response:
    return Response(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def error(status: int, message: str, **extra: Any) -> Response:
    return json_response(status, {"ok": False, "error": message, **extra})


def password_problem(host: str, password: str | None) -> str | None:
    """Why the server must not start with this host/password, or None."""
    if password is None:
        if host in LOOPBACK_HOSTS:
            return None
        return (
            f"WEB_PASSWORD mancante in .env: obbligatoria con host {host!r}. "
            "Impostala, oppure usa web.host: 127.0.0.1 (accesso solo dal Pi)."
        )
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"WEB_PASSWORD troppo corta: almeno {MIN_PASSWORD_LENGTH} caratteri."
    return None


def _default_notifier(config: Config, secrets: Secrets) -> Notifier:
    return TelegramNotifier(secrets.telegram_bot_token, secrets.telegram_chat_id, config.app.telegram)


class WebApp:
    """Request dispatch, independent of the HTTP server (tested directly)."""

    def __init__(
        self,
        config_dir: Path,
        env_file: Path,
        *,
        password: str | None,
        providers_factory: Callable[[Config], dict[str, QuoteProvider]] | None = None,
        notifier_factory: Callable[[Config, Secrets], Notifier] = _default_notifier,
        clock: Callable[[], float] = time.time,
        auth_failure_delay: float = 1.0,
    ):
        self.config_dir = Path(config_dir)
        self.env_file = Path(env_file)
        self.store = ConfigStore(self.config_dir)
        self._password = password
        self._providers_factory = providers_factory or (lambda config: build_providers(config.app))
        self._notifier_factory = notifier_factory
        self._clock = clock
        self._auth_failure_delay = auth_failure_delay

    # --- entry point ---------------------------------------------------------

    def handle(self, method: str, path: str, headers: Mapping[str, str], body: bytes) -> Response:
        headers = {k.lower(): v for k, v in headers.items()}
        path = path.split("?", 1)[0]
        if not self._authorized(headers):
            if headers.get("authorization") and self._auth_failure_delay:
                time.sleep(self._auth_failure_delay)  # slows down password guessing
            return Response(
                401, b'{"ok": false, "error": "password richiesta"}',
                headers={"WWW-Authenticate": 'Basic realm="Market Monitor", charset="UTF-8"'},
            )
        if method in ("PUT", "POST"):
            if headers.get(CSRF_HEADER) != "1" or not headers.get("content-type", "").startswith("application/json"):
                return error(403, "richiesta rifiutata (header mancante)")
            if len(body) > MAX_BODY_BYTES:
                return error(413, "richiesta troppo grande")
            try:
                payload = json.loads(body.decode("utf-8") or "null")
            except (UnicodeDecodeError, json.JSONDecodeError):
                return error(400, "JSON non valido")
        else:
            payload = None

        if method == "GET" and path in STATIC_FILES:
            return self._static(path)
        if method == "GET" and path == "/api/state":
            return self.state()
        if method == "GET" and path == "/api/status":
            return self.status()
        if method == "PUT" and path.startswith("/api/files/"):
            return self.save_file(path.removeprefix("/api/files/"), payload)
        if method == "POST" and path == "/api/check-symbol":
            return self.check_symbol(payload)
        if method == "POST" and path == "/api/test-telegram":
            return self.test_telegram()
        if method == "POST" and path == "/api/digest":
            return self.send_digest()
        return error(404, "non trovato")

    def _authorized(self, headers: Mapping[str, str]) -> bool:
        if self._password is None:
            return True
        value = headers.get("authorization", "")
        if not value.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(value[6:], validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return False
        _, _, given = decoded.partition(":")
        return hmac.compare_digest(given.encode("utf-8"), self._password.encode("utf-8"))

    def _static(self, path: str) -> Response:
        name, content_type = STATIC_FILES[path]
        return Response(200, (STATIC_DIR / name).read_bytes(), content_type)

    # --- helpers -------------------------------------------------------------

    def _config(self) -> Config | Response:
        try:
            return load_config(self.config_dir)
        except ConfigError as exc:
            return error(409, "la configurazione attuale non è valida: correggila e salva", errors=exc.errors)

    def _secrets(self) -> Secrets:
        secrets = load_secrets(self.env_file)
        register_secret(secrets.telegram_bot_token)
        return secrets

    # --- API -----------------------------------------------------------------

    def state(self) -> Response:
        files = {}
        for name in ("config", "watchlist", "alerts"):
            data, version, problem = self.store.read(name)
            files[name] = {"data": data, "version": version, "error": problem}
        secrets = self._secrets()
        try:
            config = load_config(self.config_dir)
            errors, warnings = [], list(config.warnings)
        except ConfigError as exc:
            errors, warnings = exc.errors, []
        return json_response(200, {
            "ok": True,
            "files": files,
            "validation": {"errors": errors, "warnings": warnings},
            "secrets": {
                "telegram_token": secrets.telegram_bot_token is not None,
                "telegram_chat_id": secrets.telegram_chat_id is not None,
            },
            "meta": {
                "classes": list(ASSET_CLASSES),
                "rules": list(RULES),
                "implemented_providers": list(IMPLEMENTED_PROVIDERS),
                "symbol_pattern": SYMBOL_FORMAT.pattern,
            },
        })

    def save_file(self, name: str, payload: Any) -> Response:
        if not isinstance(payload, dict) or "data" not in payload:
            return error(400, "atteso {data, version}")
        try:
            result = self.store.save(name, payload["data"], payload.get("version"))
        except UnknownFileError:
            return error(404, "file non modificabile")
        except ConflictError:
            return error(409, "il file è stato modificato nel frattempo: ricarica la pagina", conflict=True)
        if not result.ok:
            return error(422, "configurazione non valida, nessuna modifica salvata", errors=result.errors)
        log.info("web: %s.yaml saved", name)
        return json_response(200, {"ok": True, "version": result.version, "warnings": result.warnings})

    def status(self) -> Response:
        config = self._config()
        if isinstance(config, Response):
            return config
        db_path = config.app.storage.path
        if not db_path.exists():
            return json_response(200, {"ok": True, "status": None, "message": "database non ancora creato: avvia il servizio"})
        storage = Storage(db_path, read_only=True)
        try:
            content = status_content(config, storage, self._clock())
        finally:
            storage.close()
        return json_response(200, {"ok": True, "status": content})

    def check_symbol(self, payload: Any) -> Response:
        if not isinstance(payload, dict):
            return error(400, "atteso {symbol, provider}")
        symbol = str(payload.get("symbol") or "").strip()
        provider_name = str(payload.get("provider") or "")
        if not SYMBOL_FORMAT.match(symbol):
            return error(400, "simbolo non valido: ammessi lettere, cifre e . ^ = _ - (max 32)")
        config = self._config()
        if isinstance(config, Response):
            return config
        provider = self._providers_factory(config).get(provider_name)
        if provider is None:
            return error(400, f"provider {provider_name!r} non disponibile")
        asset_class = payload.get("class") if payload.get("class") in ASSET_CLASSES else "stock"
        disabled = AssetRules(FastMoveRule(False), DayMoveRule(False), OpenGapRule(False))
        asset = Asset(symbol, symbol, asset_class, str(payload.get("market") or ""), provider_name, True,
                      PriceLevels(), disabled)
        try:
            quote = provider.fetch(asset)
        except ProviderError as exc:
            return json_response(200, {"ok": False, "error": str(exc)})
        now = self._clock()
        tz = config.app.timezone
        return json_response(200, {
            "ok": True,
            "symbol": symbol,
            "price": quote.price,
            "price_text": format_price(quote.price, quote.currency),
            "change_text": format_pct(quote.change_pct) if quote.change_pct is not None else None,
            "quote_time": datetime.fromtimestamp(quote.ts, tz).strftime("%d/%m %H:%M"),
            "delay_minutes": max(0, round((now - quote.ts) / 60)),
        })

    def _notifier(self, config: Config) -> Notifier | Response:
        secrets = self._secrets()
        errors, warnings = check_secrets(secrets)
        if errors or warnings:
            return error(409, "Telegram non configurato in .env", errors=errors + warnings)
        return self._notifier_factory(config, secrets)

    def test_telegram(self) -> Response:
        config = self._config()
        if isinstance(config, Response):
            return config
        notifier = self._notifier(config)
        if isinstance(notifier, Response):
            return notifier
        if notifier.send(test_message(self._clock(), config.app.timezone, len(config.enabled_assets))):
            return json_response(200, {"ok": True})
        return error(502, "invio fallito: controlla token, chat id e connessione (dettagli nel log)")

    def send_digest(self) -> Response:
        config = self._config()
        if isinstance(config, Response):
            return config
        if not config.app.storage.path.exists():
            return error(409, "database non ancora creato: avvia il servizio")
        notifier = self._notifier(config)
        if isinstance(notifier, Response):
            return notifier
        storage = Storage(config.app.storage.path, read_only=True)
        try:
            text = digest_text(config, storage, self._clock())
        finally:
            storage.close()
        if notifier.send(text):
            return json_response(200, {"ok": True})
        return error(502, "invio fallito (dettagli nel log)")


# --- HTTP server ---------------------------------------------------------------


def make_handler(app: WebApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "MarketMonitor"
        sys_version = ""

        def _dispatch(self, method: str) -> None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > MAX_BODY_BYTES:
                response = error(413, "richiesta troppo grande")
            else:
                body = self.rfile.read(length) if length > 0 else b""
                try:
                    response = app.handle(method, self.path, dict(self.headers.items()), body)
                except Exception:
                    log.exception("web: error handling %s %s", method, self.path.split("?", 1)[0])
                    response = error(500, "errore interno (dettagli nel log)")
            self.send_response(response.status)
            for key, value in {**SECURITY_HEADERS, **response.headers}.items():
                self.send_header(key, value)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_PUT(self) -> None:
            self._dispatch("PUT")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def log_message(self, fmt: str, *args: Any) -> None:
            # Request line and status only: never headers (Authorization).
            log.info("web: %s %s", self.address_string(), fmt % args)

    return Handler


def make_server(app: WebApp, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(app))
    server.daemon_threads = True
    return server
