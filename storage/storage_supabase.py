# storage_supabase.py
# Minimal Supabase (PostgREST) client for Allo Docteur KB reviews
# - insert_review
# - count_reviews (optionally filtered by reviewer_role)
# - list_reviews (optional)
#
# Uses REST endpoint: {SUPABASE_URL}/rest/v1/{table}
#dffdfdsfdsfdfds
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


class SupabaseError(RuntimeError):
    """Raised when Supabase REST operations fail."""


@dataclass
class SupabaseConfig:
    url: str
    anon_key: str
    table: str = "reviews"
    schema: str = "public"
    timeout_s: int = 30

    @property
    def rest_base(self) -> str:
        return self.url.rstrip("/") + "/rest/v1"

    def headers(self) -> Dict[str, str]:
        # PostgREST expects both apikey and Authorization
        return {
            "apikey": self.anon_key,
            "Authorization": f"Bearer {self.anon_key}",
            "Content-Type": "application/json",
            # Ensure schema is correctly targeted
            "Accept-Profile": self.schema,
            "Content-Profile": self.schema,
        }


class SupabaseStorage:
    def __init__(self, config: SupabaseConfig):
        self.config = config

    @staticmethod
    def is_configured(url: Optional[str], key: Optional[str]) -> bool:
        return bool(url and key and str(url).startswith("http"))

    def _url(self, path: str) -> str:
        return f"{self.config.rest_base}/{path.lstrip('/')}"

    def insert_review(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Insert a row into the reviews table.
        IMPORTANT: Row keys must match your table columns.
        """
        endpoint = self._url(self.config.table)
        # Return inserted row(s)
        headers = {**self.config.headers(), "Prefer": "return=representation"}
        r = requests.post(endpoint, headers=headers, json=row, timeout=self.config.timeout_s)
        if r.status_code >= 400:
            raise SupabaseError(f"insert failed ({r.status_code}): {r.text}")
        data = r.json()
        if isinstance(data, list) and data:
            return data[0]
        return {"result": data}

    def count_reviews(self, reviewer_role: Optional[str] = None) -> int:
        """
        Count rows. If reviewer_role provided, count only that role.
        Uses Content-Range with Prefer: count=exact
        """
        q = f"{self.config.table}?select=id&limit=1"
        if reviewer_role:
            # exact match, URL encoded by requests
            q += f"&reviewer_role=eq.{reviewer_role}"

        endpoint = self._url(q)
        headers = {**self.config.headers(), "Prefer": "count=exact"}
        r = requests.get(endpoint, headers=headers, timeout=self.config.timeout_s)
        if r.status_code >= 400:
            raise SupabaseError(f"count failed ({r.status_code}): {r.text}")

        # Content-Range format: 0-0/123 or */0
        cr = r.headers.get("content-range") or r.headers.get("Content-Range") or ""
        if "/" in cr:
            try:
                total = int(cr.split("/")[-1])
                return total
            except Exception:
                pass

        # Fallback: length of returned array (not exact)
        data = r.json()
        return len(data) if isinstance(data, list) else 0

    def list_reviews(
        self,
        reviewer_role: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        order_by_created_at_desc: bool = True,
    ) -> List[Dict[str, Any]]:
        q = f"{self.config.table}?select=*&limit={int(limit)}&offset={int(offset)}"
        if reviewer_role:
            q += f"&reviewer_role=eq.{reviewer_role}"
        if order_by_created_at_desc:
            q += "&order=created_at.desc"

        endpoint = self._url(q)
        r = requests.get(endpoint, headers=self.config.headers(), timeout=self.config.timeout_s)
        if r.status_code >= 400:
            raise SupabaseError(f"list failed ({r.status_code}): {r.text}")
        data = r.json()
        return data if isinstance(data, list) else []
