## ADDED Requirements

### Requirement: Explicit migration lineage
系统 SHALL 识别既有数据库的迁移账本和关键业务表结构。2026年生产初始迁移已包含违纪字段时，默认开发迁移链 SHALL 拒绝运行旧0002；只有显式选择已核实的生产专用谱系才能继续。未知迁移节点或缺失关键字段 SHALL 阻断升级，不通过 `--fake` 伪造迁移记录。

#### Scenario: Development migration sees production 0001
- **WHEN** 默认迁移命令指向仅有2026生产0001且含违纪字段的数据库
- **THEN** 在任何写入前拒绝，不执行会重建并清零非零违纪值的旧0002

#### Scenario: Unknown source lineage
- **WHEN** 生产专用模式遇到未知迁移节点或缺失关键业务字段
- **THEN** 中止并要求人工审查，不自动猜测或修正结构

### Requirement: Copy-first baseline and recovery rehearsal
正式升级前系统 SHALL 在来源只读快照的独立副本中完成迁移、非零违纪哨兵、五张原业务表事实保全、当前学期九项缓存与报告核对，以及旧谱系恢复验证。只有有证据支持的当前学期事实可建立基线；出现历史业务、日期越界、缓存差异或重复基线时 SHALL 整体拒绝，不推算历史成绩。

#### Scenario: Verified current-term source
- **WHEN** 最终快照的活动与汇总均属于已进入的当前学期，九项缓存与原始报告一致
- **THEN** 同一事务建立当前名单、评分依据及每班预生成报告，原始业务事实与非零违纪值保持不变

#### Scenario: Source facts conflict
- **WHEN** 快照含未核实的历史活动、8月业务或缓存与报告不一致
- **THEN** 不提交部分基线，保留来源与原系统可恢复状态，等待人工核实
