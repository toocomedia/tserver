import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from plugins.roundcube_webmail.service import RoundcubeWebmailService
from plugins.manager import PluginManager


class RoundcubeLaunchTokenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ, {"ROUNDCUBE_WEBMAIL_DATA_DIR": self.temp.name}
        )
        self.env.start()
        self.service = RoundcubeWebmailService()
        self.service.data_dir.mkdir(parents=True)
        self.service.secret_path.write_bytes(b"a" * 64)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_token_contains_only_mailbox_and_expiration(self):
        token = self.service.create_launch_token("User@Example.com", now=1_000)

        self.assertEqual(
            self.service.verify_launch_token(token, now=1_000),
            "user@example.com",
        )

    def test_token_rejects_expiration_tampering_and_another_mailbox(self):
        token = self.service.create_launch_token("user@example.com", now=1_000)

        with self.assertRaises(ValueError):
            self.service.verify_launch_token(token, now=1_061)
        with self.assertRaises(ValueError):
            self.service.verify_launch_token(token[:-1] + "x", now=1_000)
        with self.assertRaises(ValueError):
            self.service.verify_launch_token(
                token,
                now=1_000,
                expected_email="other@example.com",
            )


class RoundcubeLifecycleTests(unittest.TestCase):
    def test_old_container_config_is_detected_for_automatic_refresh(self):
        service = RoundcubeWebmailService()
        service.is_installed = Mock(return_value=True)
        service._run = Mock(
            return_value=subprocess.CompletedProcess([], 0, "\n", "")
        )

        self.assertTrue(service.needs_reconcile())

        service._run.return_value = subprocess.CompletedProcess(
            [], 0, f"{service.config_version}\n", ""
        )
        self.assertFalse(service.needs_reconcile())

    def test_disable_stops_and_verifies_container(self):
        service = RoundcubeWebmailService()
        service.is_installed = Mock(return_value=True)
        service._run = Mock(
            return_value=subprocess.CompletedProcess([], 0, "", "")
        )
        service.get_status = Mock(return_value={"running": False})

        service.pause()

        self.assertEqual(
            service._run.call_args.args[0],
            ["docker", "stop", "--time", "10", service.container_name],
        )
        service.get_status.assert_called_once()

    def test_enable_starts_and_waits_for_health(self):
        service = RoundcubeWebmailService()
        service.is_installed = Mock(return_value=True)
        service._run = Mock(
            return_value=subprocess.CompletedProcess([], 0, "", "")
        )
        service.get_status = Mock(
            return_value={"healthy": True, "error": None}
        )

        service.resume()

        self.assertEqual(
            service._run.call_args.args[0],
            ["docker", "start", service.container_name],
        )

    def test_local_state_purge_requires_uninstalled_container(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"ROUNDCUBE_WEBMAIL_DATA_DIR": temp}
        ):
            service = RoundcubeWebmailService()
            service.secret_path.write_text("secret", encoding="utf-8")
            service.state_path.write_text("{}", encoding="utf-8")
            service.is_installed = Mock(return_value=False)

            service.purge_data()

            self.assertFalse(service.secret_path.exists())
            self.assertFalse(service.state_path.exists())

    def test_public_launch_url_requires_ready_ssl(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"ROUNDCUBE_WEBMAIL_DATA_DIR": temp}
        ):
            service = RoundcubeWebmailService()
            service.save_site(
                "example.com",
                {
                    "public_host": "webmail.example.com",
                    "ssl_status": "not_configured",
                },
            )
            self.assertIsNone(service.get_public_url("example.com"))
            self.assertEqual(
                service.get_configured_url("example.com"),
                "http://webmail.example.com/",
            )

            service.update_site("example.com", ssl_status="ready")
            self.assertEqual(
                service.get_public_url("example.com"),
                "https://webmail.example.com/",
            )

    def test_legacy_single_site_state_migrates_without_losing_settings(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"ROUNDCUBE_WEBMAIL_DATA_DIR": temp}
        ):
            service = RoundcubeWebmailService()
            service.data_dir.mkdir(parents=True)
            service.state_path.write_text(
                json.dumps(
                    {
                        "mail_domain": "example.com",
                        "public_host": "webmail.example.com",
                        "dns_managed": True,
                        "ssl_status": "ready",
                    }
                ),
                encoding="utf-8",
            )

            state = service.read_state()

            self.assertEqual(state["schema_version"], 2)
            self.assertEqual(
                state["sites"]["example.com"]["public_host"],
                "webmail.example.com",
            )
            self.assertEqual(
                service.get_public_url("example.com"),
                "https://webmail.example.com/",
            )

    def test_each_mail_domain_resolves_to_its_own_https_site(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"ROUNDCUBE_WEBMAIL_DATA_DIR": temp}
        ):
            service = RoundcubeWebmailService()
            service.save_site(
                "example.com",
                {"public_host": "webmail.example.com", "ssl_status": "ready"},
            )
            service.save_site(
                "example.net",
                {"public_host": "mailbox.example.net", "ssl_status": "ready"},
            )

            self.assertEqual(
                service.get_public_url("example.com"),
                "https://webmail.example.com/",
            )
            self.assertEqual(
                service.get_public_url("example.net"),
                "https://mailbox.example.net/",
            )


class RoundcubePackagingTests(unittest.TestCase):
    def test_installer_uses_labeled_limited_digest_pinned_resources(self):
        plugin = BACKEND / "plugins" / "roundcube_webmail"
        install = (plugin / "scripts" / "install_roundcube.sh").read_text(
            encoding="utf-8"
        )
        uninstall = (plugin / "scripts" / "uninstall_roundcube.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("roundcube/roundcubemail:1.7.2-apache", install)
        self.assertIn("RepoDigests", install)
        self.assertIn('"$IMAGE_REF"', install)
        self.assertIn("--label \"srv-panel.plugin=${PLUGIN_ID}\"", install)
        self.assertIn(
            '--label "srv-panel.config-version=${CONFIG_VERSION}"',
            install,
        )
        self.assertIn("docker network create --label", install)
        self.assertIn('--network "$NETWORK"', install)
        self.assertIn("--memory 256m", install)
        self.assertIn("--memory-swap 256m", install)
        self.assertIn("--cpus 0.50", install)
        self.assertIn('-p "127.0.0.1:${HOST_PORT}:80"', install)
        self.assertIn("ROUNDCUBEMAIL_DB_TYPE=sqlite", install)
        self.assertIn('MAIL_TRANSPORT="local"', install)
        self.assertIn('MAIL_TRANSPORT="tls"', install)
        self.assertIn('MAIL_TRANSPORT="tls_unverified"', install)
        self.assertIn("LOCAL_DOMAIN_VALUES=", install)
        self.assertIn('[[ "$DOMAIN" == *.local ]] && continue', install)
        self.assertIn('MAIL_HOST="mail.${MAIL_DOMAIN}"', install)
        self.assertIn('IMAP_PORT="993"', install)
        self.assertIn('IMAP_PORT="143"', install)
        self.assertIn("ROUNDCUBEMAIL_DEFAULT_HOST=${IMAP_HOST}", install)
        self.assertIn("ROUNDCUBEMAIL_SMTP_SERVER=${SMTP_HOST}", install)
        self.assertIn("SMTP_PORT=587", install)
        self.assertIn("SRV_MADDY_TRANSPORT=${MAIL_TRANSPORT}", install)
        self.assertNotIn("volume rm", uninstall)
        self.assertIn('rm -f "$STATE_FILE"', uninstall)
        self.assertIn("intentionally preserved", uninstall)

    def test_roundcube_hook_only_prefills_a_valid_signed_mailbox(self):
        plugin = BACKEND / "plugins" / "roundcube_webmail"
        hook = (
            plugin
            / "scripts"
            / "srvpanel_launch"
            / "srvpanel_launch.php"
        ).read_text(encoding="utf-8")
        maddy_ui = (
            BACKEND / "plugins" / "maddy" / "templates" / "partials" / "_scripts.html"
        ).read_text(encoding="utf-8")
        router = (plugin / "router.py").read_text(encoding="utf-8")
        template = (plugin / "templates" / "roundcube_webmail.html").read_text(
            encoding="utf-8"
        )
        javascript = (
            BACKEND / "static" / "js" / "features" / "roundcube-webmail.js"
        ).read_text(encoding="utf-8")
        roundcube_config = (
            plugin / "scripts" / "roundcube-config.inc.php"
        ).read_text(encoding="utf-8")

        self.assertIn("hash_equals", hook)
        self.assertIn("FILTER_VALIDATE_EMAIL", hook)
        self.assertIn("time() + 60", hook)
        self.assertIn("input[name=_user]", hook)
        self.assertIn("input[name=_pass]", hook)
        self.assertNotIn("password", hook.lower())
        self.assertIn("const webmailSites =", maddy_ui)
        self.assertIn("window.open('about:blank'", maddy_ui)
        self.assertIn("headers: { Accept: 'application/json' }", maddy_ui)
        self.assertIn(
            'router = APIRouter(prefix="/plugins/roundcube_webmail"',
            router,
        )
        self.assertIn('@router.post("/api/launch")', router)
        self.assertIn('domain = normalized.rsplit("@", 1)[-1]', router)
        self.assertIn('@router.post("/api/sites/add")', router)
        self.assertIn('@router.post("/api/sites/update")', router)
        self.assertIn('@router.post("/api/sites/delete")', router)
        self.assertIn("asyncio.create_task(_issue_ssl_task(domain, host))", router)
        self.assertIn("event.preventDefault()", javascript)
        self.assertIn("Manage DNS with this panel", template)
        self.assertIn("split-layout", template)
        self.assertIn("+ Add Webmail Domain", template)
        self.assertIn('class="roundcube-tabs"', template)
        self.assertIn('data-tab="domain"', template)
        self.assertIn('data-tab="https"', template)
        self.assertIn('data-tab="mail"', template)
        self.assertNotIn('class="section', template)
        self.assertIn("confirm_host_change", javascript)
        self.assertIn("Change webmail hostname?", javascript)
        self.assertIn(
            "if host_changed and not confirm_host_change:",
            router,
        )
        cleanup = router[
            router.index("        if host_changed:")
            : router.index("        elif (", router.index("        if host_changed:"))
        ]
        self.assertLess(
            cleanup.index("await dns_service.delete_record"),
            cleanup.index("await ssl_service.revoke_cert"),
        )
        self.assertLess(
            cleanup.index("await ssl_service.revoke_cert"),
            cleanup.index("await nginx_service.remove_site"),
        )
        self.assertNotIn('id="server-ip"', template)
        self.assertNotIn('id="dns-ttl"', template)
        self.assertIn("DNS_TTL = 300", router)
        self.assertIn("$config['imap_host']", roundcube_config)
        self.assertIn("$config['smtp_host']", roundcube_config)
        self.assertIn("':993'", roundcube_config)
        self.assertIn("':143'", roundcube_config)
        self.assertIn("':587'", roundcube_config)
        self.assertNotIn("$config['default_host']", roundcube_config)
        self.assertNotIn("$config['default_port']", roundcube_config)
        self.assertNotIn("$config['smtp_server']", roundcube_config)
        self.assertIn("'verify_peer' => false", roundcube_config)
        self.assertIn("Test Maddy Connection", template)
        self.assertIn("Detect Mail Security", template)
        self.assertIn('@router.get("/api/mail-diagnostics")', router)


class RoundcubeDataPurgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_purge_requires_exact_confirmation_and_removes_labeled_volume(self):
        manager = PluginManager()
        manager.get_plugin = Mock(
            return_value={
                "id": "roundcube_webmail",
                "installed": False,
                "data_purge": True,
                "dir_path": str(BACKEND / "plugins" / "roundcube_webmail"),
            }
        )
        local_service = Mock()
        manager._find_service = Mock(return_value=local_service)
        docker_service = Mock()
        docker_service.cleanup_plugin_resources.return_value = (True, "1 volumes")

        denied, _ = await manager.purge_plugin_data(
            "roundcube_webmail", "roundcube_webmail"
        )
        self.assertFalse(denied)
        docker_service.cleanup_plugin_resources.assert_not_called()

        with patch(
            "dependencies.dependency_manager.get_service",
            return_value=docker_service,
        ):
            success, _ = await manager.purge_plugin_data(
                "roundcube_webmail", "PURGE roundcube_webmail"
            )

        self.assertTrue(success)
        docker_service.cleanup_plugin_resources.assert_called_once_with(
            "roundcube_webmail",
            purge_data=True,
        )
        local_service.purge_data.assert_called_once()


if __name__ == "__main__":
    unittest.main()
