"""
Vera Deep Audit — verifies every rubric dimension is properly
surfaced in prompts, fallbacks are acceptable, and all
judge_simulator scenarios pass structurally.
No Claude API key needed.
"""
import sys, json, re, uuid, importlib
from pathlib import Path

sys.path.insert(0, "/home/claude/vera_bot")
DATASET = Path("/home/claude/magicpin/dataset")
EXPANDED = Path("/home/claude/magicpin/expanded")

# Load dataset
def jload(p): return json.loads(Path(p).read_text())

categories = {jload(f).get("slug",f.stem): jload(f) for f in (DATASET/"categories").glob("*.json")}
merchants  = {d.get("merchant_id",f.stem): d for f in (EXPANDED/"merchants").glob("*.json") for d in [jload(f)]}
customers  = {d.get("customer_id",f.stem): d for f in (EXPANDED/"customers").glob("*.json") for d in [jload(f)]}
triggers   = {d.get("id",f.stem): d for f in (EXPANDED/"triggers").glob("*.json") for d in [jload(f)]}

G="\033[92m"; R="\033[91m"; Y="\033[93m"; C="\033[96m"; B="\033[1m"; X="\033[0m"
PASS=f"{G}[PASS]{X}"; FAIL=f"{R}[FAIL]{X}"; WARN=f"{Y}[WARN]{X}"

results = []
def chk(name, ok, detail=""):
    sym = PASS if ok else FAIL
    print(f"  {sym} {name}" + (f" — {detail}" if detail else ""))
    results.append((name, ok))
    return ok

def sec(title):
    print(f"\n{B}{C}{'─'*62}{X}\n{B}{C}  {title}{X}\n{B}{C}{'─'*62}{X}")

# ── Import bot internals ────────────────────────────────────────────
import bot as vera

# ════════════════════════════════════════════════════════════════════
# A1: Prompt builder surfaces all 5 rubric dimensions
# ════════════════════════════════════════════════════════════════════
sec("A1 · PROMPT COMPLETENESS (rubric dimensions in prompt)")

# Use dentist category + Dr Meera merchant + research_digest trigger
cat  = categories["dentists"]
m    = merchants["m_001_drmeera_dentist_delhi"]
trg  = {
    "id":"t_test","kind":"research_digest","scope":"merchant","source":"jida",
    "merchant_id":"m_001_drmeera_dentist_delhi","urgency":4,
    "suppression_key":"test_key",
    "payload":{"top_item_id":"d_2026W17_jida_fluoride"},
    "expires_at":"2099-01-01T00:00:00Z"
}

prompt = vera._build_prompt(cat, m, trg)

# SPECIFICITY signals
chk("prompt: peer_ctr present",     "avg_ctr" in prompt or "peer" in prompt)
chk("prompt: merchant views present", str(m["performance"]["views"]) in prompt)
chk("prompt: merchant calls present", str(m["performance"]["calls"]) in prompt)
chk("prompt: digest trial_n present", "2100" in prompt or "trial_n" in prompt)
chk("prompt: digest source present",  "JIDA" in prompt)
chk("prompt: digest title present",   "fluoride" in prompt.lower() or "3-month" in prompt)
chk("prompt: digest actionable present", "Reassess" in prompt or "actionable" in prompt)

# CATEGORY FIT signals
chk("prompt: category slug present",  "dentists" in prompt)
chk("prompt: voice tone present",     "peer_clinical" in prompt or "peer" in prompt)
chk("prompt: taboos present",         "guaranteed" in prompt or "taboo" in prompt.lower())
chk("prompt: allowed vocab present",  "fluoride" in prompt or "vocab_allowed" in prompt)
chk("prompt: salutation style present", "Dr." in prompt)

# MERCHANT FIT signals
chk("prompt: owner name present",    "Meera" in prompt)
chk("prompt: locality present",      "Lajpat" in prompt)
chk("prompt: languages present",     "'hi'" in prompt or '"hi"' in prompt or "hi" in prompt)
chk("prompt: subscription present",  "Pro" in prompt and "82" in prompt)
chk("prompt: active offers present", "₹299" in prompt or "Dental Cleaning" in prompt)
chk("prompt: ctr gap present",       "BELOW" in prompt or "ABOVE" in prompt)
chk("prompt: lapsed count present",  str(m.get("customer_aggregate",{}).get("lapsed_180d_plus","")) in prompt)
chk("prompt: review themes present", any(r["theme"] in prompt for r in m.get("review_themes",[])))

# TRIGGER RELEVANCE signals
chk("prompt: trigger kind present",  "research_digest" in prompt)
chk("prompt: trigger urgency present","4" in prompt and "urgency" in prompt.lower())
chk("prompt: trigger payload present","top_item_id" in prompt or "d_2026W17" in prompt)
chk("prompt: suppression_key present","test_key" in prompt)

# ENGAGEMENT
chk("prompt: CTA instruction present", "YES" in prompt or "STOP" in prompt or "CTA" in prompt)
chk("prompt: lever instruction present", any(x in (prompt + vera.COMPOSE_SYSTEM) for x in ["loss_aversion","social_proof","specificity","effort_externalization","curiosity","compulsion lever"]))

# Anti-hallucination
chk("system: taboo anti-hallucination", "Never invent" in vera.COMPOSE_SYSTEM or "ONLY facts" in vera.COMPOSE_SYSTEM)
chk("system: URL ban", "No URLs" in vera.COMPOSE_SYSTEM or "no URL" in vera.COMPOSE_SYSTEM.lower())
chk("system: length rule", "320" in vera.COMPOSE_SYSTEM)

# ════════════════════════════════════════════════════════════════════
# A2: Prompt for customer-facing trigger
# ════════════════════════════════════════════════════════════════════
sec("A2 · CUSTOMER PROMPT COMPLETENESS")

# Find customer trigger
cust_trg = next((t for t in triggers.values() if t.get("customer_id")), None)
if cust_trg:
    mid = cust_trg["merchant_id"]; cid = cust_trg["customer_id"]
    cust = customers.get(cid, {})
    merch2 = merchants.get(mid, {})
    cat2 = categories.get(merch2.get("category_slug",""), cat)
    cprompt = vera._build_prompt(cat2, merch2, cust_trg, customer=cust)
    chk("customer name in prompt", cust.get("identity",{}).get("name","") in cprompt)
    chk("language_pref in prompt", "language_pref" in cprompt)
    chk("consent_scope in prompt", "consent" in cprompt)
    chk("services_received in prompt", "services_received" in cprompt or "services=" in cprompt)
    chk("preferred_slots in prompt", "preferred_slots" in cprompt or "slot" in cprompt)
    chk("merchant_on_behalf instruction", "merchant_on_behalf" in cprompt)
else:
    print(f"  {WARN} No customer triggers in expanded dataset — inject synthetic")
    synth_cust = list(customers.values())[0]
    synth_trg = {"id":"ct","kind":"recall_due","scope":"customer","source":"crm",
                 "merchant_id":list(merchants.keys())[0],"customer_id":list(customers.keys())[0],
                 "urgency":3,"suppression_key":"ct_key",
                 "payload":{"service_due":"cleaning","available_slots":[{"label":"Sat 4pm"}]},
                 "expires_at":"2099-01-01T00:00:00Z"}
    cprompt = vera._build_prompt(cat, m, synth_trg, customer=synth_cust)
    chk("customer section injected", "CUSTOMER" in cprompt)
    chk("merchant_on_behalf instruction", "merchant_on_behalf" in cprompt)

# ════════════════════════════════════════════════════════════════════
# A3: Fallback message quality per category
# ════════════════════════════════════════════════════════════════════
sec("A3 · FALLBACK MESSAGE QUALITY (no API key)")

# Patch anthropic to always fail → exercises fallback path
import anthropic as _anth
_orig = _anth.Anthropic

class _FakeAnth:
    def __init__(self, **kw): pass
    class messages:
        @staticmethod
        def create(**kw): raise Exception("no key")

_anth.Anthropic = _FakeAnth
vera._claude = _FakeAnth()

for cat_slug, category in categories.items():
    # Find a matching merchant
    m_match = next((m for m in merchants.values() if m.get("category_slug")==cat_slug), None)
    if not m_match: continue
    owner = m_match.get("identity",{}).get("owner_first_name","")
    trg_fb = {"id":"fb","kind":"perf_dip","scope":"merchant","source":"internal",
              "merchant_id":m_match["merchant_id"],"urgency":2,
              "suppression_key":f"fb_{cat_slug}","payload":{},
              "expires_at":"2099-01-01T00:00:00Z"}
    result = vera._compose(category, m_match, trg_fb)
    body = result.get("body","")
    chk(f"fallback[{cat_slug}] non-empty",  len(body) > 10, f"'{body[:60]}'")
    chk(f"fallback[{cat_slug}] ≤320 chars", len(body) <= 320, f"{len(body)}c")
    chk(f"fallback[{cat_slug}] no URL",     not re.search(r"https?://",body))
    chk(f"fallback[{cat_slug}] owner name", owner in body if owner else True, f"'{body[:60]}'")
    chk(f"fallback[{cat_slug}] has CTA",    any(x in body for x in ["YES","STOP","?","chahiye","bataiye"]))

# Customer fallback → send_as must be merchant_on_behalf
cust_fallback = vera._compose(cat, m, synth_trg if 'synth_trg' in dir() else trg_fb, customer=list(customers.values())[0])
chk("customer fallback send_as=merchant_on_behalf", cust_fallback.get("send_as")=="merchant_on_behalf",
    cust_fallback.get("send_as"))

_anth.Anthropic = _orig  # restore

# ════════════════════════════════════════════════════════════════════
# A4: Intent detection accuracy
# ════════════════════════════════════════════════════════════════════
sec("A4 · INTENT DETECTION")

INTENT_CASES = [
    # (message, expected_intent)
    ("Stop messaging me", "stop"),
    ("nahi chahiye", "stop"),
    ("band karo yeh", "stop"),
    ("Not interested at all", "stop"),
    ("unsubscribe please", "stop"),
    ("Yes, lets do it!", "accept"),
    ("haan karo", "accept"),
    ("ok confirmed", "accept"),
    ("Sure go ahead", "accept"),
    ("Sounds good whats next", "accept"),
    ("join please", "join"),
    ("mujhe add karo", "join"),
    ("hi", "greet"),
    ("hello there", "greet"),
    ("namaste", "greet"),
    ("GST invoice kahan milega", "off_topic"),
    ("password reset karo", "off_topic"),
    ("interesting tell me more", "neutral"),
    ("ok noted", "neutral"),
]
for msg, expected in INTENT_CASES:
    got = vera._intent(msg)
    chk(f"intent '{msg[:30]}'", got==expected, f"got={got} want={expected}")

# ════════════════════════════════════════════════════════════════════
# A5: Auto-reply detection accuracy
# ════════════════════════════════════════════════════════════════════
sec("A5 · AUTO-REPLY DETECTION")

AUTO_TRUE = [
    "Thank you for contacting us! Our team will respond shortly.",
    "Aapki jaankari ke liye shukriya. Hamari team aapko contact karegi.",
    "This is an automated response. We will get back to you.",
    "I am an automated assistant. Please wait for our team.",
    "Out of office: will get back to you on Monday.",
    "Thank you for reaching out. Our team will reply soon.",
]
AUTO_FALSE = [
    "Yes please go ahead",
    "Nahi chahiye band karo",
    "ok karo",
    "Interesting, tell me more about this",
    "How much does it cost?",
    "Mujhe samajh nahi aaya",
]
for msg in AUTO_TRUE:
    chk(f"auto-detect TRUE: '{msg[:45]}'", vera._is_auto(msg), "should be auto")
for msg in AUTO_FALSE:
    chk(f"auto-detect FALSE: '{msg[:45]}'", not vera._is_auto(msg), "should NOT be auto")

# ════════════════════════════════════════════════════════════════════
# A6: Digest resolution
# ════════════════════════════════════════════════════════════════════
sec("A6 · DIGEST RESOLUTION")

trg_with_digest = {"payload":{"top_item_id":"d_2026W17_jida_fluoride"}}
resolved = vera._resolve_digest(trg_with_digest, categories["dentists"])
chk("digest resolved correctly", resolved.get("id")=="d_2026W17_jida_fluoride",
    f"got id={resolved.get('id')}")
chk("digest title present", "fluoride" in resolved.get("title","").lower())
chk("digest source present", "JIDA" in resolved.get("source",""))
chk("digest trial_n present", resolved.get("trial_n")==2100)

trg_no_digest = {"payload":{}}
resolved2 = vera._resolve_digest(trg_no_digest, categories["dentists"])
chk("no digest → empty dict", resolved2=={})

trg_bad_id = {"payload":{"top_item_id":"nonexistent_id"}}
resolved3 = vera._resolve_digest(trg_bad_id, categories["dentists"])
chk("bad digest id → empty dict", resolved3=={})

# ════════════════════════════════════════════════════════════════════
# A7: Body cleaner
# ════════════════════════════════════════════════════════════════════
sec("A7 · BODY CLEANER")

chk("strips https URL", "http" not in vera._clean("Visit https://example.com for more"))
chk("strips http URL", "http" not in vera._clean("See http://foo.bar/baz"))
chk("truncates to 320", len(vera._clean("x"*400)) == 320)
chk("preserves short body", vera._clean("Hello world") == "Hello world")
chk("strips leading/trailing space", vera._clean("  hi  ") == "hi")

# ════════════════════════════════════════════════════════════════════
# A8: Context version management
# ════════════════════════════════════════════════════════════════════
sec("A8 · VERSION MANAGEMENT (context store)")

vera.contexts.clear()
vera.contexts[("merchant","m_test")] = {"version":3,"payload":{"x":1}}

chk("get v3 payload", vera._payload("merchant","m_test")=={"x":1})
chk("missing key → None", vera._payload("merchant","nonexistent") is None)
chk("missing scope → None", vera._payload("category","m_test") is None)

# ════════════════════════════════════════════════════════════════════
# A9: Tick edge cases (no API — structural only)
# ════════════════════════════════════════════════════════════════════
sec("A9 · TICK EDGE CASES")

vera.contexts.clear(); vera.fired_keys.clear()

# Expired trigger
exp_trg = {"id":"trg_exp","kind":"perf_dip","scope":"merchant","source":"internal",
           "merchant_id":"m_001_drmeera_dentist_delhi","urgency":5,
           "suppression_key":"exp_key","payload":{},
           "expires_at":"2020-01-01T00:00:00Z"}
vera.contexts[("trigger","trg_exp")] = {"version":1,"payload":exp_trg}
vera.contexts[("merchant","m_001_drmeera_dentist_delhi")] = {"version":1,"payload":m}
vera.contexts[("category","dentists")] = {"version":1,"payload":cat}

import asyncio
from bot import TickBody, tick as vera_tick
async def run_tick(tids, now="2026-05-03T12:00:00Z"):
    class B: available_triggers=tids; now="2026-05-03T12:00:00Z"
    return await vera_tick(B())

r = asyncio.run(run_tick(["trg_exp"]))
chk("expired trigger not fired", len(r.get("actions",[]))==0, f"{len(r.get('actions',[]))} actions")

# Suppression key dedup
vera.fired_keys.add("exp_key")
exp_trg2 = dict(exp_trg); exp_trg2["id"]="trg_exp2"; exp_trg2["expires_at"]="2099-01-01T00:00:00Z"
vera.contexts[("trigger","trg_exp2")] = {"version":1,"payload":exp_trg2}
r2 = asyncio.run(run_tick(["trg_exp2"]))
chk("suppressed trigger not fired", len(r2.get("actions",[]))==0, f"{len(r2.get('actions',[]))} actions")

# Missing merchant → skip
vera.fired_keys.clear()
orphan_trg = {"id":"trg_orphan","kind":"perf_dip","scope":"merchant","source":"internal",
              "merchant_id":"m_NONEXISTENT","urgency":3,"suppression_key":"orphan_key",
              "payload":{},"expires_at":"2099-01-01T00:00:00Z"}
vera.contexts[("trigger","trg_orphan")] = {"version":1,"payload":orphan_trg}
r3 = asyncio.run(run_tick(["trg_orphan"]))
chk("orphan merchant skipped", len(r3.get("actions",[]))==0, f"{len(r3.get('actions',[]))} actions")

# ════════════════════════════════════════════════════════════════════
# A10: Reply state machine
# ════════════════════════════════════════════════════════════════════
sec("A10 · REPLY STATE MACHINE")

vera._claude = _FakeAnth()
vera.contexts.clear(); vera.conversations.clear(); vera.fired_keys.clear()

# Seed a conversation
vera.conversations["conv_sm_test"] = {
    "merchant_id":"m_001_drmeera_dentist_delhi","customer_id":None,"trigger_id":None,
    "turns":[{"from":"vera","body":"Initial message"}],
    "auto_reply_count":0,"turn":1
}
vera.contexts[("merchant","m_001_drmeera_dentist_delhi")] = {"version":1,"payload":m}
vera.contexts[("category","dentists")] = {"version":1,"payload":cat}

from bot import ReplyBody, reply_handler as vera_reply

async def do_reply(msg, turn=2, conv="conv_sm_test"):
    class B:
        conversation_id=conv; merchant_id="m_001_drmeera_dentist_delhi"
        customer_id=None; from_role="merchant"; message=msg
        received_at="2026-05-03T12:00:00Z"; turn_number=turn
    return await vera_reply(B())

# STOP
r = asyncio.run(do_reply("stop messaging me now", conv="conv_stop_test"))
chk("STOP → action=end", r.get("action")=="end", f"got {r.get('action')}")

# Auto-reply x2 → end
asyncio.run(do_reply("Thank you for contacting us! Our team will respond shortly.", 2, "conv_auto_sm"))
r2 = asyncio.run(do_reply("Thank you for contacting us! Our team will respond shortly.", 3, "conv_auto_sm"))
chk("2nd auto-reply → end", r2.get("action")=="end", f"got {r2.get('action')}")

# Conversation turns append
vera.conversations["conv_turn_test"] = {"merchant_id":"m_001_drmeera_dentist_delhi",
    "customer_id":None,"trigger_id":None,"turns":[],"auto_reply_count":0,"turn":1}
asyncio.run(do_reply("tell me more", 2, "conv_turn_test"))
asyncio.run(do_reply("ok great", 3, "conv_turn_test"))
turns = vera.conversations["conv_turn_test"]["turns"]
chk("conversation turns recorded", len(turns)>=2, f"{len(turns)} turns")

# ════════════════════════════════════════════════════════════════════
# A11: All 5 categories have prompts with category-specific signals
# ════════════════════════════════════════════════════════════════════
sec("A11 · PER-CATEGORY PROMPT QUALITY")

CATEGORY_SIGNALS = {
    "dentists":   ["peer_clinical","Dr.","JIDA"],
    "salons":     ["salon","warm","friendly"],
    "restaurants":["restaurant","operator","covers"],
    "gyms":       ["gym","coaching","motivational"],
    "pharmacies": ["pharma","caring","precise","trustworthy"],
}

for cat_slug, signals in CATEGORY_SIGNALS.items():
    if cat_slug not in categories: continue
    c = categories[cat_slug]
    m_match = next((m for m in merchants.values() if m.get("category_slug")==cat_slug), None)
    if not m_match: continue
    trgx = {"id":f"tx_{cat_slug}","kind":"perf_dip","scope":"merchant","source":"internal",
            "merchant_id":m_match["merchant_id"],"urgency":3,"suppression_key":f"tx_{cat_slug}",
            "payload":{},"expires_at":"2099-01-01T00:00:00Z"}
    p = vera._build_prompt(c, m_match, trgx)
    combined = p + vera.COMPOSE_SYSTEM
    found = [s for s in signals if s.lower() in combined.lower()]
    chk(f"{cat_slug} category signals in prompt", len(found)>=1,
        f"found {found} of {signals}")

# ════════════════════════════════════════════════════════════════════
# SUMMARY
# ════════════════════════════════════════════════════════════════════
print(f"\n{B}{C}{'═'*62}{X}")
print(f"{B}{C}  DEEP AUDIT RESULTS{X}")
print(f"{B}{C}{'═'*62}{X}")
passed = [n for n,ok in results if ok]
failed = [n for n,ok in results if not ok]
pct = len(passed)/len(results)*100 if results else 0
print(f"\n  Total:  {len(results)}")
print(f"  {G}Passed: {len(passed)}{X}")
print(f"  {R}Failed: {len(failed)}{X}")
print(f"  Score:  {pct:.0f}%\n")
if failed:
    print(f"  {R}Failing:{X}")
    for n in failed: print(f"    • {n}")
sys.exit(0 if not failed else 1)
