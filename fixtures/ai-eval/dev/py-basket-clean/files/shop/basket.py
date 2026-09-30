def add_item(item, basket=None):
    """Return a new basket list with item appended."""
    items = list(basket) if basket is not None else []
    items.append(item)
    return items
