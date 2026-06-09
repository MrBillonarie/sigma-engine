#!/usr/bin/env python3
"""Script conveniente para actualizar datos de commodities desde cron."""
import sys, os
sys.path.insert(0, '/opt/sigma')
sys.path.insert(0, '/opt/sigma/engine')
os.chdir('/opt/sigma')
from commodities.fetcher import update_all
update_all()
