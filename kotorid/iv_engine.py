def compute_iv_rank(current_iv: float, iv_history: list[float]) -> float:
    """
    Compute IV rank as a percentile of the current IV within the historical range.

    IV rank = (current_iv - min_iv) / (max_iv - min_iv)

    Args:
        current_iv: The current implied volatility value
        iv_history: A list of historical IV values

    Returns:
        A float between 0.0 and 1.0 representing the IV rank.
        Returns 0.0 if all historical values are the same.

    Raises:
        ValueError: If iv_history is empty
    """
    if not iv_history:
        raise ValueError("iv_history cannot be empty")

    min_iv = min(iv_history)
    max_iv = max(iv_history)

    if max_iv == min_iv:
        return 0.0

    return max(0.0, min(1.0, (current_iv - min_iv) / (max_iv - min_iv)))


def compute_iv_percentile(current_iv: float, iv_history: list[float]) -> float:
    """
    Compute IV percentile as the fraction of historical values strictly below current IV.

    IV percentile = count(iv < current_iv) / len(iv_history)

    Args:
        current_iv: The current implied volatility value
        iv_history: A list of historical IV values

    Returns:
        A float between 0.0 and 1.0 representing the IV percentile.

    Raises:
        ValueError: If iv_history is empty
    """
    if not iv_history:
        raise ValueError("iv_history cannot be empty")

    return sum(1 for iv in iv_history if iv < current_iv) / len(iv_history)
