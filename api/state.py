# api/state.py
"""Shared global state for the API (no circular imports).

IMPORTANT: Other modules must import this module and access `api.state.result_dict`
at call time — NOT `from api.state import result_dict` at module import time,
because result_dict is None until init_state() is called by main.py.
Use `get_result_dict()` as the safe accessor in route handlers.
"""
import multiprocessing as mp
from multiprocessing.managers import SyncManager
from typing import Optional

mp_manager: Optional[SyncManager] = None
result_dict = None          # mp.managers.DictProxy after init; None before
processes: list = []        # list of (line_id, Process)
line_configs: dict = {}     # line_id -> config (for watchdog restarts)


def init_state(manager: SyncManager, result) -> None:
    global mp_manager, result_dict
    mp_manager = manager
    result_dict = result


def get_result_dict():
    return result_dict