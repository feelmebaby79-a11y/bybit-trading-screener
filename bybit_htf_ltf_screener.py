# realtime-rescan-trigger: 2026-09-29T20:10+09:00
#!/usr/bin/env python3

# -*- coding: utf-8 -*-

import argparse

import math

import time

from dataclasses import dataclass

import numpy as np

import pandas as pd

import requests

# =========================================================

# Cloudflare Worker -> Bybit V5

# =========================================================

BASES = [
    "https://bybit-trading-screener.feelmebaby79.workers.dev",
    "https://api.bybit.com",
    "https://api.bytick.com",
]

CATEGORY = "linear"

S = requests.Session()

S.headers.update({

    "User-Agent": "bybit-htf-ltf-screener/3.0"

})

TFS = {

    "1D": "D",

    "4H": "240",

    "1H": "60",

    "15m": "15",

    "5m": "5",

}

LIMITS = {

    "1D": 220,

    "4H": 260,

    "1H": 300,

    "15m": 320,

    "5m": 400,

}

@dataclass

class Cfg:

    # First filter: current Bybit USDT perpetuals ranked by 24h turnover.
    top_turnover: int = 100

    # Legacy values retained only for CLI/workflow compatibility.
    min_turnover: float = 0

    max_symbols: int = 100

    sleep: float = 0.08

    timeout: int = 20

    min_rr: float = 1.8

# =========================================================

# API

# =========================================================

def api(path, params, cfg, retries=3):
    """Fetch public Bybit V5 data with endpoint failover."""
    errors = []
    for base in BASES:
        url = base + path
        for i in range(retries):
            try:
                r = S.get(url, params=params, timeout=cfg.timeout)
                if not r.ok:
                    print("\n========== HTTP DEBUG ==========")
                    print("STATUS :", r.status_code)
                    print("URL    :", r.url)
                    print("SERVER :", r.headers.get("server"))
                    print("CF-RAY :", r.headers.get("cf-ray"))
                    print("TYPE   :", r.headers.get("content-type"))
                    print("BODY   :", r.text[:1000])
                    print("================================\n")
                r.raise_for_status()
                j = r.json()
                if j.get("retCode") == 0:
                    if base != BASES[0]:
                        print("API FAILOVER OK:", base, path)
                    return j
                raise RuntimeError(j.get("retMsg", "Unknown Bybit API error"))
            except Exception as e:
                errors.append(f"{base}: {e}")
                time.sleep(0.4 * (2 ** i))
        print("API ENDPOINT FAILED:", base, path)
    raise RuntimeError(f"{path}: all endpoints failed: {' | '.join(errors[-6:])}")

