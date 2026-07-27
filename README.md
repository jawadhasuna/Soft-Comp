Here is the code with a `README.md` file:

**is_prime.py**
```python
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
```

**README.md**
```markdown
# Prime Number Checker

This is a simple Python script that checks if a given number is prime.

## How to Run

1. Save this code in a file named `is_prime.py`.
2. Open a terminal or command prompt and navigate to the directory where you saved the file.
3. Run the script using `python is_prime.py`.

## Dependencies

* Python 3.x (tested with Python 3.8)

## Usage

Call the `is_prime` function with an integer argument to check if it's prime.

Example:
```python
print(is_prime(25))  # False
```
Note: This script uses a simple trial division method to check for primality, which is sufficient for small numbers but may be slow for large inputs. For more efficient primality testing, consider using a probabilistic primality test or a dedicated library like `sympy`.