# -*- coding: utf-8 -*-
"""用例 → 执行脚本 编译层（v1，最小实现）。

职责：把**已生成的用例**编译成执行脚本可直接消费的清单，并记录血缘：

- 用例 → 规则编号（``requirement_rule_id``）→ 断言（``custom_fields.assertions``）
- 断言按"能否在没有定位器的情况下执行"分级：本版把需要页面定位器的断言标为
  ``pending_locator``（**不伪装成通过**），只把文档字段名做**软校验**并输出覆盖度。
- 产出片段内嵌进 Playwright 脚本（执行器只下载单个脚本文件，因此清单必须内嵌）。

刻意不做的事：不猜选择器、不伪造通过、不引入模型。
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import json
import re
from dataclasses import dataclass, field as dc_field
from functools import lru_cache
from typing import Any, Iterable, Mapping

from app.services.web_upload_fixtures import (
    XLSX_MIME,
    bad_extension_bytes,
    minimal_xlsx_bytes,
    sample_spec,
    xls_base64,
    xlsx_parts_base64,
)

#: 产物编译器版本指纹（P0-2，2026-10-19）。
#: 生成期写入 spec 头部（注释 + 模块级常量）与 ``web_tests.generation_params.compiler_rev``；
#: 执行前由 ``runner`` 读 spec 头部比对，缺失/不一致即判「旧产物」。
#: ⚠️ 改动执行器模板（``_FORM_EXECUTOR_TS`` / 各业务模板）或编译逻辑时**必须同步 bump 本值**。
#:    **不要依赖这条注释——它由两处机制强制执行**（见 ``compiler_source_digest``）：
#:     1. ``backend/tests/test_compiler_rev_guard.py``：模板/逻辑源码变了而本值没变 → 测试必红；
#:     2. 产物头部同时写 ``COMPILER_DIGEST``；执行期 digest 不符即判旧产物，
#:        即便有人手工把 rev 改成同一个字符串也逃不过。
#:    bump 记录：
#:   - rev117（2026-10-19）：首次引入指纹；提交项看门狗 + SUBMIT_STAGE 心跳（P0-1）
#:   - rev118（2026-10-19）：引入 digest 绑定 + 严格模式三态（auto/on/off）
#:   - rev119（2026-10-20）：字段级上传放行（A1）—— ``field_locators.upload_allow``
#:     经 ``upload_policy.allow_upload_rules`` 下发；新增 ``UPLOAD_POLICY`` 自证日志与
#:     ``upload_policy_gap`` 缺口诊断。修的是「附件类用例没有场景 ⇒ 从无上传放行 ⇒ 恒 unverified」。
#:   - rev120（2026-10-20）：CS 模板抽出 ``enterStorageAccountList``。
#:     原先 form_executor 的上传隔离分支调用了一个**只在储值模板里存在**的函数 ⇒ 每轮抛
#:     ``ReferenceError`` 并静默回退 ``main_fallback``（TR-726/728/729 实测一致），
#:     所谓"上传独立上下文"从未生效过。抽成函数后主流程与隔离上下文共用同一段入口。
#:   - rev121（2026-10-20）：digest 扩围到 ``web_case_link_service`` 的 ``_generate_*_script``。
#:     rev120 暴露的盲点：产物一半来自那些业务模板，而 digest 只算本模块 ⇒ 改模板时 digest 不变，
#:     registry「rev ↔ digest 一一对应」被打破（守卫当场拦下）。改为 AST 静态抽取，不 import。
#:   - rev122（2026-10-20）：隔离上下文先落到登录页再进 CRM。
#:     rev121 起 ``enterStorageAccountList`` 真的被调用了，但隔离上下文是**新开 page**（about:blank），
#:     直接 ``openCrm`` 找不到 CRM 入口（TR-730 实测 ``UPLOAD_CONTEXT main_fallback: 未找到…CRM 入口``）。
#:     只在页面还没加载任何文档时先 goto 登录页；主流程 page 已在控制台，不触发。
#:   - rev123（2026-10-20）：隔离上下文改为**重放登录**。
#:     rev122 的 goto 不够：``context.storageState()`` 只带 cookie 与 localStorage，
#:     **不带 sessionStorage**，而本 SUT 会话态在 sessionStorage ⇒ goto 后仍停在登录页（TR-731）。
#:     改为调用同一 ``loginToConsole``（含验证码 OCR），与储值模板的入口函数完全同语义。
#:   - rev124（2026-10-20）：运行期唯一化（A2 / ``{{RUN_TAG}}``）。
#:     真实落库场景的填值原先在**编译期**就定死（``FORM_PLAN`` 是快照）⇒ 同一份产物
#:     重跑必撞服务端唯一约束（``20003 编号已存在``），而期望受理通道把它判成
#:     「疑似缺陷坐实」= **假缺陷**。现在占位符原样留在快照里，执行器每次运行算新值；
#:     同时「数据重复」类拒绝改判 unverified（非功能缺陷），并输出 ``RUN_TAG_GAP``
#:     列出"真实落库但值固定"的字段。
#:   - rev125（2026-10-20）：``run_tag_gap`` 判据收窄 + 纳入 digest。
#:     TR-733 实测：店鋪電話 ``12345678`` 被"≥4 位连续数字"命中，每轮都报一条
#:     ``RUN_TAG_GAP`` —— 这种噪声会让整条诊断被忽略，而它正是"重复运行必然 20003"
#:     的唯一提示渠道。改为同时要求 ≥2 个字母。``run_tag_usage`` / ``run_tag_gaps``
#:     也列入 digest（它们产出计划字段，却不属于 ``build_form_plan`` 源码）。
#:   - rev126（2026-10-20）：唯一化自证 ``RUN_TAG_APPLIED``。
#:     ``RUN_TAG_USED`` 只证明"我们算了这个值"，不证明**控件里真的是这个值**——
#:     带 maxLength 的控件会静默截断，截掉的若是尾段（恰好是区分两次运行的部分），
#:     就等于没唯一化却照样报"已唯一化"。现在填入后回读 inputValue 比对，截断即标出。
#:   - rev127（2026-10-20）：随机段 2→3 位 + 「数据重复」判据去掉裸词「唯一」。
#:     · 2 位随机撞车概率 1/1296，并行跑同一场景时有实际风险；3 位降到 1/46656。
#:     · 判据里写 ``唯一`` 会把「編號唯一性校驗不通過」这类**真缺陷**文案也降级成
#:       "数据冲突"，正好是要避免的反向误判；改为只认 ``唯一约/唯一約/unique constraint``。
#:   - rev128（2026-10-20）：新增业务模板 ``survey_operation``（营销云-系统-调查问卷管理，
#:     小杨生煎项目）。``link_service`` 新增 ``_generate_survey_operation_script`` 与
#:     ``_is_survey_operation_case``（digest 覆盖 ``_generate_*_script``，故必须 bump）。
#:   - rev129（2026-10-20）：规则级场景的 request/response 监听器挂到**应用所在页面**
#:     （anyScope 是 Page 时挂 anyScope，FrameLocator 回退 page）。实测 TR-742：门户
#:     卡片式应用（数盈营销云）新开标签页后，监听器挂在主 page 上一个请求都收不到，
#:     服务端已受理（数据已落库）却判 unverified —— 判定层假阴性。
#:   - rev130（2026-10-20，F-1/F-2/F-3 同批）：
#:     · F-2：rev129 只修了负向场景那半，**提交层**四处 `page.on(...)` 仍是主 page ——
#:       同一根因（门户卡片新标签页），触发条件换成 submit_phase 的 probe/required_empty
#:       就会重演假阴性；本次统一为 `submitListenPage`。
#:     · F-3：新增 `COVERAGE_RECONCILE`（planned / executable / skipped_no_locator /
#:       scenario_executed / emitted + 两个 unexplained 差额），把「计划 7 / 可执行 5 /
#:       实发 6 / 跳过 2」三套口径收成一条可判定恒等式；`FORM_PLAN.planned_assertions`
#:       由 `build_form_plan` 写入（同快照，不再跨快照对账）。
#:   - rev131（2026-10-20，F-3 修正）：`render_form_executor` 的 payload 是**白名单**
#:     序列化，rev130 漏登记 `planned_assertions` ⇒ 实测 TR-748 打出
#:     `planned=0 / unexplained_plan=-3 / balanced=false`，对账行反而制造新误导。
#:     本版补上该键；教训：凡运行期要读的计划字段必须在 payload 白名单里显式登记。
#:   - rev134（2026-10-20，别名与默认值落地）：①`field_locators.aliases` 的命中
#:     写进回执（`field_alias_applied`），并单列**悬空别名**（目标键不存在）——
#:     旧实现里"别名配错"与"别名没配"在回执上完全一样，配置方会去改定位器而不是改别名；
#:     ②`aliases`/`defaults` 从 `NON_LOCATOR_CONFIG_KEYS` 里剔除语义（它们是行为配置，
#:     不该在预览里被数成定位器）；③RUN_TAG 回读改走 `controlValue()`——
#:     直连 `input.inputValue()` 对 contenteditable 返回长度错乱，唯一化自证会误报截断。
#:   - rev135（2026-10-20，注释收敛）：rev134 执行器里的说明注释写了旧调用字面量
#:     （`input.inputValue` 带括号），被"富文本不得走 inputValue"的护栏测试按子串命中
#:     ⇒ 测试误红。**行为零变化**，只把注释里的字面量去掉。bump 的唯一理由：
#:     产物 digest 覆盖执行器模板全文，注释改动也会改 digest（这是设计，不是误伤）。
#:   - rev136（2026-10-20，对账口径修正）：`COVERAGE_RECONCILE` 的 plan 侧恒等式
#:     减去 `config_only_checks`。配置驱动的检查（默认选中/条件显示）不是文档断言
#:     翻译来的，旧公式把它们算进 executable ⇒ 恒为负差额。实测 TR-752 报
#:     `planned=4 / executable=6 / unexplained_plan=-2 / balanced=false`，
#:     那 -2 不是缺口，是 2 条默认值观测被误当断言。
#:   - rev137（2026-10-20，字段检查前置动作 ``pre_actions``）：子功能可声明
#:     "检查前先点一下"（labels/for_fields/times/wait_ms），执行器**按需**在第一条
#:     作用于目标字段的检查前触发，并输出 ``PRE_ACTION`` / ``PRE_ACTIONS_PLAN`` 自证行。
#:     实测背景（TR-752/753 + 探针）：问卷题目卡片在点「新增题目」前根本不存在
#:     （`[class*=questionCard]` = 0），定位器本身正确 ⇒ 旧口径只能记 unverified，
#:     与"选择器配错"无法区分。前置动作失败时后续检查报"前置动作未完成"，
#:     不再报"未定位到输入框"（后者会把排障引向定位器）。
#:   - rev138（2026-10-20，字段级**作用域** + 现场行诚实化）：rev137 让「新增题目」真的
#:     点成了（TR-754 `PRE_ACTION ok=true`），但四条断言仍报"未定位到输入框"——
#:     探针证明 `form.contains(card) === false`：题目卡片渲染在 `<form class="ant-form">`
#:     **之外**（同一选择器全文档命中 1 / 表单内 0）。新增 `fields.<字段>.scope="page"`
#:     让定位按**字段**切层（不放宽 `modal`：那会连带放大默认值/单选 `:has-text` 的匹配范围）。
#:     同时修 `[observation]` 的 tried：旧实现只回显 `placeholder_hint`（本 SUT 未配 ⇒ 恒 ''），
#:     而执行时用的是 `selector_hint` ⇒ 现场行看起来像"没配 hint"，把排障引向补 hint。
#:     现在写出真实使用的选择器与 `scope` 层，并用 `LOCATE_SCOPE` / `LOCATE_SCOPE_INVALID`
#:     自证声明是否落地。
#:   - rev139（2026-10-20，对账口径补 ``submit_resubmitted``）：rev138 修好作用域后
#:     TR-755 出现 ``emitted=9 / executable=6 / scenario=1 / unexplained_emitted=2 /
#:     balanced=false``。那 2 条不是丢失 —— 提交层把两条 ``required_error`` **又记了一次**
#:     （真实提交才算证据，这是设计）。旧公式把重发算成"多发"，于是"没丢"被报成"丢了 2 条"。
#:     本版显式列出 ``submit_resubmitted`` 并计入恒等式；提交层计数改用独立包装
#:     ``submitRec``，不拿 ``queued`` 当代数（超时/中断也会各写一条）。
#:   - rev140（2026-10-20，错误检测**锚定控件** + 提交层时间线/限界）：TR-756 两条
#:     ``required_error`` 卡 ``SUBMIT_TIMEOUT stage=settle_done 90s``。探针拿到地面真相：
#:     ① 卡片内 form-item 的标签是「标题」，规则字段名是「题目标题」→ ``:has-text`` 恒不匹配；
#:     ② ``modal`` 配置为 ``.ant-form``，题目卡片在 ``.ant-form`` 之外 → 作用域内恒 0；
#:     ③ 错误真实形态是 antd v3（``.ant-form-explain`` + ``.ant-form-item-control.has-error``，
#:     ``formItemHasError=0``）。三者叠加 = fieldError 恒 false → settle 永不返回。
#:     修复：``itemHasErrorCtl`` 按字段定位路径（与 fill 同源）找到控件 → 向上找最近
#:     ``.ant-form-item`` 查 explain/has-error，锚定失败退回旧行；客户端层与提交层
#:     检测点全部换用。防线补强：late 观察窗限界 20s（不再吃满单项预算）；「无业务写
#:     请求 + 错误面较基线扩大」即收敛为前端拦截（文案如实写明判定依据）；
#:     ``SUBMIT_SETTLE`` / ``SUBMIT_LATE`` 时间线打点，卡死可归因到具体轮次。
#:   - rev141（2026-10-20，radio 组兜底填充 + 前端拦截**归因**）：rev140 实测（TR-757）
#:     只好了一半 —— 收敛防线触发了（``form_err_base:0→now:3``）但判定分支没有承接；
#:     探针3（Playwright 原生交互复刻）拿到最终地面真相：三个 radio 组的默认选中
#:     **未注册进表单状态**，保存被它们拦在**外层表单**，卡片表单（题目标题/选项1）
#:     根本走不到校验 → 目标字段错误永不渲染（全页仅 1 条 explain「请选择业务类型」，
#:     卡片 explainCount=0）。修复：① ``fillEmptyErrored`` 真实点击 radio/checkbox 组
#:     （旧逻辑被 radio 的 ``inputValue`` 恒非空 + 「单选类无法程序化填充」双重跳过）；
#:     ② ``itemHasErrorCtl`` 祖先查找改 class **精确 token** 匹配（旧 contains 会先命中
#:     ``div.ant-form-item-control``，探针3 实锤）；③ ``__frontendBlocked`` 独立判定分支
#:     带**归因**：重测目标字段命中才 pass，否则如实 unverified（被其他必填项拦截）；
#:     ④ 合成点击先 count 再 evaluate（空定位器 evaluate 白等 30s —— 选项1 项 90s 卡死
#:     的真凶）。
#:   - rev142（2026-10-20，**toast 文本匹配** + 别名反查归因）：rev141 实测（TR-758）
#:     归因失败——探针4 + 截图拿到最终形态：保存被拦的提示是**warning 样式 toast**
#:     「题目 11：请输入标题」（橙色叹号），既不在 error 类 TOAST_SEL 里、也不是卡片
#:     explain（探针4：外层表单合法后保存，卡片 explainCount 仍为 0）——必填拦截走
#:     toast，不走 inline。且 toast 文案含的是**页面标签「标题」**而非规则字段名
#:     「题目标题」。修复：① ``blockToastText`` 按文本（请输入/请选择/必填/不能为空）
#:     匹配任意 message/notification，不依赖样式类；② 载荷下发 ``field_alias_applied``，
#:     执行器反转成「定位器键 → 页面别名」表，``attrHit`` 同时查字段名与别名 → toast
#:     归因到目标字段才 pass，否则如实 unverified 并打 ``SUBMIT_ERR_DETAIL``（explain
#:     归属 + toast 全文）；③ late 循环加「连续 5 轮请求面/错误面无变化」早停
#:     （TR-758 选项1 项白跑 66s）。
#:   - rev143（2026-10-21，``ERRORED_ITEM`` 补 antd v3 形态 + toast 快查）：TR-759 的
#:     ``SUBMIT_ERR_DETAIL`` 终于打出 3 条 explain 的真身——「请输入问卷标题 / 请选择
#:     业务类型 / 请输入优先级」，全是**外层表单**必填项。``fillEmptyErrored`` 之所以
#:     没填它们：``ERRORED_ITEM`` 是 v4 偏置选择器（``.ant-form-item.has-error``），
#:     本 SUT 是 v3（`.has-error` 在 control 上，probe1 实测 formItemHasError=0）⇒
#:     恒匹配 0 个 ⇒ 兜底填充空转 ⇒ 保存永远被外层拦死，卡片校验走不到。修复：
#:     ① ERRORED_ITEM 补 ``:has(.ant-form-explain)`` / ``:has(.has-error)``；
#:     ② settle/late 循环里 toast **先**于全量观察快查（toast 存续 ~3s，observe 全链
#:     ~2.5s 可能整段错过窗口）。
#:   - rev144（2026-10-21，guard 反转修正 + 多 toast 拼接 + 未选中优先）：TR-760 实测
#:     ERRORED_ITEM 已能命中 3 项（remaining=3）但兜底填充仍未执行——重读控制流发现
#:     required_empty 到达 guard 时目标字段必然无错误（有错误 settle 已命中返回）⇒
#:     ``guardOk`` 恒 false ⇒ ``if (!guardOk) return`` 恒早退 ⇒ fillEmptyErrored 对
#:     required_empty 是**死代码**（自 rev69 起如此，此前从未暴露是因为别家 SUT 的
#:     必填拦截不依赖「先填满外层表单」）。修复：① required_empty 放行 guard（probe
#:     守卫不变）；② checkbox 组优先点**未选中**项（业务类型默认视觉选中但表单状态为空，
#:     点已选中项会把可见状态点掉）；③ blockToastText 拼接**全部**命中 toast（一保存
#:     多条，只取第一条会让选项1 的归因被标题的 toast 挡掉）。
#:   - rev145（2026-10-21，共享化 fillErroredItems + block_siblings + label 点击验证）：
#:     TR-761 的 ``required_error:题目标题`` **首次 pass**（toast 快查 419ms 命中 + 别名
#:     「标题」归因）。收尾三件事：① ``ERRORED_ITEM``/兜底填充逻辑提升为共享
#:     ``fillErroredItems(container, skipMarked)``（提交层薄壳调用，场景层复用）；
#:     ② ``fields.<字段>.block_siblings``：卡片校验逐字段只报**第一个**空字段且每个提交项
#:     重开表单 ⇒ 标题恒空 ⇒ 选项1 的 toast 永远轮不到（TR-761 实测 block_toast 只有
#:     两条标题）——提交前先填声明的兄弟必填；③ ``clickByLabels`` 对 radio/checkbox 加
#:     **点击后验证**（label 文本点击未必触发 onChange，TR-761 外卖点击未生效 ⇒ 场景被
#:     「请选择业务类型」拦成 unverified），未选中则对 input 合成 click 兜底。
#:   - rev146（2026-10-21，TDZ 修复）：rev145 的 ``block_siblings`` 循环引用了声明在
#:     下方 TOAST 区的 ``itemDef`` ⇒ ``ReferenceError: Cannot access 'itemDef' before
#:     initialization`` ⇒ 两条 required_error 全 blocked ⇒ 硬失败闸把 TR-762 拒掉
#:     （blocked=2 视为结构性失败，这是设计行为——恰好把回归挡在了门外）。改为直接读
#:     ``fieldDefs[check.field]``。
#:   - rev147（2026-10-21，回执键改名）：``pending_locator_count`` →
#:     ``locator_depended_assertion_count``（实义是「依赖定位器的断言数」，原名与
#:     ``skipped_assertion_count`` 并排易读反成「待定位」。manifest 发射新键；执行器
#:     模板读新键并回退旧键（旧产物 manifest 无新键时 ``?? 0`` 兜底）；link_service
#:     回执同步改名 + 回退。``COMPILED_COVERAGE.pending_locator``（运行期输出键）不变。
#:   - rev148（2026-10-21，第二批通道能力三合一）：
#:     ① ``list_checks[].action``：行内操作（更多菜单/直点 + Popconfirm/Modal 确认，
#:        操作后重按搜索回读列表，以回读结果判 expect；操作面扫描含数据行独立第二行 tr）；
#:     ② ``list_checks[].readonly``：查看只读（进入查看页后统计 input/textarea/select
#:        禁用率，全禁用才 pass，READONLY_TRACE 留现场）；
#:     ③ ``export_checks`` 独立导出通道（更多菜单→导出明细弹窗→空时间段校验 toast→
#:        antd v3 日历选"倒数第二天~最后一天"→确认→导出中心轮询已完成→下载落盘
#:        →xlsx 极简 zip 解压做列名**出现性**校验，不校验列序；EXPORT_TRACE/
#:        EXPORT_EVIDENCE 留证据）。交互配方来自人工探针 xy_p1b/p2/p8 实测。
#:     诚实边界：解压失败/轮询超时/下载缺失一律 unverified，不判缺陷不判通过。
#:   - rev149（2026-10-21，rev148 导出通道重构收尾）：
#:     ① 下载监听前置——``waitForEvent('download')`` 必须在点确认**之前**挂上
#:        （注册晚了会错过 download 事件，实测该 SUT 确认即触发 exportDetail 下载）；
#:     ② 直下/导出中心双路收敛为单一 ``saveAndInspect``（合并 saveAs+fs 落盘+zip 列名
#:        校验），消除旧 step7 的重复 ``dl``/``columns`` 声明（TS 编译会炸）；
#:     ③ 直下成功后**跳过**导出中心轮询（否则轮询超时误报"无匹配记录"）；
#:     ④ ``toastAfter`` 在确认后采集（EXPORT_TRACE 取证引用）。
#:   - rev150（2026-10-21，列表/导出通道 hash 直链）：
#:     ``resolveListUrl``——配置可写 ``#/x`` 形式的列表直链，运行期以**当前页**
#:     origin+path+search（保留 per-session accessToken）还原成完整 URL 再 goto。
#:     背景：门户卡片式应用的应用 URL 带 per-session token，写死整链必失效；
#:     表单阶段结束后页面停在新增路由，必须 hash 导航回列表路由才能执行列表/导出通道。
#:   - rev151（2026-10-21，TR-764 挂死根因）：
#:     ① ``appPageForNav``——门户卡片式应用**新开标签页**，fixture 的 ``page`` 是控制台首页；
#:        列表/导出阶段改锚定 ``anyScope``（应用作用域）→ 非控制台页兜底 → ``page`` 兜底，
#:        ``resolveListUrl`` 一并按目标页解析 origin（否则还原成控制台 origin，goto 后挂死）；
#:     ② ``phaseWatchdog``——列表 180s / 导出 240s 硬上限，超时记 PHASE_WATCHDOG 并继续，
#:        不再让 evaluate 永不返回把整轮预算（28min）烧光。
#:   - rev152（2026-10-21）：列表阶段 ``LIST_STAGE`` 埋点（nav_page / goto_begin / goto_done /
#:     root_ready / scan_done）——跨标签页导航卡住时必须能分清卡在 goto 还是 evaluate。
#:   - rev153（2026-10-21，TR-765 实测）：列表页是异步 SPA，固定等 5s 采到 **0 行** ⇒
#:     ① ``waitRows`` 有界轮询到出现行（列表/导出两阶段 goto 后各 30s，搜索后 20s）；
#:     ② 导出通道入口未打开（row_not_found/entry_not_found）时**跳过**导出中心轮询，
#:        不再白等 180s 并产出"导出中心无匹配记录"的伪结论。
#:   - rev154（2026-10-21，TR-766 + 探针实测）：下拉菜单条目改用 ``clickDropdownItem``
#:     （真实鼠标点击优先）——``evaluate`` 的 ``.click()`` 不触发该框架的菜单处理器，
#:     表现为"点导出明细毫无反应"（无弹窗/无请求/无 toast）；改鼠标点击后正常弹出
#:     「导出明细 / 选择时间 ~ / 取 消 确 定」。另：弹窗未出现且未配导出中心 URL 时
#:     不再轮询导出中心（``centerWorth`` 门控），避免 180s 白等与伪结论。
#:   - rev155（2026-10-21，TR-768 + 探针实测）：**操作列在右侧固定列的克隆 tbody 里**——
#:     整页是「10 个数据行 + 10 个操作行」，数据行与操作行**不相邻**（TR-768 导出
#:     menu_not_found 的根因）。新增 ``rowWithOps``：按「同 tbody 内行序号对齐」取出
#:     该数据行对应的操作行；action / readonly / export 三处统一改用它。
#:   - rev156（2026-10-21，TR-769 + DOM 实测）：本 SUT 的操作项**随行状态变化**——
#:     有的行是「查看/编辑/启用/更多」，有的行是「查看/复制/删除/导出明细」（无「更多」）。
#:     ① ``list_checks[].action.labels``：候选标签数组，按序取第一个真实存在的
#:        （启用/禁用互换时同一份配置可复用）；
#:     ② 导出通道：配置了 ``menu`` 但该行没有「更多」时，**退回直点标签**
#:        （stage='clicked_direct'），不再直接判 menu_not_found。
#:   - rev157（2026-10-21，rev157 探针三连实测）：导出通道三个「假自证」全部堵掉——
#:     ① **任务名 ≠ 文件名**：导出中心任务名 `调查问卷-XY..-2026-10-21 10:31:24`（无后缀），
#:        下载文件名 `..10_31_24.xlsx`（有后缀）。此前拿 `filename_regex`（要求 `.xlsx`）
#:        去匹配任务名，恒不命中，导致轮询 90s 空转记 unverified。新增 `center_name_regex`
#:        （默认 `调查问卷-|問卷調查-`），`filename_regex` 只做下载取证。
#:     ② **选时间必须点两次且回读**：RangePicker 只点一次时输入框仍为空，点确定被前端
#:        校验拦截、导出请求根本不发出，而旧代码无条件置 `datesPicked=true`。改为
#:        点「当月首日 + 当月末日」后**回读弹窗输入框**，两个值都非空才算选上。
#:     ③ **只认本轮新任务**：历史行同样满足「已完成 + 名称前缀」，会假阳性。记点确认
#:        时刻，要求命中行「创建时间」不早于确认时刻减 `fresh_tolerance_s`（默认 180s）；
#:        未命中时把同名候选数与前 3 行写进断言，避免下次只能靠猜。
#:   - rev158（2026-10-21，TR-771 实测 `EXPORT_STAGE rows:2` + `row_not_found`）：
#:     列表页会**保留上一阶段的搜索条件**——`goto` 与当前完全相同的 hash 是 no-op，
#:     SPA 不重新加载，表格里只剩上次搜索命中的 2 行，导出目标行（另一个 value）必然找不到。
#:     导出阶段定位行前先按本次 `value` 重新检索（`#title` / placeholder 候选），
#:     检索后重新 ``pickRoot``（表格重渲染会换 frame/节点），并埋 `EXPORT_STAGE searched`。
#:   - rev159（2026-10-21，TR-772 实测 9/9 列名全 missing）：本 SUT 导出的 xlsx 把中文写成
#:     **HTML 数字实体**（`&#22635;&#20889;…` = 填写…），`indexOf('填写渠道')` 恒不命中。
#:     列名校验前统一解码实体（十/十六进制 + 命名字符），并把解压文本、原始 latin1、
#:     原始 utf8 三路拼接后一起解码（兼顾 deflate 与 store 两种写入方式）。
#:   - rev160（2026-10-21，TR-772 实测）：**行内操作失败曾被判 pass**——配置了 action
#:     但 ``step1.ok=false``（候选标签都不在当前行状态的操作项里），旧逻辑只把它写进 note，
#:     ``found``（搜索命中）仍为 true ⇒ 记 pass。改为：动作或确认框任一步没走通即记
#:     **unverified**，文案带候选标签与 trace 锚点，避免「通道没验证」被当成通过。
#:   - rev161（2026-10-21，TR-773 实测：列名仍 9/9 missing）：``zipText`` 改**读中央目录**。
#:     旧实现顺序扫 local file header 并在 ``csize=0`` 时 break，而本 SUT 导出的 xlsx
#:     **每个成员都是 data descriptor**（flag 0x0808）⇒ 首个成员就中断、抽出文本长度 0。
#:     实测对比：旧 0/9 命中、新 9/9 命中（text 5130 字符）。
#:   - rev162（2026-10-21，P0 数据自消耗）：``list_checks`` 支持 ``row_pick`` ——
#:     按列文本（缺省「状态」）筛行、取命中行的指定列当 value，``value`` 随之变成可选。
#:     根因：动作通道会推进状态（待启用→启用→禁用终态），写死 value 的配置跑 2–3 次
#:     必然找不到目标行（TR-773→775：FCZ 走完一轮换 PFG，PFG 本轮又进「启用」）。
#:     匹配**精确优先**（否则 in=["启用"] 会命中「待启用」）；未命中记 unverified
#:     并带 cells 样本，不再静默滑向下一个断言。
#:   - rev163（2026-10-21，TR-776 实测两处）：
#:     ① ``build_form_plan`` 漏传 ``row_pick``/``pick_label`` ⇒ plan 里根本没有选择器，
#:        执行器拿到的 value 是空串；
#:     ② 空 value 会**匹配任意行**（scan 恒 hits>0、动作点到第一行），日志上像成功
#:        实际目标行没被选中 —— 空 value 一律记 unverified，不进搜索/扫描/动作链路。
#:   - rev164（2026-10-21，TR-777 实测 pick 只看到 1 行）：pick 前**清空搜索并重新查询**。
#:     列表页会保留上一检查项的搜索条件（同 hash goto 是 no-op、SPA 不重载），
#:     上一个 readonly 断言按自己的 value 搜过之后只剩 1 行 ⇒ row_pick 必然 no_row_matched。
#:   - rev165（2026-10-21，P1 必填校验时机探针，`probe_req_timing*` 两次实测）：
#:     ① **时机必须分开采样**：清空后**不 blur** 先读提示（onChange），拿不到再 blur（onBlur）。
#:        实测 问卷标题/优先级=清空后 120ms 即出现「请输入…」⇒ onChange；题目标题/选项1
#:        两种时机都无字段级提示，提交时才给 toast「题目1：请输入标题」且**零写请求**发出
#:        ⇒ 提交时校验 + toast 形态（不是缺陷，也不是"blur 没测到就是前端漏校验"）。
#:     ② **伪 pass 防护**：清空前先记提示基线，只有"清空后新增的文案"才算本次触发。
#:        旧行把残留提示当成触发，任何一次校验失败过就会一直判 pass。
#:     ③ **过程态不重复计数**：客户端取不到证据时的 unverified 只是过程记录，提交层给出
#:        结论后从 total 里剔除（TR-779 实测 total=8/pass=7/unverified=1，真实是 7/7）。
#:   - rev166（2026-10-21，TR-780 实测 unexplained_plan=-3）：``config_only_checks``
#:     判定改**只看 source**。``list_checks``/``export_checks`` 带**自造** case_id
#:     （TC-VIEWONLY-REV157 之类，仅用于结果归属台账），旧条件"locator_config 且无
#:     case_id"把它们排除在配置驱动之外 ⇒ plan 侧恒等式恒为负
#:     （planned=1 - (executable 6 - config_only 2) = -3，balanced 永远 false）。
#:   - rev167（2026-10-21，TR-784 实测 13 pass/14 unverified）：radio 类助手补**字段级作用域**。
#:     问卷「题目类型/是否必答」的 radio 组在题目卡片里，卡片在 `.ant-form`（modal）之外；
#:     ``radioOption``/``optionLabels`` 钉死 modal 作用域且 ``optionLabels`` 不用
#:     selector_hint ⇒ default_selected 报「未观测到默认选中」（实际 SUT 默认选中
#:     真实存在：探针 单选题/是 均为 checked=true）、radio_options 报「未读到单选选项」。
#:     两者改走 ``scopeOf`` + hint 命中的 .ant-form-item → 内部 .ant-radio-wrapper。
#:     另：defaults 带 owners 的 default 断言（代为取证）进 reconcile 恒等式——
#:     新增 ``default_covered_assertions``（plan 侧）与 ``default_covered``（对账行），
#:     消掉 TR-784 的 unexplained_plan=4 假缺口。
#:   - rev168（2026-10-21，TR-785 复盘）：两处收尾。① ``render_form_executor`` 白名单
#:     漏 ``default_covered_assertions`` ⇒ TR-785 对账行 ``default_covered:0`` 恒 0、
#:     ``unexplained_plan:4`` 照旧（与 rev131/TR-748 同型：**运行期要读的计划字段必须
#:     在白名单登记**）。② ``choice_options`` 补 reject 分支——非法值不在勾选组里
#:     应判 pass（与 radio_options 同语义）；TR-785「不存在的枚举值」在 外卖/自提/堂食
#:     里不存在却报 unverified，是判向反了。
#:   - rev169（2026-10-21，P4 窄口径落地）：``compiler_source_digest`` import 期预热。
#:     lru_cache 只把重算压到每进程一次，「首次调用」仍可能落在编辑窗口（linecache 按
#:     mtime 重读半截文件 → ast.parse 500）。预热 = digest 随进程启动固化，在途请求
#:     永不读源码；撞上编辑也是启动即崩（当场可见），不再中途 500。
#:   - rev170（2026-10-21，P3 第一项）：「筛选区」独立通道 ``filter_checks``。
#:     覆盖审计实证：字段级正/逆/边界已全自动化，筛选区连用例都缺。本通道做成与
#:     list_checks/export_checks 同形的独立阶段：状态下拉（antd v3 按 placeholder 候选
#:     定位）→ 选项真实点击（先精确后包含，防「启用」⊂「待启用」）→ 结果集**第 1 页
#:     可见行**目标列全量命中 → ``reset`` 配置则点重置并断言控件清空。被拒不静默
#:     （``[filter-skip]``）；``filter_check_rejects`` 进 payload 白名单与 summary
#:     ``filter_phase``；``parse_filter_checks`` 纳入 digest。⚠️ 同批修正 rev169 缺陷：
#:     digest 预热原在模块体前部，``_FORM_EXECUTOR_TS`` 等尚未定义即被 ``g.get``
#:     静默跳过 ⇒ 固化**残缺指纹**（fec94279864a，不含执行器模板）；预热挪到模块
#:     末尾，回归测试断言 warm==full。
#:   - rev171（2026-10-21，TR-789 复盘）：``reset_clear_values``。本 SUT「重置」是把
#:     下拉置回「全部」而非清成 placeholder（TR-789 FILTER_TRACE 实测 value="全部"），
#:     旧判定（value 空 或 placeholder 可见）恒 false。回显值命中配置等值集也算清空。
#:   - rev172（2026-10-21，TR-790 复盘）：交互配方两补。① 每条检查前先点「重置」清
#:     残留（goto 同 hash 是 no-op、SPA 保留上一条件，rev158/164 同源教训）；
#:     ② 选中选项后**点「搜索/查询」触发刷新**——本 SUT 选中不自动查询，TR-790 实测
#:     选「待启用」后结果集仍是上一条件残留的「启用」行（假 unverified）。
#:   - rev173（2026-10-22，P3 第二项）：「状态×操作矩阵」独立通道
#:     ``action_matrix_checks``——按状态定位行 → 断言行内操作集（expect_row/forbid_row，
#:     数据行与克隆操作行双扫描）→ 更多菜单枚举（expect_menu）→ confirm 配置驱动
#:     验确认框（Modal+Popover 双采样）后**点取消还原**。目标状态不在第 1 页时
#:     ``filter: true`` 先状态下拉筛选（复用 rev172 配方：选中后点搜索）。被拒不
#:     静默（``[matrix-skip]``）；``action_matrix_rejects`` 进 payload 白名单与
#:     summary ``matrix_phase``；``parse_action_matrix_checks`` 纳入 digest。
#:   - rev174（2026-10-22，TR-792 复盘）：confirm(via menu) 的菜单重开时序。枚举阶段
#:     Escape 可能没真正关掉菜单（焦点不在菜单上），此时再点「更多」会把它 toggle
#:     **关闭** ⇒ 点删除时 items:0（TR-792 启用/待启用两单 unverified 的根因）。
#:     改为先探测已开菜单，没有才点「更多」。
#:   - rev175（2026-10-22，TR-793 复盘）：菜单项点击方式。confirm(via menu) 的目标项
#:     对合成 click（evaluate 里 el.click()）**无响应**——TR-793 clicked:ok 却
#:     dialog_found:false，探针 5s 全 DOM 转储为空、记录仍在；Playwright 真实
#:     输入管线（locator click）才触发 Popconfirm（探针2 实证）。优先 locator
#:     真实点击，失败回退合成。
COMPILER_REV = "rev175"

#: 纳入 digest 的模块级常量。**只放影响执行结果的东西**：改注释、改日志文案不会命中，
#: 避免为了一个错别字也要 bump 版本（那样规则很快会被人绕开）。
_DIGEST_CONSTANTS: tuple[str, ...] = (
    "_FORM_EXECUTOR_TS",
    "FORM_CHECK_KINDS",
    "LOCATOR_DEPENDENT_KINDS",
    "SOFT_CHECK_KINDS",
)

#: 纳入 digest 的编译函数（源码级）。新增/修改这些函数的实现都会改变 digest。
_DIGEST_FUNCTIONS: tuple[str, ...] = (
    "compile_cases",
    "probe_value_for",
    "build_form_plan_for_template",
    "build_form_plan",
    # rev125：计划里的 run_tag / run_tag_gap 由这两个纯函数产出，它们**不在**
    # build_form_plan 的源码里 ⇒ 不列入就改了判据而 digest 不变（同一类盲点，
    # 与 rev120「模板不在 digest 覆盖内」同源）。
    "run_tag_usage",
    "run_tag_gaps",
    # rev137：前置动作的解析规则（次数/等待上限、labels 归一化）在这里，
    # 它直接决定运行期点几下 ⇒ 必须纳入 digest，否则"改了判据而产物不变指纹"。
    "parse_pre_actions",
    # rev162：``row_pick`` 的解析语义（精确优先 / value 取自哪一列 / 缺省列名）直接
    # 决定运行期选哪一行 ⇒ 与 rev137 的 parse_pre_actions 同源，必须纳入 digest。
    "parse_row_pick",
    "parse_list_checks",
    # rev170：filter_checks 的解析语义（option 必填 / expect_in 缺省 / reset 正则）
    # 直接决定运行期选哪个选项、断言哪列 ⇒ 与 parse_list_checks 同源，必须纳入 digest。
    "parse_filter_checks",
    # rev173：状态×操作矩阵的解析语义（status_in 必填 / confirm 结构 / 数组字段校验）
    # 直接决定运行期断言哪些操作 ⇒ 同源，必须纳入 digest。
    "parse_action_matrix_checks",
    "render_form_executor",
)


@lru_cache(maxsize=1)
def compiler_source_digest() -> str:
    """当前编译器「会改变产物行为」的源码摘要（12 位）。

    为什么不用整文件 hash：整文件会把注释、无关服务、docstring 都算进去 —— 改一行说明就
    要求 bump 版本，规则必然被「反正无所谓」绕开。这里只圈定**模板与编译逻辑本身**。

    为什么需要它：``COMPILER_REV`` 是人手写的字符串，「忘了改」在运行时完全看不出来
    （旧产物 rev 等于当前 rev，一切正常）。digest 由源码算出，忘改 rev 时：
    - 单测立刻红（registry 登记不上）；
    - 万一单测被跳过，产物里的旧 digest 与新 digest 不符，执行期仍判旧产物。

    rev120 补充：产物 = 本模块的执行器片段 **+** ``web_case_link_service`` 里的业务模板
    （``_generate_*_script``）。原先只算前者 —— 改 CS/YL 模板时 digest 不变、只有 rev 变，
    registry 的「rev ↔ digest 一一对应」约束被打破（rev120 实测被守卫拦下，拦得对）。
    这里用 AST 静态抽取后者，**不 import**：避免循环依赖，也保证「有无 import 都算出同一个值」。
    """
    g = globals()
    parts: list[str] = []
    for name in _DIGEST_CONSTANTS:
        value = g.get(name)
        if value is None:
            continue
        if isinstance(value, (set, frozenset)):
            rendered = "|".join(sorted(str(item) for item in value))
        else:
            rendered = str(value)
        parts.append(f"{name}={rendered}")
    for fn_name in _DIGEST_FUNCTIONS:
        fn = g.get(fn_name)
        if fn is None:
            continue
        parts.append(f"{fn_name}:\n{inspect.getsource(fn)}")
    parts.extend(_link_service_template_parts())
    blob = "\x00".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:12]


def _link_service_template_parts() -> list[str]:
    """``WebCaseLinkService`` 里所有 ``_generate_*_script`` 的源码（按名字排序）。

    这些方法就是 CS / YL / 储值等 minimal-loop 脚本的业务模板 —— 产物的一半在它们里面。
    抽取方式必须**确定性**（不依赖模块是否已导入），否则同一份代码在不同上下文算出不同
    digest，执行期会把正常产物误判成旧产物。
    """
    import ast
    from pathlib import Path

    path = Path(__file__).with_name("web_case_link_service.py")
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except Exception:  # noqa: BLE001 - 读不到就留痕（digest 会与正常态不同 ⇒ 守卫会红）
        return ["link_service_template=<unreadable>"]
    found: dict[str, str] = {}
    for node in tree.body:
        if not (isinstance(node, ast.ClassDef) and node.name == "WebCaseLinkService"):
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name.startswith("_generate_") and item.name.endswith("_script"):
                found[item.name] = ast.get_source_segment(src, item) or ""
    if not found:
        return ["link_service_template=<not_found>"]
    return [f"link_service_template:{name}:\n{found[name]}" for name in sorted(found)]

#: 需要页面定位器才能执行断言的类型（本版不执行，仅统计）
LOCATOR_DEPENDENT_KINDS = {"required", "enum", "default", "boundary", "precision",
                           "max_length", "max_count", "file_format", "file_size"}

#: P4 窄口径（rev169，2026-10-21）：**import 期预热 digest**。lru_cache 把重算压到
#: 每进程一次，但「首次调用」仍可能落在编辑窗口内——``inspect.getsource`` 走 linecache，
#: mtime 变了就重读半截文件 ⇒ ``ast.parse`` 抛 ``EOF in multi-line string``（运行期 500，
#: 2026-10-20 实测）。预热后 digest 在进程启动时固化，在途请求永不触发源码重读；
#: 就算启动恰好撞上编辑，也是启动即崩（当场可见），不会变成中途 500。
#:
#: ⚠️ rev170 修正：预热调用**原来放在这里（模块体前部）**——执行时 ``_FORM_EXECUTOR_TS``
#: / ``render_form_executor`` / 多数 digest 函数**尚未定义**，``g.get`` 静默跳过，
#: lru_cache 把**不含执行器模板的残缺 digest** 固化给整个进程（rev169 登记的
#: fec94279864a 就是它，实测 warm≠full）。预热调用已挪到**模块末尾**（全部定义之后），
#: 见文件最后一行。

#: 无需定位器、可做软校验的类型
SOFT_CHECK_KINDS = {"visibility"}

#: ``build_form_plan`` **实际能生成检查**的全部断言种类（单一事实来源）。
#: 注意 ``range`` 不在 LOCATOR_DEPENDENT_KINDS 里（后者只用于"需定位器断言"的统计口径），
#: 但表单计划明确支持它（见下面的 ``elif kind in {"boundary", "range", ...}`` 分支）——
#: 断言校验/回写接口必须以本集合为准，否则会把合法的 range 断言误判为不支持。
FORM_CHECK_KINDS = frozenset(LOCATOR_DEPENDENT_KINDS | SOFT_CHECK_KINDS | {"range"})

# ==========================================================================
# P1-3 阶段 B：**规则级负路径场景**（``extra_config.negative_scenarios``）
# ==========================================================================
# 背景（实测 DEFECT-001）：现有负路径层（submitQueue）的入队条件**全部是"单个字段
# 的值非法"**（空必填 / 非法探针值）。而"编辑时追加一个已被其他活动占用的商品 →
# 期望服务端拒绝跨记录唯一性冲突"这类断言，违规主体是**字段值与外部状态的冲突**，
# 需要先构造前置条件（多步交互），现有框架完全没有入口 —— 因此这类缺陷
# "自动执行必然发现不了"（TR-160/TR-161 只能靠工作区手写 demo2_script.ts 完成）。
#
# 本节把那条手写链路**模板化**：声明式场景 → 渲染进执行器 → 复用既有的
# 点击提交 / 网络响应捕获 / 写隔离原语执行。
#
# ★ 实现口径差异（与设计文档 §2.2 的一处偏离，此处如实记录）：
#   设计文档写的是"把 NegativeScenario 展开为 ``submitQueue`` 里 ``kind: "rule_reject"``
#   的条目"。实现改为**独立阶段**（``runNegativeScenarios``），理由：
#   现有那个 for 循环的每一步都围绕 ``check.field``（复位该字段、定位它的输入框、
#   兜底填充时跳过它）；规则级场景**没有单一字段**，硬塞进去需要在循环内加约十处
#   ``if (item.kind === 'rule_reject')`` 分叉，很容易破坏已覆盖 149 个用例的既有路径。
#   阶段 B 复用它的**原语**（``clickSubmit`` / ``netLog`` / ``isolationDecision``），
#   但不复用它的循环体。功能等价，风险更低。

#: setup 允许的**动作原语闭集**。未在此集合中的动作一律**拒绝该场景**——
#: 静默忽略未知动作会变成"配置了但从未执行"，比直接拒绝更危险。
#:
#: ``upload``（2026-10-16 新增，rev114）：把内联夹具写进 ``input[type=file]``。
#: 背景：部分业务的**必填项本身就是附件**（实测美心 YL 店铺主数据 Store Image），
#: 没有这个原语时该场景在正式通道下**永远不可能构造出前置条件**（只能记 unverified）。
#: 它是**唯一**会主动产生 multipart 请求的动作 ⇒ 必须与 ``allow_upload`` 上传放行规则
#: **成对使用**：声明了 upload 步骤却不声明放行规则时，写隔离会把上传 abort，
#: 场景如实记为 unverified（不会静默通过）。
SETUP_ACTIONS: frozenset[str] = frozenset({"click", "select_row", "append", "fill", "wait", "select_option", "click_selector", "clear_select", "upload", "type"})

#: 允许的目标入口（仅作审计标注与文案，平台不据此猜步骤）。
#: 唯一的**行为差异**：``create`` 表示"就用平台已经打开的那个新建表单"——
#: 因此执行器不会在进入场景前把它关掉（``edit`` / ``add`` / ``custom`` 会关掉，
#: 因为它们的 setup 通常要从列表页自己的入口重新进）。
NEGATIVE_TARGET_ENTRIES: frozenset[str] = frozenset({"edit", "create", "add", "custom"})


@dataclass(frozen=True)
class SetupStep:
    """构造前置条件的一步（**业务无关**的动作原语）。"""

    action: str
    labels: tuple[str, ...] = ()
    selector: str = ""
    row_index: int = 0
    field: str = ""
    value: str = ""
    ms: int = 0
    #: ``upload`` 专用：内联夹具 ``{name, mime, b64}``。
    #: 字节**全部来自配置**，平台不内置任何业务素材（也不去读本地路径——
    #: 本地路径在其它机器上不存在，等于把"配了"变成"跑不了"）。
    fixture: dict[str, Any] = dc_field(default_factory=dict)
    #: ``upload`` 专用：先点哪个按钮才会渲染 ``input[type=file]``（如「Upload Image」）。
    open_labels: tuple[str, ...] = ()
    #: ``upload`` 专用：选完文件后要点哪个弹窗按钮确认（如「OK」/「Confirm」）。
    confirm_labels: tuple[str, ...] = ()
    #: ``type`` 专用（rev115）：``clear`` 非空 = 先三击全选已有文本（键入即整体替换，
    #  否则 keyboard.type 追加导致查询串翻倍，TR-471）；``mode="set"`` = 程序化赋值
    #  （Playwright fill 语义且**不 blur**，Google autocomplete 对 CJK 逐键输入不吐
    #  候选、对程序化 fill 正常吐，TR-473 A/B 实测）。
    clear: str = ""
    mode: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"action": self.action, "labels": list(self.labels),
                               "selector": self.selector, "row_index": self.row_index,
                               "field": self.field, "value": self.value, "ms": self.ms}
        # upload 相关字段**仅在非空时**输出：其它动作的 __dict__ 保持与历史逐字一致
        # （既有快照/单测因此零回归），而执行器对缺键一律按"没声明"处理。
        if self.fixture:
            out["fixture"] = dict(self.fixture)
        if self.open_labels:
            out["open_labels"] = list(self.open_labels)
        if self.confirm_labels:
            out["confirm_labels"] = list(self.confirm_labels)
        if self.clear:
            out["clear"] = self.clear
        if self.mode:
            out["mode"] = self.mode
        return out


@dataclass(frozen=True)
class WriteAllow:
    """写隔离的**放行规则**（收窄到单接口）。

    ★ 为什么不能从 ``api_path_hint`` 直接推出来（实测 DEFECT-001）：

    | 用途 | 匹配语义 | DEFECT-001 的真实值 |
    |---|---|---|
    | ``api_path_hint``（响应匹配） | URL **子串** | ``/v1/purchaseLimit/modify`` |
    | 写隔离放行 | host **后缀** + path **前缀** | host ``imarketing-uat.xysjg.com``、path ``/yundt-saas-gateway/api/mini-mgmt-app/v1/purchaseLimit/modify`` |

    API 请求打到的是**网关主机**（不是当前页面 host），且路径带网关前缀，
    因此子串 `/v1/purchaseLimit/modify` 既不是 host、也不是路径前缀。
    早先把 hint 直接当成路径前缀写进放行规则，会让真实提交**永远被阻断**；
    这里改成显式声明，缺省值只对"同源直连、路径等于 hint"的简单情形成立。

    缺省值（都由计划期填好，执行器不再做推断）：
    - ``host_suffix``：当前页面 host；
    - ``path_prefix``：``api_path_hint``；
    - ``methods``：``POST / PUT / PATCH``。
    """

    host_suffix: str = ""
    path_prefix: str = ""
    methods: tuple[str, ...] = ("POST", "PUT", "PATCH")

    def to_dict(self) -> dict[str, Any]:
        return {"host_suffix": self.host_suffix, "path_prefix": self.path_prefix,
                "methods": list(self.methods)}


@dataclass(frozen=True)
class RejectExpect:
    """期望服务端拒绝的判定条件。

    ★ ``api_path_hint`` **必填**：规则级场景必须真实提交，而写隔离策略默认阻断
    所有非 GET 写请求。拿不到目标接口就无法匹配服务端响应、也无法把放行白名单
    收窄到单接口 —— 与其产出一个"配了但永远 unverified"的场景，
    不如在计划期就拒绝并说明原因（fail-loud）。
    """

    api_path_hint: str
    result_code_not: str = "0"
    message_contains: tuple[str, ...] = ()
    #: 期望服务端以何种**方式**拒绝。``True``（默认）= HTTP 2xx + 业务信封
    #: ``resultCode``≠``result_code_not``（业务层拒绝）；``False`` = 直接以 HTTP 非 2xx
    #: （如 400/403）拒绝。**运行期读取**（见 ``_FORM_EXECUTOR_TS`` 的负路径判定）。
    #: 注：HTTP 5xx 一律判 blocked（服务端崩溃），**不受本字段影响**。
    #: 历史：2026-10-13 前本字段只序列化、运行期从不读取（死配置），5xx 因此被误判成 pass。
    http_ok: bool = True
    allow: WriteAllow = WriteAllow()
    #: 上传（multipart）放行规则，语义同 :class:`AcceptExpect.allow_upload`（rev114）。
    allow_upload: tuple[WriteAllow, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"api_path_hint": self.api_path_hint,
                               "result_code_not": self.result_code_not,
                               "message_contains": list(self.message_contains),
                               "http_ok": self.http_ok,
                               "allow": self.allow.to_dict()}
        if self.allow_upload:
            out["allow_upload"] = [u.to_dict() for u in self.allow_upload]
        return out


@dataclass(frozen=True)
class AcceptExpect:
    """期望服务端**受理**的判定条件（正路径业务规则场景，rev99）。

    与 :class:`RejectExpect` **互斥**：一个场景要么「期望拒绝」要么「期望受理」
    （计划期强制，配置同时给两个直接拒绝）。动机（67197 正向对照）：负路径通道
    原本只有拒绝极性，单收件人发送**应成功**的规则会被判成「受理了应被拒绝的请求」
    ——业务规则两种极性都真实存在，通道必须是双向的。
    判定口径：HTTP 5xx ⇒ blocked（疑似服务端缺陷，与拒绝极性同判，不受本配置影响）；
    HTTP 2xx/3xx 且业务信封 ``resultCode == result_code`` ⇒ pass；
    其余（业务拒绝 / HTTP 4xx 拒绝）⇒ blocked「服务端拒绝了应受理的请求」。
    """

    api_path_hint: str
    #: 期望的**成功**业务码（信封 resultCode 等于该值即视为按期望受理）。
    result_code: str = "0"
    allow: WriteAllow = WriteAllow()
    #: **上传（multipart）放行规则**，2026-10-16 rev114 新增（用户确认）。
    #: 缺省空 ⇒ multipart 一律阻断（与历史行为逐字一致）。
    #: 仅当场景**真的需要上传附件**（如必填项就是图片）时显式声明，且必须用
    #: host + path_prefix + methods 三项收窄到目标上传域，不得写成通配。
    allow_upload: tuple[WriteAllow, ...] = ()
    #: 附加写放行规则（2026-10-17 rev115 新增，与 rev114 allow_upload 同款显式声明模式）。
    #: 动机（TR-476 实测）：Select Address 弹窗的 Google Maps JS 会向
    #: ``maps.googleapis.com`` 发内部 RPC（GetViewportInfo，POST），不在 allow
    #: （业务写接口）也不在 allow_upload（multipart）内 ⇒ 被写隔离 abort，
    #: 地图内部服务初始化失败 ⇒ autocomplete 永远不出候选。此类**非业务写**的
    #: 第三方依赖必须由场景显式声明（host + path_prefix + methods 三项收窄），
    #: 不得默认放行。
    allow_extra: tuple[WriteAllow, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"api_path_hint": self.api_path_hint,
                               "result_code": self.result_code,
                               "allow": self.allow.to_dict()}
        if self.allow_upload:
            out["allow_upload"] = [u.to_dict() for u in self.allow_upload]
        if self.allow_extra:
            out["allow_extra"] = [u.to_dict() for u in self.allow_extra]
        return out


@dataclass(frozen=True)
class NegativeScenario:
    """一个业务规则级负路径场景（声明式）。"""

    scenario_id: str
    rule_ref: str = ""
    target_entry: str = "custom"
    setup: tuple[SetupStep, ...] = ()
    submit_label: tuple[str, ...] = ()
    expect_reject: RejectExpect = RejectExpect(api_path_hint="")
    expect_accept: AcceptExpect = AcceptExpect(api_path_hint="")
    case_id: str = ""
    rule_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "rule_ref": self.rule_ref,
            "target_entry": self.target_entry,
            "setup": [s.to_dict() for s in self.setup],
            "submit_label": list(self.submit_label),
            "expect_reject": self.expect_reject.to_dict(),
            # rev99 正路径极性：与 expect_reject 互斥（计划期强制），运行期按
            # expect_accept.api_path_hint 是否非空选择判定分支。
            "expect_accept": self.expect_accept.to_dict(),
            # 归属：场景把结论记到哪条用例/需求规则上。留空则只输出证据、不归属，
            # 由台账按 `source=negative_scenario` 单独归类（不冒充"用例已执行"）。
            "case_id": self.case_id,
            "rule_id": self.rule_id,
            "source": "negative_scenario",
        }


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v or "").strip()]
    return []


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_allow_upload(raw: Any) -> tuple[tuple[WriteAllow, ...], str]:
    """解析场景的 ``allow_upload``（**multipart 放行规则**，2026-10-16 rev114）。

    返回 ``(rules, error)``；``error`` 非空 ⇒ 调用方**拒绝该场景**。
    为什么是"拒绝"而不是"警告后照跑"：放行上传是**安全边界**，写宽了就等于
    把"未知上传副作用"放进来（历史口径：宁可再生环境失败，也不产生未知写副作用）。
    因此这里把三个收窄条件全部硬校验：
    - ``host_suffix`` 必填（不允许不限定主机）；
    - ``path_prefix`` 必须绝对路径前缀（匹配用的是 ``path.startsWith``）；
    - ``methods`` 缺省收敛为 ``POST``（上传不会是 GET/PUT）。
    """
    if raw in (None, ""):
        return (), ""
    if raw == []:
        return (), ""
    if not isinstance(raw, list):
        return (), f"allow_upload 必须是数组（收到 {type(raw).__name__}）"
    rules: list[WriteAllow] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            return (), f"allow_upload[{index}] 必须是对象（收到 {type(item).__name__}）"
        host = str(item.get("host_suffix") or item.get("host") or "").strip()
        if not host:
            return (), f"allow_upload[{index}] 缺少 host_suffix（上传放行不允许不限定主机）"
        if len(host.split(".")) < 2:
            return (), (f"allow_upload[{index}].host_suffix={host!r} 过宽"
                        "（至少形如 example.com）")
        prefix = str(item.get("path_prefix") or "").strip()
        if not prefix.startswith("/"):
            return (), (f"allow_upload[{index}].path_prefix 必须是绝对路径前缀"
                        f"（收到 {prefix!r}；写隔离按 path.startsWith 匹配）")
        methods = tuple(m.upper() for m in _as_str_list(item.get("methods"))) or ("POST",)
        rules.append(WriteAllow(host_suffix=host, path_prefix=prefix, methods=methods))
    return tuple(rules), ""


def parse_negative_scenarios(raw: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """解析 ``extra_config.negative_scenarios``，返回 ``(可用场景, 被拒场景)``。

    被拒场景**不静默丢弃**：每条带 ``scenario_id`` 与具体 ``reason``，由执行器
    以 ``[plan-skip]`` 同款方式输出，调用方也能写进资产审计。理由与
    ``_suggest_fields`` 一致 —— "配了但没跑"若不可见，排查成本远高于当场报错。

    非法配置**不阻断编译**：最外层 try/except 保证任何畸形输入都只产生一条拒绝记录。
    """
    if raw is None or raw == "":
        return [], []
    if not isinstance(raw, list):
        return [], [{"scenario_id": "", "reason": "negative_scenarios 必须是数组",
                     "detail": type(raw).__name__}]

    scenarios: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        sid = ""
        try:
            if not isinstance(entry, dict):
                rejects.append({"scenario_id": "", "reason": "场景必须是对象",
                                "detail": f"index={index}"})
                continue
            sid = str(entry.get("scenario_id") or "").strip()
            if not sid:
                rejects.append({"scenario_id": "", "reason": "缺少 scenario_id",
                                "detail": f"index={index}"})
                continue
            if sid in seen:
                # 重复 id 会让断言归属歧义（同一 id 出现两次时不知道该记哪条）
                rejects.append({"scenario_id": sid, "reason": "scenario_id 重复",
                                "detail": f"index={index}"})
                continue

            target_entry = str(entry.get("target_entry") or "custom").strip().lower()
            if target_entry not in NEGATIVE_TARGET_ENTRIES:
                rejects.append({
                    "scenario_id": sid, "reason": "target_entry 非法",
                    "detail": f"{target_entry!r}，允许：{sorted(NEGATIVE_TARGET_ENTRIES)}",
                })
                continue

            raw_setup = entry.get("setup") or []
            if not isinstance(raw_setup, list):
                rejects.append({"scenario_id": sid, "reason": "setup 必须是数组",
                                "detail": type(raw_setup).__name__})
                continue
            steps: list[SetupStep] = []
            bad_step = ""
            for step in raw_setup:
                if not isinstance(step, dict):
                    bad_step = "步骤必须是对象"
                    break
                action = str(step.get("action") or "").strip().lower()
                if action not in SETUP_ACTIONS:
                    bad_step = f"action {action!r} 不在 {sorted(SETUP_ACTIONS)}"
                    break
                step_fixture: dict[str, Any] = {}
                if action == "upload":
                    # 上传是**产生外部副作用**的动作：宁可在计划期拒绝，也不允许
                    # "配了 upload 但没有夹具"落到执行期变成一次必然失败的 no-op。
                    raw_fix = step.get("fixture")
                    if not isinstance(raw_fix, dict) \
                            or not str(raw_fix.get("b64") or "").strip():
                        bad_step = ("upload 步骤缺少 fixture.b64（内联夹具的 base64 字节）"
                                    "；平台不读本地路径——路径在别的机器上不存在")
                        break
                    step_fixture = {
                        "name": str(raw_fix.get("name") or "upload.bin"),
                        "mime": str(raw_fix.get("mime") or "application/octet-stream"),
                        "b64": str(raw_fix.get("b64") or "").strip(),
                    }
                steps.append(SetupStep(
                    action=action,
                    labels=tuple(_as_str_list(step.get("labels"))),
                    selector=str(step.get("selector") or ""),
                    row_index=_as_int(step.get("row_index"), 0),
                    field=str(step.get("field") or ""),
                    value=str(step.get("value") or ""),
                    ms=_as_int(step.get("ms"), 0),
                    fixture=step_fixture,
                    open_labels=tuple(_as_str_list(step.get("open_labels"))),
                    confirm_labels=tuple(_as_str_list(step.get("confirm_labels"))),
                    clear=str(step.get("clear") or ""),
                    mode=str(step.get("mode") or ""),
                ))
            if bad_step:
                rejects.append({"scenario_id": sid, "reason": "setup 步骤非法",
                                "detail": bad_step})
                continue

            raw_accept = entry.get("expect_accept")
            raw_expect = entry.get("expect_reject") or {}
            if isinstance(raw_accept, dict) and raw_accept:
                # rev99 正路径极性：与 expect_reject 互斥（两个都配 = 语义矛盾，宁可拒绝）
                if raw_expect:
                    rejects.append({"scenario_id": sid,
                                    "reason": "expect_accept 与 expect_reject 互斥",
                                    "detail": "一个场景只能声明一种极性（受理或拒绝）"})
                    continue
                hint = str(raw_accept.get("api_path_hint") or "").strip()
                if not hint:
                    rejects.append({"scenario_id": sid,
                                    "reason": "缺少 expect_accept.api_path_hint",
                                    "detail": "同 RejectExpect：匹配服务端响应 + 收窄写隔离白名单"})
                    continue
                raw_allow = raw_accept.get("allow") or {}
                if not isinstance(raw_allow, dict):
                    rejects.append({"scenario_id": sid,
                                    "reason": "expect_accept.allow 必须是对象",
                                    "detail": type(raw_allow).__name__})
                    continue
                path_prefix = str(raw_allow.get("path_prefix") or hint).strip()
                if not path_prefix.startswith("/"):
                    rejects.append({
                        "scenario_id": sid, "reason": "allow.path_prefix 必须是绝对路径前缀",
                        "detail": f"{path_prefix!r}（写隔离按 path.startsWith 匹配）",
                    })
                    continue
                methods = tuple(m.upper() for m in _as_str_list(raw_allow.get("methods"))) \
                    or ("POST", "PUT", "PATCH")
                upload_rules, upload_err = _parse_allow_upload(raw_accept.get("allow_upload"))
                if upload_err:
                    rejects.append({
                        "scenario_id": sid, "reason": "expect_accept.allow_upload 非法",
                        "detail": upload_err,
                    })
                    continue
                extra_rules, extra_err = _parse_allow_upload(raw_accept.get("allow_extra"))
                if extra_err:
                    rejects.append({
                        "scenario_id": sid, "reason": "expect_accept.allow_extra 非法",
                        "detail": extra_err,
                    })
                    continue
                scenarios.append(NegativeScenario(
                    scenario_id=sid,
                    rule_ref=str(entry.get("rule_ref") or ""),
                    target_entry=target_entry,
                    setup=tuple(steps),
                    submit_label=tuple(_as_str_list(entry.get("submit_label"))),
                    expect_reject=RejectExpect(api_path_hint=""),
                    expect_accept=AcceptExpect(
                        api_path_hint=hint,
                        result_code=str(raw_accept.get("result_code", "0")),
                        allow=WriteAllow(
                            host_suffix=str(raw_allow.get("host_suffix") or "").strip(),
                            path_prefix=path_prefix,
                            methods=methods,
                        ),
                        allow_upload=upload_rules,
                        allow_extra=extra_rules,
                    ),
                    case_id=str(entry.get("case_id") or ""),
                    rule_id=str(entry.get("rule_id") or ""),
                ).to_dict())
                seen.add(sid)
                continue
            if not isinstance(raw_expect, dict):
                rejects.append({"scenario_id": sid, "reason": "expect_reject 必须是对象",
                                "detail": type(raw_expect).__name__})
                continue
            hint = str(raw_expect.get("api_path_hint") or "").strip()
            if not hint:
                # 没有接口标识 → 无法匹配服务端响应、也无法收窄放行白名单
                #（见 RejectExpect docstring）
                rejects.append({
                    "scenario_id": sid, "reason": "缺少 expect_reject.api_path_hint",
                    "detail": "规则级场景必须真实提交；需要它匹配服务端响应、并把写隔离白名单"
                              "收窄到单接口，否则提交会被阻断（宁可在计划期拒绝，也"
                              "不产出永远 unverified 的场景；正路径场景请改用 expect_accept）",
                })
                continue
            code_not = str(raw_expect.get("result_code_not", "0"))
            msgs = tuple(_as_str_list(raw_expect.get("message_contains")))
            if not code_not.strip() and not msgs:
                # 两个判据都空 → "期望拒绝"无从判定；此时任何结果都能被解释成"拒绝"，属臆造
                rejects.append({
                    "scenario_id": sid, "reason": "expect_reject 无有效判据",
                    "detail": "result_code_not 为空且 message_contains 为空（至少要有一个）",
                })
                continue
            raw_allow = raw_expect.get("allow") or {}
            if not isinstance(raw_allow, dict):
                rejects.append({"scenario_id": sid, "reason": "expect_reject.allow 必须是对象",
                                "detail": type(raw_allow).__name__})
                continue
            path_prefix = str(raw_allow.get("path_prefix") or hint).strip()
            if not path_prefix.startswith("/"):
                # 放行规则用的是 path **startsWith**，不写绝对路径必然永远匹配不上
                rejects.append({
                    "scenario_id": sid, "reason": "allow.path_prefix 必须是绝对路径前缀",
                    "detail": f"{path_prefix!r}（写隔离按 path.startsWith 匹配）",
                })
                continue
            methods = tuple(m.upper() for m in _as_str_list(raw_allow.get("methods"))) \
                or ("POST", "PUT", "PATCH")
            upload_rules, upload_err = _parse_allow_upload(raw_expect.get("allow_upload"))
            if upload_err:
                rejects.append({
                    "scenario_id": sid, "reason": "expect_reject.allow_upload 非法",
                    "detail": upload_err,
                })
                continue

            scenarios.append(NegativeScenario(
                scenario_id=sid,
                rule_ref=str(entry.get("rule_ref") or ""),
                target_entry=target_entry,
                setup=tuple(steps),
                submit_label=tuple(_as_str_list(entry.get("submit_label"))),
                expect_reject=RejectExpect(
                    api_path_hint=hint, result_code_not=code_not,
                    message_contains=msgs,
                    http_ok=bool(raw_expect.get("http_ok", True)),
                    allow=WriteAllow(
                        host_suffix=str(raw_allow.get("host_suffix") or "").strip(),
                        path_prefix=path_prefix,
                        methods=methods,
                    ),
                    allow_upload=upload_rules,
                ),
                case_id=str(entry.get("case_id") or ""),
                rule_id=str(entry.get("rule_id") or ""),
            ).to_dict())
            seen.add(sid)
        except Exception as exc:  # noqa: BLE001  配置畸形不得阻断编译
            rejects.append({"scenario_id": sid, "reason": "场景解析异常",
                            "detail": f"{type(exc).__name__}: {exc}"[:200]})
    return scenarios, rejects


# ==========================================================================
# 运行期唯一化（A2 / RUN_TAG，2026-10-20）
# ==========================================================================
#: 配置侧占位符：任何 ``setup`` 步骤 ``value`` 里出现它，**运行期**替换成本次运行的
#: 唯一标记（``MMDDHHMMSS`` + 3 位随机，共 13 字符）。
#:
#: 为什么必须是「运行期」而不是「编译期」：``FORM_PLAN`` 是写进产物的**快照**，
#: 编译期定死等于每次跑都用同一个编号——那正是要修的病（二次提交被服务端按
#: 唯一约束拒绝 ``20003 编号已存在``，而期望受理的通道会把它判成「疑似缺陷坐实」，
#: 属**假缺陷**）。占位符原样留在快照里，执行器每次运行自己算新值。
RUN_TAG_TOKEN = "{{RUN_TAG}}"

#: 「疑似业务唯一编码」的固定值：含 ≥4 位连续数字**且**至少 2 个字母。
#: **只用于诊断**——列出"这个场景的数据没唯一化"，绝不自动改写（猜错字段比报出来更糟）。
#: 为什么要求字母：TR-733 实测，店鋪電話 ``12345678`` 被纯数字规则命中，
#: 于是每次运行都报一条 RUN_TAG_GAP —— 噪声会让人把这条诊断整体忽略，
#: 而它恰恰是"重复运行必然 20003"的唯一提示渠道。
_FIXED_CODE_DIGITS_RE = re.compile(r"\d{4,}")
_FIXED_CODE_LETTERS_RE = re.compile(r"[A-Za-z]{2,}")


def run_tag_usage(scenarios: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """统计计划里有多少 ``setup`` 步骤用到 ``{{RUN_TAG}}``。

    回执口径：``enabled`` 真 = 本次产物里至少一处值会在运行时唯一化；
    ``fields`` 供运行期 ``RUN_TAG`` 行自证"改了哪几个字段"，避免只看到
    "用了 tag"却不知道该去核对哪个提交字段。
    """
    fields: list[str] = []
    steps = 0
    for sc in scenarios or []:
        for step in (sc.get("setup") or []):
            value = str((step or {}).get("value") or "")
            if RUN_TAG_TOKEN not in value:
                continue
            steps += 1
            name = str((step or {}).get("field") or "")
            if name and name not in fields:
                fields.append(name)
    return {"enabled": bool(steps), "fields": fields, "steps": steps}


def run_tag_gaps(scenarios: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    """列出**真实落库**场景里"值看着是固定编码、却没写占位符"的字段。

    判据保守：只有 ``expect_accept``（正路径 = 会真的写库）+ 值里同时有 ≥4 位连续数字
    与 ≥2 个字母才入选（纯数字如店鋪電話 ``12345678`` 是业务值，不是唯一编码）。
    命中不代表一定冲突，只代表**不可重复运行**——这正是历史缺陷
    （二次跑 ``20003``）的成因，必须能在编译回执与运行日志里看见。
    """
    out: list[dict[str, str]] = []
    for sc in scenarios or []:
        if not ((sc.get("expect_accept") or {}).get("api_path_hint")):
            continue
        for step in (sc.get("setup") or []):
            step = step or {}
            if str(step.get("action") or "") not in {"fill", "type"}:
                continue
            value = str(step.get("value") or "")
            if not value or RUN_TAG_TOKEN in value:
                continue
            if not _FIXED_CODE_DIGITS_RE.search(value):
                continue
            if not _FIXED_CODE_LETTERS_RE.search(value):
                continue
            out.append({
                "scenario_id": str(sc.get("scenario_id") or ""),
                "field": str(step.get("field") or ""),
                "value": value[:60],
                "hint": f"固定值疑似唯一编码：重复运行会被服务端唯一约束拒绝；"
                        f"改为前缀 + {RUN_TAG_TOKEN}",
            })
    return out[:12]


# ==========================================================================
# 「列表域」字段与列契约（N3，2026-10-18）
# ==========================================================================
# 背景：文档里存在一类"字段名"，它其实不是表单控件，而是**列表区整体期望**：
#   列表顯示欄位（列清单）/ 列表排序 / 列表搜索 / 筛选 / 分页。
# 现状（真实库取证，2026-10-18）：
#   · 这类用例在库里**断言数为 0**（72 条「列表/筛选/分页」人工用例 72/72 无断言，
#     12 条"列表零检查"资产的用例同样 assertions=0，只有 requirement_rule.rule 里的列名文字）；
#   · 因此给它们配 `fields` 定位器**一条都救不回来**——缺的不是定位器，是"规则体 → 列表检查"
#     的生成器。历史 skip 原因统一写"无定位器"，把人引向错误的修法（配定位器）。
# 这里做两件事（都不改变执行能力，只让口径正确）：
#   1) 把列表域字段单独识别出来，skip 原因与 `scope` 如实标注；
#   2) 定义列契约 `field_locators.columns`，并兼容 CS 侧的旧键（`columns_22` /
#      `requirement_fields_16`），供**将来**的列表检查生成器与预览接口共用一份解析。
#      ★ 本期**没有**列表检查执行器：`columns` 只用于诊断与配置校对，
#      不会凭空产生检查（避免"配了就以为已经验了"）。
#: 列表域字段的**前缀**：这类名字一定是"整块列表"的期望（列清单/分页）。
_LIST_SCOPE_PREFIXES: tuple[str, ...] = ("列表", "分頁", "分页")
#: 列表域字段的**整名**：筛选/排序/搜索本身就是动作，不会再带控件后缀。
#: 单独列整名而不是做包含匹配，是因为真实配置里存在表单控件
#: ``地圖搜索框`` / ``筛选值`` / ``搜索關鍵詞``（148 个子功能的定位器实测），
#: 包含匹配会把它们误判成"列表域"，从而把"缺定位器"错报成"缺生成器"。
_LIST_SCOPE_EXACT: frozenset[str] = frozenset({
    "篩選", "筛选", "篩選區", "筛选区", "篩選條件", "筛选条件",
    "排序", "搜索", "搜尋", "搜索區", "排序規則",
})


def is_list_scope_field(name: Any) -> bool:
    """字段名是否属于"列表域"（列清单/筛选/分页），而非表单控件。

    判据刻意收紧（前缀 + 整名），不做关键词包含：
    ``筛选值`` / ``搜索關鍵詞`` / ``地圖搜索框`` 都是**表单控件**，
    把它们归到列表域会掩盖真正的"缺定位器"。
    """
    text = str(name or "").strip()
    if not text:
        return False
    if text in _LIST_SCOPE_EXACT:
        return True
    return text.startswith(_LIST_SCOPE_PREFIXES)


def parse_columns(locators: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """把列配置归一成一份**契约**，返回 ``(契约|None, 被拒条目)``。

    契约形状（将来列表检查生成器的唯一输入）::

        {"table_scope": ".ant-table-thead th",
         "items": [{"key": "工单编号", "header_text": "Case No.", "aliases": ["工单编号"]}],
         "source": "explicit" | "legacy_cs", "notes": [...], "column_count": 22}

    支持的输入（按优先级）：
      1. ``columns``：显式契约。``{"table_scope": str, "items": [...]}``；
         ``items`` 里的条目可以是字符串（等价于只声明 header_text）。
      2. 旧 CS 键：``columns_22``（表头文本数组）+ ``requirement_fields_16``
         （中文名 → 英文表头）。二者合并成 key=中文名、header_text=英文表头；
         只有表头没有中文名时 key 取表头本身。
      3. ``sortable_columns``：是**文字说明**（"实测 22 列表头全部 sorter=true"），
         解析不出列，原样记进 ``notes``，不冒充列。

    畸形输入只产生 rejects、不抛异常（配置错误不得阻断编译）。
    """
    loc = locators if isinstance(locators, dict) else {}
    rejects: list[dict[str, Any]] = []
    notes: list[str] = []

    # 旧 CS 的列键挂在 ``custom_config`` 顶层，**不在** ``field_locators`` 里
    # （真实库取证：columns_22/requirement_fields_16/sortable_columns 只出现在
    #  WSF-48576974 的 custom_config 顶层）。调用方用 ``columns_legacy`` 把它们带进来，
    # 这样编译链与预览能读到同一份配置，而**不需要**改库、也不改变配置形状。
    legacy = loc.get("columns_legacy") if isinstance(loc.get("columns_legacy"), dict) else {}

    def _pick(key: str) -> Any:
        if key in loc:
            return loc[key]
        return legacy.get(key)

    def _norm_item(entry: Any, *, key_hint: str = "") -> dict[str, Any] | None:
        if isinstance(entry, str):
            text = entry.strip()
            if not text:
                return None
            return {"key": key_hint or text, "header_text": text,
                    "aliases": [key_hint] if key_hint and key_hint != text else []}
        if not isinstance(entry, dict):
            rejects.append({"reason": "列条目必须是字符串或对象",
                            "detail": f"got {type(entry).__name__}"})
            return None
        header = str(entry.get("header_text") or entry.get("header") or entry.get("text") or "").strip()
        key = str(entry.get("key") or entry.get("field") or key_hint or header).strip()
        if not header and not key:
            rejects.append({"reason": "列条目缺少 header_text/key", "detail": ""})
            return None
        aliases = _as_str_list(entry.get("aliases"))
        if key and key != header:
            aliases = aliases + [key]
        return {"key": key or header, "header_text": header or key,
                "aliases": list(dict.fromkeys(a for a in aliases if a))}

    items: list[dict[str, Any]] = []
    source = ""
    #: 旧 CS 的表头数组键名带列数后缀（``columns_22``），后缀是**唯一线索**，
    #: 不能写死——同一个项目里不同子功能的列数不同。
    legacy_columns_key = next(
        (str(k) for k in list(loc) + list(legacy) if re.match(r"^columns_\d+$", str(k))),
        "",
    )
    raw = loc.get("columns")
    if isinstance(raw, dict) or isinstance(raw, list):
        source = "explicit"
        table_scope = ""
        entries: Any = raw
        if isinstance(raw, dict):
            table_scope = str(raw.get("table_scope") or raw.get("scope") or "").strip()
            entries = raw.get("items") or raw.get("columns") or []
        if not isinstance(entries, list):
            rejects.append({"reason": "columns.items 必须是数组",
                            "detail": type(entries).__name__})
            entries = []
        for entry in entries:
            item = _norm_item(entry)
            if item:
                items.append(item)
    elif legacy_columns_key or isinstance(_pick("requirement_fields_16"), dict):
        # CS 侧旧键：表头数组 + 中文名映射（键名带列数后缀，属一次性命名，不能当 schema 用）
        source = "legacy_cs"
        cn_to_header = _pick("requirement_fields_16")
        cn_to_header = cn_to_header if isinstance(cn_to_header, dict) else {}
        headers = [h for h in _as_str_list(_pick(legacy_columns_key) if legacy_columns_key else None)]
        header_to_cn: dict[str, str] = {}
        for cn, header in cn_to_header.items():
            header_text = str(header or "").strip()
            if header_text and header_text not in header_to_cn:
                header_to_cn[header_text] = str(cn or "").strip()
        for header in headers:
            item = _norm_item(header, key_hint=header_to_cn.get(header, ""))
            if item:
                items.append(item)
        # 映射里有、表头数组里没有的列：不静默丢，登记到 notes
        missing = [h for h in header_to_cn if h not in headers]
        if missing:
            notes.append(
                "requirement_fields_16 中有 %d 列不在 %s：%s"
                % (len(missing), legacy_columns_key or "表头数组", "、".join(missing[:8]))
            )
        notes.append(
            f"来源为 CS 一次性键 {legacy_columns_key or 'requirement_fields_16'}"
            "/requirement_fields_16（非 schema），建议迁入 columns"
        )

    sortable = _pick("sortable_columns")
    if isinstance(sortable, str) and sortable.strip():
        notes.append(f"sortable_columns 为文字说明、未解析成列：{sortable.strip()[:80]}")

    if not items:
        return None, rejects

    # 去重：同一 header_text 只保留一次（旧键里中英文可能重复登记同一列）
    deduped: list[dict[str, Any]] = []
    seen_headers: set[str] = set()
    for item in items:
        if item["header_text"] in seen_headers:
            continue
        seen_headers.add(item["header_text"])
        deduped.append(item)

    # 旧键名自带列数（``columns_22``）——它与实际解析出的列数**必须对得上**，
    # 对不上说明配置本身漂移了（真实库实测：WSF-48576974 的 columns_22 实含 23 项，
    # 多出的是"（勾选列）"）。不报出来，后面按 22 列做断言就会错位。
    if source == "legacy_cs" and legacy_columns_key:
        declared = re.match(r"^columns_(\d+)$", legacy_columns_key)
        if declared:
            expected = int(declared.group(1))
            if expected != len(deduped):
                notes.append(
                    f"键名 {legacy_columns_key} 声明 {expected} 列，实际解析出 {len(deduped)} 列"
                    f"（差额 {len(deduped) - expected:+d}），请核对是否含勾选列/表头重复"
                )

    table_scope = ""
    if isinstance(raw, dict):
        table_scope = str(raw.get("table_scope") or raw.get("scope") or "").strip()
    if not table_scope:
        inner = loc.get("locators") if isinstance(loc.get("locators"), dict) else {}
        legacy_inner = legacy.get("locators") if isinstance(legacy.get("locators"), dict) else {}
        table_scope = str(
            inner.get("table_thead") or legacy_inner.get("table_thead")
            or loc.get("table_thead") or legacy.get("table_thead") or ""
        ).strip()

    # 硬门禁开关：只有配置**显式**要求时才因列头不一致判失败。
    # 默认不判失败——配置列数与真实表头尚未对齐（见上面 columns_22 的列数漂移），
    # 一开就判会把配置问题变成整批假失败（同 N3 "缺规则化" 的老坑）。
    # 两处都认：`columns.assert_on_mismatch`（与 items 同级，显式契约的自然位置）
    # 与 `field_locators.assert_on_mismatch`（旧键/顶层开关）。
    _assert_flag = _pick("assert_on_mismatch")
    if _assert_flag is None and isinstance(raw, dict):
        _assert_flag = raw.get("assert_on_mismatch")
    return {
        "table_scope": table_scope,
        "items": deduped,
        "column_count": len(deduped),
        "source": source,
        "notes": notes,
        **({"assert_on_mismatch": True} if _assert_flag is True else {}),
    }, rejects


# ==========================================================================
# 「列表可见」断言（``extra_config.list_checks``，2026-10-16 新增）
# ==========================================================================
# 背景（YL 三模块覆盖评估）：三条「新建-必填全填-真实落库**并在列表可见**」用例，
# 【预期结果】写的是"列表中可查询到"，但正式通道只机检了服务端 resultCode=0 ——
# 列表可见性一直靠人工探针（TR-424/425/426/429）补验，不在回归里。
#
# 本节把「写入成功后到列表页按值检索、确认记录可见」做成**独立阶段**，
# 与 ``negative_scenarios`` 同形（配置驱动、独立阶段、复用既有原语）：
# 写请求发生在负路径阶段，因此本阶段排在它**之后**执行。
#
# ★ 与 negative_scenarios 一致的两条口径：
#   1) 被拒配置**不静默丢弃**（带 ``value`` 与 ``reason``，运行期以 ``[list-skip]`` 输出）；
#   2) 非法配置不阻断编译（最外层 try/except，畸形输入只产生一条拒绝记录）。
LIST_CHECK_EXPECTS: frozenset[str] = frozenset({"found", "absent"})


def parse_row_pick(raw: Any) -> dict[str, Any] | None:
    """解析 ``row_pick``：按「列文本」筛行，取命中行的指定列当 value。

    为什么需要（rev162，2026-10-21）：动作通道会**推进数据状态**
    （``待启用 --启用--> 启用 --禁用--> 禁用（终态）``），写死 ``value`` 的配置
    跑 2–3 次必然找不到目标行 —— TR-773→775 实测：``XY1021011339FCZ`` 走完一轮
    被推到禁用终态后换成 ``XY1021101756PFG``，后者本轮又被启用。把「选哪一行」
    交给运行时按状态列筛，配置才能跨运行复用，不再随数据状态漂移。

    配置形态::

        "row_pick": {"column": "状态", "in": ["待启用", "待啟用"],
                     "value_column": "问卷标题"}

    * ``column``       判断用的列**表头文案**（缺省「状态」）
    * ``in``           期望的列文本（字符串或数组）。先做**精确**匹配，全部无精确
                       命中才退化为包含匹配 —— 否则 ``"启用"`` 会命中 ``"待启用"``
    * ``value_column`` 取哪一列的文本当 value（缺省取该行第一个单元格）
    * ``pick``         目前只支持 ``first``（命中行的第一行）
    """
    if not isinstance(raw, dict):
        return None
    column = str(raw.get("column") or "状态").strip()
    want = _as_str_list(raw.get("in"))
    if not want:
        single = str(raw.get("in") or "").strip()
        if single:
            want = [single]
    if not want:
        return None
    return {
        "column": column,
        "in": want,
        "value_column": str(raw.get("value_column") or "").strip(),
        "pick": "first",
    }


def parse_list_checks(raw: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """解析 ``extra_config.list_checks``，返回 ``(可用检查, 被拒检查)``。

    单条配置字段：
      ``name``                断言名（缺省由 value 生成）
      ``value``               要检索的记录标识（如店鋪編號/商品編碼）；与 ``row_pick`` 二选一
      ``row_pick``            rev162：按列文本筛行、取命中行指定列当 value（见 parse_row_pick）
      ``list_url``            列表页直链（缺省回退 ``locators.list_url``；两者都空 → 拒绝）
      ``search_placeholder``  搜索框 placeholder 候选（数组或字符串；可空 → 退化为翻页扫描）
      ``expect``              ``found``（缺省）/ ``absent``
      ``case_id`` / ``rule_id``  证据归属
      ``max_pages``           翻页上限（缺省 3）
    """
    if raw is None or raw == "":
        return [], []
    if not isinstance(raw, list):
        return [], [{"value": "", "reason": "list_checks 必须是数组",
                     "detail": type(raw).__name__}]

    items: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(raw):
        try:
            if not isinstance(entry, dict):
                rejects.append({"value": "", "reason": "检查必须是对象",
                                "detail": f"index={index}"})
                continue
            value = str(entry.get("value") or "").strip()
            # rev162：``value`` 与 ``row_pick`` 二选一 —— 后者让「选哪一行」由运行期
            # 按状态列决定，避免动作通道推进状态后配置失效（见 parse_row_pick 注释）。
            row_pick = parse_row_pick(entry.get("row_pick"))
            if not value and not row_pick:
                rejects.append({"value": "", "reason": "缺少 value 或 row_pick（要定位的记录）",
                                "detail": f"index={index}"})
                continue
            expect = str(entry.get("expect") or "found").strip().lower()
            if expect not in LIST_CHECK_EXPECTS:
                rejects.append({"value": value, "reason": "expect 非法",
                                "detail": f"{expect!r}，允许：{sorted(LIST_CHECK_EXPECTS)}"})
                continue
            key = (value or f"row_pick:{row_pick['column']}={','.join(row_pick['in'])}", expect)
            if key in seen:
                rejects.append({"value": value, "reason": "同一 value+expect 重复",
                                "detail": f"index={index}"})
                continue
            phs = _as_str_list(entry.get("search_placeholder"))
            # rev148：行内操作 / 查看只读（可选，配置了才生效；畸形结构整体丢弃该子项）
            action_raw = entry.get("action")
            action = None
            # rev156：操作项随行状态变化（启用/禁用互换），允许给**候选标签数组** `labels`；
            # `label` 仍必填（=首选标签），`labels` 只用来在多状态下行间复用同一份配置。
            if isinstance(action_raw, dict) and str(action_raw.get("label") or "").strip():
                _labels = [str(x).strip() for x in (action_raw.get("labels") or [])
                           if str(x or "").strip()]
                action = {
                    "label": str(action_raw["label"]).strip(),
                    "labels": _labels,
                    "menu": str(action_raw.get("menu") or ""),
                    "confirm": str(action_raw.get("confirm") or ""),
                    "post_value": str(action_raw.get("post_value") or ""),
                }
            ro_raw = entry.get("readonly")
            readonly = None
            if isinstance(ro_raw, dict) and ro_raw.get("expect_all_disabled") is not False:
                readonly = {
                    "view_label": str(ro_raw.get("view_label") or "查看|檢視"),
                    "view_menu": str(ro_raw.get("view_menu") or ""),
                }
            elif ro_raw is True:
                readonly = {"view_label": "查看|檢視", "view_menu": ""}
            # 断言名：value 为空（纯 row_pick 模式）时用「列∈[值]」描述，避免出现空名。
            pick_label = (f"{row_pick['column']}∈[{'/'.join(row_pick['in'])}]"
                          if row_pick else "")
            items.append({
                "name": str(entry.get("name") or f"list_visible:{value or pick_label}"),
                "value": value,
                "row_pick": row_pick,
                "pick_label": pick_label,
                "expect": expect,
                "list_url": str(entry.get("list_url") or ""),
                "search_placeholder": phs,
                "max_pages": max(1, _as_int(entry.get("max_pages"), 3)),
                "action": action,
                "readonly": readonly,
                "case_id": str(entry.get("case_id") or ""),
                "rule_id": str(entry.get("rule_id") or ""),
                "rule_ref": str(entry.get("rule_ref") or "列表可见"),
            })
            seen.add(key)
        except Exception as exc:  # noqa: BLE001  配置畸形不得阻断编译
            rejects.append({"value": "", "reason": "检查解析异常",
                            "detail": f"{type(exc).__name__}: {exc}"[:200]})
    return items, rejects


# ==========================================================================
# 「筛选区」独立通道（``extra_config.filter_checks``，rev170 新增）
# ==========================================================================
# 背景（覆盖审计 2026-10-21）：字段级正/逆/边界断言已全自动化（16/16 用例进 TR），
# 但「列表筛选区」连用例都缺（仅模糊查询 1 条文档级）。本通道把「下拉筛选 → 结果集
# 目标列全量命中 → 重置还原」做成独立阶段，与 list_checks/export_checks 同形：
# 配置驱动、独立阶段、被拒不静默（``[filter-skip]`` 运行期可见）。
#
# 诚实边界（写进判定，不冒充）：
#   * 只校验**第 1 页可见行**的目标列取值（翻页筛选不在本版范围，如实 unverified）；
#   * 筛选后 0 行 → unverified（可能合理也可能筛选失效，不判通过也不判缺陷）；
#   * 找不到状态下拉/选项/列 → unverified（配置或环境问题），不冒充业务结论。
def parse_filter_checks(raw: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """解析 ``extra_config.filter_checks``，返回 ``(可用检查, 被拒检查)``。

    单条配置字段：
      ``name``                断言名（缺省 ``filter:<option>@<expect_column>``）
      ``list_url``            列表页直链（缺省回退 ``locators.list_url``；两者都空 → 运行期 unverified）
      ``control_placeholder`` 筛选下拉的 placeholder 候选（数组或字符串；缺省 狀態/状态/Status）
      ``option``              要选中的选项文本（**必填**）
      ``option_labels``       选项文本候选（繁简同配；缺省 ``[option]``）
      ``expect_column``       结果集断言的列**表头文案**（缺省「状态」）
      ``expect_in``           该列允许的取值（缺省 ``[option]``）
      ``reset``               筛选后是否点「重置」并断言控件清空（缺省 False）
      ``reset_label``         重置按钮正则（缺省 ``重\\s*置|重\\s*設|Reset``）
      ``reset_clear_values``  重置后下拉回显的「已清空」等值文本（缺省 全部/All）——
                              本 SUT 重置是把下拉置回「全部」而非清成 placeholder
                              （TR-789 FILTER_TRACE 实测 value="全部"）
      ``case_id`` / ``rule_id``  证据归属
    """
    if raw is None or raw == "":
        return [], []
    if not isinstance(raw, list):
        return [], [{"value": "", "reason": "filter_checks 必须是数组",
                     "detail": type(raw).__name__}]
    items: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        try:
            if not isinstance(entry, dict):
                rejects.append({"value": "", "reason": "筛选检查必须是对象",
                                "detail": f"index={index}"})
                continue
            option = str(entry.get("option") or "").strip()
            if not option:
                rejects.append({"value": "", "reason": "缺少 option（要选中的筛选值）",
                                "detail": f"index={index}"})
                continue
            option_labels = [s for s in _as_str_list(entry.get("option_labels")) if s]
            if not option_labels:
                option_labels = [option]
            expect_in = [s for s in _as_str_list(entry.get("expect_in")) if s]
            if not expect_in:
                expect_in = [option]
            items.append({
                "name": str(entry.get("name") or
                            f"filter:{option}@{str(entry.get('expect_column') or '状态').strip()}"),
                "list_url": str(entry.get("list_url") or ""),
                "control_placeholder": (_as_str_list(entry.get("control_placeholder"))
                                        or ["狀態", "状态", "Status"]),
                "option": option,
                "option_labels": option_labels,
                "expect_column": str(entry.get("expect_column") or "状态").strip(),
                "expect_in": expect_in,
                "reset": bool(entry.get("reset")),
                "reset_label": str(entry.get("reset_label") or r"重\s*置|重\s*設|Reset"),
                "reset_clear_values": ([s for s in _as_str_list(entry.get("reset_clear_values")) if s]
                                       or ["全部", "全部", "All"]),
                "case_id": str(entry.get("case_id") or ""),
                "rule_id": str(entry.get("rule_id") or ""),
                "rule_ref": str(entry.get("rule_ref") or "筛选与重置"),
            })
        except Exception as exc:  # noqa: BLE001  配置畸形不得阻断编译
            rejects.append({"value": "", "reason": "筛选检查解析异常",
                            "detail": f"{type(exc).__name__}: {exc}"[:200]})
    return items, rejects


# ==========================================================================
# 「导出」独立通道（``extra_config.export_checks``，rev148 新增）
# ==========================================================================
# 背景（2026-10-20 需求追溯实测）：导出 11 列走表单字段通道，天生无 required/options
# → 全被生成门槛丢掉；「更多菜单 → 导出明细弹窗 → 空时间段校验 → 选时间段 → 导出中心
# 完成 → 下载落盘 → 列名」此前只有人工探针（xy_p2/p8 系列）承接，不在回归里。
#
# ★ 诚实边界（写进判定，不冒充）：
#   * ``expect_columns`` 是**出现性校验**（xlsx 解压后全文包含该列名），不校验列序；
#   * zip 解压失败（本地文件头无尺寸/流式写入）时列名校验如实 unverified；
#   * 导出中心轮询超时 = unverified（任务可能排队），不判缺陷、不判通过。
EXPORT_CHECK_DEFAULTS: dict[str, Any] = {
    "menu": "更多",
    "label": "导出明细",
    "validate_empty_time": True,
    "validation_toast": "请选择时间|請選擇時間|请选择|請選擇",
    "pick_dates": True,
    "confirm_label": "导出|確定|确定|確認|确认",
    "status_text": "已完成|完成",
    "filename_regex": "调查问卷-.*\\.xlsx|問卷調查-.*\\.xlsx",
    #: rev157：导出中心**任务名称**与**下载文件名**是两个不同的字符串，不能共用一个正则。
    #: 实测（2026-10-21 10:31）：任务名 ``调查问卷-XY..-2026-10-21 10:31:24``（无后缀、冒号保留），
    #: 下载文件名 ``调查问卷-XY..-2026-10-21 10_31_24.xlsx``（有 .xlsx、冒号换成下划线）。
    #: 之前拿 ``filename_regex``（要求 ``\.xlsx``）去匹配任务名 ⇒ 恒不命中 ⇒ 轮询 90s 空转。
    "center_name_regex": "调查问卷-|問卷調查-",
    #: 命中行必须是**本轮新生成**的任务：行内「创建时间」需晚于点确定时刻减该容差。
    #: 否则历史行会让「导出没真发出」的情况被判成通过（假阳性）。
    "fresh_tolerance_s": 180,
    "timeout_ms": 180000,
    "poll_ms": 6000,
    "download": True,
    "max_wait_download_ms": 30000,
}


def parse_export_checks(raw: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """解析 ``extra_config.export_checks``，返回 ``(可用检查, 被拒检查)``。

    单条配置字段：
      ``name``            断言名（缺省 ``export:<label>``）
      ``value``           可选：先按此值检索定位目标行（缺省取第一行）
      ``list_url``        列表页直链（缺省回退 ``locators.list_url``；两者都空 → 拒绝）
      ``menu``            入口下拉文案（缺省「更多」；空串＝工具条直点 ``label``）
      ``label``           菜单项文案（缺省「导出明细」）
      ``validate_empty_time``  先空时间段点确认、期待校验 toast（缺省 True）
      ``validation_toast``     空时间段校验 toast 的正则
      ``pick_dates``      有时间选择器时自动选「倒数第二天 ~ 最后一天」（缺省 True）
      ``confirm_label``   弹窗确认按钮文案正则（缺省 导出|确定）
      ``export_center``   ``{url, status_text, timeout_ms, poll_ms}``（url 空＝点页面内「导出」入口）
      ``filename_regex``  下载文件名应匹配的正则
      ``expect_columns``  列名出现性校验（数组；空＝跳过列校验）
      ``download``        是否点击下载并落盘（缺省 True）
      ``case_id`` / ``rule_id``  证据归属
    """
    if raw is None or raw == "":
        return [], []
    if not isinstance(raw, list):
        return [], [{"value": "", "reason": "export_checks 必须是数组",
                     "detail": type(raw).__name__}]

    items: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        try:
            if not isinstance(entry, dict):
                rejects.append({"value": "", "reason": "检查必须是对象",
                                "detail": f"index={index}"})
                continue
            label = str(entry.get("label") or EXPORT_CHECK_DEFAULTS["label"])
            key = label
            if key in seen:
                rejects.append({"value": label, "reason": "同一 label 重复",
                                "detail": f"index={index}"})
                continue
            center = entry.get("export_center") or {}
            center = center if isinstance(center, dict) else {}
            columns = [str(c).strip() for c in (entry.get("expect_columns") or [])
                       if str(c).strip()]
            items.append({
                "name": str(entry.get("name") or f"export:{label}"),
                "value": str(entry.get("value") or ""),
                "list_url": str(entry.get("list_url") or ""),
                "menu": str(entry.get("menu", EXPORT_CHECK_DEFAULTS["menu"])),
                "label": label,
                "validate_empty_time": bool(entry.get(
                    "validate_empty_time", EXPORT_CHECK_DEFAULTS["validate_empty_time"])),
                "validation_toast": str(entry.get(
                    "validation_toast", EXPORT_CHECK_DEFAULTS["validation_toast"])),
                "pick_dates": bool(entry.get("pick_dates", EXPORT_CHECK_DEFAULTS["pick_dates"])),
                "confirm_label": str(entry.get(
                    "confirm_label", EXPORT_CHECK_DEFAULTS["confirm_label"])),
                "export_center": {
                    "url": str(center.get("url") or ""),
                    "status_text": str(center.get(
                        "status_text", EXPORT_CHECK_DEFAULTS["status_text"])),
                    "timeout_ms": max(10000, int(center.get(
                        "timeout_ms", EXPORT_CHECK_DEFAULTS["timeout_ms"]) or 180000)),
                    "poll_ms": max(2000, int(center.get(
                        "poll_ms", EXPORT_CHECK_DEFAULTS["poll_ms"]) or 6000)),
                },
                "filename_regex": str(entry.get(
                    "filename_regex", EXPORT_CHECK_DEFAULTS["filename_regex"])),
                "center_name_regex": str(entry.get(
                    "center_name_regex", EXPORT_CHECK_DEFAULTS["center_name_regex"])),
                "fresh_tolerance_s": max(0, int(entry.get(
                    "fresh_tolerance_s", EXPORT_CHECK_DEFAULTS["fresh_tolerance_s"]) or 180)),
                "expect_columns": columns,
                "download": bool(entry.get("download", EXPORT_CHECK_DEFAULTS["download"])),
                "max_wait_download_ms": max(5000, int(entry.get(
                    "max_wait_download_ms", EXPORT_CHECK_DEFAULTS["max_wait_download_ms"])
                    or 30000)),
                "case_id": str(entry.get("case_id") or ""),
                "rule_id": str(entry.get("rule_id") or ""),
                "rule_ref": str(entry.get("rule_ref") or "导出明细"),
            })
            seen.add(key)
        except Exception as exc:  # noqa: BLE001  配置畸形不得阻断编译
            rejects.append({"value": "", "reason": "检查解析异常",
                            "detail": f"{type(exc).__name__}: {exc}"[:200]})
    return items, rejects


# ==========================================================================
# 「状态×操作矩阵」独立通道（``extra_config.action_matrix_checks``，rev173 新增）
# ==========================================================================
# 背景（P3 第二项，覆盖审计 2026-10-21）：状态机与行内操作的对应关系（待启用=查看/
# 编辑/启用/更多(删除,导出明细)；启用=查看/复制/禁用/更多(删除,导出明细)；禁用=查看/
# 复制/删除/导出明细）此前只有只读探针盘点，没有用例、没有自动化断言。本通道按
# 「目标状态 → 定位该状态的一行 → 断言行内操作集 → （配置了才）枚举更多菜单 →
# （配置了才）点删除验确认框再取消」做成独立阶段，与 list/filter/export 同形。
#
# 诚实边界（写进判定，不冒充）：
#   * 目标状态行第 1 页找不到（含筛选后仍无）→ unverified（数据侧缺该状态，非断言结论）；
#   * 「更多」打不开 → unverified；expect_row 缺失 / forbid_row 出现 / expect_menu
#     缺失 → unverified + 逐项明细（业务断言结果，与字段级通道口径一致）；
#   * confirm 点击了「删除」却没有任何确认框（Modal 与 Popover 双采样均无）→ 这是
#     真断言结果（误删风险），同样记 unverified + 明细；确认框出现必须点取消还原，
#     取消失败 → unverified（先 Escape 兜底，绝不留确认框挂着）。
def parse_action_matrix_checks(raw: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """解析 ``extra_config.action_matrix_checks``，返回 ``(可用检查, 被拒检查)``。

    单条配置字段：
      ``name``             断言名（缺省由 status_in 生成）
      ``status_column``    状态所在列头文本（缺省 ``状态``）
      ``status_in``        目标状态取值（数组，繁简同配；**必填**）
      ``filter``           目标状态不在第 1 页时先用状态下拉筛选（缺省 False）
      ``filter_option``    筛选选项文本（缺省 = ``status_in[0]``）
      ``control_placeholder``  状态下拉 placeholder 候选（``filter: true`` 时用）
      ``expect_row``       行内（数据行 + 克隆操作行）必须可见的操作标签数组
      ``forbid_row``       行内不得出现的操作标签数组
      ``expect_menu``      点「更多」后菜单必须包含的项（行无「更多」→ 断言失败明细）
      ``confirm``          ``{"label": "删除", "via": "menu"|"row"}``——点它→必须出
                           确认框→点取消还原；不配则不碰任何业务动作
      ``list_url``         列表页直链（缺省回退 ``locators.list_url``）
      ``case_id`` / ``rule_id``  证据归属
    """
    if raw is None or raw == "":
        return [], []
    if not isinstance(raw, list):
        return [], [{"value": "", "reason": "action_matrix_checks 必须是数组",
                     "detail": type(raw).__name__}]

    items: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        try:
            if not isinstance(entry, dict):
                rejects.append({"value": "", "reason": "检查必须是对象",
                                "detail": f"index={index}"})
                continue
            status_in = [str(x).strip() for x in (entry.get("status_in") or [])
                         if str(x or "").strip()]
            if not status_in:
                rejects.append({"value": "", "reason": "缺少 status_in（目标状态取值数组）",
                                "detail": f"index={index}"})
                continue
            for key in ("expect_row", "forbid_row", "expect_menu"):
                val = entry.get(key)
                if val is not None and not (isinstance(val, list)
                                            and all(str(x).strip() for x in val)):
                    rejects.append({"value": "/".join(status_in), "reason": f"{key} 必须是字符串数组",
                                    "detail": f"index={index}"})
                    status_in = []
                    break
            if not status_in:
                continue
            confirm_raw = entry.get("confirm")
            confirm = None
            if confirm_raw is not None:
                if not isinstance(confirm_raw, dict) or not str(confirm_raw.get("label") or "").strip():
                    rejects.append({"value": "/".join(status_in),
                                    "reason": "confirm 必须是对象且带 label",
                                    "detail": f"index={index}"})
                    continue
                via = str(confirm_raw.get("via") or "row").strip().lower()
                if via not in ("menu", "row"):
                    rejects.append({"value": "/".join(status_in), "reason": "confirm.via 非法",
                                    "detail": f"{via!r}，允许：menu/row"})
                    continue
                confirm = {"label": str(confirm_raw["label"]).strip(), "via": via}
            name = str(entry.get("name") or f"matrix:{'/'.join(status_in)}")
            if name in seen:
                rejects.append({"value": name, "reason": "同一 name 重复",
                                "detail": f"index={index}"})
                continue
            seen.add(name)
            items.append({
                "name": name,
                "status_column": str(entry.get("status_column") or "状态"),
                "status_in": status_in,
                "filter": bool(entry.get("filter")),
                "filter_option": str(entry.get("filter_option") or status_in[0]),
                "control_placeholder": _as_str_list(entry.get("control_placeholder"))
                                       or ["请输入状态", "狀態", "状态"],
                "expect_row": [str(x).strip() for x in (entry.get("expect_row") or [])],
                "forbid_row": [str(x).strip() for x in (entry.get("forbid_row") or [])],
                "expect_menu": [str(x).strip() for x in (entry.get("expect_menu") or [])],
                "confirm": confirm,
                "list_url": str(entry.get("list_url") or ""),
                "case_id": str(entry.get("case_id") or ""),
                "rule_id": str(entry.get("rule_id") or ""),
                "rule_ref": str(entry.get("rule_ref") or "状态×操作矩阵"),
            })
        except Exception as exc:  # noqa: BLE001  配置畸形不得阻断编译
            rejects.append({"value": "", "reason": "检查解析异常",
                            "detail": f"{type(exc).__name__}: {exc}"[:200]})
    return items, rejects


#: ``pre_actions`` 的硬上限：配置写错时不能把整批运行拖死（每项都在检查循环里同步等待）。
PRE_ACTION_MAX_TIMES = 5
PRE_ACTION_MAX_WAIT_MS = 10000
PRE_ACTION_DEFAULT_WAIT_MS = 800


def parse_pre_actions(raw: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """解析 ``field_locators.pre_actions``：字段检查**之前**必须完成的一次点击。

    为什么需要它（2026-10-20，TR-752/753 实测 + 只读探针复核）：问卷「题目」区的输入框
    在点「新增题目」之前**根本不存在**（探针：点击前 `[class*=questionCard]` = 0 个，
    页面上只有一个 `#sbjTypeName`）。定位器
    ``[class*=questionCard] input[maxlength="50"]`` 本身是对的 —— 缺的是"让这块区域
    渲染出来"的那一次点击。旧口径下这类用例只能记 ``unverified``，
    与"选择器配错"在回执上**完全一样**，配置方会反复去改本来就正确的定位器。

    单条配置：
      ``labels``      **必填**：可点击文本候选（数组或字符串），按顺序试，命中即停
      ``for_fields``  可选：只在"作用到这些字段"的检查要执行前触发；缺省＝首条检查前一次
      ``times``       可选：连点次数（1–``PRE_ACTION_MAX_TIMES``，缺省 1）
      ``wait_ms``     可选：每次点击后等待毫秒（0–``PRE_ACTION_MAX_WAIT_MS``，缺省 800）
      ``reason``      可选：人读的动机（进 ``PRE_ACTION`` 日志，日后据此判断该不该保留）

    返回 ``(可用动作, 被拒动作)``。被拒项**不阻断编译**，但要进计划并在运行期日志可见——
    "配了但没生效"与"没配"必须能分开。
    """
    if raw is None or raw == "":
        return [], []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, list):
        return [], [{"labels": [], "reason": "pre_actions 必须是数组",
                     "detail": type(raw).__name__}]

    actions: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        try:
            if isinstance(entry, str):
                entry = {"labels": [entry]}
            if not isinstance(entry, dict):
                rejects.append({"labels": [], "reason": "前置动作必须是对象或字符串",
                                "detail": f"index={index}"})
                continue
            labels = _as_str_list(entry.get("labels") or entry.get("click"))
            labels = [lab for lab in labels if lab]
            if not labels:
                rejects.append({"labels": [], "reason": "缺少 labels（可点击文本）",
                                "detail": f"index={index}"})
                continue
            scope_fields = _as_str_list(entry.get("for_fields"))
            actions.append({
                "labels": labels,
                "for_fields": [f for f in scope_fields if f],
                "times": max(1, min(PRE_ACTION_MAX_TIMES, _as_int(entry.get("times"), 1))),
                "wait_ms": max(0, min(PRE_ACTION_MAX_WAIT_MS,
                                      _as_int(entry.get("wait_ms"), PRE_ACTION_DEFAULT_WAIT_MS))),
                "reason": str(entry.get("reason") or "").strip()[:200],
            })
        except Exception as exc:  # noqa: BLE001  配置畸形不得阻断编译
            rejects.append({"labels": [], "reason": "前置动作解析异常",
                            "detail": f"{type(exc).__name__}: {exc}"[:200]})
    return actions, rejects


@dataclass
class CompiledCase:
    identifier: str
    name: str
    rule_id: str = ""
    kind: str = "narrative"          # rule | narrative
    field: str = ""
    steps: list[dict[str, str]] = dc_field(default_factory=list)
    assertions: list[dict[str, Any]] = dc_field(default_factory=list)
    compile_status: str = "narrative"  # compiled | dangling_rule | narrative
    compile_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "name": self.name,
            "rule_id": self.rule_id,
            "kind": self.kind,
            "field": self.field,
            "steps": self.steps,
            "assertions": self.assertions,
            # 逐条用例的**编译状态**：区分"可编译执行"与两类不可编译用例，
            # 避免把"生成了用例"读成"已被自动验证"（见交付说明四指标口径）。
            "compile_status": self.compile_status,
            "compile_reason": self.compile_reason,
        }


def _steps_of(case: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for step in getattr(case, "test_case_steps", None) or []:
        out.append({
            "action": getattr(step, "step", "") or "",
            "expected": getattr(step, "result", "") or "",
        })
    return out


def compile_cases(cases: Iterable[Any]) -> dict[str, Any]:
    """把用例编译成执行清单（血缘 + 断言分级 + 覆盖度统计 + **逐条编译状态**）。

    编译状态（``compile_status``）分三类，表达的是**自动化可执行性**——
    用例本身全部来源于需求文档的可测试需求点、全部是可执行的测试用例
    （人工一定可执行）；状态**不**表达用例有效性：

    - ``compiled``：提取到规则体/断言 → 可自动执行（进入脚本断言执行）；
    - ``dangling_rule``：有 ``requirement_rule_id`` 但无可提取的规则体/断言 →
      无法自动执行，需人工执行（或补齐需求规则后重生成转自动）；
    - ``narrative``：无可提取规则编号与断言 → 无法自动执行，需人工执行。

    这三类的总数恒等于 ``case_count``（与四指标口径一致：生成用例数 ≠ 可自动执行用例数）。
    """
    compiled: list[CompiledCase] = []
    for case in cases:
        fields = getattr(case, "custom_fields", None) or {}
        rule = fields.get("requirement_rule") or {}
        assertions = list(fields.get("assertions") or [])
        rule_id = fields.get("requirement_rule_id") or ""
        kind = "rule" if assertions or rule else "narrative"
        if kind == "rule":
            compile_status = "compiled"
            compile_reason = f"携带 {len(assertions)} 条断言" if assertions else "携带规则体"
        elif rule_id:
            compile_status = "dangling_rule"
            # 措辞（rev64b）：用例本身可执行，缺的是可自动判定的断言
            compile_reason = "有规则编号但无可提取的规则体/断言 → 无法自动执行，需人工执行"
        else:
            compile_status = "narrative"
            compile_reason = "无可提取规则编号与断言 → 无法自动执行，需人工执行"
        compiled.append(CompiledCase(
            identifier=getattr(case, "identifier", "") or "",
            name=getattr(case, "name", "") or "",
            rule_id=rule_id,
            kind=kind,
            field=str(rule.get("field") or ""),
            steps=_steps_of(case),
            assertions=assertions,
            compile_status=compile_status,
            compile_reason=compile_reason,
        ))

    locator_assertions = sum(
        1 for c in compiled for a in c.assertions if a.get("kind") in LOCATOR_DEPENDENT_KINDS
    )
    soft_assertions = sum(
        1 for c in compiled for a in c.assertions if a.get("kind") in SOFT_CHECK_KINDS
    )
    field_labels: list[str] = []
    for c in compiled:
        if c.field and c.field not in field_labels:
            field_labels.append(c.field)
    rule_ids = [c.rule_id for c in compiled if c.rule_id]
    # 悬空规则号：有编号、无可提取规则体（无 rule 也无断言）。这类用例本身
    # 可执行（人工），但无法自动判定，**不能**充当自动覆盖度。实测两个批次各有 36 条。
    dangling_rule_ids = [
        c.rule_id for c in compiled
        if c.rule_id and c.kind != "rule"
    ]
    uncompiled = [c for c in compiled if c.compile_status != "compiled"]
    case_status_counts = {
        "compiled": sum(1 for c in compiled if c.compile_status == "compiled"),
        "dangling_rule": sum(1 for c in compiled if c.compile_status == "dangling_rule"),
        "narrative": sum(1 for c in compiled if c.compile_status == "narrative"),
    }

    return {
        "cases": [c.to_dict() for c in compiled],
        "case_count": len(compiled),
        "rule_case_count": sum(1 for c in compiled if c.kind == "rule"),
        "narrative_case_count": sum(1 for c in compiled if c.kind != "rule"),
        # ---- 四指标口径（生成用例数 / 规则用例数 / 编译断言数 / 实际执行资产数）----
        # 这里只负责前三个中的"可编译"口径；"实际执行资产数"由运行侧聚合
        # （见 services/run_generation_metrics.py）。四个数**互不等价**，不得合并成单一覆盖率。
        "compiled_case_count": case_status_counts["compiled"],
        "uncompiled_case_count": len(uncompiled),
        "case_status_counts": case_status_counts,
        "uncompiled_cases": [
            {"identifier": c.identifier, "status": c.compile_status, "reason": c.compile_reason}
            for c in uncompiled
        ],
        "rule_ids": rule_ids,
        "dangling_rule_ids": dangling_rule_ids,
        "field_labels": field_labels,
        "assertion_count": locator_assertions + soft_assertions,
        "soft_assertion_count": soft_assertions,
        "locator_depended_assertion_count": locator_assertions,
    }


def render_manifest_block(manifest: dict[str, Any], *, var_name: str = "COMPILED_CASES") -> str:
    """渲染成内嵌 TypeScript 常量（执行器只下载单个脚本文件）。"""
    payload = json.dumps(manifest, ensure_ascii=False, indent=2)
    return (
        f"// === 编译自需求用例（自动生成，请勿手改）：{manifest.get('case_count', 0)} 条用例 / "
        f"{manifest.get('assertion_count', 0)} 条断言 ===\n"
        f"const {var_name} = {payload} as const;\n"
    )


def render_coverage_snippet(*, scope_var: str = "appScope",
                            manifest_var: str = "COMPILED_CASES",
                            form_executable_checks: int = 0,
                            assertion_checks: int = 0) -> str:
    """渲染"编译覆盖度"输出片段。

    - 文档字段名做**软校验**（只统计、不判定失败，避免措辞差异造成假失败）；
    - ``pending_locator`` 扣掉表单执行器**真正执行**的那部分断言，避免把已执行的
      断言继续算作"未执行"；软校验单独记名，不冒充"已执行断言"。
    """
    return f"""
  // ---- 编译覆盖度（来自文档生成的用例/规则；软校验不判定失败）----
  // rev116（2026-10-19，实测 `店鋪名稱(CN）` 命中 0/3）：文档标签与页面文案常常只差
  // **全/半角括号、冒号、空格**（`店鋪名稱(CN）` vs `店鋪名稱（CN）`、
  // `營業時間：開始` vs `營業時間: 開始`）。直接 getByText(label) 做精确子串匹配会 0 命中，
  // 把"标签写法差异"误读成"字段不存在"。这里逐字建正则：括号/冒号放宽成字符类，
  // 字与字之间允许任意空白，末尾必填星号视为 UI 标记去掉；
  // 并把**未命中的标签名**打出来——旧输出只有 `0/3`，无法定位是哪几个。
  const __labelRe = (label: string): RegExp => {{
    const tokens: string[] = [];
    for (const ch of label) {{
      if (/\\s/.test(ch)) continue;  // 空白统一交给 join 处的 \\s*
      if (ch === '(' || ch === '（') tokens.push('[（(]');
      else if (ch === ')' || ch === '）') tokens.push('[)）]');
      else if (ch === ':' || ch === '：') tokens.push('[：:]');
      else tokens.push(ch.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&'));
    }}
    if (tokens.length && tokens[tokens.length - 1] === '\\\\*') tokens.pop();
    return new RegExp(tokens.join('\\\\s*'));
  }};
  let __labelHits = 0;
  const __labelMiss: string[] = [];
  for (const label of {manifest_var}.field_labels) {{
    try {{
      if (await {scope_var}.getByText(__labelRe(label)).first().count()) {{
        __labelHits += 1;
      }} else {{
        __labelMiss.push(label);
      }}
    }} catch {{
      // 单个标签查询失败不影响整体
    }}
  }}
  console.log('COMPILED_COVERAGE ' + JSON.stringify({{
    cases: {manifest_var}.case_count,
    rule_cases: {manifest_var}.rule_case_count,
    narrative_cases: {manifest_var}.narrative_case_count,
    dangling_rule_ids: {manifest_var}.dangling_rule_ids.length,
    assertions: {manifest_var}.assertion_count,
    form_executable_checks: {int(form_executable_checks)},
    pending_locator: Math.max(0, ({manifest_var}.locator_depended_assertion_count ?? {manifest_var}.pending_locator_count ?? 0) - {int(assertion_checks)}),
    soft_label_checks: {manifest_var}.soft_assertion_count,
    field_labels_found: __labelHits,
    field_labels_total: {manifest_var}.field_labels.length,
    //: 未命中的标签名（最多 8 个）：区分"页面没有该字段"与"标签写法不一致"
    field_labels_missing: __labelMiss.slice(0, 8),
    sample_rule_ids: {manifest_var}.rule_ids.slice(0, 5),
  }}));
"""


__all__ = [
    "CompiledCase",
    "LOCATOR_DEPENDENT_KINDS",
    "RECHARGE_FORM_LOCATORS",
    "SOFT_CHECK_KINDS",
    "build_form_plan",
    "compile_cases",
    "render_coverage_snippet",
    "render_form_executor",
    "render_manifest_block",
]


# ---------------------------------------------------------------------------
# 定位器层 + 表单校验执行器（v2：客户端层只读校验 + 「期望拒绝」项的负路径提交层）
# ---------------------------------------------------------------------------

#: 企业储值充值表单的定位器（来自 2026-09-22 对 UAT 的只读探测：
#: 列表页搜索手机号 → 点「企业充值/退款」→ 弹窗表单）。
#: 支持按子功能覆盖：WebSubFunction.custom_config["field_locators"]。
RECHARGE_FORM_LOCATORS: dict[str, Any] = {
    "name": "enterprise_recharge",
    "flow": "recharge_modal",
    "form_button": ["企业充值/退款", "创建储值充值"],
    "filter_phone_placeholder": "请输入手机号码",
    "search_button": "搜索",
    "modal": ".ant-modal",
    "fields": {
        "变动类型": {"kind": "radio"},
        "变更方式": {"kind": "radio"},
        "会员手机号": {"kind": "textarea", "placeholder_hint": "最多输入10个手机号"},
        "企业账户充值金额": {"kind": "input", "placeholder_hint": "请输入金额"},
        "企业账户退款金额": {"kind": "input", "placeholder_hint": "请输入金额"},
        "备注": {"kind": "textarea", "placeholder_hint": "请输入备注"},
    },
    #: 上传文件控件（「批量导入」模式下才出现）。
    #: **默认不执行**：附文件可能触发向 UAT 的上传请求（服务端副作用），
    #: 只有显式开启 ``allow_upload_checks`` 才会生成校验，且执行器会拦截任何上传请求。
    "upload_fields": {
        "上传文件（企业充值）": {
            "row_label": "上传文件",
            "mode": {"变动类型": "企业储值充值", "变更方式": "批量导入"},
        },
        "上传文件（企业退款）": {
            "row_label": "上传文件",
            "mode": {"变动类型": "企业储值退款（仅扣减储值不退款）", "变更方式": "批量导入"},
        },
    },
    #: 字段所在的条件模式：执行该字段的校验前先把单选切到指定选项。
    #: 金额字段在「批量导入」下会被上传控件替代，所以显式要求「手动输入」。
    "field_modes": {
        "企业账户充值金额": {"变动类型": "企业储值充值", "变更方式": "手动输入"},
        "企业账户退款金额": {"变动类型": "企业储值退款（仅扣减储值不退款）", "变更方式": "手动输入"},
    },
    "default_field_mode": {"变动类型": "企业储值充值", "变更方式": "手动输入"},
    #: 上传大小上限的**字节口径**（用户 2026-09-23 确认：1M = 1,048,576 字节，
    #: 支持格式含 CSV，前后端口径一致）。配置后大小断言会按 N−1 / N / N+1 展开验证。
    "upload_size_limit_bytes": 1_048_576,
    #: 文档声明的允许格式（用于给"格式应被接受"断言逐一构造合法样本）。
    "upload_allowed_formats": ["xlsx", "xls", "csv"],
    #: 声明了但**仍无法**构造有效样本的格式：必须显式登记为未执行，不得用假样本充数。
    #: （.xls 已提供有效 BIFF 样本，故不在此列。）
    "upload_unsupported_sample_formats": {},
    #: 条件显示断言：切换到 option 后，expect_labels 里的字段行应出现。
    #: 标签取自 2026-09-22 对 UAT 的只读 DOM 探测（批量导入下表单行就叫「上传文件」）。
    "toggles": [
        {"field": "变动类型", "option": "企业储值退款（仅扣减储值不退款）",
         "expect_labels": ["企业账户退款金额"],
         "mode": {"变更方式": "手动输入"}},
        # 另一维必须**显式钉住**：否则"切换后的状态"取决于前序检查留下了什么，
        # 归属给用例就会变成猜测（宁可多切一次，也不猜）。
        {"field": "变更方式", "option": "批量导入",
         "expect_labels": ["上传文件"],
         "mode": {"变动类型": "企业储值充值"}},
        {"field": "变更方式", "option": "批量导入",
         "expect_labels": ["上传文件"],
         "mode": {"变动类型": "企业储值退款（仅扣减储值不退款）"}},
    ],
    #: 默认选中断言（文档「默认选中」）
    "defaults": [
        {"field": "变动类型", "option": "企业储值充值"},
        {"field": "变更方式", "option": "手动输入"},
    ],
}


def _norm(text: Any) -> str:
    """归一化文本：去掉所有空白（文档"当…且…"条件串排版差异很大）。"""
    return re.sub(r"\s+", "", str(text or ""))


def _option_head(value: Any) -> str:
    """取选项/字段名的主干（去掉括号说明），用于条件串的包含匹配。

    例：``企业储值退款（仅扣减储值不退款`` → ``企业储值退款``；
    ``上传文件（企业充值）`` → ``上传文件``。
    """
    return re.split(r"[（(]", str(value or ""))[0].strip()


def _when_matches(when: Any, conditions: Iterable[tuple[str, str]]) -> bool:
    """条件串是否包含给定状态的全部 ``字段=取值``（做不到就返回 False，宁可不归属）。"""
    text = _norm(when)
    if not text:
        return False
    pairs = [(str(f), _option_head(v)) for f, v in conditions if f and _option_head(v)]
    if not pairs:
        return False
    return all(f"{_norm(f)}={head}" in text for f, head in pairs)


def _label_matches(case_field: Any, labels: Iterable[Any]) -> bool:
    """用例目标字段是否与"切换后应出现的标签"对应（允许括号说明差异）。"""
    head = _option_head(case_field)
    if not head:
        return False
    for label in labels:
        other = _option_head(label)
        if other and (head == other or head.startswith(other) or other.startswith(head)):
            return True
    return False


def _default_state(loc: Mapping[str, Any]) -> dict[str, str]:
    """定位器声明的默认态（未做任何点击时的单选状态）。"""
    state: dict[str, str] = {}
    for default in loc.get("defaults") or []:
        if default.get("field") and default.get("option"):
            state[str(default["field"])] = str(default["option"])
    return state


def _visibility_wants(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    """清单里所有"应显示"的条件显示诉求（带归属用例）。"""
    wants: list[dict[str, str]] = []
    for case in manifest.get("cases") or []:
        for assertion in case.get("assertions") or []:
            if assertion.get("kind") != "visibility":
                continue
            if str(assertion.get("expect") or "") != "visible":
                continue
            wants.append({
                "case_id": str(case.get("identifier") or ""),
                "rule_id": str(case.get("rule_id") or ""),
                "field": str(assertion.get("field") or case.get("field") or ""),
                "when": str(assertion.get("when") or ""),
                "expect": str(assertion.get("expect") or ""),
            })
    return wants


def _default_wants(manifest: Mapping[str, Any], field: str, option: str
                   ) -> list[dict[str, str]]:
    """哪些用例的 ``default`` 断言恰好等于该默认观测项（字段+选项完全一致）。"""
    owners: list[dict[str, str]] = []
    for case in manifest.get("cases") or []:
        for assertion in case.get("assertions") or []:
            if assertion.get("kind") != "default":
                continue
            if str(assertion.get("field") or case.get("field") or "") != field:
                continue
            expect = str(assertion.get("expect") or "")
            rule = str(assertion.get("rule") or "")
            if not (option and (option in expect or option in rule)):
                continue
            owners.append({"case_id": str(case.get("identifier") or ""),
                           "rule_id": str(case.get("rule_id") or "")})
    return owners


#: 长度/条数上限的文档措辞（简繁）
_LENGTH_HINT_RE = re.compile(
    r"(?:限制|最多|上限|不超過|不超过)\s*(?:輸入|输入|填寫|填写)?\s*([0-9]+)\s*(?:個|个)?\s*[字字符]"
)
#: 条数类上限措辞（"最多输入10个手机号"）——仅用于说明文字，不影响探针长度
_COUNT_HINT_RE = re.compile(
    r"(?:限制|最多|上限|不超過|不超过)\s*(?:輸入|输入|填寫|填写)?\s*([0-9]+)\s*(?:個|个)?\s*"
    r"(?:手机号|手機號|条|條|附件|附件數|附件数)"
)
_LEADING_NUMBER_RE = re.compile(r"^\s*([0-9]+)")


def probe_value_for(assertion: Mapping[str, Any]) -> tuple[str, str]:
    """为长度/条数类断言合成**可输入的探针值**，返回 ``(值, 说明)``。

    实测问题（CS 规格书）：断言的 ``data`` 是**人类可读描述**而不是输入值——
    `{"kind": "max_length", "data": "500字", "expect": "accept"}` /
    `{"data": "501字", "expect": "reject"}`。执行器原来直接 `fill(data)`，
    于是往输入框里输入了字面量 `500字`（3 个字符）：
    **边界根本没有被验证**，却会产出"通过/未验证"的结论（典型的假验证）。
    这里按文档上限换算成真实长度的样本：accept → N 字符，reject → N+1 字符。
    """
    kind = str(assertion.get("kind") or "")
    if kind not in {"max_length", "max_count"}:
        return "", ""
    data = str(assertion.get("data") or "")
    rule = str(assertion.get("rule") or "")
    expect = str(assertion.get("expect") or "")

    # data 里的数字是**样本长度描述**（"500字"= 500 字符的样本、"501字"= 超限样本、
    # "11个手机号"= 11 个手机号的样本），不是上限；上限来自 rule（"限制500個字"）。
    sample: int | None = None
    match = _LEADING_NUMBER_RE.match(data)
    if match:
        sample = int(match.group(1))

    limit: int | None = None
    for pattern in (_LENGTH_HINT_RE, _COUNT_HINT_RE):
        for source in (rule, data):
            hit = pattern.search(source)
            if hit:
                limit = int(hit.group(1))
                break
        if limit is not None:
            break

    if sample:
        target = sample
    elif limit and expect.startswith("accept"):
        target = limit
    elif limit and expect.startswith("reject"):
        target = limit + 1
    else:
        return "", ""
    note = (
        f"样本 {target} 字符（文档上限 {limit}）" if limit
        else f"样本 {target} 字符"
    )
    return "A" * target, note


#: 默认（内置）表单定位器表 → **只允许它所属的模板**使用。
#:
#: 反例（实测 2026-09-29 限购批次）：新业务模板在没有自身定位器的情况下回退到内置表，
#: 执行器会去操作内置表里的字段（如"请输入手机号码"），而目标页面根本没有该控件 →
#: 定位等待超时、整批 unverified。因此"用哪张定位器表"必须是**显式决定**：
#:   1) 子功能 ``custom_config.field_locators``（业务侧配置，推荐且零代码）；
#:   2) 本模板自带的定位器表（登记在下表中）；
#:   都没有 → **不生成表单检查**，断言如实留在 ``pending_locator``。
#: 本映射不含任何"项目/客户"信息，只按模板键登记平台自带的表。
TEMPLATE_FORM_LOCATORS: dict[str, dict[str, Any]] = {
    "enterprise_storage_recharge": RECHARGE_FORM_LOCATORS,
}


def resolve_form_locators(
    template_key: Any,
    override: Any = None,
    registered: Any = None,
) -> tuple[dict[str, Any] | None, str]:
    """决定本次表单检查用哪张定位器表，返回 ``(定位器表, 来源)``。

    来源取值（供审计写进 ``generation_params``），**优先级从具体到通用**：
    - ``sub_function_override``：子功能配置了 ``custom_config.field_locators``（最具体）；
    - ``project_registration``：项目内按模板键注册的表（P6 数据化注册，可在线维护、跨子功能复用）；
    - ``template_default``：平台内置表（老项目零改动兜底）；
    - ``none``：都没有 → 不生成表单检查（**不回退到其它业务的表**）。
    """
    if isinstance(override, dict) and override.get("fields"):
        return override, "sub_function_override"
    if isinstance(registered, dict) and registered.get("fields"):
        return registered, "project_registration"
    table = TEMPLATE_FORM_LOCATORS.get(str(template_key or ""))
    if isinstance(table, dict) and table.get("fields"):
        return table, "template_default"
    return None, "none"


#: 注册行合并成定位器表时附带的**来源元信息**键（不是定位器配置本身）。
#: 预览接口据此把它们从"定位器键"列表里剔除，避免看起来像多配了定位器。
REGISTRATION_META_KEYS: tuple[str, ...] = ("template_key", "registration_id", "source_ref")

#: ``extra_config`` 里**不是定位器**的键：它们是**行为配置**（目前是规则级负路径场景）。
#: 预览接口的"定位器键"列表必须把这些也剔掉——把 ``negative_scenarios`` 列进
#: ``locator_keys`` 会让配置方以为多注册了一个叫这个名字的定位器（实测于
#: ``test_resolve_preview_exposes_negative_scenarios_and_rejects``）。
#: 执行侧做同类剔除时必须用**这个**集合，否则两端又会分叉。
#: 预览接口的"定位器键"列表必须把这些也剔掉——把 ``negative_scenarios`` / ``list_checks``
#: 列进 locator_keys 会让"这是定位器"的语义失真（它们是行为配置，不是选择器）。
NON_LOCATOR_CONFIG_KEYS: tuple[str, ...] = REGISTRATION_META_KEYS + ("negative_scenarios",
                                                                     "list_checks",
                                                                     # rev170：筛选区通道
                                                                     # 同为行为配置，不进定位器键
                                                                     "filter_checks",
                                                                     # rev173：状态×操作矩阵通道
                                                                     # 同为行为配置，不进定位器键
                                                                     "action_matrix_checks",
                                                                     # rev119：字段级上传放行
                                                                     # 是**行为配置**，不是选择器；
                                                                     # 列进 locator_keys 会让配置方
                                                                     # 以为多注册了一个定位器。
                                                                     "upload_allow",
                                                                     # rev134：别名表与默认值声明同样是
                                                                     # 行为配置——不进定位器键清单，否则
                                                                     # 预览会显示"配了 9 个定位器"，其中
                                                                     # 两个其实是 `aliases`/`defaults`。
                                                                     "aliases",
                                                                     "defaults",
                                                                     # rev137：前置动作同样是**行为配置**
                                                                     #（"检查前先点一下"），不是选择器。
                                                                     # 列进 locator_keys 会让配置方
                                                                     # 以为多注册了一个叫
                                                                     # `pre_actions` 的定位器。
                                                                     "pre_actions")


def merge_registered_locators(row: Any) -> dict[str, Any]:
    """把 ``web_form_templates`` 注册行还原成**完整**定位器表。

    实测（TR-163，2026-09-30）：只登记 ``fields`` 时，执行器在打开表单处报
    ``FORM_PLAN.locators.form_button is not iterable`` 整条运行硬失败，且从报错
    看不出是"注册时少带了 ``extra_config``"造成的。

    故这里把 ``extra_config`` 的其余键与 ``fields`` 合并；
    ``extra_config`` 里的 ``fields`` 一律忽略（**字段表只有一处真相**）。

    本函数是执行链路与预览接口的**唯一实现**——两处若各写一份，很容易出现
    "预览说没问题、执行却崩"的分叉（TR-163 正是这么发生的）。
    """
    extra = getattr(row, "extra_config", None)
    extra = extra if isinstance(extra, dict) else {}
    table: dict[str, Any] = {k: v for k, v in extra.items() if k != "fields"}
    table["fields"] = row.fields
    return {
        **table,
        "template_key": getattr(row, "template_key", None),
        "registration_id": str(getattr(row, "id", "")) or None,
        "source_ref": getattr(row, "source_ref", None),
    }


def build_form_plan_for_template(
    template_key: Any,
    manifest: dict[str, Any],
    override: Any = None,
    registered: Any = None,
    *,
    allow_upload: bool = False,
) -> tuple[dict[str, Any] | None, str]:
    """按**显式来源**生成表单检查计划，返回 ``(计划, 来源)``。

    来源见 :func:`resolve_form_locators`；无来源时返回 ``(None, "none")``，
    **不会**跨业务回退到别的定位器表。
    """
    locators, source = resolve_form_locators(template_key, override, registered)
    if not locators:
        return None, source
    return build_form_plan(manifest, locators, allow_upload=allow_upload), source


def _normalize_field_name(name: Any) -> str:
    """字段名归一化：去空白/全角空格/括号后缀（"上传文件（企业充值）"→"上传文件"）。"""
    text = str(name or "")
    text = re.sub(r"[\s\u3000]+", "", text)
    text = re.sub(r"[（(][^）)]*[）)]\s*$", "", text)
    return text.strip()


def _suggest_fields(needle: Any, candidates: Iterable[Any], limit: int = 3) -> list[str]:
    """给"取不到定位器的字段名"找**近似候选**（纯字符串相似度，不含任何业务词）。

    实测动机（2026-09-29 P4 验收）：断言字段与定位器表里的名字差一点（多一个词/后缀不同）时，
    该断言会在**计划期**被静默跳过（只留一个 `无定位器`），既没有运行期现场、
    也只能靠人去比对两张表。这里直接给出候选，让"一次就能改对"。
    """
    target = _normalize_field_name(needle)
    if not target:
        return []
    scored: list[tuple[float, str]] = []
    for candidate in candidates:
        name = str(candidate or "")
        key = _normalize_field_name(name)
        if not key:
            continue
        if key == target:
            score = 1.0
        elif key in target or target in key:
            score = 0.9
        else:  # 双字组重合度（对中文友好，避免引入第三方库）
            pairs = {target[i:i + 2] for i in range(max(len(target) - 1, 1))}
            other = {key[i:i + 2] for i in range(max(len(key) - 1, 1))}
            shared = pairs & other
            score = len(shared) / max(len(pairs), 1)
        if score >= 0.4:
            scored.append((score, name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [name for _score, name in scored[:limit]]


def build_form_plan(
    manifest: dict[str, Any],
    locators: dict[str, Any] | None = None,
    *,
    allow_upload: bool = False,
) -> dict[str, Any] | None:
    """把编译清单里的断言映射成**可执行**的表单校验计划。

    只有"字段名能在定位器表里找到"的断言才会进入计划；其余仍留在 pending_locator。
    计划里的检查以**不提交**的客户端校验为主；「期望拒绝」但客户端层无法验证的项
    （submit_only/required_error/input_validation-reject）进入**负路径提交层**重试
    （空必填/非法探针值真实点击提交，口径经用户 2026-09-25 确认）。

    定位器表**必须显式给出**（2026-09-29 复核 P1）：历史实现不传就回退到内置的储值表，
    于是任何未配置定位器的新业务模板都会去操作储值页面的控件（实测 CS Module、
    会员限购商品管理两次同一根因）。现在 ``locators`` 为空 → 直接返回 ``None``
    （调用方据此把断言如实留在 pending_locator，而不是跨业务套用别人的表）。
    生产路径请用 :func:`build_form_plan_for_template` 或 ``resolve_form_locators``。

    归属原则：计划级校验（默认选中/条件显示）**不是无主的**——它验证的就是文档里
    对应字段的规则，因此必须带 ``owners``（用例标识 + 规则号），否则会出现
    "断言执行并通过了，却没有任何用例拿到证据"的证据黑洞（且用例被低报为未执行）。
    """
    loc = locators if isinstance(locators, dict) else {}
    fields = loc.get("fields") or {}
    if not fields:
        return None

    checks: list[dict[str, Any]] = []
    toggle_checks: list[dict[str, Any]] = []   # 顺序上排在字段校验之后
    skipped: list[dict[str, Any]] = []
    assertion_checks = 0          # 由文档断言直接生成的校验数（不含默认值/条件显示）
    field_modes = loc.get("field_modes") or {}
    default_mode = loc.get("default_field_mode") or {}
    upload_fields = loc.get("upload_fields") or {}
    #: 字段别名（2026-10-17，P1-②）：PRD 文档字段名常带英文前缀（如
    #: ``Item Code商品編碼``），与定位器键（``商品編碼``）精确匹配不上而全部
    #: 落入 pending_locator。别名表由定位器配置显式声明（``aliases``:
    #: 文档字段名 → 定位器键），**不做模糊自动匹配**——避免跨业务套用。
    aliases = loc.get("aliases") or {}
    #: 别名命中流水（rev134）：别名表配了不等于生效——键名拼错、目标键不存在都会
    #: 静默退化成"仍无定位器"。这里把**真实发生的改写**记下来，回执里可核对
    #: "配了 N 条、命中 M 条"，避免用"配置存在"冒充"已生效"。
    alias_hits: dict[tuple[str, str], int] = {}
    #: 列契约（N3，2026-10-18）：与预览接口共用**同一个**解析函数。
    #: 本期只用于诊断/配置校对，不产生检查（尚无列表检查执行器）。
    columns_contract, column_rejects = parse_columns(loc)

    def _resolve_field_name(name: str) -> str:
        """文档字段名 → 定位器键。仅按**显式别名表**解析，不做模糊匹配。"""
        if not name or name in fields:
            return name
        target = aliases.get(name) if isinstance(aliases, dict) else None
        if isinstance(target, str) and target in fields:
            key = (name, target)
            alias_hits[key] = alias_hits.get(key, 0) + 1
            return target
        return name

    wants = _visibility_wants(manifest)
    default_state = _default_state(loc)
    covered_visibility: set[tuple[str, str, str]] = set()
    covered_defaults: set[tuple[str, str]] = set()

    def owners_for(state: Mapping[str, str], labels: Iterable[Any]) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        pairs = [(k, v) for k, v in state.items() if k and v]
        for want in wants:
            key = (want["case_id"], want["field"], want["expect"])
            if key in covered_visibility:
                continue
            if not _label_matches(want["field"], labels):
                continue
            if not _when_matches(want["when"], pairs):
                continue
            covered_visibility.add(key)
            out.append({"case_id": want["case_id"], "rule_id": want["rule_id"]})
        return out

    # 顺序很重要：默认选中必须在任何点击之前观测（点击会改变单选状态），
    # 条件显示切换放最后（它会改变表单模式）。
    for default in loc.get("defaults") or []:
        field = str(default.get("field") or "")
        option = str(default.get("option") or "")
        check: dict[str, Any] = {"type": "default_selected", "field": field,
                                 "option": option, "rule": "默认选中",
                                 # 来源=定位器配置：这类检查由"配置里声明的默认值"驱动，
                                 # 不一定有对应用例断言 → 台账据此与"本该有归属却没有"区分开
                                 "source": "locator_config"}
        owners = _default_wants(manifest, field, option)
        if owners:
            check["owners"] = owners
            for owner in owners:
                covered_defaults.add((owner["case_id"], field))
        checks.append(check)

    # 默认态的条件显示：在任何点击之前，先把"默认态就该可见"的字段判定掉
    for want in wants:
        key = (want["case_id"], want["field"])
        if key in covered_visibility:
            continue
        if not _when_matches(want["when"], [(k, v) for k, v in default_state.items() if v]):
            continue
        covered_visibility.add(key)
        checks.append({"type": "visible_now", "field": want["field"],
                       "expect_labels": [want["field"]], "rule": "条件显示（默认态）",
                       "rule_id": want["rule_id"], "case_id": want["case_id"]})

    # 条件显示切换：把每个切换后的**完整状态**算出来，再归属给条件完全匹配的用例
    for toggle in loc.get("toggles") or []:
        field = str(toggle.get("field") or "")
        option = str(toggle.get("option") or "")
        mode = dict(toggle.get("mode") or {})
        state: dict[str, str] = dict(default_state)
        state.update({k: v for k, v in mode.items() if k and v})
        if field and option:
            state[field] = option
        check = {"type": "visibility", "field": field, "option": option,
                 "expect_labels": toggle.get("expect_labels") or [], "mode": mode,
                 "rule": "条件显示",
                 # R5（2026-10-17）：与 default_selected 同口径标来源。否则无 owner 的
                 # 切换检查在台账里既无 case_id 又无 source，会被判成"未归属"并每次报
                 # 假告警（实测 TR-697 的 2 条 visibility:* 即如此）。
                 "source": "locator_config"}
        owners = owners_for(state, check["expect_labels"])
        if owners:
            check["owners"] = owners
        toggle_checks.append(check)

    for case in manifest.get("cases") or []:
        # ---- R1（2026-10-17）：字段**逐断言**解析 ----
        # 历史实现只取 ``rule.field`` 一个字段，于是「同一条用例的多个断言作用于
        # 不同字段」时只有那个字段能落地，其余要么错配、要么整条用例被跳过。
        # 实测本项目库内 **1016/1016 条断言都自带 field**，编译器一个都没用上。
        # 现在：断言自身的 field 优先，规则字段仅在断言缺 field 时兜底。
        case_field = _resolve_field_name(case.get("field") or "")
        planned: list[tuple[dict[str, Any], str]] = [
            (assertion, _resolve_field_name(str(assertion.get("field") or "") or case_field))
            for assertion in (case.get("assertions") or [])
        ]

        def landed(name: str) -> bool:
            return bool(name) and (name in fields or name in upload_fields)

        if not any(name for _a, name in planned):
            # 没有断言 ⇒ 叙述型用例，静默跳过（本来就没有可执行内容）。
            # 但**有断言却没字段**不能静默（2026-10-17 补）：那说明断言写坏了，
            # 静默会让它混进"零检查"里，审计时无从下手。
            if planned:
                skipped.append({
                    "field": "",
                    "reason": "断言缺少 field（无法确定作用于哪个控件）",
                    "case_id": case.get("identifier") or "",
                    "rule_id": case.get("rule_id") or "",
                    "kinds": sorted({
                        str(a.get("kind") or "") for a, _f in planned if a.get("kind")
                    }),
                    "declared_fields": list(fields.keys())[:20],
                    "suggestions": [],
                })
            elif is_list_scope_field(case.get("field")):
                # N3（2026-10-18）：列表域规则（列清单/筛选/排序/分页）**不是叙述型用例**。
                # 真实库取证：这类用例 72/72 断言数为 0，规则体里只有列名文字。
                # 历史实现把它当 narrative 静默吞掉，于是"零检查"既无原因也无归属；
                # 更要紧的是它会被误读成"配定位器就能修"——列清单不是表单控件，
                # 补 fields 一条都救不回来，缺的是列表检查生成器。
                skipped.append({
                    "field": str(case.get("field") or ""),
                    "scope": "list",
                    "reason": (
                        "列表域规则未生成断言（需列表检查生成器：列头/筛选/分页）；"
                        "配 fields 定位器无效"
                    ),
                    "case_id": case.get("identifier") or "",
                    "rule_id": case.get("rule_id") or "",
                    "kinds": [],
                    "columns_declared": int((columns_contract or {}).get("column_count") or 0),
                    "declared_fields": list(fields.keys())[:20],
                    "suggestions": [],
                })
            continue
        if not any(landed(name) for _a, name in planned):
            # 一条都落不了地 → 整条记一次（保留既有的可诊断信息）
            probe_field = next(name for _a, name in planned if name)
            list_scope = is_list_scope_field(probe_field)
            skipped.append({
                "field": probe_field,
                "scope": "list" if list_scope else "form",
                "reason": (
                    "列表域字段无执行通道（需列契约 + 列表检查生成器，配 fields 无效）"
                    if list_scope else "无定位器"
                ),
                "case_id": case.get("identifier") or "",
                "rule_id": case.get("rule_id") or "",
                "kinds": sorted({
                    str(a.get("kind") or "")
                    for a, _f in planned if a.get("kind")
                }),
                "columns_declared": int((columns_contract or {}).get("column_count") or 0),
                "declared_fields": list(fields.keys())[:20],
                "suggestions": [] if list_scope else _suggest_fields(probe_field, fields.keys()),
            })
            continue
        for assertion, field in planned:
            is_upload = field in upload_fields
            if not landed(field):
                # 部分落地：只跳过这一条断言，其余断言照常编译
                list_scope = is_list_scope_field(field)
                skipped.append({
                    "field": field,
                    "scope": "list" if list_scope else "form",
                    "reason": (
                        "列表域字段无执行通道（该断言）" if list_scope
                        else "无定位器（该断言的字段）"
                    ),
                    "case_id": case.get("identifier") or "",
                    "rule_id": case.get("rule_id") or "",
                    "kinds": [str(assertion.get("kind") or "")],
                    "columns_declared": int((columns_contract or {}).get("column_count") or 0),
                    "declared_fields": list(fields.keys())[:20],
                    "suggestions": [] if list_scope else _suggest_fields(field, fields.keys()),
                })
                continue
            kind = assertion.get("kind")
            if kind == "required":
                # 单选组无法 fill()：其"必填"只能在提交时校验 → **显式记为未验证**，
                # 不能静默跳过（否则该用例会因为没有证据反而被算作"全部通过"）。
                if is_upload:
                    checks.append({"type": "submit_only", "field": field,
                                   "rule": assertion.get("rule", "是否必填=是"),
                                   "reason": "上传控件必填只在提交时校验（本版不提交）",
                                   "rule_id": case.get("rule_id") or "",
                                   "case_id": case.get("identifier") or ""})
                    assertion_checks += 1
                    continue
                if (fields.get(field) or {}).get("kind") == "radio":
                    checks.append({"type": "submit_only", "field": field,
                                   "rule": assertion.get("rule", "是否必填=是"),
                                   "reason": "单选组无自由输入：必填只在提交时校验（本版不提交）",
                                   "rule_id": case.get("rule_id") or "",
                                   "case_id": case.get("identifier") or ""})
                    assertion_checks += 1
                    continue
                # rev116（2026-10-17，P1-③ 实测 TR-500/TR-505）：勾选组（checkbox 群组，
                # 如 YL「營業時間」= Mon~Sun 7 个勾选框）无自由输入路径，fill 必然抛
                # ``Input of type "checkbox" cannot be filled`` → 整条作业 blocked。
                # 其"必填"只能在提交时校验 ⇒ 显式转负路径提交层，不静默跳过、也不硬失败。
                if (fields.get(field) or {}).get("kind") in {"checkbox", "checkbox_group", "multi_select"}:
                    checks.append({"type": "submit_only", "field": field,
                                   "rule": assertion.get("rule", "是否必填=是"),
                                   "reason": "勾选组（多选）无自由输入：必填只能在提交时校验（本版不提交，避免创建脏数据）",
                                   "rule_id": case.get("rule_id") or "",
                                   "case_id": case.get("identifier") or ""})
                    assertion_checks += 1
                    continue
                # 开关控件（ant-switch）始终有值，"必填"语义不成立；UI 层无法构造空值，
                # 也不需要提交（提交也造不出空值）⇒ 如实记未验证。
                if (fields.get(field) or {}).get("kind") == "switch":
                    checks.append({"type": "submit_only", "field": field,
                                   "rule": assertion.get("rule", "是否必填=是"),
                                   "reason": "开关控件始终有值：必填不适用（UI 层无法构造空值）",
                                   "rule_id": case.get("rule_id") or "",
                                   "case_id": case.get("identifier") or ""})
                    assertion_checks += 1
                    continue
                checks.append({"type": "required_error", "field": field,
                               "rule": assertion.get("rule", "是否必填=是"),
                               "mode": field_modes.get(field, default_mode),
                               "rule_id": case.get("rule_id") or "",
                               # 精确归属：rule_id 会被同字段的多条用例共享（实测 5 个 id 覆盖 14 条），
                               # 只用 rule_id 归属会把兄弟用例误标"未执行"
                               "case_id": case.get("identifier") or ""})
                assertion_checks += 1
            elif kind in {"boundary", "range", "precision", "max_length", "max_count"}:
                probe_value, probe_note = probe_value_for(assertion)
                check: dict[str, Any] = {
                    "type": "input_validation", "field": field,
                    "data": str(assertion.get("data", "")),
                    "expect": assertion.get("expect", ""),
                    "rule": assertion.get("rule", ""),
                    "mode": field_modes.get(field, default_mode),
                    # 归属：用于把执行结果落到**具体用例**上（而非只有锚点用例）
                    "rule_id": case.get("rule_id") or "",
                    "case_id": case.get("identifier") or "",
                }
                if probe_value:
                    # data 是人类可读描述（"500字"）时必须换算成真实输入值，否则边界未被验证
                    check["probe_value"] = probe_value
                    check["probe_note"] = probe_note
                checks.append(check)
                assertion_checks += 1
            elif kind == "enum" and (fields.get(field) or {}).get("kind") in {"checkbox", "checkbox_group", "multi_select"}:
                # 勾选组的"选项范围"只读可验证（读选项标签/数量），不存在输入非法值的路径。
                # rev116：YL「營業時間·支持選擇7組」即此类——数量断言由执行器按 rule/data 里的
                # "N組/組/项" 解析后与真实选项数比对。
                checks.append({"type": "choice_options", "field": field,
                               "allowed": assertion.get("allowed") or [],
                               "data": str(assertion.get("data") or ""),
                               "expect": assertion.get("expect", ""),
                               "rule": assertion.get("rule", ""),
                               "rule_id": case.get("rule_id") or "",
                               "case_id": case.get("identifier") or ""})
                assertion_checks += 1
            elif kind == "enum" and (fields.get(field) or {}).get("kind") == "radio":
                # 单选组的"选项范围"是**只读可验证**的：读选项标签即可判定，
                # 且不存在"输入非法值"的路径（用户根本无法选到不存在的项）。
                checks.append({"type": "radio_options", "field": field,
                               "allowed": assertion.get("allowed") or [],
                               "data": str(assertion.get("data") or ""),
                               "expect": assertion.get("expect", ""),
                               "rule": assertion.get("rule", ""),
                               "rule_id": case.get("rule_id") or "",
                               "case_id": case.get("identifier") or ""})
                assertion_checks += 1
            elif kind == "enum" and (fields.get(field) or {}).get("kind") == "select":
                # 下拉（antd Select）的"选项范围"同样**只读可验证**：展开下拉读选项文本。
                # 注意：Select 的选项渲染在 portal 里（可能挂到 body），执行器需在同一作用域内查。
                checks.append({"type": "select_options", "field": field,
                               "allowed": assertion.get("allowed") or [],
                               "data": str(assertion.get("data") or ""),
                               "expect": assertion.get("expect", ""),
                               "rule": assertion.get("rule", ""),
                               "rule_id": case.get("rule_id") or "",
                               "case_id": case.get("identifier") or ""})
                assertion_checks += 1
            elif kind == "default":
                # 默认选中：若定位器里已有同字段同选项的默认观测项，证据已在
                # default_selected 检查上（归属见 owners），这里不重复、也不算"不支持"
                if (case.get("identifier") or "", field) in covered_defaults:
                    continue
                skipped.append({"field": field,
                                "reason": "默认值须在未点击状态下观测，且未配置该默认观测项"})
            elif kind in {"file_format", "file_size"}:
                if not is_upload:
                    skipped.append({"field": field, "reason": "需上传文件（该字段无上传控件配置）",
                                    "kind": kind})
                    continue
                if not allow_upload:
                    # 默认关闭：附文件可能触发向 UAT 的上传请求（服务端副作用），
                    # 必须显式开启才执行；关闭时**显式登记**，不静默丢弃。
                    skipped.append({"field": field, "kind": kind,
                                    "reason": "需上传文件（allow_upload_checks=false，默认关闭）"})
                    continue
                upload_cfg = upload_fields.get(field) or {}
                expect = str(assertion.get("expect") or "")
                threshold = loc.get("upload_size_limit_bytes")
                ev_kind = "format" if kind == "file_format" else "size"
                samples: list[dict[str, Any]] = []
                if ev_kind == "format":
                    if expect == "accept":
                        # 文档说"仅支持 xlsx、xls、csv"→ 对**可构造有效样本**的格式逐个验证；
                        # 无法构造有效样本的格式（如 BIFF .xls）显式登记为未执行，绝不用假样本充数
                        for fmt in (loc.get("upload_allowed_formats") or ["xlsx"]):
                            if fmt == "xlsx":
                                samples.append(sample_spec(
                                    name="upload_valid.xlsx", mime=XLSX_MIME,
                                    source="workbook_stored", kind="valid_workbook"))
                            elif fmt == "csv":
                                samples.append(sample_spec(
                                    name="upload_valid.csv", mime="text/csv",
                                    source="csv_text", kind="valid_csv"))
                            elif fmt == "xls":
                                samples.append(sample_spec(
                                    name="upload_valid.xls", mime="application/vnd.ms-excel",
                                    source="xls_bytes", kind="valid_xls"))
                            else:
                                reason = (loc.get("upload_unsupported_sample_formats") or {}).get(fmt)
                                skipped.append({
                                    "field": field, "kind": kind, "format": fmt,
                                    "reason": "无可用的有效样本：%s" % (reason or "未支持构造"),
                                })
                    else:
                        samples.append(sample_spec(
                            name="upload_bad.exe", mime="application/octet-stream",
                            source="bad_extension", byte_size=256,
                            kind="disallowed_extension"))
                else:
                    # 大小：阈值字节数已由用户确认（1M = 1,048,576）→ 按 N−1 / N / N+1 三个边界样本验证。
                    # 未确认时**不自行假设**，改用明显超大样本并标注"临界点未验证"。
                    confirmed = isinstance(threshold, int) and threshold > 0
                    if confirmed and expect == "accept":
                        samples.append(sample_spec(name="upload_%d.xlsx" % (threshold - 1),
                                                   mime=XLSX_MIME, source="workbook_stored",
                                                   byte_size=threshold - 1, kind="boundary_minus",
                                                   threshold_confirmed=True))
                        samples.append(sample_spec(name="upload_%d.xlsx" % threshold,
                                                   mime=XLSX_MIME, source="workbook_stored",
                                                   byte_size=threshold, kind="boundary",
                                                   threshold_confirmed=True))
                    elif confirmed:
                        samples.append(sample_spec(name="upload_%d.xlsx" % (threshold + 1),
                                                   mime=XLSX_MIME, source="workbook_stored",
                                                   byte_size=threshold + 1, kind="boundary_plus",
                                                   threshold_confirmed=True))
                    elif expect == "reject":
                        samples.append(sample_spec(name="upload_oversize.xlsx", mime=XLSX_MIME,
                                                   source="workbook_stored",
                                                   byte_size=2 * 1024 * 1024,
                                                   kind="oversize_obvious",
                                                   threshold_confirmed=False))
                    else:
                        samples.append(sample_spec(name="upload_valid.xlsx", mime=XLSX_MIME,
                                                   source="workbook_stored",
                                                   kind="valid_workbook",
                                                   threshold_confirmed=False))
                for sample in samples:
                    checks.append({"type": "upload_file", "field": field,
                                   "row_label": upload_cfg.get("row_label") or "上传文件",
                                   "mode": upload_cfg.get("mode") or {},
                                   "data": str(assertion.get("data", "")),
                                   "expect": expect,
                                   "evidence_kind": ev_kind,
                                   "sample": sample,
                                   "rule": assertion.get("rule", ""),
                                   "rule_id": case.get("rule_id") or "",
                                   "case_id": case.get("identifier") or ""})
                    assertion_checks += 1
            elif kind == "visibility":
                # 条件显示由 visible_now / toggles 统一执行；能归属的已经归属，
                # 剩下的（如"不满足条件时应隐藏"）必须显式登记，绝不静默丢弃
                key = (case.get("identifier") or "", str(assertion.get("field") or field),
                       str(assertion.get("expect") or ""))
                if key in covered_visibility:
                    continue
                skipped.append({"field": field,
                                "reason": "条件显示组合未在计划中构建：%s" % (
                                    str(assertion.get("when") or "")[:48])})
            else:
                # 兜底：任何未支持的断言类型都必须显式登记，绝不静默丢弃
                # （否则会出现"既没执行、也没报告为未执行"的黑洞）
                skipped.append({"field": field,
                                "reason": f"本版不支持类型 {kind or '(none)'}"})

    checks.extend(toggle_checks)

    # 规则级负路径场景（P1-3 阶段 B）：与字段级检查**互相独立**——场景挂在"业务规则"上，
    # 不要求有对应字段定位器；配置由注册表 ``extra_config.negative_scenarios`` 提供。
    negative_scenarios, negative_scenario_rejects = parse_negative_scenarios(
        loc.get("negative_scenarios")
    )

    # 「列表可见」检查（2026-10-16）：与负路径场景**互相独立**，写请求发生在负路径阶段，
    # 本类检查在其**之后**于列表页执行（见执行器 runListChecks）。
    list_checks, list_check_rejects = parse_list_checks(loc.get("list_checks"))
    for item in list_checks:
        checks.append({
            "type": "list_visible",
            "name": item["name"],
            "value": item["value"],
            "expect": item["expect"],
            "list_url": item["list_url"] or str(loc.get("list_url") or ""),
            "search_placeholder": item["search_placeholder"],
            "max_pages": item["max_pages"],
            # rev148：行内操作 / 查看只读（配置了才生效，缺省走纯可见性）
            "action": item.get("action"),
            "readonly": item.get("readonly"),
            # rev162：按列筛行的选择器。**必须透传** —— 漏传时 value 为空串，
            # 而空串会匹配任意行（scan 恒 hits>0、动作点到第一行），TR-776 实测就是这么
            # 记成 pass 的：看日志像「点到了」，实际目标行根本没被选中。
            "row_pick": item.get("row_pick"),
            "pick_label": item.get("pick_label"),
            "rule": item["rule_ref"],
            "case_id": item["case_id"],
            "rule_id": item["rule_id"],
            # 配置驱动：与 default_selected 同口径，单独归类，避免被读成"本该有归属却没有"
            "source": "locator_config",
        })

    # 「导出」独立通道（rev148）：菜单 → 弹窗 → 时间段 → 导出中心 → 下载 → 列名。
    # 与 list_checks 同形：配置驱动、独立阶段、被拒不静默（[export-skip] 运行期可见）。
    export_checks, export_check_rejects = parse_export_checks(loc.get("export_checks"))
    for item in export_checks:
        checks.append({
            "type": "export_check",
            "name": item["name"],
            "value": item["value"],
            "list_url": item["list_url"] or str(loc.get("list_url") or ""),
            "menu": item["menu"],
            "label": item["label"],
            "validate_empty_time": item["validate_empty_time"],
            "validation_toast": item["validation_toast"],
            "pick_dates": item["pick_dates"],
            "confirm_label": item["confirm_label"],
            "export_center": item["export_center"],
            "filename_regex": item["filename_regex"],
            "expect_columns": item["expect_columns"],
            "download": item["download"],
            "max_wait_download_ms": item["max_wait_download_ms"],
            "rule": item["rule_ref"],
            "case_id": item["case_id"],
            "rule_id": item["rule_id"],
            "source": "locator_config",
        })

    # 「筛选区」独立通道（rev170）：下拉筛选 → 结果集列全量命中 → 重置还原。
    # 与 list_checks/export_checks 同形：配置驱动、独立阶段、被拒不静默（[filter-skip]）。
    filter_checks, filter_check_rejects = parse_filter_checks(loc.get("filter_checks"))
    for item in filter_checks:
        checks.append({
            "type": "filter_check",
            "name": item["name"],
            "list_url": item["list_url"] or str(loc.get("list_url") or ""),
            "control_placeholder": item["control_placeholder"],
            "option": item["option"],
            "option_labels": item["option_labels"],
            "expect_column": item["expect_column"],
            "expect_in": item["expect_in"],
            "reset": item["reset"],
            "reset_label": item["reset_label"],
            "reset_clear_values": item["reset_clear_values"],
            "rule": item["rule_ref"],
            "case_id": item["case_id"],
            "rule_id": item["rule_id"],
            "source": "locator_config",
        })

    # 「状态×操作矩阵」独立通道（rev173）：按状态定位行 → 断言行内操作集 →
    # 更多菜单枚举 → 确认框验证（点取消还原）。与 list/filter/export 同形。
    action_matrix_checks, action_matrix_rejects = parse_action_matrix_checks(
        loc.get("action_matrix_checks"))
    for item in action_matrix_checks:
        checks.append({
            "type": "action_matrix",
            "name": item["name"],
            "list_url": item["list_url"] or str(loc.get("list_url") or ""),
            "status_column": item["status_column"],
            "status_in": item["status_in"],
            "filter": item["filter"],
            "filter_option": item["filter_option"],
            "control_placeholder": item["control_placeholder"],
            "expect_row": item["expect_row"],
            "forbid_row": item["forbid_row"],
            "expect_menu": item["expect_menu"],
            "confirm": item["confirm"],
            "rule": item["rule_ref"],
            "case_id": item["case_id"],
            "rule_id": item["rule_id"],
            "source": "locator_config",
        })

    # N3（2026-10-18）：跳过项按"作用域"分账。`list` = 列表域（列头/筛选/分页），
    # `form` = 表单控件。两者修法完全不同（前者缺生成器，后者缺定位器配置），
    # 合并成一个 skipped_count 会让人按错误的路径去修。
    skipped_by_scope: dict[str, int] = {}
    for _item in skipped:
        _scope = str(_item.get("scope") or "form")
        skipped_by_scope[_scope] = skipped_by_scope.get(_scope, 0) + 1
    # A1（rev119，2026-10-20）：**字段级上传**的放行声明。
    #
    # 与场景通道的分工（实测缺口，2026-10-19 TR e76befd0 / 787e459a）：
    #   * 场景通道 ``negative_scenarios[].expect_accept.allow_upload`` 只对**规则级场景**
    #     生效（店铺真实落库那条已配，pass）；
    #   * 附件类用例（``file_size`` / ``file_format`` → ``upload_file`` 检查）走**字段级**
    #     通道，压根没有场景 ⇒ 历史上从来拿不到任何 multipart 放行规则，
    #     上传请求被写隔离 abort，表现为「附件断言恒 unverified / upload_blocked」。
    #
    # 这里给子功能一个显式声明口：``field_locators.upload_allow``（数组），
    # 结构/校验与场景的 ``allow_upload`` **完全复用** :func:`_parse_allow_upload`
    # （host 必填且至少两段、path_prefix 必须绝对路径、methods 缺省 POST）。
    # 默认空 ⇒ 与历史行为**逐字一致**（安全边界不因本改动放宽）。
    upload_allow_rules, upload_allow_error = _parse_allow_upload(loc.get("upload_allow"))
    upload_check_count = sum(1 for c in checks if c.get("type") == "upload_file")
    upload_policy_gap = ""
    if upload_allow_error:
        # 配了但写错：必须当场可见，否则会退化成"配了但上传照旧被拦"
        upload_policy_gap = f"upload_allow 配置非法：{upload_allow_error}"
    elif upload_check_count and not upload_allow_rules:
        # 有上传断言却没放行：不阻断编译（历史用例不该因此全红），但把后果写进计划，
        # 由编译回执与运行期 ``UPLOAD_POLICY`` 共同暴露。
        upload_policy_gap = (
            f"{upload_check_count} 条上传断言已生成，但未声明 upload_allow："
            "multipart 会被写隔离阻断，这些断言将恒为 unverified"
        )

    # rev137：字段检查**前置动作**。与 negative_scenarios 同为"行为配置"，
    # 但作用点在**客户端只读层**：某块字段区默认不渲染（如问卷的题目卡片要点了
    # 「新增题目」才出现），不先点一下，再正确的定位器也只会记 unverified。
    pre_actions, pre_action_rejects = parse_pre_actions(loc.get("pre_actions"))

    plan_extras = {
        # rev130（F-3）：把**编译期断言总数**带进运行期计划，让对账不必跨快照取值。
        # 对账恒等式（见 COVERAGE_RECONCILE）：
        #   planned = executable + skipped_no_locator + unexplained_plan
        #   emitted = executable + scenario_executed + unexplained_emitted
        # 旧实现只在 COMPILED_COVERAGE 里给 assertions、在 SUMMARY 里给 total，
        # 两个数分别属于不同快照，读者无法判断"计划→执行"是否有丢失（实测 TR-747：7/5/6/2）。
        "planned_assertions": int(manifest.get("assertion_count") or 0),
        # rev167：被 defaults 观测覆盖的 default 断言数（covered_defaults 的 size）。
        # 这类断言不走独立检查（default_selected 带 owners 代为取证），但对账的
        # planned 里有它们——不显式列出来，TR-784 那样的 unexplained_plan=4 假缺口
        # 就会一直存在（4 = 门店/商品/题目类型/是否必答 四条 default 断言）。
        "default_covered_assertions": len(covered_defaults),
        # rev134：别名真实命中流水（文档字段名 → 定位器键 → 解析次数）。
        # 空列表 = 没配别名或一条都没命中；配置方据此判断"别名到底起没起作用"。
        "field_alias_applied": [
            {"from": src, "to": dst, "resolved": times}
            for (src, dst), times in sorted(alias_hits.items())
        ],
        # rev134：**悬空别名**——别名配了但目标键不在定位器表里（拼错/改名后没同步）。
        # 这种配置在旧实现里完全静默：既不命中、也不告警，看起来像"配了没用"，
        # 实际是配错了。列出来才知道该改哪一条。
        "field_alias_dangling": [
            {"from": str(src), "to": str(dst)}
            for src, dst in sorted(aliases.items())
            if isinstance(dst, str) and dst not in fields
        ] if isinstance(aliases, dict) else [],
        "skipped_by_scope": skipped_by_scope,
        # rev137：前置动作随计划下发（渲染期进 FORM_PLAN.pre_actions，见 payload 白名单）。
        # ``pre_action_rejects`` 必须一起下发——被拒的动作若不进日志，配置方只会看到
        # "配了、检查还是 unverified"，又开始怀疑定位器。
        "pre_actions": pre_actions,
        "pre_action_rejects": pre_action_rejects,
        # 列契约：本期仅供诊断/配置校对（没有列表检查执行器），不产生检查。
        "columns": columns_contract,
        "column_rejects": column_rejects,
        # 运行期唯一化（A2 / rev124）：配置里写了 ``{{RUN_TAG}}`` 的字段，运行期换成
        # 本次运行的唯一值；``run_tag_gap`` 列出"真实落库但值固定"的字段（不可重复跑）。
        "run_tag": run_tag_usage(negative_scenarios),
        "run_tag_gap": run_tag_gaps(negative_scenarios),
        # 字段级上传放行（rev119）：由 render_form_executor 合并进 upload_policy
        "upload_allow": [rule.to_dict() for rule in upload_allow_rules],
        "upload_check_count": upload_check_count,
        #: 非空 ⇒ 本次产物存在「上传会被拦」的已知缺口（空串 = 无缺口）
        "upload_policy_gap": upload_policy_gap,
    }
    # `columns_legacy` 只是**解析用的载体**（把 custom_config 顶层的旧列键带进来），
    # 不该被注入到脚本的 FORM_PLAN.locators 里——渲染前摘掉，避免产物被配置原文撑大。
    plan_locators = (
        {k: v for k, v in loc.items() if k != "columns_legacy"}
        if "columns_legacy" in loc else loc
    )

    if not checks and not negative_scenarios:
        # 没有可执行检查时**不返回 None**：那样会把 skipped 现场一并丢掉
        #（实测：断言字段名与定位器表差一点 → 全部跳过 → 既没有 [plan-skip] 行，
        #  资产审计里也没有 skipped_assertions，只剩一句"无定位器"无从下手）。
        # 这里返回一个 executable=False 的计划：调用方据此**不注入执行器**（避免为 0 条检查
        # 打开表单、把环境问题变成硬失败），但审计与运行期现场仍然完整。
        if (not skipped and not negative_scenario_rejects
                and not list_check_rejects and not export_check_rejects
                and not filter_check_rejects and not action_matrix_rejects):
            return None
        return {"locators": plan_locators, "checks": [], "skipped": skipped,
                "check_count": 0, "skipped_count": len(skipped),
                "assertion_check_count": 0, "executable": False,
                "negative_scenarios": [], "negative_scenario_rejects": negative_scenario_rejects,
                "list_checks": [], "list_check_rejects": list_check_rejects,
                "export_check_rejects": export_check_rejects,
                "filter_check_rejects": filter_check_rejects,
                "action_matrix_rejects": action_matrix_rejects,
                **plan_extras}
    return {"locators": plan_locators, "checks": checks, "skipped": skipped,
            "check_count": len(checks), "skipped_count": len(skipped),
            "assertion_check_count": assertion_checks, "executable": True,
            # 只有场景、没有字段级检查时同样可执行：调用方据此注入执行器（见上面分支口径）
            "negative_scenarios": negative_scenarios,
            "negative_scenario_rejects": negative_scenario_rejects,
            "list_checks": list_checks,
            "list_check_rejects": list_check_rejects,
            "export_check_rejects": export_check_rejects,
            "filter_check_rejects": filter_check_rejects,
            "action_matrix_rejects": action_matrix_rejects,
            **plan_extras}


#: 只读表单校验执行器（TS）。占位符 @@FORM_PLAN@@ 由 render_form_executor 替换。
_FORM_EXECUTOR_TS = r"""
  // ===== 编译执行：表单规则断言（客户端层只读；「期望拒绝」未验证项进负路径提交层，2026-09-25 用户确认）=====
  // 编译断言数量随文档规则增长，需放宽单测超时（默认 30s 会中断）
  // rev71：全量批次（CS 96 条）单 test 断言执行实测需要 ~10 分钟，600s 不够
  //（TR-105 在 Member 定位等待上吃满 600s）。
  // rev116：预算由执行器下发（WEB_TEST_BUDGET_MS = runner 进程超时 - 30s），
  // 让 Playwright 自己先超时、能写报告；未注入时回退 30 分钟。
  test.setTimeout(Number(process.env.WEB_TEST_BUDGET_MS || 0) || 1800000);
  // 运行期自证：本次预算到底是多少、是执行器下发的还是回退默认值。
  console.log(`TEST_BUDGET ${JSON.stringify({ ms: Number(process.env.WEB_TEST_BUDGET_MS || 0) || 1800000, injected: !!process.env.WEB_TEST_BUDGET_MS, from: 'form_executor' })}`);
  const FORM_PLAN = @@FORM_PLAN@@;
  const FORM_PHONE = process.env.PR2_STORAGE_ACCOUNT_NO || '@@DEFAULT_PHONE@@';
  // ===== 运行期唯一化（A2 / rev124）=====
  // 配置里的 ``{{RUN_TAG}}`` 在**每次运行时**换成新值；FORM_PLAN 是编译期快照，
  // 若在编译期就替换，重新跑同一份产物会用回同一个编号 → 服务端唯一约束拒绝
  //（20003 编号已存在），期望受理通道再把它判成「疑似缺陷坐实」= 假缺陷。
  // 可用 ``WEB_RUN_TAG`` 钉住（复现历史场次时用），否则按本地时间生成。
  const RUN_TAG_INFO = (() => {
    const pinned = String((process.env as any).WEB_RUN_TAG || '').trim();
    if (pinned) return {tag: pinned, source: 'env'};
    const d = new Date();
    const p = (n: number) => String(n).padStart(2, '0');
    const rnd = Math.random().toString(36).slice(2, 5).toUpperCase().padEnd(3, '0');
    return {
      tag: `${p(d.getMonth() + 1)}${p(d.getDate())}${p(d.getHours())}`
         + `${p(d.getMinutes())}${p(d.getSeconds())}${rnd}`,
      source: 'generated',
    };
  })();
  const RUN_TAG = RUN_TAG_INFO.tag;
  //: 占位符替换：只在明确写入 ``{{RUN_TAG}}`` 的字段上发生，其余值逐字不变。
  const substRunTag = (v: any) =>
    String(v == null ? '' : v).split('{{RUN_TAG}}').join(RUN_TAG);
  //: 用到唯一化的字段逐条落日志 —— 结论要能对回"这次提交的是哪个编号"。
  const runTagUsed: Array<{field: string; value: string}> = [];
  const runTagValue = (field: string, raw: any): string => {
    const original = String(raw == null ? '' : raw);
    const out = substRunTag(original);
    if (out !== original) {
      runTagUsed.push({field, value: out});
      console.log('RUN_TAG_USED ' + JSON.stringify({field, value: out, tag: RUN_TAG}));
    }
    return out;
  };
  //: 计划期就发现的缺口（真实落库场景值固定）：先报出来，别等到 20003 再猜。
  for (const gap of (FORM_PLAN.run_tag_gap || []).slice(0, 12)) {
    console.log('RUN_TAG_GAP ' + JSON.stringify(gap));
  }
  if ((FORM_PLAN.run_tag || {}).enabled || RUN_TAG_INFO.source === 'env') {
    console.log('RUN_TAG ' + JSON.stringify({
      tag: RUN_TAG, source: RUN_TAG_INFO.source,
      fields: (FORM_PLAN.run_tag || {}).fields || [],
      steps: (FORM_PLAN.run_tag || {}).steps || 0,
    }));
  }
  //: 计划期被跳过的断言（例如字段在定位器表里找不到）**同时**输出可机读现场：
  //: 这类断言不会进入执行，运行期不会有 [observation]；没有这一行就只能看到
  //: summary 里的 `skipped_by_reason: {无定位器: 1}`，无法判断该怎么改配置。
  for (const skip of (FORM_PLAN.skipped || []).slice(0, 20)) {
    console.log('[plan-skip] ' + JSON.stringify(skip));
  }
  //: 「列表可见」检查被计划期拒绝的条目**运行期可见**（与 [negative-skip] 同口径）：
  //: 否则"配了但一条都没跑"只能靠人比对配置与日志。
  for (const rej of (FORM_PLAN.list_check_rejects || []).slice(0, 20)) {
    console.log('[list-skip] ' + JSON.stringify(rej));
  }
  type CheckOutcome = 'pass' | 'unverified' | 'blocked';
  const formResults: Array<{name: string; outcome: CheckOutcome; observed: string}> = [];
  //: 当前检查所属用例的规则号与用例标识（由循环逐条设置），用于把结果落到具体用例上
  let currentRuleId = '';
  let currentCaseId = '';
  //: 检查来源（如 locator_config=来自定位器配置）——台账据此把"配置驱动、本就没有对应用例"
  //: 与"本该有归属却缺失"区分开，避免每次运行都报一条假告警。
  let currentSource = '';
  const rec = (name: string, outcome: CheckOutcome, observed: string) => {
    formResults.push({name, outcome, observed, ruleId: currentRuleId, caseId: currentCaseId});
    console.log('COMPILED_ASSERTION ' + JSON.stringify({
      name, outcome, observed, rule_id: currentRuleId, case_id: currentCaseId,
      source: currentSource,
    }));
  };
  //: 一条计划级检查可能同时是**多条用例**的证据（例如"默认选中=企业储值充值"
  //  同时验证了变动类型用例的默认值 + 上传文件用例的前提条件）。此时按 owner 逐条输出，
  //  避免出现"执行了并通过了，却没有任何用例拿到证据"的黑洞。
  const ownersOf = (check: any): Array<{case_id: string; rule_id: string}> => {
    const list = Array.isArray(check.owners) ? check.owners : [];
    if (list.length) return list;
    return [{case_id: check.case_id || '', rule_id: check.rule_id || ''}];
  };
  const recAll = (check: any, name: string, outcome: CheckOutcome, observed: string) => {
    currentSource = String((check && check.source) || '');
    for (const owner of ownersOf(check)) {
      currentRuleId = owner.rule_id || '';
      currentCaseId = owner.case_id || '';
      rec(name, outcome, observed);
    }
  };

  //: P4：定位失败的「确定性现场」——不依赖 Playwright 报错快照（复核实测：快照的
  //: 页归属可能错位，URL 显示 A 应用、快照却是 B 页面，按图索骥会被带偏）。
  //: 定位落空时把「试过的定位器 + 当前作用域可见控件清单（tag/type/placeholder/
  //: maxlength/aria/text，截断脱敏）+ page/frame 清单 + activeElement」落成一行
  //: [observation] JSON，排障一次定位，不需要复跑。零业务词。
  async function collectLocatorObservation(modal: any, tried: string[]): Promise<any> {
    const observation: any = { tried, pages: [], frames: [], active: null, controls: [] };
    try {
      const pgs = page.context().pages();
      for (let i = 0; i < pgs.length; i += 1) {
        observation.pages.push({
          index: i,
          url: String(pgs[i].url() || '').slice(0, 80),
          title: String(await pgs[i].title().catch(() => '') || '').slice(0, 40),
        });
      }
    } catch { /* pages 清单不可用时留空 */ }
    try {
      const frs = page.frames();
      for (let i = 0; i < frs.length; i += 1) {
        observation.frames.push({ index: i, url: String(frs[i].url() || '').slice(0, 80) });
      }
    } catch { /* frames 清单不可用时留空 */ }
    try {
      observation.active = await page.evaluate(() => {
        const el = document.activeElement as any;
        if (!el || el === document.body) return null;
        const cut = (s: any, n: number) => String(s == null ? '' : s).slice(0, n);
        return {
          tag: el.tagName, type: cut(el.getAttribute && el.getAttribute('type'), 20),
          placeholder: cut(el.getAttribute && el.getAttribute('placeholder'), 40),
          aria: cut(el.getAttribute && el.getAttribute('aria-label'), 40),
        };
      }).catch(() => null);
    } catch { /* activeElement 不可用时留空 */ }
    const dumpFn = (root: any, limit: number) => {
      const cut = (s: any, n: number) => {
        const t = String(s == null ? '' : s);
        return t.length > n ? t.slice(0, n) + '…' : t;
      };
      const els = Array.from(root.querySelectorAll('input, textarea, select, button, [contenteditable="true"]'));
      const out: any[] = [];
      for (const el of els as any[]) {
        if (out.length >= limit) break;
        const visible = !!(el.offsetWidth || el.offsetHeight) && el.getClientRects().length > 0;
        if (!visible) continue;
        const row: any = {
          tag: String(el.tagName || '').toLowerCase(),
          type: cut(el.getAttribute && el.getAttribute('type'), 20),
          placeholder: cut(el.getAttribute && el.getAttribute('placeholder'), 40),
          aria: cut(el.getAttribute && el.getAttribute('aria-label'), 40),
        };
        const ml = typeof el.maxLength === 'number' && el.maxLength >= 0 ? el.maxLength : undefined;
        if (ml !== undefined) row.maxLength = ml;
        if (String(el.tagName || '').toLowerCase() === 'button') row.text = cut((el.textContent || '').trim(), 20);
        out.push(row);
      }
      return out;
    };
    try {
      if (modal && (await modal.count().catch(() => 0))) {
        observation.controls = await modal.evaluate(dumpFn, 30).catch(() => []);
      }
      if (!observation.controls.length) {
        observation.controls = await page.evaluate(dumpFn, 30).catch(() => []);
      }
    } catch { /* 控件清单不可用时留空 */ }
    return observation;
  }
  //: 定位落空统一出口：采集现场并输出一行 JSON（供台账/排障直接消费）。
  async function reportLocatorMiss(where: string, check: any, modal: any): Promise<void> {
    const def = (FORM_PLAN.locators.fields || {})[(check && check.field) || ''] || {};
    // rev138：tried 里必须写出**真正用过**的选择器。旧实现只回显 `placeholder_hint`
    //（本 SUT 未配该键 ⇒ 恒为 ''），而执行时用的是 `selector_hint`——于是现场行
    // 看起来像"没配任何 hint"，把排障引去补 hint，而 hint 本来就是对的（TR-754 实测）。
    const tried = [
      JSON.stringify({ locator: 'selector_hint', field: check.field, kind: def.kind || '',
                       hint: def.selector_hint || '' }),
      JSON.stringify({ locator: 'placeholder_hint', field: check.field, kind: def.kind || '',
                       hint: def.placeholder_hint || '' }),
      JSON.stringify({ locator: 'form_item_label', field: check.field }),
    ];
    const observation = await collectLocatorObservation(modal, tried);
    console.log('[observation] ' + JSON.stringify({
      where, field: (check && check.field) || '', rule_id: (check && check.rule_id) || '',
      case_id: (check && check.case_id) || '',
      // rev138：定位发生在哪一层 —— 'form'（表单容器）还是 'page'（整页）。
      // 这两个值决定"修选择器"还是"修 scope"，不写出来只能靠猜。
      scope: String(def.scope || 'form'),
      ...observation,
    }));
  }
  //: 提交窗口的**现场采集**（2026-10-16 补）。
  //: 动机（实测 TR-402）：规则级场景提交后没拿到目标响应时，旧实现只输出一句
  //: 「提交后窗口内未捕获到目标接口的请求」——分不清是「客户端校验拦住了」
  //: 「请求发到了别的地址」还是「按钮没点对」。而这三者的处置完全不同：
  //: 前两者要改配置/探针，后者是平台定位缺陷。没有现场就只能猜。
  //: 这里一次采集三类证据：前端校验文案（含 toast）、表单错误项、当前 URL。
  async function collectSubmitScene(): Promise<{
    url: string; form_errors: Array<{field: string; error: string}>; toasts: string[];
  }> {
    try {
      const body = anyScope.locator('body').first();
      if (!(await body.count().catch(() => 0))) {
        return {url: String(page.url()).slice(0, 140), form_errors: [], toasts: []};
      }
      return await body.evaluate(() => {
        const norm = (s: any) => String(s || '').replace(/\s+/g, ' ').trim();
        const formErrors: Array<{field: string; error: string}> = [];
        document.querySelectorAll('.ant-form-item').forEach((it: any) => {
          // antd v3 与 v4 的字段错误类名不同（实测 2026-10-16，TR-409/TR-412）：
          //   v3 = ``.ant-form-explain``（本 SUT 正是 v3，只读 v4 的
          //        ``.ant-form-item-explain`` 会永远读成"没有表单错误"，
          //        于是「Please set latitude and longitude」这条真实拦截原因
          //        被埋掉，Conclusion 被带成"点了没反应"）；
          //   v4 = ``.ant-form-item-explain``。两者都读，取先命中的。
          const err = norm((it.querySelector('.ant-form-item-explain, .ant-form-explain') as any)?.innerText || '');
          const hasErrorCls = /has-error/.test(String(it.className));
          if (!err && !hasErrorCls) return;
          formErrors.push({
            field: norm(it.querySelector('.ant-form-item-label')?.innerText || ''),
            error: err || '(has-error，错误文案未渲染)',
          });
        });
        const toasts: string[] = [];
        document.querySelectorAll(
          '.ant-message-notice-content, .ant-notification-notice-message,'
          + ' .ant-notification-notice-description, .ant-form-item-explain-error',
        ).forEach((n: any) => {
          const t = norm(n.innerText);
          if (t && toasts.indexOf(t) < 0) toasts.push(t.slice(0, 160));
        });
        return {url: String(location.href).slice(0, 140),
                form_errors: formErrors.slice(0, 15), toasts: toasts.slice(0, 10)};
      });
    } catch {
      return {url: String(page.url()).slice(0, 140), form_errors: [], toasts: []};
    }
  }
  const anyScope = appScope as any;

  //: rev71e：单项检查的**操作级超时**。实测 TR-106：required_error:Member 的 fill
  //: 在 frame 解析阶段卡满整个用例预算（1200s），级联把后续 7 项全部拖成 blocked。
  //: 任何 fill/click/blur 超 30s 即按当前检查记 blocked（带诊断），不再拖垮整批。
  const OP_T = 30000;
  //: blocked 现场诊断：列出上下文全部 page 的 URL 与 iframe 数量——
  //: 用于定位"作用域根在另一个 page（CRM 新开页签）导致 frame 永不解析"这类跨页问题。
  async function pageDiag(pg: any): Promise<string> {
    try {
      const pages: any[] = pg.context().pages();
      const parts: string[] = [];
      for (let i = 0; i < pages.length; i++) {
        const p = pages[i];
        let desc = `#${i} ${String(p.url()).slice(0, 90)}`;
        try { desc += ` iframes=${await p.locator('iframe').count()}`; } catch { desc += ' iframes=?'; }
        try { if (p.isClosed()) desc += ' [closed]'; } catch { /* ignore */ }
        parts.push(desc);
      }
      return parts.join(' ;; ');
    } catch (e: any) {
      return `diag-failed: ${String(e).slice(0, 80)}`;
    }
  }

  // 打开表单：**由定位器表的 flow 驱动**，不再硬编码储值弹窗流程。
  // - recharge_modal（默认）：先按手机号筛选 → 点「企业充值/退款」→ 等弹窗
  // - 其他（如 CS 的整页 Create 表单）：直接点 form_button → 等页面容器可见
  async function openForm(pg: any = page, scope: any = anyScope) {
    const flow = String(FORM_PLAN.locators.flow || 'recharge_modal');
    // T-04 修复（2026-10-07，实测 TR-172 / 单号 jf-20260122392-01-2963）：
    // 交互与定位都发生在 `scope` 所属文档，但 `direct_url` 原先固定导航 `pg`。
    // 当应用在**独立标签页**时（如"门户卡片 → 新开标签页"的交易云），`pg` 与 `scope` 属不同文档：
    // 于是"导航到 A 页、却在 B 页等元素" ⇒ 确定性 15s 超时；且失败快照取自 `pg`（显示页面是对的），
    // 极易被误判成被测系统缺陷。
    // 规则：scope 本身是 Page（有 goto）→ direct_url 与后续等待都用它；
    //       scope 是 FrameLocator（CS 的 CRM iframe 流程）→ 保持原样用 pg，不动既有可用路径。
    // 可用 `direct_url_page: "primary" | "app"` 显式覆盖（缺省按上述规则自动判定）。
    const scopePage = String((FORM_PLAN.locators as any).direct_url_page || '') === 'primary'
      ? pg
      : ((typeof (scope as any).goto === 'function') ? scope : pg);
    if (flow === 'recharge_modal' && FORM_PLAN.locators.filter_phone_placeholder) {
      const phoneInput = scope.getByPlaceholder(FORM_PLAN.locators.filter_phone_placeholder).first();
      await phoneInput.fill(FORM_PHONE, {timeout: OP_T});
      await scope.getByRole('button', {name: FORM_PLAN.locators.search_button}).first().click({timeout: 15000});
      await pg.waitForTimeout(3500);
    }

    let opened = false;

    // 可选：direct_url（rev73：RetroClaim/GoodWill 等独立整页表单）——
    // 直接驱动 CRM iframe 跳到目标 hash 路由（免菜单逐层导航），随后按 modal 等待渲染。
    if ((FORM_PLAN.locators as any).direct_url) {
      const frames = scopePage.frames();
      const fr = frames.find((f: any) => f.parentFrame() &&
        /mkt\.uat|eatizen|saas-/.test(f.url())) || null;
      const base = fr ? fr.url() : scopePage.url();
      let target = String((FORM_PLAN.locators as any).direct_url);
      if (target.startsWith('#')) {
        const u = new URL(base);
        target = u.origin + u.pathname + target;
      }
      if (fr) {
        await fr.goto(target, {timeout: 30000, waitUntil: 'domcontentloaded'}).catch(() => undefined);
      } else {
        await scopePage.goto(target, {timeout: 30000, waitUntil: 'domcontentloaded'}).catch(() => undefined);
      }
      await scopePage.waitForTimeout(5000);
      // T-04：把"导航所用页面"与"定位所在页面"同时留证，避免下次再被快照误导。
      console.log('DIRECT_URL_DIAG ' + JSON.stringify({
        target: String(target).slice(0, 130),
        navigated_page: String(scopePage.url()).slice(0, 130),
        primary_page: String(pg.url()).slice(0, 130),
        same_page: scopePage === pg,
        scope_kind: (typeof (scope as any).goto === 'function') ? 'Page' : 'FrameLocator',
      }));
      opened = true;
    }

    // 可选：list_row_link（rev79：timeline 子页流程从列表点首行链接进详情——
    // 行链接文本是工单标题（不固定），无法用语义入口名匹配）
    //
    // rev96：**必须等数据行真的渲染出来再点**。实测（2026-10-13 CS timeline）：
    // 列表就绪判据只判"表格/Total 可见"（实测 130ms 即通过），此时 tbody 可能还没有
    // 数据行 ⇒ `rowLink.count()` = 0 ⇒ 静默不点 ⇒ 后续 Add 在列表页找不到 ⇒
    // `opened=false` 硬失败「无法打开表单」。
    // 对照证据：同一 `timeline_subpage` flow 的 28 条资产里 24 条正常完成，
    // 只有 4 条报该错（偶发）；只读探针证明选择器链路本身可用
    //（列表 20 行 / Total 105 → 点行链接进 `#/cs/case/edit` → Add 按钮可见 → 菜单含 Note）。
    // rev96b：留证行改读 **frame 级 URL**。实测（TR-275/280/282）：CS 的 CRM 跑在 iframe 内，
    // `pg.url()` / `scopePage.url()` 恒为外层企业控制台 URL，于是 `navigated` **恒 false**
    //—— 无论表单到底开没开。这条"看起来像证据、实则永远一个样"的行会误导排障，
    // 因此改为遍历 `pg.frames()` 取带 CS 路由的那一帧（`#/cs/…`）来做前后对比。
    // 纯诊断改动：不改变任何交互，只改这行 console.log 的取值来源。
    if ((FORM_PLAN.locators as any).list_row_link) {
      const appFrameUrls = (): string[] => {
        try {
          return (pg.frames() as any[])
            .map((f: any) => String(f.url()))
            .filter((u: string) => /(#\/cs\/|\/cs\/case\/|case\/(list|edit))/.test(u))
            .map((u: string) => u.slice(0, 150));
        } catch { return []; }
      };
      const beforeFrames = appFrameUrls();
      for (let attempt = 1; attempt <= 3; attempt++) {
        try {
          await scope.locator('.ant-table-tbody tr:has(a)').first()
            .waitFor({state: 'visible', timeout: attempt === 1 ? 10000 : 5000});
        } catch { /* 本轮仍未见到带链接的数据行 → 下一轮重试 */ }
        try {
          const rowLink = scope.locator('.ant-table-tbody tr:first-child a, .ant-table-row a').first();
          if (await rowLink.count()) {
            await rowLink.click({timeout: 12000});
            await pg.waitForTimeout(4000);
            break;
          }
        } catch { /* 行链接点击失败由后续表单打开失败统一兜底 */ }
        await pg.waitForTimeout(1500);
      }
      // 现场留证：进入详情页会把 frame 路由从 `#/cs/case/list` 换成 `#/cs/case/edit?id=…`。
      // 下次再出现"无法打开表单"时，这一行能直接区分"没进详情"与"进了但 Add 找不到"。
      const afterFrames = appFrameUrls();
      console.log('[rowlink] ' + JSON.stringify({
        before: beforeFrames, after: afterFrames,
        navigated: JSON.stringify(beforeFrames) !== JSON.stringify(afterFrames),
        reached_edit: afterFrames.some((u) => /case\/edit/.test(u)),
        frames_total: (() => { try { return (pg.frames() as any[]).length; } catch { return -1; } })(),
      }));
    }

    // 可选：toolbar_modal（O10/rev98：工具条按钮 → 弹窗字段 → 提交——
    // 与 create_page 同构但**入口不同**：必须先选中列表行，工具条动作才有目标。
    // 形态实例（67197，WSF-48576977/cs-case-email）：
    //   勾选列表行 → 工具条「Email a Link」→ 弹窗 #emails/#remark → Send → POST /cs/cases/send-email。
    // 新增定位器键（均挂在定位器表上，与 fields/form_button 平级）：
    //   toolbar_button : 工具条按钮名候选数组（button → link → 精确文本三级兜底，同 form_button）；
    //   row_selector   : 数据行选择器（缺省 .ant-table-tbody tr）；
    //   row_pick       : "checkbox"（缺省：勾选行内 input[type=checkbox]，找不到则点行兜底）| "row_click"；
    //   row_index      : 行下标（缺省 0）。
    // 行就绪判据沿用 rev96 教训：必须等数据行**真实渲染**（只判表头/Total 会点到空表）。
    if (flow === 'toolbar_modal') {
      const rowSel = String((FORM_PLAN.locators as any).row_selector || '.ant-table-tbody tr');
      let rowReady = false;
      for (let attempt = 1; attempt <= 3 && !rowReady; attempt++) {
        try {
          await scope.locator(rowSel).first()
            .waitFor({state: 'visible', timeout: attempt === 1 ? 10000 : 5000});
          rowReady = (await scope.locator(rowSel).count()) > 0;
        } catch { /* 本轮仍未见到数据行 → 下一轮重试 */ }
        if (!rowReady) await pg.waitForTimeout(1500);
      }
      if (rowReady) {
        const rowIdx = Math.max(0, Number((FORM_PLAN.locators as any).row_index ?? 0) || 0);
        try {
          if (String((FORM_PLAN.locators as any).row_pick || 'checkbox') === 'row_click') {
            await scope.locator(rowSel).nth(rowIdx).click({timeout: 8000});
          } else {
            // rev98c（实测 TR-329）：**不能直接点 input**——antd 的 checkbox input 被
            // .ant-checkbox-inner 方框覆盖（opacity:0 且在底层），Playwright 命中检测
            // （elementFromPoint 必须是元素或其子孙）永远不过 ⇒ click 超时后被静默吞掉、
            // 行没选上，点 Email a Link 只会弹"请先选择工单"toast。
            // 正解（与 cs_login 实战辅助同思路）：点可见的 .ant-checkbox 容器 →
            // isChecked 自证 → 未选中再用合成 click 兜底（js el.click 不走过顶命中）。
            const rowLoc = scope.locator(rowSel).nth(rowIdx);
            const boxInput = rowLoc.locator('input[type=checkbox]').first();
            if (await boxInput.count()) {
              if (!(await boxInput.isChecked().catch(() => false))) {
                const wrap = rowLoc.locator('.ant-checkbox').first();
                if (await wrap.count()) {
                  await wrap.click({timeout: 8000}).catch(() => undefined);
                }
                if (!(await boxInput.isChecked().catch(() => false))) {
                  await boxInput.evaluate((el: any) => el.click()).catch(() => undefined);
                }
              }
            } else {
              await rowLoc.click({timeout: 8000});
            }
          }
          await pg.waitForTimeout(800);
        } catch { /* 选行失败由后续「表单打不开」统一兜底（如实报错，不装作通过） */ }
      } else {
        console.log(`CS_FORM_DIAG toolbar_modal: 未等到数据行（row_selector=${rowSel}）`);
      }
      for (const name of ((FORM_PLAN.locators as any).toolbar_button || [])) {
        try {
          const btn = scope.getByRole('button', {name: new RegExp(name)}).first();
          if (await btn.count()) {
            await btn.click({timeout: 12000});
            opened = true;
            break;
          }
        } catch { /* 尝试下一个候选名 */ }
      }
      if (!opened) {
        for (const name of ((FORM_PLAN.locators as any).toolbar_button || [])) {
          try {
            const lnk = scope.getByRole('link', {name: new RegExp(name)}).first();
            if (await lnk.count()) {
              await lnk.click({timeout: 12000});
              opened = true;
              break;
            }
          } catch { /* 尝试下一个候选名 */ }
        }
      }
      if (!opened) {
        for (const name of ((FORM_PLAN.locators as any).toolbar_button || [])) {
          try {
            const txt = scope.getByText(new RegExp(`^\\s*${name}\\s*$`)).first();
            if (await txt.count() && await txt.isVisible().catch(() => false)) {
              await txt.click({timeout: 12000});
              opened = true;
              break;
            }
          } catch { /* 尝试下一个候选名 */ }
        }
      }
    }

    // 可选：先进入「详情/编辑」页（timeline 类流程要从列表点进某一单）
    for (const name of (FORM_PLAN.locators.entry_labels || [])) {      try {
        const entry = scope.getByRole('button', {name: new RegExp(name)}).first();
        if (await entry.count()) {
          await entry.click({timeout: 12000});
          await pg.waitForTimeout(3000);
          break;
        }
        const link = scope.getByRole('link', {name: new RegExp(name)}).first();
        if (await link.count()) {
          await link.click({timeout: 12000});
          await pg.waitForTimeout(3000);
          break;
        }
      } catch { /* 尝试下一个入口候选 */ }
    }

    // 说明：form_button 缺失时**不得**直接崩溃（实测 TR-163：注册表只带 fields →
    // `FORM_PLAN.locators.form_button is not iterable` 让整条运行硬失败，且看不出是配置缺失）。
    // 这里容错为"没有候选入口"，由后续 opened 判定给出可读的未验证结论。
    const formButtonNames = FORM_PLAN.locators.form_button || [];
    const toolbarNames = (FORM_PLAN.locators as any).toolbar_button || [];
    for (const name of formButtonNames) {
      try {
        const btn = scope.getByRole('button', {name: new RegExp(name)}).first();
        if (await btn.count()) {
          await btn.click({timeout: 12000});
          opened = true;
          break;
        }
      } catch { /* 尝试下一个候选名 */ }
    }
    // rev73："+ Add" 这类入口是 link/span 而非 button——role 匹配落空时按文本兜底
    if (!opened) {
      for (const name of formButtonNames) {
        try {
          const lnk = scope.getByRole('link', {name: new RegExp(name)}).first();
          if (await lnk.count()) {
            await lnk.click({timeout: 12000});
            opened = true;
            break;
          }
        } catch { /* 尝试下一个候选名 */ }
      }
    }
    if (!opened) {
      for (const name of formButtonNames) {
        try {
          const txt = scope.getByText(new RegExp(`^\\s*\\+?\\s*${name}\\s*$`)).first();
          if (await txt.count() && await txt.isVisible().catch(() => false)) {
            await txt.click({timeout: 12000});
            opened = true;
            break;
          }
        } catch { /* 尝试下一个候选名 */ }
      }
    }
    // rev73：menu_item——点开 Add 下拉后选择具体条目（Timeline 的 Post/Task/Phone Call/Note）
    for (const mi of ((FORM_PLAN.locators as any).menu_item || [])) {
      try {
        const item = scope.locator(
          `.ant-dropdown-menu-item:has-text("${mi}"), .ant-dropdown-menu-submenu-title:has-text("${mi}")`,
        ).first();
        if (await item.count()) {
          await item.click({timeout: 10000});
          await pg.waitForTimeout(3000);
          break;
        }
      } catch { /* 尝试下一个菜单项 */ }
    }
    // 表单打不开属定位器/页面结构问题 → 硬失败（不装作通过）
    expect(
      opened,
      (formButtonNames.length + toolbarNames.length) === 0
        ? `无法打开表单：定位器未配置表单入口（form_button/toolbar_button 均为空）——请检查该模板注册/配置，`
          + `而不是把结论算成"页面没有入口"（flow=${flow}）`
        : `无法打开表单（flow=${flow}，定位器可能失效）`,
    ).toBeTruthy();
    const modal = scope.locator(FORM_PLAN.locators.modal).first();
    try {
      await modal.waitFor({state: 'visible', timeout: 15000});
    } catch (e) {
      // T-04：原报错只有"等待某个选择器超时"，而失败快照取自 `pg`——
      // 当导航页与定位页不是同一个文档时，快照会显示"页面明明是对的"，误导排障。
      // 这里把两个页面的 URL 与上下文页面清单一并写进错误信息。
      const scopeUrl = (typeof (scope as any).goto === 'function')
        ? String((scope as any).url())
        : 'FrameLocator(非 Page)';
      throw new Error(
        `无法打开表单容器：等待 ${String(FORM_PLAN.locators.modal)} 可见超时（flow=${flow}，定位器可能失效）。`
        + `定位所在文档=${scopeUrl.slice(0, 130)}；`
        + `direct_url 导航所用页面=${String(scopePage.url()).slice(0, 130)}；`
        + `测试主页面 pg=${String(pg.url()).slice(0, 130)}；`
        + `上下文页面=[${await pageDiag(pg)}]`,
      );
    }
    await pg.waitForTimeout(700);
    console.log(`CS_FORM_DIAG flow=${flow} pg=${String(pg.url()).slice(0, 90)} pages=[${await pageDiag(pg)}]`);
    return modal;
  }

  //: 读取控件当前值。**实测坑**：contenteditable 上的 `inputValue()` 不可靠
  //: （合成页实测返回长度 226，而 textContent 是 500），富文本必须读 textContent。
  //: 同 fillableDeep 的理由：先 `count()` 做无等待判定——控件不存在时不要挂到默认超时。
  async function controlValue(locator: any): Promise<string> {
    try {
      if ((await locator.count().catch(() => 0)) === 0) return '';
      const tag = String(await locator.evaluate((el: any) => (el.tagName || '').toLowerCase()));
      if (tag === 'input' || tag === 'textarea') {
        return String(await locator.inputValue({timeout: 5000}));
      }
      return String(await locator.evaluate((el: any) => el.textContent || ''));
    } catch {
      return '';
    }
  }

  function fieldInput(modal: any, field: string, tag: string) {
    return modal.locator(`.ant-form-item:has-text("${field}") ${tag}`).first();
  }

  //: rev138：字段级**作用域**声明（``fields.<字段>.scope = "page"``，缺省＝表单容器）。
  //:
  //: 动机（实测 2026-10-20 TR-754 + 探针）：问卷的题目卡片渲染在
  //: ``<form class="ant-form">`` **之外**（`form.contains(card) === false`；
  //: 同一选择器 全文档命中 1 / 表单内命中 0）。此时选择器再准也恒为 0 ——
  //: 与 rev137 的 `pre_actions` 是两个不同故障：
  //:   * `pre_actions` 修的是"区域**根本没渲染**"（要点一下才出现）；
  //:   * `scope` 修的是"渲染了，但不在表单容器这一层找"。
  //: 两个都缺时，日志都是同一句"未定位到输入框"，这是它俩长期被混为一谈的原因。
  //: 为什么不做成"把 modal 放宽到整页"：`modal` 同时是默认值/条件显示/单选
  //: `:has-text` 的匹配范围，放宽会让这些检查跨区域误命中；作用域必须**按字段**声明。
  //: 作用域声明只在**第一次真正用到**时打一行自证（避免每字段刷屏）：
  //: "配了 scope=page" 与 "scope 被静默忽略" 必须能分开（与 alias/run_tag 同一纪律）。
  const scopeLogged = new Set<string>();
  function scopeOf(modal: any, def: any) {
    const scope = String((def || {}).scope || '').trim().toLowerCase();
    if (!scope || scope === 'form') return modal;
    if (scope !== 'page') {
      if (!scopeLogged.has(scope)) {
        scopeLogged.add(scope);
        console.log('LOCATE_SCOPE_INVALID ' + JSON.stringify(
          {scope, accepted: ['form', 'page'], fell_back_to: 'form'}));
      }
      return modal;
    }
    if (!scopeLogged.has('page')) {
      scopeLogged.add('page');
      console.log('LOCATE_SCOPE ' + JSON.stringify({scope: 'page', resolved: 'anyScope'}));
    }
    return anyScope;
  }

  //: rev71：定位器表里 9/24 probe 实测的 selector_hint（如 CS New Case 表单的
  //: #memberLabel/#title/#brand）是唯一可信的控件定位——该表单不是标准
  //: .ant-form-item 结构，has-text 匹配永远落空，fill 会等满整个用例预算
  //:（实测 TR-105 Member 项吃满 600s 后级联 blocked 8 项）。hint 优先，缺失时回退。
  //: 定位覆盖**只有 `selector_hint` 生效**（值就是 CSS 选择器）。
  //:   * `placeholder_hint` 仅作诊断信息（会出现在 [observation] 里），**不参与定位**——
  //:     实测 2026-09-29：把 placeholder_hint 改成不存在的文本，断言照样通过，
  //:     因为 kind=input 走的是 `.ant-form-item:has-text("<字段名>") input` 兜底路径。
  //:   * 想制造/修复"定位不到"：改 `selector_hint`（给个不匹配的选择器）或改 `kind`
  //:     （例如页面是普通 input 却写成 richtext → 只有在 [contenteditable] 里找 → 落空）。
  function hintSel(def: any): string | null {
    const h = String((def || {}).selector_hint || '').trim();
    return h ? h : null;
  }
  function hintLocator(modal: any, def: any) {
    const h = hintSel(def);
    // rev138：hint 是**字段自身**的声明，作用域也必须跟着字段走——
    // 否则"选择器对了、容器不对"只能靠人肉比对（TR-754 的四条 unverified 正是如此）。
    return h ? scopeOf(modal, def).locator(h).first() : null;
  }

  //: rev71f：hint 元素未必是可输入控件——CS 表单的 Member 字段 hint 是 `#memberLabel`
  //:（实测 TR-106/TR-107：那是标签/容器 div，fill 等待 editability 永不满足，
  //: 无超时版把整批拖死）。可输入性判定 + 自动下钻找内部 input/textarea/contenteditable。
  //:
  //: 2026-09-29（TR-154 实测）：`locator.evaluate(fn, {timeout: 5000})` 的第二个参数是
  //: **传给页面函数的实参**，不是超时选项；而 evaluate 会按默认超时（本用例 30 分钟）等元素出现。
  //: 于是"selector_hint 写成不存在的选择器"这种最常见的配置错误，会把作业一直挂到 runner 的
  //: 600s 上限，**stdout 全空**（连 [observation] 都来不及输出）——现场反而丢了。
  //: 现在先做**无等待的存在性判定**：count() 不自动等待，为 0 立即返回，
  //: 由调用方按"未定位到输入框"记 unverified 并输出 [observation]。
  const FILLABLE = ['input', 'textarea'];
  async function fillableDeep(loc: any): Promise<any> {
    try {
      if ((await loc.count().catch(() => 0)) === 0) return loc;
      const tag = String(await loc.evaluate((el: any) => (el.tagName || '').toLowerCase()).catch(() => ''));
      const editable = await loc.getAttribute('contenteditable', {timeout: 3000}).catch(() => null);
      if (FILLABLE.includes(tag) || editable === 'true') return loc;
      const inner = loc.locator('input:not([type=hidden]), textarea, [contenteditable="true"]').first();
      if (await inner.count()) return inner;
    } catch { /* 保持原定位器，由调用方按 blocked 处理 */ }
    return loc;
  }

  //: 字段控件定位：按定位器表里的 kind 选择 input / textarea / 富文本编辑器。
  //: timeline 的 Subject/Description 常是富文本（[contenteditable]），
  //: 用 input/textarea 定位会落空 → 会被判"未定位到输入框"。
  function fieldControl(modal: any, field: string, def: any) {
    const byHint = hintLocator(modal, def);
    // hint 命中但可能是容器（标签/包装 div）→ 下钻找真正可输入的控件
    if (byHint) return fillableDeep(byHint) as any;
    const kind = String((def || {}).kind || 'input');
    // rev138：无 hint 时的 label 兜底同样要按字段声明的作用域找（见 scopeOf 注释）。
    const sc = scopeOf(modal, def);
    if (kind === 'richtext') {
      const item = sc.locator(`.ant-form-item:has-text("${field}")`).first();
      const editable = item.locator('[contenteditable="true"], .ql-editor, .public-DraftEditor-content').first();
      if (editable) return editable;
      return item.locator('[contenteditable="true"]').first();
    }
    const tag = kind === 'textarea' ? 'textarea' : 'input';
    return fieldInput(sc, field, tag);
  }

  // 单选组必须在**所属表单项**内定位：整页 :has-text 容易命中其它行。
  // rev109：新增可选 def 参数——字段名与页面标签不同语言时（YL 中文字段名/英文页面），
  // has-text 按字段名找必落空；配置了 selector_hint 时直接按 hint 定位再按选项文本过滤。
  // def 由**调用方**传入（fieldDefs 在内层作用域，此处拿不到）。
  function radioOption(modal: any, field: string, option: string, def?: any) {
    // rev167（TR-784 实测）：字段级作用域必须跟随字段——问卷「题目类型/是否必答」
    // 的 radio 组在题目卡片里，而卡片在 `.ant-form`（modal）**之外**；旧实现
    // `modal.locator(hint)` 恒 0 ⇒ default_selected 报「未观测到默认选中」，
    // 实际 SUT 默认选中真实存在（探针：单选题/是 均为 checked=true）。
    // hint 命中的是字段所在的 .ant-form-item，选项读它内部的 .ant-radio-wrapper
    //（旧 hint 路径对整个 form-item 做 hasText 过滤，多组同字段名时会串组）。
    const scope = scopeOf(modal, def);
    const h = hintSel(def);
    const item = h ? scope.locator(h).first()
                   : scope.locator(`.ant-form-item:has-text("${field}")`).first();
    return item.locator('.ant-radio-wrapper').filter({hasText: option}).first();
  }

  // ---- antd Select（下拉）--------------------------------------------------
  // 实测（CS 工单表单）：Brand/Service Mode 等是 antd Select，控件结构为
  // `.ant-form-item > .ant-select > .ant-select-selector`，已选项文本在
  // `.ant-select-selection-item`，未选中时只有 `.ant-select-selection-placeholder`。
  function selectRoot(modal: any, field: string, def: any) {
    const byHint = hintLocator(modal, def);
    if (byHint) return byHint;
    return modal.locator(`.ant-form-item:has-text("${field}") .ant-select`).first();
  }

  async function selectDisplayValue(modal: any, field: string, def: any) {
    const root = selectRoot(modal, field, def);
    if (!(await root.count())) return '';
    // rev92：兼容 antd v3（.ant-select-selection-selected-value）与 v4/v5（.ant-select-selection-item）
    const item = root.locator('.ant-select-selection-item, .ant-select-selection-selected-value').first();
    if (!(await item.count())) return '';
    return ((await item.innerText({timeout: 5000}).catch(() => '')) || '').replace(/\s+/g, ' ').trim();
  }

  //: 展开下拉并读取选项文本。
  //: 两个实测要点：
  //: 1) 下拉渲染在 portal 里（可能挂到 body），必须用**同一作用域**查询
  //:    （CS 表单在 iframe 内，用 page 查不到）；
  //: 2) 页面上可能同时存在多个 `.ant-select-dropdown`（每个 Select 一个，隐藏态），
  //:    因此只取**当前可见**的那个——实测取"最后一个"会读到隔壁字段的选项。
  //: 未读到选项时重试一次（有的实现再次点击是"展开"而非"收起"）。
  async function selectOptionLabels(pg: any, scope: any, modal: any, field: string, def: any): Promise<string[]> {
    const root = selectRoot(modal, field, def);
    if (!(await root.count())) return [];
    let labels: string[] = [];
    for (let attempt = 0; attempt < 2 && labels.length === 0; attempt += 1) {
      await root.click({ timeout: 8000 }).catch(() => undefined);
      await pg.waitForTimeout(800);
      const dropdowns = scope.locator('.ant-select-dropdown:visible');
      const n = await dropdowns.count().catch(() => 0);
      if (n) {
        const popup = dropdowns.nth(n - 1);
        // rev92：选项类名兼容——本系统为 antd v3（.ant-select-dropdown-menu-item），
        // 旧实现只查 v4/v5 的 .ant-select-item-option，实测展开后读到 0 条选项
        const items = popup.locator('.ant-select-item-option, .ant-select-dropdown-menu-item, [role="option"]');
        const count = Math.min(await items.count().catch(() => 0), 100);
        const seen = new Set<string>();
        for (let i = 0; i < count; i += 1) {
          const text = ((await items.nth(i).innerText({timeout: 5000}).catch(() => '')) || '').replace(/\\s+/g, ' ').trim();
          if (text && !seen.has(text)) { seen.add(text); labels.push(text); }
        }
      }
      if (labels.length === 0) await pg.waitForTimeout(700);
    }
    if (typeof pg.keyboard?.press === 'function') {
      await pg.keyboard.press('Escape').catch(() => undefined);
    }
    await pg.waitForTimeout(300);
    return labels;
  }

  // rev92：DatePicker 必填触发——选一个日期再点清除（都是真实 onChange，会触发
  // antd 校验规则）。兼容 antd v3（.ant-calendar-*）与 v4/v5（.ant-picker-*）。
  // 返回 ''=流程跑完；否则为诊断信息（调用方记 unverified）。
  async function datePickThenClear(pg: any, scope: any, modal: any, field: string, def: any): Promise<string> {
    const root = hintLocator(modal, def) || modal.locator(
      `.ant-form-item:has-text("${field}") .ant-picker, .ant-form-item:has-text("${field}") .ant-calendar-picker`,
    ).first();
    if (!(await root.count())) return '未定位到日期控件';
    await root.click({timeout: OP_T}).catch(() => undefined);
    await pg.waitForTimeout(900);
    const cell = scope.locator(
      '.ant-picker-dropdown:visible .ant-picker-cell-inner, .ant-calendar-picker-container:visible .ant-calendar-date',
    ).first();
    if (!(await cell.count())) return '日历面板未展开';
    await cell.click({timeout: 8000}).catch(() => undefined);
    await pg.waitForTimeout(600);
    const clearIcon = root.locator('.ant-picker-clear, .ant-calendar-picker-clear').first();
    if (!(await clearIcon.count())) return '已选日期但未出现清除图标';
    await root.hover({timeout: 5000}).catch(() => undefined);
    await clearIcon.click({timeout: 8000}).catch(() => undefined);
    await pg.waitForTimeout(400);
    return '';
  }

  async function isRadioChecked(wrap: any): Promise<boolean> {
    if (!(await wrap.count())) return false;
    const cls = (await wrap.getAttribute('class', {timeout: 5000}).catch(() => '')) || '';
    if (cls.includes('ant-radio-wrapper-checked')) return true;
    return await wrap.locator('input[type=radio]').first().isChecked().catch(() => false);
  }

  // 把表单项切到期望模式（金额字段在「批量导入」下不存在，必须先切回「手动输入」）
  async function ensureMode(pg: any, modal: any, mode: any) {
    if (!mode) return;
    for (const field of Object.keys(mode)) {
      const wrap = radioOption(modal, field, mode[field]);
      if (!(await wrap.count())) continue;
      if (!(await isRadioChecked(wrap))) {
        await wrap.click({timeout: 8000});
        await pg.waitForTimeout(600);
      }
    }
  }

  async function itemHasError(pg: any, modal: any, field: string, waitMs = 600): Promise<boolean> {
    const item = modal.locator(`.ant-form-item:has-text("${field}")`).first();
    await pg.waitForTimeout(waitMs);
    // 注意：getAttribute/innerText 在元素不存在时会等满默认 30s —— 全部给短超时，
    // 否则弹窗关闭/页面死亡时单次调用就挂 30s（实测累计吃满 600s 测试超时）。
    if (await item.locator('.ant-form-item-explain-error').count()) return true;
    const cls = String(await item.getAttribute('class', {timeout: 1500}).catch(() => '') || '');
    if (cls.includes('has-error')) return true;
    // antd v3：explain 文案 + has-error 挂在 control 上
    if (await item.locator('.ant-form-explain').count()) return true;
    if (await item.locator('.has-error').count()) return true;
    return false;
  }

  //: rev140：**锚定控件**的错误检测（2026-10-20 问卷探针实测）。
  //: 旧行 itemHasError 以 `:has-text(字段名)` 匹配标签，有两个实测盲区：
  //:  ① 卡片内 form-item 的标签是「标题」，规则字段名是「题目标题」→ 恒不匹配；
  //:  ② `modal` 配置为 `.ant-form`，题目卡片在 `.ant-form` **之外** → 作用域内恒 0。
  //: 两者叠加 = fieldError 恒 false → settle 轮询永不返回 → late 循环跑满 73s 被看门狗杀。
  //: 主路改走字段自己的定位路径（hint + scopeOf，与 fill 同源）→ 向上找最近
  //: `.ant-form-item` → 查 explain / has-error（antd v3 `.ant-form-explain` +
  //: `.ant-form-item-control.has-error`，v4 `.ant-form-item-explain-error`）。
  //: 锚定失败再退回旧行（标签匹配，0 等待——等待已在本函数做过）。
  async function itemHasErrorCtl(pg: any, modal: any, field: string, def: any, waitMs = 600): Promise<boolean> {
    await pg.waitForTimeout(waitMs);
    try {
      const ctl = fieldControl(modal, field, def);
      if (ctl && (await ctl.count().catch(() => 0))) {
        // rev141：改**精确 token** 匹配。旧 `contains(@class,"ant-form-item")` 会先命中
        // 更近的 `div.ant-form-item-control`（类名包含 "ant-form-item"），explain 不一定
        // 在 control 内部渲染 —— 探针3 实测松匹配落点就是 control。concat 空格包裹
        // 是标准 class 精确 token 匹配写法。
        const item = ctl.locator('xpath=ancestor::div[contains(concat(" ", normalize-space(@class), " "), " ant-form-item ")][1]');
        if (await item.count().catch(() => 0)) {
          if (await item.locator('.ant-form-explain').count().catch(() => 0)) return true;
          if (await item.locator('.ant-form-item-explain-error').count().catch(() => 0)) return true;
          const cls = String(await item.getAttribute('class', {timeout: 1000}).catch(() => '') || '');
          if (cls.includes('has-error')) return true;
          if (await item.locator('.has-error').count().catch(() => 0)) return true;
        }
      }
    } catch { /* 控件消失/页面迁移 → 走兜底 */ }
    return await itemHasError(pg, modal, field, 0);
  }

  //: rev165（2026-10-21，P1 必填校验时机探针）：**读提示文案**，而不只是"有没有错误"。
  //: 为什么要文案：required_error 的原判据是「填值→清空→blur→查 explain 是否出现」，
  //: 这个判据对两类 SUT 都会给出错误结论——
  //:   ① 提示是上一次交互的**残留**（清空前就已存在）→ 被当成"本次触发"= 伪 pass；
  //:   ② 提示在**清空瞬间**（onChange）就出现，blur 只是顺带 → 结论写"填值后清空出现"，
  //:      看不出真正的触发时机，也无法和"仅提交时校验"区分。
  //: 探针实测（营销云问卷，2026-10-21）：
  //:   问卷标题/优先级：清空后 120ms 即出现「请输入问卷标题」→ onChange；
  //:   题目标题/选项1（题目卡片内）：清空、blur 均无字段级提示，提交时给 toast
  //:     「题目1：请输入标题」且**零写请求**发出 → 提交时校验 + toast 形态。
  //: 返回文案数组（用于"与基线比对"识别残留）；只有 has-error 而无文案时给哨兵，
  //: 避免把"只挂 class 不渲文案"的 SUT 判成无错误。
  async function itemErrorTexts(modal: any, field: string, def: any): Promise<string[]> {
    const out: string[] = [];
    try {
      const ctl = fieldControl(modal, field, def);
      if (!ctl || !(await ctl.count().catch(() => 0))) return out;
      const item = ctl.locator('xpath=ancestor::div[contains(concat(" ", normalize-space(@class), " "), " ant-form-item ")][1]');
      if (!(await item.count().catch(() => 0))) return out;
      for (const sel of ['.ant-form-explain', '.ant-form-item-explain-error']) {
        const n = await item.locator(sel).count().catch(() => 0);
        for (let i = 0; i < n; i += 1) {
          const t = String(await item.locator(sel).nth(i).innerText({timeout: 1000}).catch(() => '') || '').trim();
          if (t) out.push(t);
        }
      }
      if (!out.length) {
        const cls = String(await item.getAttribute('class', {timeout: 1000}).catch(() => '') || '');
        if (cls.includes('has-error')) out.push('[has-error]');
        else if (await item.locator('.ant-form-item-control.has-error, .has-error').count().catch(() => 0)) {
          out.push('[has-error]');
        }
      }
    } catch { /* 控件消失/页面迁移 → 返回空，由调用方按"未取到证据"处理 */ }
    return out;
  }

  //: 读取某个单选组当前渲染出来的所有选项标签（只读，用于"选项范围"断言）
  async function optionLabels(modal: any, field: string, def?: any): Promise<string[]> {
    // rev167：与 radioOption 同口径——hint 优先 + scopeOf 决定作用域。
    // 旧实现钉死 modal 作用域且不用 hint：卡片字段（题目类型/是否必答）恒读空，
    // radio_options 只能报「未读到单选选项」（TR-784 实测 4 条全中）。
    const scope = scopeOf(modal, def);
    const h = hintSel(def);
    const item = h ? scope.locator(h).first()
                   : scope.locator(`.ant-form-item:has-text("${field}")`).first();
    const wraps = item.locator('.ant-radio-wrapper');
    const out: string[] = [];
    const n = await wraps.count();
    for (let i = 0; i < n; i++) {
      out.push(((await wraps.nth(i).innerText({timeout: 5000}).catch(() => '')) || '').trim());
    }
    return out;
  }

  //: rev116（P1-③）：勾选组选项标签。与 optionLabels 的区别：
  //:  ① 支持 selector_hint（YL 页面标签是英文 Business Hours，用中文字段名 has-text 必落空）；
  //:  ② 读 .ant-checkbox-wrapper（单选组结构不适用）。
  async function choiceOptionLabels(modal: any, field: string, def?: any): Promise<string[]> {
    const hint = hintSel(def);
    const candidates = hint ? modal.locator(hint)
                            : modal.locator(`.ant-form-item:has-text("${field}")`);
    // rev116b（TR-512/TR-513 实测）：`:has-text("Business Hours")` 会同时命中
    // 「Business Hours」与「Business Hours Description」，`.first()` 拿到描述项 → 0 个选项。
    // 多命中时选**真正含勾选框**的那一项；配置侧改用 label[title="..."] 精确匹配。
    const readTexts = async (item: any): Promise<string[]> => {
      await item.scrollIntoViewIfNeeded({timeout: 3000}).catch(() => undefined);
      const raw = await item.evaluate((el: any) => Array.from(el.querySelectorAll('.ant-checkbox-wrapper'))
        .map((w: any) => String(w.textContent || '').replace(/\s+/g, ' ').trim())).catch(() => [] as string[]);
      if (raw.length) return (raw as string[]).filter((s: string) => !!s);
      const wraps = item.locator('.ant-checkbox-wrapper');
      const out: string[] = [];
      const n = await wraps.count();
      for (let i = 0; i < n; i++) {
        out.push(((await wraps.nth(i).textContent({timeout: 3000}).catch(() => '')) || '').trim());
      }
      return out.filter((s) => !!s);
    };
    const total = await candidates.count().catch(() => 0);
    for (let i = 0; i < total; i++) {
      const item = candidates.nth(i);
      if ((await item.locator('.ant-checkbox-wrapper').count().catch(() => 0)) > 0) {
        return await readTexts(item);
      }
    }
    if (total > 0) return await readTexts(candidates.first());
    return [];
  }

  async function runFormChecks() {
    // rev101：纯场景计划（零字段检查、无 create 语义场景）**跳过主流程 openForm**——
    // list 类功能点（导出/排序/搜索）根本没有"表单"可开，硬开只会误点工具条按钮
    // （实测：toolbar_modal 的 openForm 会真的点 Export/Email a Link 并发请求）。
    // 场景的 setup 自带入口；create 语义的场景（要用平台已打开的表单）仍预开。
    const planChecks: any[] = FORM_PLAN.checks || [];
    const planScenarios: any[] = FORM_PLAN.negative_scenarios || [];
    const needsCanvas = planScenarios.some(
      (s: any) => String((s && s.target_entry) || 'custom') === 'create');
    let modal: any = null;
    if (planChecks.length > 0 || planScenarios.length === 0 || needsCanvas) {
      modal = await openForm();
    } else {
      console.log('CS_FORM_DIAG scenario-only plan: 跳过主流程 openForm（场景自带入口，'
        + '无 create 语义场景）');
    }
    const fieldDefs: Record<string, any> = (FORM_PLAN.locators || {}).fields || {};

    // ===== rev137：字段检查的**前置动作**（``pre_actions``）=====
    // 动机（实测 TR-752/753 + 只读探针）：问卷「题目」区的输入框在点「新增题目」之前
    // **根本不存在**（探针：点击前 `[class*=questionCard]` = 0，页面上只有 #sbjTypeName）。
    // 定位器本身是对的，缺的是"让这块区域渲染出来"的那一次点击；旧口径下这类用例只能
    // 记 unverified，与"选择器配错"在日志上**完全一样**，配置方会去反复改正确的定位器。
    //
    // 触发口径：**按需**。动作用 ``for_fields`` 声明"我是为哪些字段准备的"，只有在第一条
    // 作用于这些字段的检查要执行时才点 —— 这样不破坏既有不变式「默认选中必须在任何点击
    // 之前观测」（先点后测会把默认值测成"我们刚点的那个值"）。没有 ``for_fields`` 的动作
    // 等价于"首条检查前触发一次"。
    const preActions: any[] = FORM_PLAN.pre_actions || [];
    const preActionDone: boolean[] = preActions.map(() => false);
    //: 前置动作失败 ⇒ 依赖它的字段区**根本没渲染**。这类检查要报"前置动作未完成"，
    //: 而不是"未定位到输入框"——后者会把排障引向定位器。
    const preActionBlocked = new Set<string>();
    let preActionBlockedAll = false;
    let preActionMissed: string[] = [];

    const preActionScopeFields = (act: any): string[] =>
      Array.isArray(act && act.for_fields) ? act.for_fields.map((f: any) => String(f)) : [];

    //: 按文本候选点一次，返回命中的文本（'' = 一个都没点成）。
    //: 作用域优先级：表单容器 ``modal`` → 整页 ``anyScope``。表单外的同名按钮
    //: （列表工具条、侧边栏菜单）不该被前置动作误点。
    async function clickPreActionLabel(labels: string[]): Promise<string> {
      for (const text of labels) {
        const needle = String(text || '').trim();
        if (!needle) continue;
        const rx = new RegExp(needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
        const scopes = [modal, anyScope].filter(Boolean);
        for (const scope of scopes) {
          // 先按 role=button（语义最准），再退 button/a/[role=button] 的文本匹配；
          // 不做"任意可见文本"兜底——那会点到标题/标签上，看起来成功了实际没动作。
          const candidates = [
            scope.getByRole('button', {name: rx}).first(),
            scope.locator(`button:has-text("${needle}"), a:has-text("${needle}"),`
              + ` [role="button"]:has-text("${needle}")`).first(),
          ];
          for (const cand of candidates) {
            if (!(await cand.count().catch(() => 0))) continue;
            try {
              await cand.waitFor({state: 'visible', timeout: 3000});
              await cand.click({timeout: 5000});
              return needle;
            } catch {
              // 被浮层/侧边栏拦住是常态（本 SUT 左侧 sider 会 intercept pointer events）：
              // 降级为 DOM 直点。前置动作只负责"让区域渲染"，不承担业务语义。
              const domHit = await cand.evaluate((el: any) => {
                try { el.scrollIntoView({block: 'center'}); el.click(); return true; }
                catch { return false; }
              }).catch(() => false);
              if (domHit) return needle;
            }
          }
        }
      }
      return '';
    }

    async function ensurePreActions(check: any): Promise<void> {
      if (!preActions.length) return;
      const field = String((check && check.field) || '');
      for (let i = 0; i < preActions.length; i += 1) {
        if (preActionDone[i]) continue;
        const scopeFields = preActionScopeFields(preActions[i]);
        if (scopeFields.length && !scopeFields.includes(field)) continue;
        preActionDone[i] = true;
        const act = preActions[i] || {};
        const labels: string[] = (Array.isArray(act.labels) ? act.labels : []).map((s: any) => String(s));
        const times = Math.max(1, Math.min(5, Number(act.times) || 1));
        // 注意 ``|| 800`` 会把配置里显式写的 0 变成 800：0 是合法值（点完不等），
        // 必须用 isFinite 判空，否则"我配了 0 却没生效"这种排查半天的问题又会出现。
        const waitRaw = Number(act.wait_ms);
        const waitMs = Number.isFinite(waitRaw) ? Math.max(0, Math.min(10000, waitRaw)) : 800;
        let clicked = 0;
        for (let n = 0; n < times; n += 1) {
          const hit = await clickPreActionLabel(labels);
          if (!hit) break;
          clicked += 1;
          await page.waitForTimeout(waitMs);
        }
        const ok = clicked === times;
        // 自证行：配了哪几条、在哪条检查前触发、点成了几次。缺它的话"配了但没触发"
        // 与"没配"在日志上无法区分（与 RUN_TAG_USED / PRE_ACTION 同一条纪律）。
        console.log('PRE_ACTION ' + JSON.stringify({
          index: i, labels, times, clicked, for_fields: scopeFields,
          at: `${check.type}:${field}`, ok, reason: String(act.reason || '').slice(0, 120),
        }));
        if (!ok) {
          preActionMissed = preActionMissed.concat(labels);
          if (scopeFields.length) scopeFields.forEach((f) => preActionBlocked.add(f));
          else preActionBlockedAll = true;
        }
      }
    }

    // 配置回执：本次产物到底带了哪些前置动作（空/被拒都要可见）。
    if (preActions.length || (FORM_PLAN.pre_action_rejects || []).length) {
      console.log('PRE_ACTIONS_PLAN ' + JSON.stringify({
        actions: preActions.map((a: any, i: number) => ({
          index: i, labels: a.labels || [], for_fields: a.for_fields || [], times: a.times || 1,
        })),
        rejects: FORM_PLAN.pre_action_rejects || [],
      }));
    }

    // 负路径提交队列（rev67）：客户端层拿不到证据的「期望拒绝」断言，进提交层验证。
    // 口径（2026-09-25 用户确认）：只做负路径提交（空必填/非法探针值），正常不会写入合法数据；
    // 被测端若竟然接受 → 记 blocked（疑似缺陷），由末尾硬失败暴露。
    const submitQueue: Array<{check: any; name: string; kind: 'probe' | 'required_empty'; probeValue?: string}> = [];
    //: rev139：提交层**重发计数**。提交层会对"客户端层拿不到证据"的同一批检查再记一次
    //: 结果（这是设计——真实提交才算证据），但对账行若把两次都算成"新增覆盖"，
    //: 就会得出 `unexplained_emitted > 0` 的**假缺口**（TR-755 实测 2：两条
    //: required_error 客户端记一次、提交层又记一次）。缺口与重发必须分开列，
    //: 否则"对账行说丢了 2 条"会把人引去查根本没丢的地方。
    let submitEmitted = 0;
    const submitRec = (check: any, name: string, outcome: CheckOutcome, observed: string) => {
      submitEmitted += 1;
      recAll(check, name, outcome, observed);
    };

    // ===== 网络隔离（放行清单 + 默认拒绝）=====
    // 复核意见指出：按"URL 含 upload/oss/file"做特征匹配既漏（不含关键词的地址照样能收文件）
    // 又误（同源含 file 的查询类 POST 会被误拦）。这里改为**默认拒绝**：
    //   * 非 GET 请求：只放行 allowlist 内的主机，其余一律 abort 并逐条记账；
    //   * GET 请求：放行（只读，不产生服务端副作用），但同样记账便于审计。
    // 说明：这是**策略级**收敛，仍不能证明"所有潜在副作用通道"都被封死；报告里必须按此口径描述。
    const isolationBlocked: Array<{method: string; url: string; kind: string}> = [];
    // 安全边界（复核意见 1）：**写请求不再默认放行同源**。
    //   * multipart/form-data：任何情况下阻断（上传传输特征）；
    //   * GET：放行（只读，无副作用）；
    //   * 其他写请求：必须命中**显式放行规则**（host + 可选路径前缀 + 可选方法），否则阻断。
    //     规则为空（默认）＝所有写请求都被阻断，宁可再生环境失败，也不产生未知业务写副作用。
    const policyRules: Array<{host?: string; path_prefix?: string; methods?: string[]}> =
      (FORM_PLAN.upload_policy && FORM_PLAN.upload_policy.allow_rules) || [];
    const legacyHosts: string[] = (FORM_PLAN.upload_policy && FORM_PLAN.upload_policy.allow_hosts) || [];
    // 上传（multipart）放行规则：**唯一**的 multipart 口子，默认空 ⇒ 一律阻断。
    // 与 policyRules 分开配置，避免"允许写某接口"被误读成"也允许往那里传文件"。
    const uploadAllowRules: Array<{host?: string; path_prefix?: string; methods?: string[]}> =
      (FORM_PLAN.upload_policy && FORM_PLAN.upload_policy.allow_upload_rules) || [];
    // rev119（A1）：**字段级通道**的上传放行必须自证。历史上「配了但没生效」
    // 只能靠人比对配置与日志；这里与场景通道的 UPLOAD_ALLOW_RULES 分开打印，
    // 两条通道不混为一谈（前者对应 upload_file 检查，后者对应 negative_scenario）。
    console.log('UPLOAD_POLICY ' + JSON.stringify(uploadAllowRules));
    if (FORM_PLAN.upload_policy_gap) {
      // 非空 = 本次产物有「上传断言会恒 unverified」的已知缺口（配置没声明 upload_allow）
      console.log('UPLOAD_POLICY_GAP ' + JSON.stringify({reason: FORM_PLAN.upload_policy_gap}));
    }
    const pageHost = (() => {
      try { return new URL(page.url()).host; } catch { return ''; }
    })();
    // 本段可被本地测试原样执行：test_web_upload_local_node.py
    // >>> UPLOAD_ISOLATION_POLICY_BEGIN
    //: 规则匹配（host 后缀 + path 前缀 + 方法白名单）。写放行与上传放行**复用同一判据**——
    //: 各写一份迟早漂移成"配了但没生效"。
    function allowRuleMatches(rule: any, host: string, path: string, verb: string): boolean {
      if (!rule || typeof rule !== 'object') return false;
      // 兼容 ``host``（既有配置键）与 ``host_suffix``（人工配置更常写）两种写法
      const hostRule = String(rule.host || rule.host_suffix || '');
      if (hostRule && !(host && host.endsWith(hostRule))) return false;
      if (rule.path_prefix && !path.startsWith(String(rule.path_prefix))) return false;
      const methods = (rule.methods || []).map((m: any) => String(m).toUpperCase());
      if (methods.length && !methods.includes(verb)) return false;
      return true;
    }
    function isolationDecision(method: string, url: string,
                               rules: Array<{host?: string; path_prefix?: string; methods?: string[]}>,
                               legacyHosts: string[], contentType: string,
                               uploadAllowRules?: Array<{host?: string; path_prefix?: string; methods?: string[]}>): string {
      const verb = String(method).toUpperCase();
      let parsed: URL | null = null;
      try { parsed = new URL(url); } catch { parsed = null; }
      const host = parsed ? parsed.host : '';
      const path = parsed ? parsed.pathname : '';
      // 1) 文件上传的传输特征：**默认仍然一律阻断**（与写放行清单无关）。
      //    唯一口子 = 显式声明的上传放行规则（2026-10-16 用户确认新增，rev114）：
      //    部分业务的必填项本身就是附件（实测美心 YL 店铺主数据 Store Image），
      //    multipart 全阻会让这类用例在正式通道下永远不可能通过。
      //    安全口径不变：默认空清单 ⇒ 与历史行为**逐字一致**（零回归，由
      //    test_web_upload_local_node.py 的默认用例锁死）；放行必须 host + path_prefix
      //    + methods 三项同时收窄，且由场景/配置**显式声明**，不默认放行同源上传。
      if (String(contentType).toLowerCase().includes('multipart/form-data')) {
        const uploadOk = (uploadAllowRules || []).some((rule) => allowRuleMatches(rule, host, path, verb));
        return uploadOk ? 'continue' : 'abort';
      }
      // 2) 只读请求放行
      if (verb === 'GET') return 'continue';
      // 3) 写请求：必须命中显式规则；legacyHosts 仅作为"该主机 GET 放行"的兼容写法
      const matched = (rules || []).some((rule) => allowRuleMatches(rule, host, path, verb));
      if (matched) return 'continue';
      void legacyHosts;                 // 兼容字段：不再用于放行写请求（只放行 GET）
      return 'abort';
    }
    function blockedKind(url: string, contentType: string, selfHost: string): string {
      const ct = String(contentType).toLowerCase();
      if (ct.includes('multipart/form-data') || /upload|oss|minio|attachment/i.test(url)) {
        return 'upload_blocked';
      }
      try {
        if (new URL(url).host === selfHost) return 'non_upload_api_blocked';
      } catch { /* 非法 URL */ }
      if (/\/api\/|\/rest\/|\/gateway\//i.test(url)) return 'non_upload_api_blocked';
      return 'unknown_request_blocked';
    }
    // <<< UPLOAD_ISOLATION_POLICY_END
    async function installIsolation(target: any) {
      if (!target) return;
      await target.route('**/*', async (route: any) => {
        try {
          const request = route.request();
          const method = request.method().toUpperCase();
          const url = request.url();
          const contentType = String((request.headers() || {})['content-type'] || '');
          if (isolationDecision(method, url, policyRules, legacyHosts, contentType,
                                uploadAllowRules) === 'continue') {
            await route.continue();
            return;
          }
          const kind = blockedKind(url, contentType, pageHost);
          isolationBlocked.push({method, url: url.slice(0, 200), kind});
          console.log('ISOLATION_BLOCKED ' + JSON.stringify(
            {method, url: url.slice(0, 200), kind}));
          await route.abort();
        } catch {
          await route.abort();
        }
      });
    }

    //: 夹具：有效工作簿（Python 侧构造的部件 + STORED 拼装，字节精确到目标大小）。
    const FIX = FORM_PLAN.upload_fixtures || {};
    // 本段可被本地测试原样执行：test_web_upload_local_node.py
    // >>> UPLOAD_ZIP_BUILDER_BEGIN
    const crcTable = (() => {
      const table = new Uint32Array(256);
      for (let n = 0; n < 256; n++) {
        let c = n;
        for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
        table[n] = c >>> 0;
      }
      return table;
    })();
    function crc32(buf: Buffer): number {
      let c = 0xFFFFFFFF;
      for (const byte of buf) c = crcTable[(c ^ byte) & 0xFF] ^ (c >>> 8);
      return (c ^ 0xFFFFFFFF) >>> 0;
    }
    //: 用 STORED（不压缩）拼装 ZIP：先放工作簿必需部件，再用 padding 部件把总字节数补齐到目标值。
    function workbookOfExactBytes(target: number): Buffer | null {
      const parts: Array<{name: string; data: Buffer}> = [];
      for (const name of Object.keys(FIX.workbook_parts || {})) {
        parts.push({name, data: Buffer.from(FIX.workbook_parts[name], 'base64')});
      }
      if (!parts.length) return null;
      const overheadOf = (p: Array<{name: string; data: Buffer}>) => p.reduce(
        (sum, e) => sum + 30 + Buffer.byteLength(e.name, 'utf8') + e.data.length
                     + 46 + Buffer.byteLength(e.name, 'utf8'), 22);
      const padName = 'xl/padding.bin';
      let base = overheadOf(parts) + 30 + Buffer.byteLength(padName, 'utf8')
                 + 46 + Buffer.byteLength(padName, 'utf8');
      if (target < base + 1) return null;
      parts.push({name: padName, data: Buffer.alloc(target - base, 0x20)});
      const chunks: Buffer[] = [];
      const central: Buffer[] = [];
      let offset = 0;                       // 各条目本地头在文件中的偏移（中央目录必须记录）
      for (const entry of parts) {
        const entryOffset = offset;       // 本条目本地头偏移
        const nameBuf = Buffer.from(entry.name, 'utf8');
        const crc = crc32(entry.data);
        const local = Buffer.alloc(30);
        local.writeUInt32LE(0x04034b50, 0);
        local.writeUInt16LE(20, 4);
        local.writeUInt16LE(0, 6);
        local.writeUInt16LE(0, 8);
        local.writeUInt16LE(0, 10);
        local.writeUInt16LE(0, 12);
        local.writeUInt32LE(crc, 14);
        local.writeUInt32LE(entry.data.length, 18);
        local.writeUInt32LE(entry.data.length, 22);
        local.writeUInt16LE(nameBuf.length, 26);
        local.writeUInt16LE(0, 28);
        chunks.push(local, nameBuf, entry.data);
        offset += local.length + nameBuf.length + entry.data.length;
        const dir = Buffer.alloc(46);
        dir.writeUInt32LE(0x02014b50, 0);
        dir.writeUInt16LE(20, 4);
        dir.writeUInt16LE(20, 6);
        dir.writeUInt16LE(0, 8);
        dir.writeUInt16LE(0, 10);
        dir.writeUInt16LE(0, 12);
        dir.writeUInt16LE(0, 14);
        dir.writeUInt32LE(crc, 16);
        dir.writeUInt32LE(entry.data.length, 20);
        dir.writeUInt32LE(entry.data.length, 24);
        dir.writeUInt16LE(nameBuf.length, 28);
        dir.writeUInt16LE(0, 30);
        dir.writeUInt16LE(0, 32);
        dir.writeUInt16LE(0, 34);
        dir.writeUInt16LE(0, 36);
        dir.writeUInt32LE(0, 38);
        dir.writeUInt32LE(entryOffset, 42);
        central.push(dir, nameBuf);
      }
      const body = Buffer.concat(chunks);
      const dirBuf = Buffer.concat(central);
      const eocd = Buffer.alloc(22);
      eocd.writeUInt32LE(0x06054b50, 0);
      eocd.writeUInt16LE(parts.length, 8);
      eocd.writeUInt16LE(parts.length, 10);
      eocd.writeUInt32LE(dirBuf.length, 12);
      eocd.writeUInt32LE(body.length, 16);
      eocd.writeUInt16LE(0, 20);
      return Buffer.concat([body, dirBuf, eocd]);
    }
    // <<< UPLOAD_ZIP_BUILDER_END
    function uploadFixture(spec: any): {name: string; mimeType: string; buffer: Buffer} | null {
      const s = spec || {};
      const target = typeof s.bytes === 'number' ? s.bytes : null;
      if (s.source === 'workbook_stored') {
        if (target) {
          const buf = workbookOfExactBytes(target);
          return buf ? {name: s.name, mimeType: s.mime, buffer: buf} : null;
        }
        // 未指定目标字节数 → 直接用 Python 侧构造的真实最小工作簿（ZIP 压缩版）
        const fileBytes = Buffer.from((FIX.workbook_file || ''), 'base64');
        return fileBytes.length ? {name: s.name, mimeType: s.mime, buffer: fileBytes} : null;
      }
      if (s.source === 'bad_extension') {
        const head = Buffer.from(FIX.bad_extension_part || '', 'base64');
        const size = target || head.length;
        const buf = Buffer.alloc(size, 0);
        head.copy(buf, 0, 0, Math.min(head.length, size));
        return {name: s.name, mimeType: s.mime, buffer: buf};
      }
      if (s.source === 'csv_text') {
        // 合法 CSV：文本内容即有效样本（与"格式错误样本"严格区分）
        const rows = ['手机号,金额', '18320101265,100', '18320101266,200'];
        return {name: s.name, mimeType: s.mime || 'text/csv',
                buffer: Buffer.from(rows.join('\\n') + '\\n', 'utf8')};
      }
      if (s.source === 'xls_bytes') {
        const bytes = Buffer.from((FIX.xls_file || ''), 'base64');
        return bytes.length ? {name: s.name, mimeType: s.mime || 'application/vnd.ms-excel',
                               buffer: bytes} : null;
      }
      return null;
    }
    //: setup 期 ``upload`` 动作的**内联夹具**：把配置里带过来的 base64 还原成
    //: Playwright ``setInputFiles`` 需要的 {name, mimeType, buffer}。
    //: 与 uploadFixture 分开：那个是字段级阶段"构造特定字节数的坏样本"，这个是
    //: "填一个真实的必填附件"。字节全部来自配置，平台不内置任何业务素材。
    function inlineFixture(spec: any): {name: string; mimeType: string; buffer: Buffer} | null {
      const s = spec || {};
      const b64 = String(s.b64 || '');
      if (!b64) return null;
      return {name: String(s.name || 'upload.bin'),
              mimeType: String(s.mime || 'application/octet-stream'),
              buffer: Buffer.from(b64, 'base64')};
    }

    let checkIndex = 0;
    let uploadEvidenceCount = 0;
    //: 分阶段观测：把"文件选择 → 客户端反馈 → 上传请求 → 提示文本"拆开计时，
    //  否则"没有提示"这一个结果会混着多种原因（控件没收到文件 / 客户端没反应 / 提示一闪而过）。
    const installStageRecorder = async (scope: any) => {
      try {
        await scope.locator('body').first().evaluate(() => {
          const w = window as any;
          if (w.__uploadStages) return;
          w.__uploadStages = [];
          const push = (stage: string, extra: Record<string, unknown> = {}) => {
            w.__uploadStages.push({stage, at: Date.now(), ...extra});
          };
          document.addEventListener('change', (event) => {
            const target = event.target as any;
            if (target && target.type === 'file') {
              push('file_selected', {name: (target.files && target.files[0] && target.files[0].name) || '',
                                     size: (target.files && target.files[0] && target.files[0].size) || 0});
            }
          }, true);
          const origFetch = w.fetch;
          if (origFetch) {
            w.fetch = function (...args: any[]) {
              const url = String(args[0] || '').slice(0, 160);
              push('request_started', {url});
              const result = origFetch.apply(this, args as any);
              if (result && typeof result.then === 'function') {
                result.then(() => push('request_settled', {url}))
                      .catch(() => push('request_failed', {url}));
              }
              return result;
            };
          }
          const origOpen = w.XMLHttpRequest && w.XMLHttpRequest.prototype.open;
          if (origOpen) {
            w.XMLHttpRequest.prototype.open = function (method: string, url: string) {
              push('request_started', {url: String(url).slice(0, 160), method});
              return origOpen.apply(this, arguments as any);
            };
          }
        });
      } catch { /* 记录器装不上不影响主流程，只是少了阶段信息 */ }
    };
    const readStages = async (scope: any): Promise<Array<Record<string, any>>> => {
      try {
        return (await scope.locator('body').first()
          .evaluate(() => ((window as any).__uploadStages || []).slice(-40))) || [];
      } catch {
        return [];
      }
    };

    // ===== 上传校验阶段：可在**独立上下文**中执行，与业务执行上下文隔离 =====
    async function runUploadPhase(pg: any, scope: any, modal: any, checks: any[],
                                  contextLabel: string) {
      for (const check of checks) {
        const checkIndex = FORM_PLAN.checks.indexOf(check) + 1;
        const name = `upload_file:${check.field}`;
      // 说明：本分支**只采集原始事实**（样本、提示、附件块数量、拦截计数、是否超时），
      // **不做通过/未验证判定**。判定在服务端 `web_upload_evidence.classify_upload`，
      // 以便用单元测试覆盖"假通过"分支（复核意见 2/3/5）。
      await ensureMode(pg, modal, check.mode);
      const rowLabel = (check as any).row_label || '上传文件';
      const item = modal.locator(`.ant-form-item:has-text("${rowLabel}")`).first();
      let input = item.locator('input[type=file]').first();
      if (!(await input.count())) {
        input = modal.locator('input[type=file]').first();   // 退一步：整个弹窗里的文件输入
      }
      const baseEvidence: Record<string, any> = {
        check_index: checkIndex,
        case_id: (check as any).case_id || '',
        rule_id: (check as any).rule_id || '',
        field: check.field,
        evidence_kind: (check as any).evidence_kind || '',
        expect: (check as any).expect || '',
        scope: 'client_only',
        context_label: contextLabel,
        rule: check.rule || '',
        sample: {...((check as any).sample || {})},
        control_reset: false,
        chips_before: 0,
        chips_after: 0,
        messages_before: [] as string[],
        messages_after: [] as string[],
        accept_attr: '',
        waited_ms: 0,
        timed_out: false,
        timed_out_stage: '',
        stages: {
          change_ms: 0, feedback_ms: 0, saw_file_selected: false,
          saw_request: false, request_count: 0, event_count: 0,
        },
        isolation_blocks: 0,
        isolation_blocked_kinds: {
          upload_blocked: 0, non_upload_api_blocked: 0, unknown_request_blocked: 0,
        },
      };
      const emitEvidence = () => {
        uploadEvidenceCount += 1;
        console.log('UPLOAD_EVIDENCE ' + JSON.stringify(baseEvidence));
      };
      if (!(await input.count())) {
        baseEvidence.sample = {...(baseEvidence.sample || {}), error: 'no_file_input'};
        emitEvidence();
        continue;
      }
      baseEvidence.accept_attr = (await input.getAttribute('accept')) || '';

      // 状态重置：① 清空文件输入（同一文件再次选择时，浏览器/组件可能不再触发校验——
      // 这正是"大小拒绝提示偶发未观测到"的怀疑原因）；② 删掉上一次留下的附件块。
      let reset = false;
      try {
        await input.setInputFiles([], {timeout: 3000});
        reset = true;
      } catch { /* 清空失败不致命，继续尝试删除附件块 */ }
      try {
        const chipCount = await item.locator('.ant-upload-list-item').count();
        for (let i = chipCount - 1; i >= 0; i--) {
          const del = item.locator('.ant-upload-list-item .anticon-close, .ant-upload-list-item .anticon-delete').nth(i);
          if (await del.count()) {
            await del.click({timeout: 3000}).catch(() => undefined);
            reset = true;
          }
        }
        if (reset) await pg.waitForTimeout(400);
      } catch { /* 重置失败也不影响：分类器会看到 control_reset=false */ }
      baseEvidence.control_reset = reset;

      const fixture = uploadFixture((check as any).sample);
      if (!fixture) {
        baseEvidence.sample = {...(baseEvidence.sample || {}), error: 'fixture_unavailable'};
        emitEvidence();
        continue;
      }
      baseEvidence.sample = {...(baseEvidence.sample || {}), bytes: fixture.buffer.length};

      const messageTexts = async (scope: any) => {
        const inline = await item.locator('.ant-form-item-explain-error')
          .allInnerTexts().catch(() => []);
        const toasts = await scope.locator('.ant-message-error, .ant-message-warning, .ant-message-info')
          .allInnerTexts().catch(() => []);
        return inline.concat(toasts).map((t: string) => (t || '').trim()).filter(Boolean);
      };
      baseEvidence.chips_before = await item.locator('.ant-upload-list-item').count();
      baseEvidence.messages_before = await messageTexts(scope);
      const isoBefore = isolationBlocked.length;
      const blockedKinds = () => {
        const slice = isolationBlocked.slice(isoBefore);
        return {
          upload_blocked: slice.filter((b) => b.kind === 'upload_blocked').length,
          non_upload_api_blocked: slice.filter((b) => b.kind === 'non_upload_api_blocked').length,
          unknown_request_blocked: slice.filter((b) => b.kind === 'unknown_request_blocked').length,
        };
      };
      await installStageRecorder(scope);
      const stagesBefore = (await readStages(scope)).length;

      try {
        await input.setInputFiles(fixture, {timeout: 8000});
      } catch (error) {
        baseEvidence.sample = {...(baseEvidence.sample || {}),
                                error: 'set_input_failed: ' + String(error).slice(0, 60)};
        baseEvidence.isolation_blocks = isolationBlocked.length - isoBefore;
        emitEvidence();
        continue;
      }

      // 阶段 1：**文件选择事件**是否真的发生（控件收到文件才算进入校验阶段）
      const changeStart = Date.now();
      let sawChange = false;
      while (Date.now() - changeStart < 5000) {
        const stages = (await readStages(scope)).slice(stagesBefore);
        if (stages.some((s) => s.stage === 'file_selected')) { sawChange = true; break; }
        await pg.waitForTimeout(150);
      }
      const changeMs = Date.now() - changeStart;

      // 阶段 2：客户端反馈（新提示 / 上传请求 / 附件块变化），有上限等待。
      // 提示按**出现次数**比较：同一文本重复出现时集合差看不出变化（实测会白等到超时）。
      const countBy = (list: string[]) => {
        const map: Record<string, number> = {};
        for (const t of list) map[t] = (map[t] || 0) + 1;
        return map;
      };
      const beforeCounts = countBy(baseEvidence.messages_before);
      const hasNewMessage = (now: string[]) => {
        const nowCounts = countBy(now);
        return Object.keys(nowCounts).some((k) => nowCounts[k] > (beforeCounts[k] || 0));
      };
      const feedbackStart = Date.now();
      const budgetMs = sawChange ? 12000 : 4000;   // 已看到文件选择 → 给客户端校验更充裕的时间
      let feedbackSeen = false;
      while (Date.now() - feedbackStart < budgetMs) {
        await pg.waitForTimeout(200);
        const stages = (await readStages(scope)).slice(stagesBefore);
        const nowMessages = await messageTexts(scope);
        const nowChips = await item.locator('.ant-upload-list-item').count();
        if (hasNewMessage(nowMessages) || nowChips !== baseEvidence.chips_before
            || stages.some((s) => s.stage === 'request_started')) {
          feedbackSeen = true;
          break;
        }
      }
      const feedbackMs = Date.now() - feedbackStart;
      const stages = (await readStages(scope)).slice(stagesBefore);
      baseEvidence.waited_ms = changeMs + feedbackMs;
      baseEvidence.timed_out = !(sawChange && feedbackSeen);
      baseEvidence.timed_out_stage = !sawChange ? 'file_selected'
        : (feedbackSeen ? '' : 'client_feedback');
      baseEvidence.stages = {
        change_ms: changeMs,
        feedback_ms: feedbackMs,
        saw_file_selected: sawChange,
        saw_request: stages.some((s) => s.stage === 'request_started'),
        request_count: stages.filter((s) => s.stage === 'request_started').length,
        event_count: stages.length,
      };
      baseEvidence.chips_after = await item.locator('.ant-upload-list-item').count();
      baseEvidence.messages_after = await messageTexts(scope);
      baseEvidence.isolation_blocks = isolationBlocked.length - isoBefore;
      baseEvidence.isolation_blocked_kinds = blockedKinds();
      emitEvidence();
      continue;
    
      }
    }

    // rev94：客户端层整体限时预算（10 分钟）——与提交层 5 分钟预算配套，保证
    // "客户端 + 提交层 + 登录"总时长恒定低于 runner 上限（1500s），任何环境性挂起
    // 都转化为如实 unverified，而不是 stdout 被杀、全批证据丢失（实测 retro 偶发挂起）。
    const clientDeadline = Date.now() + 600000;
    for (const check of FORM_PLAN.checks) {
      checkIndex += 1;
      if (check.type === 'upload_file') continue;   // 由独立阶段处理（见下）
      // 由独立阶段处理（runListChecks / runExportChecks / runFilterChecks / runActionMatrix，写入之后）
      if (check.type === 'list_visible' || check.type === 'export_check'
          || check.type === 'filter_check' || check.type === 'action_matrix') continue;
      const name = `${check.type}:${check.field}${check.option ? '=' + check.option : ''}${check.data ? ' [' + check.data + ']' : ''}`;
      currentRuleId = (check as any).rule_id || '';
      currentCaseId = (check as any).case_id || '';
      if (Date.now() > clientDeadline) {
        recAll(check, name, 'unverified', '客户端层整体超时预算（10分钟）耗尽，该项未验证（环境性挂起，需重跑或 API 层验证）');
        continue;
      }

      try {
        // rev137：本条检查若要的字段区还没渲染出来，先跑它声明的前置动作。
        await ensurePreActions(check);
        if (preActionBlockedAll || preActionBlocked.has(String(check.field || ''))) {
          // 前置动作没点成 ⇒ 字段区不存在。如实报「前置动作未完成」，
          // 不报「未定位到输入框」——后者会把排障引向本来就正确的定位器。
          recAll(check, name, 'unverified',
                 `前置动作未完成（未找到可点击的「${preActionMissed.join('/')}」）：`
                 + '依赖它的字段区没有渲染，本项未验证');
          continue;
        }
        if (check.type === 'default_selected') {
          const def0 = fieldDefs[check.field] || {};
          if (def0.kind === 'select') {
            // 下拉默认值：读当前显示值（未选中时为空 → 未验证，不冒充通过）
            const value = await selectDisplayValue(modal, check.field, def0);
            const normV = (s: string) => (s || '').replace(/\s+/g, '');
            recAll(check, name, value && normV(value).includes(normV(check.option)) ? 'pass' : 'unverified',
                   value ? `默认显示值=${value}（期望 ${check.option}）` : '未读到下拉当前值（可能未选中）');
            continue;
          }
          // rev109：开关（antd Switch）默认态——YL 店铺表单的 營業狀態/排隊/訂位/外賣
          // 都是 switch，人工用例断言「默認開啟/默認關閉」。优先按 selector_hint 定位
          // （字段名是中文而页面标签是英文，has-text 兜底会落空）；aria-checked 缺失时
          // 退回 ant-switch-checked 类名判定（antd v3/v4 兼容）。
          if (String(def0.kind || '') === 'switch') {
            const root = hintLocator(modal, def0)
              || modal.locator(`.ant-form-item:has-text("${check.field}") .ant-switch`).first();
            if (!(await root.count())) {
              // 2026-10-16 修复：本分支文案指向的 observation 现场行**从未输出**——
              // 这里漏调 reportLocatorMiss（输入框类失效点 2578/2643/2879 都有留痕）。
              // 与项目「失败必须能看到现场」原则对齐：补上落空留痕，使文案不再指向虚空。
              await reportLocatorMiss('form:default_selected', check, modal);
              recAll(check, name, 'unverified', '未定位到开关控件（定位现场见 [observation] 行）');
              continue;
            }
            const aria = await root.first().getAttribute('aria-checked').catch(() => null);
            let on: boolean | null = null;
            if (aria === 'true') on = true;
            else if (aria === 'false') on = false;
            else {
              const cls = String((await root.first().getAttribute('class').catch(() => '')) || '');
              on = cls.includes('ant-switch-checked') ? true : (cls ? false : null);
            }
            if (on === null) { recAll(check, name, 'unverified', '开关状态不可读（无 aria-checked 且无样式类）'); continue; }
            const wantOn = /開啟|开启|on|enabled|true/i.test(String(check.option || ''));
            recAll(check, name, on === wantOn ? 'pass' : 'unverified',
                   `开关默认状态=${on ? '開啟' : '關閉'}（期望 ${check.option}）`);
            continue;
          }
          const wrap = radioOption(modal, check.field, check.option, fieldDefs[check.field]);
          const checked = await isRadioChecked(wrap);
          recAll(check, name, checked ? 'pass' : 'unverified',
                 checked ? `默认选中=${check.option}` : `未观测到默认选中 ${check.option}`);
          continue;
        }
        if (check.type === 'visible_now') {
          // 默认态下的条件显示：不做任何点击，只读当前表单
          const missing: string[] = [];
          for (const label of check.expect_labels || []) {
            if (!(await modal.locator(`.ant-form-item:has-text("${label}")`).count())) missing.push(label);
          }
          recAll(check, name, missing.length === 0 ? 'pass' : 'unverified',
                 missing.length === 0 ? `默认态下字段已显示：${(check.expect_labels || []).join('、')}`
                                      : `默认态下未显示：${missing.join('、')}`);
          continue;
        }
        if (check.type === 'submit_only') {
          const reason = String((check as any).reason || '');
          if (reason.includes('单选组')) {
            // rev69 口径：单选组默认选中是被测端防漏填的设计选择（非缺陷）。UI 层负路径
            // 物理上不可构造 → 标注「不适用」并指明服务端空值校验的覆盖路径，避免被当漏测。
            recAll(check, name, 'unverified', `${reason}；UI 层负路径不适用：单选组有默认选中，无法构造空值提交（如需覆盖服务端空值校验，需走 API 层测试）`);
          } else if (reason.includes('开关') || reason.includes('勾选组')) {
            // rev116（P1-③）：开关始终有值、勾选组无自由输入——两者都不做真实提交：
            // 开关提交也造不出空值；勾选组所在多为「新建」表单，真实提交有产生脏数据风险。
            // 如实记未验证，并指明覆盖路径（API 层或显式开启提交层）。
            recAll(check, name, 'unverified',
                   `${reason}；UI 层负路径未启用（如需覆盖服务端空值校验，需走 API 层测试）`);
          } else {
            // 上传控件必填：不带文件点「确定」→ 期望提交时校验拦截并给出该字段提示
            recAll(check, name, 'unverified', `${reason}；进入负路径提交层重试`);
            submitQueue.push({check, name, kind: 'required_empty'});
          }
          continue;
        }
        if (check.type === 'radio_options') {
          // 单选组的选项范围：读选项标签即可判定（无自由输入路径）
          const labels = await optionLabels(modal, check.field, fieldDefs[check.field]);
          const allowed: string[] = check.allowed || [];
          const norm = (s: string) => (s || '').replace(/\s+/g, '');
          if (!labels.length) {
            recAll(check, name, 'unverified', '未读到单选选项（可能未展开或该组无选项）');
            continue;
          }
          if (String(check.expect).startsWith('reject')) {
            // 同 select：reject 只判"非法值能否被选到"，不被 allowed 缺失改判（见 select 分支说明）
            const exists = labels.some((l) => norm(l).includes(norm(check.data)));
            recAll(check, name, exists ? 'unverified' : 'pass',
                   exists ? `非法值 ${check.data} 竟出现在选项里（${check.rule}）`
                          : `单选组中不存在 ${check.data || '(空)'}，用户无法选择非法值（${check.rule}）`);
          } else if (allowed.some((a) => !labels.some((l) => norm(l).includes(norm(a))))) {
            const missing = allowed.filter((a) => !labels.some((l) => norm(l).includes(norm(a))));
            recAll(check, name, 'unverified', `未观测到选项：${missing.join('、')}（实际：${labels.join('/')}）`);
          } else if (check.data) {
            const exists = labels.some((l) => norm(l).includes(norm(check.data)));
            recAll(check, name, exists ? 'pass' : 'unverified',
                   exists ? `合法选项 ${check.data} 存在（${check.rule}）`
                          : `未观测到合法选项 ${check.data}（${check.rule}）`);
          } else {
            recAll(check, name, 'pass', `选项范围齐全：${labels.join('/')}`);
          }
          continue;
        }
        if (check.type === 'choice_options') {
          // rev116（P1-③）：勾选组（checkbox 群组）的选项范围只读校验。
          // 取值路径：表单项内 .ant-checkbox-wrapper 的文本（YL「營業時間」= Mon~Sun 7 项）。
          // 数量断言口径：rule/data 里出现「N組/N组/N项/N項/N个/N個」→ 与真实选项数比对；
          // 否则按 data 是否为某个选项名做存在性判断。reject 语义在 UI 层不存在（无法选到不存在的项）。
          const labels = await choiceOptionLabels(modal, check.field, fieldDefs[check.field] || {});
          const norm = (s: string) => (s || '').replace(/\s+/g, '');
          if (!labels.length) {
            recAll(check, name, 'unverified', '未读到勾选选项（可能是单勾选框或结构变更）');
            continue;
          }
          const m = String(check.rule || '').match(/(\d+)\s*[組组項项個个]/);
          const want = m ? parseInt(m[1], 10) : 0;
          if (want > 0) {
            recAll(check, name, labels.length === want ? 'pass' : 'unverified',
                   labels.length === want
                     ? `勾选组共 ${labels.length} 个选项（${labels.join('/')}），与文档「${check.rule}」一致`
                     : `勾选组实际 ${labels.length} 个选项（${labels.join('/')}），文档要求 ${want} 组`);
            continue;
          }
          if (String(check.expect).startsWith('reject')) {
            // rev167：reject 语义与 radio_options 对齐——**只判「非法值能否被选到」**。
            // 勾选组物理上选不到不存在的值 ⇒ 不存在即 pass（TR-785 实测：
            // 「不存在的枚举值」不在 外卖/自提/堂食 里却报 unverified，是判向反了）。
            const exists = labels.some((l) => norm(l).includes(norm(check.data)));
            recAll(check, name, exists ? 'unverified' : 'pass',
                   exists ? `非法值 ${check.data} 竟出现在勾选组里（${check.rule}）`
                          : `勾选组中不存在 ${check.data || '(空)'}，用户无法勾选非法值（${check.rule}）`);
            continue;
          }
          if (check.data) {
            const exists = labels.some((l) => norm(l).includes(norm(check.data)));
            recAll(check, name, exists ? 'pass' : 'unverified',
                   exists ? `勾选组中存在 ${check.data}（${check.rule}）`
                          : `勾选组中未观测到 ${check.data}（实际：${labels.join('/')}）`);
            continue;
          }
          recAll(check, name, 'pass', `勾选组选项：${labels.join('/')}`);
          continue;
        }
        if (check.type === 'select_options') {
          // 下拉的选项范围：展开读取选项文本即可判定（无自由输入路径）
          const labels = await selectOptionLabels(page, anyScope, modal, check.field, fieldDefs[check.field] || {});
          const allowed: string[] = check.allowed || [];
          const norm = (s: string) => (s || '').replace(/\s+/g, '');
          if (!labels.length) {
            recAll(check, name, 'unverified', '未读到下拉选项（可能未成功展开或无选项）');
            continue;
          }
          if (String(check.expect).startsWith('reject')) {
            // reject 分支**只**判"非法值能否被选到"，不因 allowed 里声明的选项缺失而改判：
            // 实测（Brand）：断言 allowed=['ETZ'] 而系统实际无 ETZ，若先查 allowed，
            // 这条 reject 会被判 unverified，把"文档与系统不一致"这条真发现埋掉。
            // 声明选项的缺失由 accept 那条承载（它会带实际选项列表报 unverified）。
            const exists = labels.some((l) => norm(l).includes(norm(check.data)));
            recAll(check, name, exists ? 'unverified' : 'pass',
                   exists ? `非法值 ${check.data} 竟出现在选项里（${check.rule}）`
                          : `下拉中不存在 ${check.data || '(空)'}，用户无法选择非法值（${check.rule}）`);
          } else if (allowed.some((a) => !labels.some((l) => norm(l).includes(norm(a))))) {
            const missing = allowed.filter((a) => !labels.some((l) => norm(l).includes(norm(a))));
            recAll(check, name, 'unverified', `未观测到选项：${missing.join('、')}（实际：${labels.join('/')}）`);
          } else if (check.data) {
            const exists = labels.some((l) => norm(l).includes(norm(check.data)));
            recAll(check, name, exists ? 'pass' : 'unverified',
                   exists ? `合法选项 ${check.data} 存在（${check.rule}）`
                          : `未观测到合法选项 ${check.data}（${check.rule}）`);
          } else {
            recAll(check, name, 'pass', `选项范围齐全：${labels.join('/')}`);
          }
          continue;
        }
        if (check.type === 'visibility') {
          await ensureMode(page, modal, check.mode);
          // rev109：切换源是 switch（YL 店铺 排隊/訂位/外賣）时点开关而不是单选——
          // radioOption 按中文字段名 has-text 找 .ant-radio-wrapper，英文页面上必落空。
          const vdef = fieldDefs[check.field] || {};
          const expect: string[] = check.expect_labels || [];
          const missingNow = async () => {
            const miss: string[] = [];
            for (const label of expect) {
              if (!(await modal.locator(`.ant-form-item:has-text("${label}")`).count())) miss.push(label);
            }
            return miss;
          };
          // 候选切换控件：selector_hint 优先、中文字段名兜底（与旧实现一致），
          // 但**按效果逐个试**——见下面的实测教训。
          const cands: any[] = [];
          const byHint = hintLocator(modal, vdef);
          if (byHint) cands.push(byHint);
          if (String(vdef.kind || '') === 'switch') {
            cands.push(modal.locator(`.ant-form-item:has-text("${check.field}") .ant-switch`).first());
          } else {
            const option = radioOption(modal, check.field, check.option, vdef);
            if (option) cands.push(option);
          }
          // 2026-10-16 实测（TR-422 → TR-431）：
          //   旧实现是「点一下 → 固定等 900ms → 采样一次」，把**两种完全不同的情况**
          //   压成同一句结论 —— YL 店铺 `visibility:排隊=on` 被判
          //   unverified「切换后未出现：Third-Party Store Code、Priority Ticket Level Type」，
          //   但在**空表单**上实测：点开 Queue 开关后**500ms 内**这两个字段就出现了
          //   （可见字段 22 → 25，TR-431 逐点观察 T500/T1000/…/T8000 全部 third=1 tlt=1）。
          //   ⇒ SUT 渲染是对的，**是我们的观测不可靠**（没点中 / 没等够都长得一样）。
          //   现在：点 → **轮询等效果（≤5s）** → 没效果就试下一个候选；
          //   并记录真正生效的候选序号与切换前的 class，便于回溯"到底点了哪个"。
          let toggled = false;
          let usedIdx = -1;
          let usedLoc: any = null;
          let beforeCls = '';
          // 2026-10-16（TR-422/TR-447 两次同结果 → TR-448 反证）：
          //   同一份产物，**空表单**上手工复刻这条路径（同样的候选1定位器 +
          //   Playwright click）是**成功**的：Queue 唯一命中、class 由
          //   `ant-switch` 翻成 `ant-switch-checked`、500ms 内两字段出现。
          //   但正式通道里恒判"未出现"且**没有 via 段**（= usedIdx 停在 -1，
          //   说明两个候选一个都没点到）。"没点中"与"真没出现"在旧判据下
            //   长得一模一样 —— 直接违反项目"失败必须能看到现场"的口径。
          //   故补一行 VISIBILITY_TRACE：逐候选记 count / 点击异常 / 前后 class，
          //   并带上作用域内 .ant-form-item 总数（用于区分"scope 拿错页面"）。
          const trace: any[] = [];
          // 作用域自检：若这里为 0，说明 modal 指向的不是真正承载表单的文档
          // （YL 店铺表单在 mkt iframe 内），后续所有定位都必然落空。
          const scopeItems = await modal.locator('.ant-form-item').count().catch(() => -1);
          for (let i = 0; i < cands.length && !toggled; i += 1) {
            const c = cands[i];
            const cz = await c.count().catch(() => -1);
            if (!c || !cz) { trace.push({ i: i + 1, cnt: cz, act: 'skip' }); continue; }
            beforeCls = String(await c.getAttribute('class').catch(() => '') || '');
            let clickErr = '';
            try {
              await c.click({timeout: 8000});
            } catch (e: any) {
              clickErr = String((e && e.message) || e).replace(/\s+/g, ' ').slice(0, 140);
            }
            let midCls = beforeCls;
            if (String(vdef.kind || '') === 'switch') {
              // 开关没翻转 ⇒ 这次点击没生效（被遮挡/点到标签等），换原生 click 再试一次。
              // 原生 el.click() 是 TR-411/TR-430 实测有效的兜底路径。
              midCls = String(await c.getAttribute('class').catch(() => '') || '');
              if (midCls === beforeCls) {
                await c.evaluate((el: any) => el.click()).catch(() => undefined);
                midCls = String(await c.getAttribute('class').catch(() => '') || '');
              }
            }
            for (let k = 0; k < 12; k += 1) {
              await page.waitForTimeout(400);
              if (!(await missingNow()).length) { toggled = true; usedIdx = i; usedLoc = c; break; }
            }
            trace.push({ i: i + 1, cnt: cz, before: beforeCls.slice(0, 30),
                         after: midCls.slice(0, 40), err: clickErr, land: toggled ? 1 : 0 });
          }
          const missing = await missingNow();
          console.log('VISIBILITY_TRACE ' + JSON.stringify({
            field: check.field, opt: check.option, n: cands.length,
            scopeItems, missing, trace,
          }));

          if (!toggled && !missing.length) { toggled = true; usedIdx = -1; }
          if (!toggled && usedIdx < 0 && !cands.length) {
            recAll(check, name, 'unverified', `未找到切换控件 ${check.field}=${check.option}`);
            continue;
          }
          const via = usedIdx >= 0
            ? `（候选 #${usedIdx + 1} 生效；切换前 class=${beforeCls.slice(0, 48) || '(空)'}）`
            : '';
          recAll(check, name, missing.length === 0 ? 'pass' : 'unverified',
                 missing.length === 0 ? `切换后字段出现：${expect.join('、')}${via}`
                                      : `切换后未出现：${missing.join('、')}${via}`);
          // 复位：字段级检查只该"看"，不该把开关留在 ON 上。TR-422 实测残留的
          // 「外賣=on」让随后的提交阶段多出一条必填错误（Takeout Redirect Link），
          // 把"未填项"记到了并不属于本次场景的字段上。只对 switch 复位
          // （radio 无法可靠回到原选项，不强做）。
          if (usedIdx >= 0 && usedLoc && String(vdef.kind || '') === 'switch') {
            const afterCls = String(await usedLoc.getAttribute('class').catch(() => '') || '');
            if (afterCls !== beforeCls) {
              await usedLoc.click({timeout: 5000}).catch(() => undefined);
              await page.waitForTimeout(300);
            }
          }
          continue;
        }
        if (check.type === 'required_error') {
          const def = fieldDefs[check.field] || {};
          await ensureMode(page, modal, check.mode);
          const input = await fieldControl(modal, check.field, def);
          if (!(await input.count())) { await reportLocatorMiss('form:required_error', check, modal); recAll(check, name, 'unverified', '未定位到输入框（定位现场见 [observation] 行）'); continue; }
          // rev116（2026-10-17，P1-③）：**控件类型守卫**。定位器把它声明成 input，但真实控件是
          // checkbox/radio/switch 时，fill 会抛 ``Input of type "checkbox" cannot be filled``
          // 并把整条用例判 blocked（实测 TR-500 營業時間）。这类控件没有自由输入路径，
          // 如实记 unverified 并转负路径提交层——让"配置类型写错"表现为可诊断的未验证，
          // 而不是伪装成被测系统缺陷/硬失败。
          const ctrl = await input.evaluate((el: any) => {
            const tag = String(el.tagName || '').toLowerCase();
            const type = tag === 'input' ? String(el.type || '') : '';
            const sw = !!(el.closest && el.closest('.ant-switch'));
            return { tag, type, sw };
          }).catch(() => ({ tag: '', type: '', sw: false } as any));
          if (ctrl.type === 'checkbox' || ctrl.type === 'radio' || ctrl.sw) {
            const actual = ctrl.sw ? 'switch' : ctrl.type;
            recAll(check, name, 'unverified',
                   `控件实际类型为 ${actual}（定位器声明为 input），无法 fill：必填校验需提交层/API 层覆盖`);
            continue;
          }
          // rev92：antd Select 必填——展开下拉后 Esc 取消（无选择变更），再 blur
          // 尝试触发校验；无提示则如实 unverified 并交负路径提交层（真实提交验证）。
          if (String(def.kind || '') === 'select') {
            const root0 = selectRoot(modal, check.field, def);
            if (!(await root0.count())) { recAll(check, name, 'unverified', '未定位到下拉控件'); continue; }
            await root0.click({timeout: OP_T}).catch(() => undefined);
            await page.waitForTimeout(800);
            await page.keyboard.press('Escape').catch(() => undefined);
            await page.waitForTimeout(300);
            await modal.locator('.ant-form-item-label, label').first().click({timeout: 5000}).catch(() => undefined);
            const selErr = await itemHasErrorCtl(page, modal, check.field, def);
            recAll(check, name, selErr ? 'pass' : 'unverified',
                   selErr ? '展开下拉后取消出现必填校验提示'
                          : '展开/取消下拉未触发必填提示（无变更事件，进入负路径提交层重试）');
            if (!selErr) submitQueue.push({check, name, kind: 'required_empty'});
            continue;
          }
          // rev92：DatePicker 必填——选一个日期再点清除（真实 onChange）触发校验。
          if (String(def.kind || '') === 'date') {
            const diag = await datePickThenClear(page, anyScope, modal, check.field, def);
            if (diag) { recAll(check, name, 'unverified', `日期控件交互失败：${diag}`); continue; }
            await modal.locator('.ant-form-item-label, label').first().click({timeout: 5000}).catch(() => undefined);
            const dateErr = await itemHasErrorCtl(page, modal, check.field, def);
            recAll(check, name, dateErr ? 'pass' : 'unverified',
                   dateErr ? '选日期后清除出现必填校验提示' : '选/清日期后未出现必填提示（可能仅提交时校验）');
            if (!dateErr) submitQueue.push({check, name, kind: 'required_empty'});
            continue;
          }
          // rev71f：只读选择器（如 CS 的 Member，input[readonly] 由弹窗回填）——
          // fill 等待 editability 永不满足（TR-106 实测挂满 20 分钟根因）。
          // 这类字段 UI 层无法经"输入→清空"触发必填校验，如实记 unverified。
          const roAttr = await input.getAttribute('readonly', {timeout: 3000}).catch(() => null);
          if (roAttr !== null) {
            recAll(check, name, 'unverified', '字段为只读选择器（readonly input，值由选择弹窗回填），UI 层无法经输入触发必填校验（需选择器路径或 API 层验证）');
            continue;
          }
          // rev85：可输入性预检——UEditor 富文本等控件的同步 textarea 是 hidden/readonly，
          // 直接 fill 会等满 OP_T（实测 Task/Phone 的 Description blocked×2）。
          const editableChk = await input.evaluate((el: any) => {
            const ro = el.getAttribute && el.getAttribute('readonly');
            const dis = el.getAttribute && el.getAttribute('disabled');
            const t = String((el.getAttribute && el.getAttribute('type')) || '').toLowerCase();
            const visible = !!(el.offsetWidth || el.offsetHeight);
            return ro === null && dis === null && t !== 'hidden' && visible;
          }, {timeout: 5000}).catch(() => true);
          if (!editableChk) {
            recAll(check, name, 'unverified', '控件不可编程输入（隐藏同步域/富文本编辑器/不可见），UI 输入层不适用（需富文本交互路径或 API 层验证）');
            continue;
          }
          // 先填再清：antd 的 required 规则默认 onChange 触发，对空字段 fill('') 不产生事件
          await input.fill('1', {timeout: OP_T});
          await page.waitForTimeout(200);
          // rev165：**先取基线**（填值态下的提示）。此处若有提示，说明是上一次校验遗留的，
          // 后面"清空后仍有提示"就不能当本次触发的证据 —— 旧行正是这样产生伪 pass。
          const errBase = await itemErrorTexts(modal, check.field, def);
          await input.fill('', {timeout: OP_T});
          // ① onChange 时机：**不 blur** 先采样（antd v3 required 默认 validateTrigger=onChange）
          await page.waitForTimeout(180);
          const errOnChange = await itemErrorTexts(modal, check.field, def);
          const freshOnChange = errOnChange.filter((t) => errBase.indexOf(t) < 0);
          // ② onBlur 时机：onChange 没拿到**新**提示才继续失焦（命中 onChange 就省掉 blur）
          let errBlur = errOnChange;
          if (!freshOnChange.length) {
            await input.blur({timeout: OP_T});
            await page.waitForTimeout(600);
            errBlur = await itemErrorTexts(modal, check.field, def);
          }
          const freshBlur = errBlur.filter((t) => errBase.indexOf(t) < 0);
          let timing = freshOnChange.length ? 'on_change' : (freshBlur.length ? 'on_blur' : 'none');
          // 兜底：只有 has-error class、既无文案也未被 itemErrorTexts 捕获的 SUT
          if (timing === 'none' && await itemHasErrorCtl(page, modal, check.field, def, 0)) timing = 'flag';
          console.log('REQUIRED_TIMING ' + JSON.stringify({
            field: check.field, timing,
            baseline: errBase.slice(0, 3),
            onChange: errOnChange.slice(0, 3),
            afterBlur: errBlur.slice(0, 3),
            preexisting: errBase.length > 0,
          }));
          const hasError = timing !== 'none';
          const timingText = timing === 'on_change' ? '清空输入即出现（onChange 触发）'
            : timing === 'on_blur' ? '失焦后出现（onBlur 触发）'
            : timing === 'flag' ? '控件挂 has-error 但未渲染文案（仅标记）' : '';
          const errText = (freshOnChange.length ? freshOnChange : freshBlur)[0] || '';
          recAll(check, name, hasError ? 'pass' : 'unverified',
                 hasError
                   ? `字段级必填提示，${timingText}${errText ? `：${errText}` : ''}`
                   : '清空与失焦均未出现字段级提示：该字段为提交时校验（交提交层按 toast/请求判定）');
          if (!hasError) submitQueue.push({check, name, kind: 'required_empty'});
          continue;
        }
        if (check.type === 'input_validation') {
          const def = fieldDefs[check.field] || {};
          await ensureMode(page, modal, check.mode);
          const input = await fieldControl(modal, check.field, def);
          if (!(await input.count())) { await reportLocatorMiss('form:input_validation', check, modal); recAll(check, name, 'unverified', '未定位到输入框（定位现场见 [observation] 行）'); continue; }
          // rev79：antd Select 下拉控件的取值/边界校验需下拉交互路径，如实记 unverified
          if (String(def.kind || '') === 'select') {
            recAll(check, name, 'unverified', 'antd Select 下拉控件：输入校验需下拉交互路径，UI 输入层不适用（需下拉选择器路径或 API 层验证）');
            continue;
          }
          // rev71f：只读选择器同样无法注入探针值（同 required_error 守卫）
          const roAttr2 = await input.getAttribute('readonly', {timeout: 3000}).catch(() => null);
          if (roAttr2 !== null) {
            recAll(check, name, 'unverified', '字段为只读选择器（readonly input），UI 层无法注入边界探针值（需选择器路径或 API 层验证）');
            continue;
          }
          // rev85：可输入性预检（同 required_error），避免 fill 等满 OP_T 变 blocked
          const editableChk2 = await input.evaluate((el: any) => {
            const ro = el.getAttribute && el.getAttribute('readonly');
            const dis = el.getAttribute && el.getAttribute('disabled');
            const t = String((el.getAttribute && el.getAttribute('type')) || '').toLowerCase();
            const visible = !!(el.offsetWidth || el.offsetHeight);
            return ro === null && dis === null && t !== 'hidden' && visible;
          }, {timeout: 5000}).catch(() => true);
          if (!editableChk2) {
            recAll(check, name, 'unverified', '控件不可编程输入（隐藏同步域/富文本编辑器/不可见），UI 层无法注入边界探针值（需富文本交互路径或 API 层验证）');
            continue;
          }
          // data 可能是人类可读描述（"500字"）→ 用服务端合成的**真实探针值**（500/501 字符）
          const probeValue = String((check as any).probe_value || check.data || '');
          const probeNote = String((check as any).probe_note || '');
          const label = probeNote ? `${probeNote}` : `输入 ${probeValue}`;
          await input.fill(probeValue, {timeout: OP_T});
          await input.blur({timeout: OP_T});
          // rev140：错误检测锚定控件（与 required_error 同源，见 itemHasErrorCtl）
          const invalid = await itemHasErrorCtl(page, modal, check.field, def);
          const actual = await controlValue(input);
          // rev93（2026-10-07 实测 artifacts/b_limit_period_input.json）：部分控件"先显示后回滚"——
          // antd RangePicker 键入 2026-10-01 时 inputValue 可读到该文本（面板未确认），**失焦后回滚为空**。
          // 旧逻辑用 `actual !== probeValue` 判"被截断"，会把"控件根本没接受输入"记成 pass（假通过）。
          // 故把"值丢失（探针值非空、实际为空）"单独成类：只记 unverified，并交提交层做真实提交验证。
          const valueLost = probeValue !== '' && actual === '';
          const truncated = !valueLost && actual !== probeValue;
          const expectReject = String(check.expect).startsWith('reject');
          if (expectReject) {
            recAll(check, name, (invalid || truncated) ? 'pass' : 'unverified',
                   invalid ? `${label} 出现校验提示（${check.rule}）`
                           : (valueLost
                              ? `${label} 控件未保留输入（失焦后为空），未观测到拒绝证据（需控件专用交互路径或 API 层验证）`
                              : (truncated ? `${label} 被截断为 ${actual.length} 字符（${check.rule}）`
                                           : `${label} 未观测到客户端校验：实际长度 ${actual.length}，无校验提示（可能仅服务端校验）`)));
            if (!invalid && !truncated) submitQueue.push({check, name, kind: 'probe', probeValue});
          } else {
            recAll(check, name, (invalid || valueLost) ? 'unverified' : 'pass',
                   invalid ? `${label} 被判为非法（${check.rule}）`
                           : (valueLost
                              ? `${label} 控件未保留输入（失焦后为空），无法判定"被接受"（需控件专用交互路径或 API 层验证）`
                              : `${label} 通过（${check.rule}）`));
          }
          continue;
        }
        recAll(check, name, 'unverified', `未支持的检查类型 ${check.type}`);
      } catch (error) {
        console.log(`CS_PAGE_DIAG check=${name} ${await pageDiag(page)}`);
        recAll(check, name, 'blocked', `执行异常：${String(error).slice(0, 300)}`);
      }
    }
    // rev144：ERRORED_ITEM + 兜底填充**提升到提交层/场景层共享作用域**——
    // 负路径提交层（fillEmptyErrored）与规则级场景（提交前 pre-flight）都要用。
    // v3 形态说明见下方原注释（TR-759 SUBMIT_ERR_DETAIL 实锤）。
    const ERRORED_ITEM = '.ant-form-item.has-error, .ant-form-item:has(.ant-form-item-explain-error), .ant-form-item:has(.ant-form-explain), .ant-form-item:has(.has-error)';
    // 把当前可见的 errored 必填项填上（输入框填值；radio/checkbox 真实点击未选中项）。
    // 返回处理条数。``skipMarked``：跳过带 rev69ut 标记的「目标字段」form-item。
    async function fillErroredItems(container: any, skipMarked: boolean): Promise<number> {
      const items = container.locator(ERRORED_ITEM);
      const n = await items.count();
      let handled = 0;
      for (let i = 0; i < n; i++) {
        const it = items.nth(i);
        if (skipMarked && await it.evaluate((el: any) => !!(el as any).dataset && (el as any).dataset.rev69ut === '1').catch(() => false)) continue;
        const label = String(await it.locator('label').first().innerText({timeout: 1000}).catch(() => ''));
        const fileInput = it.locator('input[type=file]').first();
        if (await fileInput.count()) {
          await fileInput.setInputFiles(
            {name: 'probe.txt', mimeType: 'text/plain', buffer: Buffer.from('rev69 negative-path probe')},
            {timeout: 2000}).catch(() => undefined);
          handled += 1;
          continue;
        }
        const rcInput = it.locator('input[type=radio], input[type=checkbox]').first();
        if (await rcInput.count().catch(() => 0)) {
          const wrappers = it.locator('.ant-radio-wrapper:visible, .ant-checkbox-wrapper:visible');
          const wn = await wrappers.count().catch(() => 0);
          let done = false;
          for (let wi = 0; wi < wn; wi++) {
            const w = wrappers.nth(wi);
            const checked = await w.locator('input').first().isChecked().catch(() => false);
            if (!checked) { await w.click({timeout: 1500}).catch(() => undefined); done = true; break; }
          }
          if (!done && wn) await wrappers.first().click({timeout: 1500}).catch(() => undefined);
          if (!wn) await rcInput.check({timeout: 1500}).catch(() => undefined);
          handled += 1;
          continue;
        }
        const input = it.locator('input:visible, textarea:visible').first();
        if (!(await input.count())) continue;   // 单选类无法程序化填充
        const val = String(await input.inputValue({timeout: 500}).catch(() => ''));
        if (val) continue;                       // 已有值（可能是目标探针字段）
        const v = /手机|电话|phone/i.test(label) ? FORM_PHONE : '1';
        await input.fill(v, {timeout: 1000}).catch(() => undefined);
        await input.blur({timeout: 1000}).catch(() => undefined);
        handled += 1;
      }
      return handled;
    }
    // ===== 负路径提交层校验（rev67，2026-09-25 用户确认口径）=====
    // 只对客户端层未验证的「期望拒绝」断言执行真实提交。顺序：先 probe（把非法探针值
    // 提交到服务端，其余空必填用兜底值填充以触达服务端），后 required_empty（客户端必填拦截即可判定）。
    if (submitQueue.length) {
      let curModal: any = modal;
      // antd v3 中文 locale 的确认按钮渲染为「确 定」（带空格）→ 正则必须容忍空白；
      // 文本匹配不到时兜底用 footer 主按钮（取消在前、主操作在后）。
      // O1c/rev100：提交点击**有界降级**。实测（TR-282/283/288，WT-6C6D1A99 整页表单）：
      // 点提交 8s 硬超时抛 TimeoutError → 提交层单项 blocked → summary.blocked=1 硬失败
      // 拖死整批（与 rev90 预算闸同源的「单 item 拖死整批」第二实例，§11 待办 O1c）。
      // 口径：先**无等待**探测按钮存在性——footer 与顶栏候选**都空**才是结构性失效
      //（记 blocked，定位器真失效必须见光）；按钮在但点不动（遮罩/竞态/页面已迁移）
      // ⇒ 该项记 unverified（无服务端证据，如实）+ 诊断输出，**队列继续**。
      const findSubmitBtn = async (): Promise<any | null> => {
        const footer = curModal.locator('.ant-modal-footer button');
        // rev98：补 发送/Send（O10 toolbar_modal 的 email 弹窗形态，实测 67197 入口）。
        // 只扩展候选集合，既有页面仍由排在前面的关键词命中，不影响已覆盖资产。
        // rev108：补 确认/Confirm（YL 店铺主数据整页表单实测 2026-10-15：提交按钮为
        // 「Confirm/Cancel」平铺在页面底部，无 .ant-modal-footer；旧候选 保存/Save/Submit
        // 全部落空 → 提交层 16 条断言全部 unverified「未找到提交按钮」）。
        let btn = footer.filter({hasText: /确\s*定|提\s*交|保\s*存|确\s*认|发\s*送|Send|Confirm|ok/i}).last();
        if (!(await btn.count())) btn = footer.locator('.ant-btn-primary').last();
        if (!(await btn.count())) {
          // rev79：整页表单（direct_url 流程，如 CS RetroClaim/GoodWill 创建页）
          // 没有 .ant-modal-footer——顶栏 Save 按钮直接在页面作用域里找
          btn = anyScope.locator('button:visible').filter({hasText: /保\s*存|Save|Submit|确\s*认|Confirm/i}).first();
        }
        return (await btn.count()) ? btn : null;
      };
      const clickSubmit = async (): Promise<{ok: boolean; reason: string}> => {
        const btn = await findSubmitBtn();
        if (!btn) return {ok: false, reason: '未找到提交按钮（modal footer 与顶栏候选均为空）'};
        try {
          await btn.click({timeout: 8000});
          return {ok: true, reason: ''};
        } catch (e) {
          return {ok: false, reason: String(e).slice(0, 140)};
        }
      };
      // antd v3/v4 兼容的错误项选择器
      // rev69 网络响应捕获层：页面证据为主、网络证据为辅。收集提交窗口内的写请求响应
      //（状态码 + 响应体摘要），用于给「页面无拒绝证据」的负路径项补上服务端侧确定性结论。
      // tk 令牌隔离每个用例项的收集窗口，防止上一项的迟到响应污染下一项判定。
      // requestId（P1-3 阶段 A 新增）：缺陷单必须携带的定位字段（实测 DEFECT-001 工单
      // 用的就是 requestId）。此前只存 url/status/body，取证时还得回页面手工翻——
      // 这里直接从响应头取，覆盖常见的三种命名（大小写不敏感读取由 Playwright 保证）。
      const netLog: Array<{tk: number; url: string; status: number; body: string; requestId: string}> = [];
      let netToken = 0;
      // rev69 诊断：request 级捕获（区分「请求从未发出」vs「发出但无响应」）
      const reqLog: Array<{tk: number; m: string; url: string}> = [];
      // rev130（2026-10-20 F-2）：**提交层的监听器同样要挂应用所在页面**。
      // rev129 只修了规则级负向场景的监听器（见下方 listenPage），提交层这四处仍是
      // `page.on(...)`：门户卡片式应用（数盈营销云/交易云）点卡片后新开标签页，
      // 主 page 是控制台门户 —— 一个请求都收不到，于是服务端已受理却被判 unverified
      // （与 TR-741/742 完全同源，只是触发条件是 submit_phase 的 probe/required_empty）。
      // anyScope 是 Page（有 .on）→ 挂它；是 FrameLocator（iframe 应用）→ 回退 page。
      const submitListenPage = (typeof (anyScope as any).on === 'function') ? anyScope : page;
      const onNetReq = (r: any) => {
        try {
          if (String(r.method()).toUpperCase() === 'GET') return;
          reqLog.push({tk: netToken, m: String(r.method()), url: String(r.url()).slice(0, 90)});
        } catch { /* ignore */ }
      };
      submitListenPage.on('request', onNetReq);
      // rev69 诊断：JS 报错捕获（提交点击若触发未处理异常会在这里现形）
      const errLog: Array<{tk: number; kind: string; text: string}> = [];
      const onPageErr = (e: any) => {
        errLog.push({tk: netToken, kind: 'pageerror', text: String((e && e.message) || e).slice(0, 150)});
      };
      const onConsoleMsg = (m: any) => {
        try {
          if (String(m.type()) === 'error') errLog.push({tk: netToken, kind: 'console', text: String(m.text()).slice(0, 150)});
        } catch { /* ignore */ }
      };
      submitListenPage.on('pageerror', onPageErr);
      submitListenPage.on('console', onConsoleMsg);
      const onNetResp = (r: any) => {
        try {
          const req = r.request();
          if (String(req.method()).toUpperCase() === 'GET') return;
          const rt = req.resourceType();
          if (rt !== 'xhr' && rt !== 'fetch') return;
          const url = String(r.url());
          const tk = netToken;
          // requestId 提取（P1-3 阶段 A）：响应头命名在不同网关下不一致，
          // 逐个候选取第一个非空值；取不到留空串（不伪造）。
          const pickRequestId = (resp: any): string => {
            try {
              const h = resp.headers() || {};
              for (const name of ['requestid', 'x-request-id', 'request-id', 'traceid', 'x-trace-id']) {
                const v = h[name];
                if (v) return String(v);
              }
            } catch { /* 头读取失败按取不到处理 */ }
            return '';
          };
          const requestId = pickRequestId(r);
          r.text().then((t: any) => {
            netLog.push({tk, url: url.slice(0, 150), status: r.status(), requestId,
                         body: String(t ?? '').replace(/\s+/g, ' ').slice(0, 200)});
          }).catch(() => {
            netLog.push({tk, url: url.slice(0, 150), status: r.status(), requestId, body: '<body不可读>'});
          });
        } catch { /* 监听失败不致命 */ }
      };
      submitListenPage.on('response', onNetResp);
      // 把"非目标字段"的空必填用兜底值填上（手机号用默认号码，其余用 '1'），
      // 让非法探针值能穿过客户端校验触达服务端（负路径：服务端应拒绝）。
      // rev144：逻辑提升到共享的 fillErroredItems（场景层 pre-flight 复用）
      const fillEmptyErrored = async () => {
        await fillErroredItems(curModal, true);
        await page.waitForTimeout(600);
      };
      const ordered = [...submitQueue].sort(
        (a, b) => (a.kind === 'probe' ? 0 : 1) - (b.kind === 'probe' ? 0 : 1));
      // rev90：提交层整体限时预算（5 分钟）——实测 RetroClaim 负路径提交在 Save 后
      // 挂起 20+ 分钟撞 runner 上限且 stdout 被杀丢失。预算耗尽后剩余项如实记
      // unverified（归 API 层），绝不让单个提交项拖死整批。
      const submitDeadline = Date.now() + 300000;
      // rev117（P0-1）：**单项看门狗**。rev90 的整体预算只在「项与项之间」检查，
      // 单个提交项在内部挂死时预算永远没机会再判——实测商品主数据 TC-238772 卡满
      // 1710s（撞 runner 上限）、stdout 零输出，连卡在哪一步都看不出来。
      // 口径：每项最多 SUBMIT_ITEM_MS（默认 90s，可由 WEB_SUBMIT_ITEM_MS 下发）；
      // 超时即记 unverified 并带上「卡在第几阶段」，剩余项标为页面状态不可信，
      // 绝不让单项吃掉整批预算。
      const SUBMIT_ITEM_MS = Number(process.env.WEB_SUBMIT_ITEM_MS || 0) || 90000;
      let submitAborted = '';
      for (const item of ordered) {
        const check = item.check;
        if (submitAborted) {
          submitRec(check, item.name, 'unverified', submitAborted);
          continue;
        }
        if (Date.now() > submitDeadline) {
          submitRec(check, item.name, 'unverified', '提交层整体超时预算（5分钟）耗尽，该项未验证（需 API 层验证）');
          continue;
        }
        // 阶段心跳：卡死时能直接看到停在哪一步（每行压短，平台会截断 stdout）
        let submitStage = 'init';
        const submitT0 = Date.now();
        const markStage = (name: string) => {
          submitStage = name;
          console.log(`SUBMIT_STAGE ${JSON.stringify({ item: String(item.name).slice(0, 40), stage: name, elapsed_ms: Date.now() - submitT0 })}`);
        };
        // 单项执行体：原循环体原样搬入（continue → return），返回即「该项结束」
        const runSubmitItem = async (): Promise<void> => {
          markStage('open_form');
          // 韧性：页面/弹窗中途死亡时降级为 unverified 并跳过，不让整轮挂死
          if (typeof (page as any).isClosed === 'function' && (page as any).isClosed()) {
            submitRec(check, item.name, 'unverified', '提交层：页面已关闭，无法继续验证'); return;
          }
          if (!(await curModal.isVisible().catch(() => false))) {
            try { curModal = await openForm(); }
            catch { submitRec(check, item.name, 'unverified', '提交层：表单弹窗无法重开，跳过该项'); return; }
          }
          const def = fieldDefs[check.field] || {};
          // 1) 复位目标字段状态：required_empty 清空；probe 填非法探针值
          await ensureMode(page, curModal, check.mode);
          const input = await fieldControl(curModal, check.field, def);
          markStage('field_ready');
          // rev69：给「目标字段」的 form-item 打标记，兜底填充(fillEmptyErrored)跳过它，
          // 保证负路径「仅目标字段违规、其余字段合法」成立（否则空必填兜底会把目标字段也填上）
          const underItem = (await input.count())
            // rev141：精确 token 匹配（同 itemHasErrorCtl）——松匹配会命中
            // .ant-form-item-control，标记打错元素 ⇒ 兜底填充会把目标字段填上
            ? input.locator('xpath=ancestor::div[contains(concat(" ", normalize-space(@class), " "), " ant-form-item ")][1]') : null;
          if (underItem && (await underItem.count())) {
            await underItem.evaluate((el: any) => { (el as any).dataset.rev69ut = '1'; }).catch(() => undefined);
          }
          // rev71f：只读选择器在提交层同样无法注入（fill 等待 editability 超时）→ 如实记 unverified
          if (await input.count()) {
            const roSub = await input.getAttribute('readonly', {timeout: 3000}).catch(() => null);
            if (roSub !== null) {
              submitRec(check, item.name, 'unverified', '提交层：字段为只读选择器（readonly input），无法注入探针/清空触发必填校验');
              return;
            }
          }
          // rev79：antd Select 下拉控件在提交层同样无法 fill（组件内部伪 input 会抛异常）→ 如实记 unverified
          if (String(def.kind || '') === 'select') {
            submitRec(check, item.name, 'unverified', '提交层：antd Select 下拉控件无法注入探针值，需下拉交互路径或 API 层验证');
            return;
          }
          markStage('prepare_value');
          if (item.kind === 'probe') {
            if (!(await input.count())) { await reportLocatorMiss('submit_phase', item.check || check, modal); submitRec(check, item.name, 'unverified', '提交层：未定位到输入框（定位现场见 [observation] 行）'); return; }
            await input.fill(String(item.probeValue || check.data || ''), {timeout: OP_T});
            await input.blur({timeout: OP_T});
          } else if (await input.count()) {
            // rev69：上传类目标字段用 setInputFiles([]) 清空（fill 对 file input 无效）并清残留附件块
            const ftype = await input.getAttribute('type', {timeout: 1000}).catch(() => null);
            if (ftype === 'file') {
              await input.setInputFiles([], {timeout: 2000}).catch(() => undefined);
              const chips = curModal.locator('.ant-upload-list-item');
              const cn = await chips.count();
              for (let ci = cn - 1; ci >= 0; ci--) {
                const del = chips.nth(ci).locator('.anticon-close, .anticon-delete');
                if (await del.count()) await del.click({timeout: 1000}).catch(() => undefined);
              }
            } else {
              await input.fill('', {timeout: OP_T});
              await input.blur({timeout: OP_T});
            }
          }
          // rev144/rev146：block_siblings（fields.<字段>.block_siblings = [{field, value}]）——
          // 本 SUT 卡片校验**逐字段只报第一个空字段**（toast「题目1：请输入标题」），
          // 且每个提交项都重开表单 ⇒ 标题恒空 ⇒ 选项1 的 toast 永远轮不到 ⇒ 归因无门
          //（TR-761 实测：选项1 的 block_toast 只有两条标题）。提交前把声明的兄弟必填
          // 先填上，让 toast 只剩目标字段自己的那条。
          // rev146：直接读 fieldDefs——`itemDef` 声明在下方 TOAST 区，此处引用会 TDZ
          //（TR-762 实测 ReferenceError ⇒ blocked=2 硬失败闸）。
          const sibOwner = fieldDefs[check.field] || {};
          for (const sib of (Array.isArray((sibOwner as any).block_siblings) ? (sibOwner as any).block_siblings : [])) {
            if (sib && sib.field) {
              const sibDef = fieldDefs[String(sib.field)] || {};
              const sibCtl = await fieldControl(curModal, String(sib.field), sibDef);
              if (await sibCtl.count().catch(() => 0)) {
                await sibCtl.fill(String(sib.value == null ? '1' : sib.value), {timeout: OP_T}).catch(() => undefined);
                await sibCtl.blur({timeout: OP_T}).catch(() => undefined);
              }
            }
          }
          // 2) 点击提交并**轮询**判定信号（字段提示/错误toast/弹窗关闭任一命中即停）；
          //    probe/required_empty 若被其他空必填拦截 → 兜底填充（跳过目标字段）后重试；
          //    确认层出现 → 点确认放行到服务端。
          const TOAST_SEL = '.ant-message-notice-error, .ant-notification-notice-error, .ant-message .anticon-close-circle';
          // rev142（2026-10-20 探针4 + 截图实锤）：本 SUT 的必填拦截是**warning 样式
          // toast**（「题目 11：请输入标题」，橙色叹号）——不在 TOAST_SEL 的 error 类里，
          // 也不是卡片 explain（卡片校验走 toast，不走 inline）。故按**文本**匹配任意
          // message/notification 通知，不再依赖样式类。
          const TOAST_BLOCK_RE = /请输入|请选择|必填|不能为空|请填写/;
          // rev144：返回**全部**命中文案的拼接——一保存可能弹多条 toast
          //（「请输入标题」+「请输入选项内容」逐字段各一条），只取第一条会让
          // 选项1 的归因被标题的 toast 挡掉。
          const blockToastText = async (): Promise<string> => {
            const texts = await anyScope.locator('.ant-message-notice, .ant-notification-notice')
              .allInnerTexts().catch(() => [] as string[]);
            return (texts || []).map((t) => String(t || '').replace(/\s+/g, ' ').trim())
              .filter((t) => TOAST_BLOCK_RE.test(t)).join(' | ');
          };
          // rev142：归因表——由编译期 ``field_alias_applied``（文档字段名→定位器键）
          // 反转出「定位器键 → 页面别名」。toast 文案「题目 11：请输入**标题**」含的是
          // 页面标签「标题」而非规则字段名「题目标题」⇒ 归因必须同时查两者。
          const ALIAS_SOURCES: Record<string, string[]> = {};
          for (const a of (FORM_PLAN.field_alias_applied || [])) {
            if (a && a.from && a.to) (ALIAS_SOURCES[a.to] = ALIAS_SOURCES[a.to] || []).push(a.from);
          }
          const attrHit = (field: string, text: string): boolean => {
            if (!text) return false;
            if (text.includes(field)) return true;
            return (ALIAS_SOURCES[field] || []).some((s) => text.includes(s));
          };
          // rev140：全页错误元素基线（.ant-form-explain / v4 explain-error / .has-error 控件）——
          // 用于「无写请求 + 页面错误面扩大 = 前端拦截」的收敛防线（不区分是哪个字段，
          // 只作为 fieldError 未命中时的第二信号；文案里如实写明判定依据）。
          const formErrCount = async () => anyScope.locator('.ant-form-explain, .ant-form-item-explain-error').count().catch(() => -1);
          const formErrBase = await formErrCount();
          const itemDef = (FORM_PLAN.locators.fields || {})[check.field] || {};
          // rev69 关键修复：confirm 层和 toast 都渲染在应用 iframe 内（appScope=frameLocator），
          // 用 page.locator（主 frame）永远找不到——此前负路径提交被确认层卡死、请求发不出去。
          const confirmClick = async () => {
            const cb = anyScope.locator('.ant-modal-confirm .ant-btn-primary').first();
            const cn = await cb.count().catch(() => 0);
            if (cn && (await cb.isVisible().catch(() => false))) {
              await cb.click({timeout: 1500}).catch(() => undefined);
            }
          };
          const observe = async () => ({
            fieldError: await itemHasErrorCtl(page, curModal, check.field, itemDef, 250),
            toastError: await anyScope.locator(TOAST_SEL).count(),
            blockToast: await blockToastText(),
            stillOpen: await curModal.isVisible().catch(() => false),
          });
          const settleRound = async (rounds: number) => {
            const t0 = Date.now();
            for (let i = 0; i < rounds; i++) {
              await page.waitForTimeout(400);
              // rev143：toast **先**查——存续仅 ~3s，observe 全链（锚定检测+计数）~2.5s
              // 可能整段错过 toast 窗口；命中就用轻量信号直接返回。
              const btFast = await blockToastText();
              const s = btFast
                ? { fieldError: false, toastError: 0, blockToast: btFast, stillOpen: await curModal.isVisible().catch(() => false) }
                : await observe();
              // rev140：SUBMIT_SETTLE 时间线——命中即停的轮次如实打点，
              // 90s 卡死时能看出 settle 到底观察到了什么（而非只有 stage 名）。
              if (s.fieldError || s.toastError || s.blockToast || !s.stillOpen) {
                console.log(`SUBMIT_SETTLE ${JSON.stringify({ item: String(item.name).slice(0, 40), round: i + 1, elapsed_ms: Date.now() - t0, ...s })}`);
                return s;
              }
              await confirmClick();
            }
            console.log(`SUBMIT_SETTLE ${JSON.stringify({ item: String(item.name).slice(0, 40), rounds, elapsed_ms: Date.now() - t0, hit: false })}`);
            return null;
          };
          netToken += 1; netLog.length = 0;           // rev69：本用例项开启新的收集窗口
          markStage('submit_click');
          const sig = await (async () => {
            const first = await clickSubmit();
            // O1c/rev100：首次点击即失败 = 请求未发出、无可等证据 → 带标记返回，
            // 由外层记 unverified 并跳过观察窗（不空等 12s 网络轮询）
            if (!first.ok) return {__submitFailed: first.reason};
            const s1 = await settleRound(6);          // 第一轮 ~2.4s：页面证据优先
            if (s1) return s1;
            // rev69 守卫：兜底填充前确认目标字段状态——若兜底会污染目标字段（probe 值丢失/
            // 目标字段被填充）则放弃重试，避免把「正路径提交」误判成负路径结论
            let guardOk = true;
            if (item.kind === 'probe') {
              const cur = String(await input.inputValue({timeout: 500}).catch(() => ''));
              guardOk = cur === String(item.probeValue || check.data || '');
            } else {
              // rev144（2026-10-21，TR-760 实锤）：required_empty 到达这里时目标字段**必然
              // 无错误**（有错误 settleRound 早就命中返回了）⇒ 旧实现 guardOk 恒 false ⇒
              // `if (!guardOk) return` 恒早退 ⇒ fillEmptyErrored 对 required_empty 是
              // **死代码**，保存永远被「问卷标题/业务类型/优先级」三条外层必填拦死
              //（SUBMIT_ERR_DETAIL 实锤），卡片校验永远走不到。required_empty 的兜底填充
              // 只填**其他** errored 项（目标被 underItem 标记跳过），不污染负路径前提 ⇒
              // 应当放行。probe 的「值丢失则放弃重试」守卫保持不变。
              guardOk = true;
            }
            if (!guardOk) return await observe();
            await fillEmptyErrored();                 // 其他空必填拦截 → 兜底填充重试
            netToken += 1; netLog.length = 0;         // 重试窗口重新收集，排除首轮干扰
            const second = await clickSubmit();
            // 首轮已点过、重试点不动：页面可能已迁移/被遮罩——带上诊断走常规分类
            if (!second.ok) return {...(await observe()), __secondSubmitFailed: second.reason};
            const s2 = await settleRound(8);          // 第二轮 ~3.2s + 确认层放行
            return s2 || (await observe());
          })();
          markStage('settle_done');
          // O1c/rev100：首次点击失败 → 无服务端结论可下，如实记 unverified，队列继续
          if (sig && (sig as any).__submitFailed) {
            submitRec(check, item.name, 'unverified',
              `提交层：点击提交失败（${(sig as any).__submitFailed}）——未提交，无法取得服务端结论；队列继续`);
            return;
          }
          // rev69：扩展网络观察窗（~12s）——兜底填充的上传任务可能较慢，业务提交请求可能晚到；
          // 期间网络证据与页面证据任一命中即停，最终用最新页面状态判定。
          const isBizUrl = (u: string) => !/upload|file|oss|media|image|\.png|\.jpg|\.xlsx|\.xls/i.test(u);
          let late: any = null;
          let synthetic = false;
          let lastState = '';
          let stableRounds = 0;
          // rev140：观察窗限界 20s（30×400ms 名义 12s，但 observe/confirmClick 各自的
          // 定位等待在页面忙碌时会被拉长——TR-756 实测跑满 73s 触发 90s 看门狗）。
          // 超预算即跳出走判定分支：宁可如实报「无拒绝证据」，不再吃满单项预算。
          const lateT0 = Date.now();
          for (let i = 0; i < 30; i++) {
            await page.waitForTimeout(400);
            if (netLog.some((n) => n.tk === netToken && isBizUrl(n.url))) break;
            // rev143：toast 快查同 settle（~3s 窗口优先于 ~2.5s 的全量观察）
            const btFastL = await blockToastText();
            if (btFastL) {
              late = { fieldError: false, toastError: 0, blockToast: btFastL, stillOpen: await curModal.isVisible().catch(() => false) };
              break;
            }
            // rev69：~6s 仍无证据 → 用合成点击(el.click)试探一次，排除可信点击被拦截/失联的可能
            if (i === 15 && !synthetic) {
              synthetic = true;
              // rev141：先 count 再 evaluate——空定位器上的 .evaluate 会等满 30s 默认超时
              //（TR-757 选项1 项的 90s 卡死就是它吃掉的：本页无 .ant-modal-footer）
              if (await curModal.locator('.ant-modal-footer button').last().count().catch(() => 0)) {
                await curModal.locator('.ant-modal-footer button').last()
                  .evaluate((el: any) => (el as any).click()).catch(() => undefined);
              }
            }
            late = await observe();
            if (late.fieldError || late.toastError || late.blockToast || !late.stillOpen) break;
            // rev140：收敛防线——无业务写请求 + 页面错误面较基线扩大 = 前端拦截（不空等）
            const nbNow = netLog.filter((n) => n.tk === netToken && isBizUrl(n.url)).length;
            const feNow = await formErrCount();
            if (i >= 3 && nbNow === 0 && feNow > formErrBase) {
              late = { ...(await observe()), formErrDelta: feNow - formErrBase, __frontendBlocked: true };
              break;
            }
            // rev142：稳定无信号早停——连续 5 轮请求面/错误面都无变化且无任何信号，
            // 再等也不会有新证据（TR-758 选项1 项白跑 66s）。
            const stateKey = nbNow + ':' + feNow;
            if (stateKey === lastState) { stableRounds++; } else { stableRounds = 0; }
            lastState = stateKey;
            if (i >= 5 && stableRounds >= 5) break;
            await confirmClick();
          }
          if (late) { sig.fieldError = late.fieldError; sig.toastError = late.toastError; sig.blockToast = late.blockToast; sig.stillOpen = late.stillOpen; }
          console.log(`SUBMIT_LATE ${JSON.stringify({ item: String(item.name).slice(0, 40), hit: !!(late && (late.fieldError || late.toastError || late.blockToast || !late.stillOpen)), frontend_blocked: !!(late && (late as any).__frontendBlocked), block_toast: (sig.blockToast || '').slice(0, 60), form_err_base: formErrBase, form_err_now: await formErrCount(), elapsed_ms: Date.now() - lateT0 }) }`);
          markStage('evidence');
          const nb = netLog.filter((n) => n.tk === netToken);
          const nbBiz = nb.filter((n) => isBizUrl(n.url));
          const nbUp = nb.length - nbBiz.length;
          const hit4xx = nbBiz.find((n) => n.status >= 400);
          const ok2xx = nbBiz.filter((n) => n.status >= 200 && n.status < 300);
          const err2xx = ok2xx.find((n) =>
            /"success"\s*:\s*false|"code"\s*:\s*(?!200|0\b)\d+|"code"\s*:\s*"(?!200|0)[^"]*"|错误|失败|无效|不能为空|不存在|格式不/.test(n.body));
          // requestId（P1-3 阶段 A）：证据串里带上 requestId，负路径失败时无需回页面手工翻网络面板
          const summarize = (n: any) => `HTTP ${n.status} ${n.url.slice(0, 80)} requestId:${n.requestId || '-'} body:${n.body.slice(0, 120)}`;
          const fieldError = sig.fieldError, toastError = sig.toastError, stillOpen = sig.stillOpen;
          if (fieldError) {
            // rev140：前端拦截判定（无业务写请求 + 错误面扩大）在文案里如实写明判定依据
            const feBlock = !!(late && (late as any).__frontendBlocked);
            submitRec(check, item.name, 'pass', `提交层验证：提交后该字段出现校验提示${feBlock ? '，且未发出业务写请求（前端拦截）' : ''}（${check.rule}）`);
          } else if (toastError) {
            submitRec(check, item.name, 'pass', `提交层验证：提交被拒绝，页面出现错误提示（${check.rule}）`);
          } else if (sig.blockToast) {
            // rev142：warning/普通样式 toast 的文本命中（本 SUT 必填拦截的真实形态，
            // 探针4 截图：「题目 11：请输入标题」橙色 toast）。归因到目标字段才 pass：
            // 文案含字段名或其页面别名（aliases 反查）。
            if (attrHit(check.field, sig.blockToast)) {
              submitRec(check, item.name, 'pass', `提交层验证：提交被前端拦截，提示「${sig.blockToast.slice(0, 60)}」命中目标字段（${check.rule}）`);
            } else {
              submitRec(check, item.name, 'unverified',
                     `提交层：提交被前端拦截（提示「${sig.blockToast.slice(0, 60)}」），但未命中目标字段「${check.field}」——可能被其他必填项拦截（${check.rule}）`);
            }
          } else if (hit4xx) {
            submitRec(check, item.name, 'pass', `提交层验证：服务端拒绝 ${summarize(hit4xx)}（${check.rule}）`);
          } else if (err2xx) {
            submitRec(check, item.name, 'pass', `提交层验证：服务端业务层拒绝 ${summarize(err2xx)}（${check.rule}）`);
          } else if (!stillOpen) {
            const acc = ok2xx.length ? ok2xx[ok2xx.length - 1] : null;
            // requestId（P1-3 阶段 A）：坐实缺陷的分支，requestId 是提单必需项，显式列出
            // 而不只藏在 summarize 里，便于日志检索与工单直接引用
            const accRid = acc ? (acc.requestId || '未在响应头暴露') : '';
            submitRec(check, item.name, 'blocked',
                   acc ? `疑似缺陷坐实：服务端受理非法请求 ${summarize(acc)}（约束「${check.rule}」未生效）；建议 UAT 数据佐证并登记缺陷。定位凭据：requestId=${accRid}`
                       : `期望拒绝但提交成功（弹窗已关闭，疑似缺陷：约束「${check.rule}」未生效）`);
          } else if (late && (late as any).__frontendBlocked) {
            // rev141：前端拦截**归因**。收敛信号（无业务写请求 + 错误面扩大）只证明
            // 「提交被拦」，不证明「被目标字段的约束拦」——TR-757 实测错误面扩大的 3 条
            // 全是 radio 组、无一是目标字段。归因 = 锚定检测 + toast 文本（含别名反查）。
            const targetErr = await itemHasErrorCtl(page, curModal, check.field, itemDef, 300);
            const bt = sig.blockToast || await blockToastText();
            if (targetErr || attrHit(check.field, bt)) {
              submitRec(check, item.name, 'pass', `提交层验证：未发出业务写请求（前端拦截），证据命中目标字段${bt ? `（提示「${bt.slice(0, 60)}」）` : ''}（${check.rule}）`);
            } else {
              // rev142：归因失败时留地面真相（explain 归属 + toast 全文），别让下一轮再猜
              const detail = await anyScope.locator('.ant-form-explain, .ant-form-item-explain-error')
                .allInnerTexts().catch(() => [] as string[]);
              console.log(`SUBMIT_ERR_DETAIL ${JSON.stringify({ item: String(item.name).slice(0, 40), field: check.field, explains: (detail || []).map((t) => String(t).trim()).filter(Boolean).slice(0, 8), block_toast: bt.slice(0, 80) }) }`);
              submitRec(check, item.name, 'unverified',
                     `提交层：提交被前端拦截（页面出现校验提示），但均不属于目标字段「${check.field}」——可能被其他必填项拦截（兜底填充未能清空全部拦截项）（${check.rule}）`);
            }
          } else {
            const remaining = await curModal.locator(ERRORED_ITEM).count().catch(() => -1);
            // rev69 调试：零捕获时输出地面真相——提交按钮状态（是否 disabled）、
            // 整个监听期捕获的全部请求、弹窗内错误项数、页面快照
            const btnInfo = await (async () => {
              try {
                const footer = curModal.locator('.ant-modal-footer button');
                const cnt = await footer.count();
                const out: any[] = [];
                for (let bi = 0; bi < cnt; bi++) {
                  const b = footer.nth(bi);
                  out.push({
                    txt: String(await b.innerText({timeout: 500}).catch(() => '')).slice(0, 12),
                    disabled: await b.isDisabled().catch(() => null),
                    primary: String(await b.getAttribute('class', {timeout: 500}).catch(() => '')).includes('ant-btn-primary'),
                  });
                }
                return out;
              } catch { return 'btn_err'; }
            })();
            console.log('NET_DEBUG ' + JSON.stringify({
              item: item.name, kind: item.kind, remaining, nbBiz: nbBiz.length, nbUp,
              // O1c/rev100：重试点击失败的原因（首轮已点过、重试点不动时存在）
              secondSubmitFailed: (sig as any).__secondSubmitFailed || '',
              allNet: netLog.map((n) => ({u: n.url.slice(0, 90), s: n.status, r: n.requestId || ''})).slice(0, 25),
              btns: btnInfo, stillOpen,
              curModalCount: await curModal.count().catch(() => -1),
              // rev69 终极探针：目标元素究竟活在哪个 frame / 长什么样
              ownerFrame: await (async () => {
                try {
                  const h = await curModal.elementHandle();
                  if (!h) return 'no_handle';
                  const f = await h.ownerFrame();
                  return f ? String(f.url()).slice(0, 80) : 'null_frame';
                } catch (e) { return 'err:' + String(e).slice(0, 60); }
              })(),
              modalHtml: await curModal.evaluate((el: any) => String(el.outerHTML).slice(0, 200)).catch(() => 'html_err'),
              pageUrl: page.url(),
              framesCount: page.frames().length,
              bodyChildren: await page.evaluate(() =>
                Array.from(document.body.children).map((e: any) =>
                  e.tagName + '.' + String(e.className || '').slice(0, 24)).slice(0, 15)).catch(() => 'body_err'),
              reqs: reqLog.filter((r) => r.tk === netToken).slice(0, 12),
              allReqs: reqLog.slice(0, 25),
              errors: errLog.filter((e) => e.tk === netToken).slice(0, 8),
              shadowAudit: await page.evaluate(() => {
                let shadows = 0, modalInShadow = 0;
                const walk = (root: any, depth: number) => {
                  if (depth > 12) return;
                  const all = root.querySelectorAll('*');
                  for (const el of all) {
                    if (el.shadowRoot) {
                      shadows++;
                      if (el.shadowRoot.querySelector('.ant-modal-wrap, .ant-modal')) modalInShadow++;
                      walk(el.shadowRoot, depth + 1);
                    }
                  }
                };
                walk(document, 0);
                return {shadows, modalInShadow, iframes: document.querySelectorAll('iframe').length};
              }).catch(() => 'shadow_err'),
              // rev69：按 frame 审计——应用 UI 在 iframe#systemIframe 内，主 frame 查不到是正常的
              frameAudit: await (async () => {
                const out: any[] = [];
                try {
                  for (const f of page.frames()) {
                    out.push({
                      url: String(f.url()).slice(0, 60),
                      modals: await f.evaluate(() => document.querySelectorAll('.ant-modal-wrap').length).catch(() => -1),
                      confirms: await f.evaluate(() => document.querySelectorAll('.ant-modal-confirm').length).catch(() => -1),
                    });
                  }
                } catch { return 'frame_err'; }
                return out;
              })(),
              modalAudit: await page.evaluate(() => {
                const wraps = Array.from(document.querySelectorAll('.ant-modal-wrap'));
                return wraps.map((w) => ({
                  display: getComputedStyle(w).display,
                  modals: w.querySelectorAll('.ant-modal').length,
                  btns: w.querySelectorAll('.ant-modal-footer button').length,
                  inputs: w.querySelectorAll('.ant-modal input').length,
                }));
              }).catch(() => 'audit_err'),
            }));
            await page.screenshot({path: `submit_debug_${item.kind}_${Date.now()}.png`, fullPage: false}).catch(() => undefined);
            submitRec(check, item.name, 'unverified',
                   nbBiz.length
                     ? `提交层：页面无拒绝证据，捕获到 ${nbBiz.length} 条业务写请求但均无拒绝语义（${check.rule}）`
                     : (nbUp > 0
                          ? `提交层：仅捕获到 ${nbUp} 条上传类请求，业务提交请求未出现（可能上传未完成或被前端拦截）（${check.rule}）`
                          : (remaining > 0
                               ? `提交层：无网络请求发出，仍被 ${remaining} 个必填项拦截（含目标字段为空的预期项）（${check.rule}）`
                               : `提交层：未观测到拒绝证据，也未捕获到提交请求（${check.rule}）`)));
          }
          // rev69：清理目标字段标记，避免影响后续用例项
          if (underItem) await underItem.evaluate((el: any) => { delete (el as any).dataset.rev69ut; }).catch(() => undefined);
          // 4) 提交成功会关掉表单弹窗 → 重开以继续后续检查
          if (!stillOpen) curModal = await openForm();
        };
        let wdTimer: any = null;
        const watchdog = new Promise<never>((_, reject) => {
          wdTimer = setTimeout(
            () => reject(new Error(`__submit_item_timeout__ ${submitStage}`)),
            SUBMIT_ITEM_MS);
        });
        try {
          await Promise.race([runSubmitItem(), watchdog]);
        } catch (error) {
          const msg = String(error);
          if (msg.includes('__submit_item_timeout__')) {
            const stuck = msg.split('__submit_item_timeout__')[1].trim();
            console.log(`SUBMIT_TIMEOUT ${JSON.stringify({ item: String(item.name).slice(0, 40), stage: stuck, budget_ms: SUBMIT_ITEM_MS, elapsed_ms: Date.now() - submitT0 })}`);
            submitRec(check, item.name, 'unverified',
                   `提交层：单项超时（卡在 ${stuck} 阶段 > ${Math.max(1, Math.round(SUBMIT_ITEM_MS / 1000))}s）——该项未验证，需简化该步骤或改走 API 层`);
            submitAborted = `提交层：前一项（${String(item.name).slice(0, 30)}）在「${stuck}」阶段超时，页面状态不可信，本项未验证`;
          } else {
            submitRec(check, item.name, 'blocked', `提交层校验执行异常：${msg.slice(0, 120)}`);
            if (msg.includes('has been closed') || msg.includes('Target closed')) {
              submitAborted = '提交层：页面已关闭，后续项未验证';
            }
          }
        } finally {
          if (wdTimer) clearTimeout(wdTimer);
        }
      }
      page.off('response', onNetResp);            // rev69：提交层结束，卸载网络监听
      console.log('SUBMIT_PHASE ' + JSON.stringify({
        queued: submitQueue.length,
        probe: submitQueue.filter((i: any) => i.kind === 'probe').length,
        required_empty: submitQueue.filter((i: any) => i.kind === 'required_empty').length,
      }));
    }

    // ===== 规则级负路径场景（P1-3 阶段 B）=====
    // 与上面的字段级 submitQueue 的**根本差别**（DEFECT-001 的形态）：
    //   违规主体不是"某个字段的值非法"，而是"字段值与**外部状态**的冲突"
    //   （编辑时追加一个已被其他活动占用的商品）。因此需要三段：
    //     setup（多步交互构造前置条件）→ submit（真实提交）→ assert（**服务端**是否拒绝）。
    // 判定只看服务端响应，不看页面提示：DEFECT-001 的页面表现是"提交成功"，
    // 服务端也返回 resultCode=0 —— 只有拿到响应体才能坐实规则未生效。
    // 只读阶段不产生写请求；本阶段是**唯一**会真实写库的段，因此写隔离必须收窄到
    // 场景自己声明的那个接口（见 negRules），并把"装不上隔离"处理成 fail-closed。
    const negativeStat = {configured: 0, executed: 0, pass: 0, blocked: 0, unverified: 0};
    //: 「列表可见」阶段计数（2026-10-16）：与 submit_phase / negative_scenario_phase **分开计数**——
    //: 它验证的是"写入后记录是否可检索"，既不是字段级校验，也不是服务端受理判定。
    const listStat = {configured: 0, executed: 0, pass: 0, unverified: 0};
    //: 「导出」独立阶段计数（rev148）：与 list_phase 分开计数——验证的是
    //: 「菜单 → 弹窗 → 时间段 → 导出中心 → 下载 → 列名」整链，不是记录可见性。
    const exportStat = {configured: 0, executed: 0, pass: 0, unverified: 0};
    // rev170：筛选区独立阶段（下拉筛选结果集 + 重置还原）分开计数
    const filterStat = {configured: 0, executed: 0, pass: 0, unverified: 0};
    // rev173：状态×操作矩阵独立阶段（行操作集断言 + 更多菜单 + 确认框验证）分开计数
    const mxStat = {configured: 0, executed: 0, pass: 0, unverified: 0};
    async function runNegativeScenarios(): Promise<void> {
      const scenarios: any[] = Array.isArray(FORM_PLAN.negative_scenarios)
        ? FORM_PLAN.negative_scenarios : [];
      negativeStat.configured = scenarios.length;
      // 配置期被拒的场景必须**运行期可见**：否则"配了但从未执行"只能靠人比对配置与日志
      //（与 [plan-skip] 同一口径）。
      for (const rej of (FORM_PLAN.negative_scenario_rejects || []).slice(0, 20)) {
        console.log('[negative-skip] ' + JSON.stringify(rej));
      }
      if (!scenarios.length) return;
      const ownerOf = (sc: any) => ({
        case_id: String(sc.case_id || ''), rule_id: String(sc.rule_id || ''),
        source: 'negative_scenario',
      });
      const labelOf = (sc: any) => `[${sc.scenario_id}] ${sc.rule_ref || '规则级负路径'}`;
      const record = (sc: any, outcome: CheckOutcome, observed: string) => {
        negativeStat.executed += 1;
        negativeStat[outcome] += 1;
        recAll(ownerOf(sc), labelOf(sc), outcome, observed);
      };
      const escRe = (t: string) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      //: 按文本候选逐个尝试点击（菜单项 / 按钮 / 链接 / 任意可见文本）。
      //: 与手写脚本 demo2_script.ts 的 clickByText 同形——那份脚本已实测跑通该流程，
      //: 这里把它固化进执行器，使同类场景不必再手写。
      const clickByLabels = async (labels: string[]): Promise<boolean> => {
        for (const text of labels) {
          if (!text) continue;
          const rx = new RegExp(escRe(text));
          const candidates = [
            anyScope.locator(`li[role="menuitem"]:has-text("${text}")`).first(),
            anyScope.getByRole('button', {name: rx}).first(),
            anyScope.getByRole('link', {name: rx}).first(),
            anyScope.getByText(text, {exact: false}).last(),
          ];
          for (const candidate of candidates) {
            if (!(await candidate.count().catch(() => 0))) continue;
            try {
              await candidate.waitFor({state: 'visible', timeout: 2500});
              // rev144：radio/checkbox 幂等守卫——点「已选中」的选项会把可见状态**切换掉**
              //（TR-761 实锤：场景 setup 点「外卖」，默认视觉选中 ⇒ 点完反而不选 ⇒
              // 保存被「请选择业务类型」拦住，负向场景从 pass 翻车成 unverified）。
              // 命中的元素自身是 input、或其祖先 wrapper 内的 input 已选中时跳过点击。
              const rcSelf = candidate.locator('xpath=self::input[@type="radio" or @type="checkbox"]').first();
              const rcWrap = candidate.locator('xpath=ancestor::*[contains(@class,"ant-radio-wrapper") or contains(@class,"ant-checkbox-wrapper")][1]//input[@type="radio" or @type="checkbox"]').first();
              const rcSelfN = await rcSelf.count().catch(() => 0);
              const rcWrapN = await rcWrap.count().catch(() => 0);
              if (rcSelfN && await rcSelf.isChecked().catch(() => false)) return true;
              if (!rcSelfN && rcWrapN && await rcWrap.isChecked().catch(() => false)) return true;
              await candidate.click({timeout: 5000});
              // rev144：radio/checkbox 点击后**验证**——label 文本点击在 antd 结构上未必
              // 触发 onChange（TR-761：外卖点击未生效 ⇒ 保存被「请选择业务类型」拦住）；
              // 未选中则对 input 合成 click 兜底。
              if (rcSelfN || rcWrapN) {
                const rcV = rcSelfN ? rcSelf : rcWrap;
                if (!(await rcV.isChecked().catch(() => false))) {
                  await rcV.evaluate((el: any) => (el as any).click()).catch(() => undefined);
                }
              }
              return true;
            } catch { /* 该候选不可点，试下一个 */ }
          }
        }
        return false;
      };
      const closeAnyForm = async () => {
        // 字段级阶段打开的是"新建"表单；规则级场景通常从**列表页**自己的入口进入
        //（如点「编辑」）。先尽力关掉当前容器，关不掉也**不阻断**——由 setup 步骤
        // 自己去点目标入口（配置负责，平台不替用户猜）。
        try {
          const cancel = anyScope.locator('.ant-modal-footer button')
            .filter({hasText: /取\s*消|关\s*闭|Cancel/i}).first();
          if ((await cancel.count().catch(() => 0)) && await cancel.isVisible().catch(() => false)) {
            await cancel.click({timeout: 3000}).catch(() => undefined);
            await page.waitForTimeout(800);
          }
        } catch { /* 忽略 */ }
        try { await page.keyboard.press('Escape'); } catch { /* 忽略 */ }
        await page.waitForTimeout(400);
      };
      const runSetup = async (sc: any): Promise<string> => {
        const steps: any[] = Array.isArray(sc.setup) ? sc.setup : [];
        for (let i = 0; i < steps.length; i += 1) {
          const step = steps[i] || {};
          const action = String(step.action || '');
          const where = `${sc.scenario_id}#setup[${i}] ${action}`;
          if (action === 'wait') {
            await page.waitForTimeout(Math.max(0, Number(step.ms) || 1000));
            continue;
          }
          if (action === 'click' || action === 'append') {
            const labels = Array.isArray(step.labels) ? step.labels.map((s: any) => String(s)) : [];
            if (!(await clickByLabels(labels))) {
              return `${where}：未找到或无法点击 ${JSON.stringify(labels)}`;
            }
            await page.waitForTimeout(1000);
            continue;
          }
          if (action === 'select_row') {
            // 第 0 个可见 checkbox 通常是"全选"，row_index 由配置给出——
            // 平台**不**替用户加减一（两套约定必然出错），只做越界校验并如实报错。
            const boxes = anyScope.locator('input[type=checkbox]:visible');
            const total = await boxes.count().catch(() => 0);
            const idx = Number(step.row_index);
            if (!total) return `${where}：当前作用域没有可见的 checkbox`;
            if (!(idx >= 0 && idx < total)) {
              return `${where}：row_index=${idx} 越界（可见 checkbox 共 ${total} 个）`;
            }
            // rev98c（实测 TR-329）：antd checkbox input 被 inner 方框覆盖，
            // 直接点 input 过不了 Playwright 命中检测 ⇒ 超时静默、行未选中。
            // 改点其 .ant-checkbox 容器，isChecked 自证，未选中则合成 click 兜底。
            const boxInput = boxes.nth(idx);
            if (!(await boxInput.isChecked().catch(() => false))) {
              const wrap = boxInput.locator('xpath=..');
              const wrapCls = String(await wrap.getAttribute('class').catch(() => ''));
              if (wrapCls.includes('ant-checkbox')) {
                await wrap.click({timeout: 8000}).catch(() => undefined);
              } else {
                await boxInput.click({timeout: 8000}).catch(() => undefined);
              }
              if (!(await boxInput.isChecked().catch(() => false))) {
                await boxInput.evaluate((el: any) => el.click()).catch(() => undefined);
              }
            }
            if (!(await boxInput.isChecked().catch(() => false))) {
              return `${where}：checkbox 点击后仍未选中（antd 结构可能改版）`;
            }
            await page.waitForTimeout(1000);
            continue;
          }
          if (action === 'fill') {
            const field = String(step.field || '');
            if (!field) return `${where}：fill 缺少 field`;
            // rev98：fill 与字段级阶段同权使用定位器表的 selector_hint——
            // 实测（CS email 弹窗 #emails/#remark）弹窗控件不在 .ant-form-item 结构里，
            // 空 def 走 has-text 兜底永远落空 ⇒ 场景在 setup 期就 unverified。
            const setupDefs = ((FORM_PLAN.locators || {}).fields) || {};
            const input = await fieldControl(anyScope, field, setupDefs[field] || {});
            if (!(await input.count().catch(() => 0))) return `${where}：未定位到字段「${field}」`;
            // rev124（A2）：唯一化占位符在**运行期**展开，值里没有占位符则逐字不变。
            const effective = runTagValue(field, step.value);
            await input.fill(effective, {timeout: OP_T});
            await input.blur({timeout: OP_T}).catch(() => undefined);
            // rev126：唯一化"填进去的到底是不是那个值"必须自证。
            // 控件若有 maxLength，长编号会被**静默截断**——截断掉的可能正是区分两次
            // 运行的尾段，那就等于没唯一化，而日志里仍写着"已唯一化"（假绿）。
            if (runTagUsed.length && runTagUsed[runTagUsed.length - 1].field === field) {
              // rev134：回读统一走 controlValue()。旧实现直连 `input.inputValue`（无兜底），
              // 对 contenteditable（富文本）字段会读到长度错乱的字符串 ⇒ 唯一化自证
              // 会误报 truncated=true（或反过来把截断当正常），而控件其实是好的。
              const actual = String(await controlValue(input));
              console.log('RUN_TAG_APPLIED ' + JSON.stringify({
                field, intended: effective, actual, truncated: actual !== effective,
              }));
            }
            continue;
          }
          if (action === 'click_selector') {
            // rev103：按原始 CSS 点击（无文本的图标按钮等，如列表工具条 filter 图标）。
            // selector 支持 Playwright 扩展（:visible / >> nth=-1 等）。
            const selCs = String(step.selector || '').trim();
            if (!selCs) return `${where}：click_selector 缺少 selector`;
            const elCs = anyScope.locator(selCs).first();
            if (!(await elCs.count())) return `${where}：未定位到 ${selCs}`;
            await elCs.click({timeout: 8000});
            await page.waitForTimeout(Math.max(300, Number(step.ms) || 800));
            continue;
          }
          if (action === 'type') {
            // rev115：键盘逐字输入原语（autocomplete / 受控组件专用）。
            // 与 fill 的唯一区别：**不 blur**。fill = value+blur，实测（TR-468/469）
            // 会把 Google autocomplete 的 .pac-container 候选面板随失焦收起，
            // 且重新聚焦（无新 input 事件）不会重开 ⇒ 候选永远点不到。
            // type = click 定位 → page.keyboard.type 逐字输入（每键触发 input 事件，
            // 候选面板保持展开），随后可 click_selector 点 .pac-item 候选。
            const selTy = String(step.selector || '').trim();
            if (!selTy) return `${where}：type 缺少 selector`;
            const elTy = anyScope.locator(selTy).first();
            if (!(await elTy.count())) return `${where}：未定位到 ${selTy}`;
            // rev124（A2）：先一次性展开占位符，避免下面两个分支各自调用导致重复记日志。
            const typed = runTagValue(String(step.field || ''), step.value);
            if (String(step.clear || '') ) {
              // 三击全选已有文本（如弹窗搜索框预填了表单地址），随后键入即整体替换；
              // 不做清空会导致 keyboard.type 追加、查询串翻倍而无候选（TR-471 实测）。
              await elTy.click({clickCount: 3, timeout: 8000});
            } else {
              await elTy.click({timeout: 8000});
            }
            if (String(step.mode || '') === 'set') {
              // mode=set：程序化赋值（Playwright fill 语义，**不 blur**）。
              // A/B 实测（TR-473 探针，2026-10-17）：Select Address 弹窗的 Google
              // autocomplete 对 keyboard.type 的 CJK 逐键输入不吐候选（pac-container
              // 0 项），对程序化 fill+input 事件则正常出候选（1 项）——且不 blur
              // 面板保持展开；平台 fill 因自带 blur 永远点不到 .pac-item。
              await elTy.fill(typed, {timeout: 8000});
            } else {
              await page.keyboard.type(typed, {delay: 80});
            }
            await page.waitForTimeout(Math.max(300, Number(step.ms) || 800));
            continue;
          }
          if (action === 'clear_select') {
            // rev113：antd Select「确保为空」原语（幂等）。
            // 实测（2026-10-16）：商品新增页的 Brand 在**干净载入**时本就为空、连清除
            // 图标都不渲染（TR-390：.ant-select-clear 计数 0，提交体不含 brandId，
            // 服务端受理）；但同一页面也出现过「默认预选品牌列表第一项」的情形
            // （TR-381/382：未选品牌却提交 brandId=1451454576981397597，全被服务端
            // 拒 10007）。两种状态都会出现 ⇒ 硬清（click_selector 点不到就报错，
            // TR-387）与硬选后清（TR-389：clear 图标只在有值时才渲染，且默认
            // display/opacity 隐藏，普通 click 等不到可操作状态而超时）都会失败。
            // 本原语的口径：**有值就清、没值就放过**，绝不因此让场景 unverified。
            // 清除图标在 antd 中隐藏到 hover 才显现 —— 沿用本仓既有样板
            // datePickThenClear 的「先 hover 父节点、再点图标」写法。
            const selClr = String(step.selector || '').trim();
            if (!selClr) return `${where}：clear_select 缺少 selector（下拉根 CSS）`;
            const rootClr = anyScope.locator(selClr).first();
            if (!(await rootClr.count())) return `${where}：未定位到下拉根 ${selClr}`;
            const iconClr = rootClr.locator('.ant-select-clear, .ant-select-selection__clear').first();
            if (!(await iconClr.count())) {
              // 本来就为空：无清除图标可点，直接视为已达成目标
              await page.waitForTimeout(Math.max(200, Number(step.ms) || 300));
              continue;
            }
            await rootClr.hover({timeout: 5000}).catch(() => undefined);
            await page.waitForTimeout(200);
            const hitClr = await iconClr.click({timeout: 3000})
              .then(() => true).catch(() => false);
            if (!hitClr) {
              // 图标存在但不可操作（被隐藏/被覆盖）：退化为程序化点击，
              // 仍走真实 DOM click 事件，antd 的 onClear 照常触发。
              await iconClr.click({force: true, timeout: 3000}).catch(() => undefined);
            }
            await page.waitForTimeout(Math.max(300, Number(step.ms) || 500));
            continue;
          }
          if (action === 'upload') {
            // rev114：把内联夹具写进 file input —— **唯一**会主动产生 multipart 请求的 setup 动作。
            // 实测（TR-398/399/401，美心 YL 店铺 Store Image）：
            //   1) 入口是表单里的按钮（如「Upload Image」），点了才渲染 .ant-modal-wrap
            //      及其中 input[type=file]（accept=.jpeg,.jpg,.png,.gif）；
            //   2) 选完文件**不会自动确认**，还需点弹窗的 OK/Confirm；
            //   3) 上传请求是 multipart POST 打到静态域（static.uat.eatizen.com.hk/）
            //      ⇒ 场景必须声明 ``allow_upload`` 放行规则，否则写隔离会 abort。
            // 本动作在任何一步失败都**照实报错**（记 unverified），不静默通过——
            // 否则"必填附件没填上"会被下游误读成"被测系统拒绝了提交"。
            const fix = inlineFixture(step.fixture);
            if (!fix) {
              console.log('UPLOAD_STEP ' + JSON.stringify(
                {field: String(step.field || ''), ok: false, why: 'fixture.b64 缺失'}));
              return `${where}：upload 缺少 fixture.b64`;
            }
            const openLabels = Array.isArray(step.open_labels)
              ? step.open_labels.map((s: any) => String(s)) : [];
            if (openLabels.length) {
              if (!(await clickByLabels(openLabels))) {
                return `${where}：未找到上传入口 ${JSON.stringify(openLabels)}`;
              }
              await page.waitForTimeout(Math.max(400, Number(step.open_ms) || 1500));
            }
            const selUp = String(step.selector || 'input[type=file]').trim();
            const fileInput = anyScope.locator(selUp).first();
            let upFound = 0;
            try { upFound = await fileInput.count(); } catch { upFound = 0; }
            if (!upFound) {
              console.log('UPLOAD_STEP ' + JSON.stringify(
                {field: String(step.field || ''), ok: false, why: 'file input 未定位',
                 open_labels: openLabels, selector: selUp}));
              return `${where}：未定位到文件输入 ${selUp}`;
            }
            await fileInput.setInputFiles(fix,
              {timeout: Math.max(5000, Number(step.upload_ms) || 15000)});
            // 等图片真正传完（实测 TR-399：setInputFiles 后立刻点确认会拿到空值）
            await page.waitForTimeout(Math.max(800, Number(step.after_ms) || 2500));
            const confirmLabels = Array.isArray(step.confirm_labels)
              ? step.confirm_labels.map((s: any) => String(s)) : [];
            let upConfirmed = '';
            for (const cl of confirmLabels) {
              const btn = anyScope.locator(
                `.ant-modal-wrap:visible button:has-text("${cl}"),`
                + ` .ant-modal-root:visible button:has-text("${cl}")`,
              ).first();
              if (!(await btn.count().catch(() => 0))) continue;
              await btn.click({timeout: 5000}).catch(() => undefined);
              upConfirmed = cl;
              break;
            }
            await page.waitForTimeout(Math.max(300, Number(step.ms) || 1500));
            // 成功也要留痕：否则"配了 upload 但没执行/执行了没生效"在日志里看不出来
            // （本项目反复踩的坑：配置层与执行层的差异只能靠现场说话）。
            console.log('UPLOAD_STEP ' + JSON.stringify(
              {field: String(step.field || ''), ok: true, open_labels: openLabels,
               selector: selUp, file_inputs: upFound, set_input: true,
               confirm_clicked: upConfirmed || null,
               fixture: {name: fix.name, mime: fix.mimeType, bytes: fix.buffer.length}}));
            continue;
          }
          if (action === 'select_option') {
            // rev103：antd Select 交互原语（filter 面板等 list 功能点必需）。
            // selector=下拉根 CSS；labels=选项文本候选。下拉渲染在 portal，
            // 必须用 anyScope 同一文档查询（rev92 的既有教训）。
            const sel = String(step.selector || '').trim();
            if (!sel) return `${where}：select_option 缺少 selector（下拉根 CSS）`;
            const root = anyScope.locator(sel).first();
            if (!(await root.count())) return `${where}：未定位到下拉根 ${sel}`;
            await root.click({timeout: 8000}).catch(() => undefined);
            await page.waitForTimeout(600);
            const optLabels = Array.isArray(step.labels)
              ? step.labels.map((s: any) => String(s)) : [];
            let picked = false;
            for (const optText of optLabels) {
              const opt = anyScope.locator(
                `.ant-select-dropdown:visible .ant-select-item:has-text("${optText}"),`
                + ` .ant-select-dropdown:visible li:has-text("${optText}")`,
              ).first();
              if (!(await opt.count())) continue;
              await opt.click({timeout: 5000}).catch(() => undefined);
              picked = true;
              break;
            }
            if (!picked) return `${where}：下拉选项 ${JSON.stringify(optLabels)} 未命中`;
            await page.waitForTimeout(400);
            continue;
          }
          return `${where}：未知动作（解析器本应在计划期拒绝该场景）`;
        }
        return '';
      };
      // ---- 写隔离：规则只来自场景声明的目标接口 ----
      // 设计文档 §四要求"白名单收窄到单接口"：规则级场景需要真实提交，
      // 但绝不允许它顺带产生其它未知写副作用。
      // 注意语义差异（实测 DEFECT-001）：`api_path_hint` 是**响应匹配用的 URL 子串**，
      // 而放行规则按 `host 后缀 + path 前缀` 匹配——真实请求打在**网关主机**上、
      // 路径还带网关前缀，子串当不了前缀。因此以 `allow` 为准，缺省才退回
      // "同源直连、路径等于 hint"的简单情形。
      const negRules: Array<{host?: string; path_prefix?: string; methods?: string[]}> = [];
      for (const sc of scenarios) {
        // rev99：写隔离放行规则两种极性同构——accept 场景同样收窄到声明的单接口。
        const expectX = ((sc.expect_accept || {}).api_path_hint)
          ? sc.expect_accept : (sc.expect_reject || {});
        const allow = expectX.allow || {};
        const prefix = String(allow.path_prefix || expectX.api_path_hint || '');
        if (!prefix) continue;
        const allowMethods = Array.isArray(allow.methods) ? allow.methods : [];
        negRules.push({
          host: String(allow.host_suffix || pageHost),
          path_prefix: prefix,
          methods: allowMethods.length
            ? allowMethods.map((m: any) => String(m).toUpperCase())
            : ['POST', 'PUT', 'PATCH'],
        });
        // rev115：场景显式声明的附加放行（如 Google Maps JS 内部 RPC——被隔离 abort
        // 会导致地图服务初始化失败、autocomplete 不出候选，TR-476 实测）。
        for (const rX of (Array.isArray(expectX.allow_extra) ? expectX.allow_extra : [])) {
          if (!rX || typeof rX !== 'object') continue;
          negRules.push({
            host: String(rX.host || rX.host_suffix || ''),
            path_prefix: String(rX.path_prefix || '/'),
            methods: (Array.isArray(rX.methods) ? rX.methods : ['POST'])
              .map((m: any) => String(m).toUpperCase()),
          });
        }
      }
      const negBlocked: Array<{method: string; url: string; kind: string}> = [];
      // 上传（multipart）放行规则：同样**只来自场景的显式声明**（``allow_upload`` 数组），
      // 无声明 ⇒ 上传照旧 abort。由场景声明而非全局配置的理由与写放行一致：
      // 隔离的口径是"收窄到本次场景声明的目标"，上传放行同样不该跨场景泄漏。
      const negUploadRules: Array<{host?: string; path_prefix?: string; methods?: string[]}> = [];
      for (const sc of scenarios) {
        for (const pol of [sc.expect_accept || {}, sc.expect_reject || {}]) {
          const listU = Array.isArray(pol.allow_upload) ? pol.allow_upload : [];
          for (const rU of listU) {
            if (!rU || typeof rU !== 'object') continue;
            negUploadRules.push({
              host: String(rU.host || rU.host_suffix || ''),
              path_prefix: String(rU.path_prefix || '/'),
              methods: (Array.isArray(rU.methods) ? rU.methods : [])
                .map((m: any) => String(m).toUpperCase()),
            });
          }
        }
      }
      // 上报生效的上传放行规则：配置期"声明了但没到执行期"是历史上反复踩的坑，
      // 让运行日志自带这条事实（空数组同样输出，便于区分"没声明"与"没生效"）。
      console.log('UPLOAD_ALLOW_RULES ' + JSON.stringify(negUploadRules));
      const onNegRoute = async (route: any) => {
        try {
          const request = route.request();
          const method = String(request.method()).toUpperCase();
          const url = String(request.url());
          const contentType = String((request.headers() || {})['content-type'] || '');
          if (isolationDecision(method, url, negRules, [], contentType,
                                negUploadRules) === 'continue') {
            await route.continue();
            return;
          }
          const kind = blockedKind(url, contentType, pageHost);
          negBlocked.push({method, url: url.slice(0, 200), kind});
          console.log('ISOLATION_BLOCKED ' + JSON.stringify(
            {method, url: url.slice(0, 200), kind, phase: 'negative_scenario'}));
          await route.abort();
        } catch {
          await route.abort();
        }
      };
      let routeInstalled = false;
      try {
        if (typeof context.route === 'function') {
          await context.route('**/*', onNegRoute);
          routeInstalled = true;
        }
      } catch (error) {
        console.log('[negative-skip] ' + JSON.stringify(
          {scenario_id: '', reason: '写隔离安装失败', detail: String(error).slice(0, 160)}));
      }
      try {
        if (!routeInstalled) {
          // fail-closed：装不上隔离就**不提交**。规则级场景是要真实写库的，
          // "无隔离照样提交"会把一次断言失败升级成 UAT 数据污染。
          for (const sc of scenarios) {
            record(sc, 'unverified',
              `规则级负路径：写隔离策略未安装成功，为避免未知写副作用拒绝执行（${labelOf(sc)}）`);
          }
          return;
        }
        for (const sc of scenarios) {
          const label = labelOf(sc);
          const expect = sc.expect_reject || {};
          // rev99 正路径极性：expect_accept 与 expect_reject 互斥（计划期强制），
          // 这里按 accept 优先取目标接口标识——否则 accept 场景的 hint 为空串会
          // 让 includes('') 匹配到**每一个**非 GET 响应，证据被噪声淹没。
          const acceptCfg = (sc.expect_accept && sc.expect_accept.api_path_hint)
            ? sc.expect_accept : null;
          const hint = String((acceptCfg || expect).api_path_hint || '');
          const codeNot = String(expect.result_code_not == null ? '0' : expect.result_code_not);
          const msgs: string[] = Array.isArray(expect.message_contains)
            ? expect.message_contains.map((s: any) => String(s)) : [];
          if (typeof (page as any).isClosed === 'function' && (page as any).isClosed()) {
            record(sc, 'unverified', `规则级负路径：页面已关闭，未能执行（${label}）`);
            continue;
          }
          try {
            // 进入场景前先关掉字段级阶段打开的那个"新建"表单：
            // setup 通常从**列表页**自己的入口进（如点「编辑」），表单会遮住列表页。
            // 但 ``target_entry=create`` 的场景**就是**要在这个已打开的新建表单里操作，
            // 关掉反而错——平台不替用户决定，由配置语义说了算。
            if (String(sc.target_entry || 'custom') !== 'create') await closeAnyForm();
            // rev102：场景间**行选中状态隔离**——清掉上一轮残留的列表勾选。
            // 实测（TR-337）：场景共享页面状态，"未选中应阻止"类场景继承了上一轮的
            // 勾选，请求真实发出且被受理 ⇒ 判定层如实判「受理了应被拒绝的请求」——
            // 这是对 SUT 的**假缺陷指控**。每个场景自声明 setup，残留选择是执行假象。
            // 注意：每次点击会触发 React 重渲染、后续复选框 detach——必须每轮**重新查询**
            // 且给每个小操作带上显式短超时（默认 30s 超时 × N 个 = 挂死整轮，实测 TR-339）。
            // 注意 2（实测探针 2026-10-14）：`:checked` 会同时命中**隐藏固定列克隆表**的
            // input（antd fixed-left clone 排在 DOM 前面）——必须加 `:visible`，否则点到
            // 隐藏克隆、可见勾选原封不动，清选形同虚设（TR-340 假缺陷指控的根源）。
            try {
              for (let guard = 0; guard < 30; guard++) {
                const checkedNow = anyScope.locator(
                  '.ant-table-tbody input[type=checkbox]:checked:visible');
                if (!(await checkedNow.count().catch(() => 0))) break;
                const cb = checkedNow.first();
                const wrap = cb.locator('xpath=..');
                const wrapCls = String(await wrap.getAttribute('class', {timeout: 800}).catch(() => ''));
                await (wrapCls.includes('ant-checkbox') ? wrap : cb)
                  .click({timeout: 3000}).catch(() => undefined);
                await page.waitForTimeout(300);
              }
              await page.waitForTimeout(400);
            } catch { /* 清选失败不阻断——由场景 setup/判定自然暴露 */ }
            const setupErr = await runSetup(sc);
            if (setupErr) {
              record(sc, 'unverified',
                `规则级负路径：前置条件未构造成功 —— ${setupErr}（${label}）`);
              continue;
            }
            // 提交窗口取证：request 级（区分"请求没发出"）+ response 级（服务端结论）
            const hits: Array<{url: string; status: number; requestId: string; body: string; method: string}> = [];
            const reqHits: Array<{url: string; method: string; body: string}> = [];
            // 窗口内**全部**非 GET 请求（不限目标接口）：用于区分"没发出"与"发到别处"。
            const winWrites: Array<{method: string; url: string}> = [];
            const blockedBefore = negBlocked.length;
            const onResp = (resp: any) => {
              try {
                const rurl = String(resp.url());
                const method = String(resp.request().method()).toUpperCase();
                if (!rurl.includes(hint) || method === 'GET') return;
                const headers = resp.headers() || {};
                const requestId = String(headers['requestid'] || headers['x-request-id'] || '');
                hits.push({url: rurl.slice(0, 160), status: resp.status(), requestId,
                           body: '', method});
                const slot = hits[hits.length - 1];
                resp.text().then((t: string) => { slot.body = String(t || '').slice(0, 600); })
                  .catch(() => undefined);
              } catch { /* 取证异常不得干扰判定 */ }
            };
            const onReq = (req: any) => {
              try {
                const rurl = String(req.url());
                const method = String(req.method()).toUpperCase();
                if (method === 'GET') return;
                if (winWrites.length < 40) winWrites.push({method, url: rurl.slice(0, 170)});
                if (!rurl.includes(hint)) return;
                reqHits.push({url: rurl.slice(0, 160), method,
                              body: String(req.postData() || '').slice(0, 300)});
              } catch { /* 同上 */ }
            };
            // ★ 提交窗口内的**现场轮询**（2026-10-16，实测 TR-405）：前端校验提示只存续
            //   数秒，旧实现在窗口**末尾单点采样**，于是 t≈0.5s 弹出的
            //   「Business hours group …: please select at least one day」早已消失，
            //   现场只剩写隔离自身触发的 "Network Error"，把"必填项未满足"误读成
            //   "网络异常"——排查方向再次被带偏（与用户长期口径一致：toast 类提示
            //   必须人工/程序在存续期内取到，否则等于没有证据）。
            //   改为窗口内每秒轮询并累计去重，同时记录首次出现时刻，供归因使用。
            const seenToasts: Array<{t: number; txt: string}> = [];
            const seenErrors: Array<{t: number; field: string; error: string}> = [];
            let lastScene: {url: string; form_errors: any[]; toasts: string[]} =
              {url: '', form_errors: [], toasts: []};
            let tSubmit = Date.now();
            const pollScene = async () => {
              const s0 = await collectSubmitScene();
              const dt = Date.now() - tSubmit;
              for (const tt of s0.toasts) {
                if (!seenToasts.some((x) => x.txt === tt)) seenToasts.push({t: dt, txt: tt});
              }
              for (const fe of s0.form_errors) {
                if (!seenErrors.some((x) => x.field === fe.field && x.error === fe.error)) {
                  seenErrors.push({t: dt, field: fe.field, error: fe.error});
                }
              }
              lastScene = {url: s0.url, form_errors: s0.form_errors, toasts: s0.toasts};
              return s0;
            };
            // rev129（小杨生煎问卷实测 TR-742）：监听器必须挂在**应用所在页面**。
            // 门户卡片式应用（数盈营销云/交易云）点卡片后**新开标签页**，anyScope 是
            // 那个新 Page；挂到主 `page`（console 门户）会**一个请求都收不到**，
            // 服务端已受理（库里已落数据）却判 unverified —— 判定层假阴性。
            // anyScope 是 Page（有 .on）→ 挂它；是 FrameLocator（iframe 应用）→ 回退 page。
            const listenPage = (typeof (anyScope as any).on === 'function') ? anyScope : page;
            listenPage.on('response', onResp);
            listenPage.on('request', onReq);
            try {
              const labels = (Array.isArray(sc.submit_label) && sc.submit_label.length)
                ? sc.submit_label.map((s: any) => String(s))
                : ['提 交', '提交', '保 存', '确 定'];
              if (!(await clickByLabels(labels))) {
                record(sc, 'unverified',
                  `规则级负路径：未找到提交按钮 ${JSON.stringify(labels)}（${label}）`);
                continue;
              }
              // 提交窗口：给服务端往返 + 响应体落地留时间（手写脚本实测 8s 足够）。
              // 每秒轮询一次现场，累计提示/校验错（提示只存续数秒，单点必漏）。
              tSubmit = Date.now();
              await pollScene();
              for (let k = 0; k < 8; k += 1) {
                await page.waitForTimeout(1000);
                await pollScene();
              }
            } finally {
              listenPage.off('response', onResp);
              listenPage.off('request', onReq);
            }
            const hit = hits.length ? hits[hits.length - 1] : null;
            if (!hit) {
              const winBlocked = negBlocked.slice(blockedBefore);
              // ★ 归因修正（2026-10-16，实测 TR-402）：只有**命中本场景目标接口**的拦截
              //   才算"本场景的写请求被写隔离阻断"。旧实现直接拿窗口内拦截总条数，
              //   于是控制台自身的写请求（实测：`PUT .../message/inMail/status`，与提交无关）
              //   被算进来，把真实原因——**目标请求压根没发出**——掩盖成"被写隔离阻断"，
              //   排查方向被整体带偏（一度以为还要再放宽策略）。
              // ★ 归因修正（2026-10-16，实测 TR-402 + 契约用例 NEG-BLOCKED 双约束）：
              //   ① 命中场景声明的目标接口（hint）⇒ 明确是"本场景的写请求被阻断"；
              //   ② 否则，只有**落在声明接口同一资源集合**里的拦截才算本场景提交触发的。
              //      真实成因：页面提交到了同集合的兄弟接口，而该接口没被声明
              //      （契约用例 `.../purchaseLimit/not-declared` vs 实际提交的 `.../delete`）。
              //   ③ 落在**别的集合**的写（实测 `.../message/inMail/status`，应用自身的
              //      后台轮询，与提交无关）是噪声：只作为附注，绝不写进结论。
              //   旧实现直接拿"窗口内拦截总条数"，于是噪声把真实原因——**目标请求压根
              //   没发出**——掩盖成"被写隔离阻断"，排查方向被整体带偏。
              const ownAllow = ((sc.expect_accept && sc.expect_accept.allow)
                || (sc.expect_reject && sc.expect_reject.allow) || {}) as any;
              const ownPrefix = String(ownAllow.path_prefix || hint || '');
              const ownDir = ownPrefix.replace(/[^/]*$/, '');   // 去掉末段 → 资源集合目录
              const dirUsable = ownDir.split('/').filter(Boolean).length >= 2;
              const blockedTarget = hint
                ? winBlocked.filter((b) => b.url.includes(hint)).length : 0;
              const blockedFamily = dirUsable
                ? winBlocked.filter((b) => b.url.includes(ownDir)).length : 0;
              const blockedInScope = blockedTarget + (blockedTarget ? 0 : blockedFamily);
              const blockedOther = winBlocked.length - blockedInScope;
              const why = blockedTarget
                ? `该场景的写请求被写隔离策略阻断（${blockedTarget} 条未到达服务端）`
                : (blockedFamily
                    ? `该场景提交触发的写请求被写隔离策略阻断（${blockedFamily} 条未到达服务端，`
                      + '接口不在场景声明的放行范围内）'
                    : (reqHits.length
                        ? `请求已发出（${reqHits[0].method} ${reqHits[0].url}）但在窗口内未收到响应`
                        : '提交后窗口内未捕获到目标接口的请求'));
              const scene = lastScene;
              console.log('NEG_NO_HIT ' + JSON.stringify({
                scenario_id: sc.scenario_id, hint,
                blocked_target: blockedTarget, blocked_family: blockedFamily,
                blocked_other: blockedOther,
                blocked: winBlocked.slice(0, 10),
                writes: winWrites.slice(0, 30),
                form_errors: seenErrors.slice(0, 8),
                toasts: seenToasts.slice(0, 10),
                url: scene.url,
              }));
              // 归因措辞按"最先出现的证据"排序：前端校验错 > 前端提示 > 无关拦截。
              // 实测 TR-405 的教训：把无关拦截写进结论，会让真实原因（必填未满足）
              // 被读成"网络/隔离问题"。
              const firstToast = seenToasts.length ? seenToasts[0].txt : '';
              const hintText = seenErrors.length
                ? `；前端仍报校验错 ${seenErrors.length} 项，首项「${seenErrors[0].error}」（现场见 NEG_NO_HIT 行）`
                : (firstToast
                    ? `；提交后前端提示「${firstToast}」（现场见 NEG_NO_HIT 行）`
                    : (blockedOther
                        ? `；另有 ${blockedOther} 条与本接口无关的写请求被隔离阻断（不构成本场景结论）`
                        : ''));
              record(sc, 'unverified',
                `规则级负路径：未能取得服务端结论 —— ${why}；期望规则「${sc.rule_ref || sc.scenario_id}」未被验证（${label}）${hintText}`);
              continue;
            }
            // [O9-NEG-JUDGE-BEGIN] 负路径判定（契约测试逐字抽取此区间；勿删标记）
            const parsed = (() => {
              try { return JSON.parse(hit.body || '{}'); } catch { return null; }
            })();
            const bodyCode = parsed && parsed.resultCode != null ? String(parsed.resultCode) : null;
            const bodyMsg = parsed ? String(parsed.resultMsg || parsed.message || '') : '';
            const statusNum = Number(hit.status);
            const httpOk = statusNum >= 200 && statusNum < 400;   // HTTP 层"受理"（2xx/3xx）
            const serverFault = statusNum >= 500;                 // 服务端故障：非"规则拒绝"形态
            const envelopeRejected = bodyCode !== null && bodyCode !== codeNot;
            //: ★ 接线 ``RejectExpect.http_ok``（此前**只序列化、运行期从不读取**的死配置）。
            //:   true（默认）= 期望服务端以「HTTP 2xx + 业务信封 resultCode≠期望」的方式拒绝；
            //:   false       = 期望服务端直接以 HTTP 非 2xx（如 400/403）拒绝。
            const expectHttpOk = expect.http_ok !== false;
            const rejectedByHttp = !httpOk && !serverFault;       // 4xx：HTTP 层拒绝
            const msgHit = msgs.find((m) => m && hit.body.includes(m)) || '';
            const evidence = `HTTP ${hit.status} ${hit.url} requestId:${hit.requestId || '-'}`
              + ` body:${String(hit.body || '').slice(0, 160)}`;
            // ★ rev124（A2）：**数据重复 ≠ 功能缺陷**。
            //   真实落库场景的可重复性靠运行期唯一化（``{{RUN_TAG}}``）。若仍被
            //   「编号/名称已存在」拒绝，说明提交值和历史数据撞了——要么配置是固定值，
            //   要么唯一化没生效。这种拒绝既证明不了「规则未生效」（真缺陷），也不是
            //   pass，只能如实记 unverified，并把"这次提交的是哪个值"写进结论。
            //   旧实现一律走 blocked「疑似缺陷坐实」⇒ 给开发提**假单**（本项目实测）。
            const dupLikely = /已存在|已使用|重複|重复|duplicate|already exist|unique constraint|唯一约|唯一約/i.test(bodyMsg)
              || ['20003', '10002'].includes(String(bodyCode === null ? '' : bodyCode));
            const tagNote = runTagUsed.length
              ? `本次已唯一化（RUN_TAG=${RUN_TAG}，字段 `
                + `${runTagUsed.map((x) => `${x.field}=${x.value}`).join(' / ')}）`
              : '本场景未使用 {{RUN_TAG}}：提交值是固定值，重复运行必然撞唯一约束';
            if ((sc.expect_accept || {}).api_path_hint) {
              // [rev99] 期望受理极性（与期望拒绝互斥，计划期强制）：
              //   5xx ⇒ 疑似服务端缺陷（与拒绝极性同判——期望受理的请求也崩了，同样是缺陷）；
              //   2xx/3xx + resultCode==期望 ⇒ pass「按期望受理」；
              //   2xx + resultCode≠期望 ⇒ blocked「拒绝了应受理的请求」（规则未生效）；
              //   4xx ⇒ blocked「以 HTTP 非 2xx 拒绝了应受理的请求」。
              const expectCode = String((sc.expect_accept as any).result_code != null
                ? (sc.expect_accept as any).result_code : '0');
              if (serverFault) {
                record(sc, 'blocked',
                  `疑似服务端缺陷：目标接口返回 HTTP ${statusNum}（服务端故障），期望受理（expect_accept）的请求也未被正常处理 —— `
                  + `规则「${sc.rule_ref || sc.scenario_id}」无法验证；${evidence}；`
                  + `定位凭据：requestId=${hit.requestId || '未在响应头暴露'}`);
              } else if (httpOk && bodyCode !== null && bodyCode === expectCode) {
                record(sc, 'pass',
                  `服务端按期望受理（配置 expect_accept，resultCode=${bodyCode}）；${evidence}（${label}）`);
              } else if (dupLikely) {
                record(sc, 'unverified',
                  `数据冲突（非功能缺陷，需复核）：服务端以「${bodyMsg.slice(0, 60) || '数据重复'}」`
                  + `拒绝了应受理的请求 —— 提交值撞了服务端唯一约束，`
                  + `不能据此判「规则未生效」；${tagNote}；${evidence}（${label}）`);
              } else if (httpOk) {
                record(sc, 'blocked',
                  `疑似缺陷坐实：服务端拒绝了应受理的请求 —— 规则「${sc.rule_ref || sc.scenario_id}」未生效`
                  + `（实际 resultCode=${bodyCode === null ? '无（响应无业务信封）' : bodyCode}，期望 ${expectCode}）；`
                  + `${evidence}；定位凭据：requestId=${hit.requestId || '未在响应头暴露'}`);
              } else if (dupLikely) {
                record(sc, 'unverified',
                  `数据冲突（非功能缺陷，需复核）：服务端以 HTTP ${statusNum}「${bodyMsg.slice(0, 60) || '数据重复'}」`
                  + `拒绝了应受理的请求 —— 提交值撞了服务端唯一约束；${tagNote}；${evidence}（${label}）`);
              } else {
                record(sc, 'blocked',
                  `疑似缺陷坐实：服务端以 HTTP ${statusNum} 拒绝了应受理的请求 —— 规则「${sc.rule_ref || sc.scenario_id}」未生效；`
                  + `${evidence}；定位凭据：requestId=${hit.requestId || '未在响应头暴露'}`);
              }
            } else if (serverFault) {
              // ① 5xx = 服务端崩溃：既非"按规则拒绝"、也非"受理了非法请求"。
              //    旧实现把 ``httpOk`` 硬编码成 2xx/3xx，5xx 会一路穿透到最后一个 else，
              //    被记成 pass「服务端按规则拒绝」⇒ **"服务端崩溃"类缺陷永远绿**（67197 的形态）。
              record(sc, 'blocked',
                `疑似服务端缺陷：目标接口返回 HTTP ${statusNum}（服务端故障），既非正常受理也非规则拒绝 —— `
                + `规则「${sc.rule_ref || sc.scenario_id}」是否生效无法从该响应判定；`
                + `期望 resultCode≠${codeNot}。${evidence}；`
                + `定位凭据：requestId=${hit.requestId || '未在响应头暴露'}`);
            } else if (!envelopeRejected && httpOk) {
              // 服务端受理了"应被拒绝"的请求 → 该业务规则未生效（DEFECT-001 的形态）
              record(sc, 'blocked',
                `疑似缺陷坐实：服务端受理了应被拒绝的请求 —— 规则「${sc.rule_ref || sc.scenario_id}」未生效；`
                + `${evidence}；建议 UAT 数据佐证并登记缺陷。`
                + `定位凭据：requestId=${hit.requestId || '未在响应头暴露'}`);
            } else if (rejectedByHttp && !expectHttpOk && (!msgs.length || msgHit)) {
              // ③ 配置声明期望「HTTP 层拒绝」，实际确实以非 2xx 拒绝 ⇒ 按期望拒绝。
              record(sc, 'pass',
                `服务端按期望以 HTTP ${statusNum} 拒绝（配置 http_ok=false）；`
                + (msgHit ? `文案命中「${msgHit}」` : '；配置未给出文案关键词')
                + `；${evidence}（${label}）`);
            } else if (msgs.length && !msgHit) {
              // 确实被拒了，但**驳回原因与期望规则不符**（可能是权限/参数等其它原因）：
              // 既不能声称"本规则已生效"，也不构成"本规则未生效"——如实记 unverified，
              // 正是设计文档 §四"误判为缺陷"风险的对策。
              record(sc, 'unverified',
                `规则级负路径：服务端已拒绝，但驳回文案未命中期望关键词 ${JSON.stringify(msgs)}`
                + `（实际 resultCode=${bodyCode === null ? '无' : bodyCode}`
                + ` resultMsg=${bodyMsg.slice(0, 60) || '无'}）—— 无法确认为本规则生效（${label}）；${evidence}`);
            } else if (!httpOk) {
              // ④ 服务端以 HTTP 非 2xx（4xx）拒绝，但配置期望的是「HTTP 2xx + 业务码≠期望」
              //    ⇒ 驳回**方式**与期望不符，无法确认是本规则生效（鉴权/权限/参数错同样 4xx）
              //    ⇒ 保守记 unverified，不冒充"本规则已生效"。
              record(sc, 'unverified',
                `规则级负路径：服务端以 HTTP ${statusNum} 拒绝，与期望的拒绝方式不符`
                + `（配置期望 HTTP 2xx 且 resultCode≠${codeNot}，http_ok=${String(expectHttpOk)}）`
                + `（实际 resultCode=${bodyCode === null ? '无' : bodyCode}）`
                + ` —— 无法确认为本规则生效（${label}）；${evidence}`);
            } else {
              record(sc, 'pass',
                `服务端按规则拒绝：判据 resultCode=${bodyCode === null ? `HTTP ${hit.status}` : `${bodyCode}≠${codeNot}`}`
                + (msgHit ? `；文案命中「${msgHit}」` : '；配置未给出文案关键词')
                + `；${evidence}（${label}）`);
            }
            // [O9-NEG-JUDGE-END]
          } catch (error) {
            const msg = String(error);
            record(sc, 'blocked', `规则级负路径执行异常：${msg.slice(0, 200)}（${label}）`);
            if (msg.includes('has been closed') || msg.includes('Target closed')) return;
          }
        }
      } finally {
        try { await (context as any).unroute('**/*', onNegRoute); } catch { /* 忽略 */ }
        console.log('NEGATIVE_PHASE ' + JSON.stringify(
          {...negativeStat, blocked_requests: negBlocked.length,
           // rev124（A2）：本次唯一化用的标记与实际填入值，结论可回溯到具体编号
           run_tag: RUN_TAG, run_tag_source: RUN_TAG_INFO.source,
           run_tag_used: runTagUsed.slice(0, 8),
           isolation_kinds: {
             upload_blocked: negBlocked.filter((b) => b.kind === 'upload_blocked').length,
             non_upload_api_blocked: negBlocked.filter((b) => b.kind === 'non_upload_api_blocked').length,
             unknown_request_blocked: negBlocked.filter((b) => b.kind === 'unknown_request_blocked').length,
           }}));
      }
    }
    // ===== 「列表可见」阶段（2026-10-16）=====
    // 三条「新建-必填全填-真实落库**并在列表可见**」用例的【预期结果】都写了"列表中可查询到"，
    // 但此前只机检了服务端 resultCode=0，列表可见性靠人工探针补验、不在回归里。
    // 本阶段：写请求（负路径阶段）之后回列表页，按配置的 value 检索并确认记录可见。
    // 口径：**任何环境/定位问题一律记 unverified（不记 blocked）**——
    //       list 阶段位于整批末尾，记 blocked 会把无关用例一起拖成硬失败；
    //       而"没检索到"是**负证据**，同样如实记 unverified 并输出 LIST_VISIBLE_TRACE，
    //       由人复核是"真不可见"还是"查询条件/分页限制"。
    // rev150/rev151：列表/导出阶段的**目标页**与 URL 解析。
    // ① 目标页：门户卡片式应用点卡片会**新开标签页**，测试 fixture 的 ``page``
    //    是控制台首页；在控制台上 goto 应用路由会把 SPA 带进无效路由并挂死
    //    （实测 TR-764：列表阶段 12 分钟无任何 trace）。优先 ``anyScope``（应用作用域），
    //    其次取上下文里第一个非控制台页，最后才回退 ``page``。
    // ② URL 解析：应用 URL 带 per-session accessToken（query），列表直链写死必失效；
    //    配置只写 '#/x' 形式，运行期以**目标页**的 origin+path+search 还原。
    const appPageForNav = (): any => {
      try {
        const sc: any = (typeof anyScope !== 'undefined') ? anyScope : null;
        if (sc) {
          if (typeof sc.goto === 'function') return sc;
          if (typeof sc.page === 'function') {
            const p = sc.page();
            if (p) return p;
          }
        }
        const ps: any[] = page.context().pages();
        const hit = ps.find((p: any) => !/dtcloud-console/.test(String(p.url())));
        if (hit) return hit;
      } catch { /* 回退 */ }
      return page;
    };
    const resolveListUrl = (raw: string, pg: any): string => {
      if (!raw.startsWith('#')) return raw;
      try {
        const u = new URL(pg.url());
        return u.origin + u.pathname + u.search + raw;
      } catch { return raw; }
    };
    // rev153：列表页是异步加载的 SPA——固定等 5s 后采样会读到 0 行
    // （实测 TR-765：列表/导出两阶段都是 rows:0，判定全落 unverified）。
    // 改成"有界轮询到出现行"：最多等 ms，出现行即返回，超时如实返回 0。
    const waitRows = async (pg: any, ms: number): Promise<number> => {
      const deadline = Date.now() + ms;
      let n = 0;
      while (Date.now() < deadline) {
        n = Number(await pg.evaluate(
          () => document.querySelectorAll('.ant-table-tbody tr, .ant-table-row').length,
        ).catch(() => 0));
        if (n > 0) return n;
        await pg.waitForTimeout(1500);
      }
      return n;
    };

    // rev154：更多菜单里的条目必须用**真实鼠标点击**——``evaluate`` 的 ``.click()``
    // 不触发该框架的下拉处理器（实测：导出明细 evaluate 点击后无任何浮层/请求/toast；
    // locator 点击则正常弹出「导出明细 / 选择时间」弹窗）。先鼠标、失败再退回 evaluate。
    const clickDropdownItem = async (pg: any, text: string): Promise<any> => {
      const lbl = String(text || '');
      if (lbl) {
        try {
          const loc = pg.locator('.ant-dropdown:not(.ant-dropdown-hidden) li, .ant-dropdown-menu-item')
            .filter({hasText: lbl}).first();
          if (await loc.count()) {
            await loc.click({timeout: 8000});
            return {ok: true, stage: 'menu_item_clicked', via: 'mouse'};
          }
        } catch { /* 退回 evaluate */ }
      }
      return pg.evaluate((tt: string) => {
        const norm = (s: any) => String(s || '').replace(/\s+/g, ' ').trim();
        const items2 = Array.from(document.querySelectorAll(
          '.ant-dropdown:not(.ant-dropdown-hidden) li, '
          + '.ant-dropdown:not(.ant-dropdown-hidden) a, .ant-dropdown-menu-item'))
          .filter((e: any) => e.offsetParent !== null);
        const hit = items2.find((e: any) => norm(e.innerText) === tt)
          || items2.find((e: any) => norm(e.innerText).indexOf(tt) >= 0);
        if (hit) { (hit as any).click(); return {ok: true, stage: 'menu_item_clicked', via: 'evaluate'}; }
        return {ok: false, why: 'menu_item_not_found',
                items: items2.map((e: any) => norm(e.innerText)).slice(0, 10)};
      }, lbl);
    };

    async function runListChecks(): Promise<void> {
      const page = appPageForNav();   // rev151：列表阶段作用于应用标签页
      // rev152：阶段埋点——跨标签页导航一旦卡住，必须能从日志看出卡在**哪一步**
      // （TR-764/765 只见 PHASE_WATCHDOG，无法区分 goto / evaluate / 搜索 / 扫描）。
      const stageMark = (st: string, extra: any = {}) => {
        try {
          console.log('LIST_STAGE ' + JSON.stringify({
            stage: st, url: String(page.url() || '').slice(0, 110), ...extra,
          }));
        } catch { /* 页面已销毁 */ }
      };
      stageMark('nav_page', {kind: String((page as any)?.constructor?.name || '')});
      const items: any[] = (FORM_PLAN.checks || []).filter((c: any) => c.type === 'list_visible');
      listStat.configured = items.length;
      for (const rej of (FORM_PLAN.list_check_rejects || []).slice(0, 20)) {
        console.log('[list-skip] ' + JSON.stringify(rej));
      }
      if (!items.length) return;
      for (const check of items) {
        const nm = String(check.name || ('list_visible:' + (check.value || check.pick_label || '')));
        let val = String(check.value || '');
        listStat.executed += 1;
        try {
          const url = String(check.list_url || '');
          if (!url) {
            listStat.unverified += 1;
            recAll(check, nm, 'unverified',
                   '未配置列表页 URL（list_checks[].list_url 或 locators.list_url）');
            continue;
          }
          stageMark('goto_begin', {target: resolveListUrl(url, page).slice(0, 120)});
          await page.goto(resolveListUrl(url, page), {timeout: 30000, waitUntil: 'domcontentloaded'}).catch(() => undefined);
          stageMark('goto_done');
          const waitedRows = await waitRows(page, 30000);
          stageMark('rows_present', {rows: waitedRows});
          const pickRoot = async (): Promise<any> => {
            let best: any = null, bn = -1;
            const cands: any[] = [page, ...page.frames()];
            for (const f of cands) {
              try {
                const n = Number(await f.evaluate(
                  () => document.querySelectorAll('.ant-table-row, .ant-table-tbody tr').length));
                if (n > bn) { bn = n; best = f; }
              } catch { /* 跨域 */ }
            }
            return best;
          };
          let root: any = await pickRoot();
          let rowCount = root ? Number(await root.evaluate(
            () => document.querySelectorAll('.ant-table-row, .ant-table-tbody tr').length).catch(() => 0)) : 0;
          if (!rowCount) {
            await page.waitForTimeout(4000);
            root = await pickRoot();
            rowCount = root ? Number(await root.evaluate(
              () => document.querySelectorAll('.ant-table-row, .ant-table-tbody tr').length).catch(() => 0)) : 0;
          }
          // ===== rev162：row_pick —— 按列文本筛行，取命中行的指定列当 value =====
          // 动机：动作通道会推进数据状态（待启用→启用→禁用终态），写死 value 的配置
          // 跑 2–3 次必然找不到目标行（TR-773→775 实测：FCZ 走完一轮换 PFG）。
          // 改为运行期按状态列筛行，配置与具体数据解耦；pick 出 value 后仍走原
          // 「搜索 → 扫描 → 动作」路径，后续判定逻辑一律不变。
          const rp = check.row_pick;
          let pickInfo: any = null;
          if (rp) {
            // rev164（TR-777 实测 cells:["禁用"] 只有 1 行）：列表页会**保留上一检查项的
            // 搜索条件**（同 hash 的 goto 是 no-op、SPA 不重载）⇒ 上一个 readonly 断言
            // 刚按自己的 value 搜过，这里就只看得到那一行。pick 前必须先清空搜索并
            // 重新查询，把整页数据找回来。
            const phs0 = (Array.isArray(check.search_placeholder) && check.search_placeholder.length)
              ? check.search_placeholder.map((s: any) => String(s))
              : ['搜索问卷标题', 'Search'];
            let resetNote: any = {ok: false, why: 'no_root'};
            if (root) {
              resetNote = await root.evaluate((src: string) => {
                const rx = new RegExp(src, 'i');
                const ins = Array.from(document.querySelectorAll('input')) as any[];
                const t = ins.find((i: any) => i.type !== 'hidden' && rx.test(String(i.placeholder || '')));
                if (!t) {
                  return {ok: false, why: 'no_search_box',
                          phs: ins.map((i: any) => String(i.placeholder || '')).filter(Boolean).slice(0, 10)};
                }
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
                const before = String(t.value || '');
                t.focus(); setter.call(t, '');
                t.dispatchEvent(new Event('input', {bubbles: true}));
                t.dispatchEvent(new Event('change', {bubbles: true}));
                return {ok: true, before};
              }, phs0.join('|')).catch((e: any) => ({ok: false, why: 'eval_error', err: String(e).slice(0, 80)}));
              if (resetNote && resetNote.ok) {
                const btn0 = root.locator('button')
                  .filter({hasText: /搜\s*索|查\s*詢|查\s*询|Search/i}).first();
                if (await btn0.count().catch(() => 0)) {
                  await btn0.click({timeout: 8000}).catch(() => undefined);
                }
                await page.waitForTimeout(1200);
                await waitRows(page, 20000);
              }
            }
            console.log('LIST_PICK_RESET ' + JSON.stringify({
              name: nm, before_value: resetNote && resetNote.before, reset: resetNote,
            }));
            const spec = {
              column: String(rp.column || '状态'),
              in: (rp.in || []).map((s: any) => String(s)),
              valueColumn: String(rp.value_column || ''),
            };
            const pk: any = root ? await root.evaluate((sp: any) => {
              const norm = (s: any) => String(s || '').replace(/\s+/g, ' ').trim();
              // 固定列会克隆出「每行只有 1 个 td」的 tbody，混在一起数下标必然错位：
              // 取 td 数最多的那一组作为数据行。
              let dataRows: any[] = [], bestCols = 0;
              Array.prototype.forEach.call(document.querySelectorAll('.ant-table-tbody'), (tb: any) => {
                const rs = Array.prototype.filter.call(tb.children, (tr: any) =>
                  tr.querySelector('td') && tr.offsetParent !== null);
                if (!rs.length) return;
                const n = rs[0].querySelectorAll('td').length;
                if (n > bestCols) { bestCols = n; dataRows = rs; }
              });
              if (!dataRows.length) return {ok: false, why: 'no_rows'};
              // 表头优先取与数据行列数一致的；否则取 th 最多的 —— 数据行末尾「操作」
              // 被拆到克隆 tbody 时列数差 1，但状态列在操作列之前，索引仍对齐。
              let heads: string[] = [];
              Array.prototype.forEach.call(document.querySelectorAll('.ant-table-thead'), (th: any) => {
                const hs = Array.prototype.map.call(th.querySelectorAll('th'),
                  (e: any) => norm(e.innerText));
                if (hs.length === bestCols) heads = hs;
              });
              if (!heads.length) {
                Array.prototype.forEach.call(document.querySelectorAll('.ant-table-thead'), (th: any) => {
                  const hs = Array.prototype.map.call(th.querySelectorAll('th'),
                    (e: any) => norm(e.innerText));
                  if (hs.length > heads.length) heads = hs;
                });
              }
              if (!heads.length) return {ok: false, why: 'no_header'};
              const ci = heads.indexOf(sp.column);
              if (ci < 0) return {ok: false, why: 'column_not_found', column: sp.column, heads};
              const vci = sp.valueColumn ? heads.indexOf(sp.valueColumn) : -1;
              const cells = dataRows.map((tr: any) => {
                const t = tr.querySelectorAll('td')[ci];
                return t ? norm(t.innerText) : '';
              });
              // 精确优先：否则 in=["启用"] 会把「待启用」也算命中（状态机里两者相邻）
              let hit = -1, mode = '';
              cells.forEach((c: string, i: number) => {
                if (hit < 0 && sp.in.indexOf(c) >= 0) { hit = i; mode = 'exact'; }
              });
              if (hit < 0) {
                cells.forEach((c: string, i: number) => {
                  if (hit < 0 && sp.in.some((w: string) => w && c.indexOf(w) >= 0)) {
                    hit = i; mode = 'contains';
                  }
                });
              }
              if (hit < 0) {
                return {ok: false, why: 'no_row_matched', column: sp.column,
                        want: sp.in, cells: cells.slice(0, 12), heads};
              }
              const tds = dataRows[hit].querySelectorAll('td');
              const pi = vci >= 0 ? vci : 0;
              const t0 = tds[pi];
              return {ok: true, rowIndex: hit, status: cells[hit], matchMode: mode,
                      value: t0 ? norm(t0.innerText) : '',
                      row: norm(dataRows[hit].innerText).slice(0, 120),
                      rows: dataRows.length, heads};
            }, spec).catch((e: any) => ({ok: false, why: 'eval_error', err: String(e).slice(0, 100)}))
              : {ok: false, why: 'no_root'};
            pickInfo = pk;
            console.log('LIST_PICK_TRACE ' + JSON.stringify({name: nm, spec, result: pk}));
            if (!pk || !pk.ok || !String(pk.value || '')) {
              listStat.unverified += 1;
              recAll(check, nm, 'unverified',
                     `row_pick 未命中（列「${spec.column}」期望 ${JSON.stringify(spec.in)}）：`
                     + `${(pk && pk.why) || 'no_value'}`
                     + '，需按当前数据状态调整配置，或该状态已无可用行');
              continue;
            }
            val = String(pk.value);
          }
          // rev163：空 value 绝对不能进入「搜索 → 扫描 → 动作」链路 ——
          // 空串会匹配到任意行（rows 全成 hits），于是 found 恒真、动作点到第一行，
          // 日志上像成功、实际目标行没被选中（TR-776 实证：value="" 却 pass）。
          if (!val) {
            listStat.unverified += 1;
            recAll(check, nm, 'unverified',
                   '未取到记录标识（value 为空且 row_pick 未配置/未命中）：'
                   + '空串会匹配任意行，不能据此判定可见或执行行内操作');
            continue;
          }
          const phs: string[] = Array.isArray(check.search_placeholder)
            ? check.search_placeholder.map((s: any) => String(s)).filter(Boolean) : [];
          let searched = false;
          let searchNote = phs.length ? 'no_search_box' : 'no_search_config';
          if (root && phs.length && rowCount) {
            const filled: any = await root.evaluate(({src, value}: any) => {
              const rx = new RegExp(src, 'i');
              const ins = Array.from(document.querySelectorAll('input')) as any[];
              const t = ins.find((i: any) => rx.test(String(i.placeholder || '')) && String(i.type) !== 'hidden');
              if (!t) {
                return {ok: false, phs: ins.map((i: any) => String(i.placeholder || '')).filter(Boolean).slice(0, 12)};
              }
              const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
              t.focus(); setter.call(t, value);
              t.dispatchEvent(new Event('input', {bubbles: true}));
              t.dispatchEvent(new Event('change', {bubbles: true}));
              return {ok: true, ph: String(t.placeholder || '')};
            }, {src: phs.join('|'), value: val}).catch((e: any) => ({ok: false, err: String(e).slice(0, 80)}));
            searchNote = JSON.stringify(filled).slice(0, 170);
            if (filled && filled.ok) {
              const btn = root.locator('button')
                .filter({hasText: /搜\s*索|查\s*詢|查\s*询|Search/i}).first();
              if (await btn.count().catch(() => 0)) {
                await btn.click({timeout: 8000}).catch(() => undefined);
              }
              await page.waitForTimeout(1500);
              await waitRows(page, 20000);
              searched = true;
            }
          }
          stageMark('root_ready', {rows: rowCount});
          const scan = async (): Promise<any> => (root ? await root.evaluate((v: string) => {
            const norm = (s: any) => String(s || '').replace(/\s+/g, ' ').trim();
            const rows = Array.from(document.querySelectorAll('.ant-table-row, .ant-table-tbody tr'));
            const hits = rows.filter((r: any) => norm(r.innerText).indexOf(v) >= 0);
            return {rows: rows.length, hits: hits.length,
                    sample: hits.length ? norm((hits[0] as any).innerText).slice(0, 110) : ''};
          }, val).catch((e: any) => ({rows: -1, hits: -1, err: String(e).slice(0, 80)}))
            : {rows: 0, hits: 0});
          let res: any = await scan();
          stageMark('scan_done', {rows: res ? res.rows : -1, hits: res ? res.hits : -1});
          const maxPages = Math.max(1, Number(check.max_pages) || 3);
          let pages = 1;
          while (res && res.hits === 0 && pages < maxPages) {
            let advanced = false;
            try {
              const next = root.locator('li.ant-pagination-next').first();
              const disabled = await next.evaluate((el: any) =>
                /ant-pagination-disabled/.test(String(el.className))).catch(() => true);
              if (!disabled) { await next.click({timeout: 5000}); advanced = true; }
            } catch { advanced = false; }
            if (!advanced) break;
            await page.waitForTimeout(2500);
            pages += 1;
            res = await scan();
          }
          let found = !!(res && res.hits > 0);
          console.log('LIST_VISIBLE_TRACE ' + JSON.stringify({
            value: val, url: url.slice(0, 90), rows: res ? res.rows : -1, pages,
            searched, search: searchNote, expect: String(check.expect || 'found'),
            found, sample: (res && res.sample) || '',
            picked: pickInfo ? {rowIndex: pickInfo.rowIndex, status: pickInfo.status,
                                value: pickInfo.value, matchMode: pickInfo.matchMode} : null,
          }));
          // ===== rev148：行内操作（更多菜单 / 直点 + Popconfirm 确认）=====
          // 实测口径（xy_p1b/p2 探针）：①行操作可能渲染在数据行的**独立第二行 tr**，
          // 必须同时扫数据行与其后一行；②antd 确认框有 Popover 与 Modal 两种形态，
          // 确认定位必须**同时**采样 .ant-popover:not(.ant-popover-hidden) 与
          // .ant-modal-confirm——只查一种会把「弹了气泡确认」误判成找不到；
          // ③操作后重按搜索回读列表，以回读结果作为 expect 判定。
          let actionNote = '';
          // rev160（TR-772 实测）：配置了 action 但没点成功时，旧逻辑仅把它写进 note，
          // 而 found（搜索命中）仍为 true ⇒ 判 **pass** —— 「行内操作通道没跑通」被掩盖。
          // 配置了动作就必须真点到（且确认框真命中），否则记 unverified，不判通过。
          let actionFailed = '';
          const act = check.action;
          if (act && found && root) {
            const clickInRow = (val0: string, target: string, menu0: string): any => root.evaluate(
              ({value: vv, label: tt, menu: mm}: any) => {
                const norm = (s: any) => String(s || '').replace(/\s+/g, ' ').trim();
                const rows = Array.from(document.querySelectorAll('.ant-table-tbody tr, .ant-table-row'))
                  .filter((r: any) => r.offsetParent !== null);
                const idx = rows.findIndex((r: any) => norm(r.innerText).indexOf(vv) >= 0);
                if (idx < 0) return {ok: false, why: 'row_not_found'};
                // rev155：操作列在右侧固定列的**克隆 tbody** 里（整页 10 数据行 + 10 操作行，
                // 数据行与操作行并不相邻）。按「同 tbody 内行序号对齐」取出对应操作行。
                const rowWithOps = (row0: any) => {
                  const out: any[] = [row0];
                  const tb0: any = row0.parentElement;
                  const ri = Array.prototype.indexOf.call(tb0.children, row0);
                  Array.prototype.forEach.call(document.querySelectorAll('.ant-table-tbody'),
                    (t2: any) => {
                      const cand = t2.children[ri];
                      if (cand && cand !== row0) out.push(cand);
                    });
                  return out;
                };
                const scopes = rowWithOps(rows[idx]);
                if (mm) {
                  for (const sc of scopes) {
                    const more = Array.from(sc.querySelectorAll('a,button,span'))
                      .filter((e: any) => e.offsetParent !== null)
                      .find((a: any) => norm(a.innerText) === mm);
                    if (more) { (more as any).click(); return {ok: true, stage: 'menu_opened'}; }
                  }
                  return {ok: false, why: 'menu_not_found'};
                }
                for (const sc of scopes) {
                  const hit = Array.from(sc.querySelectorAll('a,button,span'))
                    .filter((e: any) => e.offsetParent !== null)
                    .find((a: any) => norm(a.innerText) === tt);
                  if (hit) { (hit as any).click(); return {ok: true, stage: 'clicked'}; }
                }
                return {ok: false, why: 'action_not_found'};
              }, {value: val0, label: target, menu: menu0});
            // rev156：候选标签逐个尝试（行状态不同 → 可点项不同）
            const actLabels: string[] = (Array.isArray(act.labels) && act.labels.length)
              ? act.labels.map((s: any) => String(s))
              : [String(act.label || '')];
            let step1: any = null;
            let usedLabel = actLabels[0];
            for (const lb of actLabels) {
              if (!lb) continue;
              step1 = await clickInRow(val, lb, String(act.menu || ''));
              if (step1 && step1.ok) { usedLabel = lb; break; }
            }
            if (step1 && step1.ok && step1.stage === 'menu_opened') {
              await page.waitForTimeout(1200);
              step1 = await clickDropdownItem(page, usedLabel);
            }
            await page.waitForTimeout(1000);
            let conf: any = {ok: false, why: act.confirm ? 'confirm_pending' : 'no_confirm'};
            if (step1 && step1.ok && act.confirm) {
              conf = await root.evaluate((cc: string) => {
                const norm = (s: any) => String(s || '').replace(/\s+/g, ' ').trim();
                const pops = Array.from(document.querySelectorAll(
                  '.ant-popover:not(.ant-popover-hidden), .ant-modal-confirm'))
                  .filter((p: any) => p.offsetParent !== null || p.className.includes('ant-modal-confirm'));
                for (const p of pops) {
                  const btns = Array.from((p as any).querySelectorAll('button'))
                    .filter((b: any) => b.offsetParent !== null);
                  const hit = btns.find((b: any) => norm(b.innerText) === cc)
                    || btns.find((b: any) => /确\s*定|確\s*定|OK/i.test(norm(b.innerText)));
                  if (hit) {
                    const out = {ok: true, clicked: norm((hit as any).innerText),
                                 text: norm((p as any).innerText).slice(0, 90)};
                    (hit as any).click();
                    return out;
                  }
                }
                return {ok: false, why: 'confirm_not_found', visible: pops.length};
              }, String(act.confirm));
            }
            await page.waitForTimeout(2500);
            // 操作后回读：重按搜索（输入框仍保留关键词），以回读为准
            if (step1 && step1.ok && root) {
              const btn2 = root.locator('button')
                .filter({hasText: /搜\s*索|查\s*詢|查\s*询/i}).first();
              if (await btn2.count().catch(() => 0)) {
                await btn2.click({timeout: 8000}).catch(() => undefined);
              }
              await page.waitForTimeout(3000);
              res = await scan();
              const postVal = String(act.post_value || val);
              found = !!(res && res.hits > 0);
              actionNote = `action=${step1.stage}/${step1.why || 'ok'}`
                + `,confirm=${conf.ok ? conf.clicked : conf.why || conf.why}`
                + `,post_found=${found}`;
              // rev160：动作或确认框任一步没走通，本轮不能算通过
              if (!step1.ok) actionFailed = String(step1.why || 'action_failed');
              else if (act.confirm && !conf.ok) actionFailed = String(conf.why || 'confirm_failed');
              console.log('LIST_ACTION_TRACE ' + JSON.stringify({
                value: val, label: act.label, menu: act.menu, confirm: act.confirm,
                step1, confirm: conf, post: {rows: res ? res.rows : -1, hits: res ? res.hits : -1},
                post_value: postVal, found, action_failed: actionFailed,
              }));
            } else {
              actionFailed = step1 ? String(step1.why || 'action_failed') : 'no_root';
              actionNote = `action_failed=${actionFailed}`;
              console.log('LIST_ACTION_TRACE ' + JSON.stringify({value: val, step1}));
            }
          }
          // ===== rev148：查看只读（查看页全部输入控件应禁用）=====
          if (check.readonly && found && root) {
            const ro = check.readonly;
            const vm = String(ro.view_menu || '');
            let opened: any = await root.evaluate(({value: vv, label: tt, menu: mm}: any) => {
              const norm = (s: any) => String(s || '').replace(/\s+/g, ' ').trim();
              const rows = Array.from(document.querySelectorAll('.ant-table-tbody tr, .ant-table-row'))
                .filter((r: any) => r.offsetParent !== null);
              const idx = rows.findIndex((r: any) => norm(r.innerText).indexOf(vv) >= 0);
              if (idx < 0) return {ok: false, why: 'row_not_found'};
              // rev155：操作列在右侧固定列的克隆 tbody 里，按行序号对齐（同 action/export）
              const rowWithOps = (row0: any) => {
                const out: any[] = [row0];
                const tb0: any = row0.parentElement;
                const ri = Array.prototype.indexOf.call(tb0.children, row0);
                Array.prototype.forEach.call(document.querySelectorAll('.ant-table-tbody'),
                  (t2: any) => {
                    const cand = t2.children[ri];
                    if (cand && cand !== row0) out.push(cand);
                  });
                return out;
              };
              const scopes = rowWithOps(rows[idx]);
              const want = (sc: any, text: string) => Array.from(sc.querySelectorAll('a,button,span'))
                .filter((e: any) => e.offsetParent !== null)
                .find((a: any) => norm(a.innerText) === text);
              if (mm) {
                for (const sc of scopes) {
                  const more = want(sc, mm);
                  if (more) { (more as any).click(); return {ok: true, stage: 'menu_opened'}; }
                }
                return {ok: false, why: 'menu_not_found'};
              }
              for (const sc of scopes) {
                const hit = want(sc, tt) || Array.from(sc.querySelectorAll('a,button,span'))
                  .filter((e: any) => e.offsetParent !== null)
                  .find((a: any) => tt.split('|').some((t: string) => norm(a.innerText) === t));
                if (hit) { (hit as any).click(); return {ok: true, stage: 'clicked'}; }
              }
              return {ok: false, why: 'view_not_found'};
            }, {value: val, label: String(ro.view_label || '查看'), menu: vm});
            if (opened && opened.ok && opened.stage === 'menu_opened') {
              await page.waitForTimeout(1200);
              opened = await root.evaluate((tt: string) => {
                const norm = (s: any) => String(s || '').replace(/\s+/g, ' ').trim();
                const items = Array.from(document.querySelectorAll(
                  '.ant-dropdown:not(.ant-dropdown-hidden) li, '
                  + '.ant-dropdown:not(.ant-dropdown-hidden) a, .ant-dropdown-menu-item'))
                  .filter((e: any) => e.offsetParent !== null);
                const hit = items.find((e: any) => tt.split('|').some(
                  (t: string) => norm(e.innerText) === t));
                if (hit) { (hit as any).click(); return {ok: true, stage: 'menu_item_clicked'}; }
                return {ok: false, why: 'view_menu_item_not_found'};
              }, String(ro.view_label || '查看'));
            }
            await page.waitForTimeout(3500);
            const roState: any = await page.evaluate(() => {
              const vis = (e: any) => e.offsetParent !== null;
              const inputs = Array.from(document.querySelectorAll('input, textarea')).filter(vis);
              const selects = Array.from(document.querySelectorAll('.ant-select')).filter(vis);
              return {
                url: String(location.hash || location.href).slice(0, 90),
                inputs_total: inputs.length,
                inputs_disabled: inputs.filter((i: any) => i.disabled || i.readOnly).length,
                selects_total: selects.length,
                selects_disabled: selects.filter((s: any) =>
                  s.className.includes('ant-select-disabled')).length,
              };
            });
            const allDisabled = roState.inputs_total > 0
              && roState.inputs_disabled === roState.inputs_total
              && roState.selects_disabled === roState.selects_total;
            console.log('READONLY_TRACE ' + JSON.stringify({value: val, opened, ...roState, allDisabled}));
            const roObs = `查看只读：输入 ${roState.inputs_disabled}/${roState.inputs_total} 禁用，`
              + `下拉 ${roState.selects_disabled}/${roState.selects_total} 禁用`
              + `（${opened && opened.ok ? '已进入' : '进入失败:' + (opened && opened.why)}）`;
            if (allDisabled && opened && opened.ok) {
              listStat.pass += 1;
              recAll(check, nm, 'pass', roObs);
            } else {
              listStat.unverified += 1;
              recAll(check, nm, 'unverified',
                     allDisabled ? roObs : `${roObs}——存在可编辑控件或未进入查看页`);
            }
            // 返回列表，恢复现场
            await page.keyboard.press('Escape').catch(() => undefined);
            await page.waitForTimeout(800);
            continue;
          }
          if (String(check.expect || 'found') === 'found') {
            if (found && actionFailed) {
              // rev160：列表可见成立，但配置的行内动作没真执行 —— 通道能力未被验证，
              // 不能记为通过（TR-772 的 action_not_found 曾被 pass 掩盖）。
              listStat.unverified += 1;
              recAll(check, nm, 'unverified',
                     `列表可见但行内操作未完成（${actionFailed}）：`
                     + `候选标签 ${JSON.stringify((check.action || {}).labels || [])} 均未命中当前行状态，`
                     + `动作与确认框链路未被验证；${actionNote}`);
            } else if (found) {
              listStat.pass += 1;
              recAll(check, nm, 'pass',
                     `列表可见：${res.rows} 行中命中 ${res.hits} 行（search=${searched ? 'on' : 'off'}，翻 ${pages} 页）`
                     + (actionNote ? `；${actionNote}` : ''));
            } else {
              listStat.unverified += 1;
              recAll(check, nm, 'unverified',
                     `未在列表中检索到「${val}」（前 ${pages} 页共 ${res ? res.rows : 0} 行，`
                     + `search=${searched ? 'on' : 'off'}${searched ? '' : '：未命中搜索框，仅翻页扫描'}）`
                     + `——现场见 LIST_VISIBLE_TRACE`);
            }
          } else if (!found) {
            listStat.pass += 1;
            recAll(check, nm, 'pass', `列表按期望不可见：「${val}」未命中`);
          } else {
            listStat.unverified += 1;
            recAll(check, nm, 'unverified',
                   `期望不可见但检索到了「${val}」——现场见 LIST_VISIBLE_TRACE`);
          }
        } catch (error) {
          listStat.unverified += 1;
          recAll(check, nm, 'unverified', `列表检查异常：${String(error).slice(0, 200)}`);
        }
      }
      console.log('LIST_PHASE ' + JSON.stringify(listStat));
    }

    // ===== rev170：筛选区独立通道（runFilterChecks）=====
    // 与 list_checks/export_checks 同形：配置驱动、独立阶段、被拒不静默（[filter-skip]）。
    // 判定口径（诚实边界）：只校验第 1 页可见行目标列取值；筛选后 0 行/控件未找到 =
    // unverified（不冒充业务结论，也不冒充通过）。选项点击必须先精确后包含 ——
    // 「启用」是「待启用」的子串，包含优先会把错误选项点进去。
    async function runFilterChecks(): Promise<void> {
      const page = appPageForNav();
      const stageMark = (st: string, extra: any = {}) => {
        try {
          console.log('FILTER_STAGE ' + JSON.stringify({
            stage: st, url: String(page.url() || '').slice(0, 110), ...extra,
          }));
        } catch { /* 页面已销毁 */ }
      };
      const items: any[] = (FORM_PLAN.checks || []).filter((c: any) => c.type === 'filter_check');
      filterStat.configured = items.length;
      for (const rej of (FORM_PLAN.filter_check_rejects || []).slice(0, 20)) {
        console.log('[filter-skip] ' + JSON.stringify(rej));
      }
      if (!items.length) return;
      const pickRoot = async (): Promise<any> => {
        let best: any = null, bn = -1;
        const cands: any[] = [page, ...page.frames()];
        for (const f of cands) {
          try {
            const n = Number(await f.evaluate(
              () => document.querySelectorAll('.ant-table-row, .ant-table-tbody tr').length));
            if (n > bn) { bn = n; best = f; }
          } catch { /* 跨域 */ }
        }
        return best;
      };
      for (const check of items) {
        const nm = String(check.name || ('filter:' + (check.option || '')));
        filterStat.executed += 1;
        try {
          const url = String(check.list_url || '');
          if (!url) {
            filterStat.unverified += 1;
            recAll(check, nm, 'unverified',
                   '未配置列表页 URL（filter_checks[].list_url 或 locators.list_url）');
            continue;
          }
          stageMark('goto_begin');
          await page.goto(resolveListUrl(url, page), {timeout: 30000, waitUntil: 'domcontentloaded'}).catch(() => undefined);
          stageMark('goto_done');
          await waitRows(page, 30000);
          const root: any = await pickRoot();
          if (!root) {
            filterStat.unverified += 1;
            recAll(check, nm, 'unverified', '列表页未渲染出表格（pickRoot 无候选）——现场见 FILTER_STAGE');
            continue;
          }
          // rev172：每条检查前先点「重置」清残留——goto 同 hash 是 no-op、SPA 不重载，
          // 列表页会保留上一检查项的筛选条件（rev158/rev164 同源教训）
          const preReset = root.locator('button')
            .filter({hasText: new RegExp(String(check.reset_label || '重\\s*置|重\\s*設|Reset'), 'i')})
            .first();
          if (await preReset.count().catch(() => 0)) {
            await preReset.click({timeout: 8000}).catch(() => undefined);
            stageMark('preset_reset');
            await page.waitForTimeout(1500);
            await waitRows(page, 20000);
          }
          // ① 找筛选下拉：antd v3 Select 按 placeholder 候选正则定位（先 enumerate
          //    可见 .ant-select，再交给 Playwright 真实点击 —— evaluate 内的合成事件
          //    不一定触发 rc-select 的 open 处理器，rev154 同源教训）。
          const phsSrc: string = (Array.isArray(check.control_placeholder) && check.control_placeholder.length)
            ? check.control_placeholder.map((s: any) => String(s)).filter(Boolean).join('|')
            : '狀態|状态|Status';
          const ctrl: any = await root.evaluate((src: string) => {
            const rx = new RegExp(src, 'i');
            const sels = Array.from(document.querySelectorAll('.ant-select'))
              .filter((s: any) => s.offsetParent !== null);
            const idx = sels.findIndex((s: any) => {
              const ph = s.querySelector('.ant-select-selection__placeholder');
              return ph && rx.test(String(ph.textContent || '').trim());
            });
            return {ok: idx >= 0, idx,
                    phs: sels.map((s: any) => String(
                      (s.querySelector('.ant-select-selection__placeholder') || {textContent: ''}).textContent || ''
                    ).trim()).filter(Boolean).slice(0, 8)};
          }, phsSrc).catch((e: any) => ({ok: false, why: 'eval_error', err: String(e).slice(0, 80)}));
          console.log('FILTER_CONTROL ' + JSON.stringify({name: nm, wanted: phsSrc, found: ctrl}));
          if (!ctrl || !ctrl.ok) {
            filterStat.unverified += 1;
            const seen = (ctrl && ctrl.phs && ctrl.phs.length) ? `，页面可见 placeholder：${JSON.stringify(ctrl.phs)}` : '';
            recAll(check, nm, 'unverified',
                   `未找到筛选下拉（placeholder 候选 ${phsSrc}${seen}）——需按页面实际控件调整 control_placeholder`);
            continue;
          }
          const visSels = root.locator('.ant-select:visible');
          await visSels.nth(ctrl.idx).click({timeout: 8000}).catch(() => undefined);
          await page.waitForTimeout(800);
          // ② 选选项：面板条目先 enumerate 文本，**先精确后包含**（防子串误选）
          const optLabels: string[] = (Array.isArray(check.option_labels) && check.option_labels.length)
            ? check.option_labels.map((s: any) => String(s)).filter(Boolean)
            : [String(check.option || '')];
          const ddLoc = root.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden)').last();
          const ddCount = await ddLoc.count().catch(() => 0);
          let picked = -1, pickedBy = '', itemTexts: string[] = [];
          if (ddCount) {
            const lis = ddLoc.locator('li[role=option], .ant-select-dropdown-menu-item:visible');
            const n2 = await lis.count().catch(() => 0);
            for (let i = 0; i < n2; i++) {
              itemTexts.push(String(await lis.nth(i).innerText().catch(() => '')).replace(/\s+/g, ' ').trim());
            }
            outer:
            for (const mode of ['exact', 'contains']) {
              for (const ol of optLabels) {
                for (let i = 0; i < itemTexts.length; i++) {
                  const hit = mode === 'exact' ? itemTexts[i] === ol
                    : itemTexts[i].indexOf(ol) >= 0;
                  if (hit) { picked = i; pickedBy = mode + ':' + ol; break outer; }
                }
              }
            }
          }
          console.log('FILTER_OPTIONS ' + JSON.stringify({
            name: nm, labels: optLabels, items: itemTexts.slice(0, 12),
            picked, pickedBy, dropdowns: ddCount,
          }));
          if (picked < 0) {
            filterStat.unverified += 1;
            recAll(check, nm, 'unverified',
                   `下拉面板${ddCount ? '未命中选项' : '未出现'}（候选 ${JSON.stringify(optLabels)}，面板条目 ${JSON.stringify(itemTexts.slice(0, 8))}）`);
            await page.keyboard.press('Escape').catch(() => undefined);
            continue;
          }
          const lis2 = ddLoc.locator('li[role=option], .ant-select-dropdown-menu-item:visible');
          await lis2.nth(picked).click({timeout: 5000}).catch(() => undefined);
          // rev172：本 SUT 选中选项**不自动查询**（TR-790 实测：选「待启用」后列表仍是
          // 上一条件残留的「启用」行），必须点「搜索/查询」触发刷新；没有按钮则如实记录
          const qbtn = root.locator('button')
            .filter({hasText: /搜\s*索|查\s*詢|查\s*询|Search/i}).first();
          const qbtnN = await qbtn.count().catch(() => 0);
          if (qbtnN) {
            await qbtn.click({timeout: 8000}).catch(() => undefined);
            stageMark('query_clicked', {n: qbtnN});
          }
          await page.waitForTimeout(2500);
          await waitRows(page, 20000);
          // 回读选中值（antd v3：selected-value；回读不到不阻断，仅取证）
          const selValue: string = String(await visSels.nth(ctrl.idx).evaluate((s: any) => {
            const el = s.querySelector('.ant-select-selection-selected-value, .ant-select-selection__selected-value');
            return el ? String(el.textContent || '').trim() : '';
          }).catch(() => ''));
          // ③ 结果集断言：第 1 页可见行的目标列全量命中 expect_in
          //    （与 row_pick 同口径：取 td 数最多的 tbody 为数据行，防固定列克隆错位）
          const sample: any = await root.evaluate((spec: any) => {
            const norm = (s: any) => String(s || '').replace(/\s+/g, ' ').trim();
            let dataRows: any[] = [], bestCols = 0;
            Array.prototype.forEach.call(document.querySelectorAll('.ant-table-tbody'), (tb: any) => {
              const rs = Array.prototype.filter.call(tb.children, (tr: any) =>
                tr.querySelector('td') && tr.offsetParent !== null);
              if (!rs.length) return;
              const n = rs[0].querySelectorAll('td').length;
              if (n > bestCols) { bestCols = n; dataRows = rs; }
            });
            if (!dataRows.length) return {ok: false, why: 'no_rows'};
            let heads: string[] = [];
            Array.prototype.forEach.call(document.querySelectorAll('.ant-table-thead'), (th: any) => {
              const hs = Array.prototype.map.call(th.querySelectorAll('th'), (e: any) => norm(e.innerText));
              if (hs.length === bestCols) heads = hs;
            });
            if (!heads.length) {
              heads = Array.prototype.map.call(
                document.querySelectorAll('.ant-table-thead th'), (e: any) => norm(e.innerText));
            }
            const ci = heads.indexOf(norm(spec.column));
            if (ci < 0) return {ok: false, why: 'column_not_found', heads: heads.slice(0, 12)};
            const vals = dataRows.map((r: any) => norm((r.querySelectorAll('td')[ci] || {}).innerText));
            return {ok: true, rows: dataRows.length, colIndex: ci, values: vals.slice(0, 30)};
          }, {column: String(check.expect_column || '状态')}).catch((e: any) =>
            ({ok: false, why: 'eval_error', err: String(e).slice(0, 80)}));
          const expectIn: string[] = (Array.isArray(check.expect_in) && check.expect_in.length)
            ? check.expect_in.map((s: any) => String(s)) : [String(check.option || '')];
          let filterOk = false, badVals: string[] = [], dist: Record<string, number> = {};
          if (sample && sample.ok) {
            for (const v of (sample.values || [])) dist[v] = (dist[v] || 0) + 1;
            badVals = (sample.values || []).filter((v: string) =>
              !expectIn.some((e: string) => v === e || v.indexOf(e) >= 0));
            filterOk = sample.rows > 0 && badVals.length === 0;
          }
          // ④ 重置（配置了才做）：点重置 → 断言下拉清空（选中值消失/placeholder 回显/
          //    回显到「已清空」等值文本——本 SUT 重置置回「全部」，TR-789 实测）
          const clearVals: string[] = (Array.isArray(check.reset_clear_values) && check.reset_clear_values.length)
            ? check.reset_clear_values.map((s: any) => String(s)) : ['全部', '全部', 'All'];
          let resetOk = true, resetNote = 'not_configured';
          let afterReset: any = null;
          if (check.reset) {
            const rbtn = root.locator('button')
              .filter({hasText: new RegExp(String(check.reset_label || '重\\s*置|重\\s*設|Reset'), 'i')})
              .first();
            const rbCount = await rbtn.count().catch(() => 0);
            if (!rbCount) {
              resetOk = false; resetNote = 'reset_button_not_found';
            } else {
              await rbtn.click({timeout: 8000}).catch(() => undefined);
              await page.waitForTimeout(2000);
              afterReset = await root.evaluate((idx: number) => {
                const sels = Array.from(document.querySelectorAll('.ant-select'))
                  .filter((s: any) => s.offsetParent !== null);
                const sel = sels[idx];
                const valEl = sel && sel.querySelector(
                  '.ant-select-selection-selected-value, .ant-select-selection__selected-value');
                const phEl = sel && sel.querySelector('.ant-select-selection__placeholder');
                return {
                  value: valEl ? String(valEl.textContent || '').trim() : '',
                  placeholderVisible: !!(phEl && (phEl as any).offsetParent !== null),
                  rows: document.querySelectorAll('.ant-table-row, .ant-table-tbody tr').length,
                };
              }, ctrl.idx).catch((e: any) => ({err: String(e).slice(0, 80)}));
              resetOk = !!afterReset && !afterReset.err
                && (!afterReset.value || afterReset.placeholderVisible
                    || clearVals.indexOf(String(afterReset.value)) >= 0);
              resetNote = JSON.stringify(afterReset).slice(0, 120);
            }
          }
          console.log('FILTER_TRACE ' + JSON.stringify({
            name: nm, option: check.option, sel_value_after: selValue,
            sample: sample && sample.ok ? {rows: sample.rows, col: sample.colIndex,
                                           dist, bad: badVals.slice(0, 5)} : sample,
            expect_in: expectIn, filter_ok: filterOk,
            reset: {configured: !!check.reset, ok: resetOk, note: resetNote},
          }));
          const distText = Object.keys(dist).map((k) => `${k}×${dist[k]}`).join('/');
          if (filterOk && resetOk) {
            filterStat.pass += 1;
            recAll(check, nm, 'pass',
                   `筛选「${check.option}」：第 1 页 ${sample.rows} 行目标列全部命中`
                   + `（${distText || '无数据行'}）`
                   + (check.reset ? `；重置后控件已清空（${resetNote}）` : '；未配置重置断言'));
          } else if (sample && sample.why === 'no_rows') {
            filterStat.unverified += 1;
            recAll(check, nm, 'unverified',
                   '筛选后第 1 页无可见行——可能数据为空也可能筛选失效，不判通过（现场见 FILTER_TRACE）');
          } else if (sample && sample.why === 'column_not_found') {
            filterStat.unverified += 1;
            recAll(check, nm, 'unverified',
                   `结果集断言列「${check.expect_column}」未在表头命中（表头 ${JSON.stringify((sample.heads || []).slice(0, 8))}）`);
          } else if (sample && sample.why) {
            filterStat.unverified += 1;
            recAll(check, nm, 'unverified', `结果集取样失败：${sample.why}——现场见 FILTER_TRACE`);
          } else if (!filterOk) {
            filterStat.unverified += 1;
            recAll(check, nm, 'unverified',
                   `筛选「${check.option}」后 ${sample.rows} 行中有 ${badVals.length} 行目标列取值不在期望集`
                   + ` ${JSON.stringify(expectIn)} 内（分布 ${distText}）——业务断言未通过，现场见 FILTER_TRACE`);
          } else {
            filterStat.unverified += 1;
            recAll(check, nm, 'unverified',
                   `筛选命中但重置未完成（${resetNote}）——重置通道未被验证，不判通过`);
          }
        } catch (error) {
          filterStat.unverified += 1;
          recAll(check, nm, 'unverified', `筛选检查异常：${String(error).slice(0, 200)}`);
        }
      }
      console.log('FILTER_PHASE ' + JSON.stringify(filterStat));
    }

    // ===== 「状态×操作矩阵」独立通道（rev173）：按状态定位行 → 行内操作集断言 →
    // 更多菜单枚举 → confirm 配置驱动的确认框验证（Modal+Popover 双采样，点取消还原）=====
    async function runActionMatrix(): Promise<void> {
      const page = appPageForNav();
      const stageMark = (st: string, extra: any = {}) => {
        try {
          console.log('MATRIX_STAGE ' + JSON.stringify({
            stage: st, url: String(page.url() || '').slice(0, 110), ...extra,
          }));
        } catch { /* 页面已销毁 */ }
      };
      const items: any[] = (FORM_PLAN.checks || []).filter((c: any) => c.type === 'action_matrix');
      mxStat.configured = items.length;
      for (const rej of (FORM_PLAN.action_matrix_rejects || []).slice(0, 20)) {
        console.log('[matrix-skip] ' + JSON.stringify(rej));
      }
      if (!items.length) return;
      const norm = (s: any) => String(s || '').replace(/\s+/g, ' ').trim();
      const pickRoot = async (): Promise<any> => {
        let best: any = null, bn = -1;
        const cands: any[] = [page, ...page.frames()];
        for (const f of cands) {
          try {
            const n = Number(await f.evaluate(
              () => document.querySelectorAll('.ant-table-row, .ant-table-tbody tr').length));
            if (n > bn) { bn = n; best = f; }
          } catch { /* 跨域 */ }
        }
        return best;
      };
      // 可见弹出层菜单读取（Dropdown 与 Popover 两种形态都采）：见循环内 readMenuItems
      for (const check of items) {
        const nm = String(check.name || ('matrix:' + (check.status_in || []).join('/')));
        mxStat.executed += 1;
        try {
          const url = String(check.list_url || '');
          if (!url) {
            mxStat.unverified += 1;
            recAll(check, nm, 'unverified',
                   '未配置列表页 URL（action_matrix_checks[].list_url 或 locators.list_url）');
            continue;
          }
          stageMark('goto_begin', {target: resolveListUrl(url, page).slice(0, 120)});
          await page.goto(resolveListUrl(url, page), {timeout: 30000, waitUntil: 'domcontentloaded'}).catch(() => undefined);
          stageMark('goto_done');
          await waitRows(page, 30000);
          const root: any = await pickRoot();
          if (!root) {
            mxStat.unverified += 1;
            recAll(check, nm, 'unverified', '列表页未渲染出表格（pickRoot 无候选）——现场见 MATRIX_STAGE');
            continue;
          }
          // 目标状态可能不在第 1 页（本 SUT 第 1 页全为「启用」，TR-791 证实待启用在
          // 筛选后才可见）：filter:true 时先状态下拉筛选——rev172 配方（选中后点搜索）。
          let filterNote: any = {configured: !!check.filter, ok: true};
          if (check.filter) {
            const phs: string[] = (Array.isArray(check.control_placeholder) && check.control_placeholder.length)
              ? check.control_placeholder.map((s: any) => String(s)) : ['请输入状态', '狀態', '状态'];
            const selIdx: number = await root.evaluate((src: string) => {
              const rx = new RegExp(src, 'i');
              const sels = Array.from(document.querySelectorAll('.ant-select'))
                .filter((s: any) => s.offsetParent !== null);
              return sels.findIndex((s: any) => {
                const ph = s.querySelector('.ant-select-selection__placeholder, .ant-select-selection-selected-value');
                return !!ph && rx.test(String(ph.textContent || ''));
              });
            }, phs.join('|')).catch(() => -1);
            if (selIdx < 0) {
              filterNote = {configured: true, ok: false, why: 'status_select_not_found'};
            } else {
              await root.locator('.ant-select').nth(selIdx).click({timeout: 8000}).catch(() => undefined);
              await page.waitForTimeout(1200);
              const dd = page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden)').last();
              const lis = dd.locator('li[role=option], .ant-select-dropdown-menu-item:visible');
              const n = await lis.count().catch(() => 0);
              const want = String(check.filter_option || (check.status_in || [])[0] || '');
              const itemTexts: string[] = [];
              for (let i = 0; i < n; i++) itemTexts.push(norm(await lis.nth(i).innerText().catch(() => '')));
              let picked = -1;
              // 先精确后包含：「启用」是「待启用」的子串，包含优先会选错（rev170 教训）
              for (const mode of ['exact', 'contains']) {
                for (let i = 0; i < itemTexts.length; i++) {
                  if (picked < 0
                      && (mode === 'exact' ? itemTexts[i] === want : itemTexts[i].indexOf(want) >= 0)) {
                    picked = i;
                  }
                }
                if (picked >= 0) break;
              }
              if (picked < 0) {
                filterNote = {configured: true, ok: false, why: 'option_not_found',
                              want, options: itemTexts.slice(0, 8)};
                await page.keyboard.press('Escape').catch(() => undefined);
              } else {
                await lis.nth(picked).click({timeout: 5000}).catch(() => undefined);
                await page.waitForTimeout(1200);
                // rev172：本 SUT 选中选项不自动查询——点「搜索」触发刷新
                const qbtn = root.locator('button')
                  .filter({hasText: /搜\s*索|查\s*詢|查\s*询|Search/i}).first();
                if (await qbtn.count().catch(() => 0)) {
                  await qbtn.click({timeout: 8000}).catch(() => undefined);
                }
                await page.waitForTimeout(2000);
                await waitRows(page, 20000);
                filterNote = {configured: true, ok: true, option: want, picked_text: itemTexts[picked]};
              }
            }
            stageMark('filter_done', filterNote);
          }
          // —— 行定位 + 操作集取样（数据行与克隆操作行双扫描，rev155 口径）——
          const snap: any = await root.evaluate((sp: any) => {
            const norm0 = (s: any) => String(s || '').replace(/\s+/g, ' ').trim();
            let dataRows: any[] = [], bestCols = 0;
            Array.prototype.forEach.call(document.querySelectorAll('.ant-table-tbody'), (tb: any) => {
              const rs = Array.prototype.filter.call(tb.children, (tr: any) =>
                tr.querySelector('td') && tr.offsetParent !== null);
              if (!rs.length) return;
              const nCols = rs[0].querySelectorAll('td').length;
              if (nCols > bestCols) { bestCols = nCols; dataRows = rs; }
            });
            if (!dataRows.length) return {ok: false, why: 'no_rows'};
            let heads: string[] = [];
            Array.prototype.forEach.call(document.querySelectorAll('.ant-table-thead'), (th: any) => {
              const hs = Array.prototype.map.call(th.querySelectorAll('th'), (e: any) => norm0(e.innerText));
              if (hs.length === bestCols) heads = hs;
            });
            if (!heads.length) {
              Array.prototype.forEach.call(document.querySelectorAll('.ant-table-thead'), (th: any) => {
                const hs = Array.prototype.map.call(th.querySelectorAll('th'), (e: any) => norm0(e.innerText));
                if (hs.length > heads.length) heads = hs;
              });
            }
            if (!heads.length) return {ok: false, why: 'no_header'};
            const ci = heads.indexOf(sp.column);
            if (ci < 0) return {ok: false, why: 'column_not_found', column: sp.column, heads};
            const rowWithOps = (row0: any) => {
              const out: any[] = [row0];
              const tb0: any = row0.parentElement;
              const ri = Array.prototype.indexOf.call(tb0.children, row0);
              Array.prototype.forEach.call(document.querySelectorAll('.ant-table-tbody'), (t2: any) => {
                const cand = t2.children[ri];
                if (cand && cand !== row0) out.push(cand);
              });
              return out;
            };
            const cells = dataRows.map((tr: any) => {
              const t = tr.querySelectorAll('td')[ci];
              return t ? norm0(t.innerText) : '';
            });
            let hit = -1, mode = '';
            cells.forEach((c: string, i: number) => {
              if (hit < 0 && sp.in.indexOf(c) >= 0) { hit = i; mode = 'exact'; }
            });
            if (hit < 0) {
              cells.forEach((c: string, i: number) => {
                if (hit < 0 && sp.in.some((w: string) => w && c.indexOf(w) >= 0)) { hit = i; mode = 'contains'; }
              });
            }
            if (hit < 0) {
              return {ok: false, why: 'no_row_matched', column: sp.column, want: sp.in,
                      cells: cells.slice(0, 12), rows: dataRows.length};
            }
            const scopes = rowWithOps(dataRows[hit]);
            // 操作标签只从 a/button 收集——状态单元格的 .ant-tag/.ant-badge 也是 span，
            // 混进来会让 forbid「启用」恒命中（状态文本本身就是「启用」）。
            const labels: string[] = [];
            for (const sc of scopes) {
              Array.prototype.forEach.call(sc.querySelectorAll('a,button'), (el: any) => {
                const t = norm0(el.innerText);
                if (t && t.length <= 12 && el.offsetParent !== null && labels.indexOf(t) < 0) labels.push(t);
              });
            }
            return {ok: true, rowIndex: hit, status: cells[hit], matchMode: mode,
                    labels, rows: dataRows.length,
                    row: norm0(dataRows[hit].innerText).slice(0, 110)};
          }, {column: String(check.status_column || '状态'),
              in: (check.status_in || []).map((s: any) => String(s))})
            .catch((e: any) => ({ok: false, why: 'eval_error', err: String(e).slice(0, 100)}));
          stageMark('row_scan', {result: snap});
          if (!snap || !snap.ok) {
            mxStat.unverified += 1;
            const fTxt = (filterNote && filterNote.configured)
              ? (filterNote.ok ? `；已先按「${filterNote.option}」筛选` : `；状态筛选失败：${filterNote.why || ''}`)
              : '';
            recAll(check, nm, 'unverified',
                   `未找到「${(check.status_in || []).join('/')}」状态的可见行（${(snap && snap.why) || 'no_root'}）${fTxt}`
                   + '——数据侧可能缺该状态记录，不判通过也不判缺陷（现场见 MATRIX_STAGE）');
            continue;
          }
          // —— 操作集断言（expect / forbid）——
          const missingRow: string[] = (check.expect_row || [])
            .filter((w: string) => snap.labels.indexOf(w) < 0);
          const unexpected: string[] = (check.forbid_row || [])
            .filter((w: string) => snap.labels.indexOf(w) >= 0);
          // —— 「更多」菜单枚举（配置了才做）——
          const clickMore = async (): Promise<any> => root.evaluate((rowIdx: number) => {
            const norm1 = (s: any) => String(s || '').replace(/\s+/g, ' ').trim();
            const bodies = Array.from(document.querySelectorAll('.ant-table-tbody'))
              .filter((b: any) => b.offsetParent !== null)
              .map((b: any) => Array.from(b.querySelectorAll('tr')).filter((tr: any) => tr.offsetParent !== null));
            const dataBody = bodies.slice().sort((a: any, b: any) =>
              ((b[0] && b[0].querySelectorAll('td').length) || 0) -
              ((a[0] && a[0].querySelectorAll('td').length) || 0))[0] || [];
            const opBodies = bodies.filter((b: any) => b !== dataBody);
            const scopes = [dataBody[rowIdx]].concat(opBodies.map((ob: any) => ob[rowIdx]).filter(Boolean));
            for (const sc of scopes) {
              const more = Array.from(sc.querySelectorAll('a,button,span'))
                .find((el: any) => norm1(el.innerText) === '更多' && el.offsetParent);
              if (more) { (more as any).click(); return {ok: true}; }
            }
            return {ok: false, why: 'more_not_found'};
          }, snap.rowIndex).catch((e: any) => ({ok: false, why: 'eval_error', err: String(e).slice(0, 80)}));
          const readMenuItems = async (): Promise<string[]> => root.evaluate(() => {
            const menus = Array.from(document.querySelectorAll(
              '.ant-dropdown:not(.ant-dropdown-hidden), .ant-popover:not(.ant-popover-hidden)'));
            const texts: string[] = [];
            for (const m of menus) {
              m.querySelectorAll('li, [class*=item], a, button').forEach((it: any) => {
                const t = String(it.innerText || '').trim().replace(/\s+/g, ' ');
                if (t && t.length <= 12 && texts.indexOf(t) < 0) texts.push(t);
              });
            }
            return texts;
          }).catch(() => [] as string[]);
          let menuItems: string[] = [];
          let menuNote = 'not_configured';
          const missingMenu: string[] = [];
          if ((check.expect_menu || []).length) {
            if (snap.labels.indexOf('更多') < 0) {
              menuNote = 'row_has_no_more';
              missingMenu.push(...check.expect_menu);
            } else {
              const opened = await clickMore();
              await page.waitForTimeout(900);
              if (opened && opened.ok) {
                menuItems = await readMenuItems();
                for (const w of check.expect_menu) {
                  if (menuItems.indexOf(w) < 0) missingMenu.push(w);
                }
                menuNote = 'opened:' + menuItems.join('/');
              } else {
                menuNote = 'more_click_failed:' + ((opened && opened.why) || '');
                missingMenu.push(...check.expect_menu);
              }
              await page.keyboard.press('Escape').catch(() => undefined);
              await page.waitForTimeout(600);
            }
          }
          // —— confirm：点目标按钮 → 必须出确认框（Modal+Popover 双采样）→ 点取消还原 ——
          let confirmNote: any = {configured: false};
          if (check.confirm && check.confirm.label) {
            const clabel = String(check.confirm.label);
            const viaMenu = String(check.confirm.via || 'row') === 'menu';
            let clicked: any = {ok: false, why: 'skipped'};
            if (viaMenu) {
              // TR-792 复盘：枚举阶段 Escape 可能没真正关掉菜单（焦点不在菜单上），
              // 此时再点「更多」会把它 toggle **关闭** ⇒ items:0。先探测已开菜单，
              // 没有才点「更多」，不再盲目 toggle。
              let menusNow: string[] = await readMenuItems();
              if (!menusNow.length) {
                const opened = await clickMore();
                await page.waitForTimeout(900);
                if (!(opened && opened.ok)) {
                  clicked = {ok: false, why: 'more_not_found'};
                  menusNow = null;
                }
              }
              if (menusNow) {
                // TR-793 复盘（rev175）：菜单项处理器对**合成 click**（evaluate 里
                // el.click()）无响应——记录仍在、无任何确认框（TR-793 启用/待启用
                // clicked:ok 却 dialog_found:false；探针 5s 全 DOM 转储为空）；
                // Playwright 真实输入管线点击才触发 Popconfirm（探针2 实证）。优先
                // locator 真实点击，失败回退合成 click。
                const esc = clabel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                const itemLoc = page.locator(
                  '.ant-dropdown:not(.ant-dropdown-hidden) li, .ant-popover:not(.ant-popover-hidden) li')
                  .filter({hasText: new RegExp('^\\s*' + esc + '\\s*$')}).first();
                let realClicked = false;
                if (await itemLoc.count().catch(() => 0)) {
                  await itemLoc.click({timeout: 5000}).catch(() => undefined);
                  realClicked = true;
                }
                clicked = realClicked
                  ? {ok: true, how: 'locator'}
                  : await root.evaluate((lb: string) => {
                  const norm1 = (s: any) => String(s || '').replace(/\s+/g, ' ').trim();
                  const menus = Array.from(document.querySelectorAll(
                    '.ant-dropdown:not(.ant-dropdown-hidden), .ant-popover:not(.ant-popover-hidden)'));
                  for (const m of menus) {
                    const el = Array.from(m.querySelectorAll('li, a, button, [class*=item]'))
                      .find((e: any) => norm1(e.innerText) === lb && (e as any).offsetParent);
                    if (el) { (el as any).click(); return {ok: true}; }
                  }
                  return {ok: false, why: 'menu_item_not_found', items: menus.length};
                }, clabel).catch((e: any) => ({ok: false, why: 'eval_error', err: String(e).slice(0, 80)}));
              }
            } else {
              clicked = await root.evaluate(({rowIdx, lb}: any) => {
                const norm1 = (s: any) => String(s || '').replace(/\s+/g, ' ').trim();
                const bodies = Array.from(document.querySelectorAll('.ant-table-tbody'))
                  .filter((b: any) => b.offsetParent !== null)
                  .map((b: any) => Array.from(b.querySelectorAll('tr')).filter((tr: any) => tr.offsetParent !== null));
                const dataBody = bodies.slice().sort((a: any, b: any) =>
                  ((b[0] && b[0].querySelectorAll('td').length) || 0) -
                  ((a[0] && a[0].querySelectorAll('td').length) || 0))[0] || [];
                const opBodies = bodies.filter((b: any) => b !== dataBody);
                const scopes = [dataBody[rowIdx]].concat(opBodies.map((ob: any) => ob[rowIdx]).filter(Boolean));
                for (const sc of scopes) {
                  const el = Array.from(sc.querySelectorAll('a,button,span'))
                    .find((e: any) => norm1(e.innerText) === lb && e.offsetParent);
                  if (el) { (el as any).click(); return {ok: true}; }
                }
                return {ok: false, why: 'action_not_found'};
              }, {rowIdx: snap.rowIndex, lb: clabel})
                .catch((e: any) => ({ok: false, why: 'eval_error', err: String(e).slice(0, 80)}));
            }
            await page.waitForTimeout(1800);
            // antd 确认框有 Modal 与 Popconfirm 两种形态，必须同时采样（2026-10-20 实证）
            const dialog: any = await root.evaluate(() => {
              const vis = (el: any) => !!el && el.offsetParent !== null;
              const modal = Array.from(document.querySelectorAll('.ant-modal-confirm'))
                .find((m: any) => vis(m.closest('.ant-modal-wrap') || m));
              const pop = Array.from(document.querySelectorAll('.ant-popover'))
                .find((p: any) => vis(p) && !/ant-popover-hidden/.test(String(p.className)));
              const mEl: any = modal || null;
              const pEl: any = pop || null;
              if (!mEl && !pEl) return {found: false};
              const scope: any = mEl || pEl;
              const btns = Array.from(scope.querySelectorAll('button'))
                .map((b: any) => ({el: b, t: String(b.innerText || '').trim()}))
                .filter((b: any) => b.t);
              return {found: true, kind: mEl ? 'modal' : 'popover',
                      text: String(scope.innerText || '').replace(/\s+/g, ' ').slice(0, 120),
                      labels: btns.map((b: any) => b.t)};
            }).catch((e: any) => ({found: false, err: String(e).slice(0, 80)}));
            let canceled = false;
            if (dialog && dialog.found) {
              const cancelRes: any = await root.evaluate(() => {
                const norm1 = (s: any) => String(s || '').replace(/\s+/g, ' ').trim();
                const scope: any = document.querySelector(
                  '.ant-modal-wrap:not([style*="display: none"]) .ant-modal-confirm')
                  || document.querySelector('.ant-modal-confirm')
                  || Array.from(document.querySelectorAll('.ant-popover'))
                    .find((p: any) => !/ant-popover-hidden/.test(String(p.className)));
                if (!scope) return {ok: false, why: 'scope_gone'};
                const btns = Array.from(scope.querySelectorAll('button'));
                // antd v3 Popconfirm：.ant-popover-buttons 里第一个是取消；Modal/气泡按文本
                const pbtn = scope.querySelector('.ant-popover-buttons button');
                const byText = btns.find((b: any) => /取\s*消|Cancel/i.test(norm1(b.innerText)));
                const target: any = pbtn || byText || null;
                if (!target) return {ok: false, why: 'cancel_not_found', labels: btns.map((b: any) => norm1(b.innerText))};
                (target as any).click();
                return {ok: true};
              }).catch((e: any) => ({ok: false, why: 'eval_error', err: String(e).slice(0, 80)}));
              await page.waitForTimeout(1200);
              const still: any = await root.evaluate(() => {
                const vis = (el: any) => !!el && el.offsetParent !== null;
                const modal = Array.from(document.querySelectorAll('.ant-modal-confirm'))
                  .find((m: any) => vis(m.closest('.ant-modal-wrap') || m));
                const pop = Array.from(document.querySelectorAll('.ant-popover'))
                  .find((p: any) => vis(p) && !/ant-popover-hidden/.test(String(p.className)));
                return {modal: !!modal, pop: !!pop};
              }).catch(() => ({modal: false, pop: false}));
              if (cancelRes && cancelRes.ok && !still.modal && !still.pop) {
                canceled = true;
              } else {
                // 取消失败必须兜底收场，绝不让确认框挂着（下一检查可能误回车）
                await page.keyboard.press('Escape').catch(() => undefined);
                await page.waitForTimeout(600);
              }
              confirmNote = {configured: true, label: clabel, via: viaMenu ? 'menu' : 'row',
                             kind: dialog.kind, canceled,
                             dialog_text: dialog.text, cancel: cancelRes};
            } else {
              confirmNote = {configured: true, label: clabel, via: viaMenu ? 'menu' : 'row',
                             clicked: clicked, dialog_found: false};
            }
            stageMark('confirm_done', confirmNote);
          }
          // —— 汇总判定 ——
          console.log('MATRIX_TRACE ' + JSON.stringify({
            name: nm, status: snap.status, labels: snap.labels, menu: menuNote,
            missing_row: missingRow, unexpected, missing_menu: missingMenu, confirm: confirmNote,
          }));
          const problems: string[] = [];
          if (missingRow.length) problems.push(`行内缺少期望操作：${missingRow.join('/')}`);
          if (unexpected.length) problems.push(`行内出现禁止操作：${unexpected.join('/')}`);
          if (missingMenu.length) {
            problems.push(menuNote === 'row_has_no_more'
              ? `该状态行没有「更多」菜单，无法核对菜单项：${missingMenu.join('/')}`
              : `「更多」菜单缺少项：${missingMenu.join('/')}（实际 ${menuNote}）`);
          }
          if (confirmNote && confirmNote.configured) {
            if (confirmNote.dialog_found === false) {
              problems.push(`点击了「${confirmNote.label}」但确认框未出现`
                + `（antd Modal 与 Popover 双采样均无，点击结果 ${(confirmNote.clicked || {}).why || 'ok'}）`
                + '——误删保护断言未通过');
            } else if (!confirmNote.canceled) {
              problems.push(`确认框出现但未能以「取消」还原（已 Escape 兜底）：`
                + `${JSON.stringify(confirmNote.cancel || {}).slice(0, 120)}——需人工复核该行是否被误操作`);
            }
          }
          if (!problems.length) {
            mxStat.pass += 1;
            const cTxt = (confirmNote && confirmNote.configured)
              ? `；确认框验证：点「${confirmNote.label}」弹出${confirmNote.kind === 'modal' ? 'Modal' : '气泡'}确认并已取消还原`
              : '';
            recAll(check, nm, 'pass',
                   `状态「${snap.status}」行操作集断言通过：${snap.labels.join('/') || '（无 a/button 操作）'}`
                   + (menuItems.length ? `；「更多」菜单含 ${menuItems.join('/')}` : '')
                   + cTxt);
          } else {
            mxStat.unverified += 1;
            recAll(check, nm, 'unverified',
                   `状态「${snap.status}」操作矩阵断言未通过——${problems.join('；')}`
                   + `（实际行内操作 ${snap.labels.join('/') || '无'}）现场见 MATRIX_STAGE`);
          }
        } catch (error) {
          mxStat.unverified += 1;
          recAll(check, nm, 'unverified', `状态×操作矩阵检查异常：${String(error).slice(0, 200)}`);
        }
      }
      console.log('MATRIX_PHASE ' + JSON.stringify(mxStat));
    }

    // ===== 「导出」独立通道（rev148）：更多菜单 → 导出明细弹窗 → 空时间段校验 →
    // 选时间段 → 确认 → 导出中心轮询 → 下载落盘 → 列名出现性校验 =====
    // 交互配方来自人工探针 xy_p2/p8（实测可用）；判定口径见 parse_export_checks 注释。
    async function runExportChecks(): Promise<void> {
      const page = appPageForNav();   // rev151：导出阶段同样作用于应用标签页
      const items: any[] = (FORM_PLAN.checks || []).filter((c: any) => c.type === 'export_check');
      exportStat.configured = items.length;
      for (const rej of (FORM_PLAN.export_check_rejects || []).slice(0, 20)) {
        console.log('[export-skip] ' + JSON.stringify(rej));
      }
      if (!items.length) return;
      // Node 内建模块（zip 解压 / 落盘）；拿不到就如实降级，不冒充通过
      const __req = (m: string): any => { try { return require(m); } catch { return null; } };
      const fsMod: any = __req('fs');
      const zlibMod: any = __req('zlib');
      const pathMod: any = __req('path');
      // 极简 zip 文本抽取：xlsx = zip(sharedStrings.xml 等 deflate 成员)。
      // 局限：本地文件头无尺寸（流式 data descriptor）时停止解析 → 列校验如实 unverified。
      // rev159（TR-772 实测）：本 SUT 导出的 xlsx 把中文写成 **HTML 数字实体**
      // （``&#22635;&#20889;…`` = 填写…），直接 indexOf 中文永远不命中（9/9 列全 missing）。
      // 校验前统一解码实体（十进制 / 十六进制 / 五个命名字符），再比对列名。
      const decodeEntities = (s: string): string => String(s)
        .replace(/&#x([0-9a-fA-F]+);/g, (_m: string, h: string) =>
          String.fromCodePoint(parseInt(h, 16)))
        .replace(/&#(\d+);/g, (_m: string, d: string) => String.fromCodePoint(parseInt(d, 10)))
        .replace(/&lt;/g, '<').replace(/&gt;/g, '>')
        .replace(/&quot;/g, '"').replace(/&apos;/g, "'")
        .replace(/&amp;/g, '&');
      // rev161（TR-773 实测：列名 9/9 仍 missing，text 长度 0）：改为**读中央目录**。
      // 旧实现顺序扫 local file header，遇 ``csize=0`` 直接 ``break``；而本 SUT 导出的
      // xlsx **每个成员都是 data descriptor**（general flag 0x0808，local header 尺寸为 0）
      // ⇒ 第一个成员就 break，抽出文本长度为 0，任何列名都不可能命中。
      // 中央目录里有真实 ``csize`` 与 local header offset，据此取数据才可靠。
      const zipText = (buf: any): string => {
        try {
          const parts: string[] = [];
          let eocd = -1;
          for (let i = buf.length - 22; i >= Math.max(0, buf.length - 65557); i--) {
            if (buf.readUInt32LE(i) === 0x06054b50) { eocd = i; break; }
          }
          if (eocd < 0) return '';
          const count = buf.readUInt16LE(eocd + 10);
          let off = buf.readUInt32LE(eocd + 16);
          for (let k = 0; k < count; k++) {
            if (off + 46 > buf.length || buf.readUInt32LE(off) !== 0x02014b50) break;
            const method = buf.readUInt16LE(off + 10);
            const csize = buf.readUInt32LE(off + 20);
            const nameLen = buf.readUInt16LE(off + 28);
            const extraLen = buf.readUInt16LE(off + 30);
            const commentLen = buf.readUInt16LE(off + 32);
            const localOff = buf.readUInt32LE(off + 42);
            // local header 的 name/extra 长度可能与中央目录不同，dataStart 必须按 local 取
            const lNameLen = buf.readUInt16LE(localOff + 26);
            const lExtraLen = buf.readUInt16LE(localOff + 28);
            const dataStart = localOff + 30 + lNameLen + lExtraLen;
            if (csize && dataStart + csize <= buf.length) {
              const data = buf.slice(dataStart, dataStart + csize);
              try {
                if (method === 0) parts.push(data.toString('utf8'));
                else if (method === 8 && zlibMod) {
                  parts.push(zlibMod.inflateRawSync(data).toString('utf8'));
                }
              } catch { /* 单成员失败不影响其它 */ }
            }
            off += 46 + nameLen + extraLen + commentLen;
          }
          return parts.join('\n');
        } catch { return ''; }
      };
      const pickRoot = async (): Promise<any> => {
        let best: any = null, bn = -1;
        const cands: any[] = [page, ...page.frames()];
        for (const f of cands) {
          try {
            const n = Number(await f.evaluate(
              () => document.querySelectorAll('.ant-table-row, .ant-table-tbody tr').length));
            if (n > bn) { bn = n; best = f; }
          } catch { /* 跨域 */ }
        }
        return best;
      };
      const toastOf = async (): Promise<string> => String(await page.evaluate(
        () => Array.from(document.querySelectorAll('.ant-message span, .ant-notification-notice'))
          .map((e: any) => String(e.innerText || '').trim()).filter(Boolean).join(' | ')
      ).catch(() => ''));
      const modalJs = () => {
        const m: any = document.querySelector(
          '.ant-modal-wrap:not([style*="display: none"]) .ant-modal-content')
          || document.querySelector('.ant-modal-content');
        if (!m) return null;
        return {
          present: true,
          text: String(m.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 200),
          pickers: m.querySelectorAll('.ant-calendar-picker, .ant-picker').length,
          btns: Array.from(m.querySelectorAll('button'))
            .filter((b: any) => b.offsetParent !== null)
            .map((b: any) => String(b.innerText || '').trim()).filter(Boolean),
        };
      };
      for (const check of items) {
        const nm = String(check.name || ('export:' + check.label));
        const t0 = Date.now();
        exportStat.executed += 1;
        const problems: string[] = [];
        try {
          const url = String(check.list_url || '');
          if (!url) {
            exportStat.unverified += 1;
            recAll(check, nm, 'unverified',
                   '未配置列表页 URL（export_checks[].list_url 或 locators.list_url）');
            continue;
          }
          await page.goto(resolveListUrl(url, page), {timeout: 30000, waitUntil: 'domcontentloaded'}).catch(() => undefined);
          const waitedRows = await waitRows(page, 30000);
          console.log('EXPORT_STAGE ' + JSON.stringify({stage: 'rows_present', rows: waitedRows,
            url: String(page.url() || '').slice(0, 100)}));
          const root: any = await pickRoot();
          // step1：打开入口（可选先按 value 检索定位行；缺省第一行）
          const val = String(check.value || '');
          const label = String(check.label || '导出明细');
          const menu = String(check.menu || '');
          // rev158（TR-771 实测 rows:2 → row_not_found 根因）：列表页会**保留上一阶段的搜索
          // 条件**——goto 与当前完全相同的 hash 是 no-op，SPA 不重新加载，表格里只剩上次
          // 搜索命中的那几行（上一阶段搜的是别的 value）。因此导出行定位前必须**先按本次
          // value 重新检索**，检索后重新 pickRoot（表格重渲染会换 frame/节点）。
          let rootUsed: any = root;
          if (val && root) {
            const phs2: string[] = (() => {
              const sp: any = (FORM_PLAN.locators as any).search_placeholder;
              return Array.isArray(sp) ? sp.map((s: any) => String(s))
                : (sp ? [String(sp)] : []);
            })();
            const filled2: any = await root.evaluate(({src, value}: any) => {
              const phs = String(src).split('|').filter(Boolean);
              const inputs = Array.from(document.querySelectorAll('input'))
                .filter((e: any) => e.offsetParent !== null);
              const t: any = inputs.find((i: any) => phs.some(
                (p: string) => String(i.placeholder || '').indexOf(p) >= 0))
                || document.querySelector('#title')
                || (inputs.length === 1 ? inputs[0] : null);
              if (!t) return {ok: false, why: 'no_search_input'};
              t.focus();
              const setter: any = Object.getOwnPropertyDescriptor(
                HTMLInputElement.prototype, 'value');
              if (setter && setter.set) setter.set.call(t, value);
              else t.value = value;
              t.dispatchEvent(new Event('input', {bubbles: true}));
              t.dispatchEvent(new Event('change', {bubbles: true}));
              return {ok: true, ph: String(t.placeholder || '')};
            }, {src: phs2.join('|'), value: val})
              .catch((e: any) => ({ok: false, err: String(e).slice(0, 80)}));
            if (filled2 && filled2.ok) {
              const btn2 = page.locator('button')
                .filter({hasText: /搜\s*索|查\s*詢|查\s*询|Search/i}).first();
              const clicked2 = !!(await btn2.count().catch(() => 0));
              if (clicked2) await btn2.click({timeout: 8000}).catch(() => undefined);
              await page.waitForTimeout(1500);
              const n2 = await waitRows(page, 20000);
              console.log('EXPORT_STAGE ' + JSON.stringify({
                stage: 'searched', value: val, filled: filled2, btn: clicked2, rows: n2}));
              rootUsed = await pickRoot();
            } else {
              console.log('EXPORT_STAGE ' + JSON.stringify({stage: 'search_skipped', filled: filled2}));
            }
          }
          let opened: any = rootUsed ? await rootUsed.evaluate(({value: vv, menu: mm, label: tt}: any) => {
            const norm = (s: any) => String(s || '').replace(/\s+/g, ' ').trim();
            const rows = Array.from(document.querySelectorAll('.ant-table-tbody tr, .ant-table-row'))
              .filter((r: any) => r.offsetParent !== null);
            // rev155：操作列在右侧固定列的**克隆 tbody** 里（整页 10 数据行 + 10 操作行，
            // 数据行与操作行并不相邻）。按「同 tbody 内行序号对齐」取出对应操作行。
            const rowWithOps = (row0: any) => {
              const out: any[] = [row0];
              const tb0: any = row0.parentElement;
              const ri = Array.prototype.indexOf.call(tb0.children, row0);
              Array.prototype.forEach.call(document.querySelectorAll('.ant-table-tbody'),
                (t2: any) => {
                  const cand = t2.children[ri];
                  if (cand && cand !== row0) out.push(cand);
                });
              return out;
            };
            let scopes: any[] = [];
            if (vv) {
              const idx = rows.findIndex((r: any) => norm(r.innerText).indexOf(vv) >= 0);
              if (idx < 0) return {ok: false, why: 'row_not_found', rows: rows.length};
              scopes = rowWithOps(rows[idx]);
            } else {
              const first = rows.find((r: any) => r.querySelector('a,button,span'));
              if (!first) return {ok: false, why: 'no_rows', rows: rows.length};
              scopes = rowWithOps(first);
            }
            if (mm) {
              for (const sc of scopes) {
                const more = Array.from(sc.querySelectorAll('a,button,span'))
                  .filter((e: any) => e.offsetParent !== null)
                  .find((a: any) => norm(a.innerText) === mm);
                if (more) { (more as any).click(); return {ok: true, stage: 'menu_opened'}; }
              }
              // rev156：本 SUT 操作项随状态变化——有的行「导出明细」直挂、无「更多」。
              // 菜单找不到时退回**直点标签**（多一条交互路径，不改判定口径）。
              for (const sc of scopes) {
                const direct = Array.from(sc.querySelectorAll('a,button,span'))
                  .filter((e: any) => e.offsetParent !== null)
                  .find((a: any) => norm(a.innerText) === tt);
                if (direct) { (direct as any).click(); return {ok: true, stage: 'clicked_direct'}; }
              }
              return {ok: false, why: 'menu_not_found'};
            }
            for (const sc of scopes) {
              const hit = Array.from(sc.querySelectorAll('a,button,span'))
                .filter((e: any) => e.offsetParent !== null)
                .find((a: any) => norm(a.innerText) === tt);
              if (hit) { (hit as any).click(); return {ok: true, stage: 'clicked'}; }
            }
            return {ok: false, why: 'entry_not_found'};
          }, {value: val, menu, label}) : {ok: false, why: 'no_root'};
          if (opened.ok && opened.stage === 'menu_opened') {
            await page.waitForTimeout(1200);
            opened = await clickDropdownItem(page, label);
          }
          if (!opened.ok) problems.push(`入口未打开(${opened.why})`);
          await page.waitForTimeout(2500);
          // step2：弹窗结构
          const dlg: any = await page.evaluate(modalJs).catch(() => null);
          // step3：空时间段校验（先点确认，期待校验 toast）
          let validation: any = {checked: false, ok: false, toast: ''};
          if (dlg && dlg.present && check.validate_empty_time) {
            await page.evaluate((rx: string) => {
              const m: any = document.querySelector('.ant-modal-content');
              if (!m) return null;
              const re = new RegExp(rx);
              const btns = Array.from(m.querySelectorAll('button'))
                .filter((b: any) => b.offsetParent !== null);
              const hit = btns.find((b: any) => re.test(String(b.innerText || '').trim()));
              if (hit) { (hit as any).click(); return String(hit.innerText || '').trim(); }
              return null;
            }, String(check.confirm_label || '导出|确定'));
            await page.waitForTimeout(2000);
            validation.toast = await toastOf();
            validation.ok = new RegExp(String(check.validation_toast || '请选择')).test(validation.toast);
            validation.checked = true;
            if (!validation.ok) problems.push('空时间段未拦截');
          }
          // step4：选时间段（antd v3 RangePicker）
          // 实测（2026-10-21 10:29 / 10:31 两轮探针）：
          //   ① 面板是**两个月并排**（.ant-calendar-range-part ≥2，各 42 天），
          //      left 面板含上月尾 + 当月，right 含当月尾 + 下月；
          //   ② RangePicker **必须点两次**——只点一次输入框仍为空，点确定会被前端
          //      校验拦截，导出请求根本不发出（此时仍判 datesPicked=true 就是假自证）；
          //   ③ 2026-10-21 直接取全量 tds 末尾 = 12-04~12-06（未来区间）⇒ 导出空表。
          // 因此：取 left 面板内「当月」的首日与末日（排除 last-month/next-month/disabled），
          // 点两次后**回读弹窗输入框**，两个值都非空才算选上。
          let datesPicked = false;
          let pickNote = '';
          if (dlg && dlg.present && Number(dlg.pickers) > 0 && check.pick_dates) {
            const clickDayInPanel = (which: string): any => page.evaluate((w: string) => {
              const cellOk = (e: any) => {
                if (e.offsetParent === null) return false;
                const td: any = e.closest('td') || e.parentElement;
                const cls = String((td && td.className) || '') + ' ' + String(e.className || '');
                if (/last-month|next-month|disabled-cell/.test(cls)) return false;
                const v = Number(String(e.innerText || '').trim());
                return Number.isFinite(v) && v >= 1 && v <= 31;
              };
              const parts = Array.from(document.querySelectorAll('.ant-calendar-range-part'));
              const scope: any = parts.length ? parts[0] : document;
              const ds = Array.from(scope.querySelectorAll('.ant-calendar-date')).filter(cellOk);
              if (!ds.length) return {ok: false, n: 0};
              const t: any = w === 'last' ? ds[ds.length - 1] : ds[0];
              (t as any).click();
              return {ok: true, val: String(t.innerText || '').trim(), n: ds.length};
            }, which);
            const readRange = (): any => page.evaluate(() => Array.from(
              document.querySelectorAll('.ant-modal-content input'))
              .filter((e: any) => e.offsetParent !== null)
              .map((e: any) => String(e.value || '')).filter((v: string) => v.length > 0));
            try {
              const picker = page.locator('.ant-modal-content .ant-calendar-picker, '
                + '.ant-modal-content .ant-picker').first();
              if (await picker.count().catch(() => 0)) {
                await picker.click({timeout: 8000});
                await page.waitForTimeout(1200);
                const d1: any = await clickDayInPanel('first');
                await page.waitForTimeout(900);
                const d2: any = await clickDayInPanel('last');
                await page.waitForTimeout(1200);
                const range: string[] = await readRange().catch(() => []);
                datesPicked = range.length >= 2;
                pickNote = JSON.stringify({first: d1 && d1.val, last: d2 && d2.val,
                                           n: d1 && d1.n, range});
              }
            } catch { datesPicked = false; }
            if (!datesPicked) {
              problems.push(`时间段未选上（输入框无值，导出请求不会被发出）${pickNote}`);
            }
          }
          // (step6a 前置) 下载监听必须在点确认**之前**挂上——事件注册晚了会错过 download
          const dl: any = {clicked: false, saved: '', bytes: 0, suggested: '', via: ''};
          const columns: any = {checked: false, found: [], missing: []};
          const dlPromise: any = check.download
            ? page.waitForEvent('download', {
              timeout: Number(check.max_wait_download_ms) || 30000,
            }).catch(() => null) : null;
          // step5：确认导出
          // rev157：记下点击确认的时刻——导出中心里**历史行**同样满足「已完成 + 任务名前缀」，
          // 不卡时间就会把「本次导出没真发出」判成通过（假阳性）。
          const confirmAt = Date.now();
          const confirmClicked: any = dlg && dlg.present
            ? await page.evaluate((rx: string) => {
              const m: any = document.querySelector('.ant-modal-content');
              if (!m) return null;
              const re = new RegExp(rx);
              const btns = Array.from(m.querySelectorAll('button'))
                .filter((b: any) => b.offsetParent !== null);
              const hit = btns.find((b: any) => re.test(String(b.innerText || '').trim()));
              if (hit) { (hit as any).click(); return String(hit.innerText || '').trim(); }
              return null;
            }, String(check.confirm_label || '导出|确定')) : null;
          if (!confirmClicked && dlg && dlg.present) problems.push('确认未点击');
          // 确认后的 toast（成功提示或校验提示，供 EXPORT_TRACE 取证）
          const toastAfter: string = await toastOf();
          const saveAndInspect = async (d: any): Promise<void> => {
            dl.suggested = String(d.suggestedFilename() || '');
            // rev157：filename_regex 只用于**下载文件名**取证，不参与导出中心轮询。
            dl.name_matched = new RegExp(String(check.filename_regex || '.')).test(dl.suggested);
            if (!fsMod) { problems.push('运行环境无 fs 模块，未落盘'); return; }
            try {
              const dir = 'downloads';
              try { fsMod.mkdirSync(dir, {recursive: true}); } catch { /* 已存在 */ }
              const p = pathMod
                ? pathMod.join(dir, `export_${Date.now()}_${dl.suggested}`)
                : `${dir}/export_${Date.now()}_${dl.suggested}`;
              await d.saveAs(p);
              dl.saved = p;
              const buf = fsMod.readFileSync(p);
              dl.bytes = buf.length;
              const want: string[] = Array.isArray(check.expect_columns)
                ? check.expect_columns.map((c: any) => String(c)) : [];
              if (want.length) {
                // rev159：解压文本 + 原始 latin1 + 原始 utf8 三路拼接后统一解码实体，
                // 兼顾 deflate/存储两种写入方式与「中文写成数字实体」的导出实现。
                const text = decodeEntities(zipText(buf) + buf.toString('latin1')
                  + buf.toString('utf8'));
                for (const c of want) {
                  (text.indexOf(c) >= 0 ? columns.found : columns.missing).push(c);
                }
                columns.checked = true;
                if (columns.missing.length) {
                  problems.push(`列名缺失:${columns.missing.join(',')}`);
                }
              }
            } catch (e: any) {
              dl.error = String(e).slice(0, 120);
              if (check.expect_columns && check.expect_columns.length) {
                problems.push('文件解析失败，列名校验未执行');
              }
            }
          };
          const directDl: any = await dlPromise;
          if (directDl) {
            dl.via = 'direct';
            await saveAndInspect(directDl);
          }
          // (step6b) 未直下 → 导出中心轮询（页面内路由或新标签页都可能）
          const ec = check.export_center || {};
          let center = {found: false, row: '', waited_ms: 0, page_url: ''};
          // rev153/154：只有"确实还有下一步可走"时才轮询导出中心——
          // 入口没打开（row_not_found）、或弹窗没出现且没配导出中心 URL 时，
          // 轮询只会白等 timeout 并产出"导出中心无匹配记录"的伪结论（TR-765/766 实测）。
          const centerWorth = !!String(ec.url || '') || !!(dlg && dlg.present);
          if (!directDl && !centerWorth) {
            problems.push('未出现导出弹窗且未配置导出中心 URL——下载与列名校验无法继续');
          }
          if (!directDl && opened.ok && centerWorth) {
            if (String(ec.url || '')) {
              await page.goto(resolveListUrl(String(ec.url), page), {timeout: 30000, waitUntil: 'domcontentloaded'})
                .catch(() => undefined);
            } else {
              // 无独立 URL：点「导出」导航进入导出中心
              await page.evaluate(() => {
                const els = Array.from(document.querySelectorAll('a,span,div,li'))
                  .filter((e: any) => e.offsetParent !== null
                    && String(e.innerText || '').trim() === '导出');
                if (els.length) (els[els.length - 1] as any).click();
              });
            }
            await page.waitForTimeout(4000);
            const pages = page.context().pages();
            const centerPage: any = pages[pages.length - 1] || page;
            center.page_url = String(centerPage.url() || '').slice(0, 90);
            const rxStatus = new RegExp(String(ec.status_text || '已完成'));
            // rev157：任务名称 ≠ 下载文件名，分开用两个正则（详见 EXPORT_CHECK_DEFAULTS 注释）。
            const rxName = new RegExp(String(check.center_name_regex || '.'));
            const tolerance = Number(check.fresh_tolerance_s || 180) * 1000;
            // 行内「创建时间」形如 2026-10-21 10:31:24；操作行（只有「下载」）解析失败返回 0。
            const rowTimeMs = (s: string): number => {
              const m = String(s).match(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/);
              if (!m) return 0;
              return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]),
                              Number(m[4]), Number(m[5]), Number(m[6])).getTime();
            };
            const deadline = Date.now() + Number(ec.timeout_ms || 180000);
            center.fresh_after = confirmAt;
            while (Date.now() < deadline) {
              const rows: string[] = await centerPage.evaluate(
                () => Array.from(document.querySelectorAll('.ant-table-tbody tr'))
                  .filter((r: any) => r.offsetParent !== null)
                  .map((r: any) => String(r.innerText || '').replace(/\s+/g, ' ').trim())
                  .slice(0, 10)).catch(() => []);
              const named = rows.filter((r: string) => rxStatus.test(r) && rxName.test(r));
              const hit = named.find((r: string) => rowTimeMs(r) >= confirmAt - tolerance);
              center.candidates = named.length;
              center.sample = rows.slice(0, 3);
              center.waited_ms = Date.now() - (deadline - Number(ec.timeout_ms || 180000));
              if (hit) {
                center.found = true;
                center.row = hit.slice(0, 110);
                center.row_time_ms = rowTimeMs(hit);
                break;
              }
              if (Date.now() + (Number(ec.poll_ms) || 6000) > deadline) break;
              await centerPage.waitForTimeout(Number(ec.poll_ms) || 6000);
            }
            if (!center.found) {
              problems.push(`导出中心 ${Math.round(Number(ec.timeout_ms || 180000) / 1000)}s 内`
                + `无「本轮新生成且已完成」的记录（同名候选 ${center.candidates || 0} 条，`
                + `前 3 行：${(center.sample || []).join(' / ').slice(0, 200)}）`);
            }
            // step7：从导出中心下载落盘 + 列名出现性校验
            if (center.found && check.download) {
              const dlPromise = centerPage.waitForEvent('download', {
                timeout: Number(check.max_wait_download_ms) || 30000,
              }).catch(() => null);
              dl.clicked = !!await centerPage.evaluate(() => {
                const els = Array.from(document.querySelectorAll('a,button,span'))
                  .filter((e: any) => e.offsetParent !== null
                    && String(e.innerText || '').trim() === '下载');
                if (els.length) { (els[0] as any).click(); return true; }
                return false;
              }).catch(() => false);
              const d: any = await dlPromise;
              if (d) { dl.via = 'center'; await saveAndInspect(d); }
              else { problems.push('未捕获下载'); }
            }
          }
          console.log('EXPORT_TRACE ' + JSON.stringify({
            name: nm, label, opened, dialog: dlg, validation, datesPicked, pick: pickNote,
            confirmClicked, toastAfter, center, dl, columns, ms: Date.now() - t0,
          }));
          console.log('EXPORT_EVIDENCE ' + JSON.stringify({
            name: nm, filename: dl.suggested, name_matched: dl.name_matched,
            saved: dl.saved, bytes: dl.bytes,
            columns_found: columns.found, columns_missing: columns.missing,
            validation_ok: validation.ok, duration_ms: Date.now() - t0,
          }));
          if (!problems.length) {
            exportStat.pass += 1;
            recAll(check, nm, 'pass',
                   `导出全链通过：入口→弹窗${dlg && dlg.present ? '' : '(未见弹窗)'}`
                   + `→空时间段校验${validation.checked ? (validation.ok ? '生效' : '未生效') : '未配置'}`
                   + `→导出中心完成(${center.waited_ms}ms)`
                   + (dl.saved ? `→已下载 ${dl.bytes}B` : '')
                   + (columns.checked ? `→列名 ${columns.found.length}/${columns.found.length + columns.missing.length} 命中` : ''));
          } else {
            exportStat.unverified += 1;
            recAll(check, nm, 'unverified', `导出链路未完全走通：${problems.join('；')}——现场见 EXPORT_TRACE`);
          }
        } catch (error) {
          exportStat.unverified += 1;
          recAll(check, nm, 'unverified', `导出检查异常：${String(error).slice(0, 200)}`);
        }
      }
      console.log('EXPORT_PHASE ' + JSON.stringify(exportStat));
    }
    await runNegativeScenarios();

    // ===== 上传校验：优先独立上下文（复核意见：隔离上传与业务上下文）=====
    const uploadChecks = FORM_PLAN.checks.filter((c: any) => c.type === 'upload_file');
    if (uploadChecks.length) {
      let isoContext: any = null;
      let isoLabel = 'main_fallback';
      let isoReason = '';
      const browser = context.browser();
      try {
        if (!browser) throw new Error('browser 不可用');
        const state = await context.storageState();
        isoContext = await browser.newContext({storageState: state});
        const isoPage = await isoContext.newPage();
        // 先完成进入与开表单（登录/加载本身需要写请求），**再**安装隔离策略
        const isoScope = await enterStorageAccountList(isoPage, isoContext);
        const isoModal = await openForm(isoPage, isoScope);
        await installIsolation(isoContext);
        isoLabel = 'isolated';
        await runUploadPhase(isoPage, isoScope, isoModal, uploadChecks, isoLabel);
      } catch (error) {
        isoReason = String(error).slice(0, 160);
        // 回退：主上下文（并如实记录"未隔离"）
        await installIsolation(context);
        await runUploadPhase(page, anyScope, modal, uploadChecks, isoLabel);
      } finally {
        if (isoContext) { await isoContext.close().catch(() => undefined); }
      }
      console.log('UPLOAD_CONTEXT ' + JSON.stringify({label: isoLabel, reason: isoReason}));
    }

    // ===== 「列表可见」阶段：排在最后（写阶段全部结束后才回列表页检索）=====
    // 位置理由：它会 `page.goto` 到列表页，放在任何还需要"表单/弹窗现场"的阶段之前
    // 会把那些阶段的现场破坏掉；放在最后则只影响 summary 输出之外的收尾动作（无）。
    //
    // rev151 看门狗：这两个阶段要跨标签页导航，一旦页面被带进无效路由，
    // ``evaluate`` 可能永不返回（实测 TR-764 列表阶段挂死 12 分钟、只烧预算不出任何 trace）。
    // 因此各自给硬上限：超时即记 PHASE_WATCHDOG 并继续（summary 照常产出，结果如实 unverified）。
    const phaseWatchdog = async (work: Promise<void>, ms: number, phase: string): Promise<void> => {
      let timer: any = null;
      const guard = new Promise<null>((res) => { timer = setTimeout(() => res(null), ms); });
      const done: any = await Promise.race([work, guard]);
      if (timer) clearTimeout(timer);
      if (done === null) console.log('PHASE_WATCHDOG ' + JSON.stringify({phase, ms}));
    };
    await phaseWatchdog(runListChecks(), 180000, 'list');
    // rev170：筛选区独立通道（同样各自 goto，排在列表检查之后）
    await phaseWatchdog(runFilterChecks(), 120000, 'filter');
    // rev173：状态×操作矩阵独立通道（含确认框验证，排在筛选之后）
    await phaseWatchdog(runActionMatrix(), 180000, 'matrix');
    // rev148：导出独立通道（在列表检查之后——两者都各自 goto，互不复用现场）
    await phaseWatchdog(runExportChecks(), 240000, 'export');

    //: rev165（2026-10-21，P1 探针）：**过程态不重复计数**。
    //: required_error 的客户端路径在"取不到字段级提示"时会先记一条 unverified，再把该项交给
    //: 提交层重做；提交层给出真实结论后，那条 unverified 只是过程记录，不是最终结论。
    //: 旧口径把它算进 total（实测 TR-779：total=8 / pass=7 / unverified=1，而真实是 7 条断言全通），
    //: 读者会以为有一条没验过。这里把"已被提交层结论覆盖"的过程态从计数里剔除，
    //: 但仍保留在 COMPILED_ASSERTION 逐条输出里（可复核），差额由 deferred_superseded 说明。
    const __DEFERRED_RE = /未出现字段级提示|未出现必填提示|必填校验需提交层|仅提交时校验/;
    const __submitCovered = new Set(
      formResults.filter((r) => String(r.observed).startsWith('提交层验证')).map((r) => r.name),
    );
    const __superseded = formResults.filter(
      (r) => r.outcome === 'unverified' && __submitCovered.has(r.name) && __DEFERRED_RE.test(String(r.observed)),
    ).length;
    const __counted = formResults.length - __superseded;

    const summary = {
      total: __counted,
      pass: formResults.filter((r) => r.outcome === 'pass').length,
      unverified: formResults.filter((r) => r.outcome === 'unverified').length - __superseded,
      blocked: formResults.filter((r) => r.outcome === 'blocked').length,
      //: 实际写出的逐条结果数（未剔过程态）+ 被剔除的条数：两者之和恒等于 total + superseded，
      //: 对账行用它还原"写了多少条"，不还原就会把这次改进读成"少跑了 1 条"。
      emitted_raw: formResults.length,
      deferred_superseded: __superseded,
      submit_phase: {
        queued: submitQueue.length,
        // rev139：本层**实际写出**多少条结果（超时/中断也会各写一条，故不能拿 queued 代）
        emitted: submitEmitted,
        pass: formResults.filter((r) => String(r.observed).startsWith('提交层验证') && r.outcome === 'pass').length,
        suspected_defect: formResults.filter(
          (r) => r.outcome === 'blocked' && String(r.observed).includes('疑似缺陷')).length,
      },
      // 规则级负路径（P1-3 阶段 B）：与字段级 submit_phase **分开计数**——
      // 两者的入队条件、写风险与判定口径都不同，混在一起会让"到底跑了几条规则级场景"看不出来。
      negative_scenario_phase: {
        ...negativeStat,
        rejected_at_plan: (FORM_PLAN.negative_scenario_rejects || []).length,
      },
      // 「列表可见」阶段（2026-10-16）：写入后记录是否可检索。与上面两个阶段分开计数。
      list_phase: {
        ...listStat,
        rejected_at_plan: (FORM_PLAN.list_check_rejects || []).length,
      },
      // 「导出」独立阶段（rev148）：菜单→弹窗→时间段→导出中心→下载→列名，分开计数。
      export_phase: {
        ...exportStat,
        rejected_at_plan: (FORM_PLAN.export_check_rejects || []).length,
      },
      // 「筛选区」独立阶段（rev170）：下拉筛选结果集 + 重置还原，分开计数。
      filter_phase: {
        ...filterStat,
        rejected_at_plan: (FORM_PLAN.filter_check_rejects || []).length,
      },
      // 「状态×操作矩阵」独立阶段（rev173）：行操作集 + 更多菜单 + 确认框，分开计数。
      matrix_phase: {
        ...mxStat,
        rejected_at_plan: (FORM_PLAN.action_matrix_rejects || []).length,
      },
      skipped_no_locator: FORM_PLAN.skipped_count,
      // 只有一个数字无法复核："跳过 9 条"到底是哪几类原因
      skipped_by_reason: (() => {
        const counts: Record<string, number> = {};
        for (const s of (FORM_PLAN.skipped || [])) {
          const key = String((s as any).reason || 'unknown').slice(0, 28);
          counts[key] = (counts[key] || 0) + 1;
        }
        return counts;
      })(),
      plan_checks: FORM_PLAN.checks.length,
      attributed_checks: FORM_PLAN.checks.filter(
        (c: any) => c.case_id || (Array.isArray(c.owners) && c.owners.length)).length,
      // 配置驱动的计划级检查：来自定位器配置（如 default_selected 的默认值声明、
      // list_checks / export_checks 的列表/导出通道）。rev166（2026-10-21，TR-780 实测
      // unexplained_plan=-3）：判定**只看 source**，不再要求"无 case_id"——
      // list_checks/export_checks 会带自造的 case_id（如 TC-VIEWONLY-REV157，仅用于
      // 结果归属台账），它们同样不是文档断言翻译来的，旧条件把它们排除在 config_only
      // 之外 ⇒ plan 侧恒等式恒为负（planned=1 - (executable 6 - config_only 2) = -3）。
      // source 是"是否文档断言"的单一事实；case_id 只是归属线索，不代表断言来源。
      config_only_checks: FORM_PLAN.checks.filter(
        (c: any) => c.source === 'locator_config',
      ).length,
      // 上传类检查**不计入 total**：它们只输出原始证据（UPLOAD_EVIDENCE），
      // 由服务端分类器判定后才写入用例结果（避免浏览器侧假通过）。
      upload_evidence: uploadEvidenceCount,
      isolation_blocked: isolationBlocked.length,
      isolation_blocked_kinds: {
        upload_blocked: isolationBlocked.filter((b) => b.kind === 'upload_blocked').length,
        non_upload_api_blocked: isolationBlocked.filter((b) => b.kind === 'non_upload_api_blocked').length,
        unknown_request_blocked: isolationBlocked.filter((b) => b.kind === 'unknown_request_blocked').length,
      },
    };
    console.log('COMPILED_ASSERTION_SUMMARY ' + JSON.stringify(summary));
    // rev130（F-3）：**覆盖对账行**——「这次到底验了几条」只留一个答案。
    // 旧产物的三个数分属编译期（COMPILED_COVERAGE.assertions）与运行期（SUMMARY.total），
    // 实测 TR-747 报 7 / 5 / 6 / 跳过 2，读者无法判断差额是「跳过」还是「丢了」。
    // 恒等式：
    //   planned   = (executable - config_only) + skipped_no_locator + unexplained_plan
    //   emitted   = executable + scenario_executed + submit_resubmitted + unexplained_emitted
    // rev139：把 `submit_resubmitted` 显式列出来。提交层对同一批检查会再写一条结果
    //（真实提交才算证据），旧公式把它算成"多发"，于是 balanced 恒为 false——
    // 实测 TR-755：emitted=9 / executable=6 / scenario=1 ⇒ unexplained_emitted=2，
    // 那 2 条不是丢失，是 required_error 在提交层的重发。缺口与重发混为一谈，
    // 等于把"没丢"报成"丢了 2 条"。
    const __plannedA = FORM_PLAN.planned_assertions || 0;
    const __execA = summary.plan_checks;
    const __skipA = summary.skipped_no_locator;
    const __configA = summary.config_only_checks || 0;
    const __scenarioA = summary.negative_scenario_phase.executed;
    const __submitA = summary.submit_phase.emitted || 0;
    // rev167：被 defaults 观测代为取证的 default 断言——检查本身标 source=locator_config
    //（计入了 config_only），但 planned 里有它们。不算进恒等式就会像 TR-784 一样
    // 恒报 unexplained_plan=4（门店/商品/题目类型/是否必答四条 default 断言）。
    const __defaultCovA = FORM_PLAN.default_covered_assertions || 0;
    const __unexplainedPlan = __plannedA - ((__execA - __configA) + __defaultCovA + __skipA);
    // rev165：用 emitted_raw（未剔过程态的实际写出行数）——summary.total 现在剔掉了
    // 被提交层覆盖的过程态，拿它做减法会把"改进"读成"少发了 1 条"（balanced 变 false）。
    const __unexplainedEmitted = summary.emitted_raw - (__execA + __scenarioA + __submitA);
    // rev136：**减去 config_only**。配置驱动的检查（`defaults` 的默认选中、条件显示）
    // 不是文档断言翻译来的，把它们算进 executable 会让 plan 侧恒等式恒为负——
    // 实测 TR-752：planned=4（3 条用例共 4 条断言）、executable=6（多出 2 条
    // 默认值观测）、unexplained_plan=-2、balanced=false。那 -2 不是缺口，
    // 是"2 条配置检查被误当成断言"；对账行自己制造了假缺口，比不报更糟。
    console.log('COVERAGE_RECONCILE ' + JSON.stringify({
      planned: __plannedA,
      executable: __execA,
      config_only: __configA,
      skipped_no_locator: __skipA,
      scenario_configured: (FORM_PLAN.negative_scenarios || []).length,
      scenario_executed: __scenarioA,
      submit_resubmitted: __submitA,
      emitted: summary.emitted_raw,
      //: 其中有多少条是"提交层已给结论、客户端过程态被剔除"的（rev165）
      superseded: summary.deferred_superseded || 0,
      unexplained_plan: __unexplainedPlan,
      default_covered: __defaultCovA,
      unexplained_emitted: __unexplainedEmitted,
      balanced: __unexplainedPlan === 0 && __unexplainedEmitted === 0,
      skipped_by_reason: summary.skipped_by_reason,
    }));
    // 结构性失败（blocked）视为硬失败：定位器失效必须暴露，而不是静默通过
    expect(summary.blocked, `编译断言执行受阻：${summary.blocked} 项`).toBe(0);
  }
  // 说明：客户端层断言全程只读；「期望拒绝」未验证项进入负路径提交层（口径经用户 2026-09-25 确认），
  // 只提交空必填/非法探针值，正常不产生合法业务数据；被测端若接受则按疑似缺陷暴露。
  await runFormChecks();
"""


def render_form_executor(plan: dict[str, Any], *, default_phone: str = "18320101265",
                         upload_allow_hosts: list[str] | None = None,
                         upload_allow_rules: list[dict[str, Any]] | None = None,
                         upload_allow_upload_rules: list[dict[str, Any]] | None = None) -> str:
    """渲染表单校验执行器（TS 片段）：客户端层只读 + 「期望拒绝」项负路径提交层。

    上传隔离策略（安全边界）：
    - ``multipart/form-data`` **默认一律阻断**；唯一口子 = ``allow_upload_rules``
      （host + path_prefix + methods 三项收窄，且必须**显式声明**）；
    - GET 放行；
    - 其他写请求**必须命中** ``upload_allow_rules``（host / path_prefix / methods），
      默认空 → 所有写请求都阻断（不默认放行同源写，避免产生未知业务写副作用）。
    - ``upload_allow_hosts`` 仅作兼容：表示"这些主机的 GET 放行"，不再放行写请求。

    ``upload_allow_rules``（multipart 口子）的来源（2026-10-16 rev114）：
    默认**从场景的 ``allow_upload`` 声明推导** —— 单一真相在场景上。写隔离的口径本来就
    是"收窄到本次场景声明的目标"，上传放行不该跨场景泄漏；配置方只写一处，也避免
    "场景声明了上传、字段级阶段却仍被拦"的漂移。kwarg 仅作补充（无场景却需上传的计划）。
    """
    #: multipart 放行规则：先场景推导，再并入显式 kwarg（去重，保序）。
    derived_upload_rules: list[dict[str, Any]] = []
    seen_upload: set[str] = set()
    for sc in (plan.get("negative_scenarios") or []):
        policies = ((sc or {}).get("expect_accept") or {}, (sc or {}).get("expect_reject") or {})
        for pol in policies:
            for rule in (pol.get("allow_upload") or []):
                if not isinstance(rule, dict):
                    continue
                key = json.dumps(rule, sort_keys=True, ensure_ascii=False)
                if key in seen_upload:
                    continue
                seen_upload.add(key)
                derived_upload_rules.append(rule)
    # 字段级通道（rev119 / A1）：子功能级 ``field_locators.upload_allow`` 声明。
    # build_form_plan 已用同一套 ``_parse_allow_upload`` 校验过，这里只做去重合并。
    # 为什么必须有这一路：附件类用例（file_size / file_format）**没有场景**，
    # 只靠下面那个场景循环永远拿不到规则 ⇒ 上传恒被 abort。
    for rule in (plan.get("upload_allow") or []):
        if not isinstance(rule, dict):
            continue
        key = json.dumps(rule, sort_keys=True, ensure_ascii=False)
        if key in seen_upload:
            continue
        seen_upload.add(key)
        derived_upload_rules.append(rule)
    for rule in (upload_allow_upload_rules or []):
        if not isinstance(rule, dict):
            continue
        key = json.dumps(rule, sort_keys=True, ensure_ascii=False)
        if key in seen_upload:
            continue
        seen_upload.add(key)
        derived_upload_rules.append(rule)

    payload = json.dumps(
        {"locators": plan.get("locators"), "checks": plan.get("checks"),
         "skipped": plan.get("skipped"), "skipped_count": plan.get("skipped_count", 0),
         # rev131（F-3 修正）：对账用的**计划断言数**必须随计划下发。
         # 这里是一个白名单字典——漏这个键的后果实测过：TR-748 的
         # COVERAGE_RECONCILE 打出 planned=0 / unexplained_plan=-3（明明有 3 条断言），
         # 对账行反而制造了新的误导。凡运行期要读的计划字段都必须在此登记。
         "planned_assertions": plan.get("planned_assertions") or 0,
         # rev167：defaults 带 owners 的 default 断言数——对账恒等式要用
         #（漏登记的后果与 rev131/TR-748 同型：TR-785 default_covered 恒 0，
         # unexplained_plan=4 假缺口照旧）。
         "default_covered_assertions": plan.get("default_covered_assertions") or 0,
         # P1-3 阶段 B：规则级负路径场景随计划下发给执行器。
         # ``negative_scenario_rejects`` 一并下发——配置期被拒的场景必须在运行期可见，
         # 否则"配了但没跑"只能靠人去比对配置与日志。
         "negative_scenarios": plan.get("negative_scenarios") or [],
         "negative_scenario_rejects": plan.get("negative_scenario_rejects") or [],
         # rev124（A2）：运行期唯一化。``run_tag`` 声明"哪些字段会唯一化"，
         # ``run_tag_gap`` 是"该唯一化却没有"的缺口（真实落库场景里值固定）。
         "run_tag": plan.get("run_tag") or {},
         "run_tag_gap": plan.get("run_tag_gap") or [],
         # rev137：字段检查前置动作（rev131 的教训：凡运行期要读的计划字段
         # 都必须在这个白名单里显式登记，否则执行器读到 undefined 且毫无提示）。
         "pre_actions": plan.get("pre_actions") or [],
         "pre_action_rejects": plan.get("pre_action_rejects") or [],
         # rev148：导出独立通道的被拒配置同样要运行期可见（[export-skip]）
         "export_check_rejects": plan.get("export_check_rejects") or [],
         # rev170：筛选区独立通道的被拒配置（[filter-skip]）
         "filter_check_rejects": plan.get("filter_check_rejects") or [],
         # rev173：状态×操作矩阵通道的被拒配置同样要运行期可见（[matrix-skip]）
         "action_matrix_rejects": plan.get("action_matrix_rejects") or [],
         # rev142：提交层 toast 归因要用「定位器键 → 页面别名」反查
         #（toast「题目 11：请输入标题」含页面标签「标题」而非字段名「题目标题」）。
         "field_alias_applied": plan.get("field_alias_applied") or [],
         # rev119：字段级上传放行的**已知缺口**（空串 = 无缺口）。运行期原样打印，
         # 让「配了但上传会被拦」在日志里可见，而不是只体现为断言 unverified。
         "upload_policy_gap": plan.get("upload_policy_gap") or "",
         "upload_policy": {
             "allow_rules": list(upload_allow_rules or []),
             "allow_hosts": list(upload_allow_hosts or []),
             # multipart 唯一口子：默认空 ⇒ 一律阻断（与历史行为逐字一致）
             "allow_upload_rules": derived_upload_rules,
         },
         "upload_fixtures": {
             "workbook_parts": xlsx_parts_base64(),
             "workbook_file": base64.b64encode(minimal_xlsx_bytes()).decode("ascii"),
             "xls_file": xls_base64(),
             "bad_extension_part": base64.b64encode(bad_extension_bytes()).decode("ascii"),
         }},
        ensure_ascii=False,
    )
    return (
        _FORM_EXECUTOR_TS
        .replace("@@FORM_PLAN@@", payload)
        .replace("@@DEFAULT_PHONE@@", default_phone)
    )


#: P4 窄口径（rev169）+ rev170 修正：import 期预热 digest **必须放在模块末尾**——
#: digest 覆盖 ``_FORM_EXECUTOR_TS``、``render_form_executor`` 与多个解析函数，
#: 放在前部时它们尚未定义，``g.get`` 静默跳过 ⇒ 固化的是**残缺 digest**（实测
#: warm=fec94279864a ≠ full=ad10a9c44913，rev170 当场发现）。模块末尾 = 全部
#: digest 输入已定义，预热结果与懒加载首调完全一致，同时保留 rev169 的
#: 「编辑窗口不 500」收益。
compiler_source_digest()
