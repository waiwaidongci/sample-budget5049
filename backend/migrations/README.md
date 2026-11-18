# Schema 版本说明

## v1（初始）
由 SQLAlchemy 模型定义，启动时 `Base.metadata.create_all` 建表。本地小型应用不执行破坏性迁移，改动前请备份 db-data 卷。

## v2（口径迁移增量，2026-11）
新增能力：分析口径注册表 `method_versions`、存量迁移批次 `migration_batches`、迁移明细 `migration_runs`，并为 `runs` 增加 `method` 列（运行冻结口径）。

- 新表由 `Base.metadata.create_all` 自动创建。
- 对已存在的旧库，`src/main.py` 的 `ensure_schema()` 幂等执行
  `ALTER TABLE runs ADD COLUMN method VARCHAR(40) NOT NULL DEFAULT 'v1-zapprox'`，
  历史运行自动回填为正态近似口径，无需停机导出。
- 回滚应用代码不会删除新表/新列；新列带默认值，旧代码读取时忽略该列。
- 口径标识：`v1-zapprox`（双侧正态近似，历史）、`v2-fisher2`（双侧 Fisher 精确检验）。
