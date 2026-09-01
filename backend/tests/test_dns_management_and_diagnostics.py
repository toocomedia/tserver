import sys
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

# Ensure backend root is in python path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from fastapi import HTTPException
from utils import powerdns
from services import dns_service, dns_diagnostic_service


class TestDnsAutoCorrections(unittest.TestCase):
    """Test smart normalizations and auto-correction rules."""

    def test_normalize_record_name(self):
        domain = "blagh.co"
        # Apex / empty / @
        self.assertEqual(powerdns.normalize_record_name("@", domain), "@")
        self.assertEqual(powerdns.normalize_record_name("", domain), "@")
        self.assertEqual(powerdns.normalize_record_name("   ", domain), "@")
        self.assertEqual(powerdns.normalize_record_name("blagh.co", domain), "@")
        self.assertEqual(powerdns.normalize_record_name("blagh.co.", domain), "@")
        self.assertEqual(powerdns.normalize_record_name("http://blagh.co/", domain), "@")
        self.assertEqual(powerdns.normalize_record_name("https://blagh.co:443", domain), "@")

        # Subdomains
        self.assertEqual(powerdns.normalize_record_name("api", domain), "api")
        self.assertEqual(powerdns.normalize_record_name("api.blagh.co", domain), "api")
        self.assertEqual(powerdns.normalize_record_name("api.blagh.co.", domain), "api")
        self.assertEqual(powerdns.normalize_record_name("http://api", domain), "api")
        self.assertEqual(powerdns.normalize_record_name("https://sub.domain.blagh.co/", domain), "sub.domain")

    def test_format_record_content_a_record(self):
        # Plain IP
        self.assertEqual(powerdns.format_record_content("A", "194.62.97.174"), "194.62.97.174")
        # URL with port and path
        self.assertEqual(powerdns.format_record_content("A", "http://194.62.97.174:8080/index.html"), "194.62.97.174")
        self.assertEqual(powerdns.format_record_content("A", "https://194.62.97.174/"), "194.62.97.174")
        # CIDR notation
        self.assertEqual(powerdns.format_record_content("A", "194.62.97.174/32"), "194.62.97.174")
        self.assertEqual(powerdns.format_record_content("A", " 194.62.97.174 "), "194.62.97.174")

    def test_format_record_content_aaaa_record(self):
        self.assertEqual(powerdns.format_record_content("AAAA", "2001:db8::1"), "2001:db8::1")
        self.assertEqual(powerdns.format_record_content("AAAA", "[2001:db8::1]"), "2001:db8::1")
        self.assertEqual(powerdns.format_record_content("AAAA", "http://[2001:db8::1]:8080/"), "2001:db8::1")
        self.assertEqual(powerdns.format_record_content("AAAA", "2001:db8::1/64"), "2001:db8::1")

    def test_format_record_content_ns_and_cname(self):
        domain = "blagh.co"
        # NS auto-appends trailing dot
        self.assertEqual(powerdns.format_record_content("NS", "ns1.blagh.co", domain), "ns1.blagh.co.")
        self.assertEqual(powerdns.format_record_content("NS", "ns1.blagh.co.", domain), "ns1.blagh.co.")
        self.assertEqual(powerdns.format_record_content("NS", "https://ns2.blagh.co/path", domain), "ns2.blagh.co.")
        
        # CNAME auto-appends trailing dot and supports @
        self.assertEqual(powerdns.format_record_content("CNAME", "target.example.com", domain), "target.example.com.")
        self.assertEqual(powerdns.format_record_content("CNAME", "@", domain), "blagh.co.")

    def test_format_record_content_mx_record(self):
        domain = "blagh.co"
        # Missing priority defaults to 10
        self.assertEqual(powerdns.format_record_content("MX", "mail.blagh.co", domain), "10 mail.blagh.co.")
        self.assertEqual(powerdns.format_record_content("MX", "http://mail.blagh.co/", domain), "10 mail.blagh.co.")
        # Explicit priority
        self.assertEqual(powerdns.format_record_content("MX", "20 mail2.blagh.co", domain), "20 mail2.blagh.co.")
        self.assertEqual(powerdns.format_record_content("MX", "5 mail.blagh.co.", domain), "5 mail.blagh.co.")

    def test_format_record_content_txt_record(self):
        # Raw SPF string gets wrapped
        self.assertEqual(powerdns.format_record_content("TXT", "v=spf1 a mx ~all"), '"v=spf1 a mx ~all"')
        # Already quoted string doesn't get double-quoted
        self.assertEqual(powerdns.format_record_content("TXT", '"v=spf1 a mx ~all"'), '"v=spf1 a mx ~all"')
        # Semicolons stripped
        self.assertEqual(powerdns.format_record_content("TXT", 'v=spf1 a mx ~all;'), '"v=spf1 a mx ~all"')

    def test_format_record_content_caa_and_srv(self):
        # CAA missing flag 0
        self.assertEqual(powerdns.format_record_content("CAA", 'issue letsencrypt.org'), '0 issue "letsencrypt.org"')
        self.assertEqual(powerdns.format_record_content("CAA", '0 issue "letsencrypt.org"'), '0 issue "letsencrypt.org"')
        
        # SRV ensures dot
        self.assertEqual(powerdns.format_record_content("SRV", "10 20 443 target.example.com"), "10 20 443 target.example.com.")


class TestDnsRrsetMergingAndDeletion(unittest.IsolatedAsyncioTestCase):
    """Test multi-value RRset merging and per-value deletion."""

    @patch("utils.powerdns.get_zone")
    @patch("utils.powerdns._apply_rrset")
    async def test_add_record_merges_with_existing(self, mock_apply, mock_get_zone):
        # Existing zone has 1 NS record: ns1.blagh.co.
        mock_get_zone.return_value = {
            "rrsets": [{
                "name": "blagh.co.",
                "type": "NS",
                "ttl": 3600,
                "records": [{"content": "ns1.blagh.co.", "disabled": False}],
            }]
        }

        # Add second NS record: ns2.blagh.co
        await powerdns.add_record("blagh.co", "@", "NS", "ns2.blagh.co")

        # Must merge both into the RRset!
        mock_apply.assert_called_once_with(
            "blagh.co.",
            "blagh.co.",
            "NS",
            ["ns1.blagh.co.", "ns2.blagh.co."],
            3600,
        )

    @patch("utils.powerdns.get_zone")
    @patch("utils.powerdns._apply_rrset")
    async def test_add_record_idempotent_duplicate(self, mock_apply, mock_get_zone):
        mock_get_zone.return_value = {
            "rrsets": [{
                "name": "blagh.co.",
                "type": "NS",
                "ttl": 3600,
                "records": [{"content": "ns1.blagh.co.", "disabled": False}],
            }]
        }
        # Add duplicate
        await powerdns.add_record("blagh.co", "@", "NS", "ns1.blagh.co.")
        mock_apply.assert_not_called()

    @patch("utils.powerdns.get_zone")
    @patch("utils.powerdns._apply_rrset")
    async def test_delete_single_record_from_multivalue_rrset(self, mock_apply, mock_get_zone):
        mock_get_zone.return_value = {
            "rrsets": [{
                "name": "blagh.co.",
                "type": "NS",
                "ttl": 3600,
                "records": [
                    {"content": "ns1.blagh.co.", "disabled": False},
                    {"content": "ns2.blagh.co.", "disabled": False},
                ],
            }]
        }

        # Delete only ns1
        await powerdns.delete_record("blagh.co", "@", "NS", content="ns1.blagh.co.")

        # Should update RRset with only ns2 remaining
        mock_apply.assert_called_once_with(
            "blagh.co.",
            "blagh.co.",
            "NS",
            ["ns2.blagh.co."],
            3600,
        )


class TestDnsDiagnosticService(unittest.IsolatedAsyncioTestCase):
    """Test DNS Diagnostic Engine checks and error classification."""

    @patch("services.dns_diagnostic_service.powerdns.get_zone")
    @patch("services.dns_diagnostic_service._doh_query")
    @patch("services.dns_diagnostic_service._udp_query_async")
    @patch("httpx.AsyncClient.get")
    async def test_diagnose_healthy_domain(self, mock_api_get, mock_udp, mock_doh, mock_get_zone):
        # 1. PowerDNS API response
        mock_api_get.return_value = MagicMock(status_code=200, json=lambda: {"version": "4.7.3"})

        # 2. Zone records: has root A and NS
        mock_get_zone.return_value = {
            "rrsets": [
                {"name": "blagh.co.", "type": "A", "records": [{"content": "194.62.97.174"}]},
                {"name": "blagh.co.", "type": "NS", "records": [{"content": "ns1.blagh.co."}, {"content": "ns2.blagh.co."}]},
            ]
        }

        # 3. Local resolution & external reachability
        mock_udp.return_value = ["194.62.97.174"]

        # 4. Public DoH query: status=0 (NOERROR)
        mock_doh.return_value = {
            "Status": 0,
            "Answer": [{"data": "194.62.97.174"}],
            "Authority": [],
        }

        result = await dns_diagnostic_service.diagnose_domain("blagh.co")

        self.assertEqual(result["domain"], "blagh.co")
        self.assertEqual(result["status"], "healthy")
        self.assertIn("healthy", result["summary"].lower())
        self.assertEqual(len(result["recommendations"]), 0)

    @patch("services.dns_diagnostic_service.powerdns.get_zone")
    @patch("services.dns_diagnostic_service._doh_query")
    @patch("services.dns_diagnostic_service._udp_query_async")
    @patch("httpx.AsyncClient.get")
    async def test_diagnose_firewall_or_servfail_issue(self, mock_api_get, mock_udp, mock_doh, mock_get_zone):
        # PowerDNS daemon is up
        mock_api_get.return_value = MagicMock(status_code=200, json=lambda: {"version": "4.7.3"})

        mock_get_zone.return_value = {
            "rrsets": [
                {"name": "blagh.co.", "type": "A", "records": [{"content": "194.62.97.174"}]},
                {"name": "blagh.co.", "type": "NS", "records": [{"content": "ns1.blagh.co."}]},
            ]
        }

        # Local query works, but external UDP fails
        async def mock_udp_side_effect(host, port, domain, **kwargs):
            if host == "127.0.0.1":
                return ["194.62.97.174"]
            return []
        mock_udp.side_effect = mock_udp_side_effect

        # Public DoH returns SERVFAIL (Status=2)
        mock_doh.return_value = {
            "Status": 2,
            "Answer": [],
            "Authority": [],
        }

        result = await dns_diagnostic_service.diagnose_domain("blagh.co")

        self.assertEqual(result["status"], "error")
        # Recommendation should mention Cloud Firewall
        self.assertTrue(any("Firewall" in rec for rec in result["recommendations"]))


if __name__ == "__main__":
    unittest.main()
