# SIGMA ENGINE — Core Module
from .data import fetch_ohlcv, fetch_multi_tf, merge_htf, validate_ohlcv
from .data_futures import (fetch_all_futures_data, fetch_open_interest,
                            fetch_funding_rate, fetch_taker_volume, fetch_ls_ratio)
from .features import build_features, features_summary
from .signals import get_signals, signals_summary
from .backtest import run_backtest, calc_metrics, score_config, print_metrics
from .risk import RiskManager, kelly_criterion, stop_rules_summary
from .regime import detect_regime, get_adaptive_params, AdaptiveStrategy, REGIME_PARAMS
from .database import init_db, save_run, get_best, get_top_runs, db_summary
from .macro_filter import is_blocked_now, get_upcoming_events
