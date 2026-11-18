"""端到端验证：口径注册/绑定、影子迁移、整批切换、回退、在途交错、恢复不重复。

不依赖 Docker/PostgreSQL/Redis：SQLite 落库 + 内存假队列，直接调用服务层与 worker 处理函数。
运行：python -I tests/e2e_migration.py
"""
import os, sys, json, tempfile, traceback
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp(prefix='e2e-mig-')
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['REDIS_URL'] = 'redis://fake:6379/0'
os.environ['JWT_SECRET'] = 'test-secret'
os.environ['ARTIFACT_DIR'] = f'{tmp}/artifacts'
Path(os.environ['ARTIFACT_DIR']).mkdir(parents=True, exist_ok=True)

from src.config.database import Base, engine, Session
from src.models.user import User
from src.models.study import Study
from src.models.observation import Observation
from src.models.run import Run
from src.models.review import Review
from src.models.migration import MigrationBatch, MigrationRun
from src.services import methods as method_service, migrations, runs as run_service
from src.services.algorithm import calculate
from src.services import algorithm as algo_mod
from src.utils.security import hash_password
from src.utils.serializers import checksum
from src.constants.methods import Z_APPROX, FISHER_TWO_SIDED

# ---- 内存假 Redis：仅实现队列所需命令 ----
class FakeRedis:
    def __init__(self): self.q = {'analysis-queue': deque(), migrations.QUEUE: deque()}
    def rpush(self, k, v): self.q.setdefault(k, deque()).append(int(v))
    def blpop(self, k, timeout=0):
        d = self.q.setdefault(k, deque())
        return (k, str(d.popleft()).encode()) if d else None
    def ping(self): return True

fake = FakeRedis()
import src.worker as worker_mod
import src.services.runs as runs_mod
import src.services.migrations as mig_mod
worker_mod.Redis = type('R', (), {'from_url': staticmethod(lambda url: fake)})
runs_mod.Redis = type('R', (), {'from_url': staticmethod(lambda url: fake)})
mig_mod.Redis = type('R', (), {'from_url': staticmethod(lambda url: fake)})

PASS, FAILS = 0, []
def check(name, cond, detail=''):
    global PASS
    if cond: PASS += 1; print(f'  ✓ {name}')
    else: FAILS.append((name, detail)); print(f'  ✗ {name} {detail}')

# ---- worker 处理函数（复用真实 worker 逻辑）----
def process_analysis(run_id):
    db = Session()
    try:
        run = db.get(Run, run_id)
        if not run or run.status in ('completed', 'failed'):
            db.close(); return
        run.status = 'running'; db.commit()
        try:
            if checksum(run.snapshot) != run.checksum: raise ValueError('输入快照校验失败')
            result = calculate(run.snapshot, run.parameters, run.method)
            result['input_sha256'] = run.checksum
            run.result = result; run.status = 'completed'
            Path(os.environ['ARTIFACT_DIR'], f'run-{run_id}.json').write_text(
                json.dumps(result, ensure_ascii=False, allow_nan=False))
        except Exception as e:
            run.status = 'failed'; run.error = str(e)
        db.commit()
    finally:
        db.close()

def drain_analysis():
    while fake.q['analysis-queue']: process_analysis(int(fake.q['analysis-queue'].popleft()))

def drain_migration():
    while fake.q[migrations.QUEUE]:
        mid = int(fake.q[migrations.QUEUE].popleft())
        db = Session()
        try: migrations.shadow_compute(db, mid)
        finally: db.close()

# ---- 准备数据 ----
Base.metadata.create_all(engine)
with Session() as db:
    method_service.bootstrap(db)
    admin = User(email='admin@demo.local', role='admin', password_hash=hash_password('pw'))
    analyst = User(email='analyst@demo.local', role='analyst', password_hash=hash_password('pw'))
    db.add_all([admin, analyst]); db.flush()
    s = Study(name='低点击率专题', source_filename='demo.csv', description='t'); db.add(s); db.flush()
    db.add_all([
        Observation(study_id=s.id, source_key='K1',
                    values={'p0': 0.02, 'p1': 0.035, 'cost_per_contact': 0.1, 'budget': 5000}),
        Observation(study_id=s.id, source_key='K2',
                    values={'p0': 0.05, 'p1': 0.08, 'cost_per_contact': 0.2, 'budget': 9000}),
    ])
    db.commit()
    sid = s.id

class RunData:
    def __init__(self, name):
        self.study_id = sid; self.name = name
        self.parameters = {'alpha': 0.05, 'power': 0.8, 'simulations': 500}

print('\n[1] 初始口径与新研究绑定 v1')
with Session() as db:
    check('默认生效口径为 v1', method_service.active_method(db) == Z_APPROX)
    r1 = run_service.create(db, RunData('研究一'), analyst); id1 = r1['id']
    r2 = run_service.create(db, RunData('研究二'), analyst); id2 = r2['id']
    r3 = run_service.create(db, RunData('研究三'), analyst); id3 = r3['id']
drain_analysis()
with Session() as db:
    for rid in (id1, id2, id3):
        r = db.get(Run, rid)
        check(f'运行 {rid} 按 v1 完成并留档', r.status == 'completed' and r.method == Z_APPROX
              and r.result['method'] == Z_APPROX and Path(os.environ['ARTIFACT_DIR'], f'run-{rid}.json').exists())
    # 研究三获批 → 永不迁移、下载沿用原口径
    run_service.approve(db, id3, type('D', (), {'note': '复核通过归档'})(), admin)
    check('研究三已获批', db.get(Review, 1) is not None)

print('\n[2] 迁移期间并发排队：在途新研究绑定当时生效口径（v1），不串版')
with Session() as db:
    r4 = run_service.create(db, RunData('在途研究'), analyst); id4 = r4['id']
    queued_run = db.get(Run, id4)
    check('迁移前排队的新研究绑定 v1', queued_run.method == Z_APPROX)
drain_analysis()
with Session() as db:
    r4obj = db.get(Run, id4)
    check('在途研究按 v1 完成（迟归不串版）', r4obj.method == Z_APPROX and r4obj.result['method'] == Z_APPROX)

print('\n[3] 创建迁移批次：只含未获批运行，获批研究排除')
with Session() as db:
    batch = migrations.create_batch(db, admin); bid = batch['id']
    run_ids = sorted(x['run_id'] for x in batch['runs'])
    check('批次包含未获批的 1/2/4', run_ids == sorted([id1, id2, id4]), str(run_ids))
    check('获批研究 3 不在批次', id3 not in run_ids)
    check('明细冻结旧结果', all(x['old_result'] and x['status'] == 'pending' for x in batch['runs']))

print('\n[4] 影子重算不触碰可见结果；处理过程中可见结果保持旧版')
# 先处理一条迁移，再检查可见结果未被改动，验证交错与影子隔离
first_mig = fake.q[migrations.QUEUE].popleft()
with Session() as db:
    migrations.shadow_compute(db, int(first_mig)); db.close()
with Session() as db:
    visible_before = {rid: db.get(Run, rid).result['method'] for rid in (id1, id2, id4)}
    check('部分影子重算后可见结果仍是 v1', all(m == Z_APPROX for m in visible_before.values()))

print('\n[4b] 迁移进行中并发排队：新研究绑定当时生效口径（v1），迟归不串版')
# 批次仍在影子阶段；再排一条新研究（生效口径仍为 v1）
with Session() as db:
    r6 = run_service.create(db, RunData('迁移期并发研究'), analyst); id6 = r6['id']
    check('迁移期新研究绑定当时生效口径 v1', db.get(Run, id6).method == Z_APPROX)
# 让这条迟归任务先完成——它必须按冻结的 v1 计算，不套用任何新版
drain_analysis()
with Session() as db:
    r6obj = db.get(Run, id6)
    check('迟归研究按 v1 完成（不混入另一版）', r6obj.method == Z_APPROX and r6obj.result['method'] == Z_APPROX)
# 排空剩余影子重算
drain_migration()
with Session() as db:
    b = migrations.get_batch(db, bid)
    check('全部影子完成', all(x['status'] == 'shadow_done' for x in b['runs']))
    check('新结果为 v2 且产物留档', all(x['new_result']['method'] == FISHER_TWO_SIDED
          and x['new_checksum'] and Path(x['artifact_path']).exists() for x in b['runs']))
    check('迁移差异已生成', all(len(x['diff']) >= 8 for x in b['runs']))
    sample = next(x for x in b['runs'] if x['run_id'] == id1)
    changed = [d['metric'] for d in sample['diff'] if d['changed']]
    check('差异含样本量/预算/口径变化', '口径' in changed and '每组样本量' in changed, str(changed))

print('\n[5] 中断恢复：只补 pending，已完成不重复入队/发布')
requeued = migrations.recover(fake)
check('恢复时无 pending 可补（返回0）', requeued == 0)
# 人为制造一条 pending（模拟 dequeue 后宕机）
with Session() as db:
    mr = db.scalar(__import__('sqlalchemy').select(MigrationRun).where(MigrationRun.run_id == id2))
    mr.status = 'pending'; db.commit()
requeued = migrations.recover(fake)
check('恢复仅把 1 条 pending 重新入队', requeued == 1 and len(fake.q[migrations.QUEUE]) == 1)
# 再次恢复是 at-least-once 重投（消息可重复）；幂等性由 shadow_compute 的状态检查保证
migrations.recover(fake)
check('重复恢复仅重投同一条 pending（幂等消费不重复发布）', len(fake.q[migrations.QUEUE]) == 2)
drain_migration()
with Session() as db:
    b = migrations.get_batch(db, bid)
    check('补算后仍只 shadow_done 一次（无重复发布）',
          sum(1 for x in b['runs'] if x['status'] == 'shadow_done') == 3)
    check('队列已排空，无悬挂消息', len(fake.q[migrations.QUEUE]) == 0)

print('\n[6] 一次切换：可见结果整体切到 v2；获批研究保持 v1')
with Session() as db:
    before_v1 = {rid: db.get(Run, rid).result['metrics'] for rid in (id1, id2, id4)}
    migrations.switch(db, bid, admin)
    for rid in (id1, id2, id4):
        r = db.get(Run, rid)
        check(f'运行 {rid} 可见结果已切 v2', r.method == FISHER_TWO_SIDED and r.result['method'] == FISHER_TWO_SIDED)
        art = json.loads(Path(os.environ['ARTIFACT_DIR'], f'run-{rid}.json').read_text())
        check(f'运行 {rid} 产物同步为 v2', art['method'] == FISHER_TWO_SIDED)
    r3obj = db.get(Run, id3)
    check('获批研究 3 沿用 v1', r3obj.method == Z_APPROX and r3obj.result['method'] == Z_APPROX)
    b = db.get(MigrationBatch, bid)
    check('批次状态 switched', b.status == 'switched' and b.switched_count == 3)

print('\n[7] 切换后新研究绑定 v2（生效口径已随切换？否——生效口径独立，验证显式切换）')
# 生效口径与迁移解耦：迁移不改 active method；新研究仍按 active。显式切换生效口径到 v2。
with Session() as db:
    method_service.switch(db, FISHER_TWO_SIDED, admin)
    check('生效口径切换为 v2', method_service.active_method(db) == FISHER_TWO_SIDED)
    r5 = run_service.create(db, RunData('新版研究'), analyst); id5 = r5['id']
drain_analysis()
with Session() as db:
    r5obj = db.get(Run, id5)
    check('新研究绑定并按 v2 计算', r5obj.method == FISHER_TWO_SIDED and r5obj.result['method'] == FISHER_TWO_SIDED)

print('\n[8] 回退：未获批运行恢复 v1；已按新版冻结/获批的保留新版')
# 先把研究 2 在切换后获批（回退后应保留 v2）
with Session() as db:
    run_service.approve(db, id2, type('D', (), {'note': '新版复核'})(), admin)
with Session() as db:
    migrations.rollback(db, bid, admin)
    r1obj = db.get(Run, id1)
    check('未获批运行 1 回退为 v1 且结果复原',
          r1obj.method == Z_APPROX and r1obj.result['method'] == Z_APPROX
          and r1obj.result['metrics'] == before_v1[id1])
    r2obj = db.get(Run, id2)
    check('回退后已获批的运行 2 保持 v2', r2obj.method == FISHER_TWO_SIDED)
    r3obj = db.get(Run, id3)
    check('从未迁移的获批运行 3 仍为 v1', r3obj.method == Z_APPROX)
    b = db.get(MigrationBatch, bid)
    check('批次状态 rolled_back', b.status == 'rolled_back')

print('\n[8b] 影子期间获批 → 阻断整批切换，放弃后重建自动排除获批项')
# 此时无在途 shadow 批次；运行 4 当前为 v2 未获批，运行 1 为 v1 未获批
with Session() as db:
    bb = migrations.create_batch(db, admin); bbid = bb['id']
    bb_ids = sorted(x['run_id'] for x in bb['runs'])
    check('新批次含未获批运行 1/4', id1 in bb_ids and id4 in bb_ids, str(bb_ids))
drain_migration()
with Session() as db:
    run_service.approve(db, id4, type('D', (), {'note': '迁移期获批'})(), admin)  # 影子完成后、切换前获批
with Session() as db:  # 独立会话，避免 switch 抛 409 后同会话读到过期状态
    blocked = False
    try: migrations.switch(db, bbid, admin)
    except Exception as e: blocked = True; bmsg = str(e)
    check('获批后整批切换被阻断', blocked and '获批' in bmsg, bmsg)
with Session() as db:
    # 运行4在步骤8回退后已是 v1；阻断切换必须保持其既有 v1 不被覆盖
    check('批次置 failed 且可见结果未被覆盖', db.get(MigrationBatch, bbid).status == 'failed'
          and db.get(Run, id4).method == Z_APPROX)
    run4_before = db.get(Run, id4).result
    migrations.abandon(db, bbid, admin)
    check('放弃后运行4结果字节级未变', db.get(Run, id4).result == run4_before)
    bb2 = migrations.create_batch(db, admin); bbid2 = bb2['id']
    check('重建批次排除已获批运行 4', all(x['run_id'] != id4 for x in bb2['runs']))
    check('重建批次仍含未获批运行 1', any(x['run_id'] == id1 for x in bb2['runs']))
drain_migration()
with Session() as db:
    migrations.switch(db, bbid2, admin)
    migrations.rollback(db, bbid2, admin)  # 复位，保持后续步骤前置状态
    check('复位批次已回退', db.get(MigrationBatch, bbid2).status == 'rolled_back')
    check('运行 1 恢复 v1', db.get(Run, id1).method == Z_APPROX)

print('\n[9] 核对失败 → 整批保留旧版（验证闸门）')
orig_calc = mig_mod.calculate
def boom(snapshot, parameters, method):
    if method == FISHER_TWO_SIDED: raise ValueError('模拟注入失败：留档核对不通过')
    return orig_calc(snapshot, parameters, method)
mig_mod.calculate = boom
try:
    with Session() as db:
        # 新批次只含当前未获批运行（1 已回退为 v1 未获批；4 v2 未获批；5 v2 未获批）
        b2 = migrations.create_batch(db, admin); bid2 = b2['id']
    drain_migration()
    with Session() as db:
        b2v = migrations.get_batch(db, bid2)
        check('失败批次明细均 failed', all(x['status'] == 'failed' for x in b2v['runs']))
        blocked = False
        try: migrations.switch(db, bid2, admin)
        except Exception as e: blocked = True; msg = str(e)
        check('切换被拒绝且整批保留旧版', blocked and '整批保留旧版' in msg, msg)
        check('失败后可见结果未被改动', db.get(Run, id1).method == Z_APPROX)
        check('批次标记 failed', db.get(MigrationBatch, bid2).status == 'failed')
        migrations.abandon(db, bid2, admin)
        check('失败批次可放弃并允许重建', db.get(MigrationBatch, bid2).status == 'failed')
finally:
    mig_mod.calculate = orig_calc

print('\n[10] 回退后重新迁移（同运行可进入新批次）')
with Session() as db:
    b3 = migrations.create_batch(db, admin); bid3 = b3['id']
    ids3 = sorted(x['run_id'] for x in b3['runs'])
    check('回退后的运行 1 可再次迁移', id1 in ids3, str(ids3))
drain_migration()
with Session() as db:
    migrations.switch(db, bid3, admin)
    check('再次切换成功', db.get(MigrationBatch, bid3).status == 'switched')
    check('运行 1 重新切到 v2', db.get(Run, id1).method == FISHER_TWO_SIDED)

print('\n[11] 下载沿用冻结口径')
with Session() as db:
    csv1 = run_service.export(db, id1, admin)
    csv3 = run_service.export(db, id3, admin)
    check('运行1导出标注 v2', FISHER_TWO_SIDED in csv1)
    check('获批运行3导出标注 v1', Z_APPROX in csv3 and FISHER_TWO_SIDED not in csv3)

print(f'\n结果：{PASS} 项通过，{len(FAILS)} 项失败')
if FAILS:
    for n, d in FAILS: print('FAIL:', n, d)
    sys.exit(1)
print('全部通过')
