import logging

_warned = False


def jkp_ocf_at(d):
    """Operating cash flow / total assets in the article. There's no fundamentals feed here, so this
    returns a price-based quality proxy (12-month return consistency) instead. Results will differ
    from the article's."""
    global _warned
    if not _warned:
        logging.getLogger("quantbot").warning("jkp_ocf_at: no fundamentals data; using consistency_12m as a proxy")
        _warned = True
    return d.factor("consistency_12m")
