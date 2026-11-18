from sqlalchemy import select,update,func
from fastapi import HTTPException
from src.models.method import MethodVersion
from src.models.run import Run
from src.constants.methods import METHODS,METHOD_LABELS,Z_APPROX,FISHER_TWO_SIDED
from src.services.audits import log
from src.utils.serializers import record,now

METHOD_DESCRIPTIONS={
    Z_APPROX:'独立等样本双组、双侧合并比例正态近似 z 检验；样本量按正态近似公式，功效由固定 seed 的二项 Monte Carlo 验证。低事件率下正态近似可能失真。',
    FISHER_TWO_SIDED:'独立等样本双组、双侧 Fisher 精确检验；固定边际超几何分布，双侧 p 值为所有概率不大于观测概率的质量点之和；样本量由固定 seed 的 Monte Carlo 功效整数反算，适用于低点击率小计数情景。',
}

def bootstrap(db):
    """启动时确保两个口径均已注册，缺省激活历史 v1。幂等。"""
    existing={x.code for x in db.scalars(select(MethodVersion))}
    if not existing:
        ts=now()
        db.add(MethodVersion(code=Z_APPROX,label=METHOD_LABELS[Z_APPROX],description=METHOD_DESCRIPTIONS[Z_APPROX],
                             active=True,created_by='system',created_at=ts,activated_at=ts))
        db.add(MethodVersion(code=FISHER_TWO_SIDED,label=METHOD_LABELS[FISHER_TWO_SIDED],description=METHOD_DESCRIPTIONS[FISHER_TWO_SIDED],
                             active=False,created_by='system',created_at=ts))
        db.commit()
    else:
        for code in METHODS:
            if code not in existing:
                db.add(MethodVersion(code=code,label=METHOD_LABELS[code],description=METHOD_DESCRIPTIONS[code],
                                     active=False,created_by='system',created_at=now()))
        db.commit()

def active_method(db):
    obj=db.scalar(select(MethodVersion).where(MethodVersion.active.is_(True)))
    if not obj:  # 兜底：理论上 bootstrap 后不会发生
        bootstrap(db)
        obj=db.scalar(select(MethodVersion).where(MethodVersion.active.is_(True)))
    return obj.code

def list_versions(db):
    versions=[record(x) for x in db.scalars(select(MethodVersion).order_by(MethodVersion.id))]
    # 附带给定口径下的运行数量，页面展示存量分布。
    counts=dict(db.execute(select(Run.method,func.count()).group_by(Run.method)).all())
    for v in versions: v['run_count']=counts.get(v['code'],0)
    return versions

def switch(db,code,user):
    if code not in METHODS: raise HTTPException(422,'未知口径标识')
    target=db.scalar(select(MethodVersion).where(MethodVersion.code==code))
    if not target: raise HTTPException(404,'口径未注册')
    current=db.scalar(select(MethodVersion).where(MethodVersion.active.is_(True)))
    if current and current.code==code: raise HTTPException(409,'该口径已是生效口径')
    db.execute(update(MethodVersion).values(active=False))
    target.active=True;target.activated_at=now()
    log(db,user,'method.switch',f'{current.code if current else "-"} -> {code}')
    db.commit()
    return record(target)
