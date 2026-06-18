import sys; sys.path.insert(0, '/opt/sigma'); sys.path.insert(0, '/opt/sigma/engine/live')
import live_executor as le
le.reconcile()
print('RECONCILE TEST DONE')
