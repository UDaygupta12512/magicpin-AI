"""
Vera full test suite — covers every judge_simulator scenario + edge cases.
Runs bot in-thread, no external server needed.
"""
import threading, time, json, sys, uuid, re
import urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone

# ── Boot server in background thread ─────────────────────────────────────────
sys.path.insert(0, "/home/claude/vera_bot")
import uvicorn

def _run_server():
    uvicorn.run("bot:app", host="127.0.0.1", port=8181, log_level="error")

t = threading.Thread(target=_run_server, daemon=True)
t.start()
time.sleep(3)

BASE = "http://127.0.0.1:8181"

# ── Colours ───────────────────────────────────────────────────────────────────
G="\033[92m"; R="\033[91m"; Y="\033[93m"; C="\033[96m"; B="\033[1m"; X="\033[0m"
PASS=f"{G}[PASS]{X}"; FAIL=f"{R}[FAIL]{X}"; WARN=f"{Y}[WARN]{X}"; INFO=f"{C}[INFO]{X}"

# ── HTTP helpers ──────────────────────────────────────────────────────────────
def _req(method, path, body=None, timeout=12):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method,
                                  headers={"Content-Type":"application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        try: return json.loads(e.read()), None
        except: return None, f"HTTP {e.code}"
    except Exception as e:
        return None, str(e)

def GET(p): return _req("GET", p)
def POST(p, b): return _req("POST", p, b)

now_iso = lambda: datetime.now(timezone.utc).isoformat().replace("+00:00","Z")

def ctx(scope, cid, version, payload):
    return POST("/v1/context", {"scope":scope,"context_id":cid,"version":version,
                                 "payload":payload,"delivered_at":now_iso()})

def tick(triggers):
    return POST("/v1/tick", {"now":now_iso(),"available_triggers":triggers})

def reply(conv_id, mid, msg, turn=2):
    return POST("/v1/reply", {"conversation_id":conv_id,"merchant_id":mid,
                               "from_role":"merchant","message":msg,
                               "received_at":now_iso(),"turn_number":turn})

def teardown():
    POST("/v1/teardown", {})

# ── Dataset ───────────────────────────────────────────────────────────────────
DATASET = Path("/home/claude/magicpin/dataset")
EXPANDED = Path("/home/claude/magicpin/expanded")

def load_json(p):
    try: return json.loads(Path(p).read_text())
    except: return {}

categories = {}
for f in (DATASET/"categories").glob("*.json"):
    d = load_json(f); categories[d.get("slug",f.stem)] = d

merchants = {}
for f in (EXPANDED/"merchants").glob("*.json"):
    d = load_json(f); merchants[d.get("merchant_id",f.stem)] = d

customers = {}
for f in (EXPANDED/"customers").glob("*.json"):
    d = load_json(f); customers[d.get("customer_id",f.stem)] = d

triggers = {}
for f in (EXPANDED/"triggers").glob("*.json"):
    d = load_json(f); triggers[d.get("id",f.stem)] = d

# ── Scoring ───────────────────────────────────────────────────────────────────
results = []

def check(name, ok, detail=""):
    sym = PASS if ok else FAIL
    print(f"  {sym} {name}" + (f" — {detail}" if detail else ""))
    results.append((name, ok))
    return ok

def section(title):
    print(f"\n{B}{C}{'─'*60}{X}")
    print(f"{B}{C}  {title}{X}")
    print(f"{B}{C}{'─'*60}{X}")

# ═════════════════════════════════════════════════════════════════════════════
# TEST 1: Basic endpoints
# ═════════════════════════════════════════════════════════════════════════════
section("T1 · ENDPOINT LIVENESS")

d,e = GET("/v1/healthz")
check("healthz 200", e is None and d.get("status")=="ok", f"status={d.get('status') if d else e}")

d,e = GET("/v1/metadata")
check("metadata 200", e is None and bool(d.get("team_name")), f"team={d.get('team_name') if d else e}")
check("metadata has model", d and bool(d.get("model")), d.get("model","") if d else "")
check("metadata has approach", d and len(d.get("approach",""))>30)

# ═════════════════════════════════════════════════════════════════════════════
# T2: Context push — valid + edge cases
# ═════════════════════════════════════════════════════════════════════════════
section("T2 · CONTEXT PUSH")
teardown()

# Push all categories
for slug, cat in categories.items():
    d,e = ctx("category", slug, 1, cat)
    check(f"category/{slug}", d and d.get("accepted"), e)

# Push 3 merchants
m_ids = list(merchants.keys())[:3]
for mid in m_ids:
    d,e = ctx("merchant", mid, 1, merchants[mid])
    check(f"merchant/{mid[:20]}", d and d.get("accepted"), e)

# Version conflict (same version → 409)
d,e = ctx("merchant", m_ids[0], 1, merchants[m_ids[0]])
check("stale_version rejected", d and not d.get("accepted") and d.get("reason")=="stale_version",
      f"accepted={d.get('accepted') if d else e}")

# Version upgrade (v2 replaces v1)
d,e = ctx("merchant", m_ids[0], 2, merchants[m_ids[0]])
check("version upgrade accepted", d and d.get("accepted"), e)

# Invalid scope
d,e = ctx("unknown_scope", "foo", 1, {})
check("invalid scope rejected", d and not d.get("accepted"), f"reason={d.get('reason') if d else e}")

# ═════════════════════════════════════════════════════════════════════════════
# T3: Tick — proactive sends
# ═════════════════════════════════════════════════════════════════════════════
section("T3 · TICK (PROACTIVE SENDS)")
teardown()

# Push all context
for slug, cat in categories.items(): ctx("category", slug, 1, cat)
for mid, m in merchants.items(): ctx("merchant", mid, 1, m)
for cid, c in customers.items(): ctx("customer", cid, 1, c)
for tid, t in triggers.items(): ctx("trigger", tid, 1, t)

trg_ids = list(triggers.keys())[:5]
d,e = tick(trg_ids)
check("tick 200", e is None, e)
actions = d.get("actions",[]) if d else []
check("tick returns actions", len(actions)>0, f"got {len(actions)}")

for a in actions:
    body = a.get("body","")
    check(f"body ≤320 chars [{a.get('trigger_id','?')[:30]}]", len(body)<=320, f"{len(body)}c")
    check(f"no URL in body", not re.search(r"https?://",body), body[:60])
    check(f"body non-empty", len(body)>10, f"'{body[:40]}'")
    check(f"valid cta", a.get("cta") in ("binary_yes_stop","open_ended","none"),
          a.get("cta"))
    check(f"valid send_as", a.get("send_as") in ("vera","merchant_on_behalf"),
          a.get("send_as"))

# Print sample messages
print(f"\n  {INFO} Sample composed messages:")
for a in actions[:3]:
    print(f"    [{a.get('trigger_id','?')[:35]}]")
    print(f"    → {a.get('body','')[:140]}")
    print(f"    rationale: {a.get('rationale','')}")
    print()

# ═════════════════════════════════════════════════════════════════════════════
# T4: Suppression key dedup
# ═════════════════════════════════════════════════════════════════════════════
section("T4 · SUPPRESSION KEY DEDUP")

# Second tick with same triggers → all suppressed
d2,_ = tick(trg_ids)
actions2 = d2.get("actions",[]) if d2 else []
check("second tick deduped", len(actions2)==0 or all(
    a.get("suppression_key","") not in {a2.get("suppression_key","") for a2 in actions}
    for a2 in actions2
), f"got {len(actions2)} repeat actions")

# ═════════════════════════════════════════════════════════════════════════════
# T5: Auto-reply detection
# ═════════════════════════════════════════════════════════════════════════════
section("T5 · AUTO-REPLY DETECTION")
teardown()
for slug,cat in categories.items(): ctx("category",slug,1,cat)
mid = list(merchants.keys())[0]
ctx("merchant", mid, 1, merchants[mid])

AUTO_MSGS = [
    "Thank you for contacting us! Our team will respond shortly.",
    "Aapki jaankari ke liye shukriya. Hamari team aapko contact karegi.",
    "This is an automated response. We will get back to you.",
    "Thank you for reaching out. Our team will reply soon.",
]
for i, amsg in enumerate(AUTO_MSGS):
    conv_id = f"conv_auto_test_{i}"
    r1,_ = reply(conv_id, mid, amsg, 2)
    r2,_ = reply(conv_id, mid, amsg, 3)
    ended = r1.get("action")=="end" or r2.get("action")=="end"
    check(f"auto-reply ends: '{amsg[:40]}'", ended,
          f"r1={r1.get('action')} r2={r2.get('action')}")

# ═════════════════════════════════════════════════════════════════════════════
# T6: Intent — STOP handling
# ═════════════════════════════════════════════════════════════════════════════
section("T6 · STOP / HOSTILE")
teardown()
for slug,cat in categories.items(): ctx("category",slug,1,cat)
mid = list(merchants.keys())[0]; ctx("merchant",mid,1,merchants[mid])

STOP_MSGS = [
    "Stop messaging me. This is spam.",
    "Nahi chahiye. Band karo.",
    "Not interested. Remove me.",
    "unsubscribe please",
]
for smsg in STOP_MSGS:
    r,_ = reply(f"conv_stop_{uuid.uuid4().hex[:4]}", mid, smsg, 2)
    check(f"STOP handled: '{smsg}'", r and r.get("action")=="end",
          f"action={r.get('action') if r else 'no response'}")

# ═════════════════════════════════════════════════════════════════════════════
# T7: Intent — ACCEPT jumps to action (no more qualifying)
# ═════════════════════════════════════════════════════════════════════════════
section("T7 · ACCEPT → ACTION (no qualifier loop)")
teardown()
for slug,cat in categories.items(): ctx("category",slug,1,cat)
mid = list(merchants.keys())[0]; ctx("merchant",mid,1,merchants[mid])

ACCEPT_MSGS = [
    "Ok lets do it. Whats next?",
    "Yes, please go ahead",
    "Haan karo, chalte hain",
    "Sure confirm kar do",
]
QUALIFYING = ["would you","do you","can you tell","what if","how about","please share","could you","kya aap"]
ACTION_WORDS = ["here","done","sending","draft","confirm","proceed","next","kiya","shuru","creating","added","scheduled"]

for amsg in ACCEPT_MSGS:
    r,_ = reply(f"conv_accept_{uuid.uuid4().hex[:4]}", mid, amsg, 2)
    body = (r.get("body","") if r else "").lower()
    action = r.get("action","") if r else ""
    still_qualifying = any(q in body for q in QUALIFYING) and not any(a in body for a in ACTION_WORDS)
    ok = action in ("send","end") and not still_qualifying
    check(f"accept→action: '{amsg}'", ok,
          f"action={action} body='{body[:60]}'")

# ═════════════════════════════════════════════════════════════════════════════
# T8: Adaptive context injection — new merchant version mid-conversation
# ═════════════════════════════════════════════════════════════════════════════
section("T8 · ADAPTIVE CONTEXT INJECTION")
teardown()
for slug,cat in categories.items(): ctx("category",slug,1,cat)
mid = list(merchants.keys())[0]
m_orig = dict(merchants[mid])
ctx("merchant", mid, 1, m_orig)

# Find or create a trigger that matches this merchant
matching_trg = next(
    (t for t in triggers.values() if t.get("merchant_id")==mid), None
)
if not matching_trg:
    matching_trg = {"id":f"trg_t8_{uuid.uuid4().hex[:6]}","kind":"perf_dip",
        "scope":"merchant","source":"internal","merchant_id":mid,"urgency":3,
        "suppression_key":f"t8_{uuid.uuid4().hex[:4]}","payload":{},
        "expires_at":"2099-01-01T00:00:00Z"}
t8_tid = matching_trg["id"]
ctx("trigger",t8_tid,1,matching_trg)
d,_ = tick([t8_tid]); actions_v1 = d.get("actions",[]) if d else []
check("tick produced action", len(actions_v1)>0, f"{len(actions_v1)} actions")

# Inject updated merchant v2
m_updated = dict(m_orig)
m_updated["offers"] = [{"id":"o_new_001","title":"BRAND NEW OFFER @ ₹149","status":"active","started":"2026-05-03"}]
m_updated["performance"] = dict(m_orig.get("performance",{}))
m_updated["performance"]["views"] = 9999
d2,_ = ctx("merchant", mid, 2, m_updated)
check("v2 context accepted", d2 and d2.get("accepted"), str(d2))

# Tick with a fresh trigger for same merchant
t8b_id = f"trg_t8b_{uuid.uuid4().hex[:6]}"
ctx("trigger",t8b_id,1,{"id":t8b_id,"kind":"perf_spike","scope":"merchant","source":"internal",
    "merchant_id":mid,"urgency":4,"suppression_key":f"t8b_{uuid.uuid4().hex[:4]}",
    "payload":{"views_jump_pct":0.80},"expires_at":"2099-01-01T00:00:00Z"})
d3,_ = tick([t8b_id]); actions_v2 = d3.get("actions",[]) if d3 else []
check("tick after v2 context works", len(actions_v2)>0, f"{len(actions_v2)} actions")

# ═════════════════════════════════════════════════════════════════════════════
# T9: Unknown trigger kind (judge injects fresh kind)
# ═════════════════════════════════════════════════════════════════════════════
section("T9 · UNKNOWN / FRESH TRIGGER KIND")
teardown()
for slug,cat in categories.items(): ctx("category",slug,1,cat)
mid = list(merchants.keys())[0]; ctx("merchant",mid,1,merchants[mid])

# Inject novel trigger kinds not in training data
novel_triggers = [
    {"id":"trg_novel_1","kind":"google_review_spike","scope":"merchant","source":"google_api",
     "merchant_id":mid,"urgency":4,"suppression_key":"novel_1_supp",
     "payload":{"review_count_7d":12,"avg_rating_7d":4.8,"top_keyword":"painless"},
     "expires_at":"2099-01-01T00:00:00Z"},
    {"id":"trg_novel_2","kind":"competitor_price_drop","scope":"merchant","source":"scraper",
     "merchant_id":mid,"urgency":3,"suppression_key":"novel_2_supp",
     "payload":{"competitor_name":"Nearby Clinic","service":"RCT","old_price":5000,"new_price":3500,"distance_km":0.8},
     "expires_at":"2099-01-01T00:00:00Z"},
    {"id":"trg_novel_3","kind":"magicpin_campaign_eligible","scope":"merchant","source":"internal",
     "merchant_id":mid,"urgency":5,"suppression_key":"novel_3_supp",
     "payload":{"campaign":"Dussehra Boost","bonus_views":5000,"deadline":"2026-10-02","slots_left":3},
     "expires_at":"2099-01-01T00:00:00Z"},
]
for t in novel_triggers:
    ctx("trigger",t["id"],1,t)

d,_ = tick([t["id"] for t in novel_triggers])
actions = d.get("actions",[]) if d else []
check("novel triggers produce actions", len(actions)>0, f"{len(actions)} actions")
for a in actions:
    body=a.get("body","")
    check(f"novel[{a.get('trigger_id','?')[-15:]}] body valid",
          10 < len(body) <= 320 and not re.search(r"https?://",body), f"'{body[:80]}'")

print(f"\n  {INFO} Novel trigger responses:")
for a in actions:
    print(f"    [{a.get('trigger_id')}]")
    print(f"    → {a.get('body','')[:150]}")

# ═════════════════════════════════════════════════════════════════════════════
# T10: Customer-facing (send_as=merchant_on_behalf)
# ═════════════════════════════════════════════════════════════════════════════
section("T10 · CUSTOMER CONTEXT (merchant_on_behalf)")
teardown()
for slug,cat in categories.items(): ctx("category",slug,1,cat)

# Find a trigger with customer_id
cust_trigger = next(
    (t for t in triggers.values() if t.get("customer_id")), None
)
if cust_trigger:
    mid=cust_trigger["merchant_id"]; cid=cust_trigger["customer_id"]
    ctx("merchant",mid,1,merchants.get(mid,{}))
    ctx("customer",cid,1,customers.get(cid,{}))
    ctx("trigger",cust_trigger["id"],1,cust_trigger)
    d,_ = tick([cust_trigger["id"]])
    actions = d.get("actions",[]) if d else []
    check("customer trigger fires", len(actions)>0, f"{len(actions)}")
    for a in actions:
        check("send_as=merchant_on_behalf", a.get("send_as")=="merchant_on_behalf",
              a.get("send_as"))
        print(f"\n  {INFO} Customer msg: {a.get('body','')[:150]}")
else:
    print(f"  {WARN} No customer triggers in dataset — injecting synthetic")
    mid=list(merchants.keys())[0]; cid=list(customers.keys())[0]
    ctx("merchant",mid,1,merchants[mid])
    ctx("customer",cid,1,customers[cid])
    synth = {"id":"trg_cust_synth","kind":"recall_due","scope":"customer","source":"crm",
             "merchant_id":mid,"customer_id":cid,"urgency":3,"suppression_key":"cust_synth_key",
             "payload":{"service_due":"cleaning","available_slots":[{"label":"Wed 5pm"},{"label":"Thu 6pm"}]},
             "expires_at":"2099-01-01T00:00:00Z"}
    ctx("trigger","trg_cust_synth",1,synth)
    d,_ = tick(["trg_cust_synth"]); actions=d.get("actions",[]) if d else []
    check("synthetic customer trigger", len(actions)>0)
    for a in actions:
        check("send_as correct", a.get("send_as")=="merchant_on_behalf", a.get("send_as"))
        print(f"\n  {INFO} Customer msg: {a.get('body','')[:150]}")

# ═════════════════════════════════════════════════════════════════════════════
# T11: Expiry — expired triggers not sent
# ═════════════════════════════════════════════════════════════════════════════
section("T11 · TRIGGER EXPIRY")
teardown()
for slug,cat in categories.items(): ctx("category",slug,1,cat)
mid=list(merchants.keys())[0]; ctx("merchant",mid,1,merchants[mid])

expired = {"id":"trg_expired","kind":"perf_spike","scope":"merchant","source":"internal",
           "merchant_id":mid,"urgency":5,"suppression_key":"expired_key",
           "payload":{},"expires_at":"2020-01-01T00:00:00Z"}
ctx("trigger","trg_expired",1,expired)
d,_ = tick(["trg_expired"]); actions=d.get("actions",[]) if d else []
check("expired trigger not sent", len(actions)==0, f"got {len(actions)} actions")

# ═════════════════════════════════════════════════════════════════════════════
# T12: Multi-language (Hindi-English code-mix)
# ═════════════════════════════════════════════════════════════════════════════
section("T12 · HINDI-ENGLISH LANGUAGE MIX")
teardown()
for slug,cat in categories.items(): ctx("category",slug,1,cat)

# Find Hindi merchant
hi_mid = next((mid for mid,m in merchants.items()
               if "hi" in m.get("identity",{}).get("languages",[])), None)
if hi_mid:
    ctx("merchant",hi_mid,1,merchants[hi_mid])
    tid = next(iter(t for t in triggers.values() if t.get("merchant_id")==hi_mid), None)
    if not tid:
        tid_id = f"trg_hi_{uuid.uuid4().hex[:4]}"
        ctx("trigger",tid_id,1,{"id":tid_id,"kind":"perf_dip","scope":"merchant","source":"internal",
            "merchant_id":hi_mid,"urgency":3,"suppression_key":f"hi_{uuid.uuid4().hex[:4]}",
            "payload":{},"expires_at":"2099-01-01T00:00:00Z"})
        d,_ = tick([tid_id])
    else:
        ctx("trigger",tid.get("id"),1,tid)
        d,_ = tick([tid.get("id")])
    actions=d.get("actions",[]) if d else []
    check("Hindi merchant produces message", len(actions)>0)
    for a in actions:
        body=a.get("body","")
        print(f"\n  {INFO} Hindi-mix: {body[:150]}")
        # Soft check: presence of common Hindi words
        hi_words = ["aap","karo","hai","mein","ka","ki","ke","ko","se","nahi","hain","dekhna","chahenge","aapka","karta","hoon","kijiye","bataiye","abhi","kuch","yeh","woh"]
        has_mix = any(w in body.lower() for w in hi_words)
        check("Hindi code-mix present", has_mix, f"body: {body[:60]}")
else:
    print(f"  {WARN} No Hindi merchant found in dataset")

# ═════════════════════════════════════════════════════════════════════════════
# T13: Multi-turn conversation
# ═════════════════════════════════════════════════════════════════════════════
section("T13 · MULTI-TURN CONVERSATION")
teardown()
for slug,cat in categories.items(): ctx("category",slug,1,cat)
mid=list(merchants.keys())[0]; ctx("merchant",mid,1,merchants[mid])

# Synthetic trigger for conversation
tid_conv="trg_conv_test"
ctx("trigger",tid_conv,1,{"id":tid_conv,"kind":"perf_dip","scope":"merchant","source":"internal",
    "merchant_id":mid,"urgency":3,"suppression_key":"conv_test_key",
    "payload":{"views_drop_pct":0.35,"calls_drop_pct":0.50},"expires_at":"2099-01-01T00:00:00Z"})

d,_ = tick([tid_conv]); actions=d.get("actions",[]) if d else []
check("initial message sent", len(actions)>0)

if actions:
    conv_id=actions[0]["conversation_id"]

    turns = [
        ("Interesting, tell me more.", "neutral → continue"),
        ("Ok yes let's do it!", "accept → action"),
    ]
    for i,(msg,label) in enumerate(turns):
        r,_ = reply(conv_id,mid,msg,i+2)
        action=r.get("action","") if r else ""
        body=r.get("body","") if r else ""
        check(f"turn {i+2} [{label}]",
              action in ("send","wait","end"), f"action={action}")
        if action=="send":
            check(f"  reply body ≤320", len(body)<=320, f"{len(body)}c")
        print(f"    [{label}] action={action} → '{body[:80]}'")

# ═════════════════════════════════════════════════════════════════════════════
# T14: Off-topic redirect
# ═════════════════════════════════════════════════════════════════════════════
section("T14 · OFF-TOPIC REDIRECT")
teardown()
mid=list(merchants.keys())[0]; ctx("merchant",mid,1,merchants[mid])

OT_MSGS=["How do I get GST invoice?","My password is not working","This is unrelated to you"]
for msg in OT_MSGS:
    r,_ = reply(f"conv_ot_{uuid.uuid4().hex[:4]}", mid, msg, 2)
    action=r.get("action","") if r else ""
    check(f"off-topic handled: '{msg[:40]}'",
          action in ("send","end"), f"action={action}")

# ═════════════════════════════════════════════════════════════════════════════
# T15: Teardown wipes state
# ═════════════════════════════════════════════════════════════════════════════
section("T15 · TEARDOWN")
r,_ = POST("/v1/teardown",{})
check("teardown 200", r and r.get("wiped"), str(r))

d,_ = GET("/v1/healthz")
counts=d.get("contexts_loaded",{}) if d else {}
total=sum(counts.values())
check("state wiped after teardown", total==0, f"remaining: {counts}")

# ═════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═════════════════════════════════════════════════════════════════════════════
print(f"\n{B}{C}{'═'*60}{X}")
print(f"{B}{C}  RESULTS{X}")
print(f"{B}{C}{'═'*60}{X}")
passed=[n for n,ok in results if ok]
failed=[n for n,ok in results if not ok]
pct=len(passed)/len(results)*100 if results else 0
print(f"\n  Total:  {len(results)}")
print(f"  {G}Passed: {len(passed)}{X}")
print(f"  {R}Failed: {len(failed)}{X}")
print(f"  Score:  {pct:.0f}%\n")
if failed:
    print(f"  {R}Failing tests:{X}")
    for n in failed: print(f"    • {n}")
print()
sys.exit(0 if not failed else 1)
