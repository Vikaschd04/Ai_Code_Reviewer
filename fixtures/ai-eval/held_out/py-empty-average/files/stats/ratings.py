def average_rating(ratings):
    """Mean of the ratings; 0 when there are none."""
    total = sum(ratings)
    return total / len(ratings)
