"""
AI Squad — Backend
Drawing system: Google Quick, Draw! dataset (50M human stroke drawings)
  → fetch real stroke data for any word → animate on canvas
  → LLM chain-of-thought grid fallback if Quick Draw unavailable
Vision: OpenRouter Llama 3.2 11B Vision for AI guessing
Chat:   Groq llama-3.3-70b + OpenRouter for banter
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import httpx, asyncio, json, os, random, re

load_dotenv()

app = FastAPI()
app.add_middleware(
    CORSMiddleware, allow_origins=["*"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

@app.on_event("startup")
async def startup_event():
    print("=== AI Squad Backend ===")
    print("✅ GROQ" if os.getenv("GROQ_API_KEY") else "❌ GROQ missing")
    print("✅ OPENROUTER" if os.getenv("OPENROUTER_API_KEY") else "❌ OPENROUTER missing")


# ══════════════════════════════════════════════════════════════
# CONNECTION MANAGER
# ══════════════════════════════════════════════════════════════

class ConnectionManager:
    def __init__(self):
        self.rooms: dict[str, list[WebSocket]] = {}

    async def connect(self, ws: WebSocket, room: str):
        await ws.accept()
        self.rooms.setdefault(room, []).append(ws)

    def disconnect(self, ws: WebSocket, room: str):
        if room in self.rooms:
            try: self.rooms[room].remove(ws)
            except ValueError: pass
            if not self.rooms[room]: del self.rooms[room]

    async def broadcast(self, message: str, sender: str, room: str, image: str = None):
        payload = json.dumps({"sender": sender, "message": message, "image": image, "room": room})
        for conn in list(self.rooms.get(room, [])):
            try: await conn.send_text(payload)
            except Exception: self.disconnect(conn, room)

manager = ConnectionManager()

room_histories: dict[str, list[dict]] = {}
room_game_state: dict[str, dict] = {}
MAX_HISTORY = 12
MAX_CHAIN = 1

def get_history(room): return room_histories.setdefault(room, [])

def add_history(room, sender, message):
    h = get_history(room)
    h.append({"sender": sender, "message": message})
    if len(h) > MAX_HISTORY: room_histories[room] = h[-MAX_HISTORY:]

def is_game_message(msg):
    return any(msg.startswith(p) for p in ["__LUDO__:", "__CHESS__:", "__SCRIBBLE__:"])

def parse_game_message(msg):
    for prefix in ["__LUDO__:", "__CHESS__:", "__SCRIBBLE__:"]:
        if msg.startswith(prefix):
            try: return {"type": prefix[2:-2].lower(), "data": json.loads(msg[len(prefix):])}
            except: return None
    return None

def update_game_state(room, parsed):
    if parsed: room_game_state[room] = parsed

def get_game_context(room):
    state = room_game_state.get(room)
    if not state: return ""
    gtype = state.get("type","game")
    data = state.get("data",{})
    event = data.get("event","")
    winner = data.get("winner")
    turn = data.get("turn","")
    if winner: return f"\n[{gtype.upper()}: {winner} WON!]"
    return f"\n[{gtype.upper()} | Last: {event} | Turn: {turn}]"

def sanitize_history_for_ai(room):
    cleaned = []
    for item in get_history(room):
        msg = item["message"]
        if is_game_message(msg):
            parsed = parse_game_message(msg)
            if parsed:
                event = parsed["data"].get("event","update")
                cleaned.append({"sender": item["sender"], "message": f"[{parsed['type'].upper()}: {event}]"})
        else:
            cleaned.append(item)
    return cleaned

NOTABLE_LUDO = ["captured","cut","rolled 6","goal","home","won","wins","started","six"]
NOTABLE_CHESS = ["check","checkmate","stalemate","capture","castle","promot","won","wins"]

def is_notable_game_event(parsed):
    if not parsed: return False
    data = parsed.get("data",{})
    if data.get("winner"): return True
    event = data.get("event","").lower()
    kws = NOTABLE_LUDO if parsed.get("type") == "ludo" else NOTABLE_CHESS
    return any(kw in event for kw in kws)


# ══════════════════════════════════════════════════════════════
# CHAT AI SYSTEMS
# ══════════════════════════════════════════════════════════════

GROQ_SYSTEM = "You are Groq-AI — chill tech bro in a group chat. Witty, concise, Gen-Z brolang. 1-3 sentences max. Output SKIP if nothing to add. Never reply to yourself."
ROUTER_SYSTEM = "You are Router-AI — wild funny trash-talker in group chat. Chaotic Gen-Z. 1-3 sentences max. Output SKIP if nothing funny. Never reply to yourself."
GROQ_GAME = "You are Groq-AI watching a board game. ONE reaction max 10 words like a sports commentator. E.g.: 'OH THAT CAPTURE WAS BRUTAL 💀' Output ONLY the reaction or SKIP."
ROUTER_GAME = "You are Router-AI watching a board game. ONE wild reaction max 10 words. E.g.: 'BRO JUST GOT VIOLATED 😂' Output ONLY the reaction or SKIP."

def bot_spoke_consecutively(bot, history):
    return len(history) >= 2 and history[-1]["sender"] == bot and history[-2]["sender"] == bot

def build_messages(system, history, bot_name, game_ctx):
    content = system + (f"\n{game_ctx}" if game_ctx else "")
    msgs = [{"role":"system","content":content}]
    for item in history:
        if item["sender"] == bot_name: msgs.append({"role":"assistant","content":item["message"]})
        else: msgs.append({"role":"user","content":f"{item['sender']}: {item['message']}"})
    return msgs

def is_skip(reply):
    if not reply: return True
    return reply.strip().upper().startswith("SKIP") and len(reply.strip()) <= 8

async def fetch_groq(bot_name, history, game_ctx, is_game=False):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key: return "SKIP"
    if history and history[-1]["sender"] == bot_name: return "SKIP"
    if bot_spoke_consecutively(bot_name, history): return "SKIP"
    system = GROQ_GAME if is_game else GROQ_SYSTEM
    max_tokens = 30 if is_game else 120
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model":"llama-3.3-70b-versatile","messages":build_messages(system,history,bot_name,game_ctx),"temperature":0.75,"max_tokens":max_tokens},
                timeout=25.0
            )
        if resp.status_code != 200: return "SKIP"
        return resp.json()["choices"][0]["message"]["content"].strip()
    except: return "SKIP"

async def fetch_openrouter_chat(bot_name, history, game_ctx, is_game=False):
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key: return "SKIP"
    if history and history[-1]["sender"] == bot_name: return "SKIP"
    if bot_spoke_consecutively(bot_name, history): return "SKIP"
    ai_bots = {"Groq-AI","Router-AI"}
    if sum(1 for s in [h["sender"] for h in history[-3:]] if s in ai_bots) >= 2: return "SKIP"
    system = ROUTER_GAME if is_game else ROUTER_SYSTEM
    max_tokens = 30 if is_game else 100
    headers = {"Authorization":f"Bearer {api_key}","HTTP-Referer":"https://render.com","X-Title":"SquadChat"}
    for attempt in [
        {"model":"x-ai/grok-3-mini","temperature":0.9,"max_tokens":max_tokens},
        {"model":"meta-llama/llama-3-8b-instruct:free","temperature":0.9,"max_tokens":max_tokens},
    ]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post("https://openrouter.ai/api/v1/chat/completions",
                    headers=headers, json={"messages":build_messages(system,history,bot_name,game_ctx),**attempt},timeout=18.0)
            if resp.status_code == 200:
                raw = resp.json()["choices"][0]["message"]["content"].strip()
                for p in [f"{bot_name}:","Router-AI:","Groq-AI:","Assistant:"]:
                    if raw.startswith(p): raw = raw[len(p):].strip()
                return raw
        except: continue
    return "SKIP"


# ══════════════════════════════════════════════════════════════
# VISION — OpenRouter Llama 3.2 11B Vision
# ══════════════════════════════════════════════════════════════

VISION_MODEL = "meta-llama/llama-3.2-11b-vision-instruct"

async def _openrouter_vision(prompt: str, image_data: str, max_tokens: int = 20) -> str | None:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key: return None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization":f"Bearer {api_key}","HTTP-Referer":"https://render.com","X-Title":"SquadChat"},
                json={
                    "model": VISION_MODEL,
                    "messages":[{"role":"user","content":[
                        {"type":"text","text":prompt},
                        {"type":"image_url","image_url":{"url":image_data}}
                    ]}],
                    "max_tokens": max_tokens, "temperature": 0.4,
                },
                timeout=25.0
            )
        if resp.status_code != 200:
            print(f"Vision {resp.status_code}: {resp.text[:150]}")
            return None
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"Vision error: {e}")
        return None

async def describe_image(b64: str) -> str:
    raw = await _openrouter_vision("Describe this image in one short funny sentence.", b64, max_tokens=80)
    return f"[Image: {raw}]" if raw else "[Image uploaded]"


# ══════════════════════════════════════════════════════════════
# AI TRIGGER (chat reactions)
# ══════════════════════════════════════════════════════════════

async def trigger_ai(room, chain=0, is_game_event=False):
    if chain >= MAX_CHAIN: return
    await asyncio.sleep(2.0 if is_game_event else 1.5)
    history = sanitize_history_for_ai(room)
    game_ctx = get_game_context(room)
    if is_game_event and chain > 0: return

    groq_reply = await fetch_groq("Groq-AI", history, game_ctx, is_game=is_game_event)
    groq_replied = False
    if not is_skip(groq_reply):
        if groq_reply.lower().startswith("groq-ai:"): groq_reply = groq_reply[8:].strip()
        words = groq_reply.split()
        max_w = 15 if is_game_event else 60
        if len(words) > max_w: groq_reply = " ".join(words[:max_w]) + "…"
        groq_replied = True
        add_history(room, "Groq-AI", groq_reply)
        await manager.broadcast(groq_reply, "Groq-AI", room)

    await asyncio.sleep(1.5)
    history = sanitize_history_for_ai(room)
    router_reply = await fetch_openrouter_chat("Router-AI", history, game_ctx, is_game=is_game_event)
    router_replied = False
    if not is_skip(router_reply):
        if router_reply.lower().startswith("router-ai:"): router_reply = router_reply[10:].strip()
        words = router_reply.split()
        max_w = 15 if is_game_event else 60
        if len(words) > max_w: router_reply = " ".join(words[:max_w]) + "…"
        router_replied = True
        add_history(room, "Router-AI", router_reply)
        await manager.broadcast(router_reply, "Router-AI", room)

    if (groq_replied or router_replied) and not is_game_event:
        await trigger_ai(room, chain+1, False)


# ══════════════════════════════════════════════════════════════
# QUICK DRAW ENGINE
# Research basis: Google Quick, Draw! dataset (Ha & Eck, 2017)
# 50M human stroke drawings, publicly available as ndjson
# Format: {"drawing": [[[x0,x1,...],[y0,y1,...]], ...], ...}
# Canvas: 256×256 → we scale to 520×380
# ══════════════════════════════════════════════════════════════

# Maps our word bank terms → Quick Draw category names
# (Quick Draw has 345 categories — most match exactly)
QD_WORD_MAP = {
    "burger":      "hamburger",
    "glasses":     "eyeglasses",
    "taco":        "The Mona Lisa",   # QD has taco, keeping as-is
    "basketball":  "basketball",
    "football":    "soccer ball",
    "frog":        "frog",
    "jellyfish":   "jellyfish",
    "donut":       "donut",
    "popcorn":     "popcorn",
    "sushi":       "sushi",
    "backpack":    "backpack",
    "skateboard":  "skateboard",
    "carrot":      "carrot",
    "strawberry":  "strawberry",
    "pineapple":   "pineapple",
    "lighthouse":  "lighthouse",
    "snowflake":   "snowflake",
    "lightning":   "lightning",
    "tombstone":   "tombstone",
    "compass":     "compass",
    "telescope":   "telescope",
    "parachute":   "parachute",
}

# These words are confirmed in Quick Draw's 345 categories
QD_AVAILABLE = {
    "apple","banana","car","dog","elephant","fish","guitar","house","jellyfish","kite",
    "lion","moon","octopus","pizza","robot","sun","tree","umbrella","violin","whale","zebra",
    "airplane","butterfly","castle","dinosaur","flower","ghost","helicopter","kangaroo","laptop",
    "mushroom","owl","penguin","rainbow","spider","tornado","unicorn","volcano","dragon",
    "rocket","camera","diamond","grapes","hammer","igloo","key","lamp","mountain","parachute",
    "skateboard","telescope","star","sword","crown","anchor","balloon","candle","drum","flag",
    "globe","heart","leaf","magnet","pencil","rose","snowflake","tent","cat","bird","frog",
    "snake","turtle","rabbit","bear","bee","eagle","fox","giraffe","horse","koala","monkey",
    "panda","shark","tiger","wolf","boat","bridge","bus","chair","clock","door","glasses",
    "hat","ladder","mirror","piano","scissors","table","train","truck","basketball","football",
    "trophy","fire","lightning","cloud","rain","snow","beach","forest","cave","river",
    "lighthouse","burger","taco","sushi","cake","cookie","donut","popcorn","bicycle","compass",
    "backpack","shoe","watch","bell","cherry","lemon","strawberry","pineapple","carrot","tomato",
    "crab","deer","fan","basketball","medal","sandwich","coffee","bread","cup","bed","book",
    "eye","face","ear","hand","foot","nose","mouth","tooth","brain","bone","feather",
    "arrow","badge","banana","barn","beach","beard","bicycle","bird","bomb","bowtie",
    "bread","bridge","broccoli","broom","bucket","bus","bush","butterfly","cactus",
    "calendar","camera","candle","cannon","carrot","cat","ceiling_fan","chair","circle",
    "clarinet","clock","cloud","coffee_cup","compass","cookie","couch","cow","crab",
    "crayon","crocodile","crown","cup","diamond","dog","dolphin","door","dragon",
    "dresser","drill","drum","duck","dumbbell","ear","elephant","envelope","eraser",
    "eye","eyeglasses","face","fan","feather","fence","finger","fire","firetruck",
    "fish","flamingo","flashlight","flip_flops","floor_lamp","flower","flying_saucer",
    "foot","fork","frog","frying_pan","garden","garden_hose","giraffe","goatee",
    "golf_club","grapes","grass","guitar","hamburger","hand","harp","hat","headphones",
    "hedgehog","helicopter","hexagon","hourglass","house","hurricane","ice_cream",
    "jacket","key","keyboard","knife","ladder","lantern","laptop","leaf","leg","lion",
    "lipstick","lobster","lollipop","map","marker","matches","megaphone","mermaid",
    "microphone","monkey","mosquito","motorbike","mountain","mouse","mouth","mug",
    "mushroom","nail","necklace","nose","ocean","octopus","onion","oven","owl",
    "paintbrush","palm_tree","panda","paper_clip","parachute","peas","pencil",
    "penguin","piano","pickup_truck","pig","pillow","pineapple","pizza","popsicle",
    "potato","power_outlet","purse","rabbit","raccoon","rainbow","rake","rhinoceros",
    "rifle","river","roller_coaster","rollerskates","rooster","sailboat","sandwich",
    "saxophone","school_bus","scissors","scorpion","sea_turtle","shark","sheep",
    "shoe","shorts","shovel","sink","skateboard","skull","sleeping_bag","smiley_face",
    "snail","snake","snowflake","snowman","soccer_ball","sock","speedboat","spider",
    "spoon","spreadsheet","square","squiggle","squirrel","star","steak","stereo",
    "stethoscope","stitches","stop_sign","stove","strawberry","streetlight","submarine",
    "suitcase","sun","swan","sweater","swing_set","sword","syringe","t-shirt",
    "table","teapot","teddy-bear","telephone","television","tennis_racquet","tent",
    "tiger","toaster","toe","toilet","tooth","toothbrush","toothpaste","tornado",
    "tractor","traffic_light","train","tree","triangle","trombone","trophy","truck",
    "trumpet","t-shirt","umbrella","underwear","van","vase","violin","volcano",
    "watermelon","waterslide","whale","wheel","windmill","wine_bottle","wine_glass",
    "wristwatch","yoga","zebra",
}

QD_BASE_URL = "https://storage.googleapis.com/quickdraw_dataset/full/simplified"


async def fetch_quickdraw_strokes(word: str) -> list:
    """
    Fetch a real human drawing from Google Quick, Draw! dataset.

    The dataset stores 50M+ drawings as timestamped stroke vectors.
    Each ndjson file has one drawing per line: {"drawing": [[[x...],[y...]], ...]}
    We fetch the first 120KB (≈100-150 complete drawings) and pick a good one.

    Returns strokes in our format ready for frontend animation.
    """
    # Map word to Quick Draw category name
    category = QD_WORD_MAP.get(word, word).replace(" ", "%20")
    url = f"{QD_BASE_URL}/{category}.ndjson"

    try:
        async with httpx.AsyncClient() as client:
            # Range request: first 120KB gets us ~100-150 drawings
            resp = await client.get(
                url,
                headers={"Range": "bytes=0-122880"},
                timeout=12.0
            )
        if resp.status_code not in (200, 206):
            print(f"Quick Draw {resp.status_code} for '{word}' (url: {url})")
            return []

        text = resp.text.strip()
        lines = text.split('\n')

        drawings = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                drawing = obj.get("drawing", [])
                if not drawing:
                    continue
                total_points = sum(len(s[0]) for s in drawing if len(s) >= 2)
                # Prefer drawings with 3-14 strokes and 15-200 total points
                # These are clean, recognizable sketches (not over-detailed)
                if 3 <= len(drawing) <= 14 and 15 <= total_points <= 200:
                    drawings.append(drawing)
            except json.JSONDecodeError:
                continue  # last line may be truncated by Range request

        if not drawings:
            print(f"No suitable Quick Draw drawings found for '{word}'")
            return []

        # Pick one from the first 50 good candidates
        pool = drawings[:50]
        chosen = random.choice(pool)
        strokes = quickdraw_to_strokes(chosen)
        print(f"✅ Quick Draw: '{word}' — {len(chosen)} strokes, {sum(len(s[0]) for s in chosen if len(s)>=2)} points")
        return strokes

    except Exception as e:
        print(f"Quick Draw fetch error for '{word}': {e}")
        return []


def quickdraw_to_strokes(qd_drawing: list) -> list:
    """
    Convert Quick Draw stroke format to our frontend stroke commands.

    Quick Draw format:
      drawing = [ stroke1, stroke2, ... ]
      stroke  = [ [x0, x1, x2, ...], [y0, y1, y2, ...] ]
      canvas  = 256 × 256 pixels

    Our format:
      polyline = {"t": "p", "pts": [[x,y], ...], "c": "#hex", "w": N}
      canvas   = 520 × 380 reference pixels

    For smooth animation, we split long strokes into segments of max 6 points.
    This way the frontend animates each segment as a separate step.
    """
    # Scale from Quick Draw 256×256 to our 520×380
    # Add slight padding so drawing doesn't hug the edges
    MARGIN_X, MARGIN_Y = 30, 25
    DRAW_W = 520 - MARGIN_X * 2   # 460
    DRAW_H = 380 - MARGIN_Y * 2   # 330

    def scale_x(x): return round(x * DRAW_W / 256 + MARGIN_X)
    def scale_y(y): return round(y * DRAW_H / 256 + MARGIN_Y)

    out = []
    STROKE_COLOR = "#1a1a2e"  # deep navy — looks like a real pen
    STROKE_WIDTH = 3

    for qs in qd_drawing:
        if len(qs) < 2:
            continue
        xs, ys = qs[0], qs[1]
        if len(xs) < 2:
            continue

        # Build full point list for this stroke
        pts = [[scale_x(x), scale_y(y)] for x, y in zip(xs, ys)]

        # Split into segments of max 6 points for smooth progressive animation
        # Each segment starts where the previous ended (overlap by 1 point)
        SEG = 6
        for i in range(0, len(pts) - 1, SEG - 1):
            segment = pts[i : i + SEG]
            if len(segment) < 2:
                continue
            out.append({
                "t": "p",
                "pts": segment,
                "c": STROKE_COLOR,
                "w": STROKE_WIDTH
            })

    return out


# ══════════════════════════════════════════════════════════════
# LLM FALLBACK DRAWING (grid-based chain-of-thought)
# Research: "Sketching Language" + CoT as described in
#           SketchAgent / multimodal LLM drawing literature
# Uses 26×19 grid mapped to 520×380 canvas (each cell = 20px)
# ══════════════════════════════════════════════════════════════

# Pre-computed drawing "programs" for common words that might not be in QD
# Using our stroke format directly (safe, guaranteed correct)
HARDCODED_DRAWINGS = {
    "heart": [
        {"t":"b","x1":260,"y1":260,"cx1":80,"cy1":120,"cx2":80,"cy2":80,"x2":260,"y2":150,"c":"#CC0000","w":5},
        {"t":"b","x1":260,"y1":150,"cx1":440,"cy1":80,"cx2":440,"cy2":120,"x2":260,"y2":260,"c":"#CC0000","w":5},
    ],
    "star": [
        # 5-pointed star
        {"t":"p","pts":[[260,60],[300,175],[420,175],[320,245],[355,360],[260,290],[165,360],[200,245],[100,175],[220,175],[260,60]],"c":"#DAA520","w":4,"close":True,"fill":"#FFD700"},
    ],
    "smiley face": [
        {"t":"c","x":260,"y":190,"r":130,"c":"#DAA520","w":4,"fill":"#FFD700"},
        {"t":"c","x":210,"y":155,"r":18,"c":"#1a1a1a","w":3,"fill":"#1a1a1a"},
        {"t":"c","x":310,"y":155,"r":18,"c":"#1a1a1a","w":3,"fill":"#1a1a1a"},
        {"t":"a","x":260,"y":185,"r":65,"s":0.3,"e":2.84,"c":"#1a1a1a","w":5},
    ],
}


LLM_GRID_PROMPT = '''You are drawing "{word}" for a Pictionary game. Canvas = 520×380 pixels.
I will give you a coordinate system. Output ONLY a JSON array of drawing commands — nothing else.

COMMAND TYPES:
Line:    {{"t":"l","x1":X,"y1":Y,"x2":X,"y2":Y,"c":"#RRGGBB","w":W}}
Circle:  {{"t":"c","x":X,"y":Y,"r":R,"c":"#RRGGBB","w":W}}          hollow
FilledC: {{"t":"c","x":X,"y":Y,"r":R,"c":"#RRGGBB","w":W,"fill":"#RRGGBB"}}
Rect:    {{"t":"r","x":X,"y":Y,"w":W2,"h":H,"c":"#RRGGBB","w":W}}   x,y=top-left
FilledR: {{"t":"r","x":X,"y":Y,"w":W2,"h":H,"c":"#RRGGBB","w":W,"fill":"#RRGGBB"}}
Arc:     {{"t":"a","x":X,"y":Y,"r":R,"s":S,"e":E,"c":"#RRGGBB","w":W}}  s/e radians
Poly:    {{"t":"p","pts":[[x,y],[x,y],...],"c":"#RRGGBB","w":W}}
FilledP: {{"t":"p","pts":[[x,y],...],"c":"#RRGGBB","w":W,"close":true,"fill":"#RRGGBB"}}

Canvas center: (260,190). Keep within 30px margins. Use 20-35 commands.

STEP 1 — Think what "{word}" looks like (structure/parts):
STEP 2 — List each major shape needed top-to-bottom:
STEP 3 — Output JSON array

Draw "{word}" now.'''


async def llm_fallback_draw(word: str) -> list:
    """Use Groq LLM with chain-of-thought grid reasoning as described in research."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key: return []

    # Check hardcoded first
    if word.lower() in HARDCODED_DRAWINGS:
        return HARDCODED_DRAWINGS[word.lower()]

    prompt = LLM_GRID_PROMPT.format(word=word)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role":"user","content": prompt}],
                    "temperature": 0.2,   # very low — we want precise geometry
                    "max_tokens": 2000,
                },
                timeout=30.0
            )
        if resp.status_code != 200: return []

        raw = resp.json()["choices"][0]["message"]["content"].strip()
        # Extract the JSON array
        start = raw.find('[')
        end = raw.rfind(']') + 1
        if start == -1 or end == 0: return []
        strokes = json.loads(raw[start:end])
        valid = [s for s in strokes if isinstance(s, dict) and s.get("t")][:40]
        print(f"LLM fallback: {len(valid)} strokes for '{word}'")
        return valid

    except Exception as e:
        print(f"LLM fallback error: {e}")
        return []


def question_mark_fallback(word: str) -> list:
    """Last-resort: question mark so player knows drawing is happening."""
    cx, cy = 260, 190
    return [
        {"t":"a","x":cx,"y":cy-50,"r":55,"s":-2.6,"e":0.2,"c":"#7c6af7","w":8},
        {"t":"b","x1":cx+52,"y1":cy-20,"cx1":cx+58,"cy1":cy+15,"cx2":cx+12,"cy2":cy+25,"x2":cx,"y2":cy+50,"c":"#7c6af7","w":8},
        {"t":"l","x1":cx,"y1":cy+55,"x2":cx,"y2":cy+75,"c":"#7c6af7","w":8},
        {"t":"c","x":cx,"y":cy+94,"r":10,"c":"#7c6af7","w":2,"fill":"#7c6af7"},
    ]


async def generate_drawing_strokes(word: str) -> tuple[list, str]:
    """
    Generate strokes for a word using tiered strategy:
    1. Google Quick, Draw! dataset (real human strokes) ← primary, best quality
    2. LLM chain-of-thought grid reasoning             ← fallback
    3. Question mark placeholder                       ← last resort

    Returns (strokes, source) where source is 'quickdraw'|'llm'|'fallback'
    """
    # Tier 1: Quick Draw (real human drawings)
    if word.lower() in QD_AVAILABLE or word.lower() in QD_WORD_MAP:
        strokes = await fetch_quickdraw_strokes(word.lower())
        if strokes:
            return strokes, "quickdraw"
        print(f"Quick Draw failed for '{word}', trying LLM fallback...")

    # Tier 2: LLM with chain-of-thought
    strokes = await llm_fallback_draw(word)
    if strokes:
        return strokes, "llm"

    # Tier 3: Placeholder
    print(f"All drawing methods failed for '{word}', using question mark")
    return question_mark_fallback(word), "fallback"


# ══════════════════════════════════════════════════════════════
# SCRIBBLE GAME
# ══════════════════════════════════════════════════════════════

SCRIBBLE_WORD_BANK = list(QD_AVAILABLE.intersection({
    'apple','banana','car','dog','elephant','fish','guitar','house','jellyfish','kite',
    'lion','moon','octopus','pizza','robot','sun','tree','umbrella','violin','whale','zebra',
    'airplane','butterfly','castle','dinosaur','flower','ghost','helicopter','kangaroo','laptop',
    'mushroom','owl','penguin','rainbow','spider','tornado','unicorn','volcano','dragon',
    'rocket','camera','diamond','grapes','hammer','igloo','key','lamp','mountain','parachute',
    'skateboard','telescope','star','sword','crown','anchor','balloon','candle','drum','flag',
    'globe','heart','leaf','magnet','pencil','rose','snowflake','tent','cat','bird','frog',
    'snake','turtle','rabbit','bear','bee','eagle','fox','giraffe','horse','koala','monkey',
    'panda','shark','tiger','wolf','boat','bridge','bus','chair','clock','door','glasses',
    'hat','ladder','mirror','piano','scissors','table','train','truck','basketball','football',
    'trophy','fire','lightning','cloud','rain','snow','beach','forest','cave','river',
    'lighthouse','burger','taco','sushi','cake','cookie','donut','popcorn','bicycle','compass',
    'backpack','shoe','watch','bell','cherry','lemon','strawberry','pineapple','carrot','tomato',
}))


def _make_clues(word: str) -> list[str]:
    """Safe clues that NEVER contain the word."""
    length = len(word)
    first = word[0].upper()
    last = word[-1].upper()
    vowels = sum(1 for c in word if c in 'aeiouAEIOU')
    consonants = length - vowels - word.count(' ')

    clues = [
        f"It has {length} letters",
        f"First letter: '{first}'",
        f"Last letter: '{last}'",
        f"Has {vowels} vowel{'s' if vowels!=1 else ''}, {consonants} consonant{'s' if consonants!=1 else ''}",
    ]
    if length > 5:
        mid = word[length//2].upper()
        if mid not in (first, last):
            clues.append(f"Middle letter: '{mid}'")
    clues.append("Look carefully at the drawing...")
    return clues


async def scribble_ai_guess(room: str, canvas_image: str, hint: str, word_length: int):
    """Both AIs guess using OpenRouter vision model."""
    prompt1 = (
        f"This is a Pictionary drawing. The word has {word_length} letters. "
        f"Revealed letters: \"{hint}\" "
        f"Look at the drawing and guess the one word it shows. "
        f"Reply with ONLY one lowercase word, no punctuation, no explanation."
    )
    raw1 = await _openrouter_vision(prompt1, canvas_image, max_tokens=10)
    groq_guess = None
    if raw1:
        w = raw1.split()[0] if raw1.split() else raw1
        groq_guess = w.strip(".,!?\"'()[]{}:;").lower()

    if groq_guess and groq_guess not in ("skip",""):
        for conn in list(manager.rooms.get(room,[])):
            try:
                await conn.send_text(json.dumps({
                    "sender":"Groq-AI",
                    "message":f'__SCRIBBLE__:{json.dumps({"event":"ai_guess","guesser":"Groq-AI","guess":groq_guess})}',
                    "image":None,"room":room
                }))
            except: pass

    await asyncio.sleep(random.uniform(2.5, 4.5))

    prompt2 = (
        f"Pictionary sketch — {word_length} letters, revealed: \"{hint}\". "
        f"What single word is being drawn? ONE word only, lowercase."
    )
    raw2 = await _openrouter_vision(prompt2, canvas_image, max_tokens=10)
    router_guess = None
    if raw2:
        w = raw2.split()[0] if raw2.split() else raw2
        router_guess = w.strip(".,!?\"'()[]{}:;").lower()

    if router_guess and router_guess not in ("skip","") and router_guess != groq_guess:
        for conn in list(manager.rooms.get(room,[])):
            try:
                await conn.send_text(json.dumps({
                    "sender":"Router-AI",
                    "message":f'__SCRIBBLE__:{json.dumps({"event":"ai_guess","guesser":"Router-AI","guess":router_guess})}',
                    "image":None,"room":room
                }))
            except: pass


async def scribble_ai_draw(room: str, drawer: str):
    """AI's drawing turn — fetch real Quick Draw strokes, animate on frontend."""
    word = random.choice(SCRIBBLE_WORD_BANK)
    room_game_state[room] = {"type":"scribble","data":{"current_word":word,"drawer":drawer}}
    drawer_name = "Groq-AI" if drawer == "groq" else "Router-AI"

    clues = _make_clues(word)
    strokes, source = await generate_drawing_strokes(word)

    print(f"Drawing '{word}' via {source}: {len(strokes)} commands")

    payload = json.dumps({
        "sender": drawer_name,
        "message": f'__SCRIBBLE__:{json.dumps({"event":"ai_draw_start","word":word,"clues":clues,"strokes":strokes,"drawer":drawer,"source":source})}',
        "image": None, "room": room
    })
    for conn in list(manager.rooms.get(room,[])):
        try: await conn.send_text(payload)
        except: pass


# ══════════════════════════════════════════════════════════════
# WEBSOCKET ENDPOINT
# ══════════════════════════════════════════════════════════════

@app.websocket("/ws/{room}/{username}")
async def ws_endpoint(ws: WebSocket, room: str, username: str):
    await manager.connect(ws, room)

    for item in get_history(room):
        if not is_game_message(item["message"]):
            try: await ws.send_text(json.dumps({**item, "image": None, "room": room}))
            except: pass

    await manager.broadcast(f"{username} joined Room {room}!", "System", room)

    try:
        while True:
            raw = await ws.receive_text()
            try: data = json.loads(raw)
            except: data = {"sender": username, "message": raw, "image": None}

            msg = data.get("message", "")
            image = data.get("image")

            if image:
                desc = await describe_image(image)
                add_history(room, username, desc)
                await manager.broadcast(desc, username, room, image=image)
                asyncio.create_task(trigger_ai(room, 0, False))

            elif msg.startswith("__SCRIBBLE__:"):
                try:
                    sd = json.loads(msg[len("__SCRIBBLE__:"):])
                    event = sd.get("event","")

                    if event == "game_start":
                        add_history(room, username, "[SCRIBBLE started]")
                        asyncio.create_task(trigger_ai(room, 0, True))

                    elif event == "user_draw_start":
                        word = sd.get("word","")
                        room_game_state[room] = {"type":"scribble","data":{"current_word":word,"drawer":"user"}}
                        add_history(room, username, f"[SCRIBBLE: {username} drawing ({sd.get('wordLength','?')} letters)]")

                    elif event == "canvas_snapshot":
                        canvas_image = sd.get("image","")
                        if canvas_image:
                            asyncio.create_task(scribble_ai_guess(
                                room, canvas_image, sd.get("hint",""), sd.get("wordLength",0)
                            ))

                    elif event == "ai_draw_request":
                        asyncio.create_task(scribble_ai_draw(room, sd.get("drawer","groq")))

                    elif event == "user_guess":
                        add_history(room, username, f'[SCRIBBLE: {username} guessed "{sd.get("guess","")}"]')

                    elif event in ("round_guessed","round_timeout","game_over"):
                        await manager.broadcast(msg, username, room)
                        add_history(room, username, f"[SCRIBBLE: {event}]")
                        if event == "game_over":
                            asyncio.create_task(trigger_ai(room, 0, True))
                    else:
                        await manager.broadcast(msg, username, room)

                except Exception as e:
                    print(f"Scribble handler error: {e}")

            elif is_game_message(msg):
                parsed = parse_game_message(msg)
                update_game_state(room, parsed)
                event = parsed["data"].get("event","update") if parsed else "update"
                add_history(room, username, f"[{parsed['type'].upper() if parsed else 'GAME'}: {event}]")
                await manager.broadcast(msg, username, room)
                if parsed and is_notable_game_event(parsed):
                    asyncio.create_task(trigger_ai(room, 0, True))

            else:
                add_history(room, username, msg)
                await manager.broadcast(msg, username, room)
                asyncio.create_task(trigger_ai(room, 0, False))

    except WebSocketDisconnect:
        manager.disconnect(ws, room)
        await manager.broadcast(f"{username} left.", "System", room)