# storage_supabase.py
"""
Supabase storage (PostgREST) for Allo Docteur KB validation reviews.

- No supabase-py dependency (works on Streamlit Cloud / Python 3.13).
- Uses the REST endpoint: {SUPABASE_URL}/rest/v1/{table}
- Auth headers:
    apikey: <anon_key>
    Authorization: Bearer <anon_key>

Expected table columns (example from your schema):
id, created_at, kb_version, kb_hash, reviewer_role, reviewer_name, reviewer_email,
chapter_id, chapter_label, entry_name, entry_type, kb_id, decision,
suggested_priority, suggest_additional_questions, suggest_rule_conflicts, comments, payload
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import json
import requests


class SupabaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    anon_key: str
    table: str = "reviews"
    timeout: int = 30


class SupabaseStorage:
    def __init__(self, cfg: SupabaseConfig):
        self.cfg = cfg
        self.base = cfg.url.rstrip("/")
        self.table = cfg.table

    def _headers(self) -> Dict[str, str]:
        key = self.cfg.anon_key
        return {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _url(self, suffix: str = "") -> str:
        # PostgREST endpoint
        if suffix and not suffix.startswith("?") and not suffix.startswith("/"):
            suffix = "/" + suffix
        return f"{self.base}/rest/v1/{self.table}{suffix}"

    # --------
    # CRUD
    # --------
    def insert_review(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Insert a row into the reviews table.
        Returns inserted row (representation) if successful.
        """
        try:
            r = requests.post(
                self._url(),
                headers={**self._headers(), "Prefer": "return=representation"},
                data=json.dumps(row, ensure_ascii=False),
                timeout=self.cfg.timeout,
            )
        except Exception as e:
            raise SupabaseError(f"Insert request failed: {e}") from e

        if r.status_code not in (200, 201):
            raise SupabaseError(f"Insert failed ({r.status_code}): {r.text}")

        # Return first row (PostgREST returns list)
        try:
            data = r.json()
            if isinstance(data, list) and data:
                return data[0]
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {"raw": r.text}

    def count_reviews(self, reviewer_role: Optional[str] = None) -> int:
        """
        Count reviews (server-side) using Content-Range.
        """
        params = "select=id&limit=1"
        if reviewer_role:
            # URL-encode minimal: spaces -> %20, quotes not needed (eq.<value>)
            # PostgREST filter: reviewer_role=eq.<value>
            from urllib.parse import quote
            params += f"&reviewer_role=eq.{quote(reviewer_role)}"

        try:
            r = requests.get(
                self._url("?" + params),
                headers={**self._headers(), "Prefer": "count=exact"},
                timeout=self.cfg.timeout,
            )
        except Exception as e:
            raise SupabaseError(f"Count request failed: {e}") from e

        if r.status_code != 200:
            raise SupabaseError(f"Count failed ({r.status_code}): {r.text}")

        content_range = r.headers.get("content-range") or r.headers.get("Content-Range") or ""
        # expected like: "0-0/123" or "*/0"
        try:
            total = content_range.split("/")[-1]
            return int(total)
        except Exception:
            # Fallback
            try:
                data = r.json()
                return len(data) if isinstance(data, list) else 0
            except Exception:
                return 0

    def list_latest(self, limit: int = 50, reviewer_role: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Optional: list latest rows (requires created_at column).
        """
        params = f"select=*&order=created_at.desc&limit={int(limit)}"
        if reviewer_role:
            from urllib.parse import quote
            params += f"&reviewer_role=eq.{quote(reviewer_role)}"

        r = requests.get(self._url("?" + params), headers=self._headers(), timeout=self.cfg.timeout)
        if r.status_code != 200:
            raise SupabaseError(f"List failed ({r.status_code}): {r.text}")
        data = r.json()
        return data if isinstance(data, list) else []
