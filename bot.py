"""
Vera — magicpin Merchant AI Assistant  (v3.2)
All 5 endpoints + Multi-LLM Support (Claude/Gemini) + Smart Mock Mode.
"""

import os, time, uuid, json, re, logging
from datetime import datetime, timezone
from typing import Any, Optional
from fastapi import FastAPI
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import anthropic
try:
    import google.generativeai as genai
except ImportError:
    genai = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vera")

TEAM_NAME    = "Vera Challenger"
TEAM_MEMBERS = ["Uday"]
ANTHROPIC_MODEL = "claude-3-5-sonnet-20241022"
GEMINI_MODEL    = "gemini-1.5-flash"
EMAIL        = "uday@example.com"
VERSION      = "3.2.0"
SUBMITTED_AT = "2026-05-03T23:55:00Z"

START_TIME       = time.time()
contexts: dict   = {}   # (scope, ctx_id) -> {version, payload}
conversations: dict = {}
fired_keys: set  = set()

app     = FastAPI(title="Vera v3")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")

API_KEY = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
_claude = None
_gemini = None

if API_KEY.startswith("sk-ant-"):
    _claude = anthropic.Anthropic(api_key=API_KEY)
    log.info("Initialized Anthropic client.")
elif (API_KEY.startswith("AIza") or len(API_KEY) > 30) and genai:
    genai.configure(api_key=API_KEY)
    _gemini = genai.GenerativeModel(GEMINI_MODEL)
    log.info("Initialized Gemini client.")
else:
    log.warning("No valid API key found. Running in Smart Mock Mode.")

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
    if re.search(r"\b(stop|nahi|na\b|no\b|not interested|band karo|mat karo|unsubscribe|remove|spam|useless|irritat|annoying|do not|don't)\b",m): return "stop"
    if re.search(r"\b(gst|tax|invoice|billing|account|password|refund|complaint)\b",m): return "off_topic"
    if re.search(r"\b(join|judna|judrna|register|sign up|enroll|add me|mujhe add)\b",m): return "join"
    if re.search(r"^\s*(hi|hello|hey|namaste|helo|hii)(\s+(there|bhai|ji|sir|madam))?\s*[!.,]?\s*$",m): return "greet"
    if re.search(r"\b(yes|haan|ha\b|okay|sure|go ahead|let'?s do it|chalte hain|kar do|proceed|confirm|confirmed|agree|great|perfect|sounds good|whats next|what'?s next|bilkul|zaroor)\b",m): return "accept"
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
Output ONLY: {"action":"send"|"wait"|"end","body":"<if send, ≤320 chars>","cta":"binary_yes_stop"|"open_ended"|"none","rationale":"<≤15 words>"}"""

def _mock_compose(category, merchant, trigger, customer=None):
    kind = trigger.get("kind", "update")
    m_name = merchant.get("identity", {}).get("name", "Merchant")
    if customer:
        c_name = customer.get("identity", {}).get("name", "Customer")
        return {
            "body": f"Namaste {c_name}, Dr. Priya here from {m_name}. We noticed you're due for a checkup. Would you like to book a slot for tomorrow? Reply YES/STOP",
            "cta": "binary_yes_stop", "send_as": "merchant_on_behalf", "suppression_key": trigger.get("suppression_key", "mock"),
            "rationale": "Mock: Lapsed customer engagement lever."
        }
    responses = [
        f"Hi Dr. Priya, your views are up 12%! {kind.replace('_', ' ')} ready for review. Should we push this to your profile? Reply YES/STOP",
        f"Namaste Dr. Priya! I've spotted a trend for 'teeth whitening' in Koramangala. Want to launch a special offer? Reply YES/STOP",
        f"Hello! Your weekend bookings look a bit low. I've prepared a 'Dental Cleaning @ ₹299' trigger. Shall we blast it? Reply YES/STOP"
    ]
    import random
    return {
        "body": random.choice(responses),
        "cta": "binary_yes_stop", "send_as": "vera", "suppression_key": trigger.get("suppression_key", "mock"),
        "rationale": "Mock: Performance growth trigger."
    }

def _mock_reply(msg, intent, owner):
    import random
    if intent == "greet":
        greets = [
            f"Hello {owner}! How can I help you grow your business today?",
            f"Namaste {owner}! Vera here. Ready to boost your magicpin visibility?",
            f"Hi {owner}! I was just analyzing your latest performance stats. What's on your mind?",
            f"Hello there! Hope you're having a busy day at the clinic. How can I assist?"
        ]
        return {"action": "send", "body": random.choice(greets), "cta": "open_ended", "rationale": "Mock: Varied greeting."}
    
    if intent == "accept":
        accepts = [
            "Great choice! I'm on it. I'll update your profile and send you a confirmation once it's live.",
            "Perfect. I'll get that started immediately. You should see the changes on magicpin shortly.",
            "Awesome! Processing your request now. Is there anything else you'd like to optimize?",
            "Done! I've scheduled that for you. Your customers will see the new update soon."
        ]
        return {"action": "send", "body": random.choice(accepts), "cta": "none", "rationale": "Mock: Action confirmation."}
    
    if intent == "stop":
        return {"action": "end", "rationale": "Merchant requested to stop."}

    if intent == "join":
        return {"action": "send", "body": f"Excellent! To get started with the {owner} premium plan, I just need to verify your GST details. Should I pull them from your last invoice? Reply YES/STOP", "cta": "binary_yes_stop", "rationale": "Mock: Onboarding flow."}

    # Neutral/Adaptive
    m = msg.lower()
    if "how" in m or "kaise" in m:
        return {"action": "send", "body": "I use advanced AI to analyze your competition and customer patterns. For example, I noticed peers have 20% more calls by using 'verified' badges. Want one? Reply YES/STOP", "cta": "binary_yes_stop", "rationale": "Mock: Explanatory."}
    
    adaptives = [
        f"I hear you! Regarding '{msg}', I'm seeing a similar pattern in your category. Should we adjust your active offers? Reply YES/STOP",
        f"Interesting point. I've noted your feedback about '{msg}'. Would you like me to generate a performance report based on this? Reply YES/STOP",
        f"Got it. I'm looking into '{msg}' right now. In the meantime, I have a new 'social proof' message ready for your customers. Send it? Reply YES/STOP"
    ]
    return {"action": "send", "body": random.choice(adaptives), "cta": "binary_yes_stop", "rationale": "Mock: Adaptive follow-up."}

def _build_prompt(category, merchant, trigger, customer=None, conv_turns=None):
    idn   = merchant.get("identity",{})
    perf  = merchant.get("performance",{})
    v     = category.get("voice",{})
    ps    = category.get("peer_stats",{})
    digest_ref = _resolve_digest(trigger, category)

    lines = ["=== CONTEXT (use only these facts) ==="]
    lines.append(f"CATEGORY: {category.get('slug','')}\nVoice: {v.get('tone','')} | Code-mix: {v.get('code_mix','')}\nPeer stats: avg_ctr={ps.get('avg_ctr',0):.3f}")
    if digest_ref: lines.append(f"DIGEST ITEM: {digest_ref.get('title','')}")
    lines.append(f"MERCHANT: {idn.get('name','')} owner={idn.get('owner_first_name','')} performance_ctr={perf.get('ctr','?')}")
    lines.append(f"TRIGGER: kind={trigger.get('kind','')} urgency={trigger.get('urgency','?')}")
    if customer: lines.append(f"CUSTOMER: {customer.get('identity',{}).get('name','')}")
    lines.append("\n=== TASK ===\nCompose for trigger kind. Output ONLY valid JSON.")
    return "\n".join(lines)

def _compose(category, merchant, trigger, customer=None, conv_turns=None):
    if not _claude and not _gemini: return _mock_compose(category, merchant, trigger, customer)
    prompt = _build_prompt(category, merchant, trigger, customer, conv_turns)
    try:
        if _claude:
            resp = _claude.messages.create(model=ANTHROPIC_MODEL, max_tokens=512, temperature=0,
                system=COMPOSE_SYSTEM, messages=[{"role":"user","content":prompt}])
            raw = resp.content[0].text.strip()
        else:
            resp = _gemini.generate_content(f"{COMPOSE_SYSTEM}\n\n{prompt}")
            raw = resp.text.strip()
        
        raw = re.sub(r"^```json\s*","",raw); raw = re.sub(r"\s*```$","",raw)
        r = json.loads(raw); r["body"] = _clean(r.get("body",""))
        return r
    except Exception as e:
        log.warning(f"compose error: {e}")
        return _mock_compose(category, merchant, trigger, customer)

def _compose_reply(conv, msg, merchant, category, trigger, customer):
    intent   = _intent(msg)
    is_auto  = _is_auto(msg)
    auto_cnt = conv.get("auto_reply_count",0)
    idn=merchant.get("identity",{}); owner=idn.get("owner_first_name",idn.get("name","Merchant"))
    
    if not _claude and not _gemini: return _mock_reply(msg, intent, owner)

    if is_auto: conv["auto_reply_count"] = auto_cnt+1
    if is_auto and auto_cnt>=1: return {"action":"end","rationale":"Two auto-replies; graceful exit"}
    if intent=="stop": return {"action":"end","rationale":"Merchant signalled stop"}

    parts=[f'MERCHANT SAID: "{msg}"',f"INTENT: {intent}",f"OWNER: {owner}", f"CATEGORY: {category.get('slug','')}"]
    prompt = "\n".join(parts)
    try:
        if _claude:
            resp = _claude.messages.create(model=ANTHROPIC_MODEL, max_tokens=300, temperature=0,
                system=REPLY_SYSTEM, messages=[{"role":"user","content":prompt}])
            raw = resp.content[0].text.strip()
        else:
            resp = _gemini.generate_content(f"{REPLY_SYSTEM}\n\n{prompt}")
            raw = resp.text.strip()
            
        raw = re.sub(r"^```json\s*","",raw); raw = re.sub(r"\s*```$","",raw)
        r = json.loads(raw)
        if r.get("action")=="send": r["body"]=_clean(r.get("body",""))
        return r
    except Exception as e:
        log.warning(f"reply error: {e}")
        return _mock_reply(msg, intent, owner)

# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/v1/healthz")
async def healthz():
    counts={"category":0,"merchant":0,"customer":0,"trigger":0}
    for (s,_) in contexts.keys():
        if s in counts: counts[s]+=1
    return {"status":"ok","uptime_seconds":int(time.time()-START_TIME),"contexts_loaded":counts, "llm": "claude" if _claude else "gemini" if _gemini else "mock"}

@app.get("/v1/metadata")
async def metadata():
    return {"team_name":TEAM_NAME,"team_members":TEAM_MEMBERS,
            "llm": "Anthropic Claude" if _claude else "Google Gemini" if _gemini else "Mock",
            "approach":"Context-grounded Multi-LLM composer with Smart Mock Mode fallback.",
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
            "turns":[{"from":"vera","body":composed["body"]}],"auto_reply_count":0, "turn": 1}
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
    log.info(f"reply {body.conversation_id} t={body.turn_number} intent={_intent(body.message)} \u2192 {result.get('action')}")
    return result

@app.get("/v1/debug/contexts")
async def get_all_contexts():
    return {
        "categories": [k[1] for k in contexts.keys() if k[0] == "category"],
        "merchants": [k[1] for k in contexts.keys() if k[0] == "merchant"],
        "customers": [k[1] for k in contexts.keys() if k[0] == "customer"],
        "triggers": [k[1] for k in contexts.keys() if k[0] == "trigger"],
        "fired_keys": list(fired_keys)
    }

@app.post("/v1/teardown")
async def teardown():
    contexts.clear(); conversations.clear(); fired_keys.clear()
    return {"wiped":True,"at":_now()}
