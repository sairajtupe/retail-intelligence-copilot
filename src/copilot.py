"""Copilot: interpret the question, compute deterministic facts, ground the
explanation with retrieved policy chunks, and (optionally) have Gemini narrate.

Design rules:
- The LLM NEVER computes business numbers (architecture rule).
- Ambiguous / unknown / unanswerable questions return clarification or a
  transparent "I don't have enough data to answer reliably."
- Without a Gemini key the copilot still answers deterministically.
"""
from __future__ import annotations
import json
import math
from typing import Any

from src.config import GEMINI_AVAILABLE, log
import src.database as db
import src.analytics as analytics
import src.data_loader as data_loader
import src.forecasting as forecasting
import src.anomaly_detection as anomaly_detection
import src.inventory_engine as inventory_engine
import src.retrieval as retrieval
from src.gemini_client import GeminiUnavailable
from src.schemas import CopilotRequest
from src.utils import safe_float, safe_int, fmt_num, fmt_money, parse_date

INSUFFICIENT_DATA_MSG = "I don't have enough data to answer reliably."

INTENTS = [
    "ATTENTION", "STOCKOUT", "OVERSTOCK", "SLOW_MOVING", "SALES_SPIKE",
    "SALES_DROP", "PRODUCT_PERFORMANCE", "FORECAST", "STORE",
    "PRODUCT_CATALOG", "LEADERBOARD", "GENERAL",
]

INTENT_KEYWORDS = {
    "ATTENTION": ["attention", "what needs", "today", "priorit", "what should i focus", "issues", "action"],
    "STOCKOUT": ["stock out", "stockout", "running out", "running low", "out of stock", "low stock",
                  "low in stock", "low on stock", "reorder", "restock", "shortage", "short on"],
    "OVERSTOCK": ["overstock", "too much stock", "excess", "surplus", "sitting on", "tie up"],
    "SLOW_MOVING": ["slow mov", "slow-sell", "dead stock", "not selling", "stagnant"],
    "SALES_SPIKE": ["spike", "surge", "jump", "soar", "increase in sales", "went up", "boost", "why did", "why is"],
    "SALES_DROP": ["drop", "decline", "declin", "decrease", "fell", "gone down", "slump", "slowdown"],
    "PRODUCT_PERFORMANCE": ["performance", "how is", "how are", "doing", "sales of", "selling"],
    "FORECAST": ["forecast", "predict", "future", "next week", "next month", "projection", "will we"],
    "PROMOTION": ["promotion", "promo", "discount", "campaign", "offer", "sale event"],
    "STORE": ["store", "branch", "location", "city", "site"],
    "PRODUCT_CATALOG": ["products", "catalog", "sku", "price of", "how much", "what is the price"],
    "LEADERBOARD": ["best seller", "top product", "best product", "best margin", "highest margin",
                    "best performing", "top selling", "worst product", "worst selling",
                    "most profitable", "least profitable", "best-selling", "top products"],
}

UNANSWERABLE = [
    "weather", "temperature", "rain ", "snow", "storm", "news",
    "recipe", "how to cook", "politics", "my staff", "employee", "hiring",
    "investment advice", "horoscope",
]
GREETINGS = ("hi", "hello", "hey", "howdy", "good morning", "good afternoon", "yo")


LEADERBOARD_PRIORITY_PHRASES = [
    "top selling", "best selling", "best-selling", "top products", "top product",
    "best product", "top seller", "best seller", "best-seller", "top performers",
    "highest selling", "most sold",
]


def _classify(query: str) -> str:
    q = query.lower()
    scores: dict[str, int] = {}
    for intent, words in INTENT_KEYWORDS.items():
        s = sum(1 for w in words if w in q)
        if s:
            scores[intent] = s

    # Product flag re-classifies to PRODUCT_PERFORMANCE
    is_product_q = any(k in q for k in ("how is", "performance of", "sales of", "is selling",
                                        "doing", "tell me about", "details", "what about",
                                        "how does", "about product"))
    has_product = bool(resolve_products(q, top_k=1))

    if is_product_q and has_product:
        return "PRODUCT_PERFORMANCE"
    if has_product and ("product" in q or "sku" in q or "how is" in q or "performance" in q
                        or "doing" in q or "tell me" in q):
        return "PRODUCT_PERFORMANCE"

    # Explicit top/best seller questions always rank LEADERBOARD first.
    if any(p in q for p in LEADERBOARD_PRIORITY_PHRASES):
        return "LEADERBOARD"

    if not scores:
        return "GENERAL"
    return sorted(scores.items(), key=lambda x: -x[1])[0][0]


def resolve_products(query: str, top_k: int = 8) -> list[dict]:
    q = query.lower().strip()
    if not q:
        return []
    matches: list[dict] = []
    seen = set()
    for r in analytics.product_menu():
        name = r["product_name"].lower()
        brand = str(r.get("brand") or "").lower()
        # Exact product id / SKU
        if r["product_id"].lower() in q or str(r["SKU"]).lower() in q:
            rec = {**r, "match": "exact_id", "score": 1.0}
            if r["product_id"] not in seen:
                seen.add(r["product_id"]); matches.append(rec)
            continue
        if len(name.split()) >= 2 and name in q:
            rec = {**r, "match": "full_name", "score": 0.95}
            if r["product_id"] not in seen:
                seen.add(r["product_id"]); matches.append(rec)
            continue
        # Brand-level match (half score: might be a category-style reference)
        if brand and brand in q:
            rec = {**r, "match": "brand", "score": 0.7}
            if r["product_id"] not in seen:
                seen.add(r["product_id"]); matches.append(rec)
            continue
        for word in q.split():
            if len(word) >= 4 and word in name.split():
                rec = {**r, "match": "partial", "score": 0.8}
                if r["product_id"] not in seen:
                    seen.add(r["product_id"]); matches.append(rec)
                break
    matches.sort(key=lambda x: -x["score"])
    return matches[:top_k]


def resolve_stores(query: str) -> list[dict]:
    q = query.lower()
    out = []
    for r in db.query_df("SELECT store_id, store_name, city FROM stores"):
        if r["store_id"].lower() in q or r["store_name"].lower() in q or r["city"].lower() in q:
            out.append(r)
    return out


def _facts_for_product(product: dict, store_id: str | None) -> dict[str, Any]:
    pid = product["product_id"]
    where = f"m.product_id = '{pid}'" + (f" AND m.store_id = '{store_id}'" if store_id else "")
    row = db.fetchone(f"""
        SELECT m.store_id, m.stock, m.cover_days, m.units_7d, m.units_30d,
               m.avg_daily_demand_7d, m.avg_daily_demand_30d, m.history_days,
               m.risk, m.risk_score, m.anomaly, m.overstock, m.slow_moving
        FROM metrics_snapshot m WHERE {where}""")
    facts: dict[str, Any] = {
        "product": product["product_name"], "product_id": pid,
        "category": product["category"], "brand": product.get("brand", ""),
        "price": _lookup(pid, "selling_price"), "cost": _lookup(pid, "unit_cost"),
    }
    months = db.query_df(f"""
        SELECT substr(date,1,7) ym, SUM(units_sold) units, SUM(revenue) revenue
        FROM sales WHERE product_id = '{pid}'
        {f"AND store_id = '{store_id}'" if store_id else ""}
        GROUP BY ym ORDER BY ym""")
    facts["sales_history"] = [{"month": r["ym"], "units": safe_int(r["units"]), "revenue": round(safe_float(r["revenue"]), 2)} for r in months]
    facts["months_on_record"] = len(facts["sales_history"])
    if row:
        facts["store_id"] = row["store_id"]
        facts["stock"] = safe_int(row["stock"])
        facts["cover_days"] = round(safe_float(row["cover_days"]), 1)
        facts["avg_daily_demand_30d"] = round(safe_float(row["avg_daily_demand_30d"]), 2)
        facts["avg_daily_demand_7d"] = round(safe_float(row["avg_daily_demand_7d"]), 2)
        facts["risk"] = row["risk"]
        facts["anomaly"] = row["anomaly"]
        facts["overstock"] = bool(row["overstock"])
        facts["slow_moving"] = bool(row["slow_moving"])
        facts["history_days"] = safe_int(row["history_days"])
        facts["units_30d"] = safe_int(row["units_30d"])
    if store_id:
        anomaly = anomaly_detection.pair_anomalies(store_id, pid)
        if anomaly:
            facts["promo_overlap"] = anomaly.get("promo_overlap")
            facts["promo_name"] = anomaly.get("promo_name")
            if anomaly.get("signal") in ("SPIKE", "DROP"):
                facts["recent_signal"] = anomaly["signal"]
                facts["signal_verdict"] = anomaly["verdict"]
    return facts


def _lookup(pid: str, col: str) -> Any:
    return db.scalar(f"SELECT {col} FROM products WHERE product_id=?", (pid,), None)


def _month_percent(facts: dict) -> float | None:
    hist = facts["sales_history"]
    if len(hist) < 2:
        return None
    cur, prev = hist[-1]["units"], hist[-2]["units"]
    if prev == 0:
        return None
    return round((cur - prev) / prev * 100, 1)


def _demand_context(store_id: str | None) -> dict:
    k = analytics.dashboard_kpis()
    top = analytics.top_products(30, 5)
    attention = analytics.attention_items(scope="all", store_id=store_id, max_items=5)
    return {
        "top_products": top,
        "attention_top": attention,
        "kpis": k,
    }


def answer(req: CopilotRequest) -> dict:
    intent = _classify(req.query)
    products = resolve_products(req.query)
    stores = resolve_stores(req.query)

    store = stores[0]["store_id"] if stores else req.store_id

    base = {
        "intent": intent,
        "status": "answered",
        "key_metrics": [],
        "recommendations": [],
        "evidence": [],
        "policy_citations": [],
        "assumptions": [],
        "limitations": [],
        "confidence": "medium",
        "llm_used": False,
        "data_used": {"tables": [], "index": True},
        "clarification_message": None,
        "needs_clarification": False,
    }

    # ---------- ambiguous product ----------
    if intent == "PRODUCT_PERFORMANCE" and len(products) > 1:
        base["intent"] = "AMBIGUOUS_PRODUCT"
        base["needs_clarification"] = True
        base["clarification_message"] = (
            f"I found several products matching your question: {', '.join(p['product_name'] for p in products[:5])}."
            " Please tell me which one (or its product ID), for example 'P-DEMO-001'.")
        base["candidates"] = [{"product_id": p["product_id"], "product_name": p["product_name"],
                               "category": p["category"]} for p in products[:5]]
        base["answer"] = base["clarification_message"]
        return base

    # ---------- unanswerable ----------
    ql = req.query.lower().strip()
    ql_word = " " + ql.replace("?", " ") + " "
    if any(u in ql_word for u in UNANSWERABLE):
        base["status"] = "insufficient_data"
        base["answer"] = INSUFFICIENT_DATA_MSG
        base["limitations"] = ["This question is outside the retail dataset scope"]
        return base
    if ql in GREETINGS or (len(ql.split()) == 1 and ql.split()[0].strip("?,.! ") in GREETINGS):
        base["intent"] = "GENERAL"
        base["answer"] = ("Hi! I'm the Retail Intelligence Copilot. Ask me things like "
                          "'What needs attention today?', 'Which products are running out of stock?', "
                          "'How is a product doing?', or 'What will next week look like?'")
        return base

    if intent == "PRODUCT_PERFORMANCE" and not products:
        # A product-looking question with no catalog match -> transparent unknown
        base["intent"] = "UNKNOWN_PRODUCT"
        base["needs_clarification"] = True
        base["status"] = "insufficient_data"
        base["clarification_message"] = (
            f"I couldn't find \"{req.query.strip().strip('?')}\" in the product catalog. "
            "Please specify a product name or its product ID (e.g. P-DEMO-001) so I can pull "
            "the right numbers.")
        base["answer"] = INSUFFICIENT_DATA_MSG
        base["limitations"] = ["No catalog entries matched this query"]
        return base

    # ---------- facts per intent ----------
    fact_result = _gather_facts(intent, product=products[0] if products else None,
                                store=store, query=req.query, req=req)
    facts = fact_result["facts"]
    base["data_used"]["tables"] = fact_result["tables"]

    # ---------- RAG context ----------
    try:
        rag_context = retrieval.context(req.query, top_k=6)
        base["data_used"]["index"] = True
    except Exception:  # noqa: BLE001
        rag_context = []
        base["data_used"]["index"] = False

    # ---------- synthesize ----------
    if req.use_llm and GEMINI_AVAILABLE:
        base["llm_used"] = True
        try:
            response = _llm_narrate(intent, req.query, facts, rag_context, store)
            base.update(response)
            return base
        except GeminiUnavailable as exc:
            log.warning("LLM path skipped: %s", exc)
            base["llm_used"] = False
            base["limitations"].append("Gemini unavailable; deterministic explanation provided")

    deterministic = _deterministic_answer(intent, facts, req.query, rag_context, store)
    base.update(deterministic)
    return base


def _gather_facts(intent: str, product: dict | None, store: str | None,
                  query: str, req: CopilotRequest) -> dict:
    """Return facts + list of DB tables used."""
    tables: list[str] = []
    facts: dict[str, Any] = {}

    if product:
        pf = _facts_for_product(product, store)
        facts["product_facts"] = pf
        tables += ["sales", "metrics_snapshot"]

    if intent in ("ATTENTION", "STOCKOUT", "OVERSTOCK", "SLOW_MOVING",
                  "SALES_SPIKE", "SALES_DROP", "PROMOTION", "STORE", "GENERAL"):
        ctx = _demand_context(store)
        facts["context"] = ctx
        tables += ["metrics_snapshot", "daily_sales_summary"]

    if intent == "STOCKOUT":
        facts["stockout_risks"] = inventory_engine.stockout_risk(store, 10)

    if intent == "OVERSTOCK":
        facts["overstock_risks"] = [r for r in data_loader.product_pairs() if r["overstock"]][:8]

    if intent == "SLOW_MOVING":
        facts["slow_moving"] = [r for r in data_loader.product_pairs()
                                if r["slow_moving"] or r["dead_stock"]][:8]

    if intent in ("SALES_SPIKE", "SALES_DROP"):
        anoms = anomaly_detection.detect_anomalies(limit=15)
        facts["anomalies"] = anoms
        facts["anomalies_summary"] = {"total": len(anoms), "promotion_driven": sum(1 for a in anoms if a["promo_overlap"])}
        tables += ["sales", "promotions", "metrics_snapshot"]

    if intent == "PROMOTION":
        promos = db.query_df("""
            SELECT p.promo_id, p.promotion_name, p.product_id, pr.product_name,
                   p.start_date, p.end_date, p.discount_pct, p.demand_lift_x
            FROM promotions p JOIN products pr ON pr.product_id = p.product_id
            ORDER BY p.start_date""")
        facts["promotions"] = promos
        tables += ["promotions"]

    if intent == "FORECAST":
        facts["forecast_network"] = forecasting.forecast_dashboard(14)
        facts["top_products"] = analytics.top_products(30, 5)
        tables += ["daily_sales_summary"]

    if intent == "LEADERBOARD":
        query = ("SELECT m.store_id, m.product_id, p.product_name, p.category, "
                 "m.margin_pct, m.revenue_30d, m.units_30d, m.stock "
                 "FROM metrics_snapshot m JOIN products p ON p.product_id = m.product_id "
                 "WHERE m.history_days >= 21 AND m.data_quality = ''")
        best_margin = db.query_df(query + " ORDER BY m.margin_pct DESC LIMIT 8")
        worst_margin = db.query_df(query + " ORDER BY m.margin_pct ASC LIMIT 8")
        facts["leaderboard"] = {
            "by_revenue": analytics.top_products(30, 8),
            "best_margin": best_margin,
            "worst_margin": worst_margin,
        }
        tables += ["metrics_snapshot", "products"]

    if intent == "STORE":
        facts["store_comparison"] = analytics.store_comparison()
        tables += ["stores", "daily_sales_summary"]

    if intent == "PRODUCT_CATALOG":
        facts["catalog_sample"] = analytics.product_menu()[:20]
        tables += ["products"]

    if intent == "GENERAL":
        k = analytics.dashboard_kpis()
        facts["summary"] = {
            "as_of": k["as_of_date"],
            "kpis": k["kpis"],
            "store_count": len(analytics.store_comparison()),
        }
        tables += ["metrics_snapshot", "daily_sales_summary"]

    return {"facts": facts, "tables": sorted(set(tables))}


def _llm_narrate(intent: str, query: str, facts: dict, rag_context: list[str],
                 store: str | None) -> dict:
    from src.gemini_client import generate_json
    facts_json = json.dumps(facts, default=str)
    system_prompt = (
        "You are the explanation layer of a Retail Intelligence Copilot. "
        "You are given DETERMINISTIC FACTS computed from a retail database and RELEVANT POLICY DOCUMENTS. "
        "Rules:\n"
        "1. You MUST NOT compute, invent, or modify any business number; use only the facts provided.\n"
        "2. ALWAYS answer grounded in the facts; cite policy sections where relevant.\n"
        "3. If the question is outside the dataset or the facts contain insufficient data, answer ONLY with "
        "the exact sentence: \"I don't have enough data to answer reliably.\" and set status to insufficient_data.\n"
        "4. If the question is ambiguous, ask a clarifying question and set needs_clarification=true.\n"
        "5. Distinguish promotion-driven sales changes from natural ones when the facts show promo_overlap.\n"
        "6. Stay concise and business-focused.\n"
        "Respond ONLY with JSON shaped like:\n"
        '{"answer": str, "status": "answered|insufficient_data|data_quality_issue", '
        '"needs_clarification": bool, "clarification_message": str|null, '
        '"key_metrics": [{"label": str, "value": str|number}], '
        '"recommendations": [{"issue": str, "priority": "CRITICAL|HIGH|MEDIUM|LOW", '
        '"recommended_action": str, "reason": str, "confidence": str}], '
        '"assumptions": [str], "limitations": [str], "confidence": "high|medium|low"}'
    )
    user_prompt = (
        f"USER QUESTION: {query}\n"
        f"INTENT: {intent}\n\n"
        f"DETERMINISTIC FACTS:\n{facts_json}\n\n"
        f"RELEVANT POLICY DOCUMENTS:\n" + ("\n".join(rag_context) if rag_context else "(none matched)")
    )
    resp = generate_json(system_prompt, user_prompt)
    resp.setdefault("answer", "Here are the facts for your question.")
    resp.setdefault("status", "answered")
    resp.setdefault("needs_clarification", False)
    if "clarification_message" not in resp:
        resp["clarification_message"] = None
    resp.setdefault("key_metrics", [])
    resp.setdefault("recommendations", [])
    resp.setdefault("assumptions", [])
    resp.setdefault("limitations", [])
    resp.setdefault("confidence", "medium")
    # Merge deterministic evidence back in
    resp["evidence"] = _evidence_from_facts(facts)
    resp["policy_citations"] = _citations_from_facts(facts)
    resp["llm_used"] = True
    return resp


def _deterministic_answer(intent: str, facts: dict, query: str,
                          rag_context: list[str], store: str | None) -> dict:
    out: dict[str, Any] = {
        "key_metrics": [], "recommendations": [],
        "evidence": [], "policy_citations": _citations_from_facts(facts),
        "assumptions": [], "limitations": [
            "Deterministic engine response (no LLM key configured)",
            "Recommendations are based on trailing 30-day demand"],
        "confidence": "high",
        "needs_clarification": False,
        "clarification_message": None,
        "llm_used": False,
    }

    pf = facts.get("product_facts")

    if intent == "PRODUCT_PERFORMANCE" and pf:
        name = pf["product"]
        hist = pf["sales_history"]
        if pf.get("months_on_record", 0) == 0 or len(hist) == 0:
            out["status"] = "insufficient_data"
            out["answer"] = INSUFFICIENT_DATA_MSG
            return out
        if pf.get("months_on_record", 0) == 1:
            out["status"] = "insufficient_data"
            out["answer"] = INSUFFICIENT_DATA_MSG
            return out

        current = hist[-1]
        prev = hist[-2]
        change = _month_percent(pf)
        lines = [f"'{name}' ({pf.get('category','')}) sold {safe_int(current['units'])} units "
                 f"({fmt_money(current['revenue'])}) in the most recent month"]
        if change is not None:
            lines.append(f"that is {change:+.1f}% vs the prior month ({safe_int(prev['units'])} units)")
        if pf.get("stock") is not None:
            lines.append(f"current stock {safe_int(pf['stock'])} units, cover {fmt_num(pf['cover_days'],1)}d, "
                         f"risk {pf['risk']}")
        if pf.get("anomaly") in ("SPIKE", "DROP"):
            lines.append(f"recent demand flags a {pf['anomaly']}"
                         + (f", promotion-driven while '{pf.get('promo_name')}' was active" if pf.get("promo_overlap") else ""))
        out["answer"] = ". ".join(lines) + "."
        out["key_metrics"] = [
            {"label": "Units sold (last month)", "value": safe_int(current["units"])},
            {"label": "Revenue (last month)", "value": fmt_money(current["revenue"])},
            {"label": "Month-over-month change", "value": f"{change:+.1f}%" if change is not None else "n/a"},
        ]
        if pf.get("stock") is not None:
            out["key_metrics"].append({"label": "Current stock", "value": safe_int(pf["stock"])})
            out["key_metrics"].append({"label": "Cover (days)", "value": fmt_num(pf["cover_days"], 1)})
            out["key_metrics"].append({"label": "Risk", "value": pf["risk"]})
        out["evidence"] = _evidence_from_facts(facts)
        return out

    if intent == "ATTENTION":
        ctx = facts.get("context", {})
        attention = ctx.get("attention_top", [])
        k = ctx.get("kpis", {}).get("kpis", {})
        if not attention:
            out["status"] = "answered"
            out["answer"] = "No high-priority issues found right now. All product-store pairs are healthy."
            out["key_metrics"] = [{"label": "Critical", "value": safe_int(k.get("stockout_critical"))},
                                  {"label": "High", "value": safe_int(k.get("stockout_high"))},
                                  {"label": "Overstock", "value": safe_int(k.get("overstock_items"))}]
            return out
        lines = [f"Here are the top {len(attention)} attention items:"]
        for a in attention:
            lines.append(f"[{a['priority']}] {a['issue_type']} - {a['product_name']} @ {a['store_id']}: {a['reason']}")
        out["answer"] = "\n".join(lines)
        out["key_metrics"] = [{"label": a["issue_type"], "value": a["product_name"]} for a in attention[:4]]
        out["recommendations"] = [{"issue": a["issue_type"], "priority": a["priority"],
                                   "recommended_action": "Review and act per policy",
                                   "reason": a["reason"], "confidence": "high"}
                                  for a in attention[:5]]
        out["evidence"] = _evidence_from_facts(facts)
        return out

    if intent == "STOCKOUT":
        risks = facts.get("stockout_risks", [])
        if not risks:
            out["answer"] = "No items are currently at risk of stock-out. Inventory is in good shape."
            out["key_metrics"] = [{"label": "At risk", "value": 0}]
            return out
        lines = [f"{len(risks)} item(s) need attention:"]
        for r in risks[:8]:
            lines.append(f"[{r['risk']}] {r['product_name']} @ {r['store_id']}: stock {safe_int(r['stock'])}, "
                         f"cover {fmt_num(r['cover_days'],1)}d, lead {safe_int(r['lead_time_days'])}d, "
                         f"order {safe_int(r['recommended_order_qty'])} units")
        out["answer"] = "\n".join(lines)
        out["key_metrics"] = [{"label": "At-risk items", "value": len(risks)}]
        covers = [r["cover_days"] for r in risks if r["cover_days"] > 0]
        if covers:
            out["key_metrics"].append({"label": "Worst cover", "value": f"{math.ceil(min(covers))}d"})
            out["recommendations"] = [{"issue": "STOCKOUT_RISK", "priority": r["risk"],
                                       "recommended_action": f"Reorder {safe_int(r['recommended_order_qty'])} units",
                                       "reason": f"cover {fmt_num(r['cover_days'],1)}d vs lead {safe_int(r['lead_time_days'])}d",
                                       "confidence": "high"} for r in risks[:5]]
        out["evidence"] = _evidence_from_facts(facts)
        return out

    if intent in ("OVERSTOCK", "SLOW_MOVING"):
        items = facts.get("overstock_risks") if intent == "OVERSTOCK" else facts.get("slow_moving", [])
        if not items:
            out["answer"] = f"No {intent.lower().replace('_',' ')} items detected in the current snapshot."
            return out
        lines = [f"{len(items)} {intent.lower().replace('_',' ')} item(s):"]
        for it in items[:8]:
            lines.append(f"- {it['product_name']} @ {it['store_id']}: stock {safe_int(it['stock'])}, "
                         f"units_30d {safe_int(it['units_30d'])}, cover {fmt_num(it['cover_days'],1)}d")
        out["answer"] = "\n".join(lines)
        out["key_metrics"] = [{"label": intent.replace("_", " ").title(), "value": len(items)}]
        out["recommendations"] = [{"issue": intent, "priority": "MEDIUM",
                                   "recommended_action": "Run a clearance strategy (promote, transfer, or stop reordering)",
                                   "reason": "stock exceeds 90 days of cover with weak recent demand",
                                   "confidence": "high"} for _ in items[:3]]
        out["evidence"] = _evidence_from_facts(facts)
        return out

    if intent in ("SALES_SPIKE", "SALES_DROP"):
        anoms = facts.get("anomalies", [])
        relevant = [a for a in anoms if a["signal"] == ("SPIKE" if intent == "SALES_SPIKE" else "DROP")]
        if not relevant:
            out["answer"] = f"No {intent.lower()} signals detected over the trailing window."
            return out
        lines = [f"{len(relevant)} {intent.lower()} signal(s) in the last 7 days:"]
        for a in relevant[:8]:
            tag = " (promotion-driven)" if a["promo_overlap"] else ""
            lines.append(f"- {a['product_name']} @ {a['store_id']}: {a['signal']} "
                         f"{a['change_pct']:+.1f}% ({fmt_num(a['mean_units_7d'],1)} vs "
                         f"{fmt_num(a['mean_prior_30d'],1)} units/day){tag}")
        out["answer"] = "\n".join(lines)
        out["key_metrics"] = [
            {"label": "Signals", "value": len(relevant)},
            {"label": "Promotion-driven", "value": sum(1 for a in relevant if a["promo_overlap"])},
        ]
        out["recommendations"] = [{"issue": a["signal"], "priority": "MEDIUM",
                                   "recommended_action": "Confirm stock covers the elevated run rate" if a["signal"] == "SPIKE" else "Review pricing / availability",
                                   "reason": f"{a['change_pct']:+.1f}% week-over-week",
                                   "confidence": "high"} for a in relevant[:3]]
        out["evidence"] = _evidence_from_facts(facts)
        return out

    if intent == "PROMOTION":
        promos = facts.get("promotions", [])
        if not promos:
            out["answer"] = "No promotions are recorded in the dataset."
            return out
        lines = [f"{len(promos)} promotion(s) in the period:"]
        for p in promos[:8]:
            lines.append(f"- {p['promotion_name']} on {p['product_name']} ({p['start_date']} → {p['end_date']}), "
                         f"-{safe_float(p['discount_pct']):.0f}%, lift ~{safe_float(p['demand_lift_x'])}x")
        out["answer"] = "\n".join(lines)
        out["key_metrics"] = [{"label": "Active promotions", "value": len(promos)}]
        out["evidence"] = _evidence_from_facts(facts)
        return out

    if intent == "FORECAST":
        fc = facts.get("forecast_network", {})
        fcast = fc.get("forecast_units", [])
        if not fcast:
            out["answer"] = INSUFFICIENT_DATA_MSG
            out["status"] = "insufficient_data"
            return out
        next7 = fcast[:7]
        total = sum(x["units"] for x in next7)
        out["answer"] = (f"Network forecast for the next {len(next7)} days: ~{fmt_num(total,0)} units "
                         f"(base {fmt_num(fc.get('base_units',0),1)} units/day, method: weighted moving average).")
        out["key_metrics"] = [{"label": "Forecast next 7d (units)", "value": round(total, 1)},
                              {"label": "Base rate (units/day)", "value": fmt_num(fc.get("base_units", 0), 1)}]
        return out

    if intent == "STORE":
        stores = facts.get("store_comparison", [])
        lines = ["Store comparison (trailing 30 days):"]
        for s in stores[:8]:
            lines.append(f"- {s['store_name']} ({s['store_id']}): {fmt_money(s['revenue_30d'])} revenue, "
                         f"{safe_int(s['units_30d'])} units, {s['revenue_change_pct']:+.1f}% MoM")
        out["answer"] = "\n".join(lines)
        out["key_metrics"] = [{"label": "Stores", "value": len(stores)},
                              {"label": "Top store", "value": stores[0]["store_name"] if stores else "-"}]
        return out

    if intent == "LEADERBOARD":
        lb = facts.get("leaderboard", {})
        best_m = lb.get("best_margin", []) or []
        worst_m = lb.get("worst_margin", []) or []
        by_rev = lb.get("by_revenue", []) or []
        lines = []
        if by_rev:
            lines.append("Top products by 30-day revenue:")
            for r in by_rev[:6]:
                lines.append(f"- {r['product_name']}: {fmt_money(r['revenue'])} "
                             f"on {safe_int(r['units'])} units")
        if best_m:
            lines.append("Highest-margin product-store pairs (trailing 30 days):")
            for r in best_m[:5]:
                lines.append(f"- {r['product_name']} @ {r['store_id']}: "
                             f"margin {round(safe_float(r['margin_pct']),1)}%, "
                             f"revenue {fmt_money(r['revenue_30d'])}")
        if worst_m:
            lines.append("Lowest-margin product-store pairs:")
            for r in worst_m[:5]:
                lines.append(f"- {r['product_name']} @ {r['store_id']}: "
                             f"margin {round(safe_float(r['margin_pct']),1)}%")
        if not lines:
            out["answer"] = "Not enough clean history to rank products."
            return out
        out["answer"] = "\n".join(lines)
        out["key_metrics"] = [
            {"label": "Top seller (revenue)", "value": by_rev[0]["product_name"]} if by_rev else {"label": "Top seller", "value": "-"},
            {"label": "Best margin", "value": f"{round(safe_float(best_m[0]['margin_pct']),1)}%"} if best_m else {"label": "Best margin", "value": "-"},
            {"label": "Worst margin", "value": f"{round(safe_float(worst_m[0]['margin_pct']),1)}%"} if worst_m else {"label": "Worst margin", "value": "-"},
        ]
        out["evidence"] = _evidence_from_facts(facts)
        out["policy_citations"] = _citations_from_facts(facts)
        return out

    # GENERAL + everything else
    summary = facts.get("summary")
    if summary:
        k = summary["kpis"]
        out["answer"] = (f"As of {summary['as_of']}, the network did {fmt_money(k.get('revenue_30d'))} revenue "
                         f"on {safe_int(k.get('units_30d'))} units in the trailing 30 days. "
                         f"Inventory carries {fmt_money(k.get('inventory_value'))} in stock value; "
                         f"{safe_int(k.get('stockout_critical'))} critical and {safe_int(k.get('stockout_high'))} "
                         f"high stock-out risks, {safe_int(k.get('overstock_items'))} overstock, "
                         f"{safe_int(k.get('slow_moving_items'))} slow-moving items.")
        out["key_metrics"] = [{"label": "Revenue 30d", "value": fmt_money(k.get("revenue_30d"))},
                              {"label": "Units 30d", "value": safe_int(k.get("units_30d"))},
                              {"label": "Inventory value", "value": fmt_money(k.get("inventory_value"))}]
        return out
    out["answer"] = "I'm here to help with sales and inventory questions grounded in your store data."
    return out


def _evidence_from_facts(facts: dict) -> list[dict]:
    ev: list[dict] = []
    pf = facts.get("product_facts")
    stockout = facts.get("stockout_risks", [])
    anomalies = facts.get("anomalies", [])
    if pf and pf.get("sales_history"):
        hist = pf["sales_history"]
        latest = hist[-1]
        ev.append({"source": "sales/grouped-by-month",
                   "record": {"product": pf["product"], "month": latest["month"],
                              "units": safe_int(latest["units"]), "revenue": round(safe_float(latest["revenue"]), 2)},
                   "policy": "documents/data_definitions.md [DEF-01, DEF-11]"})
    for a in anomalies[:5]:
        ev.append({"source": "sales/rolling-7d",
                   "record": {"product": a["product_name"], "store": a["store_id"], "signal": a["signal"],
                              "change_pct": a["change_pct"], "promo_overlap": a["promo_overlap"]},
                   "policy": "documents/sales_anomaly_policy.md [ANM-D-01/02/04]"})
    return ev


def _citations_from_facts(facts: dict) -> list[dict]:
    docs = []
    if facts.get("product_facts"):
        docs.append({"document": "documents/data_definitions.md", "rule_id": "DEF-01..08", "summary": "Sales & inventory metric definitions"})
    if facts.get("stockout_risks"):
        docs.append({"document": "documents/replenishment_policy.md", "rule_id": "STK-01..06", "summary": "Stock-out risk and reorder rules"})
    if facts.get("anomalies"):
        docs.append({"document": "documents/sales_anomaly_policy.md", "rule_id": "ANM-D-01..07", "summary": "Spike/drop anomaly detection"})
    if facts.get("overstock_risks"):
        docs.append({"document": "documents/overstock_policy.md", "rule_id": "OVR-01..07", "summary": "Overstock detection & resolution"})
    return docs