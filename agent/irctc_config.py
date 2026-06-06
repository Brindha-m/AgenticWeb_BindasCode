"""
Load IRCTC automation settings from .env.

Credentials, journey details, and passenger rows stay in environment variables
so they are never hard-coded in scripts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _ROOT / ".env"


def _load_env() -> None:
    load_dotenv(dotenv_path=_ENV_PATH, override=True)


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Passenger:
    name: str
    age: str
    gender: str
    berth: str = ""


@dataclass
class IRCTCConfig:
    username: str = ""
    password: str = ""
    from_station: str = "CBE"
    from_name: str = "Coimbatore Junction - CBE"
    to_station: str = "MAS"
    to_name: str = "MGR CHENNAI CENTRAL - MAS"
    journey_date: str = ""
    journey_date_offset_days: int = 2  # used when IRCTC_JOURNEY_DATE is empty
    train_class: str = "SL"
    preferred_train: str = ""
    mobile: str = ""
    payment_method: str = "IRCTC_IPAY"  # IRCTC_IPAY | UPI | WALLET
    payment_provider: str = "Paytm"     # Paytm, PhonePe, etc. on gateway page
    passengers: list[Passenger] = field(default_factory=list)
    captcha_mode: str = "manual"  # manual | terminal | claude
    headless: bool = False
    stop_before_payment: bool = True
    login_only: bool = False
    keep_alive_seconds: int = 120
    engine: str = "playwright"  # playwright | cdp
    slow_mo: int = 50
    anthropic_model: str = "claude-sonnet-4-6"

    @classmethod
    def from_env(cls) -> "IRCTCConfig":
        _load_env()

        journey_date = os.getenv("IRCTC_JOURNEY_DATE", "").strip()
        offset_days = int(os.getenv("IRCTC_JOURNEY_DATE_OFFSET_DAYS", "2") or "2")
        if not journey_date:
            journey_date = (datetime.now() + timedelta(days=offset_days)).strftime("%d/%m/%Y")

        count = max(1, min(6, int(os.getenv("IRCTC_PASSENGER_COUNT", "1") or "1")))
        passengers: list[Passenger] = []
        for i in range(1, count + 1):
            name = os.getenv(f"IRCTC_P{i}_NAME", "").strip()
            age = os.getenv(f"IRCTC_P{i}_AGE", "").strip()
            gender = os.getenv(f"IRCTC_P{i}_GENDER", "").strip()
            berth = os.getenv(f"IRCTC_P{i}_BERTH", "").strip()
            if name and age and gender:
                passengers.append(Passenger(name=name, age=age, gender=gender, berth=berth))

        return cls(
            username=os.getenv("IRCTC_USERNAME", "").strip(),
            password=os.getenv("IRCTC_PASSWORD", "").strip(),
            from_station=os.getenv("IRCTC_FROM_STATION", "CBE").strip(),
            from_name=os.getenv("IRCTC_FROM_NAME", "Coimbatore Junction - CBE").strip(),
            to_station=os.getenv("IRCTC_TO_STATION", "MAS").strip(),
            to_name=os.getenv("IRCTC_TO_NAME", "MGR CHENNAI CENTRAL - MAS").strip(),
            journey_date=journey_date,
            journey_date_offset_days=offset_days,
            train_class=os.getenv("IRCTC_CLASS", "SL").strip().upper(),
            preferred_train=os.getenv("IRCTC_PREFERRED_TRAIN", "").strip(),
            mobile=os.getenv("IRCTC_MOBILE", "").strip(),
            payment_method=os.getenv("IRCTC_PAYMENT_METHOD", "IRCTC_IPAY").strip().upper(),
            payment_provider=os.getenv("IRCTC_PAYMENT_PROVIDER", "Paytm").strip(),
            passengers=passengers,
            captcha_mode=os.getenv("CAPTCHA_MODE", "manual").strip().lower(),
            headless=_bool(os.getenv("HEADLESS"), default=False),
            stop_before_payment=_bool(os.getenv("STOP_BEFORE_PAYMENT"), default=True),
            login_only=_bool(os.getenv("LOGIN_ONLY"), default=False),
            keep_alive_seconds=int(os.getenv("KEEP_ALIVE_SECONDS", "120") or "120"),
            engine=os.getenv("IRCTC_ENGINE", "playwright").strip().lower(),
            slow_mo=int(os.getenv("PLAYWRIGHT_SLOW_MO", "50") or "50"),
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6").strip(),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.username:
            errors.append("IRCTC_USERNAME is missing in .env")
        if not self.password:
            errors.append("IRCTC_PASSWORD is missing in .env")
        if self.captcha_mode == "claude" and not os.getenv("ANTHROPIC_API_KEY"):
            errors.append("CAPTCHA_MODE=claude requires ANTHROPIC_API_KEY in .env")
        if not self.login_only:
            if not self.mobile:
                errors.append("IRCTC_MOBILE is missing in .env")
            if not self.passengers:
                errors.append("Add at least one passenger (IRCTC_P1_NAME, IRCTC_P1_AGE, IRCTC_P1_GENDER)")
        return errors


HumanCallback = Callable[[str], str]
