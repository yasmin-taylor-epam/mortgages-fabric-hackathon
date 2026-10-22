"""Models and evaluation work package for the fictional lender HomeBridge.

Trains the refinancing-risk classifier, the rules baseline and the behavioural
clusters. Scores accounts, assigns segments, writes Gold tables and publishes
risk_updated events. Evaluates on later deal cohorts. Synthetic data only.
"""
from .config import ensure_hbdata

ensure_hbdata()

__all__ = ["ensure_hbdata"]
