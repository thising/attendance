# ams.unzip.work 部署契约

本目录保存 `ams.unzip.work` 独立虚拟主机、Gunicorn 服务和北京时间 00:01／04:00 报告任务的实际配置。目标机是 `ali-ecs-hangzhou` 所指的 unzip.work 阿里云源站；代码发布到 `/opt/duxing/releases/<commit>`，`/opt/duxing/current` 是生效版本，虚拟环境位于 `/opt/duxing/venv`，私有数据库位于 `/var/lib/duxing/managedb.sqlite3`。应用仅监听 `127.0.0.1:8765`，由 Nginx 的独立 `ams.unzip.work` 站点转发。

`/etc/duxing/ams.env` 由目标机 root 持有、权限 0600，至少包含 `DUXING_DEBUG=0`、`DUXING_MIGRATION_LINEAGE=production-2026`、`DUXING_DATABASE=/var/lib/duxing/managedb.sqlite3`、`DUXING_ALLOWED_HOSTS=ams.unzip.work`、`DUXING_PUBLIC_BASE_URL=https://ams.unzip.work`、`TZ=Asia/Shanghai` 和各自独立的 `DUXING_SECRET_KEY` 与 `DUXING_CREDENTIAL_KEY`。旧库时间使用本地墙上时钟，应用和定时命令必须同用北京时间。密钥不进入 Git、公开目录或日志。SQLite 库、环境文件和匹配的应用版本要成套备份与恢复；新密钥丢失会使既有班委密码副本不可解密。

本次来源是 2026-09-24 17:04 北京时间从 VPS207 `managedb.sqlite3` 取得的只读快照，SHA-256 `85351d80a1ca99eb89dcb8d75d3a90e161258824e8835295b0b4709ae73e3686`。先在本地独立副本运行 `tools/rehearse_production_upgrade.py`，确认五表事实、非零违纪哨兵、当前学期基线、预生成报告和旧谱系恢复均通过，才传输升级结果。导入的是此快照时点的 5 班、243 人、64 活动、155 个人记录；旧站此后新增业务不会自动同步。

上线前先把原始来源快照和升级后的库另存到仅 root 可读的目标机备份目录，核对摘要，再启用应用；Nginx 新站配置用 `nginx -t` 验证后平滑重载。应用和两个任务的运行用户均为 `duxing`，只有 `/var/lib/duxing` 可写。公网、源站回环、登录页、静态资源、权限与报表检查必须在启用后完成，现有 `unzip.work`、`grotto.unzip.work`、`base.unzip.work`、`kimi.unzip.work` 的响应需前后对照。

若新站首次发布失败，先禁用并停止 `duxing-ams.service` 和两个定时器，将 `/etc/nginx/sites-enabled/zz-ams.unzip.work` 移走并重载已验证的 Nginx 配置；既有站点保持不变。若新站已产生业务写入，先将当前数据库、环境文件和代码版本另存，再决定恢复或人工合并，不能直接以旧快照覆盖新写入。目标机没有此应用的既有发布可回退；旧 VPS207 网站是独立服务，不在本次部署中变更。
