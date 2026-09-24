## ADDED Requirements

### Requirement: Explicit semester calendar
系统 SHALL 使用上海业务日期，将春季定义为2月1日至8月1日前，秋季定义为9月1日至次年2月1日前。

#### Scenario: January and February boundary
- **WHEN** 日期由2027年1月31日进入2月1日
- **THEN** 当前学期从2026秋季切换到2027春季，前者完整包含5个月

#### Scenario: August read-only policy
- **WHEN** 服务器业务日期进入8月
- **THEN** 不存在当前计分学期，界面默认展示最近完成的春季，拒绝新增业务记录；其他写入按提案D3审核结果执行

### Requirement: Calendar-derived monthly base score
系统 SHALL 将当前学期已进入月份或历史学期完整月份纳入平均，无事件月按当期规则月度基础分计算（默认60），未来月份不提前纳入。

#### Scenario: Month rollover without writes
- **WHEN** 9月得分50且10月刚开始、没有10月活动
- **THEN** 第一次只读查询即显示9月50、10月60、学期均分55，无需数据库写入触发

### Requirement: Current semester corrections preserve consistency
系统 SHALL 允许获授权用户补录或修改当前学期内的记录，并按发生月份和该学期绑定规则同步计算明细与汇总。

#### Scenario: Missing month cache
- **WHEN** 当前为10月，学生已有10月汇总，后来补入同一学期9月缺勤明细
- **THEN** 9月缺勤进入对应月和学期统计，缓存缺失不能导致漏计

#### Scenario: Moving or deleting an event
- **WHEN** 获授权在当前学期内修改活动发生月份或删除最后一条报告
- **THEN** 旧月和新月受影响次数均正确，空月保留日历基础分

### Requirement: Closed semesters are read-only
系统 SHALL 根据服务器业务日期将已结束学期设为只读，禁止所有角色通过网页、后台、维护入口或未来API新增、修改、删除当期业务数据或更改审核标记。

#### Scenario: Owner attempts to change a closed semester
- **WHEN** 2026秋季期间负责人尝试新增学生、修改报告、删除活动或更改2026春季审核状态
- **THEN** 请求被拒绝，历史名单、记录和分数保持不变

#### Scenario: Stale form or changed event date
- **WHEN** 旧表单提交时所属学期已结束，或尝试将活动移入/移出已结束学期
- **THEN** 提交时重新检查学期状态并拒绝写入，不依赖定时任务或客户端日期

### Requirement: Semester-specific student membership
系统 SHALL 将学期名单与当前学生资料隔离；新增学生只加入当前学期并参与其全部已进入月份计分，归档保留当期身份和计分依据。

#### Scenario: Supplement or update a current student
- **WHEN** 当前学期新增学生、改名或移除学生
- **THEN** 已结束学期名单和成绩不变，新新增学生不自动加入过去学期；当前补录月份之前的当期无记录月份仍按当期规则基础分计

#### Scenario: No scheduled rollover or long period without access
- **WHEN** 系统跨越学期边界但未运行定时任务或访问历史报表，之后发生名单变化
- **THEN** 在变化前保全已结束学期依据，历史读取不使用变化后的现行名单替代当期名单

#### Scenario: Deletion would cascade into history
- **WHEN** 删除学生或班级会级联删除已结束学期记录
- **THEN** 拒绝破坏性删除，使用不改变历史数据的归档或当前名单变更方式

### Requirement: Rebuildable summaries
系统 SHALL 将报告事实作为次数来源，保证业务唯一性并能重建派生汇总；已结束学期重建不得改变归档名单、事实或最终成绩。

#### Scenario: Rebuild before applying data repair
- **WHEN** 检测到重复业务记录或缓存差异
- **THEN** 先生成冲突/差异清单，不静默删除冲突记录；获准修复后可验证重建结果

#### Scenario: Historical rebuild differs from the archived baseline
- **WHEN** 历史重建结果与归档成绩不一致
- **THEN** 仅报告差异，保留归档结果，不通过普通修复入口修改历史分数
