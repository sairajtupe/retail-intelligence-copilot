import json, urllib.request, sys

BASE = "http://localhost:8000"

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read())

def post(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())

d = get("/api/dashboard")
print("DASH kpis revenue:", d["kpis"]["kpis"]["revenue_30d"], "units:", d["kpis"]["kpis"]["units_30d"])
print("DASH trend points:", len(d["trend"]), "categories:", len(d["categories"]), "top:", len(d["top_products"]))
print("DASH attention:", len(d["attention"]), "forecast days:", len(d["forecast"]["forecast_units"]))

att = get("/api/attention?max_items=10")
print("ATT items:", att["count"], "recs:", len(att["recommendations"]), "suppressions:", len(att["suppressions"]))
if att["items"]:
    iid = att["items"][0]["issue_id"]
    ev = get(f"/api/evidence/{iid}")
    print("EVIDENCE ok:", ev["issue_type"], ev["product"], "evidence_blocks:", len(ev["evidence"]))

prod = get("/api/products?q=cola")
print("PRODUCTS q=cola count:", prod["count"])

det = get("/api/products/P-DEMO-001?store_id=S01")
print("DETAIL P-DEMO-001 stock:", det["current_stock"], "risk:", det["risk"], "monthly:", len(det["monthly"]), "anomaly:", det.get("anomaly"))

st = get("/api/stores")
print("STORES:", len(st["stores"]), st["stores"][0]["store_name"])

tr = get("/api/sales/trend?days=14")
print("TREND days:", len(tr["points"]))

inv = get("/api/inventory/health")
print("INV value:", inv["health"]["total_inventory_value"], "stockout:", len(inv["stockout_risks"]), "overstock:", len(inv["overstock"]), "transfers:", len(inv["transfers"]))

# copilot batch
tests = [
    ("What needs my attention today?", {}),
    ("Which products are running out of stock?", {}),
    ("Tell me about P-DEMO-001", {}),
    ("How is Apple doing?", {}),
    ("Weather tomorrow?", {}),
    ("Why did sales spike?", {}),
    ("What are the overstock situations?", {}),
    ("Forecast next week", {}),
    ("What product has the best margin?", {}),
]
for q, kw in tests:
    r = post("/api/copilot", {"query": q, **kw})
    print(f"COPILOT [{q[:32]}] intent={r['intent']} status={r['status']} clarify={r.get('needs_clarification')} llm={r.get('llm_used')}")
    print("   answer:", (r.get("answer") or "")[:150].replace("\n", " | "))

# invalid request handling
try:
    post("/api/copilot", {"query": ""})
except Exception as e:
    print("EMPTY QUERY rejected:", type(e).__name__, e)

print("INDEX HTML served?")
import urllib.request
try:
    r = urllib.request.urlopen(BASE + "/", timeout=10)
    html = r.read().decode()
    print("   root status", r.status, "len", len(html), "has title:", "<title>" in html)
except Exception as e:
    print("   root err:", e)