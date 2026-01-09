import os

# Simulate the issue
raw_key = "”sk-b2dfcbd3bd724ad5877aed345147e4d2“"
sanitized_key = raw_key.strip().strip("'\"“”")
print(f"Original: {raw_key}")
print(f"Sanitized: {sanitized_key}")

expected = "sk-b2dfcbd3bd724ad5877aed345147e4d2"
if sanitized_key == expected:
    print("SUCCESS: Smart quotes removed correctly.")
else:
    print(f"FAILURE: Expected {expected}, got {sanitized_key}")
