import unittest

from streamlit.testing.v1 import AppTest


class InterfaceTests(unittest.TestCase):
    def test_default_demo_follow_up_and_reset(self):
        app = AppTest.from_file("app.py").run(timeout=15)
        self.assertFalse(app.exception)
        app.button[0].click().run(timeout=15)
        self.assertFalse(app.exception)
        self.assertTrue(app.session_state["result"]["schedule"]["feasible"])
        self.assertEqual(app.metric[0].value, "40／132 學分")
        self.assertEqual(app.metric[2].value, "58 學分")
        app.text_area[0].set_value("改成最多 15 學分")
        app.button[0].click().run(timeout=15)
        self.assertFalse(app.exception)
        self.assertFalse(app.session_state["result"]["schedule"]["feasible"])
        self.assertEqual(len(app.metric), 2)
        for button in app.button:
            if button.label == "清除本次會話":
                button.click().run(timeout=15)
                break
        self.assertFalse(app.exception)
        self.assertIsNone(app.session_state["result"])
