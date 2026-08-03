import unittest

from aurum_bot.models import AccountConfig


class AccountConfigTests(unittest.TestCase):
    def test_explicit_alias_takes_precedence(self):
        account = AccountConfig(
            name="test",
            enabled=True,
            risk_base_usd=1000,
            terminal_path="terminal64.exe",
            symbols={
                "XAUUSD": "GOLD",
                "XAGUSD": "SILVER",
                "GBPUSD": "GBPUSD.pro",
            },
        )

        self.assertEqual(account.broker_symbol("XAUUSD"), "GOLD")
        self.assertEqual(account.broker_symbol("XAGUSD"), "SILVER")
        self.assertEqual(account.broker_symbol("GBPUSD"), "GBPUSD.pro")

    def test_unmapped_forex_pair_uses_its_own_name(self):
        account = AccountConfig(
            name="test",
            enabled=True,
            risk_base_usd=1000,
            terminal_path="terminal64.exe",
            symbols={"XAUUSD": "GOLD", "DE40": "#Germany40"},
        )

        self.assertEqual(account.broker_symbol("gbpjpy"), "GBPJPY")

    def test_fixed_forex_commission_and_zero_commission_index(self):
        account = AccountConfig(
            name="test",
            enabled=True,
            risk_base_usd=1000,
            terminal_path="terminal64.exe",
            symbols={
                "XAUUSD": "GOLD",
                "XAGUSD": "SILVER",
                "DE40": "#Germany40",
            },
            commission_per_lot_usd={"FOREX": 7.0, "XAGUSD": 7.0},
        )

        self.assertEqual(account.commission_for_one_lot("GBPUSD", 1.3, 100_000), 7)
        self.assertEqual(account.commission_for_one_lot("XAGUSD", 57.948, 5_000), 7)
        self.assertEqual(account.commission_for_one_lot("DE40", 25_000, 1), 0)
        self.assertTrue(account.has_configured_commission("GBPUSD"))
        self.assertTrue(account.has_configured_commission("XAGUSD"))
        self.assertFalse(account.has_configured_commission("DE40"))

    def test_notional_commission_overrides_forex_fallback(self):
        account = AccountConfig(
            name="test",
            enabled=True,
            risk_base_usd=1000,
            terminal_path="terminal64.exe",
            symbols={"XAUUSD": "GOLD", "DE40": "#Germany40"},
            commission_per_lot_usd={"FOREX": 7.0},
            commission_rate_percent={"XAUUSD": 0.0016},
        )

        self.assertAlmostEqual(
            account.commission_for_one_lot("XAUUSD", 4100, 100),
            6.56,
        )

    def test_non_forex_symbol_requires_explicit_account_mapping(self):
        fxpro = AccountConfig(
            name="fxpro",
            enabled=True,
            risk_base_usd=1068,
            terminal_path="terminal64.exe",
            symbols={"XAUUSD": "GOLD", "DE40": "#Germany40", "US100": "#USNDAQ100"},
            commission_per_lot_usd={"US100": 1.5},
        )
        account_without_us100 = AccountConfig(
            name="secondary_demo",
            enabled=True,
            risk_base_usd=25_000,
            terminal_path="terminal64.exe",
            symbols={"XAUUSD": "XAUUSD", "DE40": "GER30"},
        )

        self.assertTrue(fxpro.supports_symbol("US100"))
        self.assertEqual(fxpro.broker_symbol("US100"), "#USNDAQ100")
        self.assertEqual(fxpro.commission_for_one_lot("US100", 27808.7, 1), 1.5)
        self.assertFalse(account_without_us100.supports_symbol("US100"))
        with self.assertRaises(KeyError):
            account_without_us100.broker_symbol("US100")


if __name__ == "__main__":
    unittest.main()
