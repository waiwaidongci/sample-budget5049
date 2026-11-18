from sqlalchemy import Column,Integer,String,JSON,Text,ForeignKey,UniqueConstraint
from src.config.database import Base

class MigrationBatch(Base):
    """一次未获批存量研究的整批口径迁移。影子重算→核对→一次切换→可回退。"""
    __tablename__='migration_batches'
    id=Column(Integer,primary_key=True)
    target_method=Column(String(40),nullable=False)
    status=Column(String(20),default='shadow',nullable=False)  # shadow/switched/rolled_back/failed
    created_by=Column(String(120),nullable=False)
    created_at=Column(String(40),nullable=False)
    switched_at=Column(String(40),nullable=True)
    rolled_back_at=Column(String(40),nullable=True)
    total=Column(Integer,default=0,nullable=False)
    shadow_done=Column(Integer,default=0,nullable=False)
    switched_count=Column(Integer,default=0,nullable=False)
    failed_count=Column(Integer,default=0,nullable=False)
    last_error=Column(Text,default='')
    note=Column(Text,default='')

class MigrationRun(Base):
    """批次内单条运行的迁移状态。run_id 唯一，保证中断恢复只补缺项、重试不重复。"""
    __tablename__='migration_runs'
    __table_args__=(UniqueConstraint('batch_id','run_id'),)
    id=Column(Integer,primary_key=True)
    batch_id=Column(Integer,ForeignKey('migration_batches.id'),nullable=False)
    run_id=Column(Integer,ForeignKey('runs.id'),nullable=False)
    old_method=Column(String(40),nullable=False)
    new_method=Column(String(40),nullable=False)
    status=Column(String(20),default='pending',nullable=False)  # pending/shadow_done/switched/failed/rolled_back
    old_result=Column(JSON,nullable=True)
    new_result=Column(JSON,nullable=True)
    new_checksum=Column(String(64),nullable=True)
    artifact_path=Column(String(260),nullable=True)
    error=Column(Text,default='')
    created_at=Column(String(40),nullable=False)
    updated_at=Column(String(40),nullable=False)
