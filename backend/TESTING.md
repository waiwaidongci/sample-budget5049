# 实际验证记录（TESTING）

环境：本地 Python 3.11 虚拟环境（NumPy 2.2.1 / SciPy 1.15.1 / FastAPI 0.115 / SQLAlchemy 2.0），
SQLite 落库 + 内存假队列，未使用 Docker；全部用 `python -I` 隔离运行。日期 2026-11-18。

## 1. Fisher 双侧 p 值正确性（算法基石）
- 脚本：临时验证 `fisher_pvalue_vectorized` 与 `scipy.stats.fisher_exact(alternative='two-sided')` 对比。
- 随机 2×2 列联表 + n=12 全枚举 + 大 n 边界（含全 0、全成功、对称表），共 4990 个用例，
  最大绝对偏差 1.12e-15（浮点容差内零偏差）。
- 零假设（p0=p1=0.1，n=100）拒绝率低于 α，符合 Fisher 精确检验在离散小计数下的保守性。

## 2. 性能预算（B=10000）
- 单次 Fisher 功效模拟约 1.2–2.3 秒；样本量整数二分搜索约 6–21 秒/情景，满足 worker 同步计算。
- 例：p0=0.08→p1=0.10 时 Fisher n=3251，正态近似参考 n=1865（精确口径更保守，符合预期）。

## 3. 端到端服务层 `tests/e2e_migration.py`：46 项通过，0 失败
覆盖：默认口径与新研究绑定、影子重算隔离可见结果、迁移期并发排队迟归不串版、
获批研究排除出批、中断恢复只补 pending 且消费幂等不重复发布、单事务整批切换、
生效口径显式切换、回退恢复旧版且已获批/新版冻结项保留、核对失败整批保留旧版与放弃重建、
回退后重新迁移、导出 CSV 沿用冻结口径。

## 4. HTTP 链路 `tests/http_migration.py`：24 项通过，0 失败
覆盖：未认证 401、viewer/analyst 权限 403、未知口径 422、影子期重复发起 409、
重复切换/回退 409、批次与差异 JSON 契约、运行详情 method 字段、导出含口径列。

## 5. Worker 恢复与双队列（直接驱动 `src/worker.py`）
- 启动恢复：queued 与 dequeue 后宕机的 stale running 均重新入分析队列；pending 迁移项补入迁移队列。
- 优先级：先排空 analysis-queue 再处理 migration-queue；二次恢复对 completed/shadow_done 不重投。

## 6. Schema 兼容
- 旧库（runs 无 method 列）经 `ensure_schema()` 幂等 ADD COLUMN，历史行回填 `v1-zapprox`；
  全新库 `create_all` 建全部新表，bootstrap 幂等注册两个口径且仅 v1 默认生效。

## 未覆盖/边界
- 未在真实 PostgreSQL16 + Redis7 + Nginx 的 compose 环境联调（本机无 Docker 守护进程）；
  SQL 方言仅用通用类型与幂等 DDL，JSON/Boolean 在两库均受支持，建议上线前在 compose 跑一次冒烟。
- 单 worker 假设沿用原设计；多 worker 下迁移批次的串行切换需额外加分布式锁（当前已限制同时仅一个 shadow 批次）。
