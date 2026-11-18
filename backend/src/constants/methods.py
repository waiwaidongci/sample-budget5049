"""共享口径标识。新研究排队时绑定当时生效口径，随运行永久留档。"""
Z_APPROX='v1-zapprox'          # 双侧两比例合并正态近似（历史口径，不可改写）
FISHER_TWO_SIDED='v2-fisher2'  # 双侧 Fisher 精确检验口径
METHODS=(Z_APPROX,FISHER_TWO_SIDED)
METHOD_LABELS={
    Z_APPROX:'双侧正态近似（合并比例 z 检验）',
    FISHER_TWO_SIDED:'双侧 Fisher 精确检验（超几何分布概率和）',
}
