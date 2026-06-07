import sys, os
sys.path.insert(0, 'engine')
os.chdir('/opt/sigma')
try:
    from optimization.multi_strategy_optimizer import optimize_tf
    print('Import OK')
except Exception as e:
    print('Import ERROR:', e)
    import traceback; traceback.print_exc()
    sys.exit(1)
