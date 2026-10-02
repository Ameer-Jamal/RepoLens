import sys
import unittest
from PyQt5.QtWidgets import QApplication

from ui.TypographyController import TypographyController


app = QApplication.instance() or QApplication(sys.argv)


class _MockConfig:
    def __init__(self, size=14):
        self._size = size

    def get_diff_font_size(self):
        return self._size

    def set_diff_font_size(self, size):
        self._size = size


class TypographyControllerTests(unittest.TestCase):
    def test_initial_font_size_from_config(self):
        cfg = _MockConfig(16)
        tc = TypographyController(cfg)
        self.assertEqual(tc.font_size, 16)

    def test_zoom_in_and_out(self):
        cfg = _MockConfig(13)
        tc = TypographyController(cfg)

        tc.zoom_in()
        self.assertEqual(tc.font_size, 14)
        self.assertEqual(cfg._size, 14)

        tc.zoom_out()
        self.assertEqual(tc.font_size, 13)
        self.assertEqual(cfg._size, 13)

    def test_clamping_limits(self):
        cfg = _MockConfig(10)
        tc = TypographyController(cfg)

        tc.set_font_size(5)
        self.assertEqual(tc.font_size, TypographyController.MIN_FONT_SIZE)

        tc.set_font_size(50)
        self.assertEqual(tc.font_size, TypographyController.MAX_FONT_SIZE)

    def test_toolbar_widget_creation(self):
        cfg = _MockConfig(13)
        tc = TypographyController(cfg)
        widget = tc.create_toolbar_widget()
        self.assertIsNotNone(widget)


if __name__ == "__main__":
    unittest.main()
