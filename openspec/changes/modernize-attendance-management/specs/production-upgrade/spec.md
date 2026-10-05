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

### Requirement: Configurable offsite backup destination
部署 SHALL 支持配置异地备份目标，数据库、运行配置和解密所需密钥配套保全，并验证传输完整性及隔离恢复。本阶段目标为家中 NAS，未来私有部署 SHALL 按客户环境替换配置，不将家庭 NAS 地址、路径或凭据写死在应用中。本要求的R15工具已在本地与真实NAS验证，并于2026-10-05安装启用；真实整机电源周期与长期触发不在首次恢复结论内，证据见发布记录。

#### Scenario: Initial home NAS backup
- **WHEN** 实施本阶段异地备份
- **THEN** 先核对既有 NAS 连接和私有存储配置，完成受保护的成套复制、摘要核验和隔离恢复后才登记为已验收；本地备份成功不能代替异地复制成功

#### Scenario: Later customer deployment
- **WHEN** 在客户环境私有部署
- **THEN** 按客户资源配置备份目标并独立验证恢复，不依赖家中 NAS 才能部署应用

### Requirement: Isolated dual-chain publication and verification
家中NAS部署 SHALL 沿用现有项目的可读档案与客户端加密系统双链路，项目身份、目录、容器、仓库和状态隔离。可读档案 SHALL 排除认证、令牌及密钥，历史只读且摘要核验后原子切换；系统仓库 SHALL 开启私有身份与append-only，解密口令不明文留在NAS。两链从本次准确恢复点回读验证后才登记成功，失败保留上一可恢复点。

#### Scenario: Interruption or stale recovery point
- **WHEN** NAS不可达、传输中断、数据超过48小时、空间低于20%、容量标记超时或时区不明
- **THEN** 任务不标记成功，保留前次恢复点，记录失败状态并使用用户授权渠道发送仅含项目/状态/时间的通知；恢复后发恢复通知

#### Scenario: Historical bundles lack code identity
- **WHEN** 原有完整备份缺少代码版本元数据
- **THEN** 原样加密保全并核对恢复字节，不改写原清单，继续标记代码依据不足，不宣称匹配代码恢复已验证

#### Scenario: Daily and monthly home NAS windows
- **WHEN** 家中NAS按02:00关机、10:30开机计划运行
- **THEN** 异地任务在北京时间11/18/23点补传，完整系统每日加密，月初11:30校验；同项目任务互斥，真实自然调度与整机周期另行观察

### Requirement: Seven-day local retention with permanent NAS history
本阶段服务器 SHALL 自动保留最近7×24小时内的已完成配对备份，NAS SHALL 保留历史且不运行自动forget/prune或档案删除。服务器清理 SHALL 在异地任务成功后执行，先对每份候选从准确NAS快照恢复并比较数据库、环境及清单摘要；v2还须保全匹配源码。未归档、校验失败、未完成、符号链接或未知内容 SHALL 保留本机并报告，NAS不可达时不删候选。无论日期如何 SHALL 保留最后一份可用本机恢复点。

#### Scenario: Multiple pending backups after NAS downtime
- **WHEN** NAS恢复后本机积累多份未上传的已完成备份
- **THEN** 逐份原样加密归档并回读核验，不只复制最新一份；记录每份不可变身份、准确快照和代码依据，然后清理超过7天的已验本机副本

#### Scenario: Unverified or corrupted historical backup
- **WHEN** 待清理备份无NAS回执、恢复失败、内容变化或含未知文件
- **THEN** 不删除这些本机备份；保留错误信息，允许保留超过7天以保障恢复

#### Scenario: NAS manual maintenance
- **WHEN** 管理员决定清理NAS历史
- **THEN** 单独预览和人工审核后执行维护，不由本机7天策略触发；本阶段不执行NAS删除或放宽append-only权限
