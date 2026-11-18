from sqlalchemy import Column,Integer,String,Text,Boolean
from src.config.database import Base

class MethodVersion(Base):
    """分析口径注册表。active 唯一；切换只改激活标记，口径定义不可改写。"""
    __tablename__='method_versions'
    id=Column(Integer,primary_key=True)
    code=Column(String(40),unique=True,nullable=False)
    label=Column(String(160),nullable=False)
    description=Column(Text,default='')
    active=Column(Boolean,default=False,nullable=False)
    created_by=Column(String(120),nullable=False)
    created_at=Column(String(40),nullable=False)
    activated_at=Column(String(40),nullable=True)
