import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from credit_agent.__main__ import DEMO_QUESTION
from credit_agent.agent import Agent, Session
from credit_agent.data import DATA_DIR, Course, Profile, TranscriptRow, import_offerings, import_transcript, parse_meetings
from credit_agent.llm import RouteModel
from credit_agent.reports import export_report
from credit_agent.tools import audit_credits, conflicts


class AgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = Agent(model=RouteModel())
        cls.rules = cls.agent.rules
        cls.sample = (DATA_DIR / "demo_transcript.csv").read_bytes()
        cls.rows = import_transcript(cls.sample, cls.rules)
        cls.offers = import_offerings(cls.agent.public_offerings, cls.rules, "115-1")

    def session(self, **overrides):
        settings = {"department": self.rules.department, "admission_year": 114, "version": self.rules.version}
        return Session(Profile(**(settings | overrides)))

    def run_agent(self, question=DEMO_QUESTION, **kwargs):
        session = kwargs.pop("session", self.session())
        transcript = kwargs.pop("transcript", self.sample)
        return self.agent.run(session, question, transcript, **kwargs)

    def test_real_snapshot_and_credit_total(self):
        self.assertEqual(len(self.offers), 48)
        self.assertEqual(self.offers["U3063"].course_id, "TU01U30077")
        self.assertEqual(self.offers["U3063"].meetings[0].label(), "週一 09:10–10:00")
        result = self.run_agent("我還缺哪些畢業要求？")
        self.assertEqual(result["audit"]["total"], 40)
        self.assertEqual(result["audit"]["missing_total"], 92)
        self.assertEqual(len(result["audit"]["missing_required"]), 16)

    def test_plan_full_trace_and_projection(self):
        result = self.run_agent()
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["schedule"]["feasible"])
        self.assertEqual(result["schedule"]["credits"], 18)
        self.assertEqual(result["schedule"]["effective_minimum"], 16)
        self.assertEqual(result["projection"]["total"], 58)
        self.assertEqual(result["replans"], 0)
        self.assertEqual(result["trace"][0]["event"], "input")
        self.assertEqual(result["trace"][-1]["event"], "result")
        tools = [step["message"] for step in result["trace"] if step["event"] == "tool_call"]
        self.assertEqual(tools, ["match_rules", "lookup_rules", "import_transcript", "audit_credits",
                                 "import_offerings", "find_offerings", "draft_schedule", "validate_schedule",
                                 "audit_credits"])
        self.assertTrue(all(not (m["label"].startswith("週五") and m["label"][3:8] >= "12:00")
                            for c in result["schedule"]["classes"] for m in c["meetings"]))

    def test_profile_year_mismatch_stops_before_audit(self):
        result = self.run_agent(session=self.session(admission_year=115))
        self.assertEqual(result["error"]["code"], "RULESET_MISMATCH")
        self.assertNotIn("audit", result)
        self.assertEqual(result["trace"][3]["status"], "error")

    def test_missing_year(self):
        result = self.run_agent(session=self.session(admission_year=None))
        self.assertEqual(result["error"]["code"], "PROFILE_REQUIRED")

    def test_no_future_offerings(self):
        result = self.run_agent("請規劃 115-2 的課表")
        self.assertEqual(result["error"]["code"], "TERM_UNAVAILABLE")
        self.assertNotIn("schedule", result)

    def test_invalid_csv_is_actionable(self):
        invalid = ("course_id,term,status\nTU99U30004,114-1,passed\n").encode()
        result = self.run_agent(transcript=invalid)
        self.assertEqual(result["error"]["code"], "CSV_FIELDS")
        self.assertIn("credits", result["error"]["message"])
        self.assertNotIn("projection", result)

    def test_negative_unknown_and_personal_fields(self):
        for raw, code in [
            (b"course_id,term,credits,status\nTU99U30004,114-1,-3,passed\n", "TRANSCRIPT_VALUE"),
            (b"course_id,term,credits,status\nNO-COURSE,114-1,3,passed\n", "UNKNOWN_COURSE"),
            (b"course_id,term,credits,status,name\nTU99U30004,114-1,3,passed,PRIVATE\n", "CSV_FIELDS"),
        ]:
            with self.subTest(code=code):
                result = self.run_agent(transcript=raw)
                self.assertEqual(result["error"]["code"], code)
                self.assertNotIn("PRIVATE", json.dumps(result, ensure_ascii=False, default=str))

    def test_transcript_deduplicates_and_gates_transfer(self):
        result = self.run_agent("學分檢核")
        details = result["audit"]["detail"]
        self.assertEqual(sum(row["counted"] for row in details if row["course_id"] == "TU99U30004"), 3)
        expected = {"failed": 0, "transfer_approved": 3, "transfer_pending": 0, "in_progress": 0}
        for state, value in expected.items():
            self.assertEqual(next(row["counted"] for row in details if row["status"] == state), value)
        self.assertEqual(len(result["audit"]["pending"]), 1)

    def test_credit_floor_invalidates_previous_projection(self):
        session = self.session()
        first = self.run_agent(session=session)
        lower = self.run_agent("改成最多 15 學分", session=session)
        recovery = self.run_agent("改成最多 18 學分", session=session)
        self.assertEqual(lower["revision"], first["revision"] + 1)
        self.assertEqual(lower["preferences"]["blocked"], first["preferences"]["blocked"])
        self.assertEqual(lower["status"], "blocked_schedule")
        self.assertIn("LIMIT_INCOMPATIBLE", [item["code"] for item in lower["schedule"]["issues"]])
        self.assertNotIn("projection", lower)
        self.assertTrue(recovery["schedule"]["feasible"])
        self.assertEqual(len(session.recent), 3)

    def test_prerequisite_and_schedule_conflict_in_official_sections(self):
        cases = [("驗證 U3063", "PREREQUISITE"), ("驗證 U2287、U3270", "TIME_CONFLICT"),
                 ("驗證 U3269", "REVIEW_OFFERING"), ("驗證 U9999", "UNKNOWN_SECTION")]
        for question, code in cases:
            with self.subTest(code=code):
                result = self.run_agent(question)
                self.assertNotIn("projection", result)
                self.assertIn(code, [item["code"] for item in result["schedule"]["issues"]])

    def test_adjacent_odd_even_unknown_weeks(self):
        left, right = self.offers["U2287"], self.offers["U3270"]
        adjacent = {"meetings": parse_meetings("3@12:00-13:00@all")}
        self.assertEqual(conflicts(left, right.model_copy(update=adjacent), self.rules.catalog), [])
        odd = left.model_copy(update={"meetings": parse_meetings("3@09:00-11:00@odd")})
        even = right.model_copy(update={"meetings": parse_meetings("3@10:00-11:00@even")})
        self.assertEqual(conflicts(odd, even, self.rules.catalog), [])
        unknown = right.model_copy(update={"meetings": parse_meetings("3@10:00-11:00@?")})
        self.assertEqual(conflicts(odd, unknown, self.rules.catalog)[0]["code"], "WEEKS_UNKNOWN")

    def test_known_terms_at_two_schedule_endpoints(self):
        result = self.run_agent("查詢 115 年度第 1 學期開課 U3063", transcript=None)
        self.assertEqual(result["courses"][0]["section_id"], "U3063")
        mixed = self.agent.public_offerings.replace(b",115-1,", b",115-2,", 1)
        result = self.run_agent(offerings=mixed)
        self.assertEqual(result["error"]["code"], "TERM_MISMATCH")

    def test_general_education_credits_and_dimensions_are_independent(self):
        chosen = [(code, c) for code, c in self.rules.catalog.items() if c.dimension in {1, 2, 3, 4}]
        records = tuple(TranscriptRow(course_id=code, term="114-1", credits=c.credits, status="passed")
                        for code, c in chosen)
        audit = audit_credits(self.rules, self.session().profile, records).report
        general = next(c for c in audit["categories"] if c["key"] == "general")
        self.assertEqual(general["counted"], 16)
        self.assertEqual(general["dimensions_missing"], 1)

    def test_shared_limit_and_common_sublimit(self):
        extensions = {}
        for index in range(9):
            category = "common_elective" if index < 3 else "external"
            extensions[f"CASE-{index}"] = Course(title=f"分類測試課 {index}", credits=3, category=category,
                                                source="CS-EXTERNAL", approval_required=True)
        rules = self.rules.model_copy(update={"catalog": self.rules.catalog | extensions})
        records = tuple(TranscriptRow(course_id=code, term="114-2", credits=course.credits,
                                      status="passed", approval_ref="APR-CASE")
                        for code, course in extensions.items())
        report = audit_credits(rules, self.session().profile, records).report
        counts = {c["key"]: c["counted"] for c in report["categories"]}
        self.assertEqual(counts["common_elective"], 6)
        self.assertEqual(counts["external"], 14)
        self.assertEqual(report["combined_cap"]["counted"], 20)
        self.assertEqual(sum(item["credits"] - item["counted"] for item in report["detail"]), 7)

    def test_total_met_but_required_course_missing(self):
        records = []
        for code, c in self.rules.catalog.items():
            if c.category in {"major_required", "major_elective"} and code != "TU01U30003":
                records.append(TranscriptRow(course_id=code, term="114-2", credits=c.credits, status="passed"))
        report = audit_credits(self.rules, self.session().profile, tuple(records)).report
        self.assertEqual(report["missing_total"], 0)
        self.assertEqual([item["course_id"] for item in report["missing_required"]], ["TU01U30003"])
        self.assertEqual(report["status"], "尚缺")

    def test_english_same_term_and_full_year_evidence(self):
        def check(*codes):
            base = [TranscriptRow(course_id=code, term=term, credits=2, status="passed")
                    for code, term in codes]
            return next(c["counted"] for c in audit_credits(
                self.rules, self.session().profile, tuple(base)).report["categories"] if c["key"] == "english")
        self.assertEqual(check(("EN-CONTEMP", "114-1"), ("EN-BUSINESS", "114-1")), 2)
        self.assertEqual(check(("EN-YEAR-1", "114-1")), 0)
        self.assertEqual(check(("EN-YEAR-1", "114-1"), ("EN-YEAR-2", "114-2")), 4)

    def test_learning_gate_and_retrieval_source(self):
        result = self.run_agent("查 CPE 規章", transcript=None)
        self.assertEqual(result["task"], "rules")
        self.assertEqual(result["rules"][0]["rule_id"], "CS-CPE")
        self.assertIn("第 3、5、6 條", result["rules"][0]["locator"])
        audit = self.run_agent("畢業學分檢核")["audit"]
        self.assertTrue(all(row["state"] == "unknown" for row in audit["evidence"]))

    def test_export_and_sqlite_keep_only_summary_in_log(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder)
            database = destination / "state.sqlite3"
            agent = Agent(database=database, model=RouteModel())
            result = agent.run(self.session(), DEMO_QUESTION, self.sample)
            md, raw = export_report(result, destination)
            report = json.loads(raw.read_text(encoding="utf-8"))
            with sqlite3.connect(database) as db:
                row = db.execute("SELECT summary FROM agent_runs").fetchone()[0]
            self.assertEqual(report["projection"]["total"], "58")
            self.assertIn("58 學分", md.read_text(encoding="utf-8"))
            self.assertNotIn(DEMO_QUESTION, row)
            self.assertNotIn("TU99U30004", row)
            self.assertEqual(json.loads(row)["input_sha256"], report["input_sha256"])

    def test_llm_transient_failure_is_bounded_and_private(self):
        model = RouteModel(enabled=True, key="unused-test-key")
        agent = Agent(model=model)
        question = "畢業學分檢核 規章。私人資料 TEST-PRIVATE-2641"
        with patch("credit_agent.llm.urlopen", side_effect=URLError("offline")) as mocked:
            result = agent.run(self.session(), question, self.sample)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(result["audit"]["total"], 40)
        self.assertEqual(result["llm_calls"], 2)
        outbound = mocked.call_args.args[0].data.decode()
        self.assertNotIn("TEST-PRIVATE-2641", outbound)
        self.assertNotIn("TU99U30004", outbound)
        self.assertNotIn("unused-test-key", json.dumps(result, default=str))


if __name__ == "__main__":
    unittest.main()
