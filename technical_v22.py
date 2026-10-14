"""Causal Wilder RSI, confirmed pivots and explicitly heuristic pattern research."""
import numpy as np
import pandas as pd


def rsi(values,period=14):
    v=np.asarray(values,dtype=float);out=np.full(len(v),np.nan)
    if len(v)<=period:return out
    d=np.diff(v);gain=np.maximum(d,0);loss=np.maximum(-d,0)
    ag=float(gain[:period].mean());al=float(loss[:period].mean())
    for i in range(period,len(v)):
        if i>period:ag=(ag*(period-1)+gain[i-1])/period;al=(al*(period-1)+loss[i-1])/period
        out[i]=50. if ag==0 and al==0 else 100. if al==0 else 100-100/(1+ag/al)
    return out


def weekly_rsi(df):
    # Completed prior weeks + today's partial-week close, never later Friday.
    c=df.Close.to_numpy();out=np.full(len(df),np.nan);last=None;ends=[]
    for i,dt in enumerate(df.index):
        w=dt.isocalendar()[:2]
        if last is not None and w!=last:ends.append(c[i-1])
        out[i]=rsi(ends+[c[i]])[-1];last=w
    return out


def divergence(close,rs,span=3,max_age=30):
    n=len(close);bull=np.zeros(n,dtype=bool);bear=bull.copy();lows=[];highs=[]
    for t in range(2*span,n):
        p=t-span;window=close[p-span:p+span+1]
        # A pivot at p becomes knowable only at t=p+span.
        for islow,points,out in [(True,lows,bull),(False,highs,bear)]:
            extreme=min(window) if islow else max(window)
            if close[p]!=extreme or np.count_nonzero(window==extreme)!=1:continue
            if points and p-points[-1]<=max_age:
                prev=points[-1]
                if np.isfinite(rs[[p,prev]]).all():
                    out[t]=(close[p]<close[prev] and rs[p]>rs[prev]) if islow else (close[p]>close[prev] and rs[p]<rs[prev])
            points.append(p)
    return bull,bear


def indicators(df):
    c=df.Close.to_numpy(dtype=float);daily=rsi(c);weekly=weekly_rsi(df);bull,bear=divergence(c,daily)
    obv=np.cumsum(np.r_[0,np.sign(np.diff(c))*df.Volume.to_numpy()[1:]])
    ma=pd.Series(c).rolling(20).mean().to_numpy();vol=df.Volume.rolling(20).mean().to_numpy()
    cross_up=np.r_[False,(daily[1:]>50)&(daily[:-1]<=50)]
    cross_down=np.r_[False,(daily[1:]<50)&(daily[:-1]>=50)]
    compound=(daily>50)&(weekly>50)&(c>ma)&(df.Volume.to_numpy()>vol)&(obv>np.r_[np.full(20,np.nan),obv[:-20]])
    return {'daily_rsi14':daily,'weekly_rsi14_partial':weekly,'bullish_divergence_confirmed':bull,'bearish_divergence_confirmed':bear,'cross50_up':cross_up,'cross50_down':cross_down,'overbought':daily>=70,'oversold':daily<=30,'compound':compound}


def pattern_snapshot(df):
    """Fixed trailing geometric diagnostics; no Elliott count or predictive claim."""
    c=np.log(df.Close.to_numpy(dtype=float));n=len(c)
    if n<90:return {'status':'insufficient'}
    hi=np.log(df.High.to_numpy()[-60:]);lo=np.log(df.Low.to_numpy()[-60:]);x=np.arange(60)
    upper=np.polyfit(x,hi,1);lower=np.polyfit(x,lo,1)
    shrink=(np.exp(hi[-20:].max()-lo[-20:].min())-1)<(np.exp(hi[:20].max()-lo[:20].min())-1)*.75
    wedge=bool(upper[0]<lower[0]<0 and shrink)
    triangle=bool(upper[0]<0<lower[0] and shrink)
    flat=bool((np.exp(c[-21]-c[-81])-1)>.2 and (np.exp(max(c[-20:])-min(c[-20:]))-1)<.12)
    # IHS requires three confirmed local troughs plus neckline breakout.
    piv=[i for i in range(n-80,n-3) if c[i]==min(c[i-3:i+4]) and np.count_nonzero(c[i-3:i+4]==c[i])==1]
    ihs=False
    if len(piv)>=3:
        a,b,z=piv[-3:];neck=max(c[a:b+1].max(),c[b:z+1].max())
        ihs=bool(c[b]<min(c[a],c[z])-.03 and abs(c[a]-c[z])<.06 and c[-1]>neck)
    return {'status':'heuristic_unvalidated','falling_wedge_candidate':wedge,'inverse_head_shoulders_breakout_candidate':ihs,'triangle_candidate':triangle,'post_rise_consolidation_candidate':flat,'basis':'trailing log-price geometry; fixed thresholds, no pattern win-rate validated'}


def snapshot(df):
    ind=indicators(df);out={k:(bool(v[-1]) if v.dtype==bool else round(float(v[-1]),2) if np.isfinite(v[-1]) else None) for k,v in ind.items()}
    out['patterns']=pattern_snapshot(df);out['weekly_definition']='prior completed weeks plus current partial week';return out
