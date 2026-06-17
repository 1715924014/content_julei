# 员工问题建议分类聚类与整改闭环工具

这是一个本地 CSV 分析工具，用于把员工原始建议整理成“原文不改、可分类、可聚类、可派单、可复盘”的整改闭环数据。

## 快速开始

生成收集模板：

```powershell
python -m src.suggestion_pipeline template --output data/suggestion_template.csv
```

分析样例数据：

```powershell
python -m src.suggestion_pipeline analyze --input examples/sample_suggestions.csv --output-dir output
```

运行测试：

```powershell
python -m unittest discover -s tests
```

## 输入字段

输入 CSV 至少应包含这些字段：

- `suggestion_id`
- `submit_date`
- `raw_text`
- `department`
- `job_group`
- `work_location`
- `scenario`
- `is_anonymous_for_report`
- `status`
- `owner_department`
- `resolution_note`
- `closed_date`

工具不会覆盖或改写 `raw_text`。所有分类、聚类、摘要、复核建议都会写入新的输出字段。

## 输出文件

分析后会生成：

- `suggestions_analyzed.csv`：逐条建议明细和分析字段
- `clusters.csv`：问题簇汇总、代表性原文、涉及部门和建议数
- `action_items.csv`：可派单整改事项
- `weekly_report.md`：管理层周报摘要

## 设计原则

- 原文保留：员工建议原文作为事实记录，不做润色。
- 分析匿名：报告中不展示姓名、工号等身份字段。
- 重点复核：高紧急度、高频、跨部门、低置信度、信息不足的问题进入人工复核。
- 闭环优先：聚类结果按问题簇生成整改事项，而不是只做静态统计。

## 工程化增量处理

初始化本地分析数据库：

```powershell
python -m src.suggestion_pipeline init-db --db output_run_check/analysis.db
```

部署前预检配置、字段映射、密码环境变量和分析库初始化：

```powershell
python -m src.suggestion_pipeline doctor --config config/mysql.example.json --db output_run_check/analysis.db
```

导入 CSV 到分析数据库：

```powershell
python -m src.suggestion_pipeline import-csv --input examples/sample_suggestions.csv --db output_run_check/analysis.db
```

当前版本使用 SQLite 作为本地开发和测试数据库。生产环境对接小程序 MySQL 时，应通过同一套 storage/repository 接口接入，避免改动分类、向量匹配和聚类业务逻辑。

从 MySQL 源表增量导入：

```powershell
$env:MINI_PROGRAM_DB_PASSWORD="your_password"
python -m src.suggestion_pipeline import-mysql --config config/mysql.example.json --db output_run_check/analysis.db --limit 1000
```

`config/mysql.example.json` 中的 `field_mapping` 用来把小程序表字段映射到分析管道需要的输入字段。生产环境请复制一份私有配置文件并修改连接信息，不要把真实密码写入配置文件。

MySQL 导入会自动读取上一批成功导入的 `cursor_end` 作为下一次的增量起点；首次导入没有历史游标时从空游标开始。需要补数或故障恢复时，可以显式传入 `--cursor 12345` 覆盖自动游标。

每日计划任务建议调用固定入口，它会执行 MySQL 增量导入并在 `logs/` 下写入 JSON 运行日志；失败时命令返回非 0，方便任务计划程序识别告警：

```powershell
python -m src.suggestion_pipeline run-daily-mysql --config config/mysql.example.json --db output_run_check/analysis.db --log-dir logs --limit 10000
```

Windows 任务计划程序可以直接调用脚本，先在运行账号下配置好 `MINI_PROGRAM_DB_PASSWORD` 环境变量：

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts/run_daily_mysql.ps1 -ProjectRoot D:\PyWorkspace\content_fenlei -ConfigPath config\mysql.example.json -DbPath data\analysis.db -LogDir logs -Limit 10000
```

查看最近导入批次、最新成功游标和核心表数量：

```powershell
python -m src.suggestion_pipeline status --db output_run_check/analysis.db --source mysql
```

备份分析库和运行日志：

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts/backup_analysis.ps1 -ProjectRoot D:\PyWorkspace\content_fenlei -DbPath data\analysis.db -LogDir logs -BackupRoot backups
```

部署检查、每日巡检、失败处理和补数恢复请参考 `docs/operations.md`。
