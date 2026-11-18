import os,logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import text,inspect
from redis import Redis
from src.config.database import Base,engine
from src.models import user,study,observation,run,review,audit,method,migration
from src.routes import auth,research
from src.middlewares.errors import ErrorMiddleware
from src.middlewares.rate_limit import RateLimitMiddleware
from src.services import methods as method_service

# v2 增量列：对已存在的库做幂等 ADD COLUMN（本地方案，无破坏性迁移）。
_INCREMENTAL_COLUMNS={
    'runs':[('method',"VARCHAR(40) NOT NULL DEFAULT 'v1-zapprox'")],
}
def ensure_schema():
    inspector=inspect(engine)
    existing={t:set(c['name'] for c in inspector.get_columns(t)) for t in inspector.get_table_names()}
    with engine.begin() as conn:
        for table,columns in _INCREMENTAL_COLUMNS.items():
            if table not in existing: continue
            for name,ddl in columns:
                if name not in existing[table]:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))

@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    ensure_schema()
    _session_bootstrap()
    if os.environ.get('SEED_DEMO')=='true':
        try:
            from scripts.seed_demo import seed
            seed()
        except Exception: logging.exception('seed failed; manual seed available')
    yield

def _session_bootstrap():
    from src.config.database import Session
    with Session() as db:
        method_service.bootstrap(db)

app=FastAPI(lifespan=lifespan,title='Research Application')
app.add_middleware(ErrorMiddleware)
app.add_middleware(RateLimitMiddleware)
app.include_router(auth.router);app.include_router(research.router)
@app.get('/health')
def health():
    with engine.connect() as conn: conn.execute(text('SELECT 1'))
    Redis.from_url(os.environ['REDIS_URL']).ping()
    return {'status':'healthy'}
