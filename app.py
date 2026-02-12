# app.py — Allo Docteur KB Validation (Streamlit)
# Objectif: Validation coordonnée du KB (médecin généraliste + urgentiste),
# navigation propre (chapitres sans doublons), sélection par entry_name,
# affichage des symptômes + population, priorités P1..P4 expliquées,
# export des avis en JSONL + stockage Supabase (optionnel).

import json
import re
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

import streamlit as st

# Supabase (optionnel)
from storage.storage_supabase import SupabaseConfig, SupabaseStorage, SupabaseError


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
# Helpers (KB)
# ---------------------------
def normalize_chapter(chapter_raw: str) -> Tuple[Optional[int], Optional[str]]:
    if not chapter_raw or not isinstance(chapter_raw, str):
        return None, None
    m = re.search(r"Chapitre\s*(\d+)", chapter_raw, flags=re.IGNORECASE)
    if not m:
        return None, None
    chap_id = int(m.group(1))
    title = CHAPTER_TITLES.get(chap_id, "")
    chap_label = f"Chapitre {chap_id} — {title}" if title else f"Chapitre {chap_id}"
    return chap_id, chap_label


def load_kb(path: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any], str, str]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"KB introuvable: {p.resolve()}")

    raw_text = p.read_text(encoding="utf-8", errors="strict")
    kb_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]  # court mais stable
    kb_obj = json.loads(raw_text)

    # Accept formats:
    # - {"items":[...]}
    # - [...]
    # - {"Chapitre 1":[...], "Chapitre 2":[...]} etc.
    if isinstance(kb_obj, dict) and isinstance(kb_obj.get("items"), list):
        items = kb_obj["items"]
        meta = {k: v for k, v in kb_obj.items() if k != "items"}
    elif isinstance(kb_obj, list):
        items, meta = kb_obj, {}
    elif isinstance(kb_obj, dict):
        flat = []
        meta = {}
        for k, v in kb_obj.items():
            if isinstance(v, list) and v and isinstance(v[0], dict) and "entry_name" in v[0]:
                flat.extend(v)
            else:
                meta[k] = v
        if not flat:
            raise ValueError("Structure KB non reconnue (dict sans items exploitables).")
        items = flat
    else:
        raise ValueError("Structure KB non reconnue (attendu list ou dict avec items).")

    kb_version = str(meta.get("kb_version") or meta.get("version") or "v1")
    return items, meta, kb_version, kb_hash


def safe_list(x):
    return x if isinstance(x, list) else []


def normalize_entry_name(name):
    if not isinstance(name, str):
        return ""
    return re.sub(r"\s+", " ", name).strip()


def build_chapter_index(items):
    chapters_map = {}
    by_chapter = {}
    for it in items:
        chap_id, chap_label = normalize_chapter(it.get("chapter", ""))
        if chap_id is None:
            continue
        chapters_map[chap_id] = chap_label
        by_chapter.setdefault(chap_id, []).append(it)
    return chapters_map, by_chapter


def build_entry_index(items_for_chapter):
    idx = {}
    for it in items_for_chapter:
        nm = normalize_entry_name(it.get("entry_name", ""))
        if not nm:
            continue
        if nm not in idx:
            idx[nm] = it
    return idx


def summarize_rules(rules):
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


# ---------------------------
# Storage (local JSONL)
# ---------------------------
def review_file_for_role(role: str) -> Path:
    slug = "generaliste" if role == ROLE_GENERALISTE else "urgentiste"
    return REVIEWS_DIR / f"reviews_{slug}.jsonl"


def append_review_local(role: str, payload: dict):
    f = review_file_for_role(role)
    with f.open("a", encoding="utf-8") as w:
        w.write(json.dumps(payload, ensure_ascii=False) + "\n")


def count_reviews_local(role: str) -> int:
    f = review_file_for_role(role)
    if not f.exists():
        return 0
    with f.open("r", encoding="utf-8") as r:
        return sum(1 for _ in r)


# ---------------------------
# Storage (Supabase)
# ---------------------------
def _get_secret(*names: str) -> Optional[str]:
    # Works both local and Streamlit Cloud (st.secrets)
    for n in names:
        v = None
        try:
            v = st.secrets.get(n)  # type: ignore[attr-defined]
        except Exception:
            v = None
        if v:
            return str(v)
    # env fallback
    import os
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


@st.cache_resource(show_spinner=False)
def get_supabase_storage() -> Optional[SupabaseStorage]:
    url = _get_secret("SUPABASE_URL", "SUPABASE_PROJECT_URL")
    anon = _get_secret("SUPABASE_ANON_KEY", "SUPABASE_KEY", "SUPABASE_PUBLISHABLE_KEY")
    table = _get_secret("SUPABASE_TABLE") or "reviews"
    if not url or not anon:
        return None
    cfg = SupabaseConfig(url=url, anon_key=anon, table=table, timeout=30)
    return SupabaseStorage(cfg)


def db_enabled() -> bool:
    return get_supabase_storage() is not None


def map_review_to_db_row(payload: Dict[str, Any], kb_version: str, kb_hash: str) -> Dict[str, Any]:
    """
    Map the app payload -> your Supabase table schema.
    """
    row = {
        "kb_version": kb_version,
        "kb_hash": kb_hash,
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
        "payload": payload,  # jsonb
    }
    # strip None for cleanliness
    return {k: v for k, v in row.items() if v is not None}


def insert_review_db(payload: Dict[str, Any], kb_version: str, kb_hash: str) -> Optional[Dict[str, Any]]:
    db = get_supabase_storage()
    if not db:
        return None
    row = map_review_to_db_row(payload, kb_version=kb_version, kb_hash=kb_hash)
    return db.insert_review(row)


def count_reviews_db(role: str) -> int:
    db = get_supabase_storage()
    if not db:
        return 0
    return db.count_reviews(reviewer_role=role)


# ---------------------------
# UI — Sidebar
# ---------------------------
st.title("Allo Docteur — Validation KB (MSF + ICD)")
st.caption("Module de revue clinique: navigation par chapitre/entrée, validation par 2 profils médecins, export en JSONL + Supabase.")

with st.sidebar:
    st.header("Chargement KB")
    kb_path = st.text_input("Chemin du fichier KB (JSON)", value=DEFAULT_KB_PATH)

    st.divider()
    st.header("Rôle du validateur")
    role = st.radio("Sélection du profil", [ROLE_GENERALISTE, ROLE_URGENTISTE])

    st.divider()
    st.header("Priorités (rappel)")
    for k in ["P1", "P2", "P3", "P4"]:
        st.write(f"**{P_LABELS[k]}**")

    st.divider()
    st.header("Stockage")
    if db_enabled():
        st.success("✅ Supabase activé")
        st.caption(f"Table: `{_get_secret('SUPABASE_TABLE') or 'reviews'}`")
    else:
        st.warning("Supabase non configuré — stockage local JSONL uniquement.")
        st.caption("Ajoute SUPABASE_URL + SUPABASE_ANON_KEY dans secrets (Streamlit) ou variables d'env (local).")


# Load KB
try:
    items, meta, kb_version, kb_hash = load_kb(kb_path)
except Exception as e:
    st.error(f"Impossible de charger le KB: {e}")
    st.stop()

# Ignore empty entry_name at UI level
items = [it for it in items if normalize_entry_name(it.get("entry_name", ""))]

chapters_map, by_chapter = build_chapter_index(items)
chapter_options = [(cid, chapters_map[cid]) for cid in sorted(chapters_map.keys())]

if not chapter_options:
    st.error("Aucun chapitre détecté dans le KB (champ 'chapter').")
    st.stop()

# ---------------------------
# UI — Main layout
# ---------------------------
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
    try:
        n = count_reviews_db(role) if db_enabled() else count_reviews_local(role)
    except SupabaseError as e:
        n = count_reviews_local(role)
        st.warning(f"Supabase indisponible pour le compteur: {e}")
    st.write(f"📌 Avis déjà enregistrés ({role}) : **{n}**")


with col_view:
    st.subheader("Vue KB")

    c1, c2 = st.columns([0.6, 0.4], gap="large")

    with c1:
        st.markdown(f"### {selected_item.get('entry_name','(sans nom)')}")
        st.write(f"**Chapitre:** {chapters_map.get(selected_chap_id, f'Chapitre {selected_chap_id}')}")
        st.write(f"**Type:** {selected_item.get('entry_type','—')}")

        # Population (requested)
        pop = safe_list(selected_item.get("population"))
        st.write(f"**Population:** {', '.join(pop) if pop else '—'}")

        st.write(f"**Niveau clinique:** {selected_item.get('clinical_level','—')}  |  **Granularité:** {selected_item.get('granularity_tag','—')}")

        ti = selected_item.get("triage_intent", {}) if isinstance(selected_item.get("triage_intent"), dict) else {}
        dp = ti.get("default_priority") or "—"
        isp = ti.get("if_severity_priority") or "—"
        st.write("**Priorité suggérée (KB):**", dp)
        st.write("**Priorité si gravité (KB):**", isp)

        st.markdown("#### Symptômes (extraits du KB)")
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
        st.markdown("#### Champs d'admission ")

        intake_fields = safe_list(selected_item.get("intake_fields"))
        if intake_fields:
            # UI improvement: compact display (chips) instead of a huge code block
            with st.expander("Voir la liste des champs", expanded=True):
                # Show as multi-column bullet list
                cols = st.columns(2)
                half = (len(intake_fields) + 1) // 2
                left, right = intake_fields[:half], intake_fields[half:]
                cols[0].write("\n".join([f"- `{x}`" for x in left]) if left else "")
                cols[1].write("\n".join([f"- `{x}`" for x in right]) if right else "")
        else:
            st.write("—")

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

        #st.markdown("#### Mapping ICD (si présent)")
        #icd_block = selected_item.get("icd_mapping") or selected_item.get("icd") or selected_item.get("icd_matches")
        #st.json(icd_block, expanded=False) if icd_block else st.write("—")

    st.divider()
    st.subheader("Formulaire de validation (médecin)")

    with st.form(key=f"review_form_{role}_{selected_chap_id}_{selected_entry_name}"):
        st.markdown(f"**Validateur:** {role}")

        reviewer_name = st.text_input("Nom (optionnel)", value="")
        reviewer_email = st.text_input("Email (optionnel)", value="")

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
            placeholder="Ex: notion d'aggravation, grossesse, immunodépression, douleur thoracique, etc.",
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
                    "reviewer_name": reviewer_name.strip() or None,
                    "reviewer_email": reviewer_email.strip() or None,
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

                # Always keep a local trail (useful for backup / debugging)
                append_review_local(role, payload)

                # Try DB insert
                db_ok = False
                if db_enabled():
                    try:
                        insert_review_db(payload, kb_version=kb_version, kb_hash=kb_hash)
                        db_ok = True
                    except SupabaseError as e:
                        st.warning(f"⚠️ Avis enregistré en local, mais échec Supabase: {e}")

                if db_ok:
                    st.success("Avis enregistré (Supabase + local) ✅")
                else:
                    st.success("Avis enregistré (local JSONL) ✅")

                st.info(f"Fichier avis local: {review_file_for_role(role).resolve()}")


# Footer
st.divider()
st.caption(
    "⚠️ Cette application sert à la **validation du KB de triage** (orientation/priorisation), "
    "pas à produire un diagnostic. Les règles et contenus doivent être validés par des médecins avant usage en production."
)




