from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import httpx
import asyncio
import json
import os
import random

load_dotenv()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
async def startup_event():
    print("--- SERVER STARTUP ---")
    print("✅ GROQ" if os.getenv("GROQ_API_KEY") else "❌ GROQ missing")
    print("✅ OPENROUTER" if os.getenv("OPENROUTER_API_KEY") else "❌ OPENROUTER missing")


# ── Room Manager ──────────────────────────────────────────────

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
    if winner: return f"\n[{gtype.upper()} GAME: {winner} just WON!]"
    return f"\n[{gtype.upper()} GAME | Last: {event} | Turn: {turn}]"

def sanitize_history_for_ai(room):
    cleaned = []
    for item in get_history(room):
        msg = item["message"]
        if is_game_message(msg):
            parsed = parse_game_message(msg)
            if parsed:
                event = parsed["data"].get("event","game update")
                cleaned.append({"sender": item["sender"], "message": f"[{parsed['type'].upper()} update: {event}]"})
        else:
            cleaned.append(item)
    return cleaned

NOTABLE_LUDO = ["captured","cut","rolled 6","goal","home stretch","home","won","wins","new","started","six"]
NOTABLE_CHESS = ["check","checkmate","stalemate","capture","castle","promot","won","wins","queen","rook"]

def is_notable_game_event(parsed):
    if not parsed: return False
    data = parsed.get("data",{})
    if data.get("winner"): return True
    event = data.get("event","").lower()
    kws = NOTABLE_LUDO if parsed.get("type") == "ludo" else NOTABLE_CHESS
    return any(kw in event for kw in kws)

# ── AI Chat Systems ───────────────────────────────────────────

GROQ_SYSTEM = """You are Groq-AI — chill tech bro in a group chat. Witty, concise, Gen-Z brolang.
RULES: 1-3 sentences max. Output SKIP if nothing to add. Never reply to yourself."""

ROUTER_SYSTEM = """You are Router-AI — wild funny trash-talker in group chat. Chaotic Gen-Z.
RULES: 1-3 sentences max. Output SKIP if nothing funny. Never reply to yourself."""

GROQ_GAME = """You are Groq-AI watching a board game. ONE reaction max 10 words like a sports commentator.
E.g.: "OH THAT CAPTURE WAS BRUTAL 💀" "SIX AGAIN?! 🔥"
Output ONLY the reaction or SKIP."""

ROUTER_GAME = """You are Router-AI watching a board game. ONE wild reaction max 10 words.
E.g.: "BRO JUST GOT VIOLATED 😂" "RIP bozo 🪦"
Output ONLY the reaction or SKIP."""

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

async def fetch_openrouter(bot_name, history, game_ctx, is_game=False):
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
                resp = await client.post("https://openrouter.ai/api/v1/chat/completions",headers=headers,
                    json={"messages":build_messages(system,history,bot_name,game_ctx),**attempt},timeout=18.0)
            if resp.status_code == 200:
                raw = resp.json()["choices"][0]["message"]["content"].strip()
                for p in [f"{bot_name}:","Router-AI:","Groq-AI:","Assistant:"]:
                    if raw.startswith(p): raw = raw[len(p):].strip()
                return raw
        except: continue
    return "SKIP"

# ── Vision via OpenRouter (Llama 3.2 11B Vision) ─────────────

VISION_MODEL = "meta-llama/llama-3.2-11b-vision-instruct"

async def _openrouter_vision(prompt: str, image_data: str, max_tokens: int = 20) -> str | None:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key: print("❌ No OPENROUTER_API_KEY"); return None
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
                    "max_tokens": max_tokens,
                    "temperature": 0.4,
                },
                timeout=25.0
            )
        if resp.status_code != 200:
            print(f"Vision error {resp.status_code}: {resp.text[:200]}")
            return None
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"Vision exception: {e}")
        return None

async def describe_image(b64: str) -> str:
    raw = await _openrouter_vision("Describe this image in one short funny sentence.", b64, max_tokens=80)
    return f"[Image: {raw}]" if raw else "[Image uploaded]"

# ── AI trigger ────────────────────────────────────────────────

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
        max_w = 15 if is_game_event else 60
        words = groq_reply.split()
        if len(words) > max_w: groq_reply = " ".join(words[:max_w]) + "…"
        groq_replied = True
        add_history(room, "Groq-AI", groq_reply)
        await manager.broadcast(groq_reply, "Groq-AI", room)

    await asyncio.sleep(1.5)
    history = sanitize_history_for_ai(room)
    router_reply = await fetch_openrouter("Router-AI", history, game_ctx, is_game=is_game_event)
    router_replied = False
    if not is_skip(router_reply):
        if router_reply.lower().startswith("router-ai:"): router_reply = router_reply[10:].strip()
        max_w = 15 if is_game_event else 60
        words = router_reply.split()
        if len(words) > max_w: router_reply = " ".join(words[:max_w]) + "…"
        router_replied = True
        add_history(room, "Router-AI", router_reply)
        await manager.broadcast(router_reply, "Router-AI", room)

    if (groq_replied or router_replied) and not is_game_event:
        await trigger_ai(room, chain+1, is_game_event=False)

# ── Scribble Vision Guessing ──────────────────────────────────

async def scribble_ai_guess(room, canvas_image, hint, word_length):
    """Both AIs guess using OpenRouter Llama 3.2 11B Vision."""
    prompt1 = (
        f"This is a Pictionary drawing. The word has {word_length} letters. "
        f"Letters revealed so far: \"{hint}\" "
        f"Look at the drawing and guess the single word it shows. "
        f"Reply with ONLY one word. No explanation, no punctuation."
    )
    raw1 = await _openrouter_vision(prompt1, canvas_image, max_tokens=10)
    groq_guess = None
    if raw1:
        w = raw1.split()[0] if raw1.split() else raw1
        groq_guess = w.strip(".,!?\"'()[]{}:;").lower()

    if groq_guess and groq_guess not in ("skip",""):
        msg = json.dumps({
            "sender":"Groq-AI",
            "message":f'__SCRIBBLE__:{json.dumps({"event":"ai_guess","guesser":"Groq-AI","guess":groq_guess})}',
            "image":None,"room":room
        })
        for conn in list(manager.rooms.get(room,[])):
            try: await conn.send_text(msg)
            except: pass

    await asyncio.sleep(random.uniform(2.5, 4.5))

    prompt2 = (
        f"Look at this Pictionary drawing. Guess what single word it represents. "
        f"The word is {word_length} letters long. Hint: \"{hint}\" "
        f"Your answer must be ONE word only."
    )
    raw2 = await _openrouter_vision(prompt2, canvas_image, max_tokens=10)
    router_guess = None
    if raw2:
        w = raw2.split()[0] if raw2.split() else raw2
        router_guess = w.strip(".,!?\"'()[]{}:;").lower()

    if router_guess and router_guess not in ("skip","") and router_guess != groq_guess:
        msg = json.dumps({
            "sender":"Router-AI",
            "message":f'__SCRIBBLE__:{json.dumps({"event":"ai_guess","guesser":"Router-AI","guess":router_guess})}',
            "image":None,"room":room
        })
        for conn in list(manager.rooms.get(room,[])):
            try: await conn.send_text(msg)
            except: pass


# ── Scribble AI Drawing ───────────────────────────────────────

SCRIBBLE_WORD_BANK = [
    'apple','banana','car','dog','elephant','fish','guitar','house','jellyfish','kite',
    'lion','moon','octopus','pizza','robot','sun','tree','umbrella','violin','whale','zebra',
    'airplane','butterfly','castle','dinosaur','flower','ghost','helicopter','kangaroo','laptop',
    'mushroom','ninja','owl','penguin','rainbow','spider','tornado','unicorn','volcano','dragon',
    'rocket','camera','diamond','grapes','hammer','igloo','key','lamp','mountain','parachute',
    'skateboard','telescope','star','sword','crown','anchor','balloon','candle','drum','flag',
    'globe','heart','leaf','magnet','pencil','rose','snowflake','tent','cat','bird','frog',
    'snake','turtle','rabbit','bear','bee','eagle','fox','giraffe','horse','koala','monkey',
    'panda','shark','tiger','wolf','boat','bridge','bus','chair','clock','door','glasses',
    'hat','ladder','mirror','piano','scissors','table','train','truck','basketball','football',
    'trophy','fire','lightning','cloud','rain','snow','beach','forest','cave','river',
    'lighthouse','burger','taco','sushi','cake','cookie','donut','popcorn','bicycle','compass',
    'backpack','shoe','watch','bell','cherry','lemon','strawberry','pineapple','carrot','tomato',
]

# ── SUPER DETAILED STROKE PROMPT ──────────────────────────────
STROKE_PROMPT = """You must generate canvas 2D drawing commands to draw "{word}" for a Pictionary game.
Canvas: 520 wide, 380 tall. Center is (260,190). Keep within 20px margins.

COMMAND FORMATS (strict JSON, no extra fields):
Line:           {{"t":"l","x1":N,"y1":N,"x2":N,"y2":N,"c":"#hex","w":N}}
Circle:         {{"t":"c","x":N,"y":N,"r":N,"c":"#hex","w":N}}
Filled circle:  {{"t":"c","x":N,"y":N,"r":N,"c":"#hex","w":N,"fill":"#hex"}}
Rectangle:      {{"t":"r","x":N,"y":N,"w":N,"h":N,"c":"#hex","w":N}}  (x,y = top-left corner)
Filled rect:    {{"t":"r","x":N,"y":N,"w":N,"h":N,"c":"#hex","w":N,"fill":"#hex"}}
Arc:            {{"t":"a","x":N,"y":N,"r":N,"s":N,"e":N,"c":"#hex","w":N}}  (s/e in radians)
Bezier:         {{"t":"b","x1":N,"y1":N,"cx1":N,"cy1":N,"cx2":N,"cy2":N,"x2":N,"y2":N,"c":"#hex","w":N}}
Polyline:       {{"t":"p","pts":[[x,y],[x,y],...],"c":"#hex","w":N}}
Closed poly:    {{"t":"p","pts":[[x,y],...],"c":"#hex","w":N,"close":true,"fill":"#hex"}}

Use 20-40 commands. Build up the drawing step by step — bigger shapes first, details after.
Use realistic colors. Make it recognizable from a distance.

SPECIFIC DRAWING GUIDES (follow exactly for the word given):

sun: filled yellow circle center (260,190,r=55,"fill":"#FFD700"), then 8 lines radiating outward (length 30px each) at 0,45,90,135,180,225,270,315 degrees from edge of circle
house: filled beige rect body (x:130,y:180,w:240,h:140,"fill":"#F5DEB3"), filled brown triangle roof as closed poly pts from (110,180) to (260,100) to (410,180), brown rect door (x:230,y:260,w:60,h:60,"fill":"#8B4513"), 2 yellow square windows (x:155,y:210,w:40,h:35,"fill":"#FFD700") and (x:325,y:210,w:40,h:35,"fill":"#FFD700")
cat: filled orange circle head (260,165,r=55,"fill":"#FFA500"), filled orange oval body as rect (x:195,y:205,w:130,h:90,"fill":"#FFA500"), two filled triangle ears as closed polys, dot eyes (x:240,y=150,r=6,"fill":"#000") and (x:280,y:150,r=6,"fill":"#000"), small pink nose circle (260,168,r=5,"fill":"#FFB6C1"), whisker lines from nose going left and right 3 each, curved tail bezier line
dog: filled tan circle head (260,155,r=50,"fill":"#D2691E"), filled tan rect body (x:185,y:200,w:150,h:90,"fill":"#D2691E"), 2 filled floppy ear circles on sides of head, dot eyes, black nose, 4 short rect legs, curled tail line
tree: filled dark-brown rect trunk (x:235,y:240,w:50,h:100,"fill":"#8B4513"), 3 overlapping filled green circles for foliage top — large (260,170,r=70,"fill":"#228B22"), medium-left (215,190,r=50,"fill":"#2E8B57"), medium-right (305,190,r=50,"fill":"#32CD32")
fish: filled blue oval body using rect (x:150,y:160,w:220,h:100,"fill":"#4169E1"), filled triangle tail as closed poly left of body, white circle eye (240,200,r=12,"fill":"#fff"), black pupil dot (240,200,r=5,"fill":"#000"), smile arc, fin lines
car: filled red rect body (x:90,y:195,w:340,h:90,"fill":"#CC0000"), filled dark rect windshield/cabin (x:160,y:155,w:180,h:55,"fill":"#88BBFF"), 2 filled black circles for wheels (155,285,r=35,"fill":"#222") and (365,285,r=35,"fill":"#222"), wheel rim circles inside wheels, headlight small yellow circles
airplane: filled gray rect fuselage (x:100,y:165,w:320,h:55,"fill":"#888"), filled gray closed poly left wing from (180,190) up to (120,130) right to (280,190), filled gray right wing (smaller), tail fin vertical closed poly, windows as small blue rects on fuselage, red/blue accent lines
star: filled yellow closed poly 10 points alternating outer r=110 and inner r=45 around center (260,190) — calculate each point at 36deg intervals starting at -90deg
heart: two bezier curves forming heart — left curve from (260,260) with controls up-left to (100,140) and (150,100) ending at (260,150); right curve from (260,150) with controls (370,100) and (420,140) to (260,260). Fill red "#CC0000"
pizza: filled large yellow-orange circle (260,190,r=140,"fill":"#F4A460"), filled red circle slice-shaped overlaps or red arcs for sauce, small colored circles for toppings (mushrooms brown, peppers green/red, cheese yellow), crust arc outline
butterfly: 4 filled rounded poly wings — upper two large colorful wings closed poly shapes, lower two smaller. Antenna lines from head up with small circles at tips. Body as small filled oval center
house: already done above
elephant: large filled gray circle body (260,200,r=95,"fill":"#808080"), gray circle head (165,175,r=55,"fill":"#808080"), long curved bezier trunk hanging down from head, large ear ellipse left side, small dot eye, 4 gray rect legs, small tail line
rocket: filled gray rect body (x:220,y:80,w:80,h:180,"fill":"#C0C0C0"), red/orange closed poly nose cone triangle at top, two filled red closed poly fins at bottom sides, orange/yellow filled circle thruster at very bottom, 3 small porthole blue circles on body
butterfly: done above

For "{word}" — plan what it looks like and draw every major part:
- Start with the largest filled shape (body/background)
- Add medium shapes (limbs, features, sections)
- Add small details last (eyes, patterns, accents)
- Use multiple strokes per part for thickness

RETURN ONLY A VALID JSON ARRAY. No text before or after. Start with [ end with ]."""


def _safe_clue(clue: str, word: str) -> str | None:
    """Return clue if it doesn't reveal the word, else None."""
    if word.lower() in clue.lower():
        return None
    return clue

def _make_clues(word: str) -> list[str]:
    """Generate clues that NEVER contain the word itself."""
    length = len(word)
    first = word[0].upper()
    last = word[-1].upper()
    mid = word[length//2].upper() if length > 2 else None
    vowels = sum(1 for c in word if c in 'aeiou')

    clues = [
        f"It has {length} letters",
        f"First letter is '{first}'",
        f"Last letter is '{last}'",
        f"Contains {vowels} vowel{'s' if vowels != 1 else ''}",
    ]
    if mid and mid not in (first, last):
        clues.append(f"Middle letter is '{mid}'")
    if length > 5:
        clues.append(f"It's a {length}-letter word — look at the drawing!")
    clues.append("Focus on the shapes being drawn...")

    return clues


async def generate_ai_strokes(word: str) -> list:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key: return []

    prompt = STROKE_PROMPT.format(word=word)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role":"user","content": prompt}],
                    "temperature": 0.3,   # lower temp = more precise drawing
                    "max_tokens": 2500,
                },
                timeout=30.0
            )
        if resp.status_code != 200:
            print(f"Stroke gen error {resp.status_code}: {resp.text[:200]}")
            return []

        raw = resp.json()["choices"][0]["message"]["content"].strip()

        # Extract JSON array
        start = raw.find('[')
        end = raw.rfind(']') + 1
        if start == -1 or end == 0: return []

        strokes = json.loads(raw[start:end])
        valid = []
        for s in strokes:
            if not isinstance(s, dict) or "t" not in s: continue
            # Clamp coordinates to canvas bounds
            valid.append(s)
        print(f"Generated {len(valid)} strokes for '{word}'")
        return valid[:45]

    except Exception as e:
        print(f"Stroke gen exception for '{word}': {e}")
        return []


def get_fallback_strokes(word: str) -> list:
    """Fallback: draw a question mark + word length hint."""
    cx, cy = 260, 190
    # Draw a big question mark using polyline
    strokes = [
        # Arc top of question mark
        {"t":"a","x":cx,"y":130,"r":50,"s":-2.8,"e":0.3,"c":"#7c6af7","w":8},
        # Curve going down
        {"t":"b","x1":cx+50,"y1":145,"cx1":cx+55,"cy1":175,"cx2":cx+10,"cy2":185,"x2":cx,"y2":210,"c":"#7c6af7","w":8},
        # Short line down
        {"t":"l","x1":cx,"y1":210,"x2":cx,"y2":230,"c":"#7c6af7","w":8},
        # Dot
        {"t":"c","x":cx,"y":255,"r":10,"c":"#7c6af7","w":2,"fill":"#7c6af7"},
    ]
    return strokes


async def scribble_ai_draw(room: str, drawer: str):
    word = random.choice(SCRIBBLE_WORD_BANK)

    room_game_state[room] = {
        "type": "scribble",
        "data": {"current_word": word, "drawer": drawer}
    }

    drawer_name = "Groq-AI" if drawer == "groq" else "Router-AI"
    clues = _make_clues(word)

    strokes = await generate_ai_strokes(word)
    if not strokes:
        print(f"Using fallback strokes for '{word}'")
        strokes = get_fallback_strokes(word)

    payload = json.dumps({
        "sender": drawer_name,
        "message": f'__SCRIBBLE__:{json.dumps({"event":"ai_draw_start","word":word,"clues":clues,"strokes":strokes,"drawer":drawer})}',
        "image": None,
        "room": room
    })

    for conn in list(manager.rooms.get(room, [])):
        try: await conn.send_text(payload)
        except: pass


# ── WebSocket endpoint ─────────────────────────────────────────

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
                    event = sd.get("event", "")

                    if event == "game_start":
                        add_history(room, username, "[SCRIBBLE started]")
                        asyncio.create_task(trigger_ai(room, 0, True))

                    elif event == "user_draw_start":
                        word = sd.get("word", "")
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
                        guess = sd.get("guess","")
                        add_history(room, username, f'[SCRIBBLE: {username} guessed "{guess}"]')

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