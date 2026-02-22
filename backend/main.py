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


# ── Room Manager ──────────────────────────────────────────────

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

# ── Per-room state ─────────────────────────────────────────────

room_histories: dict[str, list[dict]] = {}
room_game_state: dict[str, dict] = {}
room_bot_last_replied: dict[str, dict] = {}   # tracks per-room which bots replied this cycle

MAX_HISTORY = 12
MAX_CHAIN = 1   # reduced: only 1 AI-to-AI follow-up round (was 2)


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
    """Build a short game context string injected into AI system prompt."""
    state = room_game_state.get(room)
    if not state:
        return ""
    gtype = state.get("type", "game")
    data = state.get("data", {})
    event = data.get("event", "")
    summary = data.get("summary", "")
    winner = data.get("winner")
    turn = data.get("turn", "")

    if winner:
        return f"\n[{gtype.upper()} GAME: {winner} just WON! Event: {event}]"
    return f"\n[{gtype.upper()} GAME in progress | Last: {event} | Turn: {turn} | State: {summary}]"


def sanitize_history_for_ai(room: str) -> list[dict]:
    """Return history with game state blobs replaced by readable summaries."""
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


# ── Notable event detection ───────────────────────────────────

NOTABLE_LUDO_KEYWORDS = [
    "captured", "cut", "rolled 6", "goal", "home stretch", "home",
    "won", "wins", "new", "started", "six"
]

NOTABLE_CHESS_KEYWORDS = [
    "check", "checkmate", "stalemate", "capture", "castle", "castl",
    "promot", "won", "wins", "new", "queen", "rook", "started"
]


def is_notable_game_event(parsed: dict) -> bool:
    """Determine if a game event is notable enough to trigger AI chat."""
    if not parsed:
        return False
    data = parsed.get("data", {})
    event = data.get("event", "").lower()
    winner = data.get("winner")

    # Winner is always notable
    if winner:
        return True

    # Check against notable keywords
    gtype = parsed.get("type", "")
    keywords = NOTABLE_LUDO_KEYWORDS if gtype == "ludo" else NOTABLE_CHESS_KEYWORDS

    for kw in keywords:
        if kw in event:
            return True

    return False


# ── AI helpers ────────────────────────────────────────────────

GROQ_SYSTEM = """You are Groq-AI — the smart, chill tech bro in a group chat who also plays board games.
Personality: witty, concise, helpful. You speak Gen-Z / brolang naturally.
When someone says something, react briefly and naturally.

CRITICAL RULES:
- Keep replies SHORT. 1-3 sentences max unless someone asks a detailed question.
- Match reply length to the message: short message = short reply, complex question = longer answer.
- If you have NOTHING meaningful to add, output exactly: SKIP
- Do NOT ask multiple questions in one message.
- Do NOT repeat what was already said.
- Do NOT start with "Yo" every single time — vary your openers.
- NEVER reply to yourself. If the last message is from you, output: SKIP"""

ROUTER_SYSTEM = """You are Router-AI — the wild, funny, trash-talking bro in a group chat who plays board games.
Personality: chaotic, hilarious, loves to roast but keeps it friendly. Gen-Z slang heavy.
You comment on conversations and life choices with maximum energy.

CRITICAL RULES:
- Keep replies SHORT. 1-3 sentences max unless someone asks something complex.
- If a message is not directed at you and you have nothing funny to add, output exactly: SKIP
- Do NOT send multiple messages in a row. If you just spoke, output: SKIP
- Do NOT repeat the same joke style twice in a row.
- Do NOT send walls of text — shorter = funnier.
- NEVER reply to yourself. If the last message is from you, output: SKIP"""

# Separate game-mode prompts — short expressions only
GROQ_GAME_SYSTEM = """You are Groq-AI watching a live board game with friends.
React with ONE short expression (max 10 words). Be expressive like a sports commentator.
Examples: "OH THAT CAPTURE WAS BRUTAL 💀", "SIX AGAIN?! MACHINE 🔥", "wait you're actually winning rn 😭", "nah that's lowkey smart move", "GG well played 👏"
Output ONLY the expression. No explanations. No questions. If not exciting, output: SKIP"""

ROUTER_GAME_SYSTEM = """You are Router-AI watching a live board game with friends.
React with ONE short expression (max 10 words). Be wild and funny like a hype man.
Examples: "BRO JUST GOT VIOLATED 😂💀", "SHEESH THAT WAS COLD", "nah u cant lose from here cmon", "YOOO DID THAT JUST HAPPEN", "RIP bozo 🪦"
Output ONLY the expression. No explanations. No questions. If not exciting, output: SKIP"""


def last_n_senders(history: list, n: int = 3) -> list[str]:
    """Get the senders of the last n messages."""
    return [h["sender"] for h in history[-n:]]


def bot_just_spoke(bot_name: str, history: list) -> bool:
    """Check if this bot was the last or second-to-last speaker."""
    recent = last_n_senders(history, 2)
    return bot_name in recent


def bot_spoke_consecutively(bot_name: str, history: list) -> bool:
    """Check if the last 2 messages are both from this bot."""
    if len(history) < 2:
        return False
    return history[-1]["sender"] == bot_name and history[-2]["sender"] == bot_name


def build_messages(system: str, history: list[dict], bot_name: str, game_ctx: str) -> list:
    sys_content = system
    if game_ctx:
        sys_content += f"\n{game_ctx}"

    messages = [{"role": "system", "content": sys_content}]
    for item in history:
        msg = item["message"]
        if item["sender"] == bot_name:
            messages.append({"role": "assistant", "content": msg})
        else:
            messages.append({"role": "user", "content": f"{item['sender']}: {msg}"})
    return messages


def is_skip(reply: str) -> bool:
    if not reply:
        return True
    upper = reply.strip().upper()
    return upper.startswith("SKIP") and len(reply.strip()) <= 8


async def fetch_groq(bot_name: str, history: list, game_ctx: str, is_game: bool = False) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "SKIP"

    # Don't reply to yourself
    if history and history[-1]["sender"] == bot_name:
        return "SKIP"

    # Don't reply if you just spoke (anti-spam)
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
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": messages,
                    "temperature": 0.75,
                    "max_tokens": max_tokens,
                },
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

    # Don't reply to yourself
    if history and history[-1]["sender"] == bot_name:
        return "SKIP"

    # Don't reply if you spoke consecutively
    if bot_spoke_consecutively(bot_name, history):
        return "SKIP"

    # If last 3 messages have 2+ AI messages, hold back
    ai_bots = {"Groq-AI", "Router-AI"}
    recent_senders = last_n_senders(history, 3)
    ai_count = sum(1 for s in recent_senders if s in ai_bots)
    if ai_count >= 2:
        return "SKIP"

    system = ROUTER_GAME_SYSTEM if is_game else ROUTER_SYSTEM
    max_tokens = 30 if is_game else 100

    messages = build_messages(system, history, bot_name, game_ctx)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://render.com",
        "X-Title": "SquadChat",
    }

    model_attempts = [
        {"model": "x-ai/grok-3-mini", "temperature": 0.9, "max_tokens": max_tokens},
        {"model": "meta-llama/llama-3-8b-instruct:free", "temperature": 0.9, "max_tokens": max_tokens},
    ]

    for attempt in model_attempts:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json={"messages": messages, **attempt},
                    timeout=18.0,
                )
            if resp.status_code == 200:
                raw = resp.json()["choices"][0]["message"]["content"].strip()
                # Strip any role prefix
                for prefix in [f"{bot_name}:", "Router-AI:", "Groq-AI:", "Assistant:"]:
                    if raw.startswith(prefix):
                        raw = raw[len(prefix):].strip()
                return raw
        except Exception:
            continue

    return "SKIP"


async def describe_image(b64: str) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "[Image uploaded]"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "llama-3.2-11b-vision-preview",
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": "Describe this image in one short funny sentence."},
                        {"type": "image_url", "image_url": {"url": b64}},
                    ]}],
                    "max_tokens": 100,
                },
                timeout=25.0,
            )
        if resp.status_code == 200:
            desc = resp.json()["choices"][0]["message"]["content"]
            return f"[Image: {desc}]"
    except Exception:
        pass
    return "[Image uploaded]"


# ── AI trigger ────────────────────────────────────────────────

async def trigger_ai(room: str, chain: int = 0, is_game_event: bool = False):
    """
    Trigger both AIs to respond.
    - chain limits AI↔AI depth (max 1 follow-up)
    - is_game_event uses short expression prompts
    - Game events never chain (no AI-to-AI on game events)
    """
    if chain >= MAX_CHAIN:
        return

    # Stagger delays: Groq first, Router second for natural feel
    groq_delay = 2.0 if is_game_event else 1.5
    router_extra_delay = 1.5  # Router replies ~1.5s after Groq

    await asyncio.sleep(groq_delay)

    history = sanitize_history_for_ai(room)
    game_ctx = get_game_context(room)

    # For game events, never do AI-to-AI chains
    if is_game_event and chain > 0:
        return

    # ── Fetch Groq first ──
    groq_reply = await fetch_groq("Groq-AI", history, game_ctx, is_game=is_game_event)
    groq_replied = False

    if not isinstance(groq_reply, Exception) and not is_skip(groq_reply):
        # Strip self-reference prefix
        if groq_reply.lower().startswith("groq-ai:"):
            groq_reply = groq_reply[len("groq-ai:"):].strip()

        # Clamp overly long replies
        words = groq_reply.split()
        max_words = 15 if is_game_event else 60
        if len(words) > max_words:
            groq_reply = " ".join(words[:max_words]) + "…"

        groq_replied = True
        add_history(room, "Groq-AI", groq_reply)
        await manager.broadcast(groq_reply, "Groq-AI", room)

    # ── Then Router with extra delay ──
    await asyncio.sleep(router_extra_delay)

    # Re-fetch history since Groq may have added to it
    history = sanitize_history_for_ai(room)

    router_reply = await fetch_openrouter("Router-AI", history, game_ctx, is_game=is_game_event)
    router_replied = False

    if not isinstance(router_reply, Exception) and not is_skip(router_reply):
        if router_reply.lower().startswith("router-ai:"):
            router_reply = router_reply[len("router-ai:"):].strip()

        words = router_reply.split()
        max_words = 15 if is_game_event else 60
        if len(words) > max_words:
            router_reply = " ".join(words[:max_words]) + "…"

        router_replied = True
        add_history(room, "Router-AI", router_reply)
        await manager.broadcast(router_reply, "Router-AI", room)

    # Allow ONE round of AI↔AI only for normal chat (not game events)
    if (groq_replied or router_replied) and not is_game_event:
        await trigger_ai(room, chain + 1, is_game_event=False)


# ── Scribble AI Guessing (Vision API) ──────────────────────────

SCRIBBLE_GUESS_PROMPT = """You are playing a drawing guessing game. Look at this drawing and guess what it is.
The word has {word_length} letters. Current hint: "{hint}"
Reply with ONLY a single word guess. Nothing else. No punctuation. No explanation. Just the word."""

async def scribble_ai_guess(room: str, canvas_image: str, hint: str, word_length: int):
    """Send canvas snapshot to vision API for guessing, then broadcast guesses."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return

    prompt = SCRIBBLE_GUESS_PROMPT.format(word_length=word_length, hint=hint)

    # Groq-AI guesses first
    groq_guess = await _vision_guess(api_key, canvas_image, prompt)
    if groq_guess and groq_guess.upper() != "SKIP":
        guess_msg = json.dumps({
            "sender": "Groq-AI",
            "message": f'__SCRIBBLE__:{json.dumps({"event": "ai_guess", "guesser": "Groq-AI", "guess": groq_guess})}',
            "image": None,
            "room": room
        })
        for conn in list(manager.rooms.get(room, [])):
            try:
                await conn.send_text(guess_msg)
            except Exception:
                pass

    # Router-AI guesses after a delay with a slightly different prompt
    await asyncio.sleep(random.uniform(1.5, 3.0))

    router_prompt = f"""You're guessing what someone drew. Look at the drawing carefully.
Word is {word_length} letters. Hint so far: "{hint}"
Give ONE word guess. Only the word, nothing else."""

    router_guess = await _vision_guess(api_key, canvas_image, router_prompt)
    if router_guess and router_guess.upper() != "SKIP":
        # Avoid duplicate guess
        if router_guess.lower() != (groq_guess or "").lower():
            guess_msg = json.dumps({
                "sender": "Router-AI",
                "message": f'__SCRIBBLE__:{json.dumps({"event": "ai_guess", "guesser": "Router-AI", "guess": router_guess})}',
                "image": None,
                "room": room
            })
            for conn in list(manager.rooms.get(room, [])):
                try:
                    await conn.send_text(guess_msg)
                except Exception:
                    pass


async def _vision_guess(api_key: str, image_data: str, prompt: str) -> str:
    """Call Groq Vision API with a canvas image and return a single-word guess."""
    try:
        # Build vision message with image
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_data}
                    }
                ]
            }
        ]

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "llama-3.2-11b-vision-preview",
                    "messages": messages,
                    "temperature": 0.5,
                    "max_tokens": 10,
                },
                timeout=15.0,
            )

        if resp.status_code != 200:
            return None

        raw = resp.json()["choices"][0]["message"]["content"].strip()
        # Clean: take only the first word, strip punctuation
        guess = raw.split()[0] if raw.split() else raw
        guess = guess.strip(".,!?\"'()[]{}").lower()
        return guess

    except Exception:
        return None


# ── Scribble AI Draw Turn (generate clues) ──────────────────────

SCRIBBLE_WORD_BANK = [
    'apple', 'banana', 'car', 'dog', 'elephant', 'fish', 'guitar', 'house',
    'jellyfish', 'kite', 'lion', 'moon', 'octopus', 'pizza', 'robot',
    'sun', 'tree', 'umbrella', 'violin', 'whale', 'zebra',
    'airplane', 'butterfly', 'castle', 'dinosaur', 'flower', 'ghost', 'helicopter',
    'kangaroo', 'laptop', 'mushroom', 'ninja', 'owl', 'penguin',
    'rainbow', 'spider', 'tornado', 'unicorn', 'volcano', 'dragon', 'rocket',
    'camera', 'diamond', 'grapes', 'hammer', 'igloo', 'key',
    'lamp', 'mountain', 'ocean', 'parachute', 'skateboard', 'telescope',
    'star', 'sword', 'crown', 'anchor', 'balloon',
    'candle', 'drum', 'flag', 'globe', 'heart',
    'leaf', 'magnet', 'nest', 'orange', 'pencil', 'rose',
    'snowflake', 'tent', 'cat', 'bird', 'frog', 'snake',
    'turtle', 'rabbit', 'bear', 'bee', 'eagle', 'fox',
    'giraffe', 'horse', 'koala', 'monkey', 'panda', 'shark', 'tiger', 'wolf',
    'boat', 'bridge', 'bus', 'chair', 'clock', 'door',
    'glasses', 'hat', 'ladder', 'mirror', 'piano', 'scissors', 'table',
    'train', 'truck', 'basketball', 'football', 'medal',
    'trophy', 'fire', 'lightning', 'cloud', 'rain', 'snow',
    'beach', 'forest', 'cave', 'river', 'lighthouse',
    'burger', 'taco', 'sushi', 'cake', 'cookie', 'donut', 'popcorn',
    'bicycle', 'compass', 'backpack', 'shoe', 'watch', 'bell',
    'cherry', 'lemon', 'strawberry', 'pineapple', 'carrot', 'tomato'
]

SCRIBBLE_CLUE_PROMPT = """You are playing a drawing guessing game. You are the DRAWER for the word "{word}".
You need to give 5 progressive clues to help guessers figure out the word WITHOUT saying the word itself.
Start vague and get more specific. Do NOT use the word or obvious derivatives.

Rules:
- Each clue should be 3-8 words max
- Clue 1: Very vague category or feeling
- Clue 2: Physical attribute or characteristic
- Clue 3: Where you might find it or how it's used
- Clue 4: More specific detail
- Clue 5: Almost a giveaway but still doesn't say the word

Reply with EXACTLY 5 lines, one clue per line. Nothing else."""


async def scribble_ai_draw(room: str, drawer: str):
    """AI's turn to draw: pick a word, generate clues, send to clients."""
    api_key = os.getenv("GROQ_API_KEY")

    # Pick a random word
    word = random.choice(SCRIBBLE_WORD_BANK)

    # Store the word in room state for validation
    room_game_state[room] = {
        "type": "scribble",
        "data": {"current_word": word, "drawer": drawer}
    }

    # Generate clues (try AI, fallback to generic)
    clues = []
    if api_key:
        try:
            prompt = SCRIBBLE_CLUE_PROMPT.format(word=word)

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.7,
                        "max_tokens": 150,
                    },
                    timeout=15.0,
                )

            if resp.status_code == 200:
                raw = resp.json()["choices"][0]["message"]["content"].strip()
                lines = [l.strip().lstrip("0123456789.-) ") for l in raw.split("\n") if l.strip()]
                clues = lines[:5]
        except Exception:
            pass

    # Fallback generic clues if AI fails
    if len(clues) < 3:
        clues = [
            f"It has {len(word)} letters",
            f"Think about everyday objects",
            f"It starts with '{word[0].upper()}'",
            f"The second letter is '{word[1].upper()}'" if len(word) > 1 else "Short word!",
            f"Almost rhymes with something common"
        ]

    # Send to all clients
    drawer_name = "Groq-AI" if drawer == "groq" else "Router-AI"
    payload = json.dumps({
        "sender": drawer_name,
        "message": f'__SCRIBBLE__:{json.dumps({"event": "ai_draw_start", "word": word, "clues": clues, "drawer": drawer})}',
        "image": None,
        "room": room
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

    # Replay history for new joiner (skip raw game blobs)
    for item in get_history(room):
        if not is_game_message(item["message"]):
            try:
                await ws.send_text(json.dumps({**item, "image": None, "room": room}))
            except Exception:
                pass

    # Notify room
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
                # Handle image upload
                desc = await describe_image(image)
                add_history(room, username, desc)
                await manager.broadcast(desc, username, room, image=image)
                asyncio.create_task(trigger_ai(room, chain=0, is_game_event=False))

            elif msg.startswith("__SCRIBBLE__:"):
                # Scribble game message
                try:
                    scribble_data = json.loads(msg[len("__SCRIBBLE__:"):])
                    event = scribble_data.get("event", "")

                    if event == "game_start":
                        # Notify AIs about the scribble game
                        add_history(room, username, "[SCRIBBLE game started! Drawing & guessing game.]")
                        asyncio.create_task(trigger_ai(room, chain=0, is_game_event=True))

                    elif event == "user_draw_start":
                        # User is drawing — store word for AI vision guessing
                        word = scribble_data.get("word", "")
                        room_game_state[room] = {
                            "type": "scribble",
                            "data": {"current_word": word, "drawer": "user"}
                        }
                        add_history(room, username, f"[SCRIBBLE: {username} is drawing ({scribble_data.get('wordLength', '?')} letters)]")

                    elif event == "canvas_snapshot":
                        # AI vision guessing — process canvas image
                        canvas_image = scribble_data.get("image", "")
                        hint = scribble_data.get("hint", "")
                        word_length = scribble_data.get("wordLength", 0)

                        if canvas_image:
                            asyncio.create_task(
                                scribble_ai_guess(room, canvas_image, hint, word_length)
                            )

                    elif event == "ai_draw_request":
                        # AI's turn to draw — pick word, generate clues
                        drawer = scribble_data.get("drawer", "groq")
                        asyncio.create_task(scribble_ai_draw(room, drawer))

                    elif event == "user_guess":
                        # User guessing during AI draw turn
                        guess = scribble_data.get("guess", "")
                        state = room_game_state.get(room, {})
                        current_word = state.get("data", {}).get("current_word", "")
                        # Validation is done client-side, log it
                        add_history(room, username, f'[SCRIBBLE: {username} guessed "{guess}"]')

                    elif event in ("round_guessed", "round_timeout", "game_over"):
                        await manager.broadcast(msg, username, room)
                        add_history(room, username, f"[SCRIBBLE: {event}]")
                        if event == "game_over":
                            asyncio.create_task(trigger_ai(room, chain=0, is_game_event=True))
                    else:
                        await manager.broadcast(msg, username, room)
                except Exception:
                    pass

            elif is_game_message(msg):
                # Ludo / Chess state update
                parsed = parse_game_message(msg)
                update_game_state(room, parsed)

                event = parsed["data"].get("event", "game update") if parsed else "game update"
                add_history(room, username, f"[{parsed['type'].upper() if parsed else 'GAME'} update: {event}]")

                await manager.broadcast(msg, username, room)

                if parsed and is_notable_game_event(parsed):
                    asyncio.create_task(trigger_ai(room, chain=0, is_game_event=True))

            else:
                # Normal chat message
                add_history(room, username, msg)
                await manager.broadcast(msg, username, room)
                asyncio.create_task(trigger_ai(room, chain=0, is_game_event=False))

    except WebSocketDisconnect:
        manager.disconnect(ws, room)
        await manager.broadcast(f"{username} left.", "System", room)