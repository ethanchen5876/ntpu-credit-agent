"""A goal-directed agent with a bounded, observation-driven workflow."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from collections import Counter, deque
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from . import __version__
from .data import (
    DATA_DIR, DAY_NAMES, InputError, Profile, Slot, TranscriptRow, dump_json,
    import_offerings, import_transcript, load_rules, load_snapshot, match_rules,
)
from .llm import RouteModel
from .tools import (
    Preferences, audit_credits, draft_schedule, find_offerings, lookup_rules,
    mentions_rule_id, normalized, validate_schedule,
)

INTENT_TERMS = {
    "audit": ("畢業", "缺額", "缺哪些", "還缺", "稽核", "學分檢核", "已修", "採計"),
    "rules": ("規章", "課規", "條文", "規定", "門檻", "cpe", "外語", "通識"),
    "courses": ("開課", "查課", "課程查詢", "有哪些課", "流水號"),
    "plan": ("排課", "選課", "規劃", "安排", "課表", "可修"),
    "validate": ("驗證", "衝堂", "檢查課表"),
}
INTENT_NAMES = {"audit": "學分檢核", "rules": "規章查詢", "courses": "開課查詢",
                "plan": "排課", "validate": "驗證指定課表"}
CREDIT_AMOUNT = r"(\d{1,2}(?:\.\d{1,2})?)"
DAY = r"(?:週|周|星期|禮拜)([一二三四五六日天1-7])(上午|早上|下午|晚上|全天)?"
AVOID = r"(?:不要排課|不要排|不排課|不排|避開|禁排)"
DAY_WINDOWS = {"上午": (0, 720), "早上": (0, 720), "下午": (720, 1080),
               "晚上": (1080, 1440), "全天": (0, 1440), "": (0, 1440)}


@dataclass
class Session:
    profile: Profile
    preferences: Preferences = field(default_factory=Preferences)
    revision: int = 0
    last_task: str | None = None
    last_sections: list[str] = field(default_factory=list)
    recent: deque = field(default_factory=lambda: deque(maxlen=5))


def preference_update(text: str, previous: Preferences) -> tuple[Preferences, bool]:
    bounds = re.search(CREDIT_AMOUNT + r"(?:至|到|~|–|—|-)" + CREDIT_AMOUNT + r"學分", text)
    lower = re.search(r"(?:至少|最少|下限(?:為|改成|改為)?)" + CREDIT_AMOUNT + r"學分", text)
    upper = re.search(r"(?:最多|至多|不超過|上限(?:為|改成|改為)?)" + CREDIT_AMOUNT + r"學分", text)
    minimum = Decimal(lower[1]) if lower else Decimal(bounds[1]) if bounds else previous.minimum
    maximum = Decimal(upper[1]) if upper else Decimal(bounds[2]) if bounds else previous.maximum
    if not 0 <= minimum <= maximum <= 40:
        raise InputError("CREDIT_RANGE", "學分限制需介於 0 至 40，且下限不能大於上限。",
                         "請重述學分範圍，例如「16 至 18 學分」。")

    clear = "取消避開時段" in text or "清除避開時段" in text
    matches = re.findall(AVOID + DAY + "|" + DAY + AVOID, text)
    if re.search(AVOID, text) and not matches and not clear:
        raise InputError("CONSTRAINT_UNSUPPORTED", "目前無法確認你要避開的時段。",
                         "請使用「避開週五下午」或「星期三不要排課」這類句型。")
    slots = []
    for first_day, first_part, second_day, second_part in matches:
        day, part = (first_day, first_part) if first_day else (second_day, second_part)
        day = "日" if day == "天" else day
        start, end = DAY_WINDOWS[part]
        slots.append(Slot(day=int(day) if day.isdigit() else DAY_NAMES.index(day) + 1,
                          start=start, end=end))
    priority = previous.prioritize_required
    priority_change = bool(re.search(r"優先(?:安排)?(?:缺少的)?必修", text))
    if priority_change:
        priority = "不優先" not in text
    updated = replace(previous, minimum=minimum, maximum=maximum,
                      blocked=() if clear else tuple(slots) if matches else previous.blocked,
                      prioritize_required=priority)
    return updated, bool(bounds or lower or upper or matches or clear or priority_change)


def input_hash(question: str, session: Session, transcript: bytes | None, offerings: bytes | None) -> str:
    digest = hashlib.sha256()
    for value in (question.encode("utf-8"), session.profile.model_dump_json().encode("utf-8"),
                  dump_json(session.preferences.summary()).encode("utf-8"),
                  transcript or b"", offerings or b""):
        digest.update(hashlib.sha256(value).digest())
    return digest.hexdigest()


class Run:
    def __init__(self, result: dict):
        self.result = result

    def step(self, event: str, message: str, **payload) -> None:
        self.result["trace"].append({"step": len(self.result["trace"]) + 1, "event": event,
                                     "message": message, **payload})

    def tool(self, name, arguments, function, summarize):
        self.step("tool_call", name, arguments=arguments)
        started = time.perf_counter()
        try:
            value = function()
        except InputError as error:
            self.step("observation", name, status="error", error=error.details(),
                      elapsed_ms=round((time.perf_counter() - started) * 1000, 2))
            raise
        self.step("observation", name, status="ok", output=summarize(value),
                  elapsed_ms=round((time.perf_counter() - started) * 1000, 2))
        return value


class Agent:
    def __init__(self, data_dir: Path = DATA_DIR, database: Path | None = None,
                 model: RouteModel | None = None):
        self.rules = load_rules(data_dir)
        self.snapshot = load_snapshot(data_dir)
        self.public_offerings = (data_dir / "offerings.csv").read_bytes()
        self.database = database
        self.model = model if model is not None else RouteModel.from_environment()

    def run(self, session: Session, question: str, transcript: bytes | None = None,
            offerings: bytes | None = None) -> dict:
        started = time.perf_counter()
        result = {"run_id": uuid4().hex[:16], "created_at": datetime.now(timezone.utc).isoformat(),
                  "agent_version": __version__, "rule_version": self.rules.version,
                  "term": self.snapshot.term, "snapshot": self.snapshot.model_dump(),
                  "input_sha256": input_hash(question, session, transcript, offerings),
                  "status": "ok", "trace": [], "llm_calls": 0, "replans": 0}
        run = Run(result)
        result["snapshot"]["input"] = "內建校方快照" if offerings is None else "使用者匯入"
        run.step("input", "收到本次問題及資料設定", sha256=result["input_sha256"])
        try:
            self._execute(run, session, question, transcript, offerings)
        except InputError as error:
            result.update(status="needs_action", error=error.details(), message=error.message)
            run.step("decision", "資料或條件需要補正，停止目前工作流", code=error.code)
        result["revision"] = session.revision
        result["preferences"] = session.preferences.summary()
        run.step("result", result["message"], status=result["status"])
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        summary = {key: result[key] for key in ("run_id", "created_at", "rule_version", "term",
                                               "input_sha256", "status", "elapsed_ms", "llm_calls")}
        summary.update(task=result.get("task"), revision=session.revision,
                       error_code=result.get("error", {}).get("code"))
        session.recent.append(summary)
        if self.database is not None:
            try:
                write_summary(self.database, summary)
            except (OSError, sqlite3.Error):
                result["log_status"] = "本次執行摘要無法寫入 SQLite。"
        return result

    def _execute(self, run: Run, session: Session, question: str,
                 transcript: bytes | None, imported_offerings: bytes | None) -> None:
        result = run.result
        if not question.strip() or len(question) > 600:
            raise InputError("QUESTION_LENGTH", "請輸入 1 至 600 字的問題。",
                             "例如「我還缺哪些畢業要求？」。")
        run.step("decision", "確認適用的課規版本，再決定要用哪些工具")
        rules = run.tool("match_rules", {"department": "資工系學士班", "admission_year": session.profile.admission_year},
                         lambda: match_rules(session.profile, self.rules),
                         lambda value: {"version": value.version, "course_count": len(value.catalog)})
        text = normalized(question)
        preferences, changed = preference_update(text, session.preferences)
        section_ids = list(dict.fromkeys(re.findall(r"(?<![a-z0-9])[a-z]\d{4}(?![a-z0-9])", text)))
        section_ids = [value.upper() for value in section_ids]
        task = self._route(run, session, text, changed, section_ids)
        result["task"] = task
        old_revision = session.revision
        if preferences != session.preferences:
            session.preferences = preferences
            session.revision += 1
        run.step("decision", f"執行{INTENT_NAMES[task]}工作流", task=task,
                 previous_revision=old_revision, revision=session.revision,
                 invalidated_previous_schedule=old_revision != session.revision)
        if task in {"courses", "plan", "validate"}:
            self._check_term(text)
        defaults = (rules.total_rule_id, "GEN-DIM") if task == "rules" else (rules.total_rule_id,)
        if task in {"plan", "validate"}:
            defaults += ("ENROLL-MIN", "PREREQ-DATA", "PREREQ-AI")
        result["rules"] = run.tool("lookup_rules", {"version": rules.version, "default_rule_ids": defaults},
                                   lambda: lookup_rules(rules, text, defaults),
                                   lambda values: {"rule_ids": [value["rule_id"] for value in values]})
        if task == "rules":
            result["message"] = f"找到 {len(result['rules'])} 則相關規章摘要。"
            return
        if task != "courses":
            if transcript is None:
                raise InputError("TRANSCRIPT_REQUIRED", "學分檢核和排課需要已修課程 CSV。",
                                 "匯入去識別範本，或使用隨附測試資料。")
            rows = run.tool("import_transcript", {"format": "UTF-8 CSV"},
                            lambda: import_transcript(transcript, rules), lambda value: {"rows": len(value)})
            audit = run.tool("audit_credits", {"row_count": len(rows), "version": rules.version},
                             lambda: audit_credits(rules, session.profile, rows),
                             lambda value: {"counted": value.report["total"], "status": value.report["status"],
                                            "required_missing": len(value.report["missing_required"]),
                                            "evidence_pending": sum(e["state"] == "unknown"
                                                                    for e in value.report["evidence"])})
            result["audit"] = audit.report
            if task == "audit":
                result["message"] = f"目前採計 {audit.report['total']}／{rules.total} 學分，檢核結果為{audit.report['status']}。"
                return
        data = self.public_offerings if imported_offerings is None else imported_offerings
        enrolled = run.tool("import_offerings", {"term": self.snapshot.term, "input": result["snapshot"]["input"]},
                            lambda: import_offerings(data, rules, self.snapshot.term), lambda value: {"sections": len(value)})
        result["snapshot"]["section_count"] = len(enrolled)
        if task == "courses":
            found = [item for item in enrolled.values()
                     if item.section_id in section_ids or normalized(rules.catalog[item.course_id].title) in text]
            if not found and section_ids:
                raise InputError("UNKNOWN_SECTION", "查無指定流水號。",
                                 "請核對 115-1 的開課清單。")
            result["courses"] = [{"section_id": item.section_id, "course_id": item.course_id,
                                  "title": rules.catalog[item.course_id].title, "class_name": item.class_name,
                                  "credits": rules.catalog[item.course_id].credits,
                                  "meetings": [slot.label() for slot in item.meetings], "review": item.review}
                                 for item in (found or list(enrolled.values()))]
            result["message"] = f"{self.snapshot.term} 開課清單有 {len(result['courses'])} 筆符合顯示條件。"
            return
        if task == "validate":
            selected = section_ids or (session.last_sections if changed else [])
            if not selected:
                raise InputError("SECTIONS_REQUIRED", "驗證課表需要開課流水號。",
                                 "例如「驗證 U2287、U2345」。")
        else:
            run.step("decision", "已取得學分缺額，搜尋同學期班別並套用先修與時段條件")
            available, excluded = run.tool(
                "find_offerings", {"sections": len(enrolled), **preferences.summary()},
                lambda: find_offerings(enrolled, rules, session.profile, audit, preferences),
                lambda value: {"eligible_sections": len(value[0]),
                               "excluded_reasons": dict(Counter(item["code"] for item in value[1]))},
            )
            run.step("decision", "根據缺額與可用班別產生一份排序式草案")
            selected, skipped = run.tool(
                "draft_schedule", {"eligible_sections": len(available), **preferences.summary()},
                lambda: draft_schedule(available, rules, audit, preferences),
                lambda value: {"section_ids": value[0], "skipped_conflicts": len(value[1])},
            )
            result["excluded"] = excluded + skipped
        run.step("decision", "用獨立檢查重新驗證整份課表")
        schedule = run.tool("validate_schedule", {"section_ids": selected, **preferences.summary()},
                            lambda: validate_schedule(selected, enrolled, rules, session.profile, audit, preferences),
                            lambda value: {"feasible": value["feasible"], "credits": value["credits"],
                                           "issues": [item["code"] for item in value["issues"]]})
        result["schedule"] = schedule
        session.last_task, session.last_sections = task, selected if task == "validate" else []
        if schedule["feasible"]:
            run.step("decision", "草案通過，另算所有候選課通過後的學分")
            assumed = tuple(TranscriptRow(course_id=item["course_id"], term=self.snapshot.term,
                                         credits=item["credits"], status="passed") for item in schedule["classes"])
            estimate = run.tool("audit_credits", {"basis": "所有候選班別通過", "added": len(assumed)},
                                lambda: audit_credits(rules, session.profile, rows + assumed),
                                lambda value: {"projected_total": value.report["total"]})
            result["projection"] = {k: estimate.report[k] for k in ("total", "missing_total", "categories")}
            result["message"] = f"課表通過已載明條件驗證，共 {schedule['credits']} 學分。"
        else:
            run.step("decision", "有未滿足條件，保留草案與原因並結束本次排課")
            result["status"] = "blocked_schedule"
            result["message"] = "草案未通過。請依驗證結果調整限制或班別。"

    def _check_term(self, text: str) -> None:
        requested = re.findall(r"(?<!\d)(\d{3})[-年學年度]+(?:第)?([12])(?:學期)?(?!\d)", text)
        if any(f"{year}-{term}" != self.snapshot.term for year, term in requested) or (
            "下學期" in text and not requested
        ):
            raise InputError("TERM_UNAVAILABLE", f"目前收錄的校方開課快照是 {self.snapshot.term}。",
                             f"請指定 {self.snapshot.term} 試排。其他學期須先建檔。")

    def _route(self, run: Run, session: Session, text: str, changed: bool, sections: list[str]) -> str:
        signals = {task: [word for word in words if word in text] for task, words in INTENT_TERMS.items()}
        if text.startswith("驗證") or (sections and signals["validate"]):
            local = "validate"
        elif changed and not signals["plan"] and not signals["validate"] and session.last_task:
            local = session.last_task
        elif signals["plan"] and not (signals["courses"] and not changed and not signals["audit"]):
            local = "plan"
        elif signals["courses"]:
            local = "courses"
        elif changed:
            local = "plan"
        elif signals["audit"]:
            local = "audit"
        elif signals["rules"] or any(mentions_rule_id(key, text) for key in self.rules.references):
            local = "rules"
        else:
            raise InputError("NEED_INTENT", "請指出要查規章、檢核學分、查開課或規劃課表。",
                             "例如「我還缺哪些畢業要求？」或「查詢開課」。")
        # Explicit scheduling and validation intent stays in the controller.
        options = [local]
        if local in {"audit", "rules"} and signals["audit"] and signals["rules"]:
            options = ["audit", "rules"]
        if self.model.enabled:
            route, calls, error = self.model.choose(options, signals, local)
            run.result["llm_calls"] = calls
            run.step("observation", "OpenAI 意圖判斷" if not error else "模型不可用，使用本地意圖判斷",
                     task=route, calls=calls, model=self.model.model, model_error=error)
            return route
        return local


def write_summary(database: Path, summary: dict) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database, timeout=2)) as connection:
        with connection:
            connection.execute("CREATE TABLE IF NOT EXISTS agent_runs (run_id TEXT PRIMARY KEY, summary TEXT NOT NULL)")
            connection.execute("INSERT INTO agent_runs VALUES (?, ?)", (summary["run_id"], dump_json(summary)))
