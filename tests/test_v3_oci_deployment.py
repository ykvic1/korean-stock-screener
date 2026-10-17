"""Static, no-network/no-OAuth regression tests for OCI Always Free deployment."""
from __future__ import annotations

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OCIAlwaysFreePreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer = (ROOT / "deploy/oci_prepare.sh").read_text(encoding="utf-8")
        cls.service = (ROOT / "deploy/kiwoom-investor.service.example").read_text(encoding="utf-8")
        cls.timer = (ROOT / "deploy/kiwoom-investor.timer.example").read_text(encoding="utf-8")
        cls.guide = (ROOT / "KIWOOM_OCI_ALWAYS_FREE_SETUP.md").read_text(encoding="utf-8")
        cls.collector = (ROOT / "v3_kiwoom_auto_collector.py").read_text(encoding="utf-8")

    def test_bootstrap_requires_root_and_explicit_prepare(self):
        self.assertIn('"$1" != "--prepare"', self.installer)
        self.assertIn('"$EUID" -ne 0', self.installer)
        self.assertIn("Python 3.11+ required", self.installer)
        self.assertIn("expected reviewed repository checkout missing", self.installer)

    def test_bootstrap_does_not_start_or_enable_api_calls(self):
        for disallowed in ("systemctl enable", "systemctl start",
                           "systemctl restart", "curl | bash", "curl -fsSL",
                           "api.kiwoom.com", "oauth2/token", "chmod 777"):
            self.assertNotIn(disallowed, self.installer)
        self.assertIn("systemctl daemon-reload", self.installer)
        self.assertIn("oci_preparation_complete_timer_not_enabled", self.installer)

    def test_credentials_not_committed_or_printed_in_installer(self):
        self.assertIn('install -m 0600 -o root -g root /dev/null', self.installer)
        self.assertIn("chmod 0600", self.installer)
        self.assertIn("credential environment file must not be a symlink", self.installer)
        self.assertIn("EnvironmentFile=/etc/korean-stock-screener/kiwoom.env", self.service)
        self.assertIn("ProtectSystem=strict", self.service)
        self.assertIn("ProtectHome=yes", self.service)
        self.assertIn("User=kiwoom", self.service)
        self.assertIn("UMask=0077", self.service)
        self.assertIn("ReadWritePaths=/var/lib/korean-stock-screener/kiwoom", self.service)

    def test_fixed_ip_service_only_calls_readonly_collector(self):
        self.assertIn("v3_kiwoom_auto_collector.py --live --codes 252990", self.service)
        for forbidden in ("screener.py", "daily.yml", "market-order", "order-cash"):
            self.assertNotIn(forbidden, self.service)
        self.assertIn("NoNewPrivileges=yes", self.service)
        self.assertIn("PrivateTmp=yes", self.service)

    def test_timer_is_weekday_only_kst_and_no_unrequested_boot_catchup(self):
        self.assertIn("OnCalendar=Mon..Fri *-*-* 19:30:00", self.timer)
        self.assertIn("Persistent=false", self.timer)
        self.assertIn("Asia/Seoul", self.timer)
        self.assertIn("sudo timedatectl set-timezone Asia/Seoul", self.guide)
        self.assertNotIn("systemctl enable --now", self.installer)

    def test_correct_2026_free_limits_and_ip_type_distinction(self):
        self.assertIn("1,500 OCPU", self.guide)
        self.assertIn("9,000 GB", self.guide)
        self.assertIn("VM.Standard.E2.1.Micro", self.guide)
        self.assertIn("VM.Standard.A1.Flex", self.guide)
        self.assertIn("ephemeral", self.guide.lower())
        self.assertIn("Reserved", self.guide)
        self.assertIn("회수", self.guide)

    def test_private_scoring_and_cross_feed_requirements_do_not_shift(self):
        self.assertIn("confirmed_scoring_allowed", self.collector)
        self.assertIn("source_agreement_check_required", self.collector)
        self.assertIn("독립", self.guide)
        self.assertIn("다른 공급원", self.guide)
        self.assertIn("별도 개발", self.guide)

    def test_templates_contain_only_placeholders(self):
        sample = (ROOT / "deploy/kiwoom.env.example").read_text(encoding="utf-8")
        self.assertIn("REPLACE_ON_SERVER_ONLY", sample)
        self.assertNotRegex(sample, r"KIWOOM_APP_KEY=[A-Za-z0-9]{25,}")


if __name__ == "__main__":
    unittest.main()
