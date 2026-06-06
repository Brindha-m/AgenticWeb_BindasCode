"""Structured human-in-the-loop prompt tags (IRCTC-style protocol for travel_runner)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Prompt tags — agent logs HUMAN INPUT NEEDED: [TAG:params] message
TAG_OTP = "OTP"
TAG_TEXT = "TEXT"
TAG_CAPTCHA = "CAPTCHA"
TAG_LOGIN_FORM = "LOGIN_FORM"
TAG_PILGRIM_FORM = "PILGRIM_FORM"
TAG_CONFIRM_DONE = "CONFIRM_DONE"
TAG_PAYMENT_CONFIRM = "PAYMENT_CONFIRM"

_TAG_RE = re.compile(r"^\[([A-Z_]+)(?::([^\]]*))?\]")


@dataclass(frozen=True)
class ParsedPrompt:
    tag: str
    param: str
    message: str
    raw: str


def parse_human_prompt(raw: str) -> ParsedPrompt:
    text = (raw or "").strip()
    m = _TAG_RE.match(text)
    if not m:
        return ParsedPrompt(tag=TAG_TEXT, param="", message=text, raw=text)
    tag = m.group(1)
    param = (m.group(2) or "").strip()
    message = text[m.end() :].strip()
    return ParsedPrompt(tag=tag, param=param, message=message, raw=text)


def format_prompt(tag: str, message: str, param: str = "") -> str:
    if param:
        return f"[{tag}:{param}] {message}"
    return f"[{tag}] {message}"


@dataclass
class PilgrimDetail:
    name: str
    aadhaar: str
    age: str = "30"
    gender: str = "Female"
    id_proof: str = "Aadhaar"


def parse_pilgrim_response(text: str) -> list[PilgrimDetail]:
    """Parse ``name|aadhaar|age|gender|id_proof`` blocks separated by ``||``."""
    pilgrims: list[PilgrimDetail] = []
    for block in (text or "").split("||"):
        parts = [p.strip() for p in block.split("|")]
        if len(parts) >= 2 and parts[0] and parts[1]:
            aadhaar = re.sub(r"\D", "", parts[1])[:12]
            age = parts[2] if len(parts) >= 3 and parts[2] else "30"
            gender = parts[3] if len(parts) >= 4 and parts[3] else "Female"
            id_proof = parts[4] if len(parts) >= 5 and parts[4] else "Aadhaar Card"
            if "aadhaar" in id_proof.lower() or "aadhar" in id_proof.lower():
                id_proof = "Aadhaar Card"
            pilgrims.append(PilgrimDetail(parts[0], aadhaar, age, gender, id_proof))
    return pilgrims


def pilgrim_count_from_param(param: str, default: int = 1) -> int:
    if not param:
        return default
    m = re.search(r"(\d+)", param)
    if m:
        return max(1, min(8, int(m.group(1))))
    return default
