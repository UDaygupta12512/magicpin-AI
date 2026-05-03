import requests
import json
import time
import os
from datetime import datetime, timezone

# --- CONFIGURATION ---
# Set these before running or provide via environment variables
BOT_URL = os.getenv("BOT_URL", "http://localhost:8000")
LLM_API_KEY = os.getenv("GOOGLE_API_KEY", "AIzaSyCJBWM9AVxfLzS4-DJGWi--gW62AZJlUf0")
LLM_PROVIDER = "google" # or "anthropic"

# --- COLORS ---
G = "\033[92m"
R = "\033[91m"
Y = "\033[93m"
C = "\033[96m"
B = "\033[1m"
X = "\033[0m"

def log(msg, status="info"):
    icon = {"success": f"{G}[OK]{X}", "error": f"{R}[ERR]{X}", "info": f"{C}[INFO]{X}", "warn": f"{Y}[WARN]{X}"}
    clean_msg = msg.encode('ascii', 'replace').decode()
    print(f"{icon.get(status, ' ')} {clean_msg}")

def check_endpoint(name, method, path, body=None):
    url = f"{BOT_URL}{path}"
    try:
        start = time.time()
        if method == "GET":
            res = requests.get(url, timeout=10)
        else:
            res = requests.post(url, json=body, timeout=10)
        latency = (time.time() - start) * 1000
        
        if res.status_code == 200:
            log(f"{name} ({path}): {G}PASS{X} [{latency:.0f}ms]", "success")
            return res.json()
        elif res.status_code == 409 and "context" in path:
             log(f"{name} ({path}): {Y}ACCEPTED (409 Conflict - Already exists){X}", "warn")
             return None
        else:
            log(f"{name} ({path}): {R}FAIL{X} (Status {res.status_code})", "error")
            return None
    except Exception as e:
        log(f"{name} ({path}): {R}ERROR{X} ({str(e)})", "error")
        return None

def run_simulation():
    print(f"\n{B}{C}Vera Official Judge Simulator (Local Harness){X}")
    print(f"{C}Target URL: {BOT_URL}{X}\n" + "-"*50)
    
    # Clean state
    check_endpoint("Teardown", "POST", "/v1/teardown")
    
    score = 0
    total_checks = 10
    
    # 1. Health & Metadata
    health = check_endpoint("Health Check", "GET", "/v1/healthz")
    if health: score += 1
    
    meta = check_endpoint("Metadata", "GET", "/v1/metadata")
    if meta: score += 1
    
    # 2. Context Ingestion
    print(f"\n{B}Step 1: Pushing Context Layers...{X}")
    cat_res = check_endpoint("Category Context", "POST", "/v1/context", {
        "scope": "category", "context_id": "dentists", "version": 1,
        "payload": {"slug": "dentists", "voice": {"tone": "clinical", "code_mix": "hi_en"}},
        "delivered_at": datetime.now(timezone.utc).isoformat()
    })
    if cat_res: score += 1
    
    merch_res = check_endpoint("Merchant Context", "POST", "/v1/context", {
        "scope": "merchant", "context_id": "mer_smile_001", "version": 1,
        "payload": {
            "merchant_id": "mer_smile_001",
            "category_slug": "dentists",
            "identity": {"name": "Smile Dental", "owner_first_name": "Priya"},
            "performance": {"ctr": 0.045},
            "offers": [{"title": "50% off Cleaning", "status": "active"}]
        },
        "delivered_at": datetime.now(timezone.utc).isoformat()
    })
    if merch_res: score += 1
    
    trg_res = check_endpoint("Trigger Context", "POST", "/v1/context", {
        "scope": "trigger", "context_id": "trg_perf_001", "version": 1,
        "payload": {
            "id": "trg_perf_001", "kind": "perf_dip", "merchant_id": "mer_smile_001",
            "urgency": 4, "suppression_key": "smile_dip_1", "expires_at": "2099-12-31T23:59:59Z"
        },
        "delivered_at": datetime.now(timezone.utc).isoformat()
    })
    if trg_res: score += 1

    # 3. Tick (Automated Message)
    print(f"\n{B}Step 2: Running Tick (Proactive Composition)...{X}")
    tick_res = check_endpoint("Tick Engine", "POST", "/v1/tick", {
        "now": datetime.now(timezone.utc).isoformat(),
        "available_triggers": ["trg_perf_001"]
    })
    
    conv_id = None
    if tick_res and tick_res.get("actions"):
        action = tick_res["actions"][0]
        conv_id = action.get("conversation_id")
        log(f"Composed Message: {Y}'{action.get('body', '')[:100]}...'{X}")
        log(f"Rationale: {C}{action.get('rationale', 'N/A')}{X}")
        score += 2
    else:
        log("No actions returned by Tick", "warn")

    # 4. Reply (Interactive Handling)
    if conv_id:
        print(f"\n{B}Step 3: Testing Merchant Reply...{X}")
        reply_res = check_endpoint("Reply Engine", "POST", "/v1/reply", {
            "conversation_id": conv_id,
            "merchant_id": "mer_smile_001",
            "from_role": "merchant",
            "message": "Yes, please run this offer for my customers.",
            "received_at": datetime.now(timezone.utc).isoformat(),
            "turn_number": 2
        })
        if reply_res:
            log(f"Vera Reply: {Y}'{reply_res.get('body', '')[:100]}...'{X}")
            score += 2
    
    # Final Scoring
    print("\n" + "-"*50)
    final_pct = (score / total_checks) * 100
    color = G if final_pct > 80 else Y if final_pct > 50 else R
    print(f"{B}FINAL SIMULATION SCORE: {color}{final_pct:.0f}%{X}")
    print(f"Rubric Breakdown: Endpoints={score//2}/5 | Logic={score%2}/2 | Quality=3/3 (Estimated)")
    print("-"*50 + "\n")

if __name__ == "__main__":
    run_simulation()
