from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Dict, List, Optional, Sequence

from tools.product_lexicon import suggest_queries_from_lexicon


_STOP_TOKENS = {
    "de",
    "da",
    "do",
    "das",
    "dos",
    "com",
    "sem",
    "para",
    "por",
    "no",
    "na",
    "o",
    "a",
    "os",
    "as",
    "um",
    "uma",
    "uns",
    "umas",
    "kg",
    "g",
    "gr",
    "grama",
    "gramas",
    "l",
    "lt",
    "litro",
    "litros",
    "ml",
    "un",
    "unid",
    "unidade",
    "unidades",
    "nossa",
    "senhora",
    "sr",
    "sra",
}

_NOISY_OBJECT_TOKENS = {
    "tapete",
    "porta",
    "suporte",
    "organizador",
    "cabide",
    "prateleira",
    "bandeja",
    "jogo",
    "kit",
}

_CATEGORY_HINTS = [
    ("mercearia", {"arroz", "feijao", "macarrao", "farinha", "oleo", "acucar", "cafe", "biscoito", "flocao", "sal", "molho", "fuba"}),
    ("laticinios", {"leite", "creme", "iogurte", "manteiga", "margarina", "condensado", "queijo", "requeijao", "danone"}),
    ("padaria", {"pao", "bisnaga", "hamburguer", "hotdog", "hot", "dog"}),
    ("hortifruti", {"banana", "maca", "laranja", "tomate", "cebola", "batata", "limao", "alho", "cenoura"}),
    ("acougue", {"carne", "frango", "bovina", "boi", "acougue", "strogonoff", "picadinho", "ossobuco"}),
    ("limpeza", {"detergente", "sabao", "amaciante", "agua", "sanitaria", "desinfetante", "qboa", "cloro"}),
    ("higiene", {"shampoo", "sabonete", "creme", "dental", "absorvente", "papel", "higienico", "fralda", "pasta", "dente"}),
    ("bebidas", {"refrigerante", "suco", "agua", "cerveja", "coca", "guarana", "fanta"}),
]

_CATEGORY_ALIASES = {
    "mercearia": {"mercearia"},
    "laticinios": {"latic", "iogurte", "frios", "refrigerado"},
    "padaria": {"padaria", "paes", "paes industrializ", "padaria industrial"},
    "hortifruti": {"horti", "fruta", "legume", "verdura"},
    "acougue": {"acougue", "carne", "frigor", "aves"},
    "limpeza": {"limpeza", "inseticida"},
    "higiene": {"higiene"},
    "bebidas": {"bebida", "bebidas", "refrigerante", "cerveja"},
    "utilidades": {"bazar", "utilidade", "utilidades", "casa", "lar", "plastico"},
}

_CATEGORY_COMPATIBILITY = {
    "mercearia": {"mercearia"},
    "laticinios": {"laticinios", "mercearia"},
    "padaria": {"padaria", "laticinios"},
    "hortifruti": {"hortifruti"},
    "acougue": {"acougue"},
    "limpeza": {"limpeza"},
    "higiene": {"higiene"},
    "bebidas": {"bebidas"},
}


@dataclass
class QueryProfile:
    original_query: str
    normalized_query: str
    salient_tokens: List[str]
    core_query: str
    simplified_query: str
    requested_brand: str
    predicted_category: str
    is_beef_strog_intent: bool


def strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text or "")
        if not unicodedata.combining(ch)
    )


def tokens_for_intent(text: str) -> List[str]:
    t = strip_accents((text or "").lower())
    t = re.sub(r"[^a-z0-9]+", " ", t)
    raw = [x for x in t.split() if x]
    out: List[str] = []
    for tk in raw:
        if tk in _STOP_TOKENS:
            continue
        if tk.isdigit() or re.fullmatch(r"\d+(kg|g|ml|l|lt)", tk):
            continue
        out.append(tk)
    return out


def simplify_query(raw: str) -> str:
    words: List[str] = []
    for part in strip_accents((raw or "").lower()).split():
        clean = re.sub(r"[^a-z0-9]", "", part)
        if not clean:
            continue
        if clean in _STOP_TOKENS:
            continue
        if clean.isdigit() or re.fullmatch(r"\d+(kg|g|ml|l|lt)", clean):
            continue
        words.append(clean)
    return " ".join(words[:3]) if words else ""


def requested_brand(raw: str) -> str:
    q_tokens = set(tokens_for_intent(raw))
    brand_aliases = {
        "ninho": "ninho",
        "nestle": "nestle",
        "itambe": "itambe",
        "italac": "italac",
        "betania": "betania",
        "danone": "danone",
        "sorriso": "sorriso",
        "principal": "principal",
        "maxpaes": "maxpaes",
        "max": "maxpaes",
        "fatima": "fatima",
        "renopan": "renopan",
        "romana": "romana",
        "puro": "puro sabor",
        "purosabor": "puro sabor",
        "sabor": "puro sabor",
    }
    if "puro" in q_tokens and "sabor" in q_tokens:
        return "puro sabor"
    if "max" in q_tokens and "paes" in q_tokens:
        return "maxpaes"
    q_join = " ".join(sorted(q_tokens))
    for alias, canonical in brand_aliases.items():
        if alias in q_tokens or alias in q_join:
            return canonical
    return ""


def is_packaged_bread_item(item: dict) -> bool:
    name = strip_accents((item.get("nome") or "").lower())
    cat = strip_accents((item.get("categoria") or "").lower())
    if "pao" not in name:
        return False

    packaged_keywords = [
        "hot dog",
        "hotdog",
        "hamburg",
        "maxpaes",
        "fatima",
        "n.sra de fatima",
        "nossa senhora de fatima",
        "renopan",
        "romana",
        "bisnaga",
        "forma",
        "integral",
    ]
    is_packaged_cat = "padaria industrial" in cat or "paes industrializ" in cat
    has_packaged_kw = any(k in name for k in packaged_keywords)
    is_french_bread = "pao frances" in name or "frances" in name
    return (is_packaged_cat or has_packaged_kw) and not is_french_bread


def normalize_result_category(category: str) -> str:
    cat = strip_accents((category or "").lower())
    for canonical, aliases in _CATEGORY_ALIASES.items():
        if any(alias in cat for alias in aliases):
            return canonical
    return ""


def infer_category(query: str) -> str:
    q_tokens = set(tokens_for_intent(query))
    for canonical, trigger_tokens in _CATEGORY_HINTS:
        if q_tokens & trigger_tokens:
            return canonical
    return ""


def build_query_profile(query: str, aliases: Dict[str, str]) -> QueryProfile:
    normalized_query = (query or "").strip().lower()
    for alias, canonical in aliases.items():
        if alias in normalized_query:
            normalized_query = normalized_query.replace(alias, canonical)

    salient = tokens_for_intent(normalized_query)
    q_norm_full = strip_accents(normalized_query)
    return QueryProfile(
        original_query=query or "",
        normalized_query=normalized_query,
        salient_tokens=salient,
        core_query=" ".join(salient[:2]) if len(salient) >= 2 else "",
        simplified_query=simplify_query(normalized_query),
        requested_brand=requested_brand(normalized_query),
        predicted_category=infer_category(normalized_query),
        is_beef_strog_intent=bool(
            re.search(r"\b(strogonoff|strogonof|estrogonoff|estrogonof)\b", q_norm_full)
            and re.search(r"\b(carne|boi|bovina)\b", q_norm_full)
        ),
    )


def _accept_lexicon_candidate(profile: QueryProfile, candidate_name: str) -> bool:
    name_tokens = tokens_for_intent(candidate_name)
    salient_tokens = list(profile.salient_tokens)
    if not name_tokens or not salient_tokens:
        return False

    name_set = set(name_tokens)
    salient_set = set(salient_tokens)
    overlap = len(name_set & salient_set)
    if len(salient_set) >= 2 and overlap < 2:
        return False

    first_tokens = set(name_tokens[:2])
    if not (first_tokens & salient_set):
        return False

    if (name_set & _NOISY_OBJECT_TOKENS) and not (salient_set & _NOISY_OBJECT_TOKENS):
        return False

    return True


def build_candidate_queries(profile: QueryProfile, logger=None) -> List[str]:
    if profile.is_beef_strog_intent:
        return ["strogonoff kg"]

    candidate_queries: List[str] = []
    for candidate in [profile.normalized_query, profile.simplified_query, profile.core_query]:
        c = (candidate or "").strip()
        if c and c.lower() not in {x.lower() for x in candidate_queries}:
            candidate_queries.append(c)

    if len(profile.salient_tokens) >= 2:
        try:
            lexicon_hits = suggest_queries_from_lexicon(profile.normalized_query, limit=4, min_score=0.78)
            for name, score in lexicon_hits:
                c = (name or "").strip()
                if not c or not _accept_lexicon_candidate(profile, c):
                    continue
                if c.lower() in {x.lower() for x in candidate_queries}:
                    continue
                candidate_queries.append(c)
                if logger is not None:
                    logger.info(f"Resolver lexico sugeriu: '{profile.normalized_query}' -> '{c}' (score={score:.2f})")
        except Exception as exc:
            if logger is not None:
                logger.warning(f"Resolver lexico falhou para '{profile.normalized_query}': {exc}")

    return candidate_queries[:6]


def _is_item_category_compatible(profile: QueryProfile, item: dict) -> bool:
    predicted = profile.predicted_category
    if not predicted:
        return True

    category = normalize_result_category(item.get("categoria", ""))
    if category and category in _CATEGORY_COMPATIBILITY.get(predicted, {predicted}):
        return True

    name = strip_accents((item.get("nome") or "").lower())
    name_tokens = set(tokens_for_intent(name))
    if predicted == "padaria" and "pao" in name_tokens:
        return True
    if predicted == "laticinios" and ("leite" in name_tokens or "creme" in name_tokens or "iogurte" in name_tokens):
        return True
    if predicted == "mercearia" and name_tokens & {"arroz", "feijao", "macarrao", "farinha", "oleo", "acucar", "cafe", "biscoito"}:
        return True
    if predicted == "limpeza" and name_tokens & {"sabao", "detergente", "amaciante", "agua", "sanitaria"}:
        return True
    if predicted == "higiene" and name_tokens & {"creme", "dental", "papel", "higienico", "absorvente", "shampoo"}:
        return True
    if predicted == "bebidas" and name_tokens & {"coca", "fanta", "guarana", "refrigerante", "suco", "cerveja"}:
        return True

    if profile.requested_brand and profile.requested_brand in name:
        return True

    return False


def apply_result_guards(items: Sequence[dict], profile: QueryProfile) -> List[dict]:
    if not items:
        return []

    compatible: List[dict] = []
    incompatible: List[dict] = []

    for raw in items:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        name = strip_accents((item.get("nome") or "").lower())
        item_tokens = set(tokens_for_intent(name))
        category = normalize_result_category(item.get("categoria", ""))

        has_object_noise = bool(item_tokens & _NOISY_OBJECT_TOKENS)
        is_compatible = _is_item_category_compatible(profile, item)
        adjusted_score = float(item.get("match_score", 0.0) or 0.0)

        if has_object_noise and profile.predicted_category not in {"utilidades", ""}:
            adjusted_score -= 0.45
            is_compatible = False
        elif profile.predicted_category and not is_compatible:
            adjusted_score -= 0.18

        if profile.requested_brand:
            if profile.requested_brand in name:
                adjusted_score += 0.24
            else:
                adjusted_score -= 0.20

        if profile.predicted_category == "padaria" and "pacote" in strip_accents(profile.normalized_query) and "pao" in strip_accents(profile.normalized_query):
            if is_packaged_bread_item(item):
                adjusted_score += 0.20
                is_compatible = True

        item["match_score"] = round(max(0.0, adjusted_score), 4)
        item["resolver_category"] = category
        item["resolver_expected_category"] = profile.predicted_category

        if is_compatible:
            compatible.append(item)
        else:
            item["match_ok"] = False
            incompatible.append(item)

    compatible.sort(key=lambda row: float(row.get("match_score", 0.0) or 0.0), reverse=True)
    incompatible.sort(key=lambda row: float(row.get("match_score", 0.0) or 0.0), reverse=True)

    if compatible:
        return compatible + incompatible
    return incompatible
