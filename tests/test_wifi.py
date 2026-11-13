import unittest
from unittest.mock import AsyncMock, patch

from handset import wifi
from pymobiledevice3 import bonjour


class BonjourCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_wrapper_forwards_discovery_options(self):
        original = AsyncMock(return_value=4)
        with (patch.object(wifi, "_bonjour_patched", False),
              patch.object(bonjour, "_bind_ipv4"),
              patch.object(bonjour, "_send_query_all", original)):
            wifi._patch_bonjour()
            result = await bonjour._send_query_all([], b"query", ("utun",))
            self.assertEqual(result, 4)
            original.assert_awaited_once_with([], b"query", ("utun",))


if __name__ == "__main__":
    unittest.main()
