"""The tool implementations used by both the CLI and Streamlit."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
from unicodedata import normalize

from .data import Course, Offering, Profile, RuleSet, Slot, TranscriptRow, ZERO


def lookup_rules(rules: RuleSet, question: str, defaults: tuple[str, ...]) -> list[dict]:
    query = normalized(question)
    ranked = []
    for rule_id, rule in rules.references.items():
        score = sum(len(key) for key in rule.keywords if normalized(key) in query)
        if mentions_rule_id(rule_id, query):
            score += 100
        if score:
            ranked.append((score, rule_id))
    matches = sorted(ranked, key=lambda item: (-item[0], item[1]))
    selected = list(dict.fromkeys([rule_id for _, rule_id in matches[:5]] + list(defaults)))
    return [rules.source(rule_id) | {"summary": rules.references[rule_id].summary}
            for rule_id in selected]


def normalized(text: str) -> str:
    return re.sub(r"\s+", "", normalize("NFKC", text)).lower()


def mentions_rule_id(rule_id: str, normalized_question: str) -> bool:
    return bool(re.search(r"(?<![a-z0-9-])" + re.escape(rule_id.lower()) + r"(?![a-z0-9-])",
                          normalized_question))


@dataclass
class Audit:
    report: dict
    completed: dict[str, Decimal | None]


def audit_credits(rules: RuleSet, profile: Profile, records: tuple[TranscriptRow, ...]) -> Audit:
    totals = dict.fromkeys(rules.categories, ZERO)
    dimensions = {key: set() for key in rules.categories}
    completed: dict[str, Decimal | None] = {}
    recognized_terms = set()
    core_areas = set()
    core_courses = 0
    detail, pending = [], []
    whole_year = {row.course_id for row in records
                  if row.status == "passed" and row.term.startswith(str(profile.admission_year))
                  and (row.course_id, row.term[-1]) in {("EN-YEAR-1", "1"), ("EN-YEAR-2", "2")}}
    # An on-campus pass takes priority over a transfer for the same permanent code.
    ordered = sorted(records, key=lambda row: (row.status != "passed", row.term, row.course_id))
    for row in ordered:
        course = rules.catalog[row.course_id]
        category = rules.categories[course.category]
        amount = ZERO
        term_key = (course.category, row.term)
        if row.status not in {"passed", "transfer_approved", "transfer_pending"}:
            reason = {"failed": "未通過", "withdrawn": "退選或棄修",
                      "in_progress": "修讀中", "planned": "規劃中"}[row.status]
        elif row.status == "transfer_pending" or (row.status == "transfer_approved" and not row.approval_ref):
            reason = "抵免尚無核准代碼"
            pending.append({"course_id": row.course_id, "reason": reason, "source": rules.source("TRANSFER")})
        elif course.approval_required and not row.approval_ref:
            reason = "此科需導師及系主任採計認定"
            pending.append({"course_id": row.course_id, "reason": reason, "source": rules.source(category.rule_id)})
        elif row.course_id in completed:
            reason = "相同永久課號或已核對等價課程，僅採一次"
            if row.status == "passed" and row.score is not None:
                previous = completed[row.course_id]
                completed[row.course_id] = row.score if previous is None else max(previous, row.score)
        elif category.one_per_term and row.term[:3] != str(profile.admission_year) and not row.approval_ref:
            reason = "非入學學年度的大學英文，須確認重補修認定"
            pending.append({"course_id": row.course_id, "reason": reason, "source": rules.source(category.rule_id)})
        elif row.course_id in {"EN-YEAR-1", "EN-YEAR-2"} and len(whole_year) < 2:
            reason = "全學年制英文須有上下學期的通過紀錄"
            pending.append({"course_id": row.course_id, "reason": reason, "source": rules.source(category.rule_id)})
        elif category.one_per_term and term_key in recognized_terms:
            reason = "同學期大學英文僅採一門"
        else:
            completed[row.course_id] = row.score if row.status == "passed" else None
            if category.distinct_minimum and course.dimension:
                dimensions[course.category].add(course.dimension)
            if course.core_area:
                core_areas.add(course.core_area)
                core_courses += 1
            if course.category == "review":
                reason = "先向系辦核對本版能否採計"
                pending.append({"course_id": row.course_id, "reason": reason, "source": rules.source(course.source)})
            elif not category.counts:
                reason = "此科不計入畢業總學分"
            else:
                remaining = row.credits if category.cap is None else max(ZERO, category.cap - totals[course.category])
                if course.category in rules.combined_cap.categories:
                    used = sum(totals[key] for key in rules.combined_cap.categories)
                    remaining = min(remaining, max(ZERO, rules.combined_cap.cap - used))
                amount = min(row.credits, remaining)
                totals[course.category] += amount
                recognized_terms.add(term_key)
                reason = ("超過分類採計上限" if amount < row.credits else
                          "核准等學分抵免" if row.status == "transfer_approved" else "已通過")
        source_id = "TRANSFER" if row.status.startswith("transfer_") else category.rule_id
        detail.append({"course_id": row.course_id, "title": course.title, "term": row.term,
                       "status": row.status, "category": category.label, "credits": row.credits,
                       "counted": amount, "reason": reason, "source": rules.source(source_id)})

    categories = []
    for key, category in rules.categories.items():
        if not category.counts:
            continue
        row = {"key": key, "label": category.label, "minimum": category.minimum, "cap": category.cap,
               "counted": totals[key], "missing": max(ZERO, category.minimum - totals[key]),
               "source": rules.source(category.rule_id)}
        if category.distinct_minimum:
            row.update(dimensions=sorted(dimensions[key]), dimension_minimum=category.distinct_minimum,
                       dimensions_missing=max(0, category.distinct_minimum - len(dimensions[key])))
        categories.append(row)

    combined_cap = {"counted": sum((totals[key] for key in rules.combined_cap.categories), ZERO),
                    "limit": rules.combined_cap.cap, "source": rules.source(rules.combined_cap.rule_id)}

    total = sum(totals.values(), ZERO)
    missing_required = [{"course_id": key, "title": c.title, "credits": c.credits,
                         "source": rules.source(c.source)}
                        for key, c in rules.catalog.items()
                        if c.category == rules.required_category and key not in completed]
    evidence = [{"key": key, "title": rules.references[key].title,
                 "state": profile.evidence.get(key, "unknown"), "source": rules.source(key)}
                for key in rules.evidence]
    core = {"courses": core_courses, "minimum_courses": rules.core.courses,
            "areas": sorted(core_areas), "minimum_areas": rules.core.areas, "source": rules.source(rules.core.rule_id)}
    shortage = (total < rules.total or bool(missing_required) or
                any(c["missing"] or c.get("dimensions_missing") for c in categories) or
                core_courses < rules.core.courses or len(core_areas) < rules.core.areas or
                any(e["state"] == "missing" for e in evidence))
    needs_review = bool(pending) or any(e["state"] == "unknown" for e in evidence)
    status = "尚缺" if shortage else "待確認" if needs_review else "已符合所載條件"
    return Audit({"status": status, "total": total, "required_total": rules.total,
                  "missing_total": max(ZERO, rules.total - total), "source": rules.source(rules.total_rule_id),
                  "categories": categories, "missing_required": missing_required, "evidence": evidence,
                  "pending": pending, "detail": detail, "core": core, "combined_cap": combined_cap}, completed)


@dataclass(frozen=True)
class Preferences:
    minimum: Decimal = Decimal(12)
    maximum: Decimal = Decimal(18)
    blocked: tuple[Slot, ...] = ()
    prioritize_required: bool = True

    def summary(self) -> dict:
        return {"minimum": self.minimum, "maximum": self.maximum,
                "blocked": [slot.label() for slot in self.blocked],
                "prioritize_required": self.prioritize_required}


def finding(code: str, message: str, remedy: str, sections: tuple[str, ...] = (), **detail) -> dict:
    return {"code": code, "message": message, "remedy": remedy, "sections": list(sections), **detail}


def check_offering(offering: Offering, catalog: dict[str, Course], profile: Profile,
                   audit: Audit, preferences: Preferences) -> list[dict]:
    issues = []
    section = (offering.section_id,)
    course = catalog[offering.course_id]
    if offering.course_id in audit.completed:
        issues.append(finding("ALREADY_COMPLETED", f"{course.title}已取得學分或完成認定。",
                              "改選其他尚未完成的科目。", section))
    if offering.permitted_years and profile.year_level not in offering.permitted_years:
        issues.append(finding("YEAR_RESTRICTION", f"{course.title}的班別有年級限制。",
                              "核對官方選課對象，或選其他班別。", section))
    if offering.review:
        issues.append(finding("REVIEW_OFFERING", offering.review, "先核對官方課程頁及授課單位。", section))
    for prerequisite in offering.prerequisites:
        name = catalog[prerequisite.course_id].title
        if prerequisite.course_id not in audit.completed:
            issues.append(finding("PREREQUISITE", f"{course.title}缺少先修「{name}」。",
                                  "先通過先修科目，或選其他課。", section,
                                  prerequisite=prerequisite.course_id))
        elif prerequisite.score is not None:
            score = audit.completed[prerequisite.course_id]
            if score is None or score < prerequisite.score:
                known = "分數待確認" if score is None else "分數未達門檻"
                issues.append(finding("PREREQUISITE_SCORE",
                                      f"{course.title}要求{name}達 {prerequisite.score} 分，目前{known}。",
                                      "補入已取得的有效分數，或核對校方擋修認定。", section,
                                      prerequisite=prerequisite.course_id))
    if not offering.meetings or any(slot.weeks is None for slot in offering.meetings):
        issues.append(finding("TIME_UNKNOWN", f"{course.title}的上課時間或週次待確認。",
                              "查詢授課大綱並補齊時段與週次後重驗。", section))
    for slot in offering.meetings:
        if any(slot.overlaps(blocked) and slot.weeks and blocked.weeks and slot.weeks & blocked.weeks
               for blocked in preferences.blocked):
            issues.append(finding("BLOCKED_TIME", f"{course.title}落在避開時段（{slot.label()}）。",
                                  "改選其他班別，或確認是否調整避開時段。", section))
    return issues


def find_offerings(offerings: dict[str, Offering], rules: RuleSet, profile: Profile,
                   audit: Audit, preferences: Preferences) -> tuple[list[Offering], list[dict]]:
    available, excluded = [], []
    for item in offerings.values():
        issues = check_offering(item, rules.catalog, profile, audit, preferences)
        if issues:
            excluded.extend(issues)
        else:
            available.append(item)
    return available, excluded


def conflicts(left: Offering, right: Offering, catalog: dict[str, Course]) -> list[dict]:
    issues = []
    for a in left.meetings:
        for b in right.meetings:
            if not a.overlaps(b):
                continue
            if a.weeks is None or b.weeks is None:
                code, weeks = "WEEKS_UNKNOWN", None
            elif a.weeks & b.weeks:
                code, weeks = "TIME_CONFLICT", sorted(a.weeks & b.weeks)
            else:
                continue
            names = f"{catalog[left.course_id].title}與{catalog[right.course_id].title}"
            reason = "衝堂" if weeks is not None else "時間重疊且週次待確認"
            issues.append(finding(code, f"{names}{reason}（{a.label()}／{b.label()}）。",
                                  "換班或確認實際週次。", (left.section_id, right.section_id), weeks=weeks))
    return issues


def draft_schedule(available: list[Offering], rules: RuleSet, audit: Audit,
                   preferences: Preferences) -> tuple[list[str], list[dict]]:
    """Greedy baseline. A3 can try another ordering after validation feedback."""
    category_gaps = {c["key"]: c["missing"] for c in audit.report["categories"]}
    missing = {c["course_id"] for c in audit.report["missing_required"]}
    catalog = rules.catalog
    selected, skipped, seen = [], [], set()
    total = ZERO
    ranked = sorted(available, key=lambda item: (
        not (preferences.prioritize_required and item.course_id in missing),
        not bool(category_gaps.get(catalog[item.course_id].category)),
        item.section_id,
    ))
    for item in ranked:
        course = catalog[item.course_id]
        if item.course_id in seen or not course.credits or not rules.categories[course.category].counts:
            continue
        if total + course.credits > preferences.maximum:
            continue
        issues = [issue for other in selected for issue in conflicts(other, item, catalog)]
        if issues:
            skipped.extend(issues)
            continue
        selected.append(item)
        seen.add(item.course_id)
        total += course.credits
    return [item.section_id for item in selected], skipped


def validate_schedule(ids: list[str], offerings: dict[str, Offering], rules: RuleSet,
                      profile: Profile, audit: Audit, preferences: Preferences) -> dict:
    issues, selected, seen = [], [], set()
    for section in ids:
        item = offerings.get(section)
        if item is None:
            issues.append(finding("UNKNOWN_SECTION", "流水號未收錄於本學期快照。",
                                  "以課程查詢或資料清單中的開課流水號重新輸入。", (section,)))
            continue
        if item.course_id in seen:
            issues.append(finding("DUPLICATE_COURSE", "同一永久課號選了超過一班。",
                                  "同一科目保留一個班別。", (section,)))
        else:
            seen.add(item.course_id)
            selected.append(item)
        issues.extend(check_offering(item, rules.catalog, profile, audit, preferences))
    for left, right in combinations(selected, 2):
        issues.extend(conflicts(left, right, rules.catalog))

    credits = sum((rules.catalog[item.course_id].credits for item in selected), ZERO)
    official_minimum = Decimal(9 if profile.year_level == 4 else 16)
    effective_minimum = max(official_minimum, preferences.minimum)
    reference = rules.source("ENROLL-MIN")
    if effective_minimum > preferences.maximum:
        issues.append(finding("LIMIT_INCOMPATIBLE",
                              f"設定上限 {preferences.maximum} 低於適用的下限 {effective_minimum}。",
                              f"將上限調整為至少 {effective_minimum} 學分。", source=reference))
    elif credits < effective_minimum:
        issues.append(finding("CREDIT_MIN", f"草案 {credits} 學分，低於適用下限 {effective_minimum}。",
                              "加入能通過驗證的課，或調整偏好後重算。", source=reference))
    if credits > preferences.maximum:
        issues.append(finding("CREDIT_MAX", f"草案 {credits} 學分，超過設定上限 {preferences.maximum}。",
                              "移除部分課程或確認新的學分上限。"))
    classes = []
    for item in selected:
        course = rules.catalog[item.course_id]
        classes.append({"section_id": item.section_id, "course_id": item.course_id, "title": course.title,
                        "credits": course.credits, "class_name": item.class_name,
                        "meetings": [{"label": slot.label(), "weeks": sorted(slot.weeks)
                                      if slot.weeks is not None else None} for slot in item.meetings],
                        "source": rules.source(course.source)})
    return {"feasible": not issues, "credits": credits, "official_minimum": official_minimum,
            "effective_minimum": effective_minimum, "source": reference, "classes": classes, "issues": issues}
