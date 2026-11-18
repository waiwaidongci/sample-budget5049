"""HTTP 链路验证：登录、口径查询/切换权限、迁移全流程接口、422/409 契约。"""
import os, sys, tempfile, json
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp(prefix='http-mig-')
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['REDIS_URL'] = 'redis://fake:6379/0'
os.environ['JWT_SECRET'] = 'test-secret'
os.environ['ARTIFACT_DIR'] = f'{tmp}/artifacts'
Path(os.environ['ARTIFACT_DIR']).mkdir(parents=True, exist_ok=True)

from fastapi.testclient import TestClient
from src.config.database import Base, engine, Session
from src.models.user import User
from src.models.study import Study
from src.models.observation import Observation
from src.models.run import Run
from src.utils.security import hash_password
from src.services import methods as method_service, runs as run_service, migrations
from src.services.algorithm import calculate
from src.utils.serializers import checksum

class FakeRedis:
    def __init__(self): self.q = {'analysis-queue': deque(), migrations.QUEUE: deque()}
    def rpush(self, k, v): self.q.setdefault(k, deque()).append(int(v))
    def incr(self, k):
        self.c = getattr(self, 'c', {}); self.c[k] = self.c.get(k, 0) + 1; return self.c[k]
    def expire(self, *a): return True
    def ping(self): return True

fake = FakeRedis()
import src.services.runs as rm, src.services.migrations as mm, src.middlewares.rate_limit as rl
rm.Redis = type('R', (), {'from_url': staticmethod(lambda url: fake)})
mm.Redis = type('R', (), {'from_url': staticmethod(lambda url: fake)})
rl.redis = fake

from src.main import app
with Session() as db:  # 绕过 lifespan 直接备数据
    Base.metadata.create_all(engine); method_service.bootstrap(db)
    for role in ('admin', 'analyst', 'viewer'):
        db.add(User(email=f'{role}@demo.local', role=role, password_hash=hash_password('pw')))
    s = Study(name='HTTP专题', source_filename='h.csv', description=''); db.add(s); db.flush()
    db.add(Observation(study_id=s.id, source_key='H1',
                       values={'p0': 0.03, 'p1': 0.05, 'cost_per_contact': 0.15, 'budget': 6000}))
    db.commit(); sid = s.id

client = TestClient(app)
PASS, FAILS = 0, []
def check(name, cond, detail=''):
    global PASS
    if cond: PASS += 1; print('  ✓', name)
    else: FAILS.append((name, detail)); print('  ✗', name, detail)

def token(role):
    r = client.post('/auth/login', json={'email': f'{role}@demo.local', 'password': 'pw'})
    assert r.status_code == 200, r.text
    return r.headers and r.json()['token']
ta, tan, tv = token('admin'), token('analyst'), token('viewer')
H = lambda t: {'Authorization': f'Bearer {t}'}

# 未认证拒绝
check('未认证访问 /methods 被拒', client.get('/methods').status_code == 401)

# 口径列表与生效标记
r = client.get('/methods', headers=H(ta))
versions = r.json()
check('返回两个口径', len(versions) == 2)
active = [v for v in versions if v['active']]
check('仅一个生效口径且为 v1', len(active) == 1 and active[0]['code'] == 'v1-zapprox')
check('口径含 run_count 统计', all('run_count' in v for v in versions))

# 权限：viewer/analyst 不能切换
check('viewer 切换口径 403', client.post('/methods/switch', json={'code': 'v2-fisher2'}, headers=H(tv)).status_code == 403)
check('analyst 切换口径 403', client.post('/methods/switch', json={'code': 'v2-fisher2'}, headers=H(tan)).status_code == 403)
check('未知口径 422', client.post('/methods/switch', json={'code': 'nope'}, headers=H(ta)).status_code == 422)

# analyst 排队一个 v1 运行并手动完成
class D: pass
with Session() as db:
    d = D(); d.study_id = sid; d.name = 'HTTP运行'; d.parameters = {'alpha': 0.05, 'power': 0.8, 'simulations': 400}
    rec = run_service.create(db, d, db.get(User, 2)); rid = rec['id']
with Session() as db:
    run = db.get(Run, rid); run.status = 'running'
    res = calculate(run.snapshot, run.parameters, run.method); res['input_sha256'] = run.checksum
    run.result = res; run.status = 'completed'; db.commit()

# 迁移接口：viewer/analyst 不能发起
r = client.post('/migrations', json={'target_method': 'v2-fisher2'}, headers=H(tan))
check('analyst 发起迁移 403', r.status_code == 403)
r = client.post('/migrations', json={'target_method': 'v2-fisher2'}, headers=H(ta))
check('admin 发起迁移 200', r.status_code == 200, r.text)
bid = r.json()['id']
check('批次状态 shadow 且含 1 条', r.json()['status'] == 'shadow' and r.json()['total'] == 1)
# 影子阶段重复发起被拒
check('影子阶段重复发起 409', client.post('/migrations', json={}, headers=H(ta)).status_code == 409)
# 处理影子任务
while fake.q[migrations.QUEUE]:
    mid = int(fake.q[migrations.QUEUE].popleft())
    with Session() as db: migrations.shadow_compute(db, mid)
r = client.get(f'/migrations/{bid}', headers=H(tv))
check('viewer 可查看批次与差异', r.status_code == 200 and 'diff' in r.json()['runs'][0])
check('差异条目完整', len(r.json()['runs'][0]['diff']) >= 8)
# 影子未切换前运行仍 v1
with Session() as db: check('切换前可见仍 v1', db.get(Run, rid).method == 'v1-zapprox')
# 切换权限与结果
check('analyst 切换 403', client.post(f'/migrations/{bid}/switch', headers=H(tan)).status_code == 403)
r = client.post(f'/migrations/{bid}/switch', headers=H(ta))
check('admin 切换 200', r.status_code == 200 and r.json()['status'] == 'switched', r.text)
with Session() as db: check('切换后可见 v2', db.get(Run, rid).method == 'v2-fisher2')
# 重复切换被拒
check('重复切换 409', client.post(f'/migrations/{bid}/switch', headers=H(ta)).status_code == 409)
# 回退
r = client.post(f'/migrations/{bid}/rollback', headers=H(ta))
check('回退 200 且状态 rolled_back', r.status_code == 200 and r.json()['status'] == 'rolled_back')
with Session() as db: check('回退后恢复 v1', db.get(Run, rid).method == 'v1-zapprox')
check('回退后再回退 409', client.post(f'/migrations/{bid}/rollback', headers=H(ta)).status_code == 409)
# 批次列表
r = client.get('/migrations', headers=H(ta))
check('迁移批次列表', r.status_code == 200 and len(r.json()) == 1)
# 运行详情含 method 字段
r = client.get(f'/runs/{rid}', headers=H(tv))
check('运行详情带口径字段', r.json()['method'] == 'v1-zapprox')
# 导出含口径列
r = client.get(f'/runs/{rid}/export', headers=H(ta))
check('导出 CSV 含口径列', '分析口径' in r.text and 'v1-zapprox' in r.text)

print(f'\nHTTP 结果：{PASS} 通过，{len(FAILS)} 失败')
if FAILS:
    for n, d in FAILS: print('FAIL', n, d)
    sys.exit(1)
print('全部通过')
