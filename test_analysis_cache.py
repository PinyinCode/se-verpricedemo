# -*- coding: utf-8 -*-
import unittest

from analysis_cache import TTLCache


class TestTTLCache(unittest.TestCase):
    def test_cache_returns_copies(self):
        cache = TTLCache()
        original = {"items": [1, 2]}
        cache.set("a", original)
        original["items"].append(3)
        result = cache.get("a")
        result["items"].append(4)
        self.assertEqual(cache.get("a"), {"items": [1, 2]})

    def test_lru_eviction_is_bounded(self):
        cache = TTLCache(max_items=2, ttl_seconds=60)
        cache.set("a", 1)
        cache.set("b", 2)
        self.assertEqual(cache.get("a"), 1)
        cache.set("c", 3)
        self.assertIsNone(cache.get("b"))
        self.assertEqual(cache.stats()["size"], 2)

    def test_miss_and_hit_counters(self):
        cache = TTLCache()
        self.assertIsNone(cache.get("missing"))
        cache.set("ok", 1)
        self.assertEqual(cache.get("ok"), 1)
        self.assertEqual(cache.stats()["hits"], 1)
        self.assertEqual(cache.stats()["misses"], 1)


    def test_non_finite_ttl_and_non_integer_capacity_are_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf"), 0, -1, True):
            with self.subTest(ttl=value):
                with self.assertRaises(ValueError):
                    TTLCache(ttl_seconds=value)
        with self.assertRaises(ValueError):
            TTLCache(max_items=1.5)
        with self.assertRaises(ValueError):
            TTLCache(ttl_seconds=True)
        cache = TTLCache()
        for value in (float("nan"), float("inf"), float("-inf"), 0, -1):
            with self.subTest(set_ttl=value):
                with self.assertRaises(ValueError):
                    cache.set("x", 1, ttl_seconds=value)

    def test_invalid_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            TTLCache(max_items=0)


if __name__ == "__main__":
    unittest.main()
