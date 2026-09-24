# 笃行 · Agent 兼容合同

**交付状态：仅合同与离线合成测试，全部 `/api/v1` 机器端点尚未启用。** 没有创建机器凭据、注册路由或连接外部服务。`attendance.invalid` 是不可部署的占位地址，不应拿样例向实际站点发送请求。浏览器已经可用的 Session 页面不等于机器 API。

权威业务依据是 [OpenSpec Agent 合同](../openspec/changes/modernize-attendance-management/specs/agent-contract/spec.md)，并共同遵守 [班级权限](../openspec/changes/modernize-attendance-management/specs/class-access/spec.md)、[学期计分](../openspec/changes/modernize-attendance-management/specs/term-scoring/spec.md)和[负责人规则](../openspec/changes/modernize-attendance-management/specs/owner-scoring-policy/spec.md)。旧讨论中允许改历史、8 月改设置等约定不适用于本合同。

## 文件与验证

- [openapi.yaml](openapi.yaml)：OpenAPI 3.1 合同，每个操作都标记 `x-implementation-status: not-enabled`。
- [examples/](examples/)：仅含虚构班级、学生、ID 和占位授权头的 JSON 调用/响应，不含真实 token。
- [examples/validate_contract.py](examples/validate_contract.py)：使用 PyYAML 和 Python 标准库，检查内部引用、路径/参数、安全声明、全部合成请求/响应、已使用的 schema 约束及关键业务不变量。

在项目根目录运行：

```sh
.venv-duxing/bin/python api/examples/validate_contract.py
```

验证脚本不导入 Django、不访问数据库、不启动服务器、不调用网络，也不安装依赖。它是本合同使用的 JSON Schema 子集校验器及离线约束测试，不是通用 OpenAPI 认证工具，更不代表真实 API、并发、凭据撤销或服务端权限已验收。启用机器接口前还须用具体框架的 OpenAPI 校验和真实 HTTP 集成测试核验。

## 与浏览器业务服务的衔接

未来 HTTP 适配器仅完成 Bearer 凭据解析、严格 JSON 校验、请求上下文建立和 HTTP 响应映射，然后调用同一套 `manage.services` 业务逻辑；不能复制计分代码、直接操作 ORM 绕过服务或转发网页表单来冒充 API。

| 合同概念 | 服务衔接与启用时的门槛 |
| --- | --- |
| 委托机器身份 | 服务器从可撤销、可过期凭据解析负责人、班级和动作，不接受请求正文的 owner 字段；班委不是机器身份 |
| 稳定 ID | 把数据库身份序列化为稳定字符串；姓名、学号和访问码不作为授权或资源 ID |
| `term_key` / `date` | 复用上海日期及学期服务；只允许当前学期且不晚于今天的业务日期 |
| `expected_current_term_key` | 来自前次响应 `meta.current_term_key`，用于分辨旧表单跨学期与明确写历史；这是未来适配器需增加的上下文校验 |
| `roster_revision` | 读取与写入使用同一完整学期名单；未来适配器须暴露并校验名单版本，不能假定遗漏学生表示正常 |
| `revision` | 修改回传上次读取的记录版本；冲突时读取最新记录，不能静默覆盖 |
| `Idempotency-Key` | 传入业务层的稳定提交上下文；正文摘要、回执、事实、次数与成功日志一起提交 |
| `kind` / 学生 `value` | 转换为现有考勤/活动/违纪报告字段，`normal` 分别对应正常、未参加或无违纪；不改变业务含义 |
| `result` / `error` | 将业务服务结果和明确错误映射为稳定结构；不能直接暴露异常、凭据或未知字段 |
| 分页与规则版本 | 使用统一选择器；同一页和分页链保持同一数据/名单/规则快照，变化则返回 `pagination_stale` |
| 历史归档 | 返回归档名单、事实和原成绩；基线缺失返回明确错误，不用现行名单、新权重或算法猜测 |
| 月度分值 | API保留`base_score/minimum_score/maximum_score`，对应页面服务`policy.monthly.base/minimum/maximum`；`maximum_score=null`表示无上限。页面旧请求省略`monthly`时保留当前配置；本API仍只读规则 |

本次合同允许定义服务尚未提供的机器身份、名单版本、稳定游标和 HTTP 错误映射，但不宣称这些已实现。任何启用必须完成该表中的适配与测试。

## 身份与权限

负责人账号可以拥有多个班级；机器凭据是该负责人明确委托的独立身份，绑定允许班级和动作，默认只读，有到期时间且可即时撤销。服务器只保存凭据散列；签发、轮换、撤销方式留待实际接入阶段确定，本合同不提供凭据管理路由。

读取 scope 为 `classes:read`、`students:read`、`records:read`、`reports:read`、`scoring:read`；普通写入分为 `records:create` 和 `records:update`，不能由读取 scope 自动获得。权限必须同时满足负责人当前归属、凭据班级范围和操作 scope。班委使用班主任指定的单班独立账号，最多5个启用账号，固定3小时浏览器登录；这些页面账号不作为机器凭据。旧共享密码入口已停用。

每次请求与成功幂等重放都重新验证身份和范围；已撤销或过期 token 返回 401。显式越出凭据允许班级返回 403；在获授权班级内找不到记录返回 404，不能泄露其他班级是否存在该记录。身份合法、scope 足够也不能改历史学期或突破 8 月业务只读。

审计标记机器身份、委托负责人、班级、操作、目标/版本、影响摘要、来源及非认证的请求 ID，不记录 Bearer、共享密码、Cookie、原始 Session ID 或完整学生名单。成功变更与概要日志同事务；重放不再追加成功日志。跨班拒绝和故障写独立诊断记录，不伪装成成功业务事件。

## 日期、记录与计分

- `YYYY-spring`：当年 2 月 1 日起，8 月 1 日前，共 6 个月；`YYYY-autumn`：当年 9 月 1 日起，次年 2 月 1 日前，共 5 个月。
- 当前学期只纳入已进入自然月，未发生活动的月份使用该学期月度基础分（默认60分）；未来月份不提前计入。8 月 `meta.current_term_key` 为 `null`，业务数据、名单与规则全部只读。
- 已结束学期名单、记录、审核标记、规则与成绩全部只读，不提供解锁接口；当前学期可改此前月份，不能把记录移入或移出历史。
- `class` 允许 `normal/late/absent/leave`；`activity` 允许 `normal/low/mid/high`；`discipline` 允许 `normal/dlow/dmid/dhigh`。
- 录入后立即计分。响应保留 `audit_status: preview/release`，机器输入不能改变该标记。
- 新增和整单修改须传对应名单版本的完整学生数组，含 `normal`；每 ID 恰好一次，拒绝遗漏、重复和跨班学生。单条记录上限 1000 人，分页每页 1–100 人，客户端先读完全部名单再构建一条写请求；不是分批保存同一条记录。
- 九类权重、月度基础分和最低分全部是0–999999.99范围内的非负、两位小数**字符串**，例如 `"1.50"`；最高分使用同一范围或`null`表示无上限。加扣方向由类别决定；`minimum_score <= base_score <= maximum_score`，最后一个比较仅在有上限时适用。默认60/0/null。先逐月按上下限截断，再求平均并采用两位四舍五入；最终无上限的月度得分不受配置字段的999999.99上限限制，不截断或转浮点。
- 当前基础分、上下限或权重更新影响同负责人所有班级当前及未来学期，历史保持原版本和归档依据。本合同只定义规则读取；不会因新增一个 `scoring:read` scope 开放规则修改。样例25展示基础75、最低70、最高80的两个月独立截断与平均。

## 幂等、版本、分页和错误

每个逻辑写操作生成 UUID v4 格式 `Idempotency-Key`，作用域包含稳定委托身份、班级与操作/目标。验证后的字段形成规范摘要，学生数组按 ID 规范排序。相同键相同内容返回原回执；相同键不同内容返回 409。新一次真实点名可以与旧记录内容相同，但须使用新键。

响应丢失或状态不确定时，用**原键、原内容**重试，不新建键来“再保存一次”。成功返回 `result` 回执，客户端可另行 GET 最新详情；若原结果后来被合法删除，重放返回 `state: deleted`，不能重建。成功提交的去重依据须保留，不能以短 TTL 清理后再次执行原键。凭据轮换须保持相同委托身份的去重作用域；撤销本身不授予重放权。

修改请求另外回传 `revision` 和 `roster_revision`。失败时全部回滚；失败不产生成功审计。正常读取的 `meta.replayed` 为 false，写成功重放为 true。仅在重新读取与核对之后，才把变更后的内容作为新的逻辑提交。

分页使用不透明 `cursor`，绑定授权、过滤条件、limit 和数据快照。客户端沿用 `next_cursor` 直到 null；不解析游标，不按页数猜测。分页中名单、数据或规则改变时返回 409，客户端从第一页开始新的快照。每份汇总明确带学期、规则/算法版本和名单版本，不能在一个结果中混用旧规则。

| HTTP | 常见错误码 | 客户端处理 |
| --- | --- | --- |
| 400 | `validation_error`、`invalid_record_date`、`invalid_term` | 显示结构化 `fields`，修正并重新核对 |
| 401 | `unauthenticated`、`credential_expired`、`credential_revoked` | 停止写入，重新取得合法授权；无凭据回显 |
| 403 | `class_scope_denied`、`scope_denied`、`term_read_only`、`august_read_only` | 不重试越权或只读操作，写 scope 不能解锁 |
| 404 | `not_found` | 获授权范围内不存在目标 |
| 409 | `idempotency_conflict`、`revision_conflict`、`roster_conflict`、`context_expired`、`pagination_stale` | 重新读取并核对；不能仅换键强行覆盖 |
| 409 | `historical_snapshot_unavailable` | 保留历史，不生成猜测结果；需要独立数据审查 |
| 503 | `database_busy`、`temporarily_unavailable` | 遵循 `Retry-After` 有界退避；原键原内容重试 |

明确目标是历史且当前上下文有效时返回 `term_read_only` 403；请求带的 `expected_current_term_key` 已过期时返回 `context_expired` 409。8 月业务写入统一返回 403。只有暂时性 503 的 `retryable` 为 true；其他错误即使可以由用户修正，也不表示可自动重放。

## 后续启用门槛

先选择具体 Agent 与部署认证方案，再实现少量只读路由、权限矩阵和契约集成测试。普通写入须通过真实并发、去重、事务回滚、跨学期、权限撤销及审计验证后才可开放。

删除、批量多记录修改与评分权重修改须另行完善预览/提交合同，短期预览凭据绑定操作者、目标、变更摘要和规则/记录/名单版本；目标变化、预览过期或学期关闭后拒绝提交。是否要求负责人逐次人工确认由明确授权策略决定。本文件不虚构已经存在的预览或危险操作端点。

后续 MCP 只作为同一 API/业务服务的薄适配；远程 MCP 再按其适用授权标准接入，不能把此处的长期 Bearer 占位方案当作通用 MCP 授权。全量清空、删除班级、转移负责人和历史解锁不在首版机器接口范围。
