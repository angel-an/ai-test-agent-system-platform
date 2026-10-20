tables=25  pages=10
  table[0] -> page 1
  table[1] -> page 1
  table[2] -> page 2
  table[3] -> page 3
  table[4] -> page 3
  table[5] -> page 3
  table[6] -> page 4
  table[7] -> page 4
  table[8] -> page 4
  table[9] -> page 5
  table[10] -> page 5
  table[11] -> page 6

===== 一级章节（_prd_sections）=====
[0] '店鋪主數據管理' body_len=4010
     head=['1.1 產品功能描述\u200b', '維護店鋪的主數據，方便在營銷過中，快速選擇店鋪；\u200b']
[1] '導入任務' body_len=659
     head=['2.1 導入任務列表頁\u200b', '2.1.1 原型圖\u200b']
[2] '商品主數據' body_len=1226
     head=['3.1 產品功能描述\u200b', '維護商品的主數據，方便在營銷過中，快速選擇商品。\u200b']
[3] '支付方式主數據' body_len=691
     head=['4.1 產品功能描述\u200b', '維護支付方式的主數據，方便在營銷過中，快速選擇支付方式。\u200b']

===== 目标字段规则 =====
- '店鋪編號' table=1 page=1 row=6 rebuilt=False
    required=False editable='否' type='輸入框' cond=''
    desc='小於等於30個字符；字符類型：大小寫字母、數字 提示：1、填寫店鋪編號後，以填寫的編號為準；2、未填寫店鋪編號，系統自動生成品牌編號。'
    summary='最长30字'
    constraints=[{'kind': 'max_length', 'raw': '小於等於30個字', 'numbers': [30.0], 'detail': {}}]
- '所屬品牌' table=2 page=2 row=2 rebuilt=False
    required=True editable='是' type='下拉單選' cond=''
    desc='選項範圍：品牌主數據中的所有啟用的品牌；'
    summary=''
    constraints=[{'kind': 'options', 'raw': '選項範圍：品牌主數據中的所有啟用的品牌', 'numbers': [], 'detail': {'options': ['品牌主數據中的所有啟用的品牌']}}]
- '營業狀態' table=2 page=2 row=17 rebuilt=False
    required=True editable='是' type='開關' cond=''
    desc='默認開啟關閉代表暫停營業；開啟時，APP顯示營業還是休息，根據營業時間決定。'
    summary=''
    constraints=[{'kind': 'default', 'raw': '默認開啟關閉代表暫停營業', 'numbers': [], 'detail': {'value': '開啟關閉代表暫停營業'}}]
- '營業時間' table=2 page=2 row=20 rebuilt=False
    required=True editable='是' type='' cond=''
    desc='最多支持選擇7組，'
    summary=''
    constraints=[]
- '店鋪編號' table=8 page=4 row=2 rebuilt=False
    required=False editable='' type='輸入框' cond=''
    desc='小於等於30個字符默認為空，為空時查詢全部'
    summary='最长30字'
    constraints=[{'kind': 'max_length', 'raw': '小於等於30個字', 'numbers': [30.0], 'detail': {}}, {'kind': 'default', 'raw': '默認為空', 'numbers': [], 'detail': {'value': '空'}}]
- '營業狀態' table=8 page=4 row=5 rebuilt=False
    required=False editable='' type='下拉單選' cond=''
    desc='選項範圍：全部、營業、不營業'
    summary=''
    constraints=[{'kind': 'options', 'raw': '選項範圍：全部、營業、不營業', 'numbers': [], 'detail': {'options': ['全部', '營業', '不營業']}}]
- '所屬品牌' table=8 page=4 row=6 rebuilt=False
    required=False editable='' type='下拉單選' cond=''
    desc='選項範圍：取主數據中的所有品牌+全部默認選中全部'
    summary=''
    constraints=[{'kind': 'options', 'raw': '選項範圍：取主數據中的所有品牌+全部默認選中全部', 'numbers': [], 'detail': {'options': ['取主數據中的所有品牌+全部']}}, {'kind': 'default', 'raw': '默認選中全部', 'numbers': [], 'detail': {'value': '全部'}}]
- '創建時間' table=13 page=6 row=3 rebuilt=False
    required=False editable='' type='時間段選擇器' cond=''
    desc=''
    summary=''
    constraints=[]
- '創建人' table=17 page=7 row=7 rebuilt=False
    required=False editable='' type='文字輸入框' cond=''
    desc='全模糊匹配'
    summary=''
    constraints=[]
- '創建時間' table=17 page=7 row=8 rebuilt=False
    required=False editable='' type='日期範圍選擇器' cond=''
    desc='範圍匹配'
    summary=''
    constraints=[]
- '更新人' table=17 page=7 row=9 rebuilt=False
    required=False editable='' type='文字輸入框' cond=''
    desc='全模糊匹配'
    summary=''
    constraints=[]
- '更新時間' table=17 page=7 row=10 rebuilt=False
    required=False editable='' type='日期範圍選擇器' cond=''
    desc='範圍匹配'
    summary=''
    constraints=[]
- '更新人' table=22 page=9 row=4 rebuilt=False
    required=False editable='' type='文字輸入框' cond=''
    desc='全模糊匹配'
    summary=''
    constraints=[]
- '更新時間' table=22 page=9 row=5 rebuilt=False
    required=False editable='' type='日期範圍選擇器' cond=''
    desc='範圍匹配'
    summary=''
    constraints=[]

===== display_columns（列表显示字段表）=====
- table=5 page=3 columns=['店鋪編號', '店鋪名稱', '店鋪名稱(EN)', '店鋪內部名稱', '所屬品牌', '所屬菜係', '優先級', '是否在APP顯示', '營業狀態', '排隊', '訂位', '外賣', '所屬組織', '操作人', '更新時間']
- table=16 page=7 columns=['商品編號', '商品名稱(CN)', '商品名稱(EN)', '商品內部名稱', '商品規格編號', '創建人', '創建時間', '更新人']
- table=21 page=9 columns=['支付方式編號', 'POS Payment Code', '支付方式名稱', '創建人', '創建時間', '更新人', '更新時間']

===== 兜底模块用例的证据归属 =====
(检查每条证据能否唯一命中某个一级章节)
fallback cases = 20

* '繁體/英文導入模板：操作類型為下拉單選，選項範圍：更新、創建、刪除操作區域（一區）區域（二區）區域（三區）每個新增必須在單元格內的刪除' rule_id=RQ-e853791c89 flags=[]
    table=None field=None
    section_hits=NONE

* '新建頁-保存按鈕-所有必填項填寫正確' rule_id=COMPANION-01 flags=['companion_template']
    table=None field='通用交互/系統場景'
    section_hits=NONE

* '新建頁-保存按鈕-必填項未填完整' rule_id=COMPANION-02 flags=['companion_template']
    table=None field='通用交互/系統場景'
    section_hits=NONE

* '新建頁-取消按鈕-點擊取消關閉且不保存' rule_id=COMPANION-03 flags=['companion_template']
    table=None field='通用交互/系統場景'
    section_hits=NONE

* '編輯頁-只讀字段不可編輯' rule_id=COMPANION-04 flags=['companion_template']
    table=None field='通用交互/系統場景'
    section_hits=NONE

* '查看模式-頁面置灰僅支持查看' rule_id=COMPANION-05 flags=['companion_template']
    table=None field='通用交互/系統場景'
    section_hits=NONE

* '列表頁-分頁-默認每頁條數與總條數' rule_id=COMPANION-06 flags=['companion_template']
    table=None field='通用交互/系統場景'
    section_hits=NONE

* '列表頁-分頁-頁碼跳轉與每頁條數切換' rule_id=COMPANION-07 flags=['companion_template']
    table=None field='通用交互/系統場景'
    section_hits=NONE

* '列表頁-篩選-多條件組合查詢' rule_id=COMPANION-08 flags=['companion_template']
    table=None field='通用交互/系統場景'
    section_hits=NONE

* '列表頁-篩選-重置恢復默認' rule_id=COMPANION-09 flags=['companion_template']
    table=None field='通用交互/系統場景'
    section_hits=NONE

* '列表頁-篩選-無匹配結果空態' rule_id=COMPANION-10 flags=['companion_template']
    table=None field='通用交互/系統場景'
    section_hits=NONE

* '列表頁-日期範圍-起始晚於結束反向校驗' rule_id=COMPANION-11 flags=['companion_template']
    table=None field='通用交互/系統場景'
    section_hits=NONE

* '操作日誌-新建與編輯記錄準確' rule_id=COMPANION-12 flags=['companion_template']
    table=None field='通用交互/系統場景'
    section_hits=NONE

* '功能權限-有權限用戶可操作' rule_id=COMPANION-13 flags=['companion_template']
    table=None field='通用交互/系統場景'
    section_hits=NONE

* '功能權限-無權限用戶拒絕操作' rule_id=COMPANION-14 flags=['companion_template']
    table=None field='通用交互/系統場景'
    section_hits=NONE

* '篩選-創建人-匹配規則校驗' rule_id=FLT-54509b0989 flags=['filter_field_case']
    table=None field='創建人'
    section_hits={'創建人': ['店鋪主數據管理', '導入任務', '商品主數據', '支付方式主數據']}

* '篩選-創建時間-匹配規則校驗' rule_id=FLT-ce89070982 flags=['filter_field_case']
    table=None field='創建時間'
    section_hits={'創建時間': ['店鋪主數據管理', '導入任務', '商品主數據', '支付方式主數據']}

* '篩選-更新人-匹配規則校驗' rule_id=FLT-5dc0e185f0 flags=['filter_field_case']
    table=None field='更新人'
    section_hits={'更新人': ['店鋪主數據管理', '商品主數據', '支付方式主數據']}

* '篩選-更新時間-匹配規則校驗' rule_id=FLT-6d91127a3d flags=['filter_field_case']
    table=None field='更新時間'
    section_hits={'更新時間': ['店鋪主數據管理', '商品主數據', '支付方式主數據']}

* '人工補充-營業時間段重疊處理待確認' rule_id=None flags=['manual_supplement']
    table=None field=None
    section_hits=NONE

===== 導入條款（_extract_import_clauses）=====
count=23
  - '創建新店鋪時，紅色字體的為必填字段，如果不填寫，會導入失敗；'
  - '創建新店鋪時，如果填寫的店鋪編號在數據庫中存在，新會導入失敗；'
  - '創建新店鋪時，如果導入的excel中存在多條店鋪編號相同的數據，則店鋪編號相同的數據都導入失敗；'
  - '刪除和更新時店鋪編號為必填項，不填寫會導入失敗；'
  - '更新時，導入的字段值為空時，保持數據庫原有的值不變；'
  - '每次最多導入2000條；錯誤原因：'
  - '必填字段不可為空；'
  - '創建店鋪時，店鋪編號已存在；'
  - '更新店鋪時，店鋪編號字段不可為空或不存在；'
  - '刪除店鋪時，店鋪編號字段不可為空或不存在；'
  - '導入Excel中，需要創建的店鋪，店鋪編號重複；'
  - '導入Excel中，需要更新的店鋪，店鋪編號重複；'
  - '導入Excel中，需要刪除的店鋪，店鋪編號重複；'
  - '1、xxx字段：不能超過50個字符；2、XX字段：必填未填寫；'
  - '特殊字段：同一个字段支持传多组值时，第一組為必填；每組數據單獨檢驗完整性，不完整也退款失敗。'

===== 導入補充（manual_supplement）來源段落 =====
{
  "manual_supplement": true,
  "confirmation_status": "pending",
  "executable_track": "manual",
  "source_file": "YL-主数据管理.pdf",
  "source_evidence": "特殊字段：同一个字段支持传多组值时，第一組為必填；每組數據單獨檢驗完整性，不完整也退款失敗。；子项：營業時間1-周幾？、營業時間1-時間1；"
}
