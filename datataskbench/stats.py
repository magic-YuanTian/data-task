"""Statistics used by the reference analyses."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def mann_kendall(y) -> dict:
    y = np.asarray(y, float)
    tau, p = stats.kendalltau(np.arange(len(y)), y)
    slopes = [(y[j] - y[i]) / (j - i) for i in range(len(y)) for j in range(i + 1, len(y))]
    return dict(tau=float(tau), p=float(p), sen_slope=float(np.median(slopes)))


def deseasonalize_monthly(s: pd.Series) -> pd.Series:
    """Remove month-of-year means estimated on the whole series (needs two or more years)."""
    moy = s.index.month
    adj = s.groupby(moy).transform("mean") - s.mean()
    return s - adj


def shift_share(rate0: pd.Series, share0: pd.Series, rate1: pd.Series, share1: pd.Series) -> dict:
    """Decompose the change of an overall rate into within-segment rate effects and a mix effect."""
    idx = rate0.index.union(rate1.index)
    r0, r1 = rate0.reindex(idx).fillna(0), rate1.reindex(idx).fillna(0)
    s0, s1 = share0.reindex(idx).fillna(0), share1.reindex(idx).fillna(0)
    rate_eff = ((r1 - r0) * (s0 + s1) / 2)
    mix_eff = ((s1 - s0) * (r0 + r1) / 2)
    return dict(total=float((r1 * s1).sum() - (r0 * s0).sum()), rate=rate_eff.to_dict(), mix=mix_eff.to_dict(),
                rate_total=float(rate_eff.sum()), mix_total=float(mix_eff.sum()))
