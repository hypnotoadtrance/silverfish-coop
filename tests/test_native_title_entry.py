import unittest

from tools.silverfish import native_title_entry as title


class StockTitleReportTests(unittest.TestCase):
    def test_ready_requires_current_stock_menu_in_standalone_title(self):
        fields = [title.TAG, title.MAP, "ready", "PlayerControler_FP_C /Game/Title.Controller",
                  "MainMenu_Widget_C /Game/Title.Menu", "GameViewportClient /Engine/Transient.Viewport",
                  "1", "1", "1", "1", "-"]
        result = title.parse(fields, "ready", title.MAP)
        self.assertTrue(result["top"] and result["mounted"] and result["visible"])
        with self.assertRaises(ValueError):
            title.parse(fields, "hosting", title.MAP)
        fields[8] = "0"
        with self.assertRaises(ValueError):
            title.parse(fields, "ready", title.MAP)
