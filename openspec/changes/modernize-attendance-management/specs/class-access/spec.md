## ADDED Requirements

### Requirement: Class-scoped authorization
系统 SHALL 对每次业务操作验证身份、动作权限和班级归属，覆盖网页、后台、维护入口和未来 API。

#### Scenario: A class grant cannot modify another class
- **WHEN** 请求具有 A 班授权但指定 B 班活动或学生
- **THEN** 系统拒绝请求且不修改任何业务记录

#### Scenario: Only the owner deletes activities
- **WHEN** 匿名用户或仅持班级授权的用户请求删除活动
- **THEN** 系统拒绝删除，即使其知道活动编号和访问码

### Requirement: Named committee accounts
系统 SHALL 由班主任为班级指定独立班委账号，每班最多5个启用账号，完整用户名为班级固定唯一4位hash前缀加点和3–40位短名，同班短名唯一、跨班可复用，每个账号固定一个班级；共享密码和班级码不能再用于认证。

#### Scenario: Activate the sixth account
- **WHEN** 同一个班已有5个启用账号，又创建或启用第6个，即使并发提交
- **THEN** 拒绝超额操作，不产生额外账号或成功日志

#### Scenario: Single-class account scope
- **WHEN** 班委用用户名和密码登录
- **THEN** 只可访问指定班级，不能管理名单、权重或账号，不能访问其他班级；班级数字ID不能扩大权限

#### Scenario: Retired shared session
- **WHEN** 浏览器仍带旧class_grants或请求原共享密码接口
- **THEN** 旧授权不能访问业务数据，旧授权接口明确停用

### Requirement: Reusable named session
系统 SHALL 在一次正确班委账号验证后保存固定3小时会话，后续合法操作无需重复密码，普通使用不延长到期；停用或改密立即撤销旧会话。

#### Scenario: Absolute three-hour expiry
- **WHEN** 距离正确验证已满3小时，即使持续操作且浏览器Session仍有效
- **THEN** 班委会话失效；当前草稿允许用原账号重新登录后继续提交

#### Scenario: Account reset and uncertain submission
- **WHEN** 原账号重置密码后重新登录并重试原提交
- **THEN** 仍按同一账号幂等，已成功写入不重复；切换账号重试原草稿必须拒绝

#### Scenario: Disable during historical viewing
- **WHEN** 班主任在8月或查看历史期间停用班委账号
- **THEN** 旧登录失效，历史名单/记录/分数保持不变

### Requirement: Shared login failure guard
系统 SHALL 对班主任和班委登录按规范化用户名独立计数；连续5分钟内第8次失败立即阻断，此后30分钟内即使密码正确也拒绝，成功登录清零。未知用户名同样受限，清除Cookie不能重置计数。

#### Scenario: Eighth failed attempt
- **WHEN** 同一用户名在5分钟内连续8次提交错误密码
- **THEN** 第8次返回限流，30分钟后才允许再次验证

#### Scenario: Five-minute window expires
- **WHEN** 第一次失败已过去5分钟，期间未触发阻断
- **THEN** 新失败作为新窗口第1次计数

### Requirement: Current-term public class report
系统 SHALL 允许班主任或本班班委启用并获取本班高强度固定分享链接和二维码；持有链接的访客仅可查看该班**当前学期**个人学期汇总及逐月九项次数与分数，不提供历史选择、个人记录详情和写入口。班主任可更换或停用链接；班委不得更换、停用或访问其他班链接。链接令牌以摘要索引、独立密钥加密保存，二维码和响应不得发送给第三方服务。

公开内容 SHALL 预生成并保存为每班唯一的只读快照，包含学期、自然月、业务修订号和内容摘要。点名/活动、学生名单、评分规则变更成功后，事务提交后立即更新受影响班级；新自然月或新学期即使无活动也更新，8月生成无当前学期成绩的状态。北京时间每日00:01执行边界刷新、04:00执行包含内容核对的一致性校验；后者是兜底，不能代替业务写入后的刷新。并发刷新 SHALL 至多进行一次实质计算，完整快照在事务中原子替换。每次匿名访问 SHALL 先验证令牌和启用状态，再读取预生成快照；过期或缺失时同步修复，不直接公开可绕过校验的HTML文件。

只读报告 SHALL 显示当前所读快照的实际生成时间，普通访问不刷新该时间；业务变更、月/学期切换或一致性修复生成新快照后才更新。

#### Scenario: Committee shares a class report
- **WHEN** 本班班委打开班级总览并启用或查看公开报告
- **THEN** 可复制链接或下载二维码用于分享；只能获取本班报告，不能更换或停用链接

#### Scenario: Anonymous reader and revoked link
- **WHEN** 未登录访客持有有效链接
- **THEN** 仅显示当前学期只读成绩表；更换或停用后旧链接立即返回404，8月不展示已结束学期数据

#### Scenario: Committed fact change and unchanged duplicate submission
- **WHEN** 点名、名单或评分规则事务成功提交，或相同提交编号被网络重试
- **THEN** 成功的新业务变更更新相应班级快照；重复提交不触发重复计算，公开访问读取新内容

#### Scenario: Calendar boundary without records
- **WHEN** 北京时间进入新自然月、新学期或8月，期间没有新增业务记录
- **THEN** 边界刷新仍更新报告；首次访问在调度遗漏时也先修复，绝不展示上一学期成绩

#### Scenario: Daily verification and concurrent requests
- **WHEN** 04:00发现报告缺失、过期或内容与实时业务事实不一致，或多个访客同时访问过期报告
- **THEN** 校验重建异常报告；并发访问共享原子替换后的同一份完整报告，不重复计分

### Requirement: Idempotent and atomic writes
系统 SHALL 将同一身份/班级/操作的提交编号与请求摘要绑定，使用数据库唯一性和事务保证一次业务提交最多生效一次。

#### Scenario: Lost response and concurrent retries
- **WHEN** 相同编号和内容多次或并发提交
- **THEN** 只产生一次业务变更，重试返回原结果，不重复消耗新增额度

#### Scenario: Same key with changed content
- **WHEN** 已处理提交编号被用于不同内容
- **THEN** 返回冲突而不是忽略变化或重复写入

### Requirement: Versioned edits
系统 SHALL 校验活动和评分配置的修改版本，阻止旧表单覆盖新修改。

#### Scenario: Concurrent edit
- **WHEN** 提交版本早于当前记录版本
- **THEN** 返回可恢复的冲突提示，保持当前数据不变

### Requirement: Current-term destructive class management
系统 SHALL 仅允许班主任对当前学期执行清空数据、清空学生和删除班级，并校验完整班级名称、班级 revision 与幂等提交编号。清空数据只删除当前学期业务事实及对应派生缓存；清空学生仅在当前学期无业务记录时移出当前名单；已有历史学期资料的班级不得删除。已结束学期快照、身份和成绩不得改写。

#### Scenario: Clear current data with historical records
- **WHEN** 班主任确认清空当前学期数据，班级另有已结束学期或8月旧记录
- **THEN** 仅当前学期的考勤、活动、违纪和对应缓存被删除；学生名单、历史事实、历史快照和概要日志保留，并立即刷新当前公开报告

#### Scenario: Clear students before records
- **WHEN** 当前学期仍有业务记录时请求清空学生
- **THEN** 系统拒绝并提示先清空数据；数据与名单均不改变

#### Scenario: Delete a class with history
- **WHEN** 班级仍有当前名单、当前记录或任何历史学期资料
- **THEN** 系统拒绝删除；只有完全空且无历史的班级可删除，删除后其班委账号和公开链接立即失效

### Requirement: Duplicate-safe bulk roster import
系统 SHALL 在 Excel 上传、粘贴预览和最终提交中识别同批重复学号及本班当前名单重复学号，任一冲突使整批不写入；数据库唯一约束 SHALL 作为并发兜底。曾从当前学期移出的同学号学生再次导入时 SHALL 复用原身份并恢复在册，不创建重复学生。

#### Scenario: Duplicate in file or current roster
- **WHEN** 批量名单内部出现相同学号，或学号已在本班当前名单
- **THEN** 预览或提交返回重复学号错误，整批学生均不写入

#### Scenario: Re-import a removed student
- **WHEN** 批量名单包含曾移出当前学期、当前不在册的同学号学生
- **THEN** 预览标明恢复人数，提交复用原学生身份并以本次姓名、性别恢复在册


### Requirement: Owner-only reusable credential copying
系统 SHALL 按用户授权将班委密码散列与加密副本分别保存，允许所属班主任随时读取并复制完整登录表达，其他身份禁止读取。

#### Scenario: Copy on a later visit
- **WHEN** 班主任重新打开班级管理并选择复制班委登录信息
- **THEN** 以受CSRF保护的POST按需读取，组合产品/班级/完整用户名/密码复制到剪贴板；账号列表和普通页面响应不包含密码，专用响应不缓存

#### Scenario: Legacy password is unavailable
- **WHEN** 账号仅有旧不可逆密码散列
- **THEN** 提示先重置一次，不能反推密码；新建或重置后可随时复制

#### Scenario: Credential binding and audit
- **WHEN** 密文损坏、密钥错误或复制到别的账号数据行
- **THEN** 拒绝取回；成功读取只记录账号和读取事实，不记录密码，不声称剪贴板必然成功
