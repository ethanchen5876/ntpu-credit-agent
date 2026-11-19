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
        app.text_area[0].set_value("幫我規劃20學分的課表")
        app.button[0].click().run(timeout=15)
        self.assertFalse(app.exception)
        result = app.session_state["result"]
        self.assertTrue(result["schedule"]["feasible"])
        self.assertEqual(result["schedule"]["credits"], 20)
        self.assertEqual(app.tabs[0].label, "課表與開課")
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

    def test_manual_course_registration_uses_the_same_credit_check(self):
        app = AppTest.from_file("app.py").run(timeout=15)
        app.radio(key="transcript_source0").set_value("用課名登錄").run(timeout=15)
        self.assertFalse(app.exception)
        app.multiselect(key="manual_courses0").set_value(["TU99U30004", "TU99U30001"])
        next(button for button in app.button if button.label == "加入修課紀錄").click().run(timeout=15)
        self.assertFalse(app.exception)
        self.assertEqual(len(app.session_state["registered0"]), 2)

        app.text_area[0].set_value("我還缺哪些畢業要求？")
        next(button for button in app.button if button.label == "執行").click().run(timeout=15)
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["result"]["audit"]["total"], 6)

        app.multiselect(key="manual_courses0").set_value(["TU99U30004"])
        app.selectbox(key="manual_status0").set_value("failed")
        next(button for button in app.button if button.label == "加入修課紀錄").click().run(timeout=15)
        self.assertFalse(app.exception)
        self.assertEqual(len(app.session_state["registered0"]), 2)
        self.assertIsNone(app.session_state["result"])
        next(button for button in app.button if button.label == "執行").click().run(timeout=15)
        self.assertEqual(app.session_state["result"]["audit"]["total"], 3)

        app.multiselect(key="manual_remove0").set_value([("TU99U30001", "114-1")]).run(timeout=15)
        next(button for button in app.button if button.label == "移除所選紀錄").click().run(timeout=15)
        self.assertFalse(app.exception)
        self.assertEqual(len(app.session_state["registered0"]), 1)
        self.assertIsNone(app.session_state["result"])
