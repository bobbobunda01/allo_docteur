# storage/storage_supabase.py
# Stockage Supabase via REST (PostgREST)
# Fonctions: insert_review, count_reviews, list_reviews
# Dépendances: requests

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import requests


class SupabaseError(RuntimeError):
    pass


@dataclass
class SupabaseConfig:
    url: str
    anon_key: str
    table: str = "reviews"  # public.reviews


class SupabaseStorage:
    """
    Utilise l'API REST Supabase (PostgREST):
    - Base URL: {SUPABASE_URL}/rest/v1/{table}
    - Auth: headers apikey + Authorization Bearer

    IMPORTANT:
    - En production, recommande RLS + policy INSERT only (ou INSERT+SELECT si besoin).
    """

    def __init__(self, cfg: SupabaseConfig, timeout_s: int = 20) -> None:
        self.cfg = cfg
        self.timeout_s = timeout_s
        self.base = cfg.url.rstrip("/") + f"/rest/v1/{cfg.table}"

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        h = {
            "apikey": self.cfg.anon_key,
            "Authorization": f"Bearer {self.cfg.anon_key}",
            "Content-Type": "application/json",
        }
        if extra:
            h.update(extra)
        return h

    def insert_review(self, payload: Dict[str, Any]) -> None:
        """
        Insert 1 review row. On stocke aussi le payload brut (jsonb) si la colonne existe.
        """
        # Mapping minimal (aligné au SQL que je t'ai donné)
        row = {
            "kb_version": payload.get("kb_version"),
            "kb_hash": payload.get("kb_hash"),

            "reviewer_role": payload.get("role"),
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

            # audit
            "payload": payload,
        }

        r = requests.post(
            self.base,
            headers=self._headers({"Prefer": "return=minimal"}),
            json=row,
            timeout=self.timeout_s,
        )
        if r.status_code >= 300:
            raise SupabaseError(f"Insert failed [{r.status_code}]: {r.text}")

    def count_reviews(
        self,
        reviewer_role: Optional[str] = None,
        chapter_id: Optional[int] = None,
        entry_name: Optional[str] = None,
    ) -> int:
        """
        Count rows using Prefer: count=exact on a HEAD request.
        NOTE: RLS doit autoriser SELECT si tu veux count côté app.
        Si tu as RLS INSERT-only, count ne marchera pas (tu peux afficher un '—').
        """
        params = {"select": "id"}  # minimal select
        filters = []

        if reviewer_role:
            filters.append(("reviewer_role", "eq", reviewer_role))
        if chapter_id is not None:
            filters.append(("chapter_id", "eq", str(chapter_id)))
        if entry_name:
            filters.append(("entry_name", "eq", entry_name))

        for col, op, val in filters:
            params[f"{col}"] = f"{op}.{val}"

        r = requests.head(
            self.base,
            headers=self._headers({"Prefer": "count=exact"}),
            params=params,
            timeout=self.timeout_s,
        )

        if r.status_code >= 300:
            raise SupabaseError(f"Count failed [{r.status_code}]: {r.text}")

        # Supabase renvoie le count dans Content-Range: 0-0/123
        cr = r.headers.get("Content-Range", "")
        if "/" in cr:
            try:
                return int(cr.split("/")[-1])
            except Exception:
                pass
        return 0

    def list_reviews(
        self,
        reviewer_role: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        order_desc: bool = True,
        chapter_id: Optional[int] = None,
        entry_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List rows. NOTE: RLS doit autoriser SELECT.
        """
        params = {
            "select": "id,created_at,reviewer_role,chapter_id,chapter_label,entry_name,decision,suggested_priority,comments",
            "limit": str(limit),
            "offset": str(offset),
            "order": f"created_at.{ 'desc' if order_desc else 'asc' }",
        }

        filters = []
        if reviewer_role:
            filters.append(("reviewer_role", "eq", reviewer_role))
        if chapter_id is not None:
            filters.append(("chapter_id", "eq", str(chapter_id)))
        if entry_name:
            filters.append(("entry_name", "eq", entry_name))

        for col, op, val in filters:
            params[f"{col}"] = f"{op}.{val}"

        r = requests.get(
            self.base,
            headers=self._headers(),
            params=params,
            timeout=self.timeout_s,
        )
        if r.status_code >= 300:
            raise SupabaseError(f"List failed [{r.status_code}]: {r.text}")
        return r.json()
