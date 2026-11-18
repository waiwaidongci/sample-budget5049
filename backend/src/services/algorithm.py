import math
from decimal import Decimal, ROUND_HALF_UP
import numpy as np
from scipy import stats
from src.constants.methods import Z_APPROX,FISHER_TWO_SIDED

SEED=42
MAX_N=200000

def finite_rows(snapshot,keys):
    if not snapshot: raise ValueError('空批次无法分析')
    result=[]
    for row in snapshot:
        values=row['values']
        if any(k not in values for k in keys): raise ValueError('观测字段不完整')
        x=[float(values[k]) for k in keys]
        if not all(math.isfinite(v) for v in x): raise ValueError('观测必须为有限数值')
        result.append(x)
    return np.asarray(result,dtype=float)

def required_n(p0,p1,alpha=.05,power=.8):
    if not 0<p0<1 or not 0<p1<1 or p0==p1: raise ValueError('概率必须位于0与1之间且差异非零')
    if not .001<=alpha<=.2 or not .5<=power<=.99: raise ValueError('alpha或power超出范围')
    pooled=(p0+p1)/2;za=float(stats.norm.ppf(1-alpha/2));zb=float(stats.norm.ppf(power))
    return math.ceil((za*math.sqrt(2*pooled*(1-pooled))+zb*math.sqrt(p0*(1-p0)+p1*(1-p1)))**2/(p1-p0)**2)

def simulate_power(p0,p1,n,alpha,B,seed=SEED):
    rng=np.random.default_rng(seed);a=rng.binomial(n,p0,B);b=rng.binomial(n,p1,B)
    pooled=(a+b)/(2*n);se=np.sqrt(2*pooled*(1-pooled)/n)
    z=np.divide((b-a)/n,se,out=np.zeros(B,dtype=float),where=se>0)
    rejected=np.abs(z)>stats.norm.ppf(1-alpha/2)
    prob=float(np.mean(rejected));return prob,math.sqrt(prob*(1-prob)/B)

# ---- v2: 双侧 Fisher 精确检验 -------------------------------------------------
_FISHER_EPS=1e-15

def fisher_two_sided_pvalues(a,b,n):
    """等样本双组（每组 n）成功计数 a,b 的双侧 Fisher 精确 p 值。

    固定边际下 a 服从超几何分布 H(N=2n,K=n,r1=a+b)；双侧 p 值等于所有
    概率不大于观测概率的质量点之和（scipy.stats.fisher_exact(two-sided) 口径）。
    超几何分布单峰，先解析定位众数平台，再以向量化二分查找对侧尾切点，
    等概率镜像点一并计入尾部。
    """
    a=np.asarray(a,dtype=np.int64);b=np.asarray(b,dtype=np.int64);B=a.shape[0]
    N=np.full(B,2*n,dtype=np.int64);K=np.full(B,n,dtype=np.int64)
    r1=a+b
    lo=np.maximum(0,r1-n);hi=np.minimum(r1,n)
    p_obs=stats.hypergeom.pmf(a,N,K,r1)
    # 众数平台 [m0,m1]：floor((r1+1)(K+1)/(N+2))，平台至多含两个相邻整数。
    m=np.clip(np.floor((r1+1)*(K+1)/(N+2)).astype(np.int64),lo,hi)
    pm_m=stats.hypergeom.pmf(m,N,K,r1)
    m0=np.where((m>lo)&np.isclose(stats.hypergeom.pmf(m-1,N,K,r1),pm_m),m-1,m)
    m1=np.where((m<hi)&np.isclose(stats.hypergeom.pmf(m+1,N,K,r1),pm_m),m+1,m)
    in_plateau=(a>=m0)&(a<=m1)
    pvals=np.ones(B,dtype=float)
    # 观测点位于平台左侧：在 [a+1,hi] 找首个 pmf<=p_obs 的点 u，p=cdf(a)+sf(u-1)。
    lf_l,lf_r=(a+1).copy(),hi.copy()
    active=(lf_l<lf_r)&~in_plateau&(a<m0)
    while np.any(active):
        mid=np.where(active,(lf_l+lf_r)//2,0)
        le=stats.hypergeom.pmf(mid,N,K,r1)<=p_obs+_FISHER_EPS
        lf_r=np.where(active&le,mid,lf_r);lf_l=np.where(active&~le,mid+1,lf_l)
        active=(lf_l<lf_r)&~in_plateau&(a<m0)
    u=lf_r
    pvals=np.where((~in_plateau)&(a<m0),
                   stats.hypergeom.cdf(a,N,K,r1)+stats.hypergeom.sf(u-1,N,K,r1),pvals)
    # 观测点位于平台右侧：在 [lo,a-1] 找末个 pmf<=p_obs 的点 l，p=cdf(l)+sf(a-1)。
    rt_l,rt_r=lo.copy(),(a-1).copy()
    active=(rt_l<rt_r)&~in_plateau&(a>m1)
    while np.any(active):
        mid=np.where(active,(rt_l+rt_r+1)//2,0)
        le=stats.hypergeom.pmf(mid,N,K,r1)<=p_obs+_FISHER_EPS
        rt_l=np.where(active&le,mid,rt_l);rt_r=np.where(active&~le,mid-1,rt_r)
        active=(rt_l<rt_r)&~in_plateau&(a>m1)
    l=rt_l
    pvals=np.where((~in_plateau)&(a>m1),
                   stats.hypergeom.cdf(l,N,K,r1)+stats.hypergeom.sf(a-1,N,K,r1),pvals)
    return np.clip(pvals,0.,1.)

def simulate_power_fisher(p0,p1,n,alpha,B,seed=SEED):
    """Fisher 口径 Monte Carlo 功效：固定 seed 的二项抽样 + 双侧 Fisher 判定。"""
    a=np.random.default_rng(seed).binomial(n,p0,B)
    b=np.random.default_rng(seed+1).binomial(n,p1,B)
    pvals=fisher_two_sided_pvalues(a,b,n)
    prob=float(np.mean(pvals<=alpha))
    return prob,math.sqrt(prob*(1-prob)/B)

def required_n_fisher(p0,p1,alpha,power,B,seed=SEED):
    """Fisher 精确口径下达到目标模拟功效的最小等样本每组 n（对模拟功效做整数二分）。"""
    hi=200
    while simulate_power_fisher(p0,p1,hi,alpha,B,seed)[0]<power:
        hi*=2
        if hi>MAX_N: raise ValueError('精确检验样本量超过每组200000，请增大MDE或分拆研究')
    lo=hi//2
    while lo+1<hi:
        mid=(lo+hi)//2
        if simulate_power_fisher(p0,p1,mid,alpha,B,seed)[0]>=power: hi=mid
        else: lo=mid
    return hi

# ---- 通用预算核算（两口径共用，保持与历史十进制规则一致） ---------------------
def budget_rows(snapshot,data,n_for,sim_for,alpha,power,B,method_label):
    rows=[];first=None
    for r,(p0,p1,cost,budget) in zip(snapshot,data):
        if cost<=0 or budget<=0: raise ValueError('成本与预算必须为正')
        n=n_for(p0,p1)
        if n>MAX_N: raise ValueError('近似样本量超过每组200000，请增大MDE或分拆研究')
        sim,se=sim_for(p0,p1,n);total=n*2
        expense=float((Decimal(str(cost))*total).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
        affordable=int(Decimal(str(budget))/(Decimal(str(cost))*2))
        if affordable>MAX_N: raise ValueError('预算允许每组超过200000次触达，请缩小预算或提高成本口径')
        budget_power,budget_se=sim_for(p0,p1,affordable) if affordable>0 else (0.,0.)
        enough='足够' if Decimal(str(budget))>=Decimal(str(expense)) else '不足'
        row=[r['source_key'],float(p0),float(p1),n,total,expense,sim,se,affordable,budget_power,enough]
        rows.append(row)
        if first is None: first=row
    return rows,first

def calculate(snapshot,params,method=Z_APPROX):
    if method==FISHER_TWO_SIDED: return calculate_fisher(snapshot,params)
    return calculate_z(snapshot,params)

def calculate_z(snapshot,params):
    data=finite_rows(snapshot,['p0','p1','cost_per_contact','budget'])
    alpha=float(params.get('alpha',.05));power=float(params.get('power',.8));B=int(params.get('simulations',10000))
    if not 200<=B<=20000: raise ValueError('模拟次数超出范围')
    rows,first=budget_rows(
        snapshot,data,
        lambda p0,p1:required_n(p0,p1,alpha,power),
        lambda p0,p1,n:simulate_power(p0,p1,n,alpha,B),
        alpha,power,B,'z')
    return {'method':Z_APPROX,
            'metrics':{'情景数':len(rows),'首情景每组样本量':first[3],'首情景总样本量':first[4],'首情景预计成本(元)':first[5],'首情景模拟功效':first[6],'模拟功效Monte Carlo标准误':first[7],'目标功效':power,'双侧显著性α':alpha,'模拟次数':B,'随机种子':SEED},
            'interpretation':f"首情景按双侧等样本正态近似，每组需{first[3]}次独立触达，总{first[4]}次；预计成本{first[5]:.2f}元，现有预算{first[10]}。模拟功效{first[6]:.3f}。",
            'caveat':'使用预设基线与最小可检测差异进行设计，不保证真实业务效应。模拟采用双侧合并比例z检验；低事件数时正态近似可能失真。不同情景独立核算，不自动合并预算。',
            'columns':['情景','基线p0','候选p1','所需每组n','总样本','预计元','模拟功效','MC标准误','预算允许每组n','预算下功效','预算判断'],
            'rows':rows,'table_title':'样本、费用与模拟功效逐情景核对'}

def calculate_fisher(snapshot,params):
    data=finite_rows(snapshot,['p0','p1','cost_per_contact','budget'])
    alpha=float(params.get('alpha',.05));power=float(params.get('power',.8));B=int(params.get('simulations',10000))
    if not 200<=B<=20000: raise ValueError('模拟次数超出范围')
    def n_for(p0,p1): return required_n_fisher(p0,p1,alpha,power,B,SEED)
    def sim_for(p0,p1,n): return simulate_power_fisher(p0,p1,n,alpha,B,SEED)
    rows,first=budget_rows(snapshot,data,n_for,sim_for,alpha,power,B,'fisher')
    # 并列保留正态近似样本量，便于页面展示迁移差异。
    z_ref=[required_n(float(r[1]),float(r[2]),alpha,power) for r in rows]
    return {'method':FISHER_TWO_SIDED,
            'metrics':{'情景数':len(rows),'首情景Fisher每组样本量':first[3],'首情景正态近似参考n':z_ref[0],'首情景总样本量':first[4],'首情景预计成本(元)':first[5],'首情景Fisher模拟功效':first[6],'模拟功效Monte Carlo标准误':first[7],'目标功效':power,'双侧显著性α':alpha,'模拟次数':B,'随机种子':SEED},
            'interpretation':f"首情景按双侧Fisher精确检验的模拟功效反算，每组需{first[3]}次独立触达，总{first[4]}次；预计成本{first[5]:.2f}元，现有预算{first[10]}。Fisher模拟功效{first[6]:.3f}，正态近似参考每组{z_ref[0]}。",
            'caveat':'低点击率情景下正态近似失真，改用固定边际超几何分布的双侧Fisher精确检验；样本量由固定seed的Monte Carlo功效整数反算，离散检验在小计数下偏保守，结果随模拟次数存在标准误。设计用途不保证真实业务效应，不同情景独立核算，不自动合并预算。',
            'columns':['情景','基线p0','候选p1','Fisher所需每组n','正态近似参考n','总样本','预计元','Fisher模拟功效','MC标准误','预算允许每组n','预算下功效','预算判断'],
            'rows':[[r[0],r[1],r[2],r[3],z_ref[k],r[4],r[5],r[6],r[7],r[8],r[9],r[10]] for k,r in enumerate(rows)],
            'table_title':'Fisher精确口径样本、费用与模拟功效逐情景核对'}
