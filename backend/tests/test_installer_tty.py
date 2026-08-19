import errno
import os
import select
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

try:
    import pty
except ImportError:  # Windows development hosts do not provide pty.
    pty = None


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install.sh"
GET_SCRIPT = ROOT / "scripts" / "get.sh"
CAN_RUN_PTY = bool(
    pty is not None
    and os.name == "posix"
    and hasattr(os, "geteuid")
    and os.geteuid() == 0
    and Path("/bin/bash").is_file()
)


class InstallerScriptContractTests(unittest.TestCase):
    def test_bootstrap_redirects_only_the_installer_child_to_tty(self):
        content = GET_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('bash "$CLONE_DIR/scripts/install.sh" </dev/tty', content)
        self.assertNotIn("\nexec </dev/tty", content)

    def test_installer_traps_cancellation_and_fails_closed_on_read_error(self):
        content = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("trap 'cancel_install 130' INT", content)
        self.assertIn("trap 'cancel_install 143' TERM", content)
        self.assertIn("trap 'cancel_install 129' HUP", content)
        self.assertIn("Terminal input closed before installation configuration completed.", content)
        self.assertNotIn('read -r -p "$prompt" REPLY </dev/tty || REPLY=""', content)


@unittest.skipUnless(CAN_RUN_PTY, "Linux root PTY acceptance test")
class InstallerPtyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.release = Path(self.temporary.name) / "os-release"
        self.release.write_text(
            '\n'.join(
                (
                    "ID=ubuntu",
                    'VERSION_ID="24.04"',
                    "VERSION_CODENAME=noble",
                    'PRETTY_NAME="Ubuntu 24.04 LTS"',
                    "",
                )
            ),
            encoding="utf-8",
        )

    def _environment(self):
        environment = os.environ.copy()
        environment.update(
            {
                "SOURCE_DIR": str(ROOT),
                "SRV_OS_RELEASE_FILE": str(self.release),
                "SRV_OS_ARCH": "amd64",
                "SRV_INSTALLER_PREFLIGHT_ONLY": "1",
                "SERVER_IP": "203.0.113.10",
                "NONINTERACTIVE": "0",
            }
        )
        return environment

    def _spawn(self):
        pid, descriptor = pty.fork()
        if pid == 0:
            os.execve(
                "/bin/bash",
                ["bash", str(INSTALLER)],
                self._environment(),
            )
        return pid, descriptor

    @staticmethod
    def _read_until(descriptor, marker, existing=b"", timeout=10):
        data = existing
        deadline = time.monotonic() + timeout
        while marker not in data and time.monotonic() < deadline:
            ready, _, _ = select.select([descriptor], [], [], 0.2)
            if not ready:
                continue
            try:
                chunk = os.read(descriptor, 4096)
            except OSError as error:
                if error.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            data += chunk
        if marker not in data:
            raise AssertionError(f"Installer did not reach prompt {marker!r}. Output: {data!r}")
        return data

    @staticmethod
    def _collect_exit(pid, descriptor, existing=b"", timeout=10):
        data = existing
        deadline = time.monotonic() + timeout
        status = None
        while time.monotonic() < deadline:
            ready, _, _ = select.select([descriptor], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(descriptor, 4096)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    chunk = b""
                data += chunk
            waited, child_status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                status = child_status
                break
        if status is None:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            raise AssertionError(f"Installer did not exit. Output: {data!r}")
        os.close(descriptor)
        return os.waitstatus_to_exitcode(status), data

    def test_interactive_install_reaches_domain_prompt_after_accepting_ip(self):
        pid, descriptor = self._spawn()
        output = self._read_until(descriptor, b"Public SERVER_IP")
        os.write(descriptor, b"\n")
        output = self._read_until(descriptor, b"Use a domain for the panel?", output)
        os.write(descriptor, b"\n")
        output = self._read_until(descriptor, b"CERTBOT_EMAIL", output)
        os.write(descriptor, b"admin@example.com\n")
        output = self._read_until(descriptor, b"Admin username", output)
        os.write(descriptor, b"\n")
        output = self._read_until(descriptor, b"Admin password", output)
        os.write(descriptor, b"CorrectHorse9\n")
        output = self._read_until(descriptor, b"Confirm password", output)
        os.write(descriptor, b"CorrectHorse9\n")

        code, output = self._collect_exit(pid, descriptor, output)
        self.assertEqual(code, 0, output.decode(errors="replace"))
        self.assertIn(b"Installer preflight complete", output)
        self.assertNotIn(b"CorrectHorse9", output)

    def test_prompt_eof_fails_visibly(self):
        pid, descriptor = self._spawn()
        output = self._read_until(descriptor, b"Public SERVER_IP")
        os.write(descriptor, b"\x04")
        code, output = self._collect_exit(pid, descriptor, output)
        self.assertEqual(code, 1)
        self.assertIn(b"Terminal input closed", output)

    def test_ctrl_c_exits_130(self):
        pid, descriptor = self._spawn()
        output = self._read_until(descriptor, b"Public SERVER_IP")
        os.kill(pid, signal.SIGINT)
        code, output = self._collect_exit(pid, descriptor, output)
        self.assertEqual(code, 130)
        self.assertIn(b"Installation cancelled", output)

    def test_fully_noninteractive_configuration_and_missing_variable_error(self):
        environment = self._environment()
        environment.update(
            {
                "NONINTERACTIVE": "1",
                "CERTBOT_EMAIL": "admin@example.com",
                "ADMIN_USER": "admin",
                "ADMIN_PASSWORD": "CorrectHorse9",
            }
        )
        complete = subprocess.run(
            ["/bin/bash", str(INSTALLER)],
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(complete.returncode, 0, complete.stderr.decode())
        self.assertNotIn(b"CorrectHorse9", complete.stdout + complete.stderr)

        environment.pop("CERTBOT_EMAIL")
        missing = subprocess.run(
            ["/bin/bash", str(INSTALLER)],
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(missing.returncode, 1)
        self.assertIn(b"requires CERTBOT_EMAIL", missing.stderr)


if __name__ == "__main__":
    unittest.main()
