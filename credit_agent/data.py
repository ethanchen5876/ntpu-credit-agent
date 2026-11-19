"""Versioned course data and bounded CSV imports."""

from __future__ import annotations

import csv
import io
import json
import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

DATA_DIR = Path(os.environ.get("AGENT_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
Credit = Annotated[Decimal, Field(ge=0, le=40, decimal_places=2)]
CreditTotal = Annotated[Decimal, Field(ge=0, le=300, decimal_places=2)]
Code = Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{0,39}$")]
Term = Annotated[str, Field(pattern=r"^\d{3}-[12]$")]
Status = Literal["passed", "failed", "withdrawn", "in_progress", "planned",
                 "transfer_approved", "transfer_pending"]
EvidenceStatus = Literal["unknown", "confirmed", "missing"]
ZERO = Decimal(0)
WEEKS = frozenset(range(1, 19))
DAY_NAMES = "一二三四五六日"


class InputError(Exception):
    def __init__(self, code: str, message: str, remedy: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.remedy = remedy

    def details(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "remedy": self.remedy}


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Reference(Record):
    title: str
    url: str
    locator: str
    summary: str
    keywords: tuple[str, ...]


class Category(Record):
    label: str
    rule_id: str
    minimum: CreditTotal = ZERO
    cap: CreditTotal | None = None
    counts: bool = True
    distinct_minimum: int = 0
    one_per_term: bool = False


class Course(Record):
    title: str
    credits: Credit
    category: str
    source: str
    dimension: Annotated[int, Field(ge=1, le=6)] | None = None
    aliases: tuple[Code, ...] = ()
    core_area: str | None = None
    approval_required: bool = False


class CombinedCap(Record):
    categories: tuple[str, ...]
    cap: CreditTotal
    rule_id: str


class CoreRequirement(Record):
    courses: int
    areas: int
    rule_id: str


class RuleSet(Record):
    university: str
    department: str
    admission_year: int
    version: str
    total: CreditTotal
    total_rule_id: str
    required_category: str
    categories: dict[str, Category]
    evidence: tuple[str, ...]
    references: dict[str, Reference]
    catalog: dict[Code, Course]
    combined_cap: CombinedCap
    core: CoreRequirement

    @model_validator(mode="after")
    def check_references(self) -> RuleSet:
        rule_ids = {self.total_rule_id, self.combined_cap.rule_id, self.core.rule_id, *self.evidence,
                    "TRANSFER", "ENROLL-MIN", "GEN-DIM", "PREREQ-DATA", "PREREQ-AI"}
        rule_ids.update(category.rule_id for category in self.categories.values())
        rule_ids.update(course.source for course in self.catalog.values())
        if not rule_ids <= self.references.keys():
            raise ValueError("找不到規則來源")
        if self.required_category not in self.categories:
            raise ValueError("找不到必修分類")
        if not set(self.combined_cap.categories) <= self.categories.keys():
            raise ValueError("找不到合計上限所指的分類")
        seen = set(self.catalog)
        for course in self.catalog.values():
            if course.category not in self.categories:
                raise ValueError("找不到課程分類")
            for alias in course.aliases:
                if alias in seen:
                    raise ValueError("永久課號或改名對照重複")
                seen.add(alias)
        required_credits = sum(course.credits for course in self.catalog.values()
                               if course.category == self.required_category)
        if required_credits != self.categories[self.required_category].minimum:
            raise ValueError("必修目錄的學分數與規章總數不一致")
        return self

    def source(self, rule_id: str) -> dict[str, str]:
        ref = self.references[rule_id]
        return {"rule_id": rule_id, "version": self.version,
                "title": ref.title, "url": ref.url, "locator": ref.locator}


class Profile(Record):
    department: str
    admission_year: int | None
    version: str
    year_level: Annotated[int, Field(ge=1, le=4)] = 2
    evidence: dict[str, EvidenceStatus] = Field(default_factory=dict)


class TranscriptRow(Record):
    course_id: Code
    term: Term
    credits: Credit
    status: Status
    score: Annotated[Decimal, Field(ge=0, le=100, decimal_places=2)] | None = None
    approval_ref: Annotated[str, Field(pattern=r"^APR-[A-Z0-9-]{1,24}$")] | None = None


class Slot(Record):
    day: Annotated[int, Field(ge=1, le=7)]
    start: Annotated[int, Field(ge=0, lt=1440)]
    end: Annotated[int, Field(gt=0, le=1440)]
    weeks: frozenset[int] | None = WEEKS

    @model_validator(mode="after")
    def check_range(self) -> Slot:
        if self.end <= self.start or (self.weeks is not None and
                                      (not self.weeks or not self.weeks <= WEEKS)):
            raise ValueError("時間或週次區間有誤")
        return self

    def overlaps(self, other: Slot) -> bool:
        return self.day == other.day and self.start < other.end and other.start < self.end

    def label(self) -> str:
        return f"週{DAY_NAMES[self.day - 1]} {clock(self.start)}–{clock(self.end)}"


class Prerequisite(Record):
    course_id: Code
    score: Annotated[int, Field(ge=0, le=100)] | None = None


class Offering(Record):
    section_id: Code
    course_id: Code
    term: Term
    class_name: Annotated[str, Field(min_length=1, max_length=60)]
    meetings: tuple[Slot, ...]
    prerequisites: tuple[Prerequisite, ...]
    permitted_years: frozenset[Annotated[int, Field(ge=1, le=4)]]
    review: Annotated[str, Field(max_length=250)] = ""


class Snapshot(Record):
    term: Term
    retrieved_on: str
    scope: str
    url: str
    section_count: int


def load_rules(data_dir: Path = DATA_DIR) -> RuleSet:
    return RuleSet.model_validate_json((data_dir / "rules.json").read_bytes())


def load_snapshot(data_dir: Path = DATA_DIR) -> Snapshot:
    return Snapshot.model_validate_json((data_dir / "snapshot.json").read_bytes())


def match_rules(profile: Profile, rules: RuleSet) -> RuleSet:
    if not profile.department.strip() or profile.admission_year is None or not profile.version.strip():
        raise InputError("PROFILE_REQUIRED", "請提供系所、入學年度與課規版本。",
                         "在學籍設定確認三個欄位，再執行。")
    if (profile.department, profile.admission_year, profile.version) != (
        rules.department, rules.admission_year, rules.version
    ):
        raise InputError("RULESET_MISMATCH", "目前沒有符合這組學籍的課規。",
                         f"核對資料。目前內建 {rules.department} {rules.admission_year} 入學版 "
                         f"（{rules.version}）。")
    if not profile.evidence.keys() <= set(rules.evidence):
        raise InputError("EVIDENCE_KEY", "認定欄位和本版課規不符。", "從內建門檻清單輸入認定狀態。")
    return rules


def clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def minutes(value: str) -> int:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d|24:00", value):
        raise ValueError("時間應為 HH:MM")
    hours, mins = map(int, value.split(":"))
    return 60 * hours + mins


def parse_weeks(value: str) -> frozenset[int] | None:
    if value == "?":
        return None
    if value == "all":
        return WEEKS
    if value in {"odd", "even"}:
        return frozenset(w for w in WEEKS if w % 2 == (value == "odd"))
    weeks = set()
    for part in value.split("+"):
        bounds = part.split("-")
        if not all(re.fullmatch(r"\d{1,2}", bound) for bound in bounds) or len(bounds) > 2:
            raise ValueError("週次格式有誤")
        start, end = int(bounds[0]), int(bounds[-1])
        if not 1 <= start <= end <= 18:
            raise ValueError("週次範圍有誤")
        weeks.update(range(start, end + 1))
    return frozenset(weeks)


def parse_meetings(value: str) -> tuple[Slot, ...]:
    slots = []
    for part in value.split(";") if value else ():
        day, hours, weeks = part.split("@")
        start, end = hours.split("-")
        slots.append(Slot(day=int(day), start=minutes(start), end=minutes(end),
                          weeks=parse_weeks(weeks)))
    return tuple(slots)


def parse_prerequisites(value: str) -> tuple[Prerequisite, ...]:
    conditions = []
    for part in value.split(";") if value else ():
        course_id, comparator, score = part.partition(">=")
        if comparator and not score:
            raise ValueError("先修分數缺漏")
        conditions.append(Prerequisite(course_id=course_id.upper(), score=int(score) if score else None))
    return tuple(conditions)


def read_csv(raw: bytes, required: set[str], optional: set[str], limit: int):
    if len(raw) > 256_000:
        raise InputError("CSV_SIZE", "CSV 超過 256 KB。", "請以提供的範本縮小檔案。")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
        names = reader.fieldnames or []
        fields = set(names)
        if not required <= fields or fields - (required | optional) or len(fields) != len(names):
            raise InputError("CSV_FIELDS", f"CSV 欄位有誤。必填欄位為 {'、'.join(sorted(required))}。",
                             "使用 UTF-8 範本，移除姓名、學號等額外欄位。")
        for index, row in enumerate(reader, 1):
            if index > limit:
                raise InputError("CSV_ROWS", f"CSV 超過 {limit} 筆資料。", "請減少匯入筆數。")
            if None in row or any(value is None for value in row.values()):
                raise InputError("CSV_ROW", f"第 {index + 1} 列欄數有誤。", "請對照標題修正此列。")
            yield index + 1, {key: value.strip() for key, value in row.items()}
    except (UnicodeError, csv.Error) as error:
        raise InputError("CSV_FORMAT", "CSV 編碼或引號格式有誤。", "另存為 UTF-8 CSV 再匯入。") from error


def require_course(course_id: str, catalog: dict[str, Course], line: int) -> Course:
    if course_id not in catalog:
        raise InputError("UNKNOWN_COURSE", f"第 {line} 列的永久課號未收錄。",
                         "請對照課程代碼表。未收錄的外系或歷年科目須先核對分類及來源。")
    return catalog[course_id]


def import_transcript(raw: bytes, rules: RuleSet) -> tuple[TranscriptRow, ...]:
    required = {"course_id", "term", "credits", "status"}
    aliases = {alias: key for key, course in rules.catalog.items() for alias in course.aliases}
    records = []
    for line, row in read_csv(raw, required, {"score", "approval_ref"}, 300):
        code = row["course_id"].upper()
        row["course_id"] = aliases.get(code, code)
        row["score"] = row.get("score") or None
        row["approval_ref"] = row.get("approval_ref") or None
        try:
            record = TranscriptRow.model_validate(row)
        except ValidationError as error:
            fields = "、".join(sorted({str(item["loc"][0]) for item in error.errors()}))
            raise InputError("TRANSCRIPT_VALUE", f"第 {line} 列的 {fields} 不符合格式。",
                             "請核對課號、學期、非負學分、狀態與分數。") from error
        course = require_course(record.course_id, rules.catalog, line)
        if record.credits != course.credits:
            raise InputError("CREDIT_MISMATCH", f"第 {line} 列的學分與本版課程目錄不符。",
                             "請核對永久課號及學分。非等學分抵免請交由系辦覆核。")
        records.append(record)
    return tuple(records)


OFFERING_COLUMNS = {"section_id", "course_id", "term", "class_name",
                    "meetings", "prerequisites", "permitted_years", "review"}


def import_offerings(raw: bytes, rules: RuleSet, term: str) -> dict[str, Offering]:
    offerings = {}
    for line, row in read_csv(raw, OFFERING_COLUMNS, set(), 200):
        try:
            offering = Offering(
                section_id=row["section_id"].upper(), course_id=row["course_id"].upper(),
                term=row["term"], class_name=row["class_name"], meetings=parse_meetings(row["meetings"]),
                prerequisites=parse_prerequisites(row["prerequisites"]),
                permitted_years=frozenset(map(int, row["permitted_years"].split(";")))
                if row["permitted_years"] else frozenset(), review=row["review"],
            )
        except ValueError as error:
            raise InputError("OFFERING_VALUE", f"開課 CSV 第 {line} 列的格式有誤。",
                             "請對照範本檢查時段、週次、年級及先修欄位。") from error
        if offering.term != term:
            raise InputError("TERM_MISMATCH", f"開課 CSV 第 {line} 列混入不同學期。",
                             f"請只匯入 {term} 學期的開課資料。")
        if offering.section_id in offerings:
            raise InputError("DUPLICATE_SECTION", f"第 {line} 列的開課流水號重複。",
                             "同一班的所有時段應放在同一列。")
        for code in (offering.course_id, *(p.course_id for p in offering.prerequisites)):
            require_course(code, rules.catalog, line)
        offerings[offering.section_id] = offering
    return offerings


def dump_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)
