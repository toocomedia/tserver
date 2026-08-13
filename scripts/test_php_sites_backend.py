#!/usr/bin/env python3
"""Read-only HTTP smoke test for the core PHP Websites backend on a VPS."""
from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import os
import re
import ssl
import sys
from dataclasses import dataclass
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener


CSRF_RE = re.compile(
    r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)', re.I,
)


@dataclass
class Result:
    status: int
    body: str
    url: str
    headers: object

    def json(self):
        try:
            return json.loads(self.body)
        except json.JSONDecodeError:
            return None


class Client:
    def __init__(self, base_url: str, timeout: int, insecure: bool):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.csrf = ""
        handlers = [HTTPCookieProcessor(http.cookiejar.CookieJar())]
        if insecure:
            handlers.append(HTTPSHandler(context=ssl._create_unverified_context()))
        self.opener = build_opener(*handlers)

    def request(self, path: str, method: str = "GET", payload=None, csrf: bool = True) -> Result:
        data = None
        headers = {"Accept": "application/json", "User-Agent": "srv-panel-php-smoke/1"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if csrf and method not in {"GET", "HEAD"} and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        request = Request(urljoin(self.base_url, path.lstrip("/")), data=data, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except HTTPError as exc:
            return Result(exc.code, exc.read().decode("utf-8", "replace"), exc.geturl(), exc.headers)
        return Result(response.status, response.read().decode("utf-8", "replace"), response.geturl(), response.headers)

    def login(self, username: str, password: str, totp: str | None) -> Result:
        page = self.request("/login")
        self.csrf = extract_csrf(page.body)
        if page.status != 200 or not self.csrf:
            return Result(page.status, "Could not load login CSRF token.", page.url, page.headers)
        result = self._form("/login", {
            "username": username, "password": password, "next": "/", "csrf_token": self.csrf,
        })
        if urlparse(result.url).path.rstrip("/") == "/login/2fa":
            self.csrf = extract_csrf(result.body) or self.csrf
            code = totp or getpass.getpass("Panel 2FA code: ").strip()
            result = self._form("/login/2fa", {"code": code, "csrf_token": self.csrf})
        return result

    def _form(self, path: str, fields: dict[str, str]) -> Result:
        data = urlencode(fields).encode("utf-8")
        request = Request(
            urljoin(self.base_url, path.lstrip("/")), data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "text/html"},
            method="POST",
        )
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except HTTPError as exc:
            return Result(exc.code, exc.read().decode("utf-8", "replace"), exc.geturl(), exc.headers)
        return Result(response.status, response.read().decode("utf-8", "replace"), response.geturl(), response.headers)


def extract_csrf(body: str) -> str:
    match = CSRF_RE.search(body)
    return unescape(match.group(1)) if match else ""


def detail(result: Result) -> str:
    payload = result.json()
    if isinstance(payload, dict) and payload.get("detail"):
        return str(payload["detail"])
    text = re.sub(r"<[^>]+>", " ", result.body)
    return " ".join(unescape(text).split())[:240] or "empty response"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default=os.getenv("SRV_PANEL_TEST_USERNAME", "admin"))
    parser.add_argument("--site-id", type=int)
    parser.add_argument("--operation-id", type=int)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--insecure", action="store_true", help="Allow a self-signed HTTPS panel certificate.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    password = os.getenv("SRV_PANEL_TEST_PASSWORD") or getpass.getpass("Panel password: ")
    totp = os.getenv("SRV_PANEL_TEST_TOTP")
    client = Client(args.base_url, args.timeout, args.insecure)
    failures = 0

    def check(label: str, result: Result, expected=(200,)) -> bool:
        nonlocal failures
        passed = result.status in expected
        print(f"{'PASS' if passed else 'FAIL'} {label} [{result.status}]" + ("" if passed else f" {detail(result)}"))
        if args.verbose and result.json() is not None:
            print(json.dumps(redact(result.json()), indent=2)[:4000])
        failures += 0 if passed else 1
        return passed

    try:
        check("public panel health", client.request("/api/health"))
        client.login(args.username, password, totp)
        if not check("authenticated dependency status", client.request("/api/dependencies/status")):
            return 1
        dependencies = client.request("/api/dependencies/status").json() or {}
        php = next((item for item in dependencies.get("dependencies", []) if item.get("id") == "php"), {})
        active = bool(php.get("healthy"))
        print(f"INFO PHP dependency: {'active' if active else 'inactive'}")
        check("retired plugin route", client.request("/plugins/php_sites/api/options"), (404,))
        if not active:
            check("core dependency gate", client.request("/api/php-sites/"), (503,))
            print("SKIP PHP calls: activate one panel-managed PHP version in Dependencies.")
            return 1 if failures else 0

        check("core API root", client.request("/api/php-sites/"))
        check("creation options", client.request("/api/php-sites/options"))
        sites_result = client.request("/api/php-sites/sites")
        if not check("site list", sites_result):
            return 1
        sites = (sites_result.json() or {}).get("sites", [])
        check("CSRF rejection", client.request(
            "/api/php-sites/sites/0/control", "POST", {"action": "invalid-smoke"}, csrf=False,
        ), (403,))
        check("CSRF acceptance and schema rejection", client.request(
            "/api/php-sites/sites/0/control", "POST", {"action": "invalid-smoke"},
        ), (422,))
        site_id = args.site_id or (sites[0].get("id") if sites else None)
        if site_id:
            check("site detail", client.request(f"/api/php-sites/sites/{site_id}"))
            check("site health", client.request(f"/api/php-sites/sites/{site_id}/health"))
            check("operation history", client.request(f"/api/php-sites/sites/{site_id}/operations?limit=5"))
            for stream in ("access", "nginx_error", "php"):
                check(f"{stream} logs", client.request(
                    f"/api/php-sites/sites/{site_id}/logs?stream={stream}&lines=5"
                ))
            check("File Manager PHP root", client.request(
                f"/plugins/file_manager/api/apps/php:{site_id}/roots"
            ))
        else:
            print("SKIP site-specific calls: no PHP website exists; pass --site-id after creating one.")
        if args.operation_id:
            check("selected operation", client.request(f"/api/php-sites/operations/{args.operation_id}"))
    except (URLError, TimeoutError, OSError) as exc:
        print(f"FAIL connection: {exc}")
        return 1
    print(f"RESULT {'PASS' if failures == 0 else 'FAIL'} ({failures} failed check(s))")
    return 0 if failures == 0 else 1


def redact(value):
    if isinstance(value, dict):
        return {key: ("[redacted]" if any(word in key.lower() for word in ("password", "secret", "token")) else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


if __name__ == "__main__":
    sys.exit(main())
