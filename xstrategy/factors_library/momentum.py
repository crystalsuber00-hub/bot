def jkp_ret_6_1(d):
    """6-month return skipping the most recent month."""
    return d.factor("mom_6_1")


def jkp_ret_12_1(d):
    """12-month return skipping the most recent month."""
    return d.factor("mom_12_1")
