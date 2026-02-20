from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import httpx
import asyncio
import json
import os
import sys

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- STARTUP CHECKS ---
@app.on_event("startup")
async def startup_event():
    print("--- SERVER STARTUP ---")
    if os.getenv("GROQ_API_KEY"): print("✅ GROQ Key Found")
    else: print("❌ GROQ Key Missing")
    if os.getenv("OPENROUTER_API_KEY"): print("✅ OPENROUTER Key Found")
    else: print("❌ OPENROUTER Key Missing")

# --- ROOM MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.active_rooms: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, room_id: str):
        await websocket.accept()
        if room_id not in self.active_rooms:
            self.active_rooms[room_id] = []
        self.active_rooms[room_id].append(websocket)

    def disconnect(self, websocket: WebSocket, room_id: str):
        if room_id in self.active_rooms:
            if websocket in self.active_rooms[room_id]:
                self.active_rooms[room_id].remove(websocket)
            if not self.active_rooms[room_id]:
                del self.active_rooms[room_id]

    async def broadcast_to_room(self, message: str, sender: str, room_id: str, image: str = None):
        if room_id not in self.active_rooms: return
        
        payload = json.dumps({"sender": sender, "message": message, "image": image, "room": room_id})
        
        for connection in self.active_rooms[room_id][:]:
            try:
                await connection.send_text(payload)
            except Exception as e:
                print(f"Removing dead connection from {room_id}: {e}")
                self.disconnect(connection, room_id)

manager = ConnectionManager()

# --- MULTI-ROOM HISTORY STORAGE ---
room_histories = {} 
MAX_HISTORY = 10
MAX_BOT_CONVERSATION_CHAIN = 3

# --- LUDO GAME STATE PER ROOM ---
room_ludo_state = {}

def get_room_history(room_id):
    if room_id not in room_histories:
        room_histories[room_id] = []
    return room_histories[room_id]

def add_to_history(room_id, sender, message):
    if room_id not in room_histories:
        room_histories[room_id] = []
    
    room_histories[room_id].append({"sender": sender, "message": message})
    if len(room_histories[room_id]) > MAX_HISTORY:
        room_histories[room_id].pop(0)

def parse_ludo_message(message: str):
    """Parse a __LUDO__: prefixed message and return game data dict or None."""
    if not message.startswith("__LUDO__:"):
        return None
    try:
        json_part = message[len("__LUDO__:"):]
        return json.loads(json_part)
    except Exception:
        return None

def update_ludo_state(room_id: str, game_data: dict):
    """Update the stored Ludo state for a room."""
    room_ludo_state[room_id] = game_data

def get_ludo_context(room_id: str) -> str:
    """Build a natural language summary of the current Ludo game state for AI context."""
    if room_id not in room_ludo_state:
        return ""
    
    state = room_ludo_state[room_id]
    summary = state.get("summary", "")
    current_turn = state.get("currentTurn", "")
    winner = state.get("winner", None)
    last_event = state.get("event", "")
    
    if winner:
        return f"\n\n[LUDO GAME: {winner} just WON the game! Last event: {last_event}. Board: {summary}]"
    
    return f"\n\n[LUDO GAME in progress: Last event: {last_event}. Current turn: {current_turn}. Positions: {summary}]"

# --- AI FUNCTIONS ---

def build_messages_payload(bot_name: str, persona: str, current_room_history: list, ludo_context: str = ""):
    system_instruction = (
        f"You are {bot_name}. {persona} "
        "Speak in 'Brolang' / Gen-Z slang. "
        f"{ludo_context}"
        "CRITICAL: ONLY output your response. If you have nothing to say, output: SKIP."
    )
    
    messages = [{"role": "system", "content": system_instruction}]
    
    for msg in current_room_history:
        content = msg["message"]
        # Skip raw ludo state blobs from chat history — use ludo_context instead
        if content.startswith("__LUDO__:"):
            continue
        if msg["sender"] == bot_name:
            messages.append({"role": "assistant", "content": content})
        else:
            messages.append({"role": "user", "content": f"{msg['sender']}: {content}"})
            
    return messages

async def fetch_groq(bot_name: str, room_history: list, ludo_context: str = ""):
    # Prevent Groq from replying to itself
    if room_history and room_history[-1]["sender"] == bot_name:
        return "SKIP"

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key: return "SKIP"
    
    persona = (
        "You are the smart tech bro who loves board games. "
        "You give good advice but keep it casual. "
        "When a Ludo game is happening, comment on the moves, roast bad plays, "
        "and talk about your strategy like you're the smartest player at the table."
    )
    messages = build_messages_payload(bot_name, persona, room_history, ludo_context)
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions", 
                headers={"Authorization": f"Bearer {api_key}"}, 
                json={"model": "llama-3.3-70b-versatile", "messages": messages, "temperature": 0.8}, 
                timeout=30.0
            )
            if response.status_code != 200: return "SKIP"
            return response.json()["choices"][0]["message"]["content"].strip()
    except: return "SKIP"

async def fetch_openrouter(bot_name: str, room_history: list, ludo_context: str = ""):
    # Prevent Router-AI from replying to itself
    if room_history and room_history[-1]["sender"] == bot_name:
        return "SKIP"

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key: return "SKIP"
    
    persona = (
        "You are the wild, funny bro who trash talks during Ludo. "
        "You roast people's moves, celebrate when you win, cry when you get captured, "
        "and never shut up about dice luck."
    )
    messages = build_messages_payload(bot_name, persona, room_history, ludo_context)
    
    headers = {
        "Authorization": f"Bearer {api_key}", 
        "HTTP-Referer": "https://render.com", 
        "X-Title": "SquadChat"
    }

    # Attempt 1: Grok
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions", 
                headers=headers, 
                json={"model": "x-ai/grok-4.1-fast", "messages": messages, "temperature": 1.0}, 
                timeout=15.0
            )
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"].strip()
    except: pass

    # Attempt 2: Fallback
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions", 
                headers=headers, 
                json={"model": "meta-llama/llama-3-8b-instruct:free", "messages": messages, "temperature": 1.0}, 
                timeout=30.0
            )
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"].strip()
    except: pass

    return "SKIP"

# --- VISION ---
async def describe_image(base64_image):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key: return "[Image uploaded]"
    
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "model": "llama-3.2-11b-vision-preview",
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "Describe funny details in 1 sentence."},
                {"type": "image_url", "image_url": {"url": base64_image}}
            ]}
        ],
        "max_tokens": 150
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
            if res.status_code == 200: return f"[User uploaded image: {res.json()['choices'][0]['message']['content']}]"
    except: pass
    return "[User uploaded image]"


# --- AI TRIGGER (ROOM AWARE, LUDO AWARE) ---
async def trigger_ai_evaluations(room_id, chain_count=0):
    if chain_count >= MAX_BOT_CONVERSATION_CHAIN: return 

    await asyncio.sleep(2)
    
    # Get history SPECIFIC to this room
    current_history = get_room_history(room_id)
    
    # Get current Ludo context for this room
    ludo_context = get_ludo_context(room_id)
    
    tasks = [
        fetch_groq("Groq-AI", current_history, ludo_context), 
        fetch_openrouter("Router-AI", current_history, ludo_context)
    ]
    results = await asyncio.gather(*tasks)
    
    bot_names = ["Groq-AI", "Router-AI"]
    anyone_spoke = False
    
    for i, reply in enumerate(results):
        clean = reply.strip().upper().replace(".", "")
        if not clean.startswith("SKIP") and len(reply) > 2:
            anyone_spoke = True
            bot_name = bot_names[i]
            if reply.startswith(f"{bot_name}:"): reply = reply[len(bot_name)+1:].strip()

            # Add to THIS room's history
            add_to_history(room_id, bot_name, reply)
            
            # Broadcast ONLY to this room
            await manager.broadcast_to_room(reply, bot_name, room_id)
            await asyncio.sleep(1.5) 

    # Recursive call to allow bots to reply to each other
    if anyone_spoke: 
        await trigger_ai_evaluations(room_id, chain_count + 1)

# --- WEBSOCKET ROUTE ---
@app.websocket("/ws/{room_id}/{username}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, username: str):
    await manager.connect(websocket, room_id)
    
    # Send existing history to new user (skip raw ludo blobs)
    existing_history = get_room_history(room_id)
    if existing_history:
        for msg in existing_history:
            # Don't replay raw ludo state messages to new joiners
            if not msg.get("message", "").startswith("__LUDO__:"):
                await websocket.send_text(json.dumps(msg))

    # If there's an active ludo game, send current state summary to new joiner
    if room_id in room_ludo_state:
        ludo_state = room_ludo_state[room_id]
        welcome_ludo = f"[Ludo game in progress: {ludo_state.get('summary', '')}]"
        await websocket.send_text(json.dumps({
            "sender": "System",
            "message": welcome_ludo,
            "image": None,
            "room": room_id
        }))

    await manager.broadcast_to_room(f"{username} joined Room {room_id}!", "System", room_id)
    
    try:
        while True:
            data_str = await websocket.receive_text()
            try:
                data = json.loads(data_str)
                message_text = data.get("message", "")
                image_data = data.get("image", None)
                
                # Check if this is a Ludo game state update
                ludo_data = parse_ludo_message(message_text)
                
                if ludo_data:
                    # Update stored game state for this room
                    update_ludo_state(room_id, ludo_data)
                    
                    # Store a human-readable version in history for AI context
                    readable = f"[Ludo: {ludo_data.get('event', 'game update')}] {ludo_data.get('summary', '')}"
                    add_to_history(room_id, username, f"__LUDO__:{json.dumps(ludo_data)}")
                    
                    # Broadcast the full ludo message to all room members (so their boards stay in sync)
                    await manager.broadcast_to_room(message_text, username, room_id)
                    
                    # Trigger AI to react to the Ludo move
                    asyncio.create_task(trigger_ai_evaluations(room_id, chain_count=0))
                
                elif image_data:
                    desc = await describe_image(image_data)
                    add_to_history(room_id, username, desc)
                    await manager.broadcast_to_room(desc, username, room_id, image=image_data)
                    asyncio.create_task(trigger_ai_evaluations(room_id, chain_count=0))
                
                else:
                    add_to_history(room_id, username, message_text)
                    await manager.broadcast_to_room(message_text, username, room_id)
                    asyncio.create_task(trigger_ai_evaluations(room_id, chain_count=0))
            
            except json.JSONDecodeError:
                add_to_history(room_id, username, data_str)
                await manager.broadcast_to_room(data_str, username, room_id)
                asyncio.create_task(trigger_ai_evaluations(room_id, chain_count=0))
                
    except WebSocketDisconnect:
        manager.disconnect(websocket, room_id)
        await manager.broadcast_to_room(f"{username} left.", "System", room_id)