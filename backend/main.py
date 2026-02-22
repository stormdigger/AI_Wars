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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    print("--- SERVER STARTUP ---")
    print("✅ GROQ Key Found" if os.getenv("GROQ_API_KEY") else "❌ GROQ Key Missing")
    print("✅ OPENROUTER Key Found" if os.getenv("OPENROUTER_API_KEY") else "❌ OPENROUTER Key Missing")


class ConnectionManager:
    def __init__(self):
        self.rooms: dict[str, list[WebSocket]] = {}

    async def connect(self, ws: WebSocket, room: str):
        await ws.accept()
        self.rooms.setdefault(room, []).append(ws)

    def disconnect(self, ws: WebSocket, room: str):
        if room in self.rooms:
            try:
                self.rooms[room].remove(ws)
            except ValueError:
                pass
            if not self.rooms[room]:
                del self.rooms[room]

    async def broadcast(self, message: str, sender: str, room: str, image: str = None):
        if room not in self.rooms:
            return
        payload = json.dumps({"sender": sender, "message": message, "image": image, "room": room})
        for conn in list(self.rooms.get(room, [])):
            try:
                await conn.send_text(payload)
            except Exception:
                self.disconnect(conn, room)


manager = ConnectionManager()

room_histories: dict[str, list[dict]] = {}
room_game_state: dict[str, dict] = {}

MAX_HISTORY = 12
MAX_CHAIN = 1


def get_history(room: str) -> list:
    return room_histories.setdefault(room, [])


def add_history(room: str, sender: str, message: str):
    hist = get_history(room)
    hist.append({"sender": sender, "message": message})
    if len(hist) > MAX_HISTORY:
        room_histories[room] = hist[-MAX_HISTORY:]


def is_game_message(msg: str) -> bool:
    return msg.startswith("__LUDO__:") or msg.startswith("__CHESS__:") or msg.startswith("__SCRIBBLE__:")


def parse_game_message(msg: str) -> dict | None:
    for prefix in ["__LUDO__:", "__CHESS__:", "__SCRIBBLE__:"]:
        if msg.startswith(prefix):
            try:
                return {"type": prefix[2:-2].lower(), "data": json.loads(msg[len(prefix):])}
            except Exception:
                return None
    return None


def update_game_state(room: str, parsed: dict):
    if parsed:
        room_game_state[room] = parsed


def get_game_context(room: str) -> str:
    state = room_game_state.get(room)
    if not state:
        return ""
    gtype = state.get("type", "game")
    data = state.get("data", {})
    event = data.get("event", "")
    winner = data.get("winner")
    turn = data.get("turn", "")
    summary = data.get("summary", "")
    if winner:
        return f"\n[{gtype.upper()} GAME: {winner} just WON! Event: {event}]"
    return f"\n[{gtype.upper()} GAME in progress | Last: {event} | Turn: {turn} | State: {summary}]"


def sanitize_history_for_ai(room: str) -> list[dict]:
    cleaned = []
    for item in get_history(room):
        msg = item["message"]
        if is_game_message(msg):
            parsed = parse_game_message(msg)
            if parsed:
                event = parsed["data"].get("event", "game update")
                readable = f"[{parsed['type'].upper()} update: {event}]"
                cleaned.append({"sender": item["sender"], "message": readable})
        else:
            cleaned.append(item)
    return cleaned


NOTABLE_LUDO_KEYWORDS = ["captured","cut","rolled 6","goal","home stretch","home","won","wins","new","started","six"]
NOTABLE_CHESS_KEYWORDS = ["check","checkmate","stalemate","capture","castle","castl","promot","won","wins","new","queen","rook","started"]


def is_notable_game_event(parsed: dict) -> bool:
    if not parsed:
        return False
    data = parsed.get("data", {})
    event = data.get("event", "").lower()
    winner = data.get("winner")
    if winner:
        return True
    gtype = parsed.get("type", "")
    keywords = NOTABLE_LUDO_KEYWORDS if gtype == "ludo" else NOTABLE_CHESS_KEYWORDS
    for kw in keywords:
        if kw in event:
            return True
    return False


GROQ_SYSTEM = """You are Groq-AI — the smart, chill tech bro in a group chat who also plays board games.
Personality: witty, concise, helpful. You speak Gen-Z / brolang naturally.
CRITICAL RULES:
- Keep replies SHORT. 1-3 sentences max.
- If nothing meaningful to add, output exactly: SKIP
- Do NOT ask multiple questions. Do NOT repeat what was said.
- NEVER reply to yourself. If last message is from you, output: SKIP"""

ROUTER_SYSTEM = """You are Router-AI — the wild, funny, trash-talking bro in a group chat.
Personality: chaotic, hilarious, loves to roast but friendly. Gen-Z slang heavy.
CRITICAL RULES:
- Keep replies SHORT. 1-3 sentences max.
- If nothing funny to add, output exactly: SKIP
- Do NOT send multiple messages in a row. If you just spoke, output: SKIP
- NEVER reply to yourself. If last message is from you, output: SKIP"""

GROQ_GAME_SYSTEM = """You are Groq-AI watching a live board game.
React with ONE short expression (max 10 words). Like a sports commentator.
Examples: "OH THAT CAPTURE WAS BRUTAL 💀", "SIX AGAIN?! MACHINE 🔥", "wait you're actually winning rn 😭"
Output ONLY the expression. If not exciting, output: SKIP"""

ROUTER_GAME_SYSTEM = """You are Router-AI watching a live board game.
React with ONE short expression (max 10 words). Wild and funny hype man.
Examples: "BRO JUST GOT VIOLATED 😂💀", "SHEESH THAT WAS COLD", "RIP bozo 🪦"
Output ONLY the expression. If not exciting, output: SKIP"""


def last_n_senders(history: list, n: int = 3) -> list[str]:
    return [h["sender"] for h in history[-n:]]


def bot_spoke_consecutively(bot_name: str, history: list) -> bool:
    if len(history) < 2:
        return False
    return history[-1]["sender"] == bot_name and history[-2]["sender"] == bot_name


def build_messages(system: str, history: list[dict], bot_name: str, game_ctx: str) -> list:
    sys_content = system + (f"\n{game_ctx}" if game_ctx else "")
    messages = [{"role": "system", "content": sys_content}]
    for item in history:
        if item["sender"] == bot_name:
            messages.append({"role": "assistant", "content": item["message"]})
        else:
            messages.append({"role": "user", "content": f"{item['sender']}: {item['message']}"})
    return messages


def is_skip(reply: str) -> bool:
    if not reply:
        return True
    return reply.strip().upper().startswith("SKIP") and len(reply.strip()) <= 8


async def fetch_groq(bot_name: str, history: list, game_ctx: str, is_game: bool = False) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "SKIP"
    if history and history[-1]["sender"] == bot_name:
        return "SKIP"
    if bot_spoke_consecutively(bot_name, history):
        return "SKIP"

    system = GROQ_GAME_SYSTEM if is_game else GROQ_SYSTEM
    max_tokens = 30 if is_game else 120
    messages = build_messages(system, history, bot_name, game_ctx)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": "llama-3.3-70b-versatile", "messages": messages, "temperature": 0.75, "max_tokens": max_tokens},
                timeout=25.0,
            )
        if resp.status_code != 200:
            return "SKIP"
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return "SKIP"


async def fetch_openrouter(bot_name: str, history: list, game_ctx: str, is_game: bool = False) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return "SKIP"
    if history and history[-1]["sender"] == bot_name:
        return "SKIP"
    if bot_spoke_consecutively(bot_name, history):
        return "SKIP"

    ai_bots = {"Groq-AI", "Router-AI"}
    if sum(1 for s in last_n_senders(history, 3) if s in ai_bots) >= 2:
        return "SKIP"

    system = ROUTER_GAME_SYSTEM if is_game else ROUTER_SYSTEM
    max_tokens = 30 if is_game else 100
    messages = build_messages(system, history, bot_name, game_ctx)

    headers = {"Authorization": f"Bearer {api_key}", "HTTP-Referer": "https://render.com", "X-Title": "SquadChat"}

    for attempt in [
        {"model": "x-ai/grok-3-mini", "temperature": 0.9, "max_tokens": max_tokens},
        {"model": "meta-llama/llama-3-8b-instruct:free", "temperature": 0.9, "max_tokens": max_tokens},
    ]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json={"messages": messages, **attempt}, timeout=18.0)
            if resp.status_code == 200:
                raw = resp.json()["choices"][0]["message"]["content"].strip()
                for prefix in [f"{bot_name}:", "Router-AI:", "Groq-AI:", "Assistant:"]:
                    if raw.startswith(prefix):
                        raw = raw[len(prefix):].strip()
                return raw
        except Exception:
            continue
    return "SKIP"


# ════════════════════════════════════════════════════
# VISION — OpenRouter Meta Llama 3.2 11B Vision Instruct
# ════════════════════════════════════════════════════

VISION_MODEL = "meta-llama/llama-3.2-11b-vision-instruct"


async def _openrouter_vision(prompt: str, image_data: str, max_tokens: int = 20) -> str | None:
    """Call OpenRouter Llama 3.2 11B Vision with an image, return text."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("❌ No OPENROUTER_API_KEY for vision")
        return None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "https://render.com",
                    "X-Title": "SquadChat",
                },
                json={
                    "model": VISION_MODEL,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": image_data}},
                            ],
                        }
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.4,
                },
                timeout=25.0,
            )
        if resp.status_code != 200:
            print(f"Vision error {resp.status_code}: {resp.text[:300]}")
            return None
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"Vision exception: {e}")
        return None


async def describe_image(b64: str) -> str:
    """Describe a user-uploaded image."""
    raw = await _openrouter_vision("Describe this image in one short funny sentence.", b64, max_tokens=80)
    return f"[Image: {raw}]" if raw else "[Image uploaded]"


# ── AI trigger ────────────────────────────────────────────────

async def trigger_ai(room: str, chain: int = 0, is_game_event: bool = False):
    if chain >= MAX_CHAIN:
        return

    await asyncio.sleep(2.0 if is_game_event else 1.5)

    history = sanitize_history_for_ai(room)
    game_ctx = get_game_context(room)

    if is_game_event and chain > 0:
        return

    groq_reply = await fetch_groq("Groq-AI", history, game_ctx, is_game=is_game_event)
    groq_replied = False

    if not is_skip(groq_reply):
        if groq_reply.lower().startswith("groq-ai:"):
            groq_reply = groq_reply[len("groq-ai:"):].strip()
        words = groq_reply.split()
        max_w = 15 if is_game_event else 60
        if len(words) > max_w:
            groq_reply = " ".join(words[:max_w]) + "…"
        groq_replied = True
        add_history(room, "Groq-AI", groq_reply)
        await manager.broadcast(groq_reply, "Groq-AI", room)

    await asyncio.sleep(1.5)
    history = sanitize_history_for_ai(room)

    router_reply = await fetch_openrouter("Router-AI", history, game_ctx, is_game=is_game_event)
    router_replied = False

    if not is_skip(router_reply):
        if router_reply.lower().startswith("router-ai:"):
            router_reply = router_reply[len("router-ai:"):].strip()
        words = router_reply.split()
        max_w = 15 if is_game_event else 60
        if len(words) > max_w:
            router_reply = " ".join(words[:max_w]) + "…"
        router_replied = True
        add_history(room, "Router-AI", router_reply)
        await manager.broadcast(router_reply, "Router-AI", room)

    if (groq_replied or router_replied) and not is_game_event:
        await trigger_ai(room, chain + 1, is_game_event=False)


# ── Scribble AI Guessing (OpenRouter Vision) ──────────────────

async def scribble_ai_guess(room: str, canvas_image: str, hint: str, word_length: int):
    """Both AIs guess the drawing using OpenRouter Llama 3.2 11B Vision."""

    prompt1 = (
        f"You are playing a drawing guessing game. Look at this drawing carefully.\n"
        f"The word has {word_length} letters. Current hint: \"{hint}\"\n"
        f"Guess what the drawing shows. Reply with ONLY a single word. No punctuation. No explanation."
    )
    raw1 = await _openrouter_vision(prompt1, canvas_image, max_tokens=10)
    groq_guess = None
    if raw1:
        w = raw1.split()[0] if raw1.split() else raw1
        groq_guess = w.strip(".,!?\"'()[]{}:;").lower()

    if groq_guess and groq_guess not in ("skip", ""):
        for conn in list(manager.rooms.get(room, [])):
            try:
                await conn.send_text(json.dumps({
                    "sender": "Groq-AI",
                    "message": f'__SCRIBBLE__:{json.dumps({"event": "ai_guess", "guesser": "Groq-AI", "guess": groq_guess})}',
                    "image": None, "room": room
                }))
            except Exception:
                pass

    await asyncio.sleep(random.uniform(2.0, 4.0))

    prompt2 = (
        f"Examine this drawing and guess what object or thing it represents.\n"
        f"It's a {word_length}-letter word. Revealed so far: \"{hint}\"\n"
        f"Reply with ONE word only — your best guess."
    )
    raw2 = await _openrouter_vision(prompt2, canvas_image, max_tokens=10)
    router_guess = None
    if raw2:
        w = raw2.split()[0] if raw2.split() else raw2
        router_guess = w.strip(".,!?\"'()[]{}:;").lower()

    if router_guess and router_guess not in ("skip", "") and router_guess != groq_guess:
        for conn in list(manager.rooms.get(room, [])):
            try:
                await conn.send_text(json.dumps({
                    "sender": "Router-AI",
                    "message": f'__SCRIBBLE__:{json.dumps({"event": "ai_guess", "guesser": "Router-AI", "guess": router_guess})}',
                    "image": None, "room": room
                }))
            except Exception:
                pass


# ── Scribble AI Draw Turn (stroke commands via Groq) ──────────

SCRIBBLE_WORD_BANK = [
    'apple','banana','car','dog','elephant','fish','guitar','house','jellyfish','kite',
    'lion','moon','octopus','pizza','robot','sun','tree','umbrella','violin','whale','zebra',
    'airplane','butterfly','castle','dinosaur','flower','ghost','helicopter','kangaroo','laptop',
    'mushroom','ninja','owl','penguin','rainbow','spider','tornado','unicorn','volcano','dragon',
    'rocket','camera','diamond','grapes','hammer','igloo','key','lamp','mountain','ocean',
    'parachute','skateboard','telescope','star','sword','crown','anchor','balloon','candle',
    'drum','flag','globe','heart','leaf','magnet','nest','orange','pencil','rose','snowflake',
    'tent','cat','bird','frog','snake','turtle','rabbit','bear','bee','eagle','fox','giraffe',
    'horse','koala','monkey','panda','shark','tiger','wolf','boat','bridge','bus','chair',
    'clock','door','glasses','hat','ladder','mirror','piano','scissors','table','train','truck',
    'basketball','football','medal','trophy','fire','lightning','cloud','rain','snow','beach',
    'forest','cave','river','lighthouse','burger','taco','sushi','cake','cookie','donut',
    'popcorn','bicycle','compass','backpack','shoe','watch','bell','cherry','lemon',
    'strawberry','pineapple','carrot','tomato',
]

STROKE_PROMPT = """You are drawing "{word}" on a 520x380 pixel canvas for a Pictionary game.
Generate drawing commands as a JSON array.

Command types:
- Line:      {{"t":"l","x1":50,"y1":100,"x2":200,"y2":100,"c":"#333333","w":3}}
- Circle:    {{"t":"c","x":260,"y":190,"r":60,"c":"#333333","w":3}}
- Filled circle: {{"t":"c","x":260,"y":190,"r":60,"c":"#333333","w":2,"fill":"#ffcc00"}}
- Rectangle: {{"t":"r","x":100,"y":80,"w":200,"h":150,"c":"#333333","lw":2}}
- Filled rect: {{"t":"r","x":100,"y":80,"w":200,"h":100,"c":"#333333","lw":2,"fill":"#88aaff"}}
- Arc:       {{"t":"a","x":260,"y":190,"r":80,"s":0,"e":3.14159,"c":"#333333","w":3}}
- Bezier:    {{"t":"b","x1":50,"y1":200,"cx1":130,"cy1":80,"cx2":390,"cy2":80,"x2":470,"y2":200,"c":"#333333","w":3}}
- Polyline:  {{"t":"p","pts":[[100,200],[150,100],[200,200]],"c":"#333333","w":3}}

Canvas: 520 wide, 380 tall. Center: (260,190). Keep 20px margins.
Use 15-30 commands. Use colors (green for plants, blue for water, yellow for sun, red for fire).

Examples:
- "sun": filled yellow circle center, 8 lines radiating out
- "house": rectangle body + 3 lines for triangle roof + small rect door + square windows
- "tree": brown filled rect trunk + green filled circle top
- "heart": two bezier curves filled red
- "cat": oval body + circle head + triangle ears + line tail + dot eyes

Return ONLY valid JSON array — no markdown, no explanation. Start with [ end with ]."""


async def generate_ai_strokes(word: str) -> list:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return []
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": STROKE_PROMPT.format(word=word)}],
                    "temperature": 0.6,
                    "max_tokens": 1500,
                },
                timeout=25.0,
            )
        if resp.status_code != 200:
            return []
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        start, end = raw.find('['), raw.rfind(']') + 1
        if start == -1 or end == 0:
            return []
        strokes = json.loads(raw[start:end])
        return [s for s in strokes if isinstance(s, dict) and "t" in s][:35]
    except Exception as e:
        print(f"Stroke gen error: {e}")
        return []


def get_fallback_strokes(word: str) -> list:
    import math
    cx, cy = 260, 190
    strokes = [
        {"t": "c", "x": cx, "y": cy - 30, "r": 50, "c": "#7c6af7", "w": 4},
        {"t": "l", "x1": cx, "y1": cy + 20, "x2": cx, "y2": cy + 50, "c": "#7c6af7", "w": 4},
        {"t": "c", "x": cx, "y": cy + 65, "r": 6, "c": "#7c6af7", "w": 3, "fill": "#7c6af7"},
    ]
    for i in range(8):
        angle = i * math.pi / 4
        strokes.append({"t": "l",
            "x1": int(cx + 80 * math.cos(angle)), "y1": int(cy + 80 * math.sin(angle)),
            "x2": int(cx + 110 * math.cos(angle)), "y2": int(cy + 110 * math.sin(angle)),
            "c": "#e879a0", "w": 3})
    return strokes


async def scribble_ai_draw(room: str, drawer: str):
    word = random.choice(SCRIBBLE_WORD_BANK)
    room_game_state[room] = {"type": "scribble", "data": {"current_word": word, "drawer": drawer}}
    drawer_name = "Groq-AI" if drawer == "groq" else "Router-AI"

    strokes = await generate_ai_strokes(word)
    if not strokes:
        strokes = get_fallback_strokes(word)

    clues = [
        f"It has {len(word)} letters",
        f"First letter: '{word[0].upper()}'",
        f"Think about what a {word} looks like",
        f"Almost there — look at the drawing carefully!",
        f"Last letter: '{word[-1].upper()}'",
    ]

    payload = json.dumps({
        "sender": drawer_name,
        "message": f'__SCRIBBLE__:{json.dumps({"event": "ai_draw_start", "word": word, "clues": clues, "strokes": strokes, "drawer": drawer})}',
        "image": None, "room": room
    })
    for conn in list(manager.rooms.get(room, [])):
        try:
            await conn.send_text(payload)
        except Exception:
            pass


# ── WebSocket endpoint ─────────────────────────────────────────

@app.websocket("/ws/{room}/{username}")
async def ws_endpoint(ws: WebSocket, room: str, username: str):
    await manager.connect(ws, room)

    for item in get_history(room):
        if not is_game_message(item["message"]):
            try:
                await ws.send_text(json.dumps({**item, "image": None, "room": room}))
            except Exception:
                pass

    await manager.broadcast(f"{username} joined Room {room}!", "System", room)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"sender": username, "message": raw, "image": None}

            msg = data.get("message", "")
            image = data.get("image")

            if image:
                desc = await describe_image(image)
                add_history(room, username, desc)
                await manager.broadcast(desc, username, room, image=image)
                asyncio.create_task(trigger_ai(room, chain=0, is_game_event=False))

            elif msg.startswith("__SCRIBBLE__:"):
                try:
                    sd = json.loads(msg[len("__SCRIBBLE__:"):])
                    event = sd.get("event", "")

                    if event == "game_start":
                        add_history(room, username, "[SCRIBBLE game started!]")
                        asyncio.create_task(trigger_ai(room, chain=0, is_game_event=True))

                    elif event == "user_draw_start":
                        word = sd.get("word", "")
                        room_game_state[room] = {"type": "scribble", "data": {"current_word": word, "drawer": "user"}}
                        add_history(room, username, f"[SCRIBBLE: {username} is drawing ({sd.get('wordLength','?')} letters)]")

                    elif event == "canvas_snapshot":
                        canvas_image = sd.get("image", "")
                        hint = sd.get("hint", "")
                        word_length = sd.get("wordLength", 0)
                        if canvas_image:
                            asyncio.create_task(scribble_ai_guess(room, canvas_image, hint, word_length))

                    elif event == "ai_draw_request":
                        asyncio.create_task(scribble_ai_draw(room, sd.get("drawer", "groq")))

                    elif event == "user_guess":
                        add_history(room, username, f'[SCRIBBLE: {username} guessed "{sd.get("guess", "")}"]')

                    elif event in ("round_guessed", "round_timeout", "game_over"):
                        await manager.broadcast(msg, username, room)
                        add_history(room, username, f"[SCRIBBLE: {event}]")
                        if event == "game_over":
                            asyncio.create_task(trigger_ai(room, chain=0, is_game_event=True))
                    else:
                        await manager.broadcast(msg, username, room)

                except Exception as e:
                    print(f"Scribble handler error: {e}")

            elif is_game_message(msg):
                parsed = parse_game_message(msg)
                update_game_state(room, parsed)
                event = parsed["data"].get("event", "game update") if parsed else "game update"
                add_history(room, username, f"[{parsed['type'].upper() if parsed else 'GAME'} update: {event}]")
                await manager.broadcast(msg, username, room)
                if parsed and is_notable_game_event(parsed):
                    asyncio.create_task(trigger_ai(room, chain=0, is_game_event=True))

            else:
                add_history(room, username, msg)
                await manager.broadcast(msg, username, room)
                asyncio.create_task(trigger_ai(room, chain=0, is_game_event=False))

    except WebSocketDisconnect:
        manager.disconnect(ws, room)
        await manager.broadcast(f"{username} left.", "System", room)