import os
from pathlib import Path
# 产物卷默认 /app/artifacts（compose 挂载），本地测试可用环境变量覆盖。
ARTIFACT_DIR=Path(os.environ.get('ARTIFACT_DIR','/app/artifacts'))
