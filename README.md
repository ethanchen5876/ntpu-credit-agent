# 選課與畢業學分檢核 Agent

AI Agent 課程指定作業 2

這個 Agent 依國立臺北大學資訊工程學系 **114 學年度入學課規**檢查畢業學分，並使用 **115 學年度第 1 學期**官方開課快照試排。可從 Streamlit 問問題、匯入去識別修課資料、查看工具追蹤，以及下載 Markdown 與 JSON。

- 課規版本為 `NTPU-CSIE-114-20250410`。畢業總學分 132、系必修 64、系選修至少 24。系統另查向度分布、核心選修與非學分門檻。[資工系 114 入學專業科目規劃表](https://www.csie.ntpu.edu.tw/uploads/file/dat_202504151527295407.pdf)
- 截至 2026-11-19，校方開放查詢至 115-1。內建快照含資工系學士班查詢的 36 筆資料與六向度各 2 筆通識班別，共 48 筆。公告停開或有限制的班別仍可查詢，試排會依條件排除。[資料來源與核對紀錄](data/SOURCES.md)
- 示範修課紀錄為測試資料。系所認定、開課異動、名額與加簽以校方為準。

## 用 Podman 啟動

使用 Linux 或 Windows 的 WSL2 Ubuntu，建議 4 核心、8 GB RAM。第一次建置需連線下載 Python 基底與套件。內建課規、課表、檢核與試排可離線執行。

WSL2 Ubuntu 尚未安裝 Podman 時，在 Ubuntu 終端安裝。已安裝者可直接在專案目錄建置。

```bash
sudo apt-get update
sudo apt-get install -y podman
```

```bash
podman build --layers -t ntpu-credit-agent:0.2 .
podman volume create ntpu-credit-agent-state
podman run --rm --name ntpu-credit-agent -p 127.0.0.1:8501:8501 \
  -v ntpu-credit-agent-state:/state ntpu-credit-agent:0.2
```

開啟 [localhost:8501](http://localhost:8501)，使用預填示範問題按下「執行」。應看到目前採計 **40／132 學分**、**18 學分**候選課表與 **58 學分**的通過後預估。可到「課表與開課」檢查班別時段，到「規章來源」定位引用，或到「執行追蹤」查看工具輸入摘要與觀察值。

同一會話輸入「改成最多 15 學分」會重算並撤下舊預估。示範學籍為大二，一般下限 16 學分，系統會回報 `LIMIT_INCOMPATIBLE`。再輸入「改成最多 18 學分」可恢復試排。

## 操作與資料格式

1. 在側欄確認系所、入學年、就讀年級、課規版本與認定狀態。
2. 要檢核自己的資料時，關閉示範開關，下載課號對照與修課範本。請將已修資料整理成 UTF-8 CSV。選填的外語、體育、程式能力與跨域認定用側欄表示。
3. 填入問題後按「執行」。可用「查 CPE 規章」、「我還缺哪些畢業要求？」、「查詢開課 U3063」、「規劃 115-1 課表，16 至 18 學分，避開週五下午」、「驗證 U2287、U3270」。
4. 下載結果後可用「清除本次會話」清理本次輸入。閒置逾 30 分鐘後，下一次介面操作會清除舊會話。

### 修課 CSV

```csv
course_id,term,credits,status,score,approval_ref
TU99U30004,114-1,3,passed,88,
TU01U30006,114-2,3,transfer_approved,,APR-DEMO1
TU01U30046,114-2,3,transfer_pending,,
```

必填欄位為 `course_id`、`term`、`credits`、`status`，最多 300 筆。學期使用民國學年，例如 `114-2`。狀態使用 `passed`、`failed`、`withdrawn`、`in_progress`、`planned`、`transfer_approved` 或 `transfer_pending`。含分數門檻的先修可提供 `score`。已核准等學分抵免以受認定的本校課號及去識別 `APR-…` 代碼表示。欄位限於範本內容。

### 開課 CSV

可選擇匯入同學期、最多 200 筆的開課 CSV。範本為 [offerings.csv](data/offerings.csv)。`section_id` 代表學期流水號，`course_id` 使用課程目錄的代碼，`class_name` 記載班別。

- `meetings` 格式為 `3@09:10-10:00@all;3@10:10-11:00@all`，星期 1 到 7，週次可用 `all`、`odd`、`even`、`1-8+10` 或 `?`。週次或時段待確認的班別會停在驗證。
- `prerequisites` 可用 `TU99U30004>=60;TU01U30046`，分號表示兩項皆須達成。只有課號時檢查通過或核准抵免。
- `permitted_years` 可填 `3;4`，留空代表未錄入年級限制。`review` 用於停開、特殊限修或採計對照未確認。使用內建課表可從班別表開啟校方大綱。

## 命令列與驗證

```bash
podman run --rm --network none ntpu-credit-agent:0.2 python -m credit_agent --demo
podman run --rm --network none ntpu-credit-agent:0.2 \
  python -m credit_agent --demo --json -q "驗證 U3063"
podman run --rm --network none ntpu-credit-agent:0.2 \
  python -m unittest discover -s tests -v
mkdir -p replay
podman run --rm --network none -v "$PWD/replay:/evidence" ntpu-credit-agent:0.2 \
  python -m scripts.run_cases --out /evidence
```

CLI 支援同次重複 `--question` 保留條件、`--transcript`、`--offerings` 匯入掛載的 CSV、`--out` 匯出 Markdown／JSON。執行成功退出碼為 0，條件不符或草案未通過為 2，匯出目錄寫入失敗為 3。

已跑案例可由 [summary.json](test-results/summary.json) 查閱，每案有原始 JSON，A01 另附可閱讀的 [Markdown 報告](test-results/A01-report.md)。各案例的執行時間是本次量測值。[單元與介面測試紀錄](test-results/unittest.txt)

## 設定與技術

| 設定 | 用途 |
| --- | --- |
| `AGENT_DATA_DIR` | 課規、課表與隨附範例目錄。預設使用映像內的 `/app/data` |
| `AGENT_STATE_DB` | SQLite 執行摘要路徑。映像預設 `/state/agent.sqlite3`，可掛載命名卷 |
| `AGENT_LLM` | 預設由本地意圖與狀態邏輯決策，設為 `1` 可啟用 OpenAI 意圖分類 |
| `OPENAI_API_KEY` | 只有啟用模型時使用，以環境變數傳入 |
| `LLM_MODEL` | 可覆寫固定模型 `gpt-4.1-mini-2025-04-14` |

啟用模型時，請先在自己的終端設定金鑰，並將所需環境變數以 `podman run -e AGENT_LLM=1 -e OPENAI_API_KEY` 傳入。只送詞彙白名單辨識出的意圖訊號。API 暫時失敗至多重試一次，之後回到本地工作流。SQLite 只保存執行摘要、版本、耗時和雜湊。報告依使用者操作輸出逐筆去識別結果。[模型文件](https://developers.openai.com/api/docs/models/gpt-4.1-mini)

受控工作流依序確認課規、檢索條文、檢核學分、篩選班別、產生草案並獨立驗證。本階段已知工具順序與停止條件，採單一目標導向 Agent。條文檢索使用人工核對的摘要與關鍵詞，同一課規版本的資料可重現。作業三可在這份排序式基準上加入驗證回饋與重排。[工具契約與評分對照](documents/tools.md)

## 常見狀況

| 狀況 | 處理方式 |
| --- | --- |
| 埠 8501 已使用 | 將 `-p 127.0.0.1:8501:8501` 的左側埠改為 8502，瀏覽器使用相同埠 |
| Podman 無法取得依賴 | 首次建置確認可下載 Python 映像與 PyPI 套件，再重跑建置 |
| 課規不符 | 確認系所、入學年度和版本。其他學籍需先建檔 |
| `UNKNOWN_COURSE` | 用永久課號或目錄內對照代碼。外系及未收錄課程先確認採計依據 |
| 課表未通過 | 查看原因。可調整班別、補齊先修認定或重設限制後再驗證 |
| 需要其他學期 | 等校方公開該學期資料後，更新快照、來源與測試基準 |

GitHub 繳交以本資料夾為儲存庫根目錄。上傳後確認老師及助教可以讀取 Repository Link，再於課程平台繳交。
