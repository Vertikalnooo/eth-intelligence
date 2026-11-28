import json
import unittest
from public_market_offline import parse_context

class PublicMarketTests(unittest.TestCase):
    def fixture(self):
        return [{"universe":[{"name":"BTC"},{"name":"ETH"}]},[{},{"markPx":"2000.5","funding":"-0.0001","openInterest":"100","dayNtlVlm":"200000"}]]
    def parse(self,p):
        return parse_context(json.dumps(p).encode())
    def test_valid(self):
        result=self.parse(self.fixture())
        self.assertEqual(result["mark_price"],"2000.5")
        self.assertEqual(result["funding_rate"],"-0.0001")
        self.assertEqual(result["open_interest"],"100")
        self.assertEqual(result["daily_notional_volume"],"200000")
        for key in ("recommendation_eligible","exchange_freshness_verified","stage2_complete"):
            self.assertIs(result[key],False)
        self.assertIsNone(result["exchange_timestamp"])
    def test_invalid_numbers(self):
        for field in ("markPx","funding","openInterest","dayNtlVlm"):
            for value in (None,True,1,"NaN","Infinity","1e100"," 1","1"*65):
                with self.subTest(field=field,value=value):
                    p=self.fixture()
                    p[1][1][field]=value
                    with self.assertRaises(ValueError): self.parse(p)
    def test_ranges(self):
        for field,value in (("markPx","0"),("markPx","-1"),("openInterest","-1"),("dayNtlVlm","-1")):
            p=self.fixture()
            p[1][1][field]=value
            with self.assertRaises(ValueError): self.parse(p)
    def test_schema(self):
        for p in (None,{},[],[{},[]],[{"universe":[{"name":"ETH"},{"name":"ETH"}]},[{},{}]],[{"universe":[{"name":"BTC"}]},[{}]]):
            with self.assertRaises(ValueError): self.parse(p)
    def test_duplicate_and_size(self):
        for raw in (b'{"a":1,"a":2}',b" "*2000001):
            with self.assertRaises(ValueError): parse_context(raw)
