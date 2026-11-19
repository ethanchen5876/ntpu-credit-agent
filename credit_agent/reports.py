"""Generate the two local export formats from the same verified result."""

from pathlib import Path

from .data import dump_json

EVIDENCE_LABELS = {"confirmed": "已依輸入確認", "missing": "尚缺", "unknown": "待確認"}


def markdown_report(result: dict) -> str:
    lines = [
        "# 選課與畢業學分檢核",
        "",
        f"課規版本 `{result['rule_version']}`｜開課學期 `{result['term']}`｜執行編號 `{result['run_id']}`",
        "",
        result["message"],
    ]
    if "error" in result:
        error = result["error"]
        lines += ["", f"處理方式（`{error['code']}`）　{error['remedy']}"]
    audit = result.get("audit")
    if audit:
        lines += ["", "## 目前採計", "",
                  f"**{audit['status']}**｜已採計 {audit['total']}／{audit['required_total']} 學分，"
                  f"總學分缺 {audit['missing_total']}。", "",
                  "| 分類 | 目前 | 最低 | 尚缺 | 採計上限 | 規則 |",
                  "| --- | ---: | ---: | ---: | ---: | --- |"]
        for category in audit["categories"]:
            cap = category["cap"] if category["cap"] is not None else "—"
            rule = category["source"]["rule_id"]
            lines.append(f"| {category['label']} | {category['counted']} | {category['minimum']} | "
                         f"{category['missing']} | {cap} | {rule} |")
            if "dimensions" in category:
                dims = "、".join(map(str, category["dimensions"])) or "無"
                lines.append(f"| 向度通識已完成向度 {dims} | {len(category['dimensions'])} | "
                             f"{category['dimension_minimum']} | {category['dimensions_missing']} | — | {rule} |")
        if audit["missing_required"]:
            lines += ["", "### 缺修必修", ""]
            lines += [f"- {c['title']}（{c['course_id']}，{c['credits']} 學分，{c['source']['rule_id']}）"
                      for c in audit["missing_required"]]
        core = audit["core"]
        lines += ["", f"核心選修完成 {core['courses']}／{core['minimum_courses']} 門，涵蓋 "
                  f"{len(core['areas'])}／{core['minimum_areas']} 個領域（{core['source']['rule_id']}）。"]
        cap = audit["combined_cap"]
        lines += ["", f"外系與共同選修合計已採計 {cap['counted']}／{cap['limit']} 學分（"
                  f"{cap['source']['rule_id']}）。"]
        lines += ["", "### 其他畢業條件", ""]
        lines += [f"- {e['title']}（{e['key']}）　{EVIDENCE_LABELS[e['state']]}。" for e in audit["evidence"]]
        lines += [f"- {p['course_id']}　{p['reason']}（{p['source']['rule_id']}）。" for p in audit["pending"]]
        if audit["detail"]:
            lines += ["", "### 逐筆採計", "",
                      "| 永久課號 | 科目 | 學期 | 狀態 | 採計／原學分 | 理由 |",
                      "| --- | --- | --- | --- | --- | --- |"]
            lines += [f"| {d['course_id']} | {d['title']} | {d['term']} | {d['status']} | "
                      f"{d['counted']}／{d['credits']} | {d['reason']}（{d['source']['rule_id']}） |"
                      for d in audit["detail"]]
    if "courses" in result:
        lines += ["", "## 開課清單", "", "| 流水號 | 課程 | 班別 | 學分 | 時段 |", "| --- | --- | --- | ---: | --- |"]
        lines += [f"| {c['section_id']} | {c['title']} | {c['class_name']} | {c['credits']} | "
                  f"{'、'.join(c['meetings']) or '待確認'} |" for c in result["courses"]]
    if "schedule" in result:
        schedule = result["schedule"]
        verdict = "通過已載明條件" if schedule["feasible"] else "草案未通過"
        lines += ["", "## 課表驗證", "",
                  f"**{verdict}**｜{schedule['credits']} 學分｜適用下限 {schedule['effective_minimum']} 學分"
                  f"｜設定上限 {result['preferences']['maximum']} 學分", ""]
        lines += [f"- {c['title']}（{c['section_id']}，{c['credits']} 學分）　"
                  f"{'、'.join(m['label'] for m in c['meetings']) or '時段待確認'}。" for c in schedule["classes"]]
        if schedule["issues"]:
            lines += [""] + [f"- `{i['code']}` {i['message']} {i['remedy']}" for i in schedule["issues"]]
        if result.get("excluded") and not schedule["feasible"]:
            lines += ["", "### 部分跳過的班別與原因", ""]
            lines += [f"- {'、'.join(i['sections'])}　{i['message']}" for i in result["excluded"][:15]]
    if "projection" in result:
        estimate = result["projection"]
        lines += ["", "## 候選課程全部通過後的預估", "",
                  f"預估可採計 {estimate['total']} 學分，距總門檻尚缺 {estimate['missing_total']} 學分。",
                  "", "| 分類 | 預估採計 | 預估尚缺 |", "| --- | ---: | ---: |"]
        lines += [f"| {c['label']} | {c['counted']} | {c['missing']} |" for c in estimate["categories"]]
    if result.get("rules"):
        lines += ["", "## 規章查詢", ""]
        lines += [f"- `{r['rule_id']}`　{r['summary']} [{r['locator']}]({r['url']})" for r in result["rules"]]
    if audit or "schedule" in result:
        sources = {}
        for row in ([audit, audit["core"]] + audit["categories"] + audit["evidence"] + audit["missing_required"] +
                    audit["pending"] + audit["detail"]) if audit else []:
            source = row["source"]
            sources[source["rule_id"]] = source
        for row in result.get("schedule", {}).get("classes", []):
            sources[row["source"]["rule_id"]] = row["source"]
        if sources:
            lines += ["", "## 核對來源", ""]
            lines += [f"- `{r['rule_id']}`　{r['title']} [{r['locator']}]({r['url']})" for r in sources.values()]
    lines += ["", "## 執行追蹤", ""]
    lines += [f"{step['step']}. `{step['event']}`　{step['message']}" for step in result["trace"]]
    lines += ["", "結果屬本版資料的初步檢核。正式認定及選課資格以校方審查為準。", ""]
    return "\n".join(lines)


def export_payloads(result: dict) -> tuple[str, str]:
    return markdown_report(result), dump_json(result) + "\n"


def export_report(result: dict, target: Path) -> tuple[Path, Path]:
    target.mkdir(parents=True, exist_ok=True)
    files = tuple(target / f"{result['run_id']}.{extension}" for extension in ("md", "json"))
    for file, content in zip(files, export_payloads(result), strict=True):
        file.write_text(content, encoding="utf-8")
    return files
