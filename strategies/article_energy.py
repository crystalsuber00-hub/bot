# The survivor strategy from the article, unchanged except for this comment.
# Run it:  quantbot validate specs/article_energy.json
from xstrategy import *
from xstrategy.factors_library.momentum import jkp_ret_6_1
from xstrategy.factors_library.volatility import cz_idio_vol_proxy
from xstrategy.factors_library.quality import jkp_ocf_at

class Strategy(XStrategy):
    def alpha(self, d):
        eligible = [
            "XOM", "CVX", "COP", "OXY", "SLB", "HAL",
            "EQT", "WMB", "ENB", "CEG", "VST", "CCJ",
            "UEC", "DNN", "GEV", "PWR", "VRT", "ETN", "NEE"
        ]
        momentum  = jkp_ret_6_1(d)        # 60% weight — trend is real
        low_risk  = cz_idio_vol_proxy(d)  # 25% weight — avoid crowded names
        cashflow  = jkp_ocf_at(d)         # 15% weight — quality filter

        score = combine(
            cs_rank(momentum),
            cs_rank(low_risk),
            cs_rank(cashflow),
            weights=[0.60, 0.25, 0.15]
        )
        mask = np.array([s in eligible for s in d.symbols])
        return np.where(mask, score, np.nan)
