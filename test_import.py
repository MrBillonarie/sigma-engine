import sys
sys.path.insert(0, 'engine')
try:
    from optimization.multi_strategy_optimizer import STRATEGIES
    print('OK - strategies:', list(STRATEGIES.keys()))
except Exception as e:
    print('ERROR:', e)
    import traceback; traceback.print_exc()
