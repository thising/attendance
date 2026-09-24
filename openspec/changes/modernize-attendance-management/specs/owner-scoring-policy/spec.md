## ADDED Requirements

### Requirement: Owner-wide configurable weights
系统 SHALL 允许负责人设置九类加减分权重和月度基础分、下限、可选上限，并对其所有班级使用同一当前学期规则。

#### Scenario: Updating lateness weight
- **WHEN** 负责人将迟到扣分从1调整为2并成功保存
- **THEN** 其所有班级当前学期按新权重计算，其他负责人数据不受影响

### Requirement: Immutable versions and semester binding
系统 SHALL 为每次规则修改建立不可变版本，并用负责人/学期绑定决定规则，不根据访问时最新默认解释历史。

#### Scenario: Past semester unaffected by new weights
- **WHEN** 2026秋季修改评分权重
- **THEN** 2026春季继续使用原绑定版本，未来学期继承有效默认版本

#### Scenario: Closed semester facts cannot be corrected
- **WHEN** 2026秋季尝试修正2026春季的一次迟到
- **THEN** 拒绝修改，2026春季事实、绑定规则和成绩保持不变

#### Scenario: First access to an unused past semester
- **WHEN** 某学期从未被访问或创建绑定，且在以后的学期才首次查看
- **THEN** 根据带生效学期和提交时间的规则版本历史还原当期规则，不能使用访问时最新默认

### Requirement: Immediate consistent scoring
系统 SHALL 在规则提交成功后使后续汇总读取使用新版本，一次响应不得混用新旧规则。

#### Scenario: Policy update during report generation
- **WHEN** 规则更新与班级报表读取并发
- **THEN** 单份报表使用一个明确版本，提交后新发起请求使用最新当前学期版本

### Requirement: Scoring arithmetic and scope preview
系统 SHALL 将每月基础分/下限/可选上限、九项权重和学期平均分显示位数一并版本化。新设置的分值范围为0–999999.5且以0.5分递增，平均分显示位数为0–4位、默认2位；旧版本的小数分值仍按原值只读计算。月度默认60/0/无上限，下限≤基础≤上限；先逐月截断再求平均，平均分显示位数不改变月分或业务事实，并在保存前展示个人分差及从当前学期开始生效、历史不受影响的范围。

#### Scenario: Fractional weights and zero events
- **WHEN** 设置0或0.5分倍数的权重，且某月没有事件
- **THEN** 权重合法计算，空月为该学期基础分，学期分数按完整纳入月份平均并统一舍入展示

#### Scenario: Average display precision belongs to the policy version
- **WHEN** 当前学期将平均分显示位数改为3位，并在之后改成1位
- **THEN** 每次新读取的当前学期平均分按当期版本显示，月分始终保留两位；历史学期仍按旧版本原有位数显示，默认2位

#### Scenario: Half-point input is enforced on preview and save
- **WHEN** 新规则提交1.25分或超出范围的月度分值、权重，或提交0–4以外的显示位数
- **THEN** 服务端拒绝预览和保存，不创建评分版本、概要日志或成功回执


#### Scenario: Configured monthly bounds
- **WHEN** 设置基础75、下限70、上限80，某月原始分低于70或高于80
- **THEN** 该月分别为70或80，再按纳入月份平均；无事件月为75，历史学期保留原配置

#### Scenario: Omitted monthly settings on an existing client
- **WHEN** 旧客户端只提交九类权重且省略monthly
- **THEN** 保留当前有效的月度配置，不静默重置为默认

#### Scenario: Invalid bounds
- **WHEN** 下限大于基础、上限小于基础、超出值域或不是0.5分的倍数
- **THEN** 拒绝预览/保存，不留下新版本或成功日志
