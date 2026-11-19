"""Run the Agent from the repository root without opening the browser."""

import argparse
import os
import sys
from pathlib import Path

from .agent import Agent, Session
from .data import DATA_DIR, Profile, dump_json
from .reports import export_report, markdown_report

DEMO_QUESTION = "我還缺哪些畢業要求？請規劃 115-1 的課表，12 至 18 學分，週五下午不要排課，優先安排缺少的必修。"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="國立臺北大學資工系選課與畢業學分檢核 Agent")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--demo", action="store_true", help="使用附帶的去識別範例修課紀錄")
    source.add_argument("--transcript", type=Path, help="已修課程 UTF-8 CSV")
    parser.add_argument("--offerings", type=Path, help="自訂同學期開課 CSV")
    parser.add_argument("--department", default="資訊工程學系", help="學籍系所")
    parser.add_argument("--admission-year", type=int, default=114, help="民國入學學年")
    parser.add_argument("--version", help="課規版本")
    parser.add_argument("--year-level", type=int, choices=range(1, 5), default=2, help="本學期年級")
    parser.add_argument("--evidence", action="append", default=[], metavar="RULE_ID=STATE",
                        help="佐證狀態為 confirmed、missing 或 unknown，可重複給定")
    parser.add_argument("--question", "-q", action="append", help="同一會話依序執行一或多個問題")
    parser.add_argument("--json", action="store_true", help="輸出 JSON，含完整追蹤")
    parser.add_argument("--out", type=Path, help="將每次檢核的 Markdown 與 JSON 匯出到此資料夾")
    args = parser.parse_args()
    if not args.question and not args.demo:
        parser.error("請提供 --question，或使用 --demo 執行預設案例")
    try:
        evidence = dict(value.split("=", 1) for value in args.evidence)
        database = os.environ.get("AGENT_STATE_DB")
        agent = Agent(database=Path(database) if database else None)
        profile = Profile(department=args.department, admission_year=args.admission_year,
                          version=args.version if args.version is not None else agent.rules.version,
                          year_level=args.year_level, evidence=evidence)
        path = DATA_DIR / "demo_transcript.csv" if args.demo else args.transcript
        transcript = path.read_bytes() if path is not None else None
        offerings = args.offerings.read_bytes() if args.offerings else None
    except (OSError, ValueError):
        parser.error("無法讀取資料，或學籍／佐證參數格式有誤")
    session = Session(profile)
    results = []
    for question in args.question or [DEMO_QUESTION]:
        result = agent.run(session, question, transcript, offerings)
        results.append(result)
        if args.out:
            try:
                export_report(result, args.out)
            except OSError:
                print("無法寫入匯出資料夾。請檢查掛載目錄與寫入權限。", file=sys.stderr)
                return 3
    if args.json:
        print(dump_json(results[0] if len(results) == 1 else results))
    else:
        print("\n\n".join(markdown_report(result) for result in results))
    return 0 if all(result["status"] == "ok" for result in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
