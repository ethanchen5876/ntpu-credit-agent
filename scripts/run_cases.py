"""Reproduce the assignment evidence from the frozen inputs."""

import argparse
from pathlib import Path

from credit_agent.__main__ import DEMO_QUESTION
from credit_agent.agent import Agent, Session
from credit_agent.data import DATA_DIR, Profile, dump_json
from credit_agent.llm import RouteModel
from credit_agent.reports import markdown_report


def schedule_codes(result):
    return {item["code"] for item in result.get("schedule", {}).get("issues", [])}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    agent = Agent(model=RouteModel())
    profile = Profile(department=agent.rules.department, admission_year=114, version=agent.rules.version)
    sample = (DATA_DIR / "demo_transcript.csv").read_bytes()
    invalid = b"course_id,term,status\nTU99U30004,114-1,passed\n"
    follow_up = Session(profile)
    cases = [
        ("A01", "官方課表試排與完整追蹤", DEMO_QUESTION, follow_up, sample,
         "已採計 40、可行課表 18、預估 58",
         lambda r: (r["audit"]["total"], r["schedule"]["credits"], r["projection"]["total"]) == (40, 18, 58)),
        ("A02", "追問改上限", "改成最多 15 學分", follow_up, sample,
         "LIMIT_INCOMPATIBLE，撤下舊預估，保留週五時段",
         lambda r: "LIMIT_INCOMPATIBLE" in schedule_codes(r) and "projection" not in r and
         r["preferences"]["blocked"] == ["週五 12:00–18:00"] and r["revision"] == 2),
        ("A03", "調整限制後恢復試排", "改成最多 18 學分", follow_up, sample,
         "恢復 18 學分可行課表，條件版本 3",
         lambda r: r["schedule"]["feasible"] and r["schedule"]["credits"] == 18 and r["revision"] == 3),
        ("A04", "入學年度不符", "檢查畢業學分", Session(profile.model_copy(update={"admission_year": 115})), sample,
         "RULESET_MISMATCH，停止學分計算", lambda r: r["error"]["code"] == "RULESET_MISMATCH" and "audit" not in r),
        ("A05", "代理人課程的先修不足", "驗證 U3063", Session(profile), sample,
         "PREREQUISITE，指出人工智慧導論，沒有學分預估",
         lambda r: "PREREQUISITE" in schedule_codes(r) and "projection" not in r and
         any(i.get("prerequisite") == "TU01U30046" for i in r["schedule"]["issues"])),
        ("A06", "兩個官方班別衝堂", "驗證 U2287、U3270", Session(profile), sample,
         "TIME_CONFLICT，草案未通過",
         lambda r: "TIME_CONFLICT" in schedule_codes(r) and not r["schedule"]["feasible"]),
        ("A07", "修課 CSV 缺學分欄位", "我還缺哪些畢業要求？", Session(profile), invalid,
         "CSV_FIELDS，提示補齊 credits 欄位", lambda r: r["error"]["code"] == "CSV_FIELDS" and "credits" in r["message"]),
        ("A08", "規章摘要與引用", "請查 CPE 規章", Session(profile), None,
         "回傳 CS-CPE 及條次引用", lambda r: r["rules"][0]["rule_id"] == "CS-CPE"),
        ("A09", "尚未公布的學期", "請排 115-2 的課表", Session(profile), sample,
         "TERM_UNAVAILABLE，提示可用 115-1 快照", lambda r: r["error"]["code"] == "TERM_UNAVAILABLE"),
    ]
    summary = []
    for case_id, title, question, session, transcript, expected, check in cases:
        result = agent.run(session, question, transcript)
        try:
            passed = bool(check(result))
        except (KeyError, TypeError, ValueError):
            passed = False
        observed = sorted(schedule_codes(result))
        if result.get("error"):
            observed.append(result["error"]["code"])
        item = {"id": case_id, "title": title, "input": question, "expected": expected,
                "message": result["message"], "codes": observed, "run_id": result["run_id"],
                "elapsed_ms": result["elapsed_ms"], "pass": passed}
        summary.append(item)
        (args.out / f"{case_id}.json").write_text(dump_json({"case": item, "result": result}) + "\n", encoding="utf-8")
        if case_id == "A01":
            (args.out / "A01-report.md").write_text(markdown_report(result), encoding="utf-8")
        print(f"{case_id} {'PASS' if passed else 'FAIL'} {title} ({result['elapsed_ms']} ms)")
    (args.out / "summary.json").write_text(dump_json(summary) + "\n", encoding="utf-8")
    return 0 if all(item["pass"] for item in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
