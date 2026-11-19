# 工具定義與作業對照

## 工具契約

| 名稱 | 目的 | 輸入 | 輸出 | 錯誤處理 |
| --- | --- | --- | --- | --- |
| `match_rules` | 選定適用課規 | 系所、入學年度、版本、佐證鍵值 | 已確認課規與課程目錄 | 缺值回 `PROFILE_REQUIRED`，版本不合回 `RULESET_MISMATCH` |
| `lookup_rules` | 定位課規依據 | 已確認課規、問題與預設規則 | 規則 ID、版本、節錄摘要、網址、條次 | 無詞彙命中時顯示工作流的預設來源 |
| `import_transcript` | 驗證並標準化修課紀錄 | UTF-8 CSV、課程目錄 | 已核對型別、學分與代碼的資料列 | 缺欄、未知課號、學分不符或多餘欄位會停止檢核 |
| `audit_credits` | 算已採計、缺額及認定狀態 | 課規、學籍與驗證後的修課資料 | 各分類、必修、核心、非學分門檻與逐筆理由 | 未核准抵免、特殊分類與缺佐證保留待確認 |
| `import_offerings` | 驗證開課快照 | CSV、目錄、選定學期 | 按流水號索引的班別資料 | 混入其他學期、重複流水號或資料錯誤會停止試排 |
| `find_offerings` | 篩選候選班別 | 快照、先修通過紀錄、年級及避開時段 | 可用班別與逐門排除原因 | 保留缺先修、限修、時段不明、停開等結構化原因 |
| `draft_schedule` | 從候選班別組出基準草案 | 班別、缺額、已確認偏好 | 草案流水號，另記跳過的衝堂 | 候選不足時交驗證工具解釋 |
| `validate_schedule` | 查整份草案是否可行 | 班別流水號、快照、學籍、先修及限制 | 學分、時段、可行性、違規理由與修正方式 | 有已知衝堂、缺先修、負荷不符或資料待核時保留草案，只有通過才計預估 |
| `export_report` | 取得同次檢核輸出 | 結構化檢核結果及本機目錄 | Markdown 與 JSON | CLI 寫入失敗提示目錄權限並退出，GUI 可直接下載 |

## 作業要求對照

| 評量 | 對應證據 |
| --- | --- |
| Working Agent 6% | `app.py`、`python -m credit_agent`、`Containerfile`、A01 |
| Tool／Workflow／ReAct／Planning 5% | `credit_agent/agent.py`、`tools.py`、A01 的實際 Tool Call 與 Observation |
| 功能完整性 3% | 版本核對、匯入、學分與認定、排課驗證、會話條件、引用與匯出 |
| Error Handling 2% | A02 低於學期下限、A04 課規不符、A05 缺先修、A06 衝堂、A07 缺欄、A09 學期未建檔 |
| Testing／Failure Evidence 2% | `tests/`、`scripts/run_cases.py`、`test-results/` |
| README／執行環境 2% | `README.md` 的 Podman 建置、執行、設定、測試與排錯 |

工作流的每一道檢查都有固定的工具契約。Agent 依意圖選路，確認規則版本與資料後才檢核，觀察驗證結果決定產出預估或提示修正。決策及工具摘要均由實際執行產生。後續重排可重用同一份驗證輸出。

成功步驟可從 [A01.json](../test-results/A01.json) 的 `trace` 欄位逐步重播，全部案例與預期值見 [summary.json](../test-results/summary.json)。
