import sys, os
sys.path.insert(0, 'engine')
os.environ['PYTHONIOENCODING'] = 'utf-8'
import warnings; warnings.filterwarnings('ignore')

from optimization.multi_strategy_optimizer import optimize_tf
result = optimize_tf('4h')
print('DONE 4H')
