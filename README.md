# 笃行 · 学生操行管理系统

面向班级日常管理，记录考勤、活动加分与违纪扣分。界面采用纸白、墨色、朱砂和竹节标志；快速点名直接选择状态，录入后立即计分。

当前在 [ams.unzip.work](https://ams.unzip.work/) 以独立服务上线，实际部署代码为 `0e0c099`。唯一生效的改造依据是 [OpenSpec 提案](openspec/changes/modernize-attendance-management/proposal.md)，实际发布范围、数据时点与回滚见 [发布记录](openspec/changes/modernize-attendance-management/release.md)。VPS207 旧站按用户要求继续运行，使用者需只在新站录入，两个站点不会自动同步。

2026-09-24 17:04（北京时间）从 VPS207 的 `managedb.sqlite3` 只读取得固定来源快照：5 班、243 人、64 条活动、155 条学生报告，243 人九类次数及分数与源缓存逐项一致。用户明确选择这份已同步快照作为本次上线数据；17:04 后旧站的新记录不会自动带入。SHA-256 与隔离迁移演练见 [实施验收记录](openspec/changes/modernize-attendance-management/implementation-validation.md)。本地 [8005 审查副本](http://127.0.0.1:8005/?term=2026-autumn)仍展示同一取样。正式站通过独立生产迁移谱系在**副本**上升级后导入，不曾对 VPS207 运行旧 `0002` 或修改其数据库。下方开发初始化命令仅用于空开发库，不适用于生产来源。

## 本地运行

公开报告使用数据库预生成快照，业务提交后更新；新站已配置北京时间 00:01 月初边界刷新和 04:00 一致性校验。目标机路径、运行环境和回滚边界见 [部署契约](deploy/README.md) 与 [发布记录](openspec/changes/modernize-attendance-management/release.md)。
每日 04:20 在服务器本机另存一份私有数据库与匹配环境文件；首次备份和隔离恢复已核验。异地备份尚待指定目标。

运行基线：Python 3.12、Django 5.2；依赖以 `requirements.txt` / `requirements-dev.txt` 为准。已有个人 `Pipfile` 文件保留，不作为本版本运行依据。

```sh
python3.12 -m venv .venv-duxing
.venv-duxing/bin/python -m pip install -r requirements-dev.txt
mkdir -p .local
.venv-duxing/bin/python manage.py migrate
.venv-duxing/bin/python manage.py seed_demo
.venv-duxing/bin/python manage.py runserver 127.0.0.1:8001
```

打开 <http://127.0.0.1:8001/>。`seed_demo` 仅支持空的默认开发库，生成虚构学生与随机演示密码；登录资料在本地 `.local/demo-access.json`，不要提交或外发。

默认配置 `attendance.runtime_settings` 使用独立的 `.local/development.sqlite3`，不导入旧 `attendance/settings.py`，不连接原业务库。仅在已核对目标和备份后设置 `DUXING_DATABASE`。正式环境还必须提供 `DUXING_SECRET_KEY`、`DUXING_CREDENTIAL_KEY`、`DUXING_DEBUG=0`、`DUXING_ALLOWED_HOSTS` 和 HTTPS；部署步骤须根据实际目标另行确定。

`DUXING_CREDENTIAL_KEY`是班委密码复制功能的独立Fernet密钥，不能与应用SECRET_KEY混用。开发模式首次启动会原子创建权限600的`.local/credential.key`；须与数据库配套备份且不得提交到Git。遗失密钥会导致已有密码副本无法解密，需要重置相关班委密码。升级前只有密码散列的旧账号也须重置一次，此后可随时复制。

## 已实现的主流程

- 首页提供班主任与班委双登录，默认班主任；每班最多 5 个启用的独立班委账号，固定绑定一个班级、登录有效 3 小时，停用/改密立即撤销旧会话。用户名由班级固定4位hash前缀和短名组成；班主任可随时一键复制产品、班级、完整用户名、密码及使用说明。
- 春季 2–7 月，秋季 9–次年 1 月；8 月业务只读，已结束学期禁止修改。
- 活动有可选详情；旧名称保持完整。历史学期首次读取时固定有依据的名单、规则、分数与记录快照，之后只看快照；缺少可信历史记录的归档提示待核实。
- 按已进入自然月计分；负责人可配置月度基础、下限、可选上限和九项权重，预览后即时作用于本人所有班级的当前学期，未来继承、历史规则保留。默认60/0/无上限。
- 当前学期新增学生；原 Excel 模板下载、直接上传或三列粘贴、校验预览后整批提交。事务、提交幂等、版本冲突与具体班委账号日志。
- 工作台默认展示全部名下班级、全体学生的九类次数与个人学期分数；班级卡片显示所选学期人数、累计活动和最近录入时间，不展示班均分。本月/上月全班明细关联个人当月记录；历史学期展示期末两个月。
- 系统与班级导航分区，非零扣分朱砂、奖励青绿、零值弱化，分数标记相对当期基础值。桌面及手机界面，按钮式点名、撤销、草稿、断网重试与授权恢复。

[OpenAPI 合同和调用样例](api/README.md)目前只是未启用草案；按最新要求，Agent合同定稿和接入在本版本上线之后再实施。

## 验证与旧库边界

```sh
.venv-duxing/bin/python manage.py check
.venv-duxing/bin/python manage.py makemigrations --check --dry-run
.venv-duxing/bin/python manage.py test manage --noinput
.venv-duxing/bin/python api/examples/validate_contract.py
node --check static/workspace/workspace.js
```

最新生产取样仍只有2026年9月当前学期证据，当前学期导入经名单、逐字段和逐人成绩验证后完成；没有补造历史学期。R1另一份本机2023年旧库的历史名单和成绩仍需确认，其`legacy_pending`限制不变。两者的迁移谱系不同；生产迁移风险、历史证据缺口及可执行回滚方式见 [发布准备](openspec/changes/modernize-attendance-management/release.md)。不要将“副本导入通过”视为已允许切换真实系统。

原项目为作者学习 Django 时建立的高校考勤系统；本次保留其业务数据表并替换权限、计分和界面实现。
