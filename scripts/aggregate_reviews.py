# aggregate_reviews.py
# Agrège les avis des médecins (généraliste + urgentiste) depuis ./reviews/*.jsonl
# Sorties:
#  - review_aggregate.json : agrégation par entry (par chapitre + entry_name)
#  - review_summary.json   : synthèse (statistiques, conflits, items à escalader)
#
# Usage:
#   python aggregate_reviews.py --kb kb_with_icd_full_CORRECTED.json --reviews-dir reviews --out-dir out
#
import argparse
import json
import re
from pathlib import Path
from collections import defaultdict, Counter
from typing import Dict, Any, Tuple, Optional, List
from datetime import datetime

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

def load_kb_items(kb_path: Path) -> List[Dict[str, Any]]:
    kb = json.loads(kb_path.read_text(encoding="utf-8"))
    if isinstance(kb, dict) and isinstance(kb.get("items"), list):
        items = kb["items"]
    elif isinstance(kb, list):
        items = kb
    elif isinstance(kb, dict):
        # flatten dict-of-lists
        items = []
        for v in kb.values():
            if isinstance(v, list) and v and isinstance(v[0], dict) and "entry_name" in v[0]:
                items.extend(v)
    else:
        raise ValueError("KB structure not recognized")
    # normalize minimal
    out=[]
    for it in items:
        if not isinstance(it, dict):
            continue
        en = normalize_entry_name(it.get("entry_name"))
        if not en:
            continue
        it2 = dict(it)
        it2["entry_name"] = en
        chap_id, chap_label = normalize_chapter(it2.get("chapter"))
        it2["_chapter_id"] = chap_id
        it2["_chapter_label"] = chap_label
        out.append(it2)
    return out

def load_reviews(reviews_dir: Path) -> List[Dict[str, Any]]:
    reviews = []
    for f in sorted(reviews_dir.glob("reviews_*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    reviews.append(obj)
            except Exception:
                # ignore bad line
                continue
    return reviews

def review_key(r: Dict[str, Any]) -> Tuple[Optional[int], str]:
    chap_id = r.get("chapter_id")
    if isinstance(chap_id, int):
        cid = chap_id
    else:
        cid, _ = normalize_chapter(r.get("chapter_label"))  # fallback
    name = normalize_entry_name(r.get("entry_name"))
    return cid, name

def last_review_per_role(reviews_for_item: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Garder le dernier avis (par timestamp) par rôle.
    """
    by_role = {}
    # sort by ts ascending when possible
    def ts_key(x):
        t = x.get("ts")
        return t if isinstance(t, str) else ""
    for r in sorted(reviews_for_item, key=ts_key):
        role = r.get("role")
        if isinstance(role, str) and role.strip():
            by_role[role] = r
    return by_role

def analyze_pair(g: Optional[Dict[str, Any]], u: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Détecte accords / désaccords, conflits de priorité, besoin d’escalade.
    """
    out = {
        "generaliste": g,
        "urgentiste": u,
        "status": None,  # AGREEMENT / CONFLICT / MISSING / NEEDS_DISCUSSION
        "reasons": [],
    }

    if not g and not u:
        out["status"] = "MISSING"
        out["reasons"].append("Aucun avis.")
        return out

    if not g or not u:
        out["status"] = "MISSING"
        out["reasons"].append("Avis manquant (1 seul médecin).")
        return out

    dg = g.get("decision")
    du = u.get("decision")
    if dg not in VALID_DECISIONS or du not in VALID_DECISIONS:
        out["status"] = "NEEDS_DISCUSSION"
        out["reasons"].append("Décision invalide détectée.")
        return out

    # Simple rules:
    # - If any "À CORRIGER" -> escalate
    # - If any "INCERTAIN" -> needs discussion
    # - If both APPROUVER -> agreement
    if dg == DECISION_A_CORRIGER or du == DECISION_A_CORRIGER:
        out["status"] = "CONFLICT"
        out["reasons"].append("Au moins un médecin demande correction.")
    elif dg == DECISION_INCERTAIN or du == DECISION_INCERTAIN:
        out["status"] = "NEEDS_DISCUSSION"
        out["reasons"].append("Au moins un médecin est incertain.")
    elif dg == DECISION_APPROUVER and du == DECISION_APPROUVER:
        out["status"] = "AGREEMENT"
    else:
        out["status"] = "NEEDS_DISCUSSION"
        out["reasons"].append("Décisions différentes.")

    # Priority conflict
    pg = g.get("suggested_priority")
    pu = u.get("suggested_priority")
    if isinstance(pg, str) and isinstance(pu, str) and pg and pu and pg != pu:
        out["reasons"].append(f"Conflit de priorité suggérée: {pg} vs {pu}")

    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", required=True, help="KB JSON (ex: kb_with_icd_full_CORRECTED.json)")
    ap.add_argument("--reviews-dir", default="reviews", help="Dossier des avis (jsonl)")
    ap.add_argument("--out-dir", default="out", help="Dossier sortie")
    args = ap.parse_args()

    kb_path = Path(args.kb)
    reviews_dir = Path(args.reviews_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    kb_items = load_kb_items(kb_path)
    reviews = load_reviews(reviews_dir)

    # Index KB items by (chapter_id, entry_name)
    kb_index = {}
    for it in kb_items:
        cid = it.get("_chapter_id")
        en = it.get("entry_name")
        if isinstance(cid, int) and isinstance(en, str) and en:
            kb_index[(cid, en)] = it

    # Group reviews by item
    grouped = defaultdict(list)
    for r in reviews:
        cid, en = review_key(r)
        if cid is None or not en:
            continue
        grouped[(cid, en)].append(r)

    aggregate = []
    stats = Counter()
    conflicts = []
    missing = []
    needs_discussion = []

    for key, revs in grouped.items():
        cid, en = key
        by_role = last_review_per_role(revs)
        g = by_role.get(ROLE_GENERALISTE)
        u = by_role.get(ROLE_URGENTISTE)

        analysis = analyze_pair(g, u)
        status = analysis["status"]
        stats[status] += 1

        chapter_label = CHAPTER_TITLES.get(cid, "")
        chapter_label = f"Chapitre {cid} - {chapter_label}" if chapter_label else f"Chapitre {cid}"

        kb_item = kb_index.get((cid, en))

        rec = {
            "chapter_id": cid,
            "chapter_label": chapter_label,
            "entry_name": en,
            "kb_id": (kb_item or {}).get("id") or (kb_item or {}).get("kb_id"),
            "analysis": analysis,
            "all_reviews_count": len(revs),
            "last_ts": max([r.get("ts","") for r in revs if isinstance(r.get("ts"), str)] or [""]),
        }
        aggregate.append(rec)

        if status == "CONFLICT":
            conflicts.append(rec)
        elif status == "MISSING":
            missing.append(rec)
        elif status == "NEEDS_DISCUSSION":
            needs_discussion.append(rec)

    # Also detect KB items with no reviews at all
    no_review_items = []
    for (cid, en), it in kb_index.items():
        if (cid, en) not in grouped:
            chapter_label = CHAPTER_TITLES.get(cid, "")
            chapter_label = f"Chapitre {cid} - {chapter_label}" if chapter_label else f"Chapitre {cid}"
            no_review_items.append({
                "chapter_id": cid,
                "chapter_label": chapter_label,
                "entry_name": en,
                "kb_id": it.get("id") or it.get("kb_id"),
                "status": "NO_REVIEW",
            })
    stats["NO_REVIEW"] = len(no_review_items)

    out_agg = out_dir / "review_aggregate.json"
    out_sum = out_dir / "review_summary.json"

    out_agg.write_text(json.dumps({
        "generated_at": datetime.utcnow().isoformat()+"Z",
        "kb": str(kb_path),
        "reviews_dir": str(reviews_dir),
        "aggregate": aggregate,
        "no_review_items": no_review_items,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    out_sum.write_text(json.dumps({
        "generated_at": datetime.utcnow().isoformat()+"Z",
        "counts": dict(stats),
        "conflicts_count": len(conflicts),
        "needs_discussion_count": len(needs_discussion),
        "missing_pair_count": len(missing),
        "examples": {
            "conflicts": conflicts[:10],
            "needs_discussion": needs_discussion[:10],
            "missing": missing[:10],
            "no_review": no_review_items[:10],
        }
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[OK] Wrote: {out_agg}")
    print(f"[OK] Wrote: {out_sum}")
    print("[Counts]", dict(stats))

if __name__ == "__main__":
    main()
