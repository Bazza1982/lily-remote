import sys
sys.path.insert(0, r'C:\Users\Barry Li (UoN)\clawd\projects\lily-remote')
from agent.control import input as inp
print("Moving mouse to (100, 100)...")
r1 = inp.move(100, 100)
print(f"Result 1: {r1}")
import time
time.sleep(0.5)
print("Moving mouse to (900, 500)...")
r2 = inp.move(900, 500)
print(f"Result 2: {r2}")
