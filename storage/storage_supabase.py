# storage_supabase.py
import json
import requests
from typing import Optional, Dict, Any, List


class SupabaseError(Exception):
    pass


class SupabaseStorage:
    """
    Stockage Supabase (PostgREST):
    - insert_review(payload) : mappe le payload Streamlit vers la table 'reviews'
    - count_reviews(reviewer_role=...) : count exact (filtrable)
    - list_reviews(reviewer_role=..., limit=...) : optionnel
    """

    def __init__(
        self,
        supabase_url: str,
        supabase_anon_key: str,
        table: str = "reviews",
        timeout: int = 30,
    ):
        self.url = supabase_url.rstrip("/")
        self.key = supabase_anon_key
        self.table = table
        self.timeout = timeout

    def _headers(self, prefer: Optional[str] = None) -> Dict[str, str]:
        h = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if prefer:
            h["Prefer"] = prefer
        return h

    def _endpoint(self, path: str) -> str:
        return f"{self.url}/rest/v1/{path.lstrip('/')}"

    # ---------------------------
    # Mapping payload -> row DB
    # ---------------------------
    def _map_payload_to_row(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ta table Supabase a les colonnes :
        id, created_at, kb_version, kb_hash, reviewer_role, reviewer_name, reviewer_email,
        chapter_id, chapter_label, entry_name, entry_type, kb_id, decision, suggested_priority,
        suggest_additional_questions, suggest_rule_conflicts, comments, payload
        """

        # On garde le payload brut complet en JSONB dans la colonne "payload"
        row = {
            "kb_version": payload.get("kb_version") or "v1",
            "kb_hash": payload.get("kb_hash") or payload.get("kb_sha256") or "unknown",
            "reviewer_role": payload.get("role"),  # role -> reviewer_role
            "reviewer_name": payload.get("reviewer_name"),
            "reviewer_email": payload.get("reviewer_email"),
            "chapter_id": payload.get("chapter_id"),
            "chapter_label": payload.get("chapter_label"),
            "entry_name": payload.get("entry_name"),
            "entry_type": payload.get("entry_type"),
            "kb_id": payload.get("kb_id"),
            "decision": payload.get("decision"),
            "suggested_priority": payload.get("suggested_priority"),
            "suggest_additional_questions": payload.get("suggest_additional_questions"),
            "suggest_rule_conflicts": payload.get("suggest_rule_conflicts"),
            "comments": payload.get("comments"),
            "payload": payload,  # JSONB
        }

        # Nettoyage : enlever les clés None pour éviter certains NOT NULL / policies strictes
        return {k: v for k, v in row.items() if v is not None}

    # ---------------------------
    # Insert
    # ---------------------------
    def insert_review(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        endpoint = self._endpoint(self.table)
        row = self._map_payload_to_row(payload)

        r = requests.post(
            endpoint,
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

    # ---------------------------
    # Count (exact) with optional filter
    # ---------------------------
    def count_reviews(self, reviewer_role: Optional[str] = None) -> int:
        endpoint = self._endpoint(self.table)

        params = {"select": "id"}
        if reviewer_role:
            params["reviewer_role"] = f"eq.{reviewer_role}"
        # limit=1 uniquement pour réduire la charge; le count exact vient du header
        params["limit"] = "1"

        r = requests.get(
            endpoint,
            headers=self._headers(prefer="count=exact"),
            params=params,
            timeout=self.timeout,
        )
        if r.status_code >= 300:
            raise SupabaseError(f"Supabase count failed: {r.status_code} | {r.text}")

        # content-range: */0  ou 0-0/123
        cr = r.headers.get("content-range", "")
        if "/" in cr:
            return int(cr.split("/")[-1])
        return 0

    # ---------------------------
    # Optional list
    # ---------------------------
    def list_reviews(self, reviewer_role: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        endpoint = self._endpoint(self.table)
        params = {"select": "id,created_at,reviewer_role,chapter_id,entry_name,decision", "order": "created_at.desc", "limit": str(limit)}
        if reviewer_role:
            params["reviewer_role"] = f"eq.{reviewer_role}"

        r = requests.get(endpoint, headers=self._headers(), params=params, timeout=self.timeout)
        if r.status_code >= 300:
            raise SupabaseError(f"Supabase list failed: {r.status_code} | {r.text}")
        return r.json()
