from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import httpx
import asyncio
import json
import os

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
        # We now support sending an 'image' field in the JSON
        payload = json.dumps({"sender": sender, "message": message, "image": image})
        for connection in self.active_connections:
            await connection.send_text(payload)

manager = ConnectionManager()

# --- SHARED CONTEXT ---
chat_history = []
MAX_HISTORY = 10
MAX_BOT_CONVERSATION_CHAIN = 3

# --- 1. THE VISION EYE (Groq) ---
async def describe_image(base64_image):
    """
    Sends the image to Groq's free vision model to get a text description.
    This allows the text-only bots to 'see' the image.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key: return "[Image uploaded, but Vision AI is disabled (No Key)]"

    headers = {"Authorization": f"Bearer {api_key}"}
    
    payload = {
        "model": "meta-llama/llama-4-maverick-17b-128e-instruct", # Free, fast vision model
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image in 1 sentence. Focus on funny or interesting details."},
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
                print(f"Vision Error: {response.text}")
                return "[User uploaded an image, but Vision AI failed to read it.]"
    except Exception as e:
        print(f"Vision Request Failed: {e}")
        return "[User uploaded an image]"

# --- 2. THE TEXT BRAINS ---

def build_messages_payload(bot_name: str, persona: str):
    system_instruction = (
        f"You are {bot_name}. {persona} "
        "Speak in 'Brolang' / Gen-Z slang (fr, ngl, bet, cooked). "
        "If you see a system message like '[User uploaded an image...]', react to the description as if you are seeing the photo! "
        "CRITICAL: ONLY output your response. If you have nothing to say, output: SKIP."
    )
    
    messages = [{"role": "system", "content": system_instruction}]
    
    for msg in chat_history:
        # We only feed the TEXT message to the bots (which includes the vision description)
        # We do NOT send them the base64 image data to avoid crashing them.
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
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key: return "SKIP"
    
    persona = "You are the wild, funny bro. You roast people."
    messages = build_messages_payload(bot_name, persona)
    
    # Using the free model we configured earlier
    payload = {
        "model": "x-ai/grok-4.1-fast",
        "messages": messages,
        "temperature": 1.0,
        "provider": { "order": ["Free"] }
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions", 
                headers={"Authorization": f"Bearer {api_key}", "HTTP-Referer": "http://localhost", "X-Title": "SquadChat"}, 
                json=payload, 
                timeout=30.0
            )
            if response.status_code != 200: return "SKIP"
            return response.json()["choices"][0]["message"]["content"].strip()
    except: return "SKIP"

# --- AUTONOMOUS CHAT ENGINE ---
async def trigger_ai_evaluations(chain_count=0):
    if chain_count >= MAX_BOT_CONVERSATION_CHAIN: return 

    await asyncio.sleep(2)
    tasks = [fetch_groq("Groq-AI"), fetch_openrouter("Router-AI")]
    results = await asyncio.gather(*tasks)
    bot_names = ["Groq-AI", "Router-AI"]
    anyone_spoke = False
    
    for i, reply in enumerate(results):
        clean = reply.strip().upper().replace(".", "")
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
            # Receive data (Could be text OR image JSON)
            data_str = await websocket.receive_text()
            
            try:
                data = json.loads(data_str)
                message_text = data.get("message", "")
                image_data = data.get("image", None) # Base64 string
                
                # --- CASE 1: IMAGE UPLOAD ---
                if image_data:
                    # 1. Analyze image with Vision AI
                    description = await describe_image(image_data)
                    
                    # 2. Save the DESCRIPTION to history (so bots can read it)
                    chat_history.append({"sender": username, "message": description})
                    
                    # 3. Broadcast the ACTUAL IMAGE to humans
                    # We send the description as the 'message' so the UI can show the AI's analysis too
                    await manager.broadcast(description, username, image=image_data)
                
                # --- CASE 2: NORMAL TEXT ---
                else:
                    chat_history.append({"sender": username, "message": message_text})
                    await manager.broadcast(message_text, username)
            
            except json.JSONDecodeError:
                # Fallback for plain text (legacy)
                chat_history.append({"sender": username, "message": data_str})
                await manager.broadcast(data_str, username)

            if len(chat_history) > MAX_HISTORY: chat_history.pop(0)
            
            # Trigger the bots (they will react to the description if it was an image)
            asyncio.create_task(trigger_ai_evaluations(chain_count=0))
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        await manager.broadcast(f"{username} left the chat.", "System")