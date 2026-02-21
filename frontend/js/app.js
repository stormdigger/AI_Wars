/**
 * Squad.GG – Main App Controller (app.js)
 *
 * Handles:
 *  - WebSocket connection & message routing
 *  - Chat rendering (Instagram Live overlay style)
 *  - Lobby & ready system
 *  - Join vote handling
 *  - Game coordination (Ludo ↔ Chess tab switching)
 *  - Game special event → backend broadcast
 *  - Mobile/desktop chat toggle
 */

(function () {
  "use strict";

  // ── Config ─────────────────────────────────────────────────
  const WS_HOST = "ai-wars.onrender.com";  // ← change if needed

  // ── State ──────────────────────────────────────────────────
  let ws           = null;
  let myUsername   = "";
  let myRoom       = "";
  let activeGame   = "ludo";   // "ludo" | "chess"
  let gameStarted  = false;
  let isSpectator  = false;
  let myColor      = "";       // assigned color for current game
  let lobbyPlayers = {};       // username → {ready, color}
  let iAmHost      = false;
  let unreadCount  = 0;
  let chatExpanded = false;
  let pendingVote  = null;     // {requester, timer}
  let selectedGameType = "ludo";

  // ── DOM refs (populated after DOMContentLoaded) ─────────────
  let dom = {};

  // ── Init ────────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", () => {
    dom = {
      setupScreen:    document.getElementById("setup-screen"),
      app:            document.getElementById("app"),
      inpUser:        document.getElementById("inp-user"),
      inpRoom:        document.getElementById("inp-room"),
      roomBadge:      document.getElementById("room-badge"),
      chatOverlay:    document.getElementById("chat-overlay"),
      chatMessages:   document.getElementById("chat-messages"),
      chatInput:      document.getElementById("chat-inp"),
      chatBadge:      document.getElementById("chat-badge"),
      chatToggleBtn:  document.getElementById("chat-toggle-btn"),
      gameLog:        document.getElementById("game-log"),
      lobbyView:      document.getElementById("lobby-view"),
      gameView:       document.getElementById("game-view"),
      ludoPane:       document.getElementById("pane-ludo"),
      chessPane:      document.getElementById("pane-chess"),
      ludoBoardWrap:  document.getElementById("ludo-board-wrap"),
      voteBanner:     document.getElementById("vote-banner"),
      tabLudo:        document.getElementById("tab-ludo"),
      tabChess:       document.getElementById("tab-chess"),
    };

    // Keyboard shortcut: Enter to join
    dom.inpRoom.addEventListener("keypress", e => { if (e.key==="Enter") joinRoom(); });
    dom.inpUser.addEventListener("keypress", e => { if (e.key==="Enter") dom.inpRoom.focus(); });

    // Restore last session (UX nicety)
    const saved = sessionStorage.getItem("squad_username");
    if (saved) dom.inpUser.value = saved;
  });

  // ── Join ────────────────────────────────────────────────────
  window.joinRoom = function () {
    myUsername = (dom.inpUser.value || "").trim();
    myRoom     = (dom.inpRoom.value || "").trim();
    if (!myUsername || !myRoom) { showToast("Enter a name and room ID!"); return; }

    sessionStorage.setItem("squad_username", myUsername);
    dom.setupScreen.style.display = "none";
    dom.app.classList.add("visible");
    dom.roomBadge.textContent = "Room " + myRoom;

    connectWS();
  };

  // ── WebSocket ───────────────────────────────────────────────
  function connectWS() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${WS_HOST}/ws/${encodeURIComponent(myRoom)}/${encodeURIComponent(myUsername)}`);

    ws.onopen = () => { console.log("[WS] connected"); };

    ws.onmessage = e => {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      routeMessage(msg);
    };

    ws.onclose = () => {
      appendSystemMsg("⚠️ Disconnected. Reconnecting in 3s…");
      setTimeout(connectWS, 3000);
    };

    ws.onerror = () => ws.close();
  }

  function wsSend(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
    }
  }

  // ── Message Router ──────────────────────────────────────────
  function routeMessage(msg) {
    switch (msg.type) {
      case "chat":
        appendChatMsg(msg.sender, msg.message, msg.image);
        if (!chatExpanded) bumpUnread();
        break;

      case "system":
        appendSystemMsg(msg.message);
        break;

      case "lobby_update":
        handleLobbyUpdate(msg);
        break;

      case "game_start":
        handleGameStart(msg);
        break;

      case "game_sync":
        handleGameSync(msg);
        break;

      case "vote_request":
        handleVoteRequest(msg);
        break;

      case "vote_result":
        appendSystemMsg(msg.message);
        if (pendingVote && pendingVote.timer) clearTimeout(pendingVote.timer);
        pendingVote = null;
        hideBanner();
        break;

      default:
        // Legacy format: old-style {sender, message}
        if (msg.sender && msg.message) {
          appendChatMsg(msg.sender, msg.message, msg.image);
        }
        break;
    }
  }

  // ── Chat Rendering ─────────────────────────────────────────
  function appendChatMsg(sender, text, image) {
    const box = dom.chatMessages;
    const wrap = document.createElement("div");

    let cls = "msg-other";
    if      (sender === myUsername)          cls = "msg-me";
    else if (sender === "System")            { appendSystemMsg(text); return; }
    else if (sender.includes("Groq"))        cls = "msg-groq";
    else if (sender.includes("Router"))      cls = "msg-router";

    wrap.className = "msg " + cls;

    if (cls !== "msg-me") {
      const n = document.createElement("div");
      n.className = "msg-name";
      n.textContent = sender;
      wrap.appendChild(n);
    }

    const b = document.createElement("div");
    b.className = "msg-bubble";

    if (image) {
      const img = document.createElement("img");
      img.src = image;
      img.style.cssText = "max-width:100%;border-radius:6px;display:block;";
      b.appendChild(img);
      if (text) {
        const t = document.createElement("div");
        t.style.cssText = "font-size:.78em;opacity:.7;margin-top:4px;";
        t.textContent = text;
        b.appendChild(t);
      }
    } else {
      b.textContent = text;
    }

    wrap.appendChild(b);
    box.appendChild(wrap);
    trimChatOverflow();
    box.scrollTop = box.scrollHeight;
  }

  function appendSystemMsg(text) {
    const box = dom.chatMessages;
    const d = document.createElement("div");
    d.className = "msg-system";
    d.textContent = text;
    box.appendChild(d);
    box.scrollTop = box.scrollHeight;
  }

  function appendGameEvent(text) {
    const box = dom.chatMessages;
    const d = document.createElement("div");
    d.className = "msg-game-event";
    d.textContent = "🎮 " + text;
    box.appendChild(d);
    box.scrollTop = box.scrollHeight;
    if (!chatExpanded) bumpUnread();
  }

  // Keep overlay from growing too large in collapsed mode
  function trimChatOverflow() {
    if (chatExpanded) return;
    const children = dom.chatMessages.children;
    while (children.length > 40) dom.chatMessages.removeChild(children[0]);
  }

  function bumpUnread() {
    unreadCount++;
    dom.chatBadge.textContent = unreadCount > 9 ? "9+" : unreadCount;
    dom.chatBadge.classList.add("visible");
  }

  // ── Chat controls ──────────────────────────────────────────
  window.toggleChat = function () {
    chatExpanded = !chatExpanded;
    dom.chatOverlay.classList.toggle("expanded", chatExpanded);
    dom.chatOverlay.classList.toggle("collapsed", !chatExpanded);
    if (chatExpanded) {
      unreadCount = 0;
      dom.chatBadge.classList.remove("visible");
      dom.chatMessages.scrollTop = dom.chatMessages.scrollHeight;
    }
  };

  window.sendMsg = function () {
    const inp = dom.chatInput;
    const text = inp.value.trim();
    if (!text) return;
    wsSend({ type: "chat", sender: myUsername, message: text, image: null });
    inp.value = "";
    // Also show locally immediately
    appendChatMsg(myUsername, text, null);
  };

  window.sendMsgOnEnter = function (e) { if (e.key === "Enter") sendMsg(); };

  window.handleFile = function (inp) {
    const f = inp.files[0]; if (!f) return;
    if (f.size > 2 * 1024 * 1024) { showToast("Max image size: 2MB"); return; }
    const r = new FileReader();
    r.onload = e => {
      wsSend({ type: "chat", sender: myUsername, message: "", image: e.target.result });
      appendChatMsg(myUsername, "", e.target.result);
    };
    r.readAsDataURL(f);
    inp.value = "";
  };

  // ── Lobby ──────────────────────────────────────────────────
  function handleLobbyUpdate(msg) {
    lobbyPlayers = msg.players || {};
    iAmHost      = msg.host === myUsername;
    isSpectator  = msg.game_active && !(myUsername in lobbyPlayers);

    renderLobby();

    if (isSpectator) {
      showToast("You joined as spectator — play next round!");
    }
  }

  function renderLobby() {
    const listEl = document.getElementById("lobby-player-list");
    if (!listEl) return;
    listEl.innerHTML = "";

    for (const [username, info] of Object.entries(lobbyPlayers)) {
      const row = document.createElement("div");
      row.className = "player-row" + (username === myUsername ? " is-you" : "");

      const dot = document.createElement("div");
      dot.className = "player-dot";
      dot.style.background = colorToCSS(info.color) || "#666";

      const name = document.createElement("span");
      name.className = "player-name";
      name.textContent = username + (username === myUsername ? " (you)" : "");

      const badges = document.createElement("div");
      badges.style.cssText = "display:flex;gap:5px;align-items:center;";

      if (username === myUsername && iAmHost) {
        const hb = document.createElement("span");
        hb.className = "player-host-badge"; hb.textContent = "HOST";
        badges.appendChild(hb);
      }

      const rb = document.createElement("span");
      rb.className = "player-ready-badge " + (info.ready ? "ready" : "not-ready");
      rb.textContent = info.ready ? "✓ Ready" : "Not ready";
      badges.appendChild(rb);

      row.appendChild(dot); row.appendChild(name); row.appendChild(badges);
      listEl.appendChild(row);
    }

    // Show/hide start button (host only, 1+ ready)
    const startBtn = document.getElementById("start-game-btn");
    if (startBtn) {
      const readyCount = Object.values(lobbyPlayers).filter(p => p.ready).length;
      startBtn.disabled = !(iAmHost && readyCount >= 1);
      startBtn.style.display = iAmHost ? "block" : "none";
    }

    // Show game view if game active
    const gameActive = Object.values(lobbyPlayers).some(p => p.color);
    if (!gameActive) showLobby();
  }

  window.toggleReady = function () {
    const isReady = lobbyPlayers[myUsername]?.ready;
    wsSend({ type: "ready", ready: !isReady });
  };

  window.startGame = function () {
    if (!iAmHost) return;
    wsSend({ type: "start_game", game_type: selectedGameType });
  };

  window.selectGameType = function (type) {
    selectedGameType = type;
    document.querySelectorAll(".game-type-btn").forEach(b => {
      b.classList.toggle("selected", b.dataset.type === type);
    });
  };

  // ── Game Start ─────────────────────────────────────────────
  function handleGameStart(msg) {
    gameStarted = true;
    activeGame  = msg.game_type;
    myColor     = msg.assignments?.[myUsername] || "";
    isSpectator = !(myUsername in (msg.assignments || {}));

    appendSystemMsg(msg.message);
    appendGameEvent(`${msg.game_type.toUpperCase()} game started! Good luck!`);

    showGame(activeGame);

    if (activeGame === "ludo") {
      startLudoGame(msg);
    } else {
      startChessGame(msg);
    }
  }

  function handleGameSync(msg) {
    // Another client broadcasted game state — sync our board
    if (msg.sender === myUsername) return; // our own state, skip
    if (msg.game_type === "ludo" && window.LudoGame) {
      LudoGame.syncState(msg.payload);
    }
    // Chess AI is local, no sync needed for chess unless multiplayer
  }

  // ── Game Initialization ─────────────────────────────────────
  function startLudoGame(msg) {
    const assignments = msg.assignments || {};
    const names  = { red: null, green: "Groq-AI", blue: "Router-AI" };
    const humans = { red: false, green: false, blue: false };

    for (const [username, color] of Object.entries(assignments)) {
      if (color in names) {
        names[color]  = username;
        humans[color] = true;
      }
    }
    // Red is always the local player if they have a slot
    if (!names.red) names.red = "You";

    LudoGame.init({
      names, humans,
      callbacks: {
        onSpecialEvent: (eventType, detail, summary) => {
          appendGameEvent(detail);
          broadcastGameEvent("ludo", eventType, detail, summary, LudoGame.getState());
        },
        onMove: (eventType, detail, summary, state) => {
          broadcastGameState("ludo", eventType, detail, summary, state);
        },
        onGameEnd: (winnerColor, winnerName) => {
          appendGameEvent(`🏆 ${winnerName} WINS the Ludo game!`);
        },
        onLog: (msg) => {
          const el = document.getElementById("game-log");
          if (el) { el.innerHTML += msg + "<br>"; el.scrollTop = el.scrollHeight; }
        },
      },
    });
  }

  function startChessGame(msg) {
    const assignments = msg.assignments || {};
    const names  = { w: myUsername || "You", b: "Groq-AI" };
    const humans = { w: true, b: false };

    // Check if black slot is taken by a human
    for (const [username, color] of Object.entries(assignments)) {
      if (color === "black") { names.b = username; humans.b = true; }
      if (color === "white") { names.w = username; }
    }

    ChessGame.init({
      names, humans,
      callbacks: {
        onSpecialEvent: (eventType, detail) => {
          appendGameEvent(detail);
          broadcastGameEvent("chess", eventType, detail, "", ChessGame.getState());
        },
        onMove: (eventType, detail, state) => {
          broadcastGameState("chess", eventType, detail, "", state);
        },
        onGameEnd: (winner, name) => {
          appendGameEvent(`🏆 ${name} WINS the Chess game!`);
        },
      },
    });
  }

  // ── Game Broadcasting ──────────────────────────────────────
  const SPECIAL_EVENTS = new Set([
    "capture","dice_six","token_enter","home_stretch",
    "near_win","win","comeback",
    "check","checkmate","stalemate","queen_capture",
    "promotion","castling","rook_capture",
  ]);

  function broadcastGameState(gameType, eventType, detail, summary, state) {
    const isSpecial = SPECIAL_EVENTS.has(eventType);
    const payload = {
      event:      detail,
      event_type: eventType,
      is_special: isSpecial,
      summary:    summary,
      ...(state || {}),
    };
    wsSend({
      type:    "game_update",
      message: `__${gameType.toUpperCase()}__:${JSON.stringify(payload)}`,
      sender:  myUsername,
    });
  }

  function broadcastGameEvent(gameType, eventType, detail, summary, state) {
    broadcastGameState(gameType, eventType, detail, summary, state);
  }

  // ── Vote System ────────────────────────────────────────────
  function handleVoteRequest(msg) {
    const requester = msg.requester;
    if (!(myUsername in lobbyPlayers)) return; // spectators don't vote

    pendingVote = { requester };

    // Show vote banner
    const banner = dom.voteBanner;
    if (!banner) return;
    banner.querySelector(".vote-msg").textContent = msg.message || `${requester} wants to join!`;
    banner.style.display = "flex";

    // Auto-resolve after timeout (UI side)
    pendingVote.timer = setTimeout(() => hideBanner(), (msg.timeout || 20) * 1000 + 1000);
  }

  window.castVote = function (accepted) {
    if (!pendingVote) return;
    wsSend({ type: "vote", vote: accepted });
    hideBanner();
  };

  function hideBanner() {
    const b = dom.voteBanner;
    if (b) b.style.display = "none";
  }

  // ── View Switching ──────────────────────────────────────────
  function showLobby() {
    if (dom.lobbyView)  dom.lobbyView.style.display  = "flex";
    if (dom.gameView)   dom.gameView.style.display    = "none";
  }

  function showGame(type) {
    if (dom.lobbyView) dom.lobbyView.style.display  = "none";
    if (dom.gameView)  dom.gameView.style.display   = "flex";

    // Tab switch to correct game
    switchTab(type);
  }

  window.switchTab = function (name) {
    activeGame = name;
    document.querySelectorAll(".tab-btn").forEach(b => {
      b.classList.toggle("active", b.dataset.tab === name);
    });
    if (dom.ludoPane)  dom.ludoPane.classList.toggle("hidden",  name !== "ludo");
    if (dom.chessPane) dom.chessPane.classList.toggle("hidden", name !== "chess");
  };

  window.newGame = function (type) {
    if (type === "ludo") {
      LudoGame.reset();
      broadcastGameEvent("ludo", "game_reset", "New Ludo game started!", "", LudoGame.getState());
    } else {
      ChessGame.reset();
      broadcastGameEvent("chess", "game_reset", "New Chess game started!", "", ChessGame.getState());
    }
  };

  // ── Utility ────────────────────────────────────────────────
  function colorToCSS(color) {
    const map = { red:"#ff5577", green:"#44dd88", blue:"#4488ff",
                  yellow:"#ffcc44", white:"#eee", black:"#555" };
    return map[color] || "#888";
  }

  window.showToast = function (msg, duration = 3000) {
    const t = document.createElement("div");
    t.className = "toast"; t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), duration);
  };

  // Initialize chat overlay as collapsed on mobile
  window.addEventListener("load", () => {
    if (dom.chatOverlay) {
      dom.chatOverlay.classList.add("collapsed");
    }
  });

})();