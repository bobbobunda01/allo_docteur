# storage_supabase.py
import json
import requests
from typing import Optional, Dict, Any

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
            # Message d’erreur lisible (RLS / NOT NULL / table name / etc.)
            raise RuntimeError(f"Supabase insert failed: {r.status_code} | {r.text}")

        # Supabase retourne souvent une liste de lignes insérées
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        return {"status_code": r.status_code, "data": data}

    def count_reviews(self) -> int:
        # count exact via header Content-Range
        endpoint = f"{self.url}/rest/v1/{self.table}?select=id"
        r = requests.get(endpoint, headers={**self._headers(), "Prefer": "count=exact"}, timeout=self.timeout)
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase count failed: {r.status_code} | {r.text}")
        cr = r.headers.get("content-range", "")  # ex: "0-0/123"
        if "/" in cr:
            return int(cr.split("/")[-1])
        return 0
