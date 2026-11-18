"""未获批存量研究的整批口径迁移。

阶段：影子重算（不触碰可见结果）→ 核对样本量/预算功效/留档 → 一次切换可见结果。
任一运行核对失败则整批保留旧版；中断恢复只补缺项，重试不重复发布；
回退后已按新版冻结（获批或已被新在途计算接管）的研究仍按新版完成。
"""
import os,json
from pathlib import Path
from redis import Redis
from sqlalchemy import select
from fastapi import HTTPException
from src.models.run import Run
from src.models.review import Review
from src.models.migration import MigrationBatch,MigrationRun
from src.constants.methods import FISHER_TWO_SIDED
from src.config.paths import ARTIFACT_DIR
from src.services.algorithm import calculate
from src.services.audits import log
from src.utils.serializers import record,now,checksum

QUEUE='migration-queue'

def _redis(): return Redis.from_url(os.environ['REDIS_URL'])

def _row_value(result,label):
    """从结果明细首行取预算列；两口径预算三列同名同义。"""
    columns=result['columns']
    return result['rows'][0][columns.index(label)]

def diff_of(old_result,new_result):
    """旧版/新版差异：核对样本量、预算功效与留档口径。任一侧缺值则 changed=True。"""
    om=old_result.get('metrics',{});nm=new_result.get('metrics',{})
    specs=[
        ('每组样本量',next((k for k in om if k.endswith('每组样本量')),None),
                      next((k for k in nm if k.endswith('每组样本量')),None),'metrics'),
        ('总样本量','首情景总样本量','首情景总样本量','metrics'),
        ('预计成本(元)','首情景预计成本(元)','首情景预计成本(元)','metrics'),
        ('模拟功效',next((k for k in om if k.endswith('模拟功效')),None),
                    next((k for k in nm if k.endswith('模拟功效')),None),'metrics'),
        ('MC标准误','模拟功效Monte Carlo标准误','模拟功效Monte Carlo标准误','metrics'),
    ]
    diff=[]
    for label,ok,nk,kind in specs:
        ov=om.get(ok);nv=nm.get(nk)
        diff.append({'metric':label,'old':ov,'new':nv,'changed':ov!=nv})
    for label in ('预算允许每组n','预算下功效','预算判断'):
        ov=_row_value(old_result,label);nv=_row_value(new_result,label)
        diff.append({'metric':label,'old':ov,'new':nv,'changed':ov!=nv})
    diff.append({'metric':'口径','old':old_result.get('method'),'new':new_result.get('method'),
                 'changed':old_result.get('method')!=new_result.get('method')})
    return diff

def create_batch(db,user,target=FISHER_TWO_SIDED):
    if target!=FISHER_TWO_SIDED: raise HTTPException(422,'当前仅支持迁移到双侧 Fisher 精确检验口径')
    # 同一时间只允许一个影子批次在途；失败批次可放弃后重建，回退/已切换批次不阻塞。
    busy=db.scalar(select(MigrationBatch).where(MigrationBatch.status=='shadow'))
    if busy: raise HTTPException(409,f'迁移批次 #{busy.id} 仍在影子阶段，请先完成切换')
    reviewed=set(db.scalars(select(Review.run_id)))
    runs=[r for r in db.scalars(select(Run).order_by(Run.id)) if r.id not in reviewed]
    if not runs: raise HTTPException(409,'没有需要迁移的未获批运行')
    not_ready=[r.id for r in runs if r.status!='completed' or not r.result]
    if not_ready: raise HTTPException(409,f'运行 {not_ready} 尚未成功完成，不能迁移；请先处理失败/在途运行')
    batch=MigrationBatch(target_method=target,status='shadow',created_by=user.email,
                         created_at=now(),total=len(runs))
    db.add(batch);db.flush()
    ts=now();pending=[]
    for r in runs:
        mr=MigrationRun(batch_id=batch.id,run_id=r.id,old_method=r.method,new_method=target,
                        status='pending',old_result=r.result,created_at=ts,updated_at=ts)
        db.add(mr);pending.append(mr)
    db.flush()
    log(db,user,'migration.create',f'batch {batch.id}: {len(pending)} runs -> {target}')
    db.commit()
    redis=_redis()
    for mr in pending: redis.rpush(QUEUE,mr.id)   # 仅入队一次
    return get_batch(db,batch.id)

def abandon(db,batch_id,user):
    """放弃失败的影子批次：明细保留留档，批次终结，允许重新发起整批迁移。"""
    batch=db.get(MigrationBatch,batch_id)
    if not batch: raise HTTPException(404,'迁移批次不存在')
    if batch.status!='failed': raise HTTPException(409,'只能放弃核对失败的批次')
    batch.status='failed';batch.note=(batch.note or '')+' 批次已放弃，可重新发起迁移。'
    log(db,user,'migration.abandon',str(batch_id));db.commit()
    return record(batch)

def shadow_compute(db,migration_run_id):
    """worker 调用：影子重算单条明细并自检。绝不修改 runs 表可见字段。"""
    mr=db.get(MigrationRun,migration_run_id)
    if not mr or mr.status in ('shadow_done','switched','rolled_back'): return
    run=db.get(Run,mr.run_id)
    if not run:
        mr.status='failed';mr.error='关联运行已不存在';mr.updated_at=now();db.commit();return
    try:
        if checksum(run.snapshot)!=run.checksum: raise ValueError('输入快照校验失败')
        new_result=calculate(run.snapshot,run.parameters,mr.new_method)
        _verify_archive(new_result)
        new_result['input_sha256']=run.checksum
        payload=json.dumps(new_result,ensure_ascii=False,allow_nan=False)
        ARTIFACT_DIR.mkdir(exist_ok=True)
        path=ARTIFACT_DIR/f'migration-batch{mr.batch_id}-run{run.id}.json'
        path.write_text(payload)
        stored=json.loads(payload)
        mr.new_result=stored;mr.new_checksum=checksum(stored);mr.artifact_path=str(path)
        mr.status='shadow_done';mr.error='';mr.updated_at=now()
    except Exception as e:
        mr.status='failed';mr.error=str(e);mr.updated_at=now()
    db.commit()

def _verify_archive(new_result):
    """样本量、预算功效与留档三项核对；不通过即抛错，整批不得切换。"""
    metrics=new_result.get('metrics',{})
    n_key=next((k for k in metrics if k.endswith('每组样本量')),None)
    n=metrics.get(n_key) if n_key else None
    if not isinstance(n,int) or not (0<n<=200000): raise ValueError('新版样本量核对失败')
    cols=new_result['columns'];row=new_result['rows'][0]
    an=row[cols.index('预算允许每组n')];bp=row[cols.index('预算下功效')]
    if not isinstance(an,int) or an<0 or not isinstance(bp,(int,float)) or not (0<=bp<=1):
        raise ValueError('新版预算功效核对失败')
    if new_result.get('method')!='v2-fisher2' or not new_result.get('interpretation') or not new_result.get('caveat'):
        raise ValueError('新版留档字段不完整')

def _refresh_counters(db,batch):
    mrs=list(db.scalars(select(MigrationRun).where(MigrationRun.batch_id==batch.id)))
    batch.total=len(mrs)
    batch.shadow_done=sum(1 for m in mrs if m.status=='shadow_done')
    batch.switched_count=sum(1 for m in mrs if m.status in ('switched','rolled_back'))
    batch.failed_count=sum(1 for m in mrs if m.status=='failed')
    return mrs

def get_batch(db,batch_id):
    batch=db.get(MigrationBatch,batch_id)
    if not batch: raise HTTPException(404,'迁移批次不存在')
    mrs=_refresh_counters(db,batch);db.commit()
    data=record(batch);data['runs']=[]
    for mr in mrs:
        item=record(mr)
        run=db.get(Run,mr.run_id)
        item['run_name']=run.name if run else ''
        item['run_status']=run.status if run else ''
        item['reviewed']=db.scalar(select(Review).where(Review.run_id==mr.run_id)) is not None
        if mr.old_result and mr.new_result:
            item['diff']=diff_of(mr.old_result,mr.new_result)
        data['runs'].append(item)
    return data

def list_batches(db):
    out=[]
    for b in db.scalars(select(MigrationBatch).order_by(MigrationBatch.id.desc())):
        _refresh_counters(db,b);out.append(record(b))
    db.commit()
    return out

def switch(db,batch_id,user):
    batch=db.get(MigrationBatch,batch_id)
    if not batch: raise HTTPException(404,'迁移批次不存在')
    if batch.status!='shadow': raise HTTPException(409,'只有影子阶段的批次可以切换')
    mrs=_refresh_counters(db,batch)
    pending=[m for m in mrs if m.status=='pending']
    failed=[m for m in mrs if m.status=='failed']
    if pending: raise HTTPException(409,f'仍有 {len(pending)} 条影子重算在途，禁止切换')
    if failed:
        batch.status='failed';batch.last_error=f'{len(failed)} 条核对失败，整批保留旧版'
        log(db,user,'migration.switch_blocked',f'{batch.id}: {len(failed)} failed')
        db.commit()  # 先提交失败状态，再抛错，避免异常回滚掉状态变更
        raise HTTPException(409,'存在核对失败项，整批保留旧版；请修复数据后新建迁移批次')
    if not mrs: raise HTTPException(409,'批次为空')
    # 闸门：影子期间新增获批的运行必须退出迁移（获批即沿用原口径）。
    # 为保证“一次整批切换”，不做部分切换：阻断本批并标记 failed（整批保留旧版），
    # 负责人放弃后可重建批次，获批项会自动排除。
    newly_reviewed=[mr.run_id for mr in mrs
                    if db.scalar(select(Review).where(Review.run_id==mr.run_id)) is not None]
    if newly_reviewed:
        batch.status='failed';batch.last_error=f'运行 {newly_reviewed} 在迁移期间获批，整批保留旧版'
        log(db,user,'migration.switch_blocked',f'{batch.id}: reviewed {newly_reviewed}')
        db.commit()  # 先提交失败状态，再抛错
        raise HTTPException(409,'迁移期间有运行获批，须沿用原口径；请放弃本批次后重新发起（获批项自动排除）')
    for mr in mrs:  # 二次留档核对，防中断/篡改
        if mr.status!='shadow_done' or not mr.new_result or not mr.new_checksum:
            raise HTTPException(409,f'运行 {mr.run_id} 影子结果缺失，禁止切换')
        if checksum(mr.new_result)!=mr.new_checksum: raise HTTPException(409,f'运行 {mr.run_id} 留档校验不一致')
        p=Path(mr.artifact_path)
        if not p.exists() or checksum(json.loads(p.read_text()))!=mr.new_checksum:
            raise HTTPException(409,f'运行 {mr.run_id} 产物留档缺失或被改动')
    switched=0  # 单事务一次切换；获批运行从不在批次内，天然沿用原口径
    for mr in mrs:
        run=db.get(Run,mr.run_id)
        if not run: raise HTTPException(409,f'运行 {mr.run_id} 已不存在')
        run.result=mr.new_result;run.method=mr.new_method;run.status='completed';run.error=''
        ARTIFACT_DIR.mkdir(exist_ok=True)
        (ARTIFACT_DIR/f'run-{run.id}.json').write_text(
            json.dumps(mr.new_result,ensure_ascii=False,allow_nan=False))
        mr.status='switched';mr.updated_at=now();switched+=1
    batch.status='switched';batch.switched_at=now();batch.switched_count=switched
    log(db,user,'migration.switch',f'{batch.id}: switched {switched} runs -> {batch.target_method}')
    db.commit()
    return get_batch(db,batch.id)

def rollback(db,batch_id,user):
    batch=db.get(MigrationBatch,batch_id)
    if not batch: raise HTTPException(404,'迁移批次不存在')
    if batch.status!='switched': raise HTTPException(409,'只有已切换批次可以回退')
    mrs=_refresh_counters(db,batch)
    reverted=0;kept=0
    for mr in mrs:
        run=db.get(Run,mr.run_id)
        reviewed=run and db.scalar(select(Review).where(Review.run_id==mr.run_id)) is not None
        # 已获批、或运行已脱离本批次新版绑定（例如回退后重跑）的，保持新版完成。
        if not run or reviewed or run.method!=mr.new_method:
            kept+=1;continue
        run.result=mr.old_result;run.method=mr.old_method;run.status='completed';run.error=''
        ARTIFACT_DIR.mkdir(exist_ok=True)
        (ARTIFACT_DIR/f'run-{run.id}.json').write_text(
            json.dumps(mr.old_result,ensure_ascii=False,allow_nan=False))
        mr.status='rolled_back';mr.updated_at=now();reverted+=1
    batch.status='rolled_back';batch.rolled_back_at=now()
    batch.note=(batch.note or '')+f' 回退：{reverted} 条恢复旧口径，{kept} 条按新版保留。'
    log(db,user,'migration.rollback',f'{batch.id}: reverted {reverted}, kept-new {kept}')
    db.commit()
    return get_batch(db,batch.id)

def recover(redis):
    """worker 启动：仅把 pending 明细重新入队，已完成项不重复（只补缺项）。"""
    from src.config.database import Session
    with Session() as db:
        rows=list(db.scalars(select(MigrationRun).where(MigrationRun.status=='pending')))
        for mr in rows: redis.rpush(QUEUE,mr.id)
        return len(rows)
