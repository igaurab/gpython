# %%
import math
radius = 3

# %%
area = math.pi * radius ** 2
print(f"area = {area:.2f}")

# %%
squares = [i * i for i in range(radius)]
squares

# %%
# Change radius above to 5 and press ctrl+r there: this cell re-runs too.
f"circumference = {2 * math.pi * radius:.2f}"
