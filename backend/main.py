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

# --- AI FUNCTIONS ---

def build_messages_payload(bot_name: str, persona: str, current_room_history: list):
    system_instruction = (
        f"You are {bot_name}. {persona} "
        "Speak in 'Brolang' / Gen-Z slang. "
        "CRITICAL: ONLY output your response. If you have nothing to say, output: SKIP."
    )
    
    messages = [{"role": "system", "content": system_instruction}]
    
    for msg in current_room_history:
        content = msg["message"]
        if msg["sender"] == bot_name:
            messages.append({"role": "assistant", "content": content})
        else:
            messages.append({"role": "user", "content": f"{msg['sender']}: {content}"})
            
    return messages

async def fetch_groq(bot_name: str, room_history: list):
    # --- FIX 1: Prevent Groq from replying to itself ---
    if room_history and room_history[-1]["sender"] == bot_name:
        return "SKIP"

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key: return "SKIP"
    
    persona = "You are the smart tech bro. You give good advice but keep it casual."
    messages = build_messages_payload(bot_name, persona, room_history)
    
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

async def fetch_openrouter(bot_name: str, room_history: list):
    # --- FIX 2: Prevent Router-AI from replying to itself (The Fix You Asked For) ---
    if room_history and room_history[-1]["sender"] == bot_name:
        return "SKIP"

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key: return "SKIP"
    
    persona = "You are the wild, funny bro. You roast people."
    messages = build_messages_payload(bot_name, persona, room_history)
    
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


# --- AI TRIGGER (ROOM AWARE) ---
async def trigger_ai_evaluations(room_id, chain_count=0):
    if chain_count >= MAX_BOT_CONVERSATION_CHAIN: return 

    await asyncio.sleep(2)
    
    # Get history SPECIFIC to this room
    current_history = get_room_history(room_id)
    
    tasks = [
        fetch_groq("Groq-AI", current_history), 
        fetch_openrouter("Router-AI", current_history)
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
    
    # Send existing history to new user
    existing_history = get_room_history(room_id)
    if existing_history:
        for msg in existing_history:
            await websocket.send_text(json.dumps(msg))

    await manager.broadcast_to_room(f"{username} joined Room {room_id}!", "System", room_id)
    
    try:
        while True:
            data_str = await websocket.receive_text()
            try:
                data = json.loads(data_str)
                message_text = data.get("message", "")
                image_data = data.get("image", None)
                
                if image_data:
                    desc = await describe_image(image_data)
                    add_to_history(room_id, username, desc)
                    await manager.broadcast_to_room(desc, username, room_id, image=image_data)
                else:
                    add_to_history(room_id, username, message_text)
                    await manager.broadcast_to_room(message_text, username, room_id)
            
            except json.JSONDecodeError:
                add_to_history(room_id, username, data_str)
                await manager.broadcast_to_room(data_str, username, room_id)
            
            asyncio.create_task(trigger_ai_evaluations(room_id, chain_count=0))
                
    except WebSocketDisconnect:
        manager.disconnect(websocket, room_id)
        await manager.broadcast_to_room(f"{username} left.", "System", room_id)