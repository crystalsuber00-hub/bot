def cz_idio_vol_proxy(d):
    """Idiosyncratic (non-SPY) volatility, sign-flipped so higher = calmer.

    The article ranks this with weight on "low_risk", so calmer names must score higher.
    """
    return d.factor("low_idio_vol")
