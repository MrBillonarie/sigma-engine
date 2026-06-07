
import sys, time, os
sys.path.insert(0, 'engine')
os.chdir('/opt/sigma')

import multiprocessing, platform
cpus = multiprocessing.cpu_count()
print(f'CPUs: {cpus}')
print(f'Python: {platform.python_version()}')

import psutil
mem = psutil.virtual_memory()
print(f'RAM total: {mem.total//1048576}MB | disponible: {mem.available//1048576}MB')

# Test velocidad backtest
import numpy as np
import pandas as pd
t0 = time.time()
arr = np.random.randn(300000, 10)
df = pd.DataFrame(arr)
df2 = df.rolling(14).mean()
t1 = time.time()
print(f'Test numpy/pandas 300k rows: {t1-t0:.2f}s')

# Test optuna
import optuna
optuna.logging.set_verbosity(optuna.logging.WARNING)
def obj(t): return t.suggest_float('x', -1, 1) ** 2
study = optuna.create_study()
t0 = time.time()
study.optimize(obj, n_trials=1000)
t1 = time.time()
print(f'Test Optuna 1000 trials: {t1-t0:.2f}s')
print(f'Velocidad: {1000/(t1-t0):.0f} trials/sec')
