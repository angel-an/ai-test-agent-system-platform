====================================================================================================
# YL-主数据管理.pdf
字段规则 69 条 / 用例 163 条 / 断言缺口 12 个需求点

## 1. 零覆盖字段规则（没有任何用例引用该行/该字段）：7 条
   （该表用例数：{1: 9, 2: 20, 4: 12, 8: 10, 13: 1, 19: 8, 24: 5, 17: 9, 22: 4}）
  - table=2 row=18 '營業狀態關閉日期'
      desc='當店鋪狀態開啟時，顯示該字段支持設置營業狀態關閉日期，到達營業狀態關閉日期後營業狀態變為已關閉；如果不設置，代表一直開啟；示例：2026-10-01 00:00:00'
      constraints=[] required=False filter=False heading='1.2.1.2 字段說明'
  - table=2 row=19 '營業狀態開啟日期'
      desc='當店鋪狀態關閉時，顯示該字段支持設置營業狀態開啟日期，達到營業狀態開啟日期後營業狀態變為已開啟；如果不設置，代表一直關閉；示例：2026-10-01 00:00:00；'
      constraints=[] required=False filter=False heading='1.2.1.2 字段說明'
  - table=4 row=1 '排隊控制該店鋪是否支持排隊，以及優先取票規則'
      desc='每組中需要設置營業時間，並設置這個時間用於週一到週日的哪幾天；多組營業時間，週幾不能重複；一天最多支持設置2個時間段，且2個時間段不能重複；示例：週一、週二、週三，09:00~14'
      constraints=[] required=False filter=False heading='1.2.1.2 字段說明'
  - table=4 row=8 '訂位控制該店鋪是否支持訂位'
      desc=''
      constraints=[] required=False filter=False heading='1.2.1.2 字段說明'
  - table=4 row=12 '外賣控制該店鋪是否支持跳轉到外賣頁'
      desc=''
      constraints=[] required=False filter=False heading='1.2.1.2 字段說明'
  - table=8 row=9 'EDW更新時間'
      desc='最多選擇30天'
      constraints=[] required=False filter=False heading='1.2.2.4 篩選查詢區'
  - table=13 row=4 '狀態'
      desc='選項發範圍：待開始、進行中、已完成、失敗'
      constraints=[] required=False filter=False heading='2.1.4 篩選查詢區'

## 2. 断言覆盖（有断言 / 总数）
  - COMPANION   0/ 14  (0%)
  - FLT         0/ 17  (0%)
  - FR         60/ 61  (98%)
  - IMP         0/ 20  (0%)
  - LST         0/  3  (0%)
  - MAP         0/  1  (0%)
  - NONE        0/  1  (0%)
  - RO          0/  6  (0%)
  - RQ         10/ 40  (25%)

## 3. 数量/范围类表述的约束识别
  - '最多支持選擇7組，' → 未识别为任何约束
  - '最多選擇30天' → 未识别为任何约束
  - '最多支持選擇' → 未识别为任何约束
  - '最大長度50' → 未识别为任何约束

## 4. 模块分布
  -  114  店鋪主數據管理
  -   21  商品主數據
  -   14  通用建议（非PRD条款）
  -   13  支付方式主數據
  -    1  導入任務
  模块总数=5；含简化字模块名=['通用建议（非PRD条款）']

## 5. 未提取到可判定断言的需求点（12 个）
  - '維護店鋪的主數據，方便在營銷過中，快速選擇店鋪' → 用例 1 条
  - '創建新店鋪時，紅色字體的為必填字段，如果不填寫，會導入失敗' → 用例 1 条
  - '更新時，導入的字段值為空時，保持數據庫原有的值不變' → 用例 1 条
  - '每次最多導入2000條' → 用例 1 条
  - '必填字段不可為空' → 用例 1 条
  - '特殊字段：同一个字段支持传多组值时，第一組為必填' → 用例 1 条

## 6. 溯源字段齐备度：line 69/69，section 69/69
  用例侧 source_location 覆盖率：61/163
====================================================================================================
# 小杨生煎-订单评价(问卷调查)功能 (1).pdf
字段规则 30 条 / 用例 41 条 / 断言缺口 14 个需求点

## 1. 零覆盖字段规则（没有任何用例引用该行/该字段）：24 条
   （该表用例数：{4: 2, 6: 4}）
  - table=6 row=1 '业务类型'
      desc='1. 多选，必选2. 可选项：外卖、自提、堂食3. 未选校验：请选择业务类型'
      constraints=[] required=False filter=False heading='2026.9.24 新增'
  - table=7 row=2 '是否必答'
      desc='是否必答'
      constraints=[] required=False filter=False heading='1. 必填'
  - table=9 row=1 '标题'
      desc='标题'
      constraints=[] required=False filter=False heading='1. 必填'
  - table=9 row=2 '操作-删除题目'
      desc='操作-删除题目'
      constraints=[] required=False filter=False heading='1. 必填'
  - table=9 row=3 '选项内容'
      desc='选项内容'
      constraints=[] required=False filter=False heading='1. 必填'
  - table=9 row=5 '添加选项'
      desc='添加选项'
      constraints=[] required=False filter=False heading='1. 必填'
  - table=9 row=6 '删除选项'
      desc='删除选项'
      constraints=[] required=False filter=False heading='1. 必填'
  - table=9 row=7 '拖拽选项'
      desc='拖拽选项'
      constraints=[] required=False filter=False heading='1. 必填'
  - table=9 row=8 '选项内容'
      desc='选项内容'
      constraints=[] required=False filter=False heading='1. 必填'
  - table=9 row=9 '添加选项'
      desc='添加选项'
      constraints=[] required=False filter=False heading='1. 必填'
  - table=9 row=10 '删除选项'
      desc='删除选项'
      constraints=[] required=False filter=False heading='1. 必填'
  - table=9 row=11 '拖拽选项'
      desc='拖拽选项'
      constraints=[] required=False filter=False heading='1. 必填'
  - table=10 row=1 '说明'
      desc='说明'
      constraints=[] required=False filter=False heading='2.3.2 调查问卷管理'
  - table=12 row=2 '已启用的问卷即为投入使用，不可继续编辑'
      desc='已启用的问卷即为投入使用，不可继续编辑'
      constraints=[] required=False filter=False heading='2.3.2 调查问卷管理'
  - table=12 row=3 '已禁用的问卷不再支持投放'
      desc='已禁用的问卷不再支持投放'
      constraints=[] required=False filter=False heading='2.3.2 调查问卷管理'
  - table=13 row=2 '填写渠道'
      desc='固定显示，包括支付宝小程序/微信小程序'
      constraints=[] required=False filter=False heading='2.3.6 导出明细'
  - table=13 row=3 '用户名'
      desc='固定显示，获取用户昵称，为空时显示“-”'
      constraints=[] required=False filter=False heading='2.3.6 导出明细'
  - table=15 row=1 '会员卡号'
      desc='固定显示，获取会员卡号'
      constraints=[] required=False filter=False heading='2.3.6 导出明细'
  - table=15 row=2 '门店名称'
      desc='固定显示，获取该用户触发问卷的门店名称，为空时显示“-”'
      constraints=[] required=False filter=False heading='2.3.6 导出明细'
  - table=15 row=3 '门店编码'
      desc='固定显示，获取该用户触发问卷的门店编码，为空时显示“-”'
      constraints=[] required=False filter=False heading='2.3.6 导出明细'
  - table=15 row=4 '关联订单号'
      desc='固定显示，获取触发该问卷的订单号，为空时显示“-”注意：若直接扫门店问卷链接二维码，则获取不到订单号'
      constraints=[] required=False filter=False heading='2.3.6 导出明细'
  - table=15 row=5 '关联订单实付金额'
      desc='固定显示，获取触发该问卷的订单实付金额，为空时显示“-”注意：若直接扫门店问卷链接二维码，则获取不到订单实付金额'
      constraints=[] required=False filter=False heading='2.3.6 导出明细'
  - table=15 row=6 '填写时间'
      desc='固定显示，获取用户填写提交问卷时间'
      constraints=[] required=False filter=False heading='2.3.6 导出明细'
  - table=15 row=7 '问题及回答'
      desc='获取问卷内所设置的问题及回答1. 若问题类型为单选题，则问题格式为[单选题]问题N：{这里是问题标题}；用户所填写信息格式为所选择的“选项内容”2. 若问题类型为多选题，则问题格式'
      constraints=[] required=False filter=False heading='2.3.6 导出明细'

## 2. 断言覆盖（有断言 / 总数）
  - COMPANION   0/ 14  (0%)
  - FR          6/  6  (100%)
  - RQ          4/ 21  (19%)

## 3. 数量/范围类表述的约束识别
  - '最多输入20个字' → ['max_length']
  - '限制最多设置10道' → 未识别为任何约束

## 4. 模块分布
  -   22  营销云-调查问卷管理
  -   14  通用建议（非PRD条款）
  -    3  调查问卷-推送订阅消息
  -    2  小杨生煎-订单评价(问卷调查)功能 (1)
  模块总数=4；含简化字模块名=['通用建议（非PRD条款）']

## 5. 未提取到可判定断言的需求点（14 个）
  - '消费后支持对订单进行评价，需支持指定商品触发评价' → 用例 1 条
  - '云徙提供问卷明细表，支持导出功能' → 用例 1 条
  - '操作入口：营销云-营销中心-调查问卷管理-新建' → 用例 1 条
  - '注意，刚创建完成的问卷为“待启用”，需启用后才可正式投放使用' → 用例 1 条
  - '状态---默认选中全部，可下拉筛选全部/待启用/启用/禁用' → 用例 1 条
  - '操作：支持新建，以及对问卷进行查看、编辑、启用/禁用、删除、导出明细' → 用例 1 条

## 6. 溯源字段齐备度：line 30/30，section 30/30
  用例侧 source_location 覆盖率：6/41
