"""Classify SET news headlines into typed announcement categories.

Thai-keyword-driven. Rules are explicit — add/remove patterns as we
encounter new phrasing. Keep the `type` values synced with
reference/data_schemas/news.schema.json enum.

A single headline can match multiple categories; we return the most
specific one (financial_statement > capital_actions > meetings > other).
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple


# Ordered: earlier rules win. Put specific/narrow patterns above broad ones
# so they claim the match before the broad rule fires. The top-of-list
# pre-filters (treasury_stock, mgmt_discussion) exist to prevent headlines
# like "รายงานผลการซื้อหุ้นคืน" from being misclassified as acquisition.
RULES: List[Tuple[str, re.Pattern]] = [
    # ── Pre-filters: specific routine filings that would otherwise match
    #     broader rules below. Check these FIRST.
    ("financial_statement", re.compile(r"งบการเงิน", re.IGNORECASE)),
    ("mgmt_discussion", re.compile(
        r"คำอธิบายและวิเคราะห์ของฝ่ายจัดการ|MD&A",
        re.IGNORECASE,
    )),
    ("share_buyback", re.compile(
        r"ซื้อหุ้นคืน|หุ้นคืน|Treasury Stock",
        re.IGNORECASE,
    )),
    ("regulatory_filing", re.compile(
        # SEC News + form 59 + routine disclosure filings
        r"SEC News|แบบ ?59|F45|F45-3|แบบรายงาน.*แบบ",
        re.IGNORECASE,
    )),

    # ── Capital / share actions
    ("rights_offering", re.compile(
        r"เสนอขายหุ้น|ใช้สิทธิ.*warrant|จัดสรรหุ้น",
        re.IGNORECASE,
    )),
    ("stock_split", re.compile(
        r"แตกพาร์|ลดพาร์|เปลี่ยนแปลงมูลค่าที่ตราไว้",
        re.IGNORECASE,
    )),
    ("capital_restructuring", re.compile(r"เพิ่มทุน|ลดทุน", re.IGNORECASE)),
    ("tender_offer", re.compile(
        r"คำเสนอซื้อหลักทรัพย์|tender offer",
        re.IGNORECASE,
    )),

    # ── Deals (after pre-filters so treasury buybacks don't trigger here)
    ("divestiture", re.compile(
        r"ขายหุ้น|จำหน่ายเงินลงทุน|จำหน่ายหุ้น|จำหน่ายไปซึ่ง|โอนหุ้น",
        re.IGNORECASE,
    )),
    ("acquisition", re.compile(
        r"ซื้อหุ้น|ได้มาซึ่งหุ้น|เข้าซื้อกิจการ|เข้าลงทุน",
        re.IGNORECASE,
    )),

    # ── Related party / inter-company
    ("related_party_transaction", re.compile(
        r"รายการที่เกี่ยวโยง|เกี่ยวโยงกัน|related.party",
        re.IGNORECASE,
    )),

    # ── Cash returns
    ("dividend", re.compile(r"จ่ายปันผล|จ่ายเงินปันผล|ปันผล", re.IGNORECASE)),

    # ── Ownership changes
    ("change_in_shareholders", re.compile(
        r"การเปลี่ยนแปลงผู้ถือหุ้น|แบบรายงานการได้มาหรือจำหน่ายหลักทรัพย์",
        re.IGNORECASE,
    )),

    # ── Governance
    ("governance", re.compile(
        r"เปลี่ยนแปลงกรรมการ|แต่งตั้งกรรมการ|ลาออก.*กรรมการ|มติที่ประชุมคณะกรรมการ",
        re.IGNORECASE,
    )),

    # ── Agreements / intentions
    ("mou", re.compile(
        r"บันทึกข้อตกลง|MOU|ลงนามความร่วมมือ",
        re.IGNORECASE,
    )),
]


VALID_TYPES = [
    "financial_statement",
    "mgmt_discussion",
    "share_buyback",
    "regulatory_filing",
    "divestiture",
    "acquisition",
    "dividend",
    "rights_offering",
    "stock_split",
    "tender_offer",
    "change_in_shareholders",
    "related_party_transaction",
    "governance",
    "capital_restructuring",
    "mou",
    "other",
]


def classify(headline: str) -> str:
    """Return the most specific announcement type for a headline."""
    if not headline:
        return "other"
    for kind, pat in RULES:
        if pat.search(headline):
            return kind
    return "other"


def extract_related_symbols(headline: str, primary: str) -> List[str]:
    """Find ticker-like tokens in the headline, excluding the primary symbol.

    Example: "CPALL ขายหุ้น CPAXT ให้ผู้ลงทุน" with primary=CPALL → ["CPAXT"].
    Relies on the SET convention that tickers are 2-10 uppercase A-Z / digits
    tokens separated from surrounding Thai text.
    """
    if not headline:
        return []
    candidates = re.findall(r"\b[A-Z][A-Z0-9&\-\.]{1,9}\b", headline)
    return sorted({c for c in candidates if c != primary})
