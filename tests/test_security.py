"""Security regression tests: report links, fetch pinning, and error redaction."""

import json
import socket
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import agent
import agent_stub
import server
from report_links import safe_link

PASSCODE = "test-passcode"
HEADERS = {"X-Demo-Passcode": PASSCODE}


def _addrinfo(*ips: str):
    """Shape socket.getaddrinfo results for the given addresses."""
    rows = []
    for ip in ips:
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        sockaddr = (ip, 0, 0, 0) if family == socket.AF_INET6 else (ip, 0)
        rows.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
    return rows


class SafeLinkTests(unittest.TestCase):
    def test_http_and_https_pass_through(self) -> None:
        for url in ("https://example.com/visit", "http://example.com", "HTTPS://Example.com/a?b=c"):
            self.assertEqual(safe_link(url), url)

    def test_dangerous_or_malformed_links_are_dropped(self) -> None:
        for url in (
            "javascript:alert(document.domain)",
            "JavaScript:alert(1)",
            " javascript:alert(1)",
            "java\nscript:alert(1)",
            "java\tscript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "vbscript:msgbox(1)",
            "file:///etc/passwd",
            "//evil.example/path",
            "/relative/path",
            "https://",
            "",
            None,
            42,
            "https://example.com/" + "a" * 3000,
        ):
            self.assertIsNone(safe_link(url), url)

    def test_both_report_models_drop_unsafe_links(self) -> None:
        for model in (agent.PlaceReport, agent_stub.PlaceReport):
            report = model(
                label="x", summary="y", latitude=0, longitude=0,
                link="javascript:alert(1)", link_title="Click me",
            )
            self.assertIsNone(report.link, model.__module__)
            good = model(label="x", summary="y", latitude=0, longitude=0, link="https://example.com")
            self.assertEqual(good.link, "https://example.com")


class FetchPinningTests(unittest.TestCase):
    def test_private_resolution_is_refused(self) -> None:
        for ips in (("127.0.0.1",), ("10.0.0.5",), ("169.254.169.254",), ("::1",), ("93.184.215.14", "192.168.1.1")):
            with patch.object(agent.socket, "getaddrinfo", return_value=_addrinfo(*ips)):
                reason, ip = agent._check_public_url("https://rebind.example/")
            self.assertIsNotNone(reason, ips)
            self.assertIsNone(ip)

    def test_public_resolution_returns_the_checked_address(self) -> None:
        with patch.object(agent.socket, "getaddrinfo", return_value=_addrinfo("93.184.215.14")):
            self.assertEqual(agent._check_public_url("https://example.com/"), (None, "93.184.215.14"))

    def test_non_http_schemes_are_refused_without_dns(self) -> None:
        with patch.object(agent.socket, "getaddrinfo") as resolver:
            for url in ("file:///C:/Windows/win.ini", "ftp://example.com/", "gopher://example.com/"):
                self.assertIsNotNone(agent._check_public_url(url)[0])
            resolver.assert_not_called()

    def test_pinned_request_keeps_host_and_sni(self) -> None:
        url, headers, ext = agent._pinned_request("https://example.com/a/b?c=1", "93.184.215.14")
        self.assertEqual(url, "https://93.184.215.14/a/b?c=1")
        self.assertEqual(headers, {"Host": "example.com"})
        self.assertEqual(ext, {"sni_hostname": "example.com"})

        url, headers, ext = agent._pinned_request("http://example.com:8080/x", "2606:2800:21f:cb07::1")
        self.assertEqual(url, "http://[2606:2800:21f:cb07::1]:8080/x")
        self.assertEqual(headers, {"Host": "example.com:8080"})
        self.assertEqual(ext, {})

    def test_fetch_connects_to_the_checked_ip_not_a_second_lookup(self) -> None:
        """A rebinding resolver answers public first, private second. Only one lookup may happen."""
        answers = iter([_addrinfo("93.184.215.14"), _addrinfo("127.0.0.1")])
        sent = []

        def fake_send(self, request, stream=False):
            sent.append(request)
            raise agent.httpx.ConnectError("stop here", request=request)

        with patch.object(agent.socket, "getaddrinfo", side_effect=lambda *a, **k: next(answers)), \
             patch.object(agent.httpx.Client, "send", fake_send):
            agent._fetch_web_page("https://rebind.example/page")

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].url.host, "93.184.215.14")
        self.assertEqual(sent[0].headers["host"], "rebind.example")
        self.assertEqual(sent[0].extensions.get("sni_hostname"), "rebind.example")

    def test_redirect_to_private_address_is_refused(self) -> None:
        answers = iter([_addrinfo("93.184.215.14"), _addrinfo("10.0.0.5")])

        def fake_send(self, request, stream=False):
            return agent.httpx.Response(302, headers={"location": "http://internal.example/"}, request=request)

        with patch.object(agent.socket, "getaddrinfo", side_effect=lambda *a, **k: next(answers)), \
             patch.object(agent.httpx.Client, "send", fake_send):
            result = agent._fetch_web_page("https://example.com/")
        self.assertIn("non-public", result["error"])


class _ExplodingAgent:
    SECRET = "arn:aws:iam::123456789012:role/internal-role AccessDenied"

    def __call__(self, prompt, **kwargs):
        raise RuntimeError(self.SECRET)

    async def stream_async(self, prompt, **kwargs):
        raise RuntimeError(self.SECRET)
        yield  # pragma: no cover - makes this an async generator


class ErrorRedactionTests(unittest.TestCase):
    def setUp(self) -> None:
        server._sessions.clear()
        self.client = TestClient(server.app)
        self.patches = [
            patch.object(server, "_configured_passcode", return_value=PASSCODE),
            patch.object(server, "build_agent", return_value=_ExplodingAgent()),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in self.patches:
            item.stop()
        server._sessions.clear()

    def test_stream_failure_does_not_leak_exception_text(self) -> None:
        with self.assertLogs("map_click_agent", level="ERROR") as logs:
            response = self.client.post("/api/describe/stream", headers=HEADERS, json={"latitude": 1, "longitude": 2})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("123456789012", response.text)
        self.assertIn(json.dumps(server.AGENT_FAILED_DETAIL), response.text)
        self.assertIn("123456789012", "\n".join(logs.output))

    def test_describe_failure_does_not_leak_exception_text(self) -> None:
        with self.assertLogs("map_click_agent", level="ERROR"):
            response = self.client.post("/api/describe", headers=HEADERS, json={"latitude": 1, "longitude": 2})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], server.AGENT_FAILED_DETAIL)
        self.assertNotIn("123456789012", response.text)


if __name__ == "__main__":
    unittest.main()
