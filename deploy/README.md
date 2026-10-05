# ams.unzip.work 部署契约

本目录保存 `ams.unzip.work` 独立虚拟主机、Gunicorn 服务和北京时间 00:01／04:00 报告任务的实际配置。目标机是 `ali-ecs-hangzhou` 所指的 unzip.work 阿里云源站；代码发布到 `/opt/duxing/releases/<commit>`，`/opt/duxing/current` 是生效版本，虚拟环境位于 `/opt/duxing/venv`，私有数据库位于 `/var/lib/duxing/managedb.sqlite3`。应用仅监听 `127.0.0.1:8765`，由 Nginx 的独立 `ams.unzip.work` 站点转发。

`/etc/duxing/ams.env` 由目标机 root 持有、权限 0600，至少包含 `DUXING_DEBUG=0`、`DUXING_MIGRATION_LINEAGE=production-2026`、`DUXING_DATABASE=/var/lib/duxing/managedb.sqlite3`、`DUXING_ALLOWED_HOSTS=ams.unzip.work`、`DUXING_PUBLIC_BASE_URL=https://ams.unzip.work`、`TZ=Asia/Shanghai` 和各自独立的 `DUXING_SECRET_KEY` 与 `DUXING_CREDENTIAL_KEY`。旧库时间使用本地墙上时钟，应用和定时命令必须同用北京时间。密钥不进入 Git、公开目录或日志。SQLite 库、环境文件和匹配的应用版本要成套备份与恢复；新密钥丢失会使既有班委密码副本不可解密。

本次来源是 2026-09-24 17:04 北京时间从 VPS207 `managedb.sqlite3` 取得的只读快照，SHA-256 `85351d80a1ca99eb89dcb8d75d3a90e161258824e8835295b0b4709ae73e3686`。先在本地独立副本运行 `tools/rehearse_production_upgrade.py`，确认五表事实、非零违纪哨兵、当前学期基线、预生成报告和旧谱系恢复均通过，才传输升级结果。导入的是此快照时点的 5 班、243 人、64 活动、155 个人记录；旧站此后新增业务不会自动同步。

上线前先把原始来源快照和升级后的库另存到仅 root 可读的目标机备份目录，核对摘要，再启用应用；Nginx 新站配置用 `nginx -t` 验证后平滑重载。应用和两个任务的运行用户均为 `duxing`，只有 `/var/lib/duxing` 可写。公网、源站回环、登录页、静态资源、权限与报表检查必须在启用后完成，现有 `unzip.work`、`grotto.unzip.work`、`base.unzip.work`、`kimi.unzip.work` 的响应需前后对照。

每日北京时间 04:20 的 `duxing-backup.timer` 以 root 身份在 `/var/backups/duxing/` 新建仅 root 可读的目录，用 SQLite 在线备份数据库并配对当时环境文件；完整性、外键和环境稳定性验证通过后才写 `manifest.json`。每次新建，不覆盖或自动删除旧备份。本机备份能应对误改或单文件损坏，**不能代替异地备份或整机灾难恢复**；异地目标已确认为家中 NAS；R15 提供的复制工具与任务模板尚未安装，不能据此声称已有异地副本。

若新站首次发布失败，先禁用并停止 `duxing-ams.service` 和两个定时器，将 `/etc/nginx/sites-enabled/zz-ams.unzip.work` 移走并重载已验证的 Nginx 配置；既有站点保持不变。若新站已产生业务写入，先将当前数据库、环境文件和代码版本另存，再决定恢复或人工合并，不能直接以旧快照覆盖新写入。此段首次部署步骤仅供历史追溯；当前已发布 R14，匹配代码/数据库/环境文件的回滚位置以 OpenSpec 发布记录为准。旧 VPS207 网站是独立服务，不能代替新站上线后的业务数据回滚。


## R15 统一 NAS 备份链路

2026-10-05 用户授权固定版本、部署 unzip 并整合备份，随后要求沿用 NAS 其他项目的统一思路。当前安装方案复用托管日记已验收的 rsync 接收、NAS finalizer、来源网关和 append-only Rest Server 镜像；笃行拥有独立目录、仓库、凭据、状态和容器，不更改其他项目的数据与调度。旧 SFTP/挂载目录复制工具保留为可选客户环境适配，不是家中 NAS 的生效任务。

| 北京时间 | 有效任务 | 内容 |
| --- | --- | --- |
| 每日 04:20 | duxing-backup.timer | SQLite 在线快照 + 匹配 ams.env + manifest v2，保留旧备份 |
| 每日 11:00、18:00、23:00 | duxing-offsite-backup.timer | 新本机恢复点的可读业务档案及加密系统包；已有同一恢复点成功则跳过 |
| 每月 1 日 11:30 | duxing-offsite-check.timer | 可读历史全量回读和 Restic 数据抽样；不删除历史 |

NAS 02:00–10:30 关机；上述复制窗口错开已有日记补偿任务。可读档案包含允许的业务事实 JSON、预生成成绩 JSON 和 UTF-8 CSV；不包含用户认证表、公开令牌、旧班级凭据或解密密钥。JSON/CSV 不对外公开，NAS 项目目录 0700、文件 0600。rsync 只可覆盖本项目 incoming，NAS finalizer 校验 SHA-256 后以硬链接保存历史并原子切换 current；历史模块只读。不得使用 --inplace 修改 incoming 的硬链接文件。

完整系统包每日使用客户端 Restic 加密，包含同一时点数据库/环境、固定版本源码包、项目配置、systemd 与 Nginx 配置。Rest Server 使用 private repositories + append-only；仓库解密口令不落到 NAS，NAS 接收认证与仓库口令分离。本机 /etc/duxing/nas-backup.json 只保存配置和私有口令文件引用，地址/端口/目录可按客户环境替换。所有恢复先在独立目录核对摘要、SQLite、迁移账本和源码包，再验证应用；不能覆盖线上新增数据。

NAS 接收端配置在 nas-offsite/，镜像由安装时核验的既有内容 ID 固定；端口只绑定 Tailnet，rsync 白名单和 Rest 网关仅允许 unzip Tailnet 来源。四个容器均以 1003:100 运行、根文件系统只读、capabilities 全部丢弃、不挂 Docker socket，只有本项目接收/档案/仓库/日志卷。停用或回滚只停止本项目容器与 timer，保留已生成历史；真实整机开关机周期仍作为运行观察。

nas_backup.py 共用互斥锁，数据生成时点超过 48 小时、NAS 可用空间低于 20%、容量标记过期、传输/校验/恢复失败均拒绝成功；记录最新尝试与数据恢复时点，不用近期重验旧包冒充新备份。可读档案须确认 NAS 归档并从只读历史回读；系统包须从本次准确 Restic snapshot 恢复并比对数据库、密钥环境和代码。两条链路都成功后才写成功状态。使用用户确认的 backup@unzip.work 邮件渠道发送失败/超期与恢复通知，只含项目、状态和时间；SMTP 失败另记本机状态，VPS 整机失联仍无法发信。

新版 backup_sqlite.py 的 manifest 记录 immutable release、迁移账本、数据库/环境摘要和带偏移的生成时间。DUXING_BACKUP_DATABASE/ENVIRONMENT/ROOT/RELEASE 支持替换本机路径；迁移窗口须暂停应用写入、报告和备份任务，防止代码/结构不配套。旧无代码身份的包不原地改写，保留为 legacy_pending；首 30 天不执行删除、forget、prune，之后须单独审查保留策略。

检查入口：

```sh
python manage.py check_release_readiness
python manage.py check_runtime_health --offsite-status /var/lib/duxing/backup-state/offsite.json --max-backup-age-hours 48
python /usr/local/libexec/duxing/nas_backup.py --config /etc/duxing/nas-backup.json --mode check
```

当前版本、实际备份位置、首次恢复、安装门禁和回滚以[发布记录](../openspec/changes/modernize-attendance-management/release.md)为准。仓库口令须另保存在本机私有恢复材料或密码管理器；NAS 上没有解密口令，不能把“仓库在线”当作口令也已安全保全。未来部署保留同样的契约和验收，按客户基础设施调整传输与调度。
