def is_prime(n):
    """Checks if a number is prime.

    Args:
        n (int): The number to check.

    Returns:
        bool: True if the number is prime, False otherwise.
    """
    if n <= 1:
        return False
    for i in range(2, int(n ** 0.5) + 1):
        if n % i == 0:
            return False
    return True

print(is_prime(25))  # False
print(is_prime(23))  # True
print(is_prime(37))  # True
print(is_prime(48))  # False