# app.py — Allo Docteur KB Validation (Streamlit)
# Validation coordonnée du KB (médecin généraliste + urgentiste)
# Stockage: Supabase (si configuré) + fallback local JSONL
# Navigation propre (chapitres sans doublons), sélection par entry_name,
# affichage symptômes + population, priorités P1..P4 expliquées.

from __future__ import annotations

import json
import re
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

# Import Supabase storage (optional)
SUPABASE_AVAILABLE = False
try:
    # expected repo structure: storage/storage_supabase.py
    from storage.storage_supabase import SupabaseConfig, SupabaseStorage, SupabaseError  # type: ignore
    SUPABASE_AVAILABLE = True
except Exception:
    SUPABASE_AVAILABLE = False


# ---------------------------
# Configuration
# ---------------------------
st.set_page_config(
    page_title="Allo Docteur — Validation KB (MSF + ICD)",
    layout="wide",
)

DEFAULT_KB_PATH = "data/kb_with_icd_full_CORRECTED.json"  # adapte si besoin
REVIEWS_DIR = Path("reviews")
REVIEWS_DIR.mkdir(exist_ok=True)

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

P_LABELS = {
    "P1": "P1 — Urgence vitale (référer/urgence immédiate)",
    "P2": "P2 — Urgent (avis médical rapide / même jour)",
    "P3": "P3 — Semi-urgent (consultation rapide planifiée)",
    "P4": "P4 — Non urgent (conseils/consultation standard)",
}

ROLE_GENERALISTE = "Médecin généraliste"
ROLE_URGENTISTE = "Urgentiste"


# ---------------------------
# Helpers
# ---------------------------
def normalize_chapter(chapter_raw: str) -> Tuple[Optional[int], Optional[str]]:
    """Retourne (chap_id, chap_label) ou (None, None)."""
    if not chapter_raw or not isinstance(chapter_raw, str):
        return None, None

    m = re.search(r"Chapitre\s*(\d+)", chapter_raw, flags=re.IGNORECASE)
    if not m:
        return None, None

    chap_id = int(m.group(1))
    title = CHAPTER_TITLES.get(chap_id, "")
    chap_label = f"Chapitre {chap_id} - {title}" if title else f"Chapitre {chap_id}"
    return chap_id, chap_label


def load_kb(path: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"KB introuvable: {p.resolve()}")

    kb = json.loads(p.read_text(encoding="utf-8"))

    # Accept formats:
    # - {"items":[...]}
    # - [...]
    # - {"Chapitre 1":[...], "Chapitre 2":[...]} etc.
    if isinstance(kb, dict) and isinstance(kb.get("items"), list):
        items = kb["items"]
        meta = {k: v for k, v in kb.items() if k != "items"}
        return items, meta

    if isinstance(kb, list):
        return kb, {}

    if isinstance(kb, dict):
        flat: List[Dict[str, Any]] = []
        meta: Dict[str, Any] = {}
        for _, v in kb.items():
            if isinstance(v, list) and v and isinstance(v[0], dict) and "entry_name" in v[0]:
                flat.extend(v)
            else:
                # ignore meta-like keys silently
                pass
        if flat:
            return flat, meta

    raise ValueError("Structure KB non reconnue (attendu list ou dict avec items).")


def safe_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else []


def normalize_entry_name(name: Any) -> str:
    if not isinstance(name, str):
        return ""
    return re.sub(r"\s+", " ", name).strip()


def build_chapter_index(items: List[Dict[str, Any]]):
    chapters_map: Dict[int, str] = {}
    by_chapter: Dict[int, List[Dict[str, Any]]] = {}

    for it in items:
        chap_id, chap_label = normalize_chapter(it.get("chapter", ""))
        if chap_id is None or chap_label is None:
            continue
        chapters_map[chap_id] = chap_label
        by_chapter.setdefault(chap_id, []).append(it)

    return chapters_map, by_chapter


def build_entry_index(items_for_chapter: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """entry_name -> item (si doublon, garde le premier)."""
    idx: Dict[str, Dict[str, Any]] = {}
    for it in items_for_chapter:
        nm = normalize_entry_name(it.get("entry_name", ""))
        if not nm:
            continue
        if nm not in idx:
            idx[nm] = it
    return idx


def review_file_for_role(role: str) -> Path:
    slug = "generaliste" if role == ROLE_GENERALISTE else "urgentiste"
    return REVIEWS_DIR / f"reviews_{slug}.jsonl"


def append_review_local(role: str, payload: Dict[str, Any]) -> None:
    f = review_file_for_role(role)
    with f.open("a", encoding="utf-8") as w:
        w.write(json.dumps(payload, ensure_ascii=False) + "\n")


def count_reviews_local(role: str) -> int:
    f = review_file_for_role(role)
    if not f.exists():
        return 0
    return sum(1 for _ in f.open("r", encoding="utf-8"))


def summarize_rules(rules: Any) -> str:
    out = []
    for r in safe_list(rules):
        if not isinstance(r, dict):
            continue
        rid = r.get("id", "")
        pr = r.get("priority", "")
        dec = r.get("decision", {}) if isinstance(r.get("decision"), dict) else {}
        pl = dec.get("priority_level", "")
        act = dec.get("action", "")
        out.append(f"- {rid} | priority={pr} | {pl} | action={act}")
    return "\n".join(out) if out else "—"


@st.cache_resource
def get_supabase_storage() -> Optional["SupabaseStorage"]:
    if not SUPABASE_AVAILABLE:
        return None

    try:
        url = st.secrets.get("SUPABASE_URL", "").strip()
        key = st.secrets.get("SUPABASE_ANON_KEY", "").strip()
        table = st.secrets.get("SUPABASE_TABLE", "reviews").strip() or "reviews"
    except Exception:
        return None

    if not url or not key:
        return None

    cfg = SupabaseConfig(url=url, anon_key=key, table=table, timeout=30)
    return SupabaseStorage(cfg)


def db_enabled() -> bool:
    return get_supabase_storage() is not None


def append_review(role: str, payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Write to Supabase (if configured) AND always write to local JSONL as backup.

    Returns (db_ok, db_error_message)
    """
    # Always keep local backup
    append_review_local(role, payload)

    store = get_supabase_storage()
    if not store:
        return False, "Supabase non configuré (fallback local uniquement)."

    # Map columns expected by Supabase table (avoid surprises)
    row = dict(payload)
    row["reviewer_role"] = payload.get("role")
    row["created_at"] = payload.get("ts")

    try:
        store.insert(row)
        return True, None
    except SupabaseError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)


def count_reviews(role: str) -> int:
    store = get_supabase_storage()
    if not store:
        return count_reviews_local(role)

    try:
        return store.count(filters=[("reviewer_role", "eq", role)])
    except Exception:
        # fallback local if DB count fails
        return count_reviews_local(role)


# ---------------------------
# UI
# ---------------------------
st.title("Allo Docteur — Validation KB (MSF + ICD)")
st.caption("Module de revue clinique: navigation par chapitre et entrée, validation par 2 profils médecins, export des avis en JSONL.")

with st.sidebar:
    st.header("Chargement KB")
    kb_path = st.text_input("Chemin du fichier KB (JSON)", value=DEFAULT_KB_PATH)

    st.divider()
    st.header("Rôle du validateur")
    role = st.radio("Sélection du profil", [ROLE_GENERALISTE, ROLE_URGENTISTE])

    st.divider()
    st.header("Stockage")
    if db_enabled():
        st.success("Supabase: ACTIVÉ ✅")
        st.caption(f"Table: `{st.secrets.get('SUPABASE_TABLE','reviews')}`")
    else:
        st.warning("Supabase: non configuré (stockage local JSONL).")

    st.divider()
    st.header("Priorités (rappel)")
    for k in ["P1", "P2", "P3", "P4"]:
        st.write(f"**{P_LABELS[k]}**")


# Load KB
try:
    items, _meta = load_kb(kb_path)
except Exception as e:
    st.error(f"Impossible de charger le KB: {e}")
    st.stop()

# ignore empty entry_name at UI level
items = [it for it in items if normalize_entry_name(it.get("entry_name", ""))]

chapters_map, by_chapter = build_chapter_index(items)
chapter_options = [(cid, chapters_map[cid]) for cid in sorted(chapters_map.keys())]

if not chapter_options:
    st.error("Aucun chapitre détecté dans le KB (champ 'chapter').")
    st.stop()

col_nav, col_view = st.columns([0.35, 0.65], gap="large")

with col_nav:
    st.subheader("Navigation")

    selected_chap = st.selectbox(
        "Chapitre",
        options=chapter_options,
        format_func=lambda x: x[1],
    )
    selected_chap_id = selected_chap[0]

    entries = by_chapter.get(selected_chap_id, [])
    entry_idx = build_entry_index(entries)
    entry_names = sorted(entry_idx.keys())

    st.caption(f"Entrées disponibles: **{len(entry_names)}**")
    if not entry_names:
        st.warning("Aucune entrée trouvée pour ce chapitre.")
        st.stop()

    selected_entry_name = st.selectbox("Entry (entry_name)", options=entry_names)
    selected_item = entry_idx[selected_entry_name]

    st.divider()
    st.subheader("Statut validation")
    st.write(f"📌 Avis déjà enregistrés ({role}) : **{count_reviews(role)}**")

with col_view:
    st.subheader("Vue KB")
    c1, c2 = st.columns([0.6, 0.4], gap="large")

    with c1:
        st.markdown(f"### {selected_item.get('entry_name','(sans nom)')}")
        st.write(f"**Chapitre:** {chapters_map.get(selected_chap_id, f'Chapitre {selected_chap_id}')}")
        st.write(f"**Type:** {selected_item.get('entry_type','—')}")
        st.write(f"**Population:** {', '.join(safe_list(selected_item.get('population'))) or '—'}")

        ti = selected_item.get("triage_intent", {}) if isinstance(selected_item.get("triage_intent"), dict) else {}
        dp = ti.get("default_priority", "—") or "—"
        isp = ti.get("if_severity_priority", "—") or "—"
        st.write("**Priorité suggérée (KB):**", dp)
        st.write("**Priorité si gravité (KB):**", isp)

        st.markdown("#### Symptômes")
        symptoms = safe_list(selected_item.get("symptoms"))
        if symptoms:
            st.write("\n".join([f"- {s}" for s in symptoms]))
        else:
            st.info("Aucun symptôme renseigné pour cette entrée.")

        st.markdown("#### Signes de gravité (severity_signs)")
        sev = safe_list(selected_item.get("severity_signs"))
        st.write("\n".join([f"- {s}" for s in sev]) if sev else "—")

        st.markdown("#### Red flags")
        rfs = safe_list(selected_item.get("red_flags"))
        if rfs:
            lines = []
            for rf in rfs:
                if isinstance(rf, str):
                    lines.append(f"- {rf}")
                elif isinstance(rf, dict):
                    lines.append(f"- {rf.get('label', rf.get('id','(rf)'))} ({rf.get('severity','—')})")
            st.write("\n".join(lines))
        else:
            st.write("—")

    with c2:
        st.markdown("#### Champs intake (intake_fields)")
        intake_fields = safe_list(selected_item.get("intake_fields"))
        st.code("\n".join(intake_fields) if intake_fields else "—")

        st.markdown("#### Questions de triage (triage_questions)")
        tq = safe_list(selected_item.get("triage_questions"))
        if tq:
            lines = []
            for q in tq:
                if isinstance(q, dict):
                    lines.append(f"- {q.get('id','Q')} — {q.get('label','')}".strip())
                else:
                    lines.append(f"- {q}")
            st.write("\n".join(lines))
        else:
            st.write("—")

        st.markdown("#### Règles (rules)")
        st.code(summarize_rules(selected_item.get("rules")), language="text")

        st.markdown("#### Mapping ICD (si présent)")
        icd_block = selected_item.get("icd_mapping") or selected_item.get("icd") or selected_item.get("icd_matches")
        st.json(icd_block, expanded=False) if icd_block else st.write("—")

    st.divider()
    st.subheader("Formulaire de validation (médecin)")

    with st.form(key=f"review_form_{role}_{selected_chap_id}_{selected_entry_name}"):
        st.markdown(f"**Validateur:** {role}")

        decision = st.radio(
            "Décision",
            ["APPROUVER", "À CORRIGER", "INCERTAIN / À DISCUTER"],
            horizontal=True,
        )

        suggested_priority = st.selectbox(
            "Priorité recommandée (P1→P4)",
            options=["(laisser tel quel)", "P1", "P2", "P3", "P4"],
        )

        suggest_additional_questions = st.text_area(
            "Questions supplémentaires proposées (optionnel)",
            placeholder="Ex: durée des symptômes, notion d'aggravation, grossesse, immunodépression, etc.",
            height=90,
        )

        suggest_rule_conflicts = st.text_area(
            "Conflits / incohérences détectés (optionnel)",
            placeholder="Ex: Deux règles actives donnent P2 et P3 simultanément; proposer dominance; clarifier conditions…",
            height=90,
        )

        comments = st.text_area(
            "Commentaires cliniques (obligatoire si 'À CORRIGER')",
            placeholder="Expliquer précisément ce qui doit être corrigé/clarifié.",
            height=120,
        )

        submitted = st.form_submit_button("Enregistrer l'avis")

        if submitted:
            if decision == "À CORRIGER" and not comments.strip():
                st.error("Commentaires obligatoires si la décision est 'À CORRIGER'.")
            else:
                payload = {
                    "ts": datetime.utcnow().isoformat() + "Z",
                    "role": role,
                    "chapter_id": selected_chap_id,
                    "chapter_label": chapters_map.get(selected_chap_id),
                    "entry_name": selected_item.get("entry_name"),
                    "entry_type": selected_item.get("entry_type"),
                    "decision": decision,
                    "suggested_priority": None if suggested_priority == "(laisser tel quel)" else suggested_priority,
                    "suggest_additional_questions": suggest_additional_questions.strip() or None,
                    "suggest_rule_conflicts": suggest_rule_conflicts.strip() or None,
                    "comments": comments.strip() or None,
                    "kb_id": selected_item.get("id") or selected_item.get("kb_id") or None,
                }

                db_ok, db_err = append_review(role, payload)

                st.success("Avis enregistré ✅ (backup local JSONL toujours écrit)")
                st.info(f"Fichier avis local: {review_file_for_role(role).resolve()}")

                if db_ok:
                    st.success("Enregistré dans Supabase ✅")
                else:
                    st.warning(f"Supabase non écrit: {db_err}")

# Footer
st.divider()
st.caption(
    "⚠️ Important: cette application sert à la validation du KB de triage (orientation/priorisation), "
    "pas à produire un diagnostic. Les règles et contenus doivent être validés par des médecins avant usage en production."
)
