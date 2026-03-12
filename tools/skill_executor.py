import re
import json
import unicodedata
import difflib
from tools.search_router import search_products
from pathlib import Path

# Carregar aliases da skill normalizar_termos
ALIASES = {}
aliases_path = Path("skills/normalizar_termos/aliases.json")
if aliases_path.exists():
    with open(aliases_path, 'r', encoding='utf-8') as f:
        ALIASES = json.load(f)

def _strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text or "")
        if not unicodedata.combining(ch)
    )

def _tokens_for_intent(text: str) -> list[str]:
    t = _strip_accents((text or "").lower())
    t = re.sub(r"[^a-z0-9]+", " ", t)
    raw = [x for x in t.split() if x]
    stop = {
        "de", "da", "do", "das", "dos", "com", "sem", "para", "por", "no", "na",
        "o", "a", "os", "as", "um", "uma", "uns", "umas",
        "kg", "g", "gr", "grama", "gramas", "l", "lt", "litro", "litros", "ml",
        "un", "unid", "unidade", "unidades",
        "nossa", "senhora", "sr", "sra",
    }
    out = []
    for tk in raw:
        if tk in stop:
            continue
        if tk.isdigit() or re.fullmatch(r"\d+(kg|g|ml|l|lt)", tk):
            continue
        out.append(tk)
    return out

def _requested_brand(raw: str) -> str:
    q_tokens = set(_tokens_for_intent(raw))
    brand_aliases = {
        "ninho": "ninho",
        "nestle": "nestle",
        "itambe": "itambe",
        "italac": "italac",
        "betania": "betania",
        "danone": "danone",
        "sorriso": "sorriso",
    }
    for alias, canonical in brand_aliases.items():
        if alias in q_tokens:
            return canonical
    return ""

def _needs_confirmation(items: list, original_query: str) -> tuple[bool, str]:
    candidates = [i for i in items if isinstance(i, dict) and "nome" in i]
    if not candidates:
        return False, ""
    ranked = sorted(candidates, key=lambda i: float(i.get("match_score", 0.0) or 0.0), reverse=True)
    best = float(ranked[0].get("match_score", 0.0) or 0.0)
    second = float(ranked[1].get("match_score", 0.0) or 0.0) if len(ranked) > 1 else 0.0
    margin = best - second

    q_tokens = set(_tokens_for_intent(original_query))
    n_tokens = set(_tokens_for_intent(ranked[0].get("nome", "")))
    coverage = (len(q_tokens & n_tokens) / max(len(q_tokens), 1)) if q_tokens else 1.0

    top3 = ranked[:3]
    cat_set = set()
    for r in top3:
        cat = _strip_accents((r.get("categoria", "") or "").lower())
        if "limpeza" in cat:
            cat = "limpeza"
        elif "higiene" in cat:
            cat = "higiene"
        elif "bebida" in cat:
            cat = "bebidas"
        elif "acougue" in cat or "carne" in cat:
            cat = "acougue"
        elif "horti" in cat or "fruta" in cat or "legume" in cat:
            cat = "hortifruti"
        cat_set.add(cat or "outros")

    low_score = best < 0.58
    very_low_score = best < 0.48
    low_margin = len(ranked) > 1 and margin < 0.04
    weak_coverage = len(q_tokens) >= 2 and coverage < 0.35
    mixed_categories = len(cat_set) >= 2 and len(top3) >= 2
    critical_category_mix = (
        mixed_categories
        and (
            ("limpeza" in cat_set and "higiene" in cat_set)
            or ("acougue" in cat_set and "hortifruti" in cat_set)
            or ("acougue" in cat_set and "bebidas" in cat_set)
        )
    )

    q_norm = _strip_accents((original_query or "").lower())
    top_name = _strip_accents((ranked[0].get("nome", "") or "").lower())
    requested_brand = _requested_brand(original_query)
    if requested_brand:
        # Se a marca pedida aparece no topo, evitamos pedir confirmação por variação.
        if requested_brand in top_name:
            return False, ""

    strog_intent = bool(re.search(r"\b(strogonoff|strogonof|estrogonoff|estrogonof)\b", q_norm))
    if strog_intent and not re.search(r"\b(strogonoff|strogonof|estrogonoff|estrogonof)\b", top_name):
        return True, "pedido de strogonoff sem item de strogonoff no topo"

    ovo_bandeja_intent = bool(re.search(r"\b(bandeja|cartela)\b", q_norm) and re.search(r"\bovos?\b", q_norm))
    if ovo_bandeja_intent:
        top3_names = [
            _strip_accents((r.get("nome", "") or "").lower())
            for r in top3
            if isinstance(r, dict)
        ]
        has_ovo20_top3 = any(("ovo" in nm and re.search(r"\b20\b", nm)) for nm in top3_names)
        if "ovo" not in top_name and not has_ovo20_top3:
            return True, "pedido de bandeja/cartela de ovo sem ovo no topo"
        if not re.search(r"\b20\b", top_name):
            if has_ovo20_top3 and best >= 0.52:
                return False, ""
            return True, "pedido de bandeja/cartela de ovo deve priorizar 20 unidades"

    if ("tapioca" in q_norm or "goma" in q_norm) and ("goma" in top_name or "tapioca" in top_name):
        return False, ""
    if ("fandangos" in q_norm and ("vermelho" in q_norm or "presunto" in q_norm)) and ("fandangos" in top_name):
        return False, ""
    if ("animados" in q_norm and "chocolate" in q_norm) and ("animados" in top_name):
        return False, ""
    if (("massa fina" in q_norm) or ("massafina" in q_norm) or ("sovado" in q_norm)) and ("sovado" in top_name):
        return False, ""
    if (("mao de vaca" in q_norm) or ("ossobuco" in q_norm) or ("ossubuco" in q_norm)) and (("ossobuco" in top_name) or ("ossubuco" in top_name)):
        return False, ""
    if ("absorvente" in q_norm or "abs" in q_norm):
        wants_noturno = ("noturno" in q_norm or "noturna" in q_norm)
        if wants_noturno and (("abs" in top_name or "absorv" in top_name) and ("noturn" in top_name or " not " in f" {top_name} ")):
            return False, ""
        if not wants_noturno and ("abs" in top_name or "absorv" in top_name):
            return False, ""
    if all(k in q_norm for k in ["pao", "integral", "fatima"]) and all(k in top_name for k in ["pao", "integral", "fatima"]):
        return False, ""
    if all(k in q_norm for k in ["pacote", "pao"]) and any(
        k in top_name for k in ["hot dog", "hamburg", "max paes", "fatima"]
    ):
        return False, ""

    if very_low_score and weak_coverage:
        return True, "score baixo e baixa cobertura dos termos do cliente"
    if low_score and weak_coverage and critical_category_mix:
        return True, "score baixo com categorias conflitantes"
    if low_margin and weak_coverage and best < 0.62:
        return True, "candidatos muito empatados para a intencao"
    if low_margin and critical_category_mix:
        return True, "empate entre categorias conflitantes"
    return False, ""



def _simplify_query(raw: str) -> str:
    q_norm = _strip_accents((raw or "").lower())
    noise = {
        "de", "da", "do", "das", "dos", "com", "sem", "para", "por", "no", "na",
        "um", "uma", "uns", "umas", "o", "a", "os", "as",
        "grande", "pequeno", "pequena", "medio", "media", "vermelho", "azul",
        "verde", "preto", "branco", "tradicional", "original",
        "kg", "g", "gr", "grama", "gramas", "l", "lt", "litro", "litros", "ml",
        "un", "unid", "unidade", "unidades",
        "nossa", "senhora",
    }
    words = []
    for part in q_norm.split():
        clean = re.sub(r"[^a-z0-9]", "", part)
        if not clean:
            continue
        if clean.isdigit() or re.fullmatch(r"\d+(kg|g|ml|l|lt)", clean):
            continue
        if clean in noise:
            continue
        words.append(clean)
    if not words:
        return ""
    return " ".join(words[:3])

def _semantic_rerank(items: list, original_query: str) -> list:
    if not items:
        return items
    q_tokens = set(_tokens_for_intent(original_query))
    q_norm = " ".join(sorted(q_tokens))
    q_full = _strip_accents((original_query or "").lower())
    requested_brand = _requested_brand(original_query)

    intent_category_hints = [
        ({"iogurte", "danone", "danoninho", "petit", "suisse", "lacteo"}, {"iogurte", "latic"}),
        ({"cerveja", "heineken", "skol", "brahma", "budweiser"}, {"cerveja", "bebida"}),
        ({"refrigerante", "coca", "pepsi", "guarana", "fanta", "sprite", "refri"}, {"bebida", "refrigerante"}),
        ({"carne", "acougue", "boi", "bovina", "frango", "suina", "suino"}, {"acougue", "carne", "frigor", "aves"}),
        ({"arroz", "feijao", "macarrao", "farinha", "oleo", "acucar"}, {"mercearia"}),
        ({"detergente", "sabao", "amaciante", "agua", "sanitaria", "desinfetante"}, {"limpeza"}),
        ({"shampoo", "sabonete", "creme", "dental", "fralda"}, {"higiene"}),
        ({"banana", "maca", "laranja", "tomate", "cebola", "batata", "limao"}, {"horti", "fruta", "legume", "verdura"}),
        ({"pao", "queijo", "mussarela", "presunto"}, {"padaria", "frios", "latic"}),
        ({"veneno", "inseticida", "rato", "murisoca", "mata"}, {"limpeza", "inseticida", "bazar"}),
        ({"tapioca", "goma"}, {"mercearia", "padaria", "frios"}),
    ]

    reranked = []
    for item in items:
        if not isinstance(item, dict):
            continue
        base = float(item.get("match_score", 0.0) or 0.0)
        name = _strip_accents((item.get("nome") or "").lower())
        cat = _strip_accents((item.get("categoria") or "").lower())
        name_tokens = set(_tokens_for_intent(name))

        overlap = len(q_tokens & name_tokens) / max(len(q_tokens), 1) if q_tokens else 0.0
        ratio = difflib.SequenceMatcher(
            None,
            q_norm,
            " ".join(sorted(set(_tokens_for_intent(f"{name} {cat}")))),
        ).ratio()

        semantic = (0.45 * base) + (0.35 * overlap) + (0.20 * ratio)

        for trigger_tokens, category_hints in intent_category_hints:
            if q_tokens & trigger_tokens:
                if any(h in cat for h in category_hints):
                    semantic += 0.10
                else:
                    semantic -= 0.03

        if requested_brand:
            if requested_brand in name:
                semantic += 0.08
            else:
                semantic -= 0.02

        if "pao" in q_tokens and "pacote" in q_tokens:
            if any(k in name for k in ["hot dog", "hamburg", "max paes", "fatima"]):
                semantic += 0.30
            elif "pao" in name and "pacote" in name:
                semantic += 0.20
            elif "pao frances" in name or "frances" in name:
                semantic -= 0.12

        if "integral" in q_tokens:
            if "integral" in name:
                semantic += 0.20
            elif "pao" in q_tokens and "pao" in name:
                semantic -= 0.10
        if "fatima" in q_tokens:
            if "fatima" in name:
                semantic += 0.18
            elif "pao" in q_tokens and "pao" in name:
                semantic -= 0.06
        if {"pao", "integral", "fatima"}.issubset(q_tokens):
            if all(token in name for token in ("pao", "integral", "fatima")):
                semantic += 0.30
            elif "pao" in name and "fatima" in name and "tradicional" in name:
                semantic -= 0.12
        if (("massa" in q_tokens and "fina" in q_tokens) or "massafina" in q_tokens or "sovado" in q_tokens):
            if "sovado" in name:
                semantic += 0.35
            elif "pao frances" in name or "frances" in name:
                semantic -= 0.10
        if (("mao" in q_tokens and "vaca" in q_tokens) or "ossobuco" in q_tokens or "ossubuco" in q_tokens):
            if "ossobuco" in name or "ossubuco" in name:
                semantic += 0.35
            elif "bife" in name or "musculo" in name:
                semantic += 0.08
        if "absorvente" in q_tokens or "abs" in q_tokens:
            if "abs" in name or "absorv" in name:
                semantic += 0.20
        if "noturno" in q_tokens or "noturna" in q_tokens:
            if "noturn" in name or " not " in f" {name} ":
                semantic += 0.20
            elif "abs" in name or "absorv" in name:
                semantic -= 0.08
        if "tapioca" in q_tokens or "goma" in q_tokens:
            if "tapioca" in name or "goma" in name:
                semantic += 0.25
        if "veneno" in q_tokens or "inseticida" in q_tokens or "rato" in q_tokens or "murisoca" in q_tokens:
            if "inseticida" in name or "isca" in name or "repel" in name:
                semantic += 0.25
            if "rato" in q_tokens and ("rato" in name or "rat" in name):
                semantic += 0.30

        if re.search(r"\b(strogonoff|strogonof|estrogonoff|estrogonof)\b", q_full):
            if re.search(r"\b(strogonoff|strogonof|estrogonoff|estrogonof)\b", name):
                semantic += 0.25
            elif any(corte in name for corte in ["paleta", "acem", "musculo", "coxao", "patinho"]):
                semantic -= 0.20
        if re.search(r"\b(bandeja|cartela)\b", q_full) and re.search(r"\bovos?\b", q_full):
            if "ovo branco" in name and re.search(r"\b20\b", name):
                semantic += 0.40
            elif "ovo branco" in name:
                semantic += 0.30
            elif "ovo" in name:
                semantic += 0.12
            else:
                semantic -= 0.20

        semantic = round(max(0.0, min(1.2, semantic)), 4)
        item["semantic_score"] = semantic
        item["match_score"] = max(base, semantic)
        item["match_ok"] = bool(item.get("match_score", 0.0) >= 0.50)
        reranked.append(item)

    reranked.sort(key=lambda r: float(r.get("match_score", 0.0) or 0.0), reverse=True)
    return reranked

def _run_search_for_item(q: str, telefone: str) -> tuple[str, list]:
    raw = search_products(q, telefone=telefone)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return raw, parsed
        return raw, []
    except Exception:
        return raw, []

def buscar_e_validar(telefone: str, query: str) -> str:
    from config.logger import setup_logger
    logger = setup_logger(__name__)

    # The LLM already processed the query contextually. We only normalize it using aliases
    # for backwards compatibility.
    query_original = (query or "").strip().lower()
    for alias, canonical in ALIASES.items():
        if alias in query_original:
            query_original = query_original.replace(alias, canonical)

    query_limpa = _simplify_query(query_original)
    salient = _tokens_for_intent(query_original)
    query_core = " ".join(salient[:2]) if len(salient) >= 2 else ""

    q_norm_full = _strip_accents(query_original)
    is_beef_strog_intent = bool(
        re.search(r"\b(strogonoff|strogonof|estrogonoff|estrogonof)\b", q_norm_full)
        and re.search(r"\b(carne|boi|bovina)\b", q_norm_full)
    )

    candidate_queries = []
    if is_beef_strog_intent:
        candidate_queries = ["strogonoff kg"]
    else:
        for candidate in [query_original, query_limpa, query_core]:
            c = (candidate or "").strip()
            if c and c.lower() not in {x.lower() for x in candidate_queries}:
                candidate_queries.append(c)

    merged = {}
    for idx, cq in enumerate(candidate_queries[:4]):
        if idx > 0:
            logger.info(f"🔄 Retry Busca Inteligente: '{query_original}' -> '{cq}'")
        _, rows = _run_search_for_item(cq, telefone)
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get("id") or (row.get("nome") or "").lower())
            prev = merged.get(key)
            if prev is None:
                merged[key] = dict(row)
            else:
                prev_score = float(prev.get("match_score", 0.0) or 0.0)
                new_score = float(row.get("match_score", 0.0) or 0.0)
                if new_score > prev_score:
                    merged[key] = dict(row)

    resultados = list(merged.values())
    resultados = _semantic_rerank(resultados, query_original)

    if is_beef_strog_intent and resultados:
        strog_results = []
        other_results = []
        for r in resultados:
            if not isinstance(r, dict):
                continue
            nome_no_acc = _strip_accents((r.get("nome") or "").lower())
            if re.search(r"\b(strogonoff|strogonof|estrogonoff|estrogonof)\b", nome_no_acc):
                strog_results.append(r)
            else:
                other_results.append(r)

        if strog_results:
            resultados = strog_results
        else:
            for r in other_results:
                r["match_ok"] = False
            warning = {
                "id": "AVISO_STROGONOFF_EXATO",
                "nome": "⚠️ ITEM ESPECÍFICO NÃO LOCALIZADO",
                "preco": 0.0,
                "estoque": 0,
                "match_ok": False,
                "aviso": (
                    "Para 'carne para strogonoff' eu só posso usar o item oficial 'STROGONOFF kg'. "
                    "No momento ele não apareceu na busca."
                ),
            }
            resultados = [warning] + other_results

    if resultados:
        top_results = [r for r in resultados if r.get("match_score", 0) > 0.5]
        categorias = set()
        for r in top_results:
            cat = r.get("categoria", "").upper()
            if "LIMPEZA" in cat: cat = "LIMPEZA"
            elif "HIGIENE" in cat: cat = "HIGIENE"
            elif "BEBIDAS" in cat: cat = "BEBIDAS"
            elif "AÇOUGUE" in cat or "CARNE" in cat: cat = "AÇOUGUE"
            elif "HORTIFRUTI" in cat or "LEGUMES" in cat: cat = "HORTIFRUTI"
            
            if cat:
                categorias.add(cat)
        
        if len(categorias) > 1 and "LIMPEZA" in categorias and "HIGIENE" in categorias:
             warning = {
                 "id": "AVISO_AMBIGUIDADE",
                 "nome": "⚠️ AMBIGUIDADE DETECTADA",
                 "preco": 0.0,
                 "estoque": 0,
                 "match_ok": False,
                 "aviso": f"Encontrei produtos de categorias diferentes ({', '.join(categorias)}). PERGUNTE ao cliente qual ele deseja antes de adicionar."
             }
             resultados.insert(0, warning)

        needs_confirm, motivo = _needs_confirmation(resultados, query)
        if needs_confirm:
            for r in resultados:
                if isinstance(r, dict) and r.get("id") not in {"AVISO_AMBIGUIDADE", "AVISO_BAIXA_CONFIANCA"}:
                    r["match_ok"] = False
            warning = {
                "id": "AVISO_BAIXA_CONFIANCA",
                "nome": "⚠️ CONFIRMAÇÃO NECESSÁRIA",
                "preco": 0.0,
                "estoque": 0,
                "match_ok": False,
                "aviso": f"Busca com baixa confiança ({motivo}). Confirme com o cliente antes de adicionar.",
            }
            if not any(isinstance(r, dict) and r.get("id") == "AVISO_BAIXA_CONFIANCA" for r in resultados):
                resultados.insert(0, warning)

        requested_brand = _requested_brand(query)
        if requested_brand:
            has_brand_hit = any(
                requested_brand in _strip_accents((r.get("nome") or "").lower())
                for r in resultados
                if isinstance(r, dict)
            )
            if not has_brand_hit:
                for r in resultados:
                    if isinstance(r, dict) and r.get("id") not in {"AVISO_AMBIGUIDADE", "AVISO_BAIXA_CONFIANCA", "AVISO_MARCA"}:
                        r["match_ok"] = False
                aviso_marca = {
                    "id": "AVISO_MARCA",
                    "nome": "⚠️ MARCA NÃO LOCALIZADA",
                    "preco": 0.0,
                    "estoque": 1.0,
                    "match_score": 0.0,
                    "match_ok": False,
                    "aviso": (
                        f"O cliente pediu marca '{requested_brand}', mas os resultados não mostram essa marca no nome. "
                        "Confirme com o cliente se aceita alternativa."
                    ),
                }
                if not any(isinstance(r, dict) and r.get("id") == "AVISO_MARCA" for r in resultados):
                    resultados.insert(0, aviso_marca)

    return json.dumps(resultados, ensure_ascii=False)
