export const ROLES=['admin','analyst','viewer'];
export const STATES=['queued','running','completed','failed'];
export const STATE_LABELS={queued:'等待计算',running:'计算进行中',completed:'已完成',failed:'失败需修复'};
export const METHOD_LABELS={'v1-zapprox':'双侧正态近似','v2-fisher2':'双侧 Fisher 精确检验'};
export const MIGRATION_BATCH_LABELS={shadow:'影子重算中',switched:'已切换新版',rolled_back:'已回退旧版',failed:'核对失败整批保留'};
export const MIGRATION_RUN_LABELS={pending:'等待影子重算',shadow_done:'影子完成待切换',switched:'已切换',failed:'核对失败',rolled_back:'已回退'};
