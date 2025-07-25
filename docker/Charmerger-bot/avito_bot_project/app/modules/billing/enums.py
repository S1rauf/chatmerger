# /app/modules/billing/enums.py
from enum import Enum

class TariffPlan(str, Enum):
    START = "start"
    PRO = "pro"
    EXPERT = "expert"