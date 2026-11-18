# 榆灯触达样本预算台

内容触达基线点击率→最小可检测差异与预算→预注册方案；单双侧语义固定双侧，功效模拟固定seed。原生 HTML/CSS/JavaScript 多模块工作席，所有计算由 Python 完成。

## 分析口径与存量迁移（v2）
低点击率情景下正态近似失真，新增 **双侧 Fisher 精确检验**口径（`v2-fisher2`）：固定边际超几何分布，双侧 p 值取所有概率不大于观测概率的质量点之和（与 `scipy.stats.fisher_exact(two-sided)` 一致，已用约 5000 组列联表核对）；样本量由固定 seed 的 Monte Carlo 功效整数二分反算。历史口径 `v1-zapprox`（双侧合并比例正态近似）保留且不可改写。

- 口径在 `method_versions` 注册表中维护，仅一个生效；负责人可在“口径迁移”页切换，切换只影响**此后新排队**的研究。
- 每条运行排队时绑定当时生效口径并写入 `runs.method` 永久留档，worker 严格按冻结口径计算，**迟归计算不混入另一版**；导出 CSV 带口径列。
- 未获批存量研究可整批迁移到 Fisher 口径：先**影子重算**（不触碰可见结果）并核对样本量、预算功效与留档（含产物 SHA256），再由负责人**一次切换**可见结果；任一项失败整批保留旧版，失败批次可放弃后重建。
- 迁移期间仍可并发排队；**获批研究及其下载沿用原口径，永不入批**。
- 中断后 worker 启动只把未完成项重新入队（补缺项），消费幂等保证重试不重复发布；回退恢复未获批运行的旧版，已按新版冻结（切换后获批或已被新在途计算接管）的研究仍按新版完成。
- 页面“口径迁移”可查看生效口径、每个批次的逐条新旧差异，并执行切换/回退；审计留痕 `method.switch`、`migration.*`、`run.queue/export`（均带口径）。

## 模拟业务
榆灯研究协作组，12名分析师与业务人员，日常使用 `sample-budget-202611-1.csv` 手工抄表。当前三个痛点：观测与口径不可追溯、重算易出错、结果与复核割裂。上游提供独立合成业务观测；本应用接管数据登记、方法执行和输入冻结；下游负责人形成复测/预算方案，观察员获取复核后的CSV。

核心业务实体：CampaignPlan / BaselineScenario / PowerRun / BudgetDecision。源码 model/schema/service/controller/route 与前端 api/store/pages 完成链路，不依赖浏览器本地假数据。

## 功能与方法
- 触达计划、基线情景录入/编辑/删除；重名记录与非法数值明确拒绝。
- 两比例实验样本量正态近似与二项Monte Carlo功效验证。独立等样本双组、双侧正态近似。模拟功效用于检验近似；低事件率应谨慎采用，正式实验须另行预注册。
- 每次运行冻结参数、观测和SHA256。Redis任务由独立worker计算，写DB与共享产物卷；输入修改不会改写历史结果。
- 负责人复核不可覆盖；审计记录登录、数据改动、排队、复核与导出；240请求/分钟限流、全局可恢复错误。

## 快速启动
```bash
cp .env.example .env
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18244 ，API http://localhost:19244/docs 。数据库映射 17744。无需Node构建。可以从中文和带空格目录启动。清理 `docker compose down -v --remove-orphans` 会删除本题演示数据卷。

|角色|账号|密码|权限与典型动作|
|---|---|---|---|
|admin 负责人|admin@demo.local|Research2026!|全部读写、批准复核、下载|
|analyst 分析师|analyst@demo.local|Research2026!|数据修改、创建任务、读取结果及下载；不能复核|
|viewer 观察员|viewer@demo.local|Research2026!|只读；仅可导出已复核结果|

## 一天的工作剧本
1. 09:00 admin登录，查“榆灯·冬季专题”。
2. 09:10 analyst核对 `sample-budget-202611-1.csv` 的seed记录。
3. 09:15 来源录入页检查 `基线点击率 (0–1)` 等业务字段，必要时编辑。
4. 09:20 分析执行页保留默认参数 `{"alpha": 0.05, "power": 0.8, "simulations": 10000}`，命名本轮研究。
5. 09:21 冻结输入并排队，worker实算，结果页自动刷新。
6. 09:25 阅读核心数值、方法边界、明细与SHA256；发现失败先修数据重跑。
7. 09:35 admin打开业务复核，写明限制与下一步动作，归档后不可覆盖。
8. 09:40 viewer读取复核意见并下载CSV，在操作留痕页核对下载事件。

seed是3个前瞻设计情景，每个计划1条基线/候选概率、成本和预算记录；本题用于实验前预算设计，不伪造实验后点击事件。预算与单位成本采用十进制除法向下取整，预计总成本四舍五入至分（ROUND_HALF_UP），避免0.6÷(0.1×2)浮点误差少算样本。预算每组触达上限200000；不足一次双组触达时预算可行样本数与功效均为0。

## 技术与目录
|部分|实现|
|---|---|
|前端|原生ES modules、HTML/CSS、Nginx1.27，无前端框架|
|API|Python3.11、FastAPI0.115、SQLAlchemy2、JWT、PBKDF2密码|
|计算|NumPy2.2、SciPy1.15，固定seed42；v1 正态近似 z 检验，v2 双侧 Fisher 精确检验（超几何分布向量化）|
|数据库|PostgreSQL16|
|任务|Redis7持久队列，Python独立worker，心跳healthcheck|

`frontend/src/`分api、stores、types、components/common、hooks、pages、router、utils、constants；`backend/src/`分config、models、schemas、services、controllers、routes、middlewares、utils、constants；`backend/scripts/seed_demo.py`自然键插入、重复不覆盖修改；`backend/migrations/README.md`说明schema版本。

环境变量 `.env.example`：COMPOSE_PROJECT_NAME、FRONTEND_PORT、BACKEND_PORT、DB_PORT、DB_PASSWORD、JWT_SECRET、SEED_DEMO。开发时在本地venv安装backend/requirements.txt，设置DATABASE_URL、REDIS_URL、JWT_SECRET再运行uvicorn；前端开发经Nginx代理 `/api/` 到不带前缀的API。

共享枚举：角色admin/analyst/viewer和队列queued/running/completed/failed；后端`src/constants/enums.py`、认证与worker使用相同值；前端`src/types/enums.js`、状态与角色界面使用相同值。Google Fonts：Noto Serif SC + IBM Plex Mono；断网使用serif/monospace回退。

## 算法输入、失败态与复现
输入为选定批次全部观测和参数，输出核心数值、明细、解释；参数经后端严格白名单和范围验证。两种口径均为独立等样本双组、固定双侧。v1 采用合并比例正态近似 z 检验；v2 采用双侧 Fisher 精确检验，样本量按固定 seed 的 Monte Carlo 功效整数反算，离散检验在小计数下偏保守。模拟功效用于检验近似；低事件率优先采用 Fisher 口径，正式实验须另行预注册。空批次拒绝；不完整字段/非有限数/非法计数拒绝；常量相关列与不足样本使任务failed并保留错误。固定seed42、输入checksum相同且口径相同可复现。独立oracle：功效随样本趋势；公式对norm.ppf；MDE零与越界概率拒绝；Fisher 双侧 p 值对 scipy 参考实现零偏差（容差1e-15）。

## 验证
`backend/tests/e2e_migration.py`（SQLite + 内存假队列，46 项）覆盖口径绑定、影子隔离、整批切换、回退保留新版、在途交错不串版、中断恢复不重复发布、失败闸门、重新迁移、导出口径。`backend/tests/http_migration.py`（FastAPI TestClient，24 项）覆盖鉴权、权限、路由契约与 JSON 差异。运行：`python -I backend/tests/e2e_migration.py`。

已知边界：小型本地研究应用，使用合成数据，不含企业SSO与外发邮件；结果由人工复核，不自动执行业务决策；单worker恢复queued/running任务，数据库与Redis间未采用分布式事务。以当前测试目录TESTING.md记录实际验证结果。

MIT License。
