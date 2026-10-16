"""Read-only contract checks for mobile UI and missing investor-flow evidence.

A green UI smoke test is not an endorsement of the data or an investment score.
"""


def check_flow_filter(data, visible_count):
    """Check source counts against real flags and rendered filter, including zero.

    Returns an explicit *data-quality* status; never fabricates confirmed flows.
    Raises AssertionError for discrepancies rather than allowing a false UI PASS.
    """
    stocks = data.get('stocks')
    if not isinstance(stocks, list):
        raise AssertionError('Published stock array missing')
    n = data.get('flow_confirmed_count')
    if type(n) is not int or not 0 <= n <= len(stocks):
        raise AssertionError('Invalid published flow_confirmed_count')
    actual = sum(row.get('flows_confirmed') is True for row in stocks)
    if n != actual:
        raise AssertionError(f'Published flow count {n} differs from {actual} flagged rows')
    if visible_count != n:
        raise AssertionError(f'Mobile flow filter shows {visible_count}, expected {n}')
    return {
        'reported': n,
        'flagged': actual,
        'rendered': visible_count,
        'collection_status': 'NO_CONFIRMED_FLOW_DATA' if n == 0 else 'SOME_FLOW_DATA_REPORTED',
        'investment_data_ready': False,  # KRX session / independent corroboration not yet established.
    }
