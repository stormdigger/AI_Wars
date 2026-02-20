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

# --- STARTUP CHECKS ---
@app.on_event("startup")
async def startup_event():
    print("--- SERVER STARTUP ---")
    if os.getenv("GROQ_API_KEY"): print("✅ GROQ Key Found")
    else: print("❌ GROQ Key Missing")
    if os.getenv("OPENROUTER_API_KEY"): print("✅ OPENROUTER Key Found")
    else: print("❌ OPENROUTER Key Missing")

# --- LUDO GAME ENGINE ---
class LudoGame:
    def __init__(self):
        self.players = {} # Map color -> username
        self.turn_order = ["red", "green", "yellow", "blue"]
        self.current_turn_index = 0
        self.dice_value = 0
        self.waiting_for_move = False
        # Positions: -1 = Base, 0-51 = Main Path, 52-57 = Home Stretch, 99 = Home/Win
        self.pieces = {
            "red": [-1, -1, -1, -1],
            "green": [-1, -1, -1, -1],
            "yellow": [-1, -1, -1, -1],
            "blue": [-1, -1, -1, -1]
        }
        # Start offsets for each color on the main path (0-51)
        self.start_offsets = {"red": 0, "green": 13, "yellow": 26, "blue": 39}

    def join(self, color, username):
        if color in self.players: return False
        self.players[color] = username
        return True

    def roll_dice(self):
        self.dice_value = random.randint(1, 6)
        self.waiting_for_move = True
        
        # Auto-skip if no moves possible (simple logic: all in base and no 6)
        current_color = self.turn_order[self.current_turn_index]
        my_pieces = self.pieces[current_color]
        can_move = False
        for p in my_pieces:
            if p != -1: can_move = True # Piece on board
            if p == -1 and self.dice_value == 6: can_move = True # Can launch
        
        if not can_move:
            self.waiting_for_move = False
            self.next_turn()
            return f"Rolled a {self.dice_value}, but no moves possible!"
        return f"Rolled a {self.dice_value}!"

    def move_piece(self, piece_index):
        if not self.waiting_for_move: return False
        color = self.turn_order[self.current_turn_index]
        pos = self.pieces[color][piece_index]

        # Rule: Need 6 to start
        if pos == -1:
            if self.dice_value == 6:
                self.pieces[color][piece_index] = 0 # Move to start
                self.waiting_for_move = False
                return True
            return False

        # Move logic
        new_pos = pos + self.dice_value
        if new_pos > 57: return False # Can't overshoot home
        
        self.pieces[color][piece_index] = new_pos
        
        # Check for cuts (collision)
        self.check_cut(color, new_pos)
        
        self.waiting_for_move = False
        if self.dice_value != 6: self.next_turn()
        return True

    def check_cut(self, my_color, my_relative_pos):
        # Convert relative pos to global board index (0-51)
        if my_relative_pos > 51: return # Safe in home stretch
        
        my_global = (my_relative_pos + self.start_offsets[my_color]) % 52
        
        for enemy_color, positions in self.pieces.items():
            if enemy_color == my_color: continue
            for i, enemy_pos in enumerate(positions):
                if enemy_pos != -1 and enemy_pos <= 51:
                    enemy_global = (enemy_pos + self.start_offsets[enemy_color]) % 52
                    if enemy_global == my_global:
                        # CUT! Send enemy back to base
                        self.pieces[enemy_color][i] = -1

    def next_turn(self):
        # Simple round robin, skip empty seats
        start_index = self.current_turn_index
        while True:
            self.current_turn_index = (self.current_turn_index + 1) % 4
            next_color = self.turn_order[self.current_turn_index]
            if next_color in self.players: break
            if self.current_turn_index == start_index: break # Should not happen if game started

    def get_state(self):
        return {
            "players": self.players,
            "turn": self.turn_order[self.current_turn_index],
            "dice": self.dice_value,
            "pieces": self.pieces,
            "waiting": self.waiting_for_move
        }

# --- ROOM & CONNECTION MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.active_rooms: dict[str, list[WebSocket]] = {}
        self.active_games: dict[str, LudoGame] = {}

    async def connect(self, websocket: WebSocket, room_id: str):
        await websocket.accept()
        if room_id not in self.active_rooms:
            self.active_rooms[room_id] = []
        self.active_rooms[room_id].append(websocket)

    def disconnect(self, websocket: WebSocket, room_id: str):
        if room_id in self.active_rooms:
            if websocket in self.active_rooms[room_id]:
                self.active_rooms[room_id].remove(websocket)

    async def broadcast_to_room(self, message: str, sender: str, room_id: str, image: str = None, game_state: dict = None):
        if room_id not in self.active_rooms: return
        payload = json.dumps({"sender": sender, "message": message, "image": image, "room": room_id, "game_state": game_state})
        for connection in self.active_rooms[room_id][:]:
            try:
                await connection.send_text(payload)
            except:
                self.disconnect(connection, room_id)

manager = ConnectionManager()

# --- CHAT HISTORY ---
room_histories = {} 
MAX_HISTORY = 10

def add_to_history(room_id, sender, message):
    if room_id not in room_histories: room_histories[room_id] = []
    room_histories[room_id].append({"sender": sender, "message": message})
    if len(room_histories[room_id]) > MAX_HISTORY: room_histories[room_id].pop(0)

# --- AI LOGIC (WITH FIXES) ---

def build_messages_payload(bot_name: str, persona: str, current_room_history: list):
    system_instruction = (
        f"You are {bot_name}. {persona} "
        "Speak in 'Brolang' / Gen-Z slang. "
        "CRITICAL: ONLY output your response. If you have nothing to say, output: SKIP."
    )
    messages = [{"role": "system", "content": system_instruction}]
    for msg in current_room_history:
        messages.append({"role": "user", "content": f"{msg['sender']}: {msg['message']}"})
    return messages

async def fetch_ai_response(bot_name, persona, room_history, api_key, url, model):
    if not api_key: return "SKIP"
    
    # --- FIX 1: STOP SELF-REPLY LOOPS ---
    if room_history:
        last_sender = room_history[-1]["sender"]
        if last_sender == bot_name: 
            return "SKIP" # Don't reply if I just spoke

    messages = build_messages_payload(bot_name, persona, room_history)
    headers = {"Authorization": f"Bearer {api_key}", "HTTP-Referer": "https://render.com", "X-Title": "SquadChat"}
    
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, headers=headers, json={"model": model, "messages": messages, "temperature": 0.9}, timeout=15.0)
            if res.status_code == 200: return res.json()["choices"][0]["message"]["content"].strip()
    except: pass
    return "SKIP"

async def trigger_ai_evaluations(room_id, chain_count=0):
    if chain_count >= 3: return 
    await asyncio.sleep(2)
    
    hist = room_histories.get(room_id, [])
    
    # GROQ
    groq_resp = await fetch_ai_response("Groq-AI", "Smart tech bro.", hist, os.getenv("GROQ_API_KEY"), "https://api.groq.com/openai/v1/chat/completions", "llama-3.3-70b-versatile")
    
    # OPENROUTER (With fallback logic simplified for brevity, assuming standard call)
    router_resp = await fetch_ai_response("Router-AI", "Wild funny bro.", hist, os.getenv("OPENROUTER_API_KEY"), "https://openrouter.ai/api/v1/chat/completions", "meta-llama/llama-3-8b-instruct:free")
    
    responses = [("Groq-AI", groq_resp), ("Router-AI", router_resp)]
    anyone_spoke = False
    
    for bot, reply in responses:
        clean = reply.strip().upper().replace(".", "")
        if not clean.startswith("SKIP") and len(reply) > 2:
            anyone_spoke = True
            add_to_history(room_id, bot, reply)
            await manager.broadcast_to_room(reply, bot, room_id)
            await asyncio.sleep(1.5)

    if anyone_spoke: await trigger_ai_evaluations(room_id, chain_count + 1)

# --- WEBSOCKET ROUTE ---
@app.websocket("/ws/{room_id}/{username}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, username: str):
    await manager.connect(websocket, room_id)
    
    # Send current game state if exists
    if room_id in manager.active_games:
        await websocket.send_text(json.dumps({"game_state": manager.active_games[room_id].get_state()}))

    await manager.broadcast_to_room(f"{username} joined!", "System", room_id)
    
    try:
        while True:
            data_str = await websocket.receive_text()
            try:
                data = json.loads(data_str)
                type = data.get("type", "chat")
                
                # --- CHAT HANDLING ---
                if type == "chat":
                    msg = data.get("message", "")
                    img = data.get("image", None)
                    if img: msg = "[Image Uploaded]" # Placeholder for logic simplicity
                    
                    add_to_history(room_id, username, msg)
                    await manager.broadcast_to_room(msg, username, room_id, image=img)
                    asyncio.create_task(trigger_ai_evaluations(room_id))

                # --- GAME HANDLING ---
                elif type == "game":
                    action = data.get("action")
                    if room_id not in manager.active_games: manager.active_games[room_id] = LudoGame()
                    game = manager.active_games[room_id]
                    
                    response_msg = None
                    
                    if action == "join":
                        color = data.get("color")
                        if game.join(color, username): response_msg = f"{username} sat at {color}!"
                    
                    elif action == "roll":
                        response_msg = game.roll_dice()
                        
                    elif action == "move":
                        idx = data.get("piece_index")
                        if game.move_piece(idx): response_msg = f"{username} moved piece!"
                    
                    # Broadcast Update
                    state = game.get_state()
                    await manager.broadcast_to_room(response_msg if response_msg else "", "GameBot", room_id, game_state=state)

            except Exception as e: print(f"Error: {e}")
                
    except WebSocketDisconnect:
        manager.disconnect(websocket, room_id)