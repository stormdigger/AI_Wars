"""
Squad.GG – Backend
FastAPI WebSocket server with:
  - Smart AI responses (only on special game events)
  - Anti-spam per-bot timestamp enforcement
  - Per-room asyncio.Lock (no concurrent AI triggers)
  - Lobby / player-slot management
  - Vote system for new player join requests
  - Sanitized game history (no raw JSON to AI)
"""

import asyncio
import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


# ═════════════════════════════════════════════════════════════
#  CONSTANTS
# ═════════════════════════════════════════════════════════════

MIN_BOT_INTERVAL   = 6.0   # seconds – minimum gap between any message from same bot
GLOBAL_BOT_COOLDOWN = 3.0  # seconds – any bot spoke within this? other bot waits
MAX_HISTORY        = 14    # messages kept in context window
MAX_CHAT_CHAIN     = 1     # AI→AI reply rounds for chat messages
MAX_TOKENS_GAME    = 55    # hard token cap for game event reactions
MAX_TOKENS_CHAT    = 110   # hard token cap for chat replies
VOTE_TIMEOUT_SECS  = 20    # seconds before auto-resolving join vote

# Game events that warrant an AI one-liner reaction
SPECIAL_GAME_EVENTS = {
    "ludo": {
        "capture", "dice_six", "token_enter",
        "home_stretch", "near_win", "win", "comeback"
    },
    "chess": {
        "check", "checkmate", "stalemate", "queen_capture",
        "promotion", "castling", "rook_capture"
    },
}

AI_BOTS = {"Groq-AI", "Router-AI"}


# ═════════════════════════════════════════════════════════════
#  ROOM STATE
# ═════════════════════════════════════════════════════════════

@dataclass
class RoomState:
    lock:          asyncio.Lock  = field(default_factory=asyncio.Lock)
    history:       list          = field(default_factory=list)
    game_type:     str           = ""          # "ludo" | "chess" | ""
    game_active:   bool          = False
    game_data:     dict          = field(default_factory=dict)
    # Bot anti-spam timestamps
    bot_last:      dict          = field(default_factory=dict)  # bot→timestamp
    # Lobby
    players:       dict          = field(default_factory=dict)  # username→{ready,color}
    host:          str           = ""
    # Pending vote for a new joiner
    pending_vote:  Optional[dict] = None   # {requester, votes:{user:bool}, task}


rooms: dict[str, RoomState] = {}


def get_room(room_id: str) -> RoomState:
    if room_id not in rooms:
        rooms[room_id] = RoomState()
    return rooms[room_id]


# ═════════════════════════════════════════════════════════════
#  CONNECTION MANAGER
# ═════════════════════════════════════════════════════════════

class ConnectionManager:
    def __init__(self):
        self._rooms: dict[str, list[WebSocket]] = {}

    async def connect(self, ws: WebSocket, room_id: str):
        await ws.accept()
        self._rooms.setdefault(room_id, []).append(ws)

    def disconnect(self, ws: WebSocket, room_id: str):
        lst = self._rooms.get(room_id, [])
        if ws in lst:
            lst.remove(ws)
        if not lst and room_id in self._rooms:
            del self._rooms[room_id]

    async def broadcast(self, room_id: str, payload: dict, exclude: WebSocket = None):
        text = json.dumps(payload)
        for conn in list(self._rooms.get(room_id, [])):
            if conn is exclude:
                continue
            try:
                await conn.send_text(text)
            except Exception:
                self.disconnect(conn, room_id)

    async def send_to(self, ws: WebSocket, payload: dict):
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:
            pass


manager = ConnectionManager()


# ═════════════════════════════════════════════════════════════
#  HISTORY HELPERS
# ═════════════════════════════════════════════════════════════

def add_history(room_id: str, sender: str, message: str):
    state = get_room(room_id)
    state.history.append({"sender": sender, "message": message})
    if len(state.history) > MAX_HISTORY:
        state.history = state.history[-MAX_HISTORY:]


def sanitize_history(room_id: str) -> list:
    """Return history with raw game blobs replaced by readable summaries."""
    out = []
    for item in get_room(room_id).history:
        msg = item["message"]
        if msg.startswith("__"):
            # Already replaced with readable summary at add time
            out.append(item)
        else:
            out.append(item)
    return out


def is_game_msg(msg: str) -> bool:
    return msg.startswith("__LUDO__:") or msg.startswith("__CHESS__:")


def parse_game_msg(msg: str) -> Optional[dict]:
    for prefix, gtype in [("__LUDO__:", "ludo"), ("__CHESS__:", "chess")]:
        if msg.startswith(prefix):
            try:
                data = json.loads(msg[len(prefix):])
                data["_game_type"] = gtype
                return data
            except Exception:
                return None
    return None


def is_special_event(parsed: dict) -> bool:
    gtype = parsed.get("_game_type", "")
    event = parsed.get("event_type", parsed.get("event", ""))
    specials = SPECIAL_GAME_EVENTS.get(gtype, set())
    return any(s in event for s in specials)


def game_context_str(room_id: str) -> str:
    state = get_room(room_id)
    if not state.game_data:
        return ""
    d = state.game_data
    gtype = d.get("_game_type", "game")
    event = d.get("event", "")
    summary = d.get("summary", "")
    winner = d.get("winner")
    turn = d.get("turn", "")
    if winner:
        return f"\n[{gtype.upper()} GAME OVER: {winner} WINS! Last event: {event}]"
    return (
        f"\n[{gtype.upper()} GAME | Last event: {event} | "
        f"Current turn: {turn} | Positions: {summary}]"
    )


# ═════════════════════════════════════════════════════════════
#  AI ANTI-SPAM HELPERS
# ═════════════════════════════════════════════════════════════

def bot_can_speak(room_id: str, bot_name: str) -> bool:
    """True if bot is allowed to speak (anti-spam check)."""
    state = get_room(room_id)
    now = time.time()

    # Check own cooldown
    last_self = state.bot_last.get(bot_name, 0)
    if now - last_self < MIN_BOT_INTERVAL:
        return False

    # Check global bot cooldown (any other bot spoke recently?)
    for other, t in state.bot_last.items():
        if other != bot_name and now - t < GLOBAL_BOT_COOLDOWN:
            return False

    # Don't speak if the last message in history is from this bot
    hist = state.history
    if hist and hist[-1]["sender"] == bot_name:
        return False

    return True


def record_bot_spoke(room_id: str, bot_name: str):
    get_room(room_id).bot_last[bot_name] = time.time()


def is_skip_reply(reply: str) -> bool:
    if not reply or len(reply.strip()) == 0:
        return True
    upper = reply.strip().upper()
    # SKIP alone, optionally with punctuation
    return upper.startswith("SKIP") and len(reply.strip()) <= 8


def strip_bot_prefix(reply: str, bot_name: str) -> str:
    for prefix in [f"{bot_name}:", "Groq-AI:", "Router-AI:", "Assistant:", "AI:"]:
        if reply.lower().startswith(prefix.lower()):
            reply = reply[len(prefix):].strip()
    return reply


def clamp_length(reply: str, max_words: int = 50) -> str:
    words = reply.split()
    if len(words) > max_words:
        return " ".join(words[:max_words]) + "…"
    return reply


# ═════════════════════════════════════════════════════════════
#  AI SYSTEM PROMPTS
# ═════════════════════════════════════════════════════════════

GROQ_BASE = (
    "You are Groq-AI — the smart, chill tech bro in a group chat who also plays board games. "
    "Personality: witty, concise, actually helpful. Gen-Z / brolang. "
    "Match response length to context: short casual message = 1-2 sentences, "
    "complex question = up to 4 sentences. NEVER write essays. "
    "If you have nothing meaningful to add, output exactly: SKIP"
)

ROUTER_BASE = (
    "You are Router-AI — the wild, funny, chaotic bro in a group chat who loves to roast. "
    "Gen-Z slang heavy. You react to things with energy but keep it brief. "
    "Do NOT dominate the conversation. If the last message was from you or another AI, output: SKIP. "
    "If the conversation doesn't need your input right now, output: SKIP"
)

GAME_ADDON = (
    "\n\nGAME MODE RULES: A board game is in progress. "
    "React ONLY to the game event described. "
    "Keep your response to ONE sentence, max 10 words. "
    "Express pure reaction (hype, roast, sympathy, trash talk). No explanations."
)


def build_messages(system: str, history: list, bot_name: str, extra_ctx: str = "") -> list:
    sys_content = system + (extra_ctx if extra_ctx else "")
    msgs = [{"role": "system", "content": sys_content}]
    for item in history:
        msg = item["message"]
        if item["sender"] == bot_name:
            msgs.append({"role": "assistant", "content": msg})
        else:
            msgs.append({"role": "user", "content": f"{item['sender']}: {msg}"})
    return msgs


# ═════════════════════════════════════════════════════════════
#  AI FETCH FUNCTIONS
# ═════════════════════════════════════════════════════════════

async def fetch_groq(room_id: str, is_game: bool) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "SKIP"
    if not bot_can_speak(room_id, "Groq-AI"):
        return "SKIP"

    history  = sanitize_history(room_id)
    game_ctx = game_context_str(room_id) if is_game else ""
    system   = GROQ_BASE + (GAME_ADDON if is_game else "")
    msgs     = build_messages(system, history, "Groq-AI", game_ctx)
    max_tok  = MAX_TOKENS_GAME if is_game else MAX_TOKENS_CHAT

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": "llama-3.3-70b-versatile", "messages": msgs,
                      "temperature": 0.75, "max_tokens": max_tok},
                timeout=22.0,
            )
        if r.status_code != 200:
            return "SKIP"
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return "SKIP"


async def fetch_openrouter(room_id: str, is_game: bool) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return "SKIP"
    if not bot_can_speak(room_id, "Router-AI"):
        return "SKIP"

    # Extra guard: Router-AI checks recent AI message count more strictly
    hist = get_room(room_id).history
    recent_ai = sum(1 for h in hist[-4:] if h["sender"] in AI_BOTS)
    if recent_ai >= 2:
        return "SKIP"

    history  = sanitize_history(room_id)
    game_ctx = game_context_str(room_id) if is_game else ""
    system   = ROUTER_BASE + (GAME_ADDON if is_game else "")
    msgs     = build_messages(system, history, "Router-AI", game_ctx)
    max_tok  = MAX_TOKENS_GAME if is_game else MAX_TOKENS_CHAT

    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://render.com",
        "X-Title": "SquadGG",
    }
    models = [
        {"model": "x-ai/grok-3-mini",                    "temperature": 0.9},
        {"model": "meta-llama/llama-3-8b-instruct:free",  "temperature": 0.9},
    ]
    for m in models:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json={"messages": msgs, "max_tokens": max_tok, **m},
                    timeout=15.0,
                )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            continue
    return "SKIP"


async def describe_image(b64: str) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "[Image uploaded]"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "llama-3.2-11b-vision-preview",
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": "Describe this image in one short funny sentence."},
                        {"type": "image_url", "image_url": {"url": b64}},
                    ]}],
                    "max_tokens": 80,
                },
                timeout=22.0,
            )
        if r.status_code == 200:
            return f"[Image: {r.json()['choices'][0]['message']['content']}]"
    except Exception:
        pass
    return "[Image uploaded]"


# ═════════════════════════════════════════════════════════════
#  AI TRIGGER  (the core — with lock + anti-spam)
# ═════════════════════════════════════════════════════════════

async def trigger_ai(room_id: str, is_game: bool = False, chain: int = 0):
    """
    Triggers AI responses for the room. Uses a per-room lock so only ONE
    trigger runs at a time — eliminates duplicate/concurrent AI messages.

    - is_game=True  → game event, one-liners only, no chain
    - is_game=False → chat message, allows 1 chain round of AI↔AI
    """
    state = get_room(room_id)

    # Try to acquire lock without blocking indefinitely
    try:
        await asyncio.wait_for(state.lock.acquire(), timeout=8.0)
    except asyncio.TimeoutError:
        return  # Another trigger is taking too long; skip

    try:
        # Small delay: let messages settle
        await asyncio.sleep(2.2 if is_game else 1.8)

        # Run both bots sequentially (not in parallel) to control ordering
        for bot_name, fetch_fn in [("Groq-AI", fetch_groq), ("Router-AI", fetch_openrouter)]:
            reply = await fetch_fn(room_id, is_game)
            if is_skip_reply(reply):
                continue

            reply = strip_bot_prefix(reply, bot_name)
            reply = clamp_length(reply, 30 if is_game else 55)

            record_bot_spoke(room_id, bot_name)
            add_history(room_id, bot_name, reply)
            await manager.broadcast(room_id, {
                "type": "chat",
                "sender": bot_name,
                "message": reply,
                "image": None,
            })
            # Gap between the two bots
            await asyncio.sleep(1.4)

    finally:
        state.lock.release()

    # One round of AI↔AI for real chat messages only
    if not is_game and chain < MAX_CHAT_CHAIN:
        hist = get_room(room_id).history
        if hist and hist[-1]["sender"] in AI_BOTS:
            asyncio.create_task(trigger_ai(room_id, is_game=False, chain=chain + 1))


# ═════════════════════════════════════════════════════════════
#  LOBBY / VOTE HELPERS
# ═════════════════════════════════════════════════════════════

def lobby_summary(room_id: str) -> dict:
    state = get_room(room_id)
    return {
        "type": "lobby_update",
        "players": {
            u: {"ready": d["ready"], "color": d.get("color", "")}
            for u, d in state.players.items()
        },
        "host":        state.host,
        "game_active": state.game_active,
        "game_type":   state.game_type,
    }


async def resolve_vote(room_id: str, requester: str):
    """Called after VOTE_TIMEOUT or all players voted."""
    state = get_room(room_id)
    if not state.pending_vote or state.pending_vote["requester"] != requester:
        return
    votes = state.pending_vote["votes"]
    yes   = sum(1 for v in votes.values() if v)
    no    = sum(1 for v in votes.values() if not v)
    accepted = yes >= no and (yes + no) > 0

    if accepted and not state.game_active:
        state.players[requester] = {"ready": False, "color": ""}
        if not state.host:
            state.host = requester
        await manager.broadcast(room_id, {
            "type": "vote_result",
            "requester": requester,
            "accepted": True,
            "message": f"✅ {requester} was accepted into the room!",
        })
        await manager.broadcast(room_id, lobby_summary(room_id))
    else:
        await manager.broadcast(room_id, {
            "type": "vote_result",
            "requester": requester,
            "accepted": False,
            "message": f"❌ {requester} was not accepted (or game is in progress).",
        })

    state.pending_vote = None


# ═════════════════════════════════════════════════════════════
#  WEBSOCKET ENDPOINT
# ═════════════════════════════════════════════════════════════

@app.websocket("/ws/{room_id}/{username}")
async def ws_endpoint(ws: WebSocket, room_id: str, username: str):
    await manager.connect(ws, room_id)
    state = get_room(room_id)

    is_first = not state.players and not state.game_active
    is_game_in_progress = state.game_active

    # ── Replay non-game chat history for new joiner ──
    for item in state.history:
        if not item["message"].startswith("__"):
            await manager.send_to(ws, {
                "type": "chat", "sender": item["sender"],
                "message": item["message"], "image": None,
            })

    if is_game_in_progress:
        # Late joiner → spectator
        await manager.send_to(ws, {
            "type": "system",
            "message": f"Game in progress — you joined as spectator. You can play next round!",
        })
        # Send current game state so they can watch
        if state.game_data:
            prefix = "__LUDO__:" if state.game_type == "ludo" else "__CHESS__:"
            await manager.send_to(ws, {
                "type": "game_sync",
                "payload": state.game_data,
                "game_type": state.game_type,
            })
        await manager.send_to(ws, lobby_summary(room_id))

    elif username in state.players:
        # Reconnect
        await manager.send_to(ws, {
            "type": "system",
            "message": f"Welcome back, {username}!",
        })
        await manager.send_to(ws, lobby_summary(room_id))

    elif is_first:
        # First player → host, auto-join
        state.players[username] = {"ready": False, "color": ""}
        state.host = username
        await manager.broadcast(room_id, {
            "type": "system",
            "message": f"🏠 {username} created the room and is the host!",
        })
        await manager.broadcast(room_id, lobby_summary(room_id))

    elif state.pending_vote is None:
        # New player → start vote (if no vote pending)
        vote_task_holder = {}

        async def vote_timeout(req: str):
            await asyncio.sleep(VOTE_TIMEOUT_SECS)
            await resolve_vote(room_id, req)

        state.pending_vote = {
            "requester": username,
            "votes":     {},
            "ws":        ws,
        }
        task = asyncio.create_task(vote_timeout(username))
        state.pending_vote["task"] = task

        await manager.broadcast(room_id, {
            "type": "vote_request",
            "requester": username,
            "message": f"🙋 {username} wants to join! Vote to accept.",
            "timeout": VOTE_TIMEOUT_SECS,
        })
        await manager.send_to(ws, {
            "type": "system",
            "message": "Waiting for room players to accept you…",
        })

    else:
        # Another vote is pending — queue them as spectator for now
        await manager.send_to(ws, {
            "type": "system",
            "message": "Another join request is pending. Please wait a moment and reconnect.",
        })

    # ── Message loop ──
    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"type": "chat", "message": raw}

            msg_type = data.get("type", "chat")
            message  = data.get("message", "")
            image    = data.get("image")

            # ── Vote cast ──
            if msg_type == "vote":
                if state.pending_vote and username in state.players:
                    vote_val = data.get("vote", True)
                    state.pending_vote["votes"][username] = vote_val
                    # If all existing players voted, resolve immediately
                    if len(state.pending_vote["votes"]) >= len(state.players):
                        state.pending_vote["task"].cancel()
                        await resolve_vote(room_id, state.pending_vote["requester"])
                continue

            # ── Ready toggle ──
            if msg_type == "ready":
                if username in state.players:
                    state.players[username]["ready"] = data.get("ready", True)
                    await manager.broadcast(room_id, lobby_summary(room_id))
                    rdy_count = sum(1 for p in state.players.values() if p["ready"])
                    if rdy_count >= 2:
                        await manager.broadcast(room_id, {
                            "type": "system",
                            "message": f"🎮 {rdy_count} players ready! Host can start the game.",
                        })
                continue

            # ── Game start ──
            if msg_type == "start_game":
                if username != state.host:
                    await manager.send_to(ws, {
                        "type": "system", "message": "Only the host can start the game."
                    })
                    continue
                game_type = data.get("game_type", "ludo")
                ready_players = [u for u, p in state.players.items() if p["ready"]]
                if len(ready_players) < 1:
                    await manager.send_to(ws, {
                        "type": "system", "message": "At least 1 player must be ready."
                    })
                    continue

                # Assign colors based on game type
                state.game_type   = game_type
                state.game_active = True
                colors = (["red", "green", "blue"] if game_type == "ludo"
                          else ["white", "black"])
                assignments = {}
                for i, uname in enumerate(ready_players):
                    if i < len(colors):
                        assignments[uname] = colors[i]
                        state.players[uname]["color"] = colors[i]

                await manager.broadcast(room_id, {
                    "type": "game_start",
                    "game_type": game_type,
                    "assignments": assignments,
                    "players": list(state.players.keys()),
                    "message": f"🎮 Game starting! {game_type.upper()} — GL HF everyone!",
                })
                add_history(room_id, "System", f"Game started: {game_type}")
                continue

            # ── Game state update ──
            if msg_type == "game_update" or is_game_msg(message):
                raw_payload = message if is_game_msg(message) else data.get("payload", "")
                parsed = parse_game_msg(raw_payload) if raw_payload else None

                if parsed:
                    state.game_data = parsed
                    state.game_active = not bool(parsed.get("winner"))
                    event   = parsed.get("event", "")
                    summary = parsed.get("summary", "")
                    special = is_special_event(parsed)

                    # Store human-readable event in history
                    add_history(room_id, username, f"[{state.game_type.upper()}: {event}]")

                    # Broadcast raw game state to all clients for board sync
                    await manager.broadcast(room_id, {
                        "type": "game_sync",
                        "payload": parsed,
                        "game_type": state.game_type,
                        "sender": username,
                    }, exclude=ws)

                    # Only trigger AI for special events
                    if special:
                        asyncio.create_task(trigger_ai(room_id, is_game=True))

                    # If game ended, notify and reset
                    if parsed.get("winner"):
                        state.game_active = False
                        winner = parsed["winner"]
                        await manager.broadcast(room_id, {
                            "type": "system",
                            "message": f"🏆 {winner} wins! GG everyone.",
                        })
                        await manager.broadcast(room_id, lobby_summary(room_id))
                continue

            # ── Image ──
            if image:
                desc = await describe_image(image)
                add_history(room_id, username, desc)
                await manager.broadcast(room_id, {
                    "type": "chat", "sender": username,
                    "message": desc, "image": image,
                })
                asyncio.create_task(trigger_ai(room_id, is_game=False))
                continue

            # ── Normal chat ──
            if message:
                add_history(room_id, username, message)
                await manager.broadcast(room_id, {
                    "type": "chat", "sender": username,
                    "message": message, "image": None,
                })
                asyncio.create_task(trigger_ai(room_id, is_game=False))

    except WebSocketDisconnect:
        manager.disconnect(ws, room_id)
        if username in state.players:
            del state.players[username]
            if state.host == username:
                # Assign new host
                remaining = list(state.players.keys())
                state.host = remaining[0] if remaining else ""
        await manager.broadcast(room_id, {
            "type": "system",
            "message": f"{username} left the room.",
        })
        await manager.broadcast(room_id, lobby_summary(room_id))


# ── Startup ──────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    print("── Squad.GG Backend ──")
    print("✅ GROQ"       if os.getenv("GROQ_API_KEY")       else "❌ GROQ key missing")
    print("✅ OPENROUTER" if os.getenv("OPENROUTER_API_KEY") else "❌ OPENROUTER key missing")