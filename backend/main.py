from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import httpx
import asyncio
import json
import os
import sys

# Load .env for local dev, but Render uses its own system
load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- STARTUP DIAGNOSTICS ---
@app.on_event("startup")
async def startup_event():
    print("--- SERVER STARTUP CHECKS ---")
    groq_key = os.getenv("GROQ_API_KEY")
    router_key = os.getenv("OPENROUTER_API_KEY")
    
    if groq_key:
        print(f"✅ GROQ_API_KEY found: {groq_key[:5]}...****")
    else:
        print("❌ CRITICAL: GROQ_API_KEY is MISSING!")
        
    if router_key:
        print(f"✅ OPENROUTER_API_KEY found: {router_key[:5]}...****")
    else:
        print("❌ CRITICAL: OPENROUTER_API_KEY is MISSING!")
    print("-----------------------------")

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str, sender: str, image: str = None):
        payload = json.dumps({"sender": sender, "message": message, "image": image})
        for connection in self.active_connections[:]:
            try:
                await connection.send_text(payload)
            except Exception as e:
                print(f"Removing dead connection: {e}")
                self.disconnect(connection)

manager = ConnectionManager()

# --- SHARED CONTEXT ---
chat_history = []
MAX_HISTORY = 10
MAX_BOT_CONVERSATION_CHAIN = 3

# --- 1. THE VISION EYE (Groq) ---
async def describe_image(base64_image):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key: return "[Image uploaded, but Vision AI is disabled (No Key)]"

    headers = {"Authorization": f"Bearer {api_key}"}
    
    payload = {
        "model": "llama-3.2-11b-vision-preview",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image in 1 sentence. Focus on funny details."},
                    {"type": "image_url", "image_url": {"url": base64_image}}
                ]
            }
        ],
        "temperature": 0.5,
        "max_tokens": 150
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=30.0)
            if response.status_code == 200:
                description = response.json()["choices"][0]["message"]["content"]
                return f"[User uploaded an image. Vision AI analysis: {description}]"
            else:
                return "[User uploaded an image, but Vision AI failed.]"
    except Exception as e:
        print(f"Vision Error: {e}")
        return "[User uploaded an image]"

# --- 2. THE TEXT BRAINS ---

def build_messages_payload(bot_name: str, persona: str):
    system_instruction = (
        f"You are {bot_name}. {persona} "
        "Speak in 'Brolang' / Gen-Z slang. "
        "CRITICAL: ONLY output your response. If you have nothing to say, output: SKIP."
    )
    
    messages = [{"role": "system", "content": system_instruction}]
    
    for msg in chat_history:
        content = msg["message"] 
        if msg["sender"] == bot_name:
            messages.append({"role": "assistant", "content": content})
        else:
            messages.append({"role": "user", "content": f"{msg['sender']}: {content}"})
            
    return messages

async def fetch_groq(bot_name: str):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key: return "SKIP"
    
    persona = "You are the smart tech bro. You give good advice but keep it casual."
    messages = build_messages_payload(bot_name, persona)
    
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

async def fetch_openrouter(bot_name: str):
    # --- DEEP DEBUGGING LOGIC ---
    print(f"[{bot_name}] Starting request process...")
    
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key: 
        print(f"❌ [{bot_name}] ABORTING: API Key is missing in environment!")
        return "SKIP"
    
    persona = "You are the wild, funny bro. You roast people."
    messages = build_messages_payload(bot_name, persona)
    
    headers = {
        "Authorization": f"Bearer {api_key}", 
        "HTTP-Referer": "https://render.com", 
        "X-Title": "SquadChat",
        "Content-Type": "application/json"
    }

    # ATTEMPT 1: GROK
    try:
        print(f"[{bot_name}] Attempting to contact GROK (x-ai/grok-4.1-fast)...")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions", 
                headers=headers, 
                json={
                    "model": "x-ai/grok-4.1-fast",
                    "messages": messages,
                    "temperature": 1.0
                }, 
                timeout=15.0
            )
            
            print(f"[{bot_name}] Grok Response Code: {response.status_code}")
            
            if response.status_code == 200:
                print(f"[{bot_name}] Grok SUCCESS.")
                return response.json()["choices"][0]["message"]["content"].strip()
            else:
                print(f"[{bot_name}] Grok FAILED with body: {response.text}")

    except Exception as e:
        print(f"[{bot_name}] Grok Connection CRASHED: {e}")

    # ATTEMPT 2: FALLBACK
    try:
        print(f"[{bot_name}] Attempting FALLBACK (meta-llama/llama-3-8b-instruct:free)...")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions", 
                headers=headers, 
                json={
                    "model": "meta-llama/llama-3-8b-instruct:free",
                    "messages": messages,
                    "temperature": 1.0
                }, 
                timeout=30.0
            )
            
            print(f"[{bot_name}] Fallback Response Code: {response.status_code}")
            
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"].strip()
            else:
                print(f"[{bot_name}] Fallback FAILED with body: {response.text}")
                
    except Exception as e:
        print(f"[{bot_name}] Fallback Connection CRASHED: {e}")

    return "SKIP"

# --- AUTONOMOUS CHAT ENGINE ---
async def trigger_ai_evaluations(chain_count=0):
    if chain_count >= MAX_BOT_CONVERSATION_CHAIN: return 

    await asyncio.sleep(2)
    
    print("--- Triggering AI Evaluation Round ---")
    tasks = [fetch_groq("Groq-AI"), fetch_openrouter("Router-AI")]
    results = await asyncio.gather(*tasks)
    
    bot_names = ["Groq-AI", "Router-AI"]
    anyone_spoke = False
    
    for i, reply in enumerate(results):
        clean = reply.strip().upper().replace(".", "")
        print(f"Decision from {bot_names[i]}: {reply[:20]}...") # Log decision
        
        if not clean.startswith("SKIP") and len(reply) > 2:
            anyone_spoke = True
            bot_name = bot_names[i]
            if reply.startswith(f"{bot_name}:"): reply = reply[len(bot_name)+1:].strip()

            chat_history.append({"sender": bot_name, "message": reply})
            if len(chat_history) > MAX_HISTORY: chat_history.pop(0)
            await manager.broadcast(reply, bot_name)
            await asyncio.sleep(1.5) 

    if anyone_spoke: await trigger_ai_evaluations(chain_count + 1)

# --- WEBSOCKET ROUTE ---
@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await manager.connect(websocket)
    await manager.broadcast(f"{username} joined the chat!", "System")
    
    try:
        while True:
            data_str = await websocket.receive_text()
            try:
                data = json.loads(data_str)
                message_text = data.get("message", "")
                image_data = data.get("image", None)
                
                if image_data:
                    description = await describe_image(image_data)
                    chat_history.append({"sender": username, "message": description})
                    await manager.broadcast(description, username, image=image_data)
                else:
                    chat_history.append({"sender": username, "message": message_text})
                    await manager.broadcast(message_text, username)
            except json.JSONDecodeError:
                chat_history.append({"sender": username, "message": data_str})
                await manager.broadcast(data_str, username)

            if len(chat_history) > MAX_HISTORY: chat_history.pop(0)
            
            # FIRE THE AI LOGIC
            asyncio.create_task(trigger_ai_evaluations(chain_count=0))
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        await manager.broadcast(f"{username} left the chat.", "System")