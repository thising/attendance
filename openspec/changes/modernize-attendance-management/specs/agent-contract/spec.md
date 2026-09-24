## ADDED Requirements

### Requirement: Shared business contract
系统 SHALL 为未来机器调用定义版本化HTTP接口合同，复用页面的业务服务与授权，不允许直接写数据库。

#### Scenario: Contract-only first release
- **WHEN** 首轮兼容准备完成
- **THEN** 提供OpenAPI合同、合成样例和测试，明确未启用的机器端点，不宣称Agent已可真实写入

### Requirement: Structured and retryable operations
接口合同 SHALL 定义稳定ID、分页、学期/规则/记录版本、字段错误、幂等键及冲突行为。

#### Scenario: Retried future API request
- **WHEN** Agent重试已经成功的请求
- **THEN** 遵循与网页相同的幂等语义，不产生重复业务记录

### Requirement: Scoped delegated identity
后续机器接入 SHALL 使用可过期、可撤销且限定负责人/班级/动作的凭据，默认只读，不复用班级管理密码。

#### Scenario: Forged owner field
- **WHEN** 调用者传入与凭据不符的owner或班级
- **THEN** 服务器根据实际授权拒绝越权，记录必要审计而不记录凭据内容

#### Scenario: Write scope cannot unlock a closed semester
- **WHEN** 具备合法写权限的Agent尝试修改已结束学期业务数据
- **THEN** 拒绝请求并保持历史数据不变，合同不提供历史解锁接口

### Requirement: Controlled high-impact changes
后续高影响写操作 SHALL 提供绑定目标与版本的预览/提交机制，具体人工确认规则由明确授权策略决定。

#### Scenario: Stale preview
- **WHEN** 预览后相关记录或规则版本已变化
- **THEN** 提交被拒绝并要求刷新预览，不静默应用到变化后的数据
