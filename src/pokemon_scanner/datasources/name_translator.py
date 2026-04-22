from __future__ import annotations

import difflib
import json
import logging

import requests

from src.pokemon_scanner.core.paths import CACHE_DIR

_LOG = logging.getLogger(__name__)
_CACHE_FILE = CACHE_DIR / "pokemon_de_en.json"
_GRAPHQL_URL = "https://beta.pokeapi.co/graphql/v1beta"
# Single query fetching both DE (language_id=6) and EN (language_id=9) names.
# Uses Hasura field aliases so we can filter by different language_id in one round-trip.
_GRAPHQL_QUERY = """
{
  de_names: pokemon_v2_pokemonspeciesname(where: {language_id: {_eq: 6}}) {
    name
    pokemon_species_id
  }
  en_names: pokemon_v2_pokemonspeciesname(where: {language_id: {_eq: 9}}) {
    name
    pokemon_species_id
  }
}
"""

# Small hardcoded seed — used if GraphQL is unavailable
_SEED: dict[str, str] = {
    "Flunkifer": "Nickit",
    "Rattfratz": "Rattata",
    "Rattikarl": "Raticate",
    "Knebeldorfer": "Thievul",
    "Turtok": "Blastoise",
    "Glumanda": "Charmander",
    "Glurak": "Charizard",
    "Bisaflor": "Venusaur",
    "Bisasam": "Bulbasaur",
    "Schiggy": "Squirtle",
    "Pikachu": "Pikachu",
    "Evoli": "Eevee",
    "Glurak": "Charizard",
    "Dragoran": "Dragonite",
    "Garados": "Gyarados",
    "Raichu": "Raichu",
    "Gengar": "Gengar",
    "Relaxo": "Snorlax",
    "Lapras": "Lapras",
    "Umbreon": "Umbreon",
    "Espeon": "Espeon",
    "Wailord": "Wailord",
    "Panzaeron": "Blissey",
}

_mapping: dict[str, str] | None = None

# ---------------------------------------------------------------------------
# Trainer / item name table — maps lowercase OCR variants → canonical card name.
# Used both to block fuzzy Pokémon matching AND to correct OCR for API search.
# ---------------------------------------------------------------------------
_TRAINER_NAMES: dict[str, str] = {
    # "Professor's Research" and OCR variants
    "professor's research": "Professor's Research",
    "professors research":  "Professor's Research",
    "prolessor research":   "Professor's Research",
    "prolessors research":  "Professor's Research",
    "prolessor rescarch":   "Professor's Research",
    "prolessors rescarch":  "Professor's Research",
    "prolessor kescar":     "Professor's Research",
    "prolessors kescar":    "Professor's Research",
    "professor research":   "Professor's Research",
    "professor resea":      "Professor's Research",
    # "Tarragon" trainer
    "tarragon":  "Tarragon",
    "terragon":  "Tarragon",
    "arragon":   "Tarragon",
    "tarrago":   "Tarragon",
    "tarranon":  "Tarragon",
    "tarragoni": "Tarragon",
    # Violette
    "violette": "Violette",
    "yiolette": "Violette",
    "yiolete":  "Violette",
    "violete":  "Violette",
    # Iris's Fighting Spirit (many i/l OCR variants)
    "iris's fighting spirit":  "Iris's Fighting Spirit",
    "iris fighting spirit":    "Iris's Fighting Spirit",
    "irlss fighting spirit":   "Iris's Fighting Spirit",
    "iriss fighting spirit":   "Iris's Fighting Spirit",
    "ira fighting spirit":     "Iris's Fighting Spirit",
    "ira fighting splrit":     "Iris's Fighting Spirit",
    "ira fightlng splrit":     "Iris's Fighting Spirit",
    "iriss flghtlng spirit":   "Iris's Fighting Spirit",
    "irlss flghtlng spirit":   "Iris's Fighting Spirit",
    "iris flghtlng spirit":    "Iris's Fighting Spirit",
    "ira flghtlng spirit":     "Iris's Fighting Spirit",
    "iriss flghtlng splrit":   "Iris's Fighting Spirit",
    # Other known trainers (single-word — used for block only, canonical = title-case)
    "cynthia": "Cynthia", "marnie": "Marnie", "bede": "Bede",
    "hop": "Hop", "sonia": "Sonia", "raihan": "Raihan",
    "leon": "Leon", "oleana": "Oleana", "rose": "Rose",
    "nemona": "Nemona", "arven": "Arven", "penny": "Penny",
    "geeta": "Geeta", "larry": "Larry", "rika": "Rika",
    "poppy": "Poppy", "hassel": "Hassel", "miriam": "Miriam",
    "jacq": "Jacq", "turo": "Turo", "sada": "Sada",
    # Items (ticket variants — fossil handled by keyword search in pipeline)
    "redeemable ticket":  "Redeemable Ticket",
    "redeemable tlck":    "Redeemable Ticket",
    "redocmable tlcket":  "Redeemable Ticket",
    "bodycheck":          "Bodycheck",
}

# frozenset of all keys — used for fast membership and fuzzy matching
_TRAINER_BLOCKLIST: frozenset[str] = frozenset(_TRAINER_NAMES.keys())


def _is_trainer_name(text: str) -> bool:
    """Return True when *text* (or its OCR corruptions) is a known trainer/item name."""
    lower = text.strip().lower()
    if lower in _TRAINER_BLOCKLIST:
        return True
    # Fuzzy check against blocklist — if OCR is very close to a trainer name, block it.
    # Threshold 0.82 (not 0.85) to catch i/l OCR variants that differ in 1-2 chars.
    hits = difflib.get_close_matches(lower, list(_TRAINER_BLOCKLIST), n=1, cutoff=0.82)
    return bool(hits)


def _closest_trainer_name(text: str) -> str | None:
    """Return the canonical trainer name for *text*, or None if not a trainer."""
    lower = text.strip().lower()
    if lower in _TRAINER_NAMES:
        return _TRAINER_NAMES[lower]
    hits = difflib.get_close_matches(lower, list(_TRAINER_BLOCKLIST), n=1, cutoff=0.82)
    if hits:
        return _TRAINER_NAMES[hits[0]]
    return None


def correct_raw_for_search(raw_ocr: str) -> str:
    """Return the best API search term for *raw_ocr*.

    Priority:
    1. English Pokémon name if OCR-corrected successfully.
    2. Canonical trainer/item name (so API finds the actual card).
    3. Raw OCR as-is (let the API decide).
    """
    pokemon = correct_ocr_pokemon_name(raw_ocr)
    if pokemon:
        return pokemon
    trainer = _closest_trainer_name(raw_ocr)
    if trainer:
        return trainer
    return raw_ocr


def translate_de_to_en(german_name: str) -> str | None:
    """Translate a German Pokémon name to English. Returns None if not found."""
    m = _get_mapping()
    return m.get(german_name) or m.get(german_name.lower().capitalize())


def translate_de_to_en_fuzzy(name: str, cutoff: float = 0.72) -> str | None:
    """Like translate_de_to_en but falls back to fuzzy matching when no exact result."""
    exact = translate_de_to_en(name)
    if exact:
        return exact
    m = _get_mapping()
    if not m:
        return None
    # Case-preserving attempt first
    keys = list(m.keys())
    matches = difflib.get_close_matches(name, keys, n=1, cutoff=cutoff)
    if matches:
        return m[matches[0]]
    # Case-insensitive fallback
    lower = name.lower()
    lower_keys = [k.lower() for k in keys]
    ci_matches = difflib.get_close_matches(lower, lower_keys, n=1, cutoff=cutoff)
    if ci_matches:
        idx = lower_keys.index(ci_matches[0])
        return m[keys[idx]]
    return None


def translate_to_en(name: str) -> str | None:
    """Translate any supported non-English Pokémon name (DE, JA, ZH) to English.
    Uses fuzzy matching so partial / OCR-mangled German names also resolve.
    """
    return translate_de_to_en_fuzzy(name) or _get_cjk_mapping().get(name) or _get_cjk_mapping().get(name.strip())


# ---------------------------------------------------------------------------
# OCR-error correction
# ---------------------------------------------------------------------------

# Common single-char and bigram OCR confusions – ordered most-specific first.
_OCR_SUBSTITUTIONS: list[tuple[str, str]] = [
    # Bigram / trigram corrections (most-specific first)
    ("obn", "eba"),  # wobnrak → webarak (Spinarak)
    ("ut", "if"),    # funkuter → funkifer → flunkifer (Mawile)
    ("ng", "nt"),   # culang → culant → cufant (Cufant); g/t OCR confusion at end
    ("rn", "m"),    # "rn" read as "m"
    ("Ml", "Hi"),   # Ml → Hi: Mlppoterus → Hippowdon
    ("Nl", "Ni"),
    ("Vl", "Vi"),   # Vlctlni → Victini
    ("lI", "li"),
    ("vv", "w"),
    ("VV", "W"),
    ("cl", "d"),
    ("ci", "d"),
    # Single-char corrections
    ("0", "O"),     # zero → capital O
    ("1", "l"),     # one → lowercase L
]


def _fuzzy_inner(
    candidate: str,
    de_keys: list[str],
    de_keys_lower: list[str],
    en_names: list[str],
    en_names_lower: list[str],
    cutoff: float,
) -> tuple[str | None, str | None]:
    """Fuzzy match *candidate* against German keys first, then English values.

    Returns (matched_key, english_name) where matched_key is the DE key or EN
    name that was matched (used for sanity checking), and english_name is the
    final translation.  Both are None on no match.
    """
    m = _get_mapping()
    lower = candidate.lower()
    hits = difflib.get_close_matches(lower, de_keys_lower, n=1, cutoff=cutoff)
    if hits:
        idx = de_keys_lower.index(hits[0])
        return hits[0], m[de_keys[idx]]
    hits = difflib.get_close_matches(lower, en_names_lower, n=1, cutoff=cutoff)
    if hits:
        idx = en_names_lower.index(hits[0])
        return hits[0], en_names[idx]
    return None, None


def correct_ocr_pokemon_name(raw_ocr: str) -> str | None:
    """Best-effort correction of an OCR-mangled Pokémon name.

    Strategy (in order):
    1. Standard translate_to_en (fuzzy cutoff 0.72) – catches easy cases.
    2. Apply known OCR char-substitutions → retry fuzzy at 0.62.
    3. Try each individual word (≥4 chars) with fuzzy at 0.65.
       Skipped for 3+ word OCR output (likely trainer-card text with accidental
       Pokémon substrings, e.g. "Splrit" inside "Iris's Fighting Spirit").
    4. Final attempt on original at lower cutoff 0.62.
    Also matches directly against English Pokémon names (for EN-language cards).

    Sanity checks reject implausible matches:
    - Step 2: corrected form must be ≥ 0.50 similar to the matched name.
    - Step 3: individual word must be ≥ 0.50 (single-word OCR) or ≥ 0.60
      (multi-word OCR) similar to the matched name.
    - Step 4: raw OCR must be ≥ 0.25 (single-word) or ≥ 0.55 (multi-word)
      similar to the matched name.
    """
    if not raw_ocr:
        return None

    # Blocklist check — skip fuzzy matching for known trainer/item names entirely
    if _is_trainer_name(raw_ocr):
        _LOG.debug("OCR correction: %r blocked as trainer/item name", raw_ocr)
        return None

    m = _get_mapping()
    de_keys: list[str] = list(m.keys())
    de_keys_lower = [k.lower() for k in de_keys]
    # Unique English names for direct EN-card matching
    en_names: list[str] = list(dict.fromkeys(m.values()))
    en_names_lower = [v.lower() for v in en_names]

    word_count = len(raw_ocr.split())

    # 0. Exact lookups (trusted, no sanity check needed).
    #    a) Exact German key → English (handles DE Pokémon names verbatim)
    exact_de = translate_de_to_en(raw_ocr)
    if exact_de:
        return exact_de
    #    b) Exact English value → return as-is (handles EN-card names like "Haunter",
    #       "Charmander" that would otherwise fuzzy-match a German key first)
    raw_lower = raw_ocr.strip().lower()
    if raw_lower in en_names_lower:
        return en_names[en_names_lower.index(raw_lower)]
    #    c) CJK lookup
    cjk_result = _get_cjk_mapping().get(raw_ocr) or _get_cjk_mapping().get(raw_ocr.strip())
    if cjk_result:
        return cjk_result

    #    d) 'ol' → 'oi' exact-match (EasyOCR frequently reads 'oi' as 'ol').
    #       Check before any fuzzy matching to avoid e.g. "Nolbat" → "Golbat".
    if word_count == 1 and "ol" in raw_lower:
        candidate_oi = raw_lower.replace("ol", "oi")
        if candidate_oi in en_names_lower:
            result_oi = en_names[en_names_lower.index(candidate_oi)]
            _LOG.debug("OCR correction: %r → %r via ol→oi exact", raw_ocr, result_oi)
            return result_oi

    def _fuzzy(candidate: str, cutoff: float) -> tuple[str | None, str | None]:
        return _fuzzy_inner(candidate, de_keys, de_keys_lower, en_names, en_names_lower, cutoff)

    def _plausible(source: str, matched_key: str, threshold: float) -> bool:
        """True when *source* is similar enough to *matched_key* (DE key or EN name)
        to not be a false positive.  Comparing against the matched key is more
        meaningful than comparing against the translated EN name."""
        return difflib.SequenceMatcher(None, source.lower(), matched_key.lower()).ratio() >= threshold

    # 1. Fuzzy DE/EN lookup at raised cutoff (0.78) with sanity check.
    #    Sanity compares source against the matched DE/EN key (not the translation):
    #    e.g. "arragon" matches DE key "paragoni" at 0.78 → sim(arragon, paragoni)=0.78
    #    but translation is "Phantump" → sim(arragon, Phantump)=0.13 — correctly rejected.
    #    Meanwhile "gramokles" matches exactly at 1.0 → sim=1.0 → accepted.
    matched_key1, result1 = _fuzzy(raw_ocr, cutoff=0.78)
    if result1 and matched_key1 and _plausible(raw_ocr, matched_key1, threshold=0.72):
        return result1

    # 2. Apply OCR substitutions and retry
    for wrong, right in _OCR_SUBSTITUTIONS:
        corrected = raw_ocr.replace(wrong, right)
        if corrected != raw_ocr:
            matched_key2, r = _fuzzy(corrected, cutoff=0.65)
            if r and matched_key2 and _plausible(corrected, matched_key2, threshold=0.65):
                _LOG.debug("OCR correction: %r → %r via substitution %r→%r", raw_ocr, r, wrong, right)
                return r

    # 2b. 'ol' → 'oi' via fuzzy fallback (already exact-checked in step 0d above;
    #     this handles multi-word OCR where 'ol' appears in one word).
    if "ol" in raw_ocr.lower():
        candidate_oi = raw_ocr.lower().replace("ol", "oi")
        if candidate_oi in en_names_lower:
            result_oi = en_names[en_names_lower.index(candidate_oi)]
            _LOG.debug("OCR correction: %r → %r via ol→oi substitution", raw_ocr, result_oi)
            return result_oi

    # 3. Try individual words only for single-word OCR output.
    # Multi-word OCR (2+ words) is almost always a trainer/item card name —
    # splitting it and matching individual words causes false Pokémon hits
    # (e.g. "Prolebkor" from "Prolebkor Resvar" → Pyroar).
    if word_count == 1:
        for word in raw_ocr.split():
            if len(word) >= 4:
                matched_key3, r = _fuzzy(word, cutoff=0.65)
                if r and matched_key3 and _plausible(word, matched_key3, threshold=0.65):
                    _LOG.debug("OCR correction: %r → %r via word %r", raw_ocr, r, word)
                    return r

    # 4. Lower cutoff on original — stricter sanity threshold to avoid trainer-card hits
    matched_key4, r = _fuzzy(raw_ocr, cutoff=0.65)
    if r and matched_key4:
        final_threshold = 0.60 if word_count > 1 else 0.65
        if not _plausible(raw_ocr, matched_key4, threshold=final_threshold):
            _LOG.debug("OCR correction: %r → %r rejected by sanity check (step 4)", raw_ocr, r)
            r = None
        else:
            _LOG.debug("OCR correction: %r → %r via low-cutoff fuzzy", raw_ocr, r)
    return r


def _get_mapping() -> dict[str, str]:
    global _mapping
    if _mapping is not None:
        return _mapping
    if _CACHE_FILE.exists():
        try:
            _mapping = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            _LOG.debug("Loaded %d DE→EN name mappings from cache", len(_mapping))
            return _mapping
        except Exception as exc:
            _LOG.warning("Could not read name cache: %s", exc)
    _mapping = _fetch_from_graphql()
    if _mapping:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps(_mapping, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _LOG.info("Saved %d DE→EN name mappings to %s", len(_mapping), _CACHE_FILE)
    else:
        _mapping = dict(_SEED)
        _LOG.warning("Using hardcoded seed dictionary (%d entries)", len(_mapping))
    return _mapping


def _fetch_from_graphql() -> dict[str, str]:
    try:
        resp = requests.post(
            _GRAPHQL_URL,
            json={"query": _GRAPHQL_QUERY},
            timeout=30,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = (resp.json().get("data") or {})
        de_items = data.get("de_names") or []
        en_items = data.get("en_names") or []
        # Build species_id → English name lookup, then map German names
        en_by_id: dict[int, str] = {}
        for item in en_items:
            sid = item.get("pokemon_species_id")
            en_slug = item.get("name", "")
            if sid and en_slug:
                en_by_id[sid] = " ".join(w.capitalize() for w in en_slug.replace("-", " ").split())
        mapping: dict[str, str] = {}
        for item in de_items:
            de_name: str = item.get("name", "")
            sid = item.get("pokemon_species_id")
            en_name = en_by_id.get(sid, "") if sid else ""
            if de_name and en_name:
                mapping[de_name] = en_name
        if mapping:
            _LOG.info("Fetched %d German\u2192English Pok\u00e9mon name mappings from pokeapi", len(mapping))
            return mapping
        _LOG.warning("GraphQL returned 0 entries")
        return {}
    except Exception as exc:
        _LOG.warning("Could not fetch name translations from pokeapi GraphQL: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Japanese / Chinese → English translation
# ---------------------------------------------------------------------------

_CACHE_FILE_CJK = CACHE_DIR / "pokemon_ja_zh_en.json"

# PokeAPI language IDs:  11 = ja-Hrkt (kana, used on JP cards)
#                         4 = zh-Hant (Traditional, used on TW/HK cards)
#                        12 = zh-Hans (Simplified, used on CN cards)
_GRAPHQL_QUERY_CJK = """
{
  ja_names: pokemon_v2_pokemonspeciesname(where: {language_id: {_eq: 11}}) {
    name
    pokemon_species_id
  }
  zht_names: pokemon_v2_pokemonspeciesname(where: {language_id: {_eq: 4}}) {
    name
    pokemon_species_id
  }
  zhs_names: pokemon_v2_pokemonspeciesname(where: {language_id: {_eq: 12}}) {
    name
    pokemon_species_id
  }
  en_names: pokemon_v2_pokemonspeciesname(where: {language_id: {_eq: 9}}) {
    name
    pokemon_species_id
  }
}
"""

_mapping_cjk: dict[str, str] | None = None


def _get_cjk_mapping() -> dict[str, str]:
    global _mapping_cjk
    if _mapping_cjk is not None:
        return _mapping_cjk
    if _CACHE_FILE_CJK.exists():
        try:
            _mapping_cjk = json.loads(_CACHE_FILE_CJK.read_text(encoding="utf-8"))
            _LOG.debug("Loaded %d JA/ZH\u2192EN mappings from cache", len(_mapping_cjk))
            return _mapping_cjk
        except Exception as exc:
            _LOG.warning("Could not read CJK name cache: %s", exc)
    _mapping_cjk = _fetch_cjk_from_graphql()
    if _mapping_cjk:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE_CJK.write_text(
            json.dumps(_mapping_cjk, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _LOG.info("Saved %d JA/ZH\u2192EN mappings to %s", len(_mapping_cjk), _CACHE_FILE_CJK)
    else:
        _mapping_cjk = {}
        _LOG.warning("CJK name mapping empty \u2014 PokeAPI fetch failed")
    return _mapping_cjk


def _fetch_cjk_from_graphql() -> dict[str, str]:
    try:
        resp = requests.post(
            _GRAPHQL_URL,
            json={"query": _GRAPHQL_QUERY_CJK},
            timeout=30,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = (resp.json().get("data") or {})
        en_items = data.get("en_names") or []
        en_by_id: dict[int, str] = {}
        for item in en_items:
            sid = item.get("pokemon_species_id")
            en_slug = item.get("name", "")
            if sid and en_slug:
                en_by_id[sid] = " ".join(w.capitalize() for w in en_slug.replace("-", " ").split())
        mapping: dict[str, str] = {}
        for source_key in ("ja_names", "zht_names", "zhs_names"):
            for item in (data.get(source_key) or []):
                src = item.get("name", "")
                sid = item.get("pokemon_species_id")
                en = en_by_id.get(sid, "") if sid else ""
                if src and en:
                    mapping[src] = en
        if mapping:
            _LOG.info("Fetched %d JA/ZH\u2192EN Pok\u00e9mon name mappings from pokeapi", len(mapping))
        else:
            _LOG.warning("CJK GraphQL returned 0 entries")
        return mapping
    except Exception as exc:
        _LOG.warning("Could not fetch CJK name translations: %s", exc)
        return {}
