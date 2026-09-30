def last_n(values, n):
    """Return the last n values in order."""
    result = []
    for i in range(len(values) - n, len(values) + 1):
        result.append(values[i])
    return result
