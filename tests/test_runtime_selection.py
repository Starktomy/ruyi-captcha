import os
import unittest
from unittest.mock import patch

from ruyi_captcha import RuntimeUnavailable
from ruyi_captcha.runtime import load_bindings


class RuntimeSelectionTests(unittest.TestCase):
    def test_invalid_mode_is_rejected(self):
        with patch.dict(os.environ, {"RUYI_CAPTCHA_KERNEL": "invalid"}), self.assertRaises(RuntimeUnavailable):
            load_bindings()

    def test_required_bundle_never_falls_back_to_an_external_kernel(self):
        with patch.dict(os.environ, {"RUYI_CAPTCHA_KERNEL": "bundled"}), \
             patch("ruyi_captcha.runtime.importlib.util.find_spec", return_value=None), \
             self.assertRaises(RuntimeUnavailable):
            load_bindings()

    def test_auto_prefers_the_packaged_private_kernel(self):
        module = object()
        with patch.dict(os.environ, {"RUYI_CAPTCHA_KERNEL": "auto"}), \
             patch("ruyi_captcha.runtime.importlib.util.find_spec", return_value=object()), \
             patch("ruyi_captcha.runtime.importlib.import_module", return_value=module) as load:
            self.assertIs(load_bindings(), module)
            load.assert_called_once_with("ruyi_captcha._vendor.ruyijs")


if __name__ == "__main__":
    unittest.main()
