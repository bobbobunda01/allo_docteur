# apply_decisions.py
# Applique les décisions agrégées sur le KB pour produire un KB "final" (validé)
#
# Philosophie: on ne détruit rien — on ajoute des champs de validation.
# - validation_status: APPROVED / NEEDS_FIX / DISCUSS / PENDING / NO_REVIEW
# - validation: {generaliste:{...}, urgentiste:{...}, final:{...}}
# - final_priority_override: si consensus (ou règle que tu choisis)
#
# Usage:
#   python apply_decisions.py \
#     --kb kb_with_icd_full_CORRECTED.json \
#     --review-aggregate out/review_aggregate.json \
#     --out-kb kb_final_validated.json
#
import argparse
import json
import re
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List

ROLE_GENERALISTE = "Médecin généraliste"
ROLE_URGENTISTE = "Urgentiste"

DECISION_APPROUVER = "APPROUVER"
DECISION_A_CORRIGER = "À CORRIGER"
DECISION_INCERTAIN = "INCERTAIN / À DISCUTER"

VALID_DECISIONS = {DECISION_APPROUVER, DECISION_A_CORRIGER, DECISION_INCERTAIN}

CHAPTER_TITLES = {
    1: "Quelques symptômes ou syndromes",
    2: "Pathologie respiratoire",
    3: "Pathologie digestive",
    4: "Pathologie dermatologique",
    5: "Pathologie ophtalmologique",
    6: "Maladies parasitaires",
    7: "Maladies bactériennes",
    8: "Maladies virales",
    9: "Pathologie génito-urinaire",
    10: "Pathologie médico-chirurgicale",
    11: "Troubles psychiques chez l’adulte",
    12: "Autres pathologies",
}

def normalize_entry_name(name: Any) -> str:
    if not isinstance(name, str):
        return ""
    return re.sub(r"\s+", " ", name).strip()

def normalize_chapter(chapter_raw: Any) -> Tuple[Optional[int], Optional[str]]:
    if not isinstance(chapter_raw, str) or not chapter_raw.strip():
        return None, None
    m = re.search(r"Chapitre\s*(\d+)", chapter_raw, flags=re.IGNORECASE)
    if not m:
        return None, None
    chap_id = int(m.group(1))
    title = CHAPTER_TITLES.get(chap_id, "")
    label = f"Chapitre {chap_id} - {title}" if title else f"Chapitre {chap_id}"
    return chap_id, label

def load_kb_items(kb_path: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    kb = json.loads(kb_path.read_text(encoding="utf-8"))
    if isinstance(kb, dict) and isinstance(kb.get("items"), list):
        return kb, kb["items"]
    if isinstance(kb, list):
        return {"items": kb}, kb
    if isinstance(kb, dict):
        # flatten dict-of-lists
        flat = []
        meta = {}
        for k, v in kb.items():
            if isinstance(v, list) and v and isinstance(v[0], dict) and "entry_name" in v[0]:
                flat.extend(v)
            else:
                meta[k] = v
        if flat:
            root = dict(meta)
            root["items"] = flat
            return root, flat
    raise ValueError("KB structure not recognized")

def pick_final_status(g_dec: Optional[str], u_dec: Optional[str]) -> str:
    """
    Définir un statut final simple et robuste.
    """
    if not g_dec and not u_dec:
        return "NO_REVIEW"
    if not g_dec or not u_dec:
        return "PENDING"  # manque un avis
    if g_dec == DECISION_A_CORRIGER or u_dec == DECISION_A_CORRIGER:
        return "NEEDS_FIX"
    if g_dec == DECISION_INCERTAIN or u_dec == DECISION_INCERTAIN:
        return "DISCUSS"
    if g_dec == DECISION_APPROUVER and u_dec == DECISION_APPROUVER:
        return "APPROVED"
    return "DISCUSS"

def consensus_priority(g_p: Optional[str], u_p: Optional[str]) -> Optional[str]:
    if isinstance(g_p, str) and isinstance(u_p, str) and g_p and u_p and g_p == u_p:
        return g_p
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", required=True, help="KB JSON")
    ap.add_argument("--review-aggregate", required=True, help="out/review_aggregate.json")
    ap.add_argument("--out-kb", required=True, help="KB final enrichi des validations")
    args = ap.parse_args()

    kb_path = Path(args.kb)
    agg_path = Path(args.review_aggregate)

    kb_root, items = load_kb_items(kb_path)
    agg = json.loads(agg_path.read_text(encoding="utf-8"))

    aggregate_list = agg.get("aggregate", [])
    if not isinstance(aggregate_list, list):
        raise ValueError("review_aggregate.json mal formé: aggregate doit être une liste")

    # Build lookup from aggregate: (chapter_id, entry_name) -> analysis
    lookup: Dict[Tuple[int, str], Dict[str, Any]] = {}
    for rec in aggregate_list:
        cid = rec.get("chapter_id")
        en = normalize_entry_name(rec.get("entry_name"))
        if isinstance(cid, int) and en:
            lookup[(cid, en)] = rec

    # Apply
    for it in items:
        en = normalize_entry_name(it.get("entry_name"))
        if not en:
            continue
        cid, chap_label = normalize_chapter(it.get("chapter"))
        if cid is None:
            continue

        rec = lookup.get((cid, en))
        it.setdefault("validation", {})
        it["validation"].setdefault("meta", {
            "chapter_id": cid,
            "chapter_label": chap_label,
        })

        if not rec:
            it["validation_status"] = "NO_REVIEW"
            continue

        analysis = rec.get("analysis", {})
        g = analysis.get("generaliste")
        u = analysis.get("urgentiste")

        g_dec = (g or {}).get("decision")
        u_dec = (u or {}).get("decision")
        g_p = (g or {}).get("suggested_priority")
        u_p = (u or {}).get("suggested_priority")

        status = pick_final_status(g_dec, u_dec)
        it["validation_status"] = status

        it["validation"][ROLE_GENERALISTE] = g
        it["validation"][ROLE_URGENTISTE] = u

        # If both approved and agree on priority -> override suggestion
        pr = consensus_priority(g_p, u_p)
        if status == "APPROVED" and pr:
            it["final_priority_override"] = pr

        # store conflict reasons
        it["validation"]["analysis"] = {
            "status": analysis.get("status"),
            "reasons": analysis.get("reasons", []),
            "all_reviews_count": rec.get("all_reviews_count"),
            "last_ts": rec.get("last_ts"),
        }

    out_path = Path(args.out_kb)
    out_path.write_text(json.dumps(kb_root, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] Wrote validated KB: {out_path}")

if __name__ == "__main__":
    main()
