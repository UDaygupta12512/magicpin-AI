"""
Vera — magicpin Merchant AI Assistant  (v2.0)
All 5 endpoints + Claude-powered context-grounded composition.
"""

import os, time, uuid, json, re, logging
from datetime import datetime, timezone
from typing import Any, Optional
from fastapi import FastAPI
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import anthropic

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vera")

TEAM_NAME    = "Vera Challenger"
TEAM_MEMBERS = ["Uday"]
MODEL        = "claude-sonnet-4-5"
EMAIL        = "uday@example.com"
VERSION      = "3.0.0"
SUBMITTED_AT = "2026-05-03T23:45:00Z"

START_TIME       = time.time()
contexts: dict   = {}   # (scope, ctx_id) -> {version, payload}
conversations: dict = {}
fired_keys: set  = set()

app     = FastAPI(title="Vera v2")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")

_claude = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

def _now(): return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
def _payload(scope, cid):
    e = contexts.get((scope, cid))
    return e["payload"] if e else None
def _merchant_category(m):
    return _payload("category", m.get("category_slug",""))
def _clean(body):
    body = re.sub(r"https?://\S+","",body).strip()
    return body[:320] if len(body)>320 else body

_AUTO_PATTERNS = [
    r"thank you for contacting",r"aapki jaankari ke liye",r"shukriya.*pahuncha",
    r"automated (assistant|reply|response|message)",r"main ek automated",
    r"i am an? automated",r"this is an? auto",r"out of office",
    r"will get back to you",r"our team will (respond|reply|contact)",
    r"we will respond",r"madad ke liye shukriya.*automated",
]
def _is_auto(msg): return any(re.search(p,msg.lower()) for p in _AUTO_PATTERNS)

def _intent(msg):
    m = msg.lower().strip()
    # stop first — highest priority
    if re.search(r"\b(stop|nahi|na\b|no\b|not interested|band karo|mat karo|unsubscribe|remove|spam|useless|irritat|annoying|do not|don't)\b",m): return "stop"
    # off_topic before accept — catches "password reset karo" before karo matches accept
    if re.search(r"\b(gst|tax|invoice|billing|account|password|refund|complaint)\b",m): return "off_topic"
    # join
    if re.search(r"\b(join|judna|judrna|register|sign up|enroll|add me|mujhe add)\b",m): return "join"
    # greet — match full-message greetings incl "hello there"
    if re.search(r"^\s*(hi|hello|hey|namaste|helo|hii)(\s+(there|bhai|ji|sir|madam))?\s*[!.,]?\s*$",m): return "greet"
    # accept — require meaningful commitment word, not just "ok noted" style
    if re.search(r"\b(yes|haan|ha\b|okay|sure|go ahead|let'?s do it|chalte hain|kar do|proceed|confirm|confirmed|agree|great|perfect|sounds good|whats next|what'?s next|bilkul|zaroor)\b",m): return "accept"
    # "ok" alone or "ok karo" → accept; "ok noted"/"ok i see" → neutral
    if re.search(r"\bok\b",m) and re.search(r"\b(karo|do it|proceed|start|chalte|let'?s)\b",m): return "accept"
    return "neutral"

def _resolve_digest(trigger, category):
    tid = trigger.get("payload",{}).get("top_item_id")
    if not tid: return {}
    return next((d for d in category.get("digest",[]) if d.get("id")==tid), {})

COMPOSE_SYSTEM = """\
You are Vera, magicpin's AI merchant assistant composing WhatsApp messages.

CARDINAL RULES:
1. Use ONLY facts in the CONTEXT block. Never invent numbers, citations, competitor names, offers.
2. Body ≤ 320 chars (aim 150-200). Count carefully.
3. No URLs in body.
4. One CTA at the very end: "Reply YES / STOP" for action triggers; open question for info; none for pure sharing.
5. No opener like "I hope you're well". No self-intro after first message.
6. Prefer service+price ("Dental Cleaning @ ₹299") over generic discounts ("Flat 30% off").
7. One compulsion lever per message: specificity | loss_aversion | social_proof | curiosity | effort_externalization.

VOICE BY CATEGORY:
- dentists: peer/clinical, use "Dr." prefix, cite source (JIDA/DCI/IDA), technical vocab OK
- salons: warm/practical, owner first name, code-mix OK  
- restaurants: operator-to-operator, margin/covers language
- gyms: coaching tone, member outcomes, motivational
- pharmacies: caring/precise, compliance-safe

LANGUAGE: if languages includes "hi" → natural Hindi-English code-mix

ADAPTIVE: If trigger kind is unknown, reason from its payload: what happened? who is affected? what should merchant do?

OUTPUT: Return ONLY valid JSON (no markdown fences):
{
  "body": "<message ≤320 chars>",
  "cta": "binary_yes_stop"|"open_ended"|"none",
  "send_as": "vera"|"merchant_on_behalf",
  "suppression_key": "<from trigger>",
  "rationale": "<≤25 words: lever + why now>"
}"""

def _build_prompt(category, merchant, trigger, customer=None, conv_turns=None):
    idn   = merchant.get("identity",{})
    perf  = merchant.get("performance",{})
    d7    = perf.get("delta_7d",{})
    sub   = merchant.get("subscription",{})
    ca    = merchant.get("customer_aggregate",{})
    sigs  = merchant.get("signals",[])
    offers= merchant.get("offers",[])
    revs  = merchant.get("review_themes",[])
    hist  = merchant.get("conversation_history",[])
    v     = category.get("voice",{})
    ps    = category.get("peer_stats",{})
    digest_ref = _resolve_digest(trigger, category)

    a_off = [o["title"] for o in offers if o.get("status")=="active"]
    e_off = [o["title"] for o in offers if o.get("status")=="expired"]
    peer_ctr = ps.get("avg_ctr",0); my_ctr = perf.get("ctr",0)
    ctr_note = f"{'BELOW' if my_ctr<peer_ctr else 'ABOVE'} peer by {abs(my_ctr-peer_ctr)*100:.1f}pts"

    lines = ["=== CONTEXT (use only these facts) ==="]
    lines.append(f"""
CATEGORY: {category.get('slug','')}
Voice: {v.get('tone','')} | Code-mix: {v.get('code_mix','')}
Allowed vocab: {v.get('vocab_allowed',[])}
TABOOS (never use): {v.get('vocab_taboo',[])}
Salutation style: {v.get('salutation_examples',[])}
Peer stats ({ps.get('scope','')}): avg_ctr={ps.get('avg_ctr',0):.3f} avg_rating={ps.get('avg_rating','?')} avg_reviews={ps.get('avg_review_count','?')} avg_views={ps.get('avg_views_30d','?')} avg_calls={ps.get('avg_calls_30d','?')}
Seasonal beats: {[f"{s['month_range']}: {s['note']}" for s in category.get('seasonal_beats',[])]}
Trend signals: {[f"{t.get('query')} {'+' if t.get('delta_yoy',0)>0 else ''}{int(t.get('delta_yoy',0)*100)}% YoY" for t in category.get('trend_signals',[])]}
Offer catalog templates: {[o.get('title') for o in category.get('offer_catalog',[])[:6]]}""")

    if digest_ref:
        lines.append(f"""
DIGEST ITEM (referenced by trigger — anchor your message on this):
  title: {digest_ref.get('title','')}
  source: {digest_ref.get('source','')}
  trial_n: {digest_ref.get('trial_n','')}
  patient_segment: {digest_ref.get('patient_segment','')}
  summary: {digest_ref.get('summary','')}
  actionable: {digest_ref.get('actionable','')}
  date: {digest_ref.get('date','')}""")
    elif category.get("digest"):
        lines.append("\nALL DIGEST ITEMS (pick most relevant):")
        for d in category.get("digest",[])[:6]:
            lines.append(f"  [{d.get('kind','')}] {d.get('title','')} — {d.get('source','')} actionable: {d.get('actionable','')}")

    lines.append(f"""
MERCHANT:
  name={idn.get('name','')} owner={idn.get('owner_first_name','')} locality={idn.get('locality','')} city={idn.get('city','')}
  verified={idn.get('verified',False)} languages={idn.get('languages',['en'])}
  subscription: {sub.get('status','')} {sub.get('plan','')} days_remaining={sub.get('days_remaining','?')}
  performance 30d: views={perf.get('views','?')} calls={perf.get('calls','?')} directions={perf.get('directions','?')} ctr={perf.get('ctr','?')} ({ctr_note}) leads={perf.get('leads','?')}
  7d delta: views {'+' if d7.get('views_pct',0)>=0 else ''}{d7.get('views_pct',0)*100:.0f}% calls {'+' if d7.get('calls_pct',0)>=0 else ''}{d7.get('calls_pct',0)*100:.0f}%
  active offers: {a_off if a_off else 'NONE'}
  expired offers: {e_off}
  signals: {sigs}
  customer_agg: total_ytd={ca.get('total_unique_ytd','?')} lapsed_180d={ca.get('lapsed_180d_plus','?')} retention_6mo={ca.get('retention_6mo_pct','?')} high_risk_adults={ca.get('high_risk_adult_count','?')}
  review_themes: {[f"{r['theme']}({r['sentiment']},{r.get('occurrences_30d','?')}x)" for r in revs]}
  last vera convo: {[f"[{t['from'].upper()}] {t['body']}" for t in (hist or [])[-2:]]}""")

    lines.append(f"""
TRIGGER:
  kind={trigger.get('kind','')} scope={trigger.get('scope','')} source={trigger.get('source','')} urgency={trigger.get('urgency','?')}/5
  suppression_key={trigger.get('suppression_key','')} expires={trigger.get('expires_at','')}
  payload={json.dumps(trigger.get('payload',{}), ensure_ascii=False)}""")

    if customer:
        cid=customer.get("identity",{}); rel=customer.get("relationship",{}); pref=customer.get("preferences",{}); con=customer.get("consent",{})
        lines.append(f"""
CUSTOMER (compose on behalf of merchant):
  name={cid.get('name','')} language_pref={cid.get('language_pref','')} state={customer.get('state','')}
  visits={rel.get('visits_total','?')} last_visit={rel.get('last_visit','')}\n  services_received={rel.get('services_received',[])} avg_spend={rel.get('avg_spend_per_visit','?')}
  preferred_slots={pref.get('preferred_slots','')} consent_scope={con.get('scope',[])}
send_as must be "merchant_on_behalf" """)
    else:
        lines.append('\nsend_as must be "vera"')

    if conv_turns:
        lines.append("\nCONVERSATION SO FAR (do not repeat):")
        for t in conv_turns[-4:]: lines.append(f"  [{t['from'].upper()}] {t['body']}")
        lines.append("Write the NEXT message only.")

    lines.append(f"\n=== TASK ===\nCompose for trigger kind='{trigger.get('kind','')}' urgency={trigger.get('urgency','?')}/5.")
    lines.append("Extract the most specific verifiable facts from CONTEXT. Use one compulsion lever. End with CTA.")
    lines.append("Output ONLY valid JSON.")
    return "\n".join(lines)

def _compose(category, merchant, trigger, customer=None, conv_turns=None):
    prompt = _build_prompt(category, merchant, trigger, customer, conv_turns)
    fb_name = merchant.get("identity",{}).get("owner_first_name","") or merchant.get("identity",{}).get("name","")
    try:
        resp = _claude.messages.create(model=MODEL, max_tokens=512, temperature=0,
            system=COMPOSE_SYSTEM, messages=[{"role":"user","content":prompt}])
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```json\s*","",raw); raw = re.sub(r"\s*```$","",raw)
        r = json.loads(raw); r["body"] = _clean(r.get("body",""))
        return r
    except Exception as e:
        log.warning(f"compose error: {e}")
        return {"body":f"{fb_name}, profile update ready — dekhna chahenge? Reply YES/STOP",
                "cta":"binary_yes_stop",
                "send_as":"merchant_on_behalf" if customer else "vera",
                "suppression_key":trigger.get("suppression_key",f"fb:{uuid.uuid4().hex[:6]}"),
                "rationale":f"Fallback: {str(e)[:60]}"}

REPLY_SYSTEM = """\
You are Vera responding to a merchant's WhatsApp reply. Be concise and specific.

Rules:
• Body ≤ 320 chars. No URLs.
• intent=accept/join → IMMEDIATELY move to action. Give concrete next step. NO more qualifying questions.
• intent=stop → {"action":"end","rationale":"merchant stopped"}
• auto_reply detected twice → {"action":"end","rationale":"auto-reply loop"}
• auto_reply first time → try different hook, {"action":"send",...}
• intent=off_topic → briefly acknowledge, redirect to profile/business growth  
• intent=greet → warm brief response, ask what they need
• intent=neutral → advance conversation using trigger context

Output ONLY: {"action":"send"|"wait"|"end","body":"<if send, ≤320 chars>","cta":"binary_yes_stop"|"open_ended"|"none","rationale":"<≤15 words>"}
No markdown."""

def _compose_reply(conv, msg, merchant, category, trigger, customer):
    intent   = _intent(msg)
    is_auto  = _is_auto(msg)
    auto_cnt = conv.get("auto_reply_count",0)
    if is_auto: conv["auto_reply_count"] = auto_cnt+1
    if is_auto and auto_cnt>=1: return {"action":"end","rationale":"Two auto-replies; graceful exit"}
    if intent=="stop": return {"action":"end","rationale":"Merchant signalled stop"}

    idn=merchant.get("identity",{}); owner=idn.get("owner_first_name",idn.get("name",""))
    a_off=[o["title"] for o in merchant.get("offers",[]) if o.get("status")=="active"]
    digest_ref = _resolve_digest(trigger, category)

    parts=[f'MERCHANT SAID: "{msg}"',f"INTENT: {intent}",f"AUTO_REPLY: {is_auto} (count={conv['auto_reply_count']})",
           f"TURN: {conv.get('turn',2)}",f"OWNER: {owner} LANGS: {idn.get('languages',['en'])}",
           f"CATEGORY: {category.get('slug','')} VOICE: {category.get('voice',{}).get('tone','')}",
           f"TRIGGER KIND: {trigger.get('kind','')} PAYLOAD: {json.dumps(trigger.get('payload',{}))}",
           f"ACTIVE OFFERS: {a_off}",f"PERF: views={merchant.get('performance',{}).get('views','?')} ctr={merchant.get('performance',{}).get('ctr','?')}"]
    if digest_ref: parts.append(f"DIGEST REF: {digest_ref.get('title','')} ({digest_ref.get('source','')})")
    if conv.get("turns"): parts.append("RECENT:\n"+"\n".join(f"  [{t['from'].upper()}] {t['body']}" for t in conv["turns"][-4:]))
    if intent in ("accept","join"): parts.append("\n⚡ CRITICAL: Merchant committed. Jump to action NOW. Concrete next step. No more questions.")
    if is_auto and conv["auto_reply_count"]==1: parts.append("\n⚠ First auto-reply. Try one different hook. Be brief.")
    if intent=="off_topic": parts.append("\n↩ Off-topic. Acknowledge briefly, redirect to magicpin/profile help.")
    parts.append("Output ONLY valid JSON.")

    try:
        resp = _claude.messages.create(model=MODEL, max_tokens=300, temperature=0,
            system=REPLY_SYSTEM, messages=[{"role":"user","content":"\n".join(parts)}])
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```json\s*","",raw); raw = re.sub(r"\s*```$","",raw)
        r = json.loads(raw)
        if r.get("action")=="send": r["body"]=_clean(r.get("body",""))
        return r
    except Exception as e:
        log.warning(f"reply error: {e}")
        return {"action":"send","body":"Samajh gaya — abhi kaam karta hoon.","cta":"none","rationale":f"Fallback: {str(e)[:40]}"}

# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/v1/healthz")
async def healthz():
    counts={"category":0,"merchant":0,"customer":0,"trigger":0}
    for (s,_) in contexts:
        if s in counts: counts[s]+=1
    return {"status":"ok","uptime_seconds":int(time.time()-START_TIME),"contexts_loaded":counts}

@app.get("/v1/metadata")
async def metadata():
    return {"team_name":TEAM_NAME,"team_members":TEAM_MEMBERS,"model":MODEL,
            "approach":"Context-grounded Claude composer: extracts all available facts from 4-context payload; handles any trigger kind via first-principles LLM reasoning; auto-reply detection (12 patterns EN+HI); intent routing (accept/stop/join/hostile/off-topic); version-aware adaptive context; anti-hallucination.",
            "contact_email":EMAIL,"version":VERSION,"submitted_at":SUBMITTED_AT}

class CtxBody(BaseModel):
    scope: str; context_id: str; version: int; payload: dict[str,Any]; delivered_at: str

@app.post("/v1/context")
async def push_context(body: CtxBody):
    if body.scope not in {"category","merchant","customer","trigger"}:
        return JSONResponse(status_code=400, content={"accepted":False,"reason":"invalid_scope"})
    key=(body.scope,body.context_id); cur=contexts.get(key)
    if cur and cur["version"]>=body.version:
        return JSONResponse(status_code=409, content={"accepted":False,"reason":"stale_version","current_version":cur["version"]})
    contexts[key]={"version":body.version,"payload":body.payload}
    log.info(f"stored {body.scope}/{body.context_id} v{body.version}")
    return {"accepted":True,"ack_id":f"ack_{body.context_id}_v{body.version}_{uuid.uuid4().hex[:4]}","stored_at":_now()}

class TickBody(BaseModel):
    now: str; available_triggers: list[str]=[]

@app.post("/v1/tick")
async def tick(body: TickBody):
    actions=[]
    for trg_id in body.available_triggers:
        if len(actions)>=20: break
        trg=_payload("trigger",trg_id)
        if not trg: continue
        sk=trg.get("suppression_key","")
        if sk and sk in fired_keys: continue
        if trg.get("expires_at","9")<body.now: continue
        mid=trg.get("merchant_id"); cid=trg.get("customer_id")
        merchant=_payload("merchant",mid)
        if not merchant: continue
        category=_merchant_category(merchant)
        if not category: continue
        customer=_payload("customer",cid) if cid else None
        composed=_compose(category,merchant,trg,customer)
        if not composed.get("body"): continue
        if sk: fired_keys.add(sk)
        conv_id=f"conv_{mid}_{trg_id}_{uuid.uuid4().hex[:6]}"
        conversations[conv_id]={"merchant_id":mid,"customer_id":cid,"trigger_id":trg_id,
            "turns":[{"from":"vera","body":composed["body"]}],"auto_reply_count":0,"turn":1}
        m_name=merchant.get("identity",{}).get("name","Merchant")
        actions.append({"conversation_id":conv_id,"merchant_id":mid,"customer_id":cid,
            "send_as":composed.get("send_as","vera"),"trigger_id":trg_id,
            "template_name":f"vera_{trg.get('kind','generic')}_v1",
            "template_params":[m_name,trg.get("kind",""),composed["body"][:80]],
            "body":composed["body"],"cta":composed.get("cta","open_ended"),
            "suppression_key":sk,"rationale":composed.get("rationale","")})
        log.info(f"action: {conv_id} [{trg.get('kind')}] {len(composed['body'])}c")
    return {"actions":actions}

class ReplyBody(BaseModel):
    conversation_id: str; merchant_id: Optional[str]=None; customer_id: Optional[str]=None
    from_role: str; message: str; received_at: str; turn_number: int

@app.post("/v1/reply")
async def reply_handler(body: ReplyBody):
    conv=conversations.get(body.conversation_id)
    if not conv:
        conv={"merchant_id":body.merchant_id,"customer_id":body.customer_id,"trigger_id":None,
              "turns":[],"auto_reply_count":0,"turn":1}
        conversations[body.conversation_id]=conv
    conv["turns"].append({"from":body.from_role,"body":body.message})
    conv["turn"]=body.turn_number
    mid=conv.get("merchant_id") or body.merchant_id
    cid=conv.get("customer_id") or body.customer_id
    tid=conv.get("trigger_id")
    merchant=_payload("merchant",mid) if mid else {}
    category=(_merchant_category(merchant) if merchant else {}) or {}
    trigger=_payload("trigger",tid) if tid else {}
    customer=_payload("customer",cid) if cid else None
    if not merchant: merchant={"identity":{"name":"Merchant","languages":["en"]},"performance":{},"offers":[],"signals":[]}
    if not category: category={"slug":"general","voice":{"tone":"friendly"},"peer_stats":{},"digest":[],"seasonal_beats":[],"trend_signals":[],"offer_catalog":[]}
    if not trigger: trigger={"kind":"follow_up","suppression_key":"","payload":{},"urgency":2}
    result=_compose_reply(conv,body.message,merchant,category,trigger,customer)
    if result.get("action")=="send" and result.get("body"):
        conv["turns"].append({"from":"vera","body":result["body"]})
    log.info(f"reply {body.conversation_id} t={body.turn_number} intent={_intent(body.message)} → {result.get('action')}")
    return result

@app.get("/v1/debug/contexts")
async def get_all_contexts():
    return {
        "categories": [k[1] for k in contexts if k[0] == "category"],
        "merchants": [k[1] for k in contexts if k[0] == "merchant"],
        "customers": [k[1] for k in contexts if k[0] == "customer"],
        "triggers": [k[1] for k in contexts if k[0] == "trigger"],
        "fired_keys": list(fired_keys)
    }

@app.post("/v1/teardown")
async def teardown():
    contexts.clear(); conversations.clear(); fired_keys.clear()
    return {"wiped":True,"at":_now()}
