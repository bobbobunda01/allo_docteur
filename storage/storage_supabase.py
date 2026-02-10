# storage/storage_supabase.py
# Minimal Supabase (PostgREST) client for Streamlit KB review app.
# Features: insert, count (optionally filtered), list (optional).

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests


class SupabaseError(RuntimeError):
    """Raised when Supabase REST calls fail (RLS, bad table, bad key, etc.)."""


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    anon_key: str
    table: str = "reviews"
    timeout: int = 30


class SupabaseStorage:
    def __init__(self, config: SupabaseConfig):
        self.url = config.url.rstrip("/")
        self.key = config.anon_key
        self.table = config.table
        self.timeout = config.timeout

    def _headers(self, prefer: Optional[str] = None) -> Dict[str, str]:
        h = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if prefer:
            h["Prefer"] = prefer
        return h

    def _endpoint(self) -> str:
        return f"{self.url}/rest/v1/{self.table}"

    def insert(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Insert one row. Returns inserted row(s) if allowed by RLS."""
        r = requests.post(
            self._endpoint(),
            headers=self._headers(prefer="return=representation"),
            data=json.dumps(row, ensure_ascii=False),
            timeout=self.timeout,
        )
        if r.status_code >= 300:
            raise SupabaseError(f"Supabase insert failed: {r.status_code} | {r.text}")
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        return {"status_code": r.status_code, "data": data}

    def count(self, filters: Optional[List[Tuple[str, str, Any]]] = None) -> int:
        """Count rows using Content-Range with Prefer: count=exact.

        filters: list of (column, op, value) where op is one of: eq, ilike, in, is, gt, gte, lt, lte.
        Example: [("reviewer_role","eq","Médecin généraliste")]
        """
        params: Dict[str, str] = {"select": "id", "limit": "1"}  # minimal payload
        if filters:
            for col, op, val in filters:
                # PostgREST filter syntax: col=eq.value
                if val is None:
                    params[col] = "is.null"
                elif op == "in":
                    # val should be list/tuple
                    vals = ",".join(str(x) for x in val)
                    params[col] = f"in.({vals})"
                else:
                    params[col] = f"{op}.{val}"

        r = requests.get(
            self._endpoint(),
            headers=self._headers(prefer="count=exact"),
            params=params,
            timeout=self.timeout,
        )
        if r.status_code >= 300:
            raise SupabaseError(f"Supabase count failed: {r.status_code} | {r.text}")
        cr = r.headers.get("content-range", "")  # e.g. '0-0/123'
        if "/" in cr:
            try:
                return int(cr.split("/")[-1])
            except Exception:
                return 0
        return 0

    def list(self, filters: Optional[List[Tuple[str, str, Any]]] = None, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        params: Dict[str, str] = {"select": "*", "limit": str(limit), "offset": str(offset), "order": "created_at.desc"}
        if filters:
            for col, op, val in filters:
                if val is None:
                    params[col] = "is.null"
                elif op == "in":
                    vals = ",".join(str(x) for x in val)
                    params[col] = f"in.({vals})"
                else:
                    params[col] = f"{op}.{val}"

        r = requests.get(self._endpoint(), headers=self._headers(), params=params, timeout=self.timeout)
        if r.status_code >= 300:
            raise SupabaseError(f"Supabase list failed: {r.status_code} | {r.text}")
        return r.json()
