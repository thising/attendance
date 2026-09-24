## ADDED Requirements

### Requirement: Owner-readable class event timeline
系统 SHALL 为每班提供负责人可查看的概要事件日志，记录重要业务操作的时间、身份、摘要与影响范围。

#### Scenario: Owner views class operations
- **WHEN** 班级负责人查看本班操作记录
- **THEN** 按时间显示活动/名单/规则等操作摘要，并允许按事件类型筛选

#### Scenario: Committee login does not imply log access
- **WHEN** 班委账号或其他班级负责人的用户请求日志
- **THEN** 拒绝访问该班日志

### Requirement: Accurate and minimal actor attribution
系统 SHALL 如实区分负责人账号、“班委”（独立指定账号）和未来机器身份，保留操作者账号与当时显示姓名快照，旧共享授权日志不反推身份。

#### Scenario: Named committee actor
- **WHEN** 操作通过独立班委账号完成
- **THEN** 标记为“班委”并保存具体账号及当时显示姓名，不记录原始会话凭据；重命名或停用账号不改变已有日志

#### Scenario: Legacy shared actor
- **WHEN** 查看旧共享密码授权产生的日志
- **THEN** 标记为“班委（旧共享授权）”，不将其推定为新建的某个个人账号

### Requirement: Atomic and idempotent event recording
系统 SHALL 将成功业务变更与概要日志同事务提交，日志不可由普通用户编辑或删除。

#### Scenario: Request retries or fails
- **WHEN** 请求被幂等重放或业务事务回滚
- **THEN** 不增加重复或虚假的成功日志

#### Scenario: Activity deletion retains event summary
- **WHEN** 获授权删除活动
- **THEN** 保留该活动已有概要事件及必要名称快照，删除操作本身有一条日志

### Requirement: Global policy change is visible in affected classes
系统 SHALL 将负责人全局权重更新关联到实际受影响班级，而不为每个学生生成相同事件。

#### Scenario: One owner updates weights for multiple classes
- **WHEN** 负责人成功保存当前及未来学期评分规则
- **THEN** 其受影响班级各显示一次相关更新摘要，其他负责人的班级不可见该事件

### Requirement: Secrets excluded from summaries
系统 SHALL 不向概要日志写入明文或加密密码副本、token、原始Session ID、Cookie或完整敏感请求内容。

#### Scenario: Password is reset
- **WHEN** 负责人重设班委密码
- **THEN** 日志只记录操作事实、操作者及时间，不包含新旧密码


#### Scenario: Owner retrieves credentials
- **WHEN** 班主任成功读取班委登录信息用于复制
- **THEN** 仅记录读取账号、操作者和时间，不记录凭据内容，也不将读取回执视为剪贴板已成功写入
