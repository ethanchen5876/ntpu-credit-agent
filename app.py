"""Streamlit interface for the course and graduation credit Agent."""

import hashlib
import os
import time
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from credit_agent.agent import Agent, Session
from credit_agent.data import DATA_DIR, MAX_TRANSCRIPT_ROWS, Profile, TranscriptRow, dump_json, transcript_csv
from credit_agent.reports import EVIDENCE_LABELS, export_payloads
from credit_agent.__main__ import DEMO_QUESTION

st.set_page_config(page_title="選課與畢業學分檢核", page_icon="🎓", layout="wide")

TRANSCRIPT_STATUS_LABELS = {
    "passed": "已通過",
    "failed": "未通過",
    "withdrawn": "已退選或棄修",
    "in_progress": "修讀中",
    "planned": "規劃中",
    "transfer_approved": "已核准抵免",
    "transfer_pending": "抵免待確認",
}


@st.cache_resource
def get_agent() -> Agent:
    database = os.getenv("AGENT_STATE_DB")
    return Agent(database=Path(database) if database else None)


def reset_session() -> None:
    generation = st.session_state.get("generation", 0)
    for key in list(st.session_state):
        del st.session_state[key]
    st.session_state["generation"] = generation + 1


def manual_transcript(agent: Agent, generation: int) -> bytes | None:
    records: dict[tuple[str, str], TranscriptRow] = st.session_state.setdefault(f"registered{generation}", {})
    catalog = agent.rules.catalog
    course_codes = sorted(catalog, key=lambda code: catalog[code].title)
    latest_year, latest_term = map(int, agent.snapshot.term.split("-"))
    terms = [f"{year}-{term}" for year in range(agent.rules.admission_year, latest_year + 1)
             for term in (1, 2) if (year, term) <= (latest_year, latest_term)]
    with st.container(border=True):
        st.subheader("用課名登錄修課紀錄")
        with st.form(f"manual_entry{generation}", clear_on_submit=True):
            term_column, status_column = st.columns(2)
            term = term_column.selectbox("學期", terms, key=f"manual_term{generation}")
            status = status_column.selectbox("修課結果", tuple(TRANSCRIPT_STATUS_LABELS),
                                             format_func=TRANSCRIPT_STATUS_LABELS.get,
                                             key=f"manual_status{generation}")
            course_ids = st.multiselect(
                "課程", course_codes,
                format_func=lambda code: f"{catalog[code].title}（{catalog[code].credits} 學分｜{code}）",
                placeholder="輸入課名搜尋，可一次選多門", key=f"manual_courses{generation}",
            )
            st.caption("同一學期、同一修課結果的課程可以一起加入。")
            with st.expander("補充分數或認定代碼（選填）"):
                score = st.number_input("分數", 0.0, 100.0, value=None, step=0.01, key=f"manual_score{generation}")
                approval_ref = st.text_input("已核准的去識別認定代碼", placeholder="APR-…",
                                             max_chars=28, key=f"manual_approval{generation}").strip() or None
            submitted = st.form_submit_button("加入修課紀錄")
        if submitted:
            if not course_ids:
                st.error("請先選擇至少一門課。")
            elif len(course_ids) > 1 and (score is not None or approval_ref is not None):
                st.error("有填分數或認定代碼時，請逐門加入。")
            elif len(records.keys() | {(code, term) for code in course_ids}) > MAX_TRANSCRIPT_ROWS:
                st.error(f"最多可登錄 {MAX_TRANSCRIPT_ROWS} 筆修課紀錄。")
            else:
                try:
                    new_records = [TranscriptRow(course_id=code, term=term, status=status,
                                                 credits=catalog[code].credits, score=score,
                                                 approval_ref=approval_ref) for code in course_ids]
                except ValidationError:
                    st.error("請確認分數介於 0 至 100，認定代碼符合 APR-… 格式。")
                else:
                    records.update(((record.course_id, record.term), record) for record in new_records)
                    st.success(f"已儲存 {len(new_records)} 筆修課紀錄。")

        if not records:
            return transcript_csv() if st.checkbox("我目前沒有修課紀錄", key=f"manual_empty{generation}") else None
        with st.expander(f"已登錄 {len(records)} 筆修課紀錄"):
            st.dataframe([{"科目": catalog[row.course_id].title, "學期": row.term,
                           "學分": str(row.credits), "修課結果": TRANSCRIPT_STATUS_LABELS[row.status],
                           "分數": str(row.score) if row.score is not None else "—"}
                          for row in records.values()], hide_index=True, use_container_width=True)
            removal = st.multiselect("要移除的紀錄", list(records),
                                     format_func=lambda key: f"{catalog[key[0]].title}｜{key[1]}｜{key[0]}",
                                     key=f"manual_remove{generation}")
            if st.button("移除所選紀錄", disabled=not removal):
                for key in removal:
                    records.pop(key)
                st.rerun()
            payload = transcript_csv(records.values())
            st.download_button("下載這次的修課 CSV", payload, "transcript.csv", "text/csv")
        return payload


def audit_panel(audit: dict, projection: dict | None) -> None:
    cols = st.columns(3 if projection else 2)
    cols[0].metric("目前採計", f"{audit['total']}／{audit['required_total']} 學分")
    cols[1].metric("總學分尚缺", f"{audit['missing_total']} 學分")
    if projection:
        cols[2].metric("候選課全部通過後的預估", f"{projection['total']} 學分")
    st.caption(f"本版檢核結果　{audit['status']}｜{audit['source']['rule_id']}｜正式認定以校方審查為準")
    projected = {c["key"]: c["counted"] for c in projection["categories"]} if projection else {}
    table = []
    for category in audit["categories"]:
        row = {"分類": category["label"], "已採計": str(category["counted"]),
               "最低": str(category["minimum"]), "尚缺": str(category["missing"]),
               "採計上限": str(category["cap"]) if category["cap"] is not None else "—",
               "規則": category["source"]["rule_id"]}
        if projection:
            row["預估採計"] = str(projected[category["key"]])
        table.append(row)
        if "dimensions" in category:
            dims = "、".join(map(str, category["dimensions"])) or "無"
            st.caption(f"向度通識已完成向度 {dims}，共 {len(category['dimensions'])}／"
                       f"{category['dimension_minimum']} 個向度。")
    st.dataframe(table, hide_index=True, use_container_width=True)
    core = audit["core"]
    st.caption(f"核心選修 {core['courses']}／{core['minimum_courses']} 門，涵蓋 "
               f"{len(core['areas'])}／{core['minimum_areas']} 個領域｜{core['source']['rule_id']}")
    cap = audit["combined_cap"]
    st.caption(f"外系與共同選修合計 {cap['counted']}／{cap['limit']} 學分｜{cap['source']['rule_id']}")
    with st.expander(f"缺修必修 · {len(audit['missing_required'])} 門", expanded=True):
        if audit["missing_required"]:
            st.dataframe([{"永久課號": c["course_id"], "科目": c["title"], "學分": str(c["credits"]),
                           "規則": c["source"]["rule_id"]} for c in audit["missing_required"]],
                         hide_index=True, use_container_width=True, height=246)
        else:
            st.success("本版必修科目均已完成。")
    with st.expander("非學分門檻與待確認紀錄", expanded=True):
        for item in audit["evidence"]:
            st.markdown(f"- {item['title']}　**{EVIDENCE_LABELS[item['state']]}** "
                        f"[{item['source']['locator']}]({item['source']['url']})")
        for item in audit["pending"]:
            st.write(f"{item['course_id']}　{item['reason']}")
    with st.expander("逐筆採計明細"):
        st.dataframe([{"永久課號": d["course_id"], "科目": d["title"], "學期": d["term"],
                       "採計": str(d["counted"]), "原學分": str(d["credits"]), "理由": d["reason"],
                       "規則": d["source"]["rule_id"]} for d in audit["detail"]],
                     hide_index=True, use_container_width=True)


def schedule_panel(result: dict) -> None:
    if result.get("courses"):
        st.dataframe([{"流水號": c["section_id"], "課程": c["title"], "班別": c["class_name"],
                       "時段": "、".join(c["meetings"]) or "待確認",
                       "備註": c["review"]} for c in result["courses"]],
                     hide_index=True, use_container_width=True)
    schedule = result.get("schedule")
    if not schedule:
        return
    if schedule["feasible"]:
        st.success(f"課表通過已載明條件，共 {schedule['credits']} 學分")
    else:
        st.warning(f"草案未通過，共 {schedule['credits']} 學分")
    preferences = result["preferences"]
    target = preferences.get("target_credits")
    credit_condition = (f"目標 {target} 學分｜校方下限 {schedule['official_minimum']}"
                        if target is not None else
                        f"適用下限 {schedule['effective_minimum']}｜設定上限 {preferences['maximum']}")
    st.caption(f"{credit_condition}｜條件版本 {result['revision']}｜"
               f"{'、'.join(preferences['blocked']) or '未設定避開時段'}")
    if schedule["classes"]:
        rows = []
        for item in schedule["classes"]:
            row = {"流水號": item["section_id"], "課程": item["title"], "學分": str(item["credits"]),
                   "時段": "、".join(slot["label"] for slot in item["meetings"]) or "待確認",
                   "班別": item["class_name"]}
            if result["snapshot"]["input"] == "內建校方快照":
                year, term = result["term"].split("-")
                row["課程大綱"] = ("https://sea.cc.ntpu.edu.tw/pls/dev_stud/course_query.queryGuide?"
                               f"g_serial={item['section_id']}&g_year={year}&g_term={term}&show_info=part")
            rows.append(row)
        st.dataframe(rows, column_config={"課程大綱": st.column_config.LinkColumn(display_text="校方大綱")},
                     hide_index=True, use_container_width=True)
    for issue in schedule["issues"]:
        st.write(f"{issue['code']}｜{issue['message']} {issue['remedy']}")
    if result.get("excluded"):
        with st.expander("班別排除原因"):
            st.dataframe([{"流水號": "、".join(i["sections"]), "檢查": i["code"], "原因": i["message"]}
                          for i in result["excluded"]], hide_index=True, use_container_width=True)


def result_panels(result: dict, session: Session) -> None:
    if "error" in result:
        st.error(result["error"]["message"])
        st.write(f"{result['error']['code']}｜{result['error']['remedy']}")
    else:
        st.subheader(result["message"])
    panels = []
    if "schedule" in result or "courses" in result:
        panels.append(("課表與開課", lambda: schedule_panel(result)))
    if "audit" in result:
        panels.append(("學分檢核", lambda: audit_panel(result["audit"], result.get("projection"))))
    if "rules" in result:
        panels.append(("規章來源", lambda: sources_panel(result)))
    panels.append(("執行追蹤", lambda: trace_panel(result, session)))
    for tab, (_, show) in zip(st.tabs([label for label, _ in panels]), panels, strict=True):
        with tab:
            show()
    md, report_json = export_payloads(result)
    left, right, _ = st.columns([1, 1, 3])
    left.download_button("下載 Markdown", md, f"{result['run_id']}.md", "text/markdown")
    right.download_button("下載 JSON", report_json, f"{result['run_id']}.json", "application/json")


def sources_panel(result: dict) -> None:
    sources = {r["rule_id"]: r for r in result["rules"]}
    if "audit" in result:
        audit = result["audit"]
        for entry in [audit["core"]] + audit["categories"] + audit["evidence"] + audit["missing_required"] + audit["pending"]:
            source = entry["source"]
            sources.setdefault(source["rule_id"], source)
    st.caption(f"課規版本　{result['rule_version']}")
    for item in sources.values():
        st.markdown(f"**{item['rule_id']}｜{item['title']}**")
        st.markdown(f"{item.get('summary', '')} [{item['locator']}]({item['url']})")


def trace_panel(result: dict, session: Session) -> None:
    st.caption(f"執行編號 {result['run_id']}｜{result['elapsed_ms']} ms｜"
               f"模型呼叫 {result['llm_calls']} 次｜重排 {result['replans']} 次")
    if "log_status" in result:
        st.warning(result["log_status"])
    st.dataframe([{"步驟": s["step"], "事件": s["event"], "記錄": s["message"]}
                  for s in result["trace"]], hide_index=True, use_container_width=True, height=450)
    with st.expander("工具參數與觀察值"):
        st.json(dump_json(result["trace"]))
    with st.expander("本次會話最近五輪"):
        st.dataframe(list(session.recent), hide_index=True, use_container_width=True)


def main() -> None:
    agent = get_agent()
    now = time.monotonic()
    if now - st.session_state.get("last_active", now) > 1800:
        reset_session()
        st.info("會話已逾時，請重新登錄或匯入資料。")
    st.session_state["last_active"] = now
    generation = st.session_state.get("generation", 0)
    transcript = None
    with st.sidebar:
        st.header("學籍與資料")
        department = st.text_input("系所", agent.rules.department, key=f"department{generation}")
        admission = st.number_input("入學學年度（民國）", min_value=90, max_value=130, value=114,
                                    key=f"admission{generation}")
        year = st.selectbox(f"{agent.snapshot.term} 就讀年級", (1, 2, 3, 4), index=1, key=f"year{generation}")
        with st.expander("課規版本及認定資料"):
            version = st.text_input("課規版本", agent.rules.version, key=f"version{generation}")
            evidence = {key: st.selectbox(agent.rules.references[key].title,
                                          ("unknown", "confirmed", "missing"),
                                          format_func=EVIDENCE_LABELS.get, key=f"{key}{generation}")
                        for key in agent.rules.evidence}
        source = st.radio("修課資料來源", ("示範紀錄", "用課名登錄", "上傳 CSV"),
                          index=0 if generation == 0 else 1, key=f"transcript_source{generation}")
        if source == "上傳 CSV":
            csv_upload = st.file_uploader("已修課程 CSV", type="csv", key=f"transcript{generation}")
            transcript = csv_upload.getvalue() if csv_upload is not None else None
        with st.expander("匯入開課資料"):
            open_upload = st.file_uploader(f"{agent.snapshot.term} 開課 CSV", type="csv",
                                           key=f"offerings{generation}")
            st.download_button("下載官方快照 CSV", agent.public_offerings, "offerings.csv", "text/csv")
        offerings = open_upload.getvalue() if open_upload is not None else None
        st.download_button("下載修課 CSV 範本", transcript_csv(),
                           "transcript.csv", "text/csv")
        st.download_button("下載課號與學分對照", (DATA_DIR / "catalog.csv").read_bytes(),
                           "catalog.csv", "text/csv")
        st.button("清除本次會話", on_click=reset_session)
    st.title("選課與畢業學分檢核")
    st.caption(f"國立臺北大學資訊工程學系｜114 入學課規｜{agent.snapshot.term} 開課"
               f"｜快照 {agent.snapshot.retrieved_on} [校方課程查詢]({agent.snapshot.url})")
    sample = source == "示範紀錄"
    if sample:
        transcript = (DATA_DIR / "demo_transcript.csv").read_bytes()
        st.info("目前使用去識別示範紀錄。可在側欄選擇用課名登錄或上傳 CSV。")
    elif source == "用課名登錄":
        transcript = manual_transcript(agent, generation)
    profile = Profile(department=department, admission_year=admission, version=version,
                      year_level=year, evidence=evidence)
    fingerprint = hashlib.sha256(profile.model_dump_json().encode() +
                                hashlib.sha256(transcript or b"").digest() +
                                hashlib.sha256(offerings or b"").digest()).hexdigest()
    if st.session_state.get("inputs") != fingerprint:
        st.session_state.update(session=Session(profile), inputs=fingerprint, result=None)
    session = st.session_state["session"]
    with st.form(f"ask{generation}"):
        question = st.text_area("想查詢什麼", value=DEMO_QUESTION if sample and generation == 0 else "",
                                placeholder="例如「我還缺哪些畢業要求？」或「幫我規劃 20 學分的課表」",
                                max_chars=600, height=100, key=f"question{generation}")
        submitted = st.form_submit_button("執行", type="primary")
    if submitted:
        with st.spinner("執行課規與工具檢核"):
            st.session_state["result"] = agent.run(session, question, transcript, offerings)
    if st.session_state["result"] is not None:
        result_panels(st.session_state["result"], session)


main()
