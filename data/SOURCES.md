# 課規與開課資料核對

**校系**　國立臺北大學資訊工程學系日間學士班  
**版本**　`NTPU-CSIE-114-20250410`  
**查詢日**　2026-11-19（臺北時間）

## 官方資料

| 來源 | 核對及用途 |
| --- | --- |
| [資工系 114 入學修業辦法索引](https://www.csie.ntpu.edu.tw/pages_detail/2170) | 114 規劃表、外語指標、系訂程式能力、抵免 |
| [114 學士班專業科目規劃表](https://www.csie.ntpu.edu.tw/uploads/file/dat_202504151527295407.pdf) | 核對第 1–5 頁永久課號、26 門必修合計 64 學分、各課學分及專業核心標記。第 5 頁列畢業總學分 132、系選修至少 24 |
| [資工系修業規章](https://www.csie.ntpu.edu.tw/uploads/file/dat_202504151533597023.doc) | 第 2 條明示共同選修與外系合計最多 20，並有共同選修 6 學分子上限。第 3 條規定核心選修至少 3 門、至少 2 領域 |
| [官方共同必修對照](https://sea.cc.ntpu.edu.tw/pls/eval/CMAP_5SYS.ntpu_course113?syear=114&deptno=LU32) | 113 後入學共同必修 24，國文與英文各 4、向度通識 16，另有零學分必修體育 |
| [通識教育課程實施辦法](https://cms-carrier.ntpu.edu.tw/uploads/11_28_450fc52100.pdf) | 第 9 條核對 16 學分及六向度任五，雷同課程仍需認定 |
| [114 新生手冊](https://cms-carrier.ntpu.edu.tw/uploads/114_0ccc5d270b.pdf) | 語文通識課程表與減修條件。一般學士班大一至大三最低 16 學分，大四非延修生最低 9 |
| [畢業生專區](https://graduate.ntpu.edu.tw/) | 校定程式能力及跨域學習等門檻，由個別認定狀態確認 |
| [115-1 資工系學士班官方查詢結果](https://sea.cc.ntpu.edu.tw/pls/dev_stud/course_query_all.queryByAllConditions?qEdu=U&qCollege=&qdept=TU01&qYear=115&qTerm=1&qGrade=&qClass=&cour=&teach=&qMemo=&week=&seq1=A&seq2=M) | 36 筆，含系所查詢列出的共同及跨系班別 |
| [115-1 向度通識查詢](https://sea.cc.ntpu.edu.tw/pls/dev_stud/course_query_all.queryByOther?qYear=115&qTerm=1&qDept=GU18&qGroup=&week=&seq1=A&seq2=M) | 六向度各取 2 筆，使用 `105-向度` 標籤、明確的每週時段及可核對的學分 |
| [校方節次對照](https://sea.cc.ntpu.edu.tw/pls/dev_stud/course_query_all.time_memo) | 一律用校方節次換成實際起訖時間 |
| [115-1 U3063 課程大綱](https://sea.cc.ntpu.edu.tw/pls/dev_stud/course_query.queryGuide?g_serial=U3063&g_year=115&g_term=1&show_info=part) | 115-1 大綱列程式設計，114 規劃表另列人工智慧導論，試排同時檢核 |

115-2 資工系查詢於查詢日回報無符合條件之課程，課程查詢表單標示公開範圍到 115-1。歷年課名用於核對版本，程式執行不連線向官網重新擷取。[官方課程查詢系統](https://sea.cc.ntpu.edu.tw/pls/dev_stud/course_query_all.chi_main)

## 資料建檔與適用界線

- `rules.json` 依來源逐項轉錄，`rule_id` 指向版本、網址與條次或頁碼。`catalog.csv` 為同版課號及學分對照，`TU…` 為系規劃表的永久課號。開課流水號屬於學期班別。`ZH-…`、`EN-…`、`GE-…` 為原型的對照代碼，`REVIEW-…` 代表採計對照待核。
- `offerings.csv` 的時段使用實際每節區間。校方列為每週的班別按每週標記，主題式英文扣除首週實體上課。未知時段、明確停開、專題需指導同意，以及未核對採計的跨系班別保留提示。
- 本版依 114 課規與學期大綱檢查已載明的先修。有「微積分」但未區分學期的機率課，本版用微積分 I、II 都通過作自動確認條件。其他認定交由系辦確認。開課表的「應修年級」隨班別顯示，明確限修另以資料欄位檢查。
- 向度通識在本版畢業採計計至 16，另以已通過且可對照的科目核對向度。超修或特殊替代的正式採計由系辦核定。主題式英文採入學學年各一門不同主題，全學年制需有上下學期通過紀錄。免修、非等學分抵免、轉系或學則例外需另核。
- 程式驗證候選班別整份試排的負荷、先修、限制和時段。若本學期已有確定班別，應將其流水號一併輸入「驗證」以檢查整份課表。學分上限來自使用者設定，校方額外收費、名額和選課資格由校方系統判定。
- `demo_transcript.csv` 全部為虛構修課與分數。核准代碼只示範資料欄位。個人輸入欄位不得包含姓名、學號或證明全文。

檢視時的來源檔案摘要可和下列值核對。校方更新頁面後，當期資料的雜湊會變動。

| 來源 | SHA-256 |
| --- | --- |
| 114 專業科目規劃 PDF | `7ce2aefa0676f3b1b6ad56f00f3bcfb2fbeb450ac7510bd4451641ab89400dcc` |
| 115-1 資工系查詢 HTML | `c27e5ee4080b1dcaa351780a795f803aae56106fe68b51ee1865c110b4ef28f4` |
| 115-1 向度通識查詢 HTML | `c1f9329ba0f4ddcc4fd908e3b1cf3afb611b46244fd4ed5780fcbb3a334b7a59` |
