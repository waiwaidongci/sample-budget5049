import os,json,logging
from pathlib import Path
from redis import Redis
from sqlalchemy import select
from src.config.database import Session
from src.config.paths import ARTIFACT_DIR
from src.models.run import Run
from src.models import user,study,observation,review,audit
from src.services.algorithm import calculate
from src.services import migrations
from src.utils.serializers import checksum

def make_redis(): return Redis.from_url(os.environ['REDIS_URL'])

def recover_on_startup(redis):
    """恢复中断任务：分析队列取 queued/running；迁移队列只补 pending（补缺项，不重复）。"""
    ARTIFACT_DIR.mkdir(exist_ok=True)
    with Session() as db:
        pending=list(db.scalars(select(Run).where(Run.status.in_(['queued','running']))))
        for run in pending: redis.rpush('analysis-queue',run.id)
        migration_pending=migrations.recover(redis)
    logging.info('recovered %s analysis runs and %s migration items',len(pending),migration_pending)

def handle_analysis(redis,job):
    id=int(job[1])
    with Session() as db:
        run=db.get(Run,id)
        if not run or run.status in ('completed','failed'): return
        run.status='running';db.commit()
        try:
            if checksum(run.snapshot)!=run.checksum: raise ValueError('输入快照校验失败')
            # 严格按运行冻结口径计算，迟到任务不会套用切换后的另一版口径。
            result=calculate(run.snapshot,run.parameters,run.method)
            result['input_sha256']=run.checksum
            run.result=result;run.status='completed'
            ARTIFACT_DIR.mkdir(exist_ok=True)
            (ARTIFACT_DIR/f'run-{id}.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False))
        except Exception as e:
            logging.exception('analysis failed');run.status='failed';run.error=str(e)
        db.commit()

def handle_migration(job):
    mid=int(job[1])
    with Session() as db:
        try: migrations.shadow_compute(db,mid)
        except Exception: logging.exception('migration shadow compute failed for %s',mid)

def main():
    redis=make_redis()
    recover_on_startup(redis)
    # 单 worker：优先排空分析队列，再处理迁移影子重算；互不改变可见结果正确性。
    while True:
        Path('/tmp/worker-heartbeat').touch()
        job=redis.blpop('analysis-queue',timeout=2)
        if job: handle_analysis(redis,job);continue
        mjob=redis.blpop(migrations.QUEUE,timeout=2)
        if mjob: handle_migration(mjob)

if __name__=='__main__':
    main()
