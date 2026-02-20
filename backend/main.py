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
            self.rooms[room].discard(ws) if hasattr(self.rooms[room], 'discard') else (
                self.rooms[room].remove(ws) if ws in self.rooms[room] else None
            )
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
room_game_state: dict[str, dict] = {}   # last game state per room
MAX_HISTORY = 12                         # messages kept in context
MAX_CHAIN = 2                            # max AI→AI reply rounds (reduced from 3)


def get_history(room: str) -> list:
    return room_histories.setdefault(room, [])


def add_history(room: str, sender: str, message: str):
    hist = get_history(room)
    hist.append({"sender": sender, "message": message})
    # Keep only last MAX_HISTORY items
    if len(hist) > MAX_HISTORY:
        room_histories[room] = hist[-MAX_HISTORY:]


def is_game_message(msg: str) -> bool:
    return msg.startswith("__LUDO__:") or msg.startswith("__CHESS__:")


def parse_game_message(msg: str) -> dict | None:
    for prefix in ["__LUDO__:", "__CHESS__:"]:
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
    """
    Return history with game state blobs replaced by readable summaries.
    This prevents weird game JSON from confusing the AI.
    """
    cleaned = []
    for item in get_history(room):
        msg = item["message"]
        if is_game_message(msg):
            parsed = parse_game_message(msg)
            if parsed:
                event = parsed["data"].get("event", "game update")
                readable = f"[{parsed['type'].upper()} update: {event}]"
                cleaned.append({"sender": item["sender"], "message": readable})
            # skip totally unparseable game blobs
        else:
            cleaned.append(item)
    return cleaned


# ── AI helpers ────────────────────────────────────────────────

GROQ_SYSTEM = """You are Groq-AI — the smart, chill tech bro in a group chat who also plays board games.
Personality: witty, concise, helpful. You speak Gen-Z / brolang naturally.
When someone says something or a game move happens, react briefly and naturally.

CRITICAL RULES:
- Keep replies SHORT. 1-3 sentences max unless someone asks a detailed question.
- Match reply length to the message: short message = short reply, complex question = longer answer.
- If you have NOTHING meaningful to add, output exactly: SKIP
- Do NOT ask multiple questions in one message.
- Do NOT repeat what was already said.
- Do NOT start with "Yo" every single time — vary your openers.
- When reacting to a game move, keep it to ONE punchy comment max."""

ROUTER_SYSTEM = """You are Router-AI — the wild, funny, trash-talking bro in a group chat who plays board games.
Personality: chaotic, hilarious, loves to roast but keeps it friendly. Gen-Z slang heavy.
You comment on game moves, conversations, and life choices with maximum energy.

CRITICAL RULES:
- Keep replies SHORT. 1-3 sentences max unless someone asks something complex.
- If a message is not directed at you and you have nothing funny to add, output exactly: SKIP
- Do NOT send 3 messages in a row. If you just spoke, stay quiet.
- Do NOT repeat the same joke style twice in a row.
- Do NOT send walls of text — shorter = funnier.
- SKIP game-only events 50% of the time to avoid spam."""

def should_skip_game_event(bot_name: str, history: list) -> bool:
    """For game events, randomly skip to reduce spam."""
    return random.random() < 0.45  # 45% chance to skip game-only events


def build_messages(system: str, history: list[dict], bot_name: str, game_ctx: str) -> list:
    # Inject game context into system if present
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
    # Check starts with SKIP (allow punctuation after)
    return upper.startswith("SKIP") and len(reply.strip()) <= 8


async def fetch_groq(bot_name: str, history: list, game_ctx: str) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "SKIP"

    # Don't reply to yourself
    if history and history[-1]["sender"] == bot_name:
        return "SKIP"

    messages = build_messages(GROQ_SYSTEM, history, bot_name, game_ctx)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": messages,
                    "temperature": 0.75,
                    "max_tokens": 120,   # hard cap — keeps replies short
                },
                timeout=25.0,
            )
        if resp.status_code != 200:
            return "SKIP"
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return "SKIP"


async def fetch_openrouter(bot_name: str, history: list, game_ctx: str) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return "SKIP"

    # Don't reply to yourself
    if history and history[-1]["sender"] == bot_name:
        return "SKIP"

    # If last two messages were both from AI bots, hold back
    ai_bots = {"Groq-AI", "Router-AI"}
    recent_senders = [h["sender"] for h in history[-3:]]
    ai_count = sum(1 for s in recent_senders if s in ai_bots)
    if ai_count >= 2:
        return "SKIP"

    messages = build_messages(ROUTER_SYSTEM, history, bot_name, game_ctx)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://render.com",
        "X-Title": "SquadChat",
    }

    # Model list — try Grok first, fall back to free llama
    model_attempts = [
        {"model": "x-ai/grok-3-mini", "temperature": 0.9, "max_tokens": 100},
        {"model": "meta-llama/llama-3-8b-instruct:free", "temperature": 0.9, "max_tokens": 100},
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
                # Normalize: strip any role prefix that models sometimes add
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
    Trigger both AIs to respond. Chain limits AI↔AI conversation depth.
    Game events get lighter treatment to reduce spam.
    """
    if chain >= MAX_CHAIN:
        return

    # Slightly longer delay for game events so they don't spam every move
    delay = 2.5 if is_game_event else 1.8
    await asyncio.sleep(delay)

    history = sanitize_history_for_ai(room)
    game_ctx = get_game_context(room)

    # For game events at chain>0, be much more selective
    if is_game_event and chain > 0:
        return

    tasks = [
        fetch_groq("Groq-AI", history, game_ctx),
        fetch_openrouter("Router-AI", history, game_ctx),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    bot_names = ["Groq-AI", "Router-AI"]
    anyone_replied = False

    for i, reply in enumerate(results):
        if isinstance(reply, Exception):
            continue
        if is_skip(reply):
            continue

        bot = bot_names[i]
        # Strip self-reference prefix if model echoed it
        if reply.lower().startswith(bot.lower() + ":"):
            reply = reply[len(bot) + 1:].strip()

        # Clamp overly long replies (double safety)
        words = reply.split()
        if len(words) > 60:
            reply = " ".join(words[:60]) + "…"

        anyone_replied = True
        add_history(room, bot, reply)
        await manager.broadcast(reply, bot, room)
        await asyncio.sleep(1.2)

    # Allow one round of AI↔AI only if a human message triggered it (not game event)
    if anyone_replied and not is_game_event:
        await trigger_ai(room, chain + 1, is_game_event=False)


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

            elif is_game_message(msg):
                # Game state update
                parsed = parse_game_message(msg)
                update_game_state(room, parsed)
                # Store human-readable version in history
                event = parsed["data"].get("event", "game update") if parsed else "game update"
                add_history(room, username, f"[{parsed['type'].upper() if parsed else 'GAME'} update: {event}]")
                # Broadcast raw game message so other clients can sync
                await manager.broadcast(msg, username, room)
                # Only trigger AI for notable events (not every tick)
                if parsed and parsed["data"].get("winner"):
                    asyncio.create_task(trigger_ai(room, chain=0, is_game_event=True))
                elif parsed and parsed["data"].get("event"):
                    asyncio.create_task(trigger_ai(room, chain=0, is_game_event=True))

            else:
                # Normal chat message
                add_history(room, username, msg)
                await manager.broadcast(msg, username, room)
                asyncio.create_task(trigger_ai(room, chain=0, is_game_event=False))

    except WebSocketDisconnect:
        manager.disconnect(ws, room)
        await manager.broadcast(f"{username} left.", "System", room)