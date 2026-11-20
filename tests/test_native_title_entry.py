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

    def test_startup_requires_a_same_frame_idle_title_and_absolute_windows_path(self):
        snapshot = dict(mode="idle", state="ready", map=title.MAP, sequence=29,
                        native_title_entry={"state": "ready"})
        directory = r"C:\Silverfish Co-op\campaigns\trial\game-user\Saved"
        fields = [title.STARTUP_TAG, title.MAP, "29", directory]
        self.assertEqual(title.parse_startup(fields, snapshot),
                         dict(map=title.MAP, sequence=29, saved_directory=directory))
        for changed in (dict(mode="host"), dict(sequence=30), dict(native_title_entry={"state": "waiting"})):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                title.parse_startup(fields, snapshot | changed)
        with self.assertRaises(ValueError):
            title.parse_startup([*fields[:3], "Saved/"], snapshot)
