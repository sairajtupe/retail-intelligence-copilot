import sys, json
sys.path.insert(0, ".")
from src.schemas import CopilotRequest
from src import copilot
import src.retrieval as retrieval

def q(text, **kw):
    req = CopilotRequest(query=text, **kw)
    r = copilot.answer(req)
    print("=" * 80)
    print("Q:", text)
    print("intent:", r.get("intent"), "| status:", r.get("status"),
          "| llm:", r.get("llm_used"), "| needs_clarification:", r.get("needs_clarification"))
    if r.get("clarification_message"):
        print("clarification:", r["clarification_message"][:160])
    print("answer:", (r.get("answer") or "")[:400])
    if r.get("key_metrics"):
        print("metrics:", json.dumps(r["key_metrics"][:4], default=str)[:300])
    if r.get("evidence"):
        print("evidence count:", len(r["evidence"]))
    if r.get("policy_citations"):
        print("citations:", len(r["policy_citations"]))

q("What needs my attention today?")
q("Which products are running out of stock?")
q("How is Apple doing?")
q("Weather forecast for tomorrow?")
q("Tell me about product P-DEMO-001 in store S01")
q("Why did sales spike?")
q("What are the overstock situations?")
q("What will next week look like?")
q("hi")
print("=" * 80)
print("RETRIEVAL TEST")
for c in retrieval.search("promotion spike overstock policy", top_k=3):
    print(c["score"], c["source"], c["section"], "|", c["text"][:80])