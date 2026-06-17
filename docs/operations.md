# 运维手册

本文档用于把员工建议聚类项目落地成每日可运行、可检查、可恢复的批处理任务。

## 部署前检查

1. 确认运行账号可以访问项目目录、MySQL 源库和 SQLite 分析库目录。
2. 安装依赖：

```powershell
python -m pip install -r requirements.txt
```

3. 复制 `config/mysql.example.json` 为生产私有配置文件，并确认 `field_mapping` 与小程序 MySQL 表字段一致。建议命名为 `config/mysql.prod.json`；`config/*.prod.json` 和 `config/*.local.json` 默认会被 Git 忽略。
4. 在运行账号下配置 `MINI_PROGRAM_DB_PASSWORD` 环境变量，密码不要写入 Git 或配置文件。
5. 初始化分析库：

```powershell
python -m src.suggestion_pipeline init-db --db data/analysis.db
```

6. 运行本地预检，确认配置、字段映射、密码环境变量和分析库可用：

```powershell
python -m src.suggestion_pipeline doctor --config config\mysql.prod.json --db data\analysis.db
```

预检返回 `success` 才进入任务计划配置；如果返回 `failed`，先按 `issues` 修复。`field_mapping_complete` 为 `false` 时，通常表示小程序 MySQL 字段映射缺少 `suggestion_id`、`raw_text` 等必要字段。

## 每日运行

Windows 任务计划程序建议调用脚本入口：

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts/run_daily_mysql.ps1 -ProjectRoot D:\PyWorkspace\content_fenlei -ConfigPath config\mysql.prod.json -DbPath data\analysis.db -LogDir logs -Limit 10000
```

脚本内部会调用：

```powershell
python -m src.suggestion_pipeline run-daily-mysql --config config\mysql.prod.json --db data\analysis.db --log-dir logs --limit 10000
```

任务成功时退出码为 `0`；失败时退出码为 `1`，并在 `logs` 目录写入 `daily-mysql-*.json` 日志。

## 运行后检查

查看最近导入批次、最新成功游标和核心表数量：

```powershell
python -m src.suggestion_pipeline status --db data/analysis.db --source mysql
```

重点检查字段：

- `latest_successful_cursor`：下一次自动增量导入会从这个游标之后开始。
- `latest_batch.status`：`success` 表示最近批次全部成功，`partial` 表示有失败行。
- `latest_batch.rows_read`：本次读取源库行数。
- `latest_batch.rows_created`：新增或刷新分析的行数。
- `latest_batch.rows_skipped`：源数据未变化而跳过的行数。
- `latest_batch.rows_failed`：失败行数，应优先排查。
- `table_counts.source_suggestions`：本地已保存源建议总数。
- `table_counts.issue_clusters`：当前问题簇总数。

## 失败处理

1. 先查看 Windows 任务计划程序的最近运行结果，确认是否为非 0 退出码。
2. 打开最新的 `logs/daily-mysql-*.json`，查看 `status` 和 `error`。
3. 如果错误与密码有关，确认运行账号下存在 `MINI_PROGRAM_DB_PASSWORD`。
4. 如果错误与 MySQL 字段有关，核对生产配置中的 `field_mapping` 和 `cursor_field`。
5. 如果 `rows_failed` 大于 0，查看 `latest_batch.error_summary`，确认是否存在空文本、缺失 ID 或字段格式异常。
6. 修复源数据或配置后，重新运行每日任务脚本。

## 补数和恢复

正常情况下不要手工传游标，系统会使用 `latest_successful_cursor` 自动续跑。需要补数或故障恢复时，可以显式指定 `--cursor`：

```powershell
python -m src.suggestion_pipeline run-daily-mysql --config config\mysql.prod.json --db data\analysis.db --log-dir logs --cursor 12345 --limit 10000
```

建议做法：

1. 先运行 `status --db data/analysis.db --source mysql` 记录当前 `latest_successful_cursor`。
2. 选择补数起点，传入比目标数据更早的 `--cursor`。
3. 补数完成后再次运行 `status`，确认最新成功游标、`rows_failed` 和核心表数量。
4. 如果补数产生重复数据，系统会按 `source_suggestion_id` 幂等跳过或刷新，不会重复创建源建议。

## 日常维护节奏

- 每天确认任务计划结果为成功。
- 每天检查 `status` 输出中的 `rows_failed` 是否为 `0`。
- 每周抽查 `review_tasks` 和低置信度聚类，避免相似问题被错误合并。
- 每月备份 `data/analysis.db` 和 `logs` 目录。

执行备份：

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts/backup_analysis.ps1 -ProjectRoot D:\PyWorkspace\content_fenlei -DbPath data\analysis.db -LogDir logs -BackupRoot backups -RetentionDays 90
```

备份会在 `backups/yyyyMMdd-HHmmss/` 下保存 `analysis.db` 和 `logs`，并默认清理超过 `RetentionDays` 天的旧备份目录。需要暂时关闭自动清理时，可以传入 `-RetentionDays 0`。

恢复时先停止每日任务，再把备份目录里的 `analysis.db` 复制回 `data/analysis.db`，随后运行：

```powershell
python -m src.suggestion_pipeline status --db data/analysis.db --source mysql
```

确认 `latest_successful_cursor` 和核心表数量符合预期后，再恢复每日任务。
