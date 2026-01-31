# storage_supabase.py
import json
import requests
from typing import Dict, Any


class SupabaseStorage:
    def __init__(self, supabase_url: str, supabase_key: str, table: str = "reviews", timeout: int = 30):
        self.url = supabase_url.rstrip("/")
        self.key = supabase_key
        self.table = table
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    def insert_review(self, row: Dict[str, Any]) -> Dict[str, Any]:
        endpoint = f"{self.url}/rest/v1/{self.table}"
        r = requests.post(endpoint, headers=self._headers(), data=json.dumps(row), timeout=self.timeout)

        if r.status_code >= 300:
            raise RuntimeError(f"Supabase insert failed: {r.status_code} | {r.text}")

        return {"status_code": r.status_code, "data": r.json()}

    def count_reviews(self, reviewer_role: str) -> int:
        endpoint = (
            f"{self.url}/rest/v1/{self.table}"
            f"?select=id&reviewer_role=eq.{requests.utils.quote(reviewer_role)}"
        )

        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Prefer": "count=exact",
        }

        r = requests.get(endpoint, headers=headers, timeout=self.timeout)
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase count failed: {r.status_code} | {r.text}")

        content_range = r.headers.get("content-range", "")
        if "/" in content_range:
            return int(content_range.split("/")[-1])
        return 0
