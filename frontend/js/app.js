// ═══════════════════════════════════════════
// AI SQUAD – App Core (Chat, WebSocket, Game Mode)
// ═══════════════════════════════════════════

let ws = null;
let myUsername = '';
let myRoom = '';
let gameMode = false;
let currentTab = null;  // null = no game tab selected initially
let ludoStarted = false;
let chessStarted = false;

// ── Join Chat ──
function joinChat() {
    myUsername = document.getElementById('inp-user').value.trim();
    myRoom = document.getElementById('inp-room').value.trim();
    if (!myUsername || !myRoom) { alert('Fill both fields!'); return; }

    document.getElementById('setup-screen').style.display = 'none';
    document.getElementById('chat-live').style.display = 'flex';
    document.getElementById('room-badge').textContent = 'Room ' + myRoom;
    document.getElementById('cname-w').textContent = myUsername + ' (White)';

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//ai-wars.onrender.com/ws/${encodeURIComponent(myRoom)}/${encodeURIComponent(myUsername)}`);

    ws.onmessage = e => {
        const d = JSON.parse(e.data);
        if (d.message?.startsWith('__LUDO__:')) { handleLudoSync(d); return; }
        if (d.message?.startsWith('__CHESS__:')) { handleChessSync(d); return; }
        appendMsg(d.sender, d.message, d.image);
        appendLiveMsg(d.sender, d.message);
    };

    ws.onclose = () => {
        appendMsg('System', 'Connection lost. Refresh.');
        appendLiveMsg('System', 'Connection lost.');
    };
}

// ── Start game functions (called from start screens) ──
function startLudoGame() {
    if (!myUsername) { alert('Join a room first!'); return; }
    document.getElementById('ludo-start-screen').style.display = 'none';
    document.getElementById('ludo-game-area').style.display = 'block';
    ludoStarted = true;
    if (typeof ludoStart === 'function') ludoStart();
}

function startChessGame() {
    if (!myUsername) { alert('Join a room first!'); return; }
    document.getElementById('chess-start-screen').style.display = 'none';
    document.getElementById('chess-game-area').style.display = 'block';
    chessStarted = true;
    if (typeof chessInit === 'function') chessInit();
}

// ── File upload ──
function handleFile(inp) {
    const f = inp.files[0];
    if (!f) return;
    if (f.size > 2097152) { alert('Max 2MB!'); return; }
    const r = new FileReader();
    r.onload = e => ws.send(JSON.stringify({ sender: myUsername, message: '', image: e.target.result }));
    r.readAsDataURL(f);
    inp.value = '';
}

// ── Send message ──
function sendMsg() {
    const inp = document.getElementById('chat-inp');
    const t = inp.value.trim();
    if (!t) return;
    ws.send(JSON.stringify({ sender: myUsername, message: t, image: null }));
    inp.value = '';
}

function sendLiveMsg() {
    const inp = document.getElementById('live-chat-inp');
    const t = inp.value.trim();
    if (!t || !ws) return;
    ws.send(JSON.stringify({ sender: myUsername, message: t, image: null }));
    inp.value = '';
}

function wsSend(obj) {
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
}

// ── Append message to main chat ──
function appendMsg(sender, text, image) {
    const box = document.getElementById('chat-msgs');
    const w = document.createElement('div');

    if (sender === 'System') {
        w.className = 'ms';
        w.textContent = text;
        box.appendChild(w);
        box.scrollTop = box.scrollHeight;
        return;
    }

    let cls = 'mot';
    if (sender === myUsername) cls = 'mme';
    else if (sender.includes('Groq')) cls = 'mgq';
    else if (sender.includes('Router')) cls = 'mrt';

    w.className = 'mw ' + cls;

    if (cls !== 'mme') {
        const n = document.createElement('div');
        n.className = 'mn';
        n.textContent = sender;
        w.appendChild(n);
    }

    const b = document.createElement('div');
    b.className = 'mb';

    if (image) {
        const i = document.createElement('img');
        i.src = image;
        i.style.cssText = 'max-width:100%;border-radius:6px;';
        b.appendChild(i);
        if (text) {
            const t2 = document.createElement('div');
            t2.style.cssText = 'font-size:.78em;opacity:.7;margin-top:4px;';
            t2.textContent = text;
            b.appendChild(t2);
        }
    } else {
        b.textContent = text;
    }

    w.appendChild(b);
    box.appendChild(w);
    box.scrollTop = box.scrollHeight;
}

function appendGameMsg(txt) {
    const box = document.getElementById('chat-msgs');
    const d = document.createElement('div');
    d.className = 'mgame';
    d.textContent = '🎮 ' + txt;
    box.appendChild(d);
    box.scrollTop = box.scrollHeight;
    appendLiveMsg('🎮', txt);
}

// ── Live chat overlay ──
function appendLiveMsg(sender, text) {
    const box = document.getElementById('live-chat-msgs');
    if (!box) return;

    const msg = document.createElement('div');
    msg.className = 'live-msg';

    const nameSpan = document.createElement('span');
    nameSpan.className = 'live-name';

    if (sender === myUsername) {
        nameSpan.classList.add('live-name-user');
        nameSpan.textContent = 'You:';
    } else if (sender.includes && sender.includes('Groq')) {
        nameSpan.classList.add('live-name-groq');
        nameSpan.textContent = 'Groq:';
    } else if (sender.includes && sender.includes('Router')) {
        nameSpan.classList.add('live-name-router');
        nameSpan.textContent = 'Router:';
    } else if (sender === 'System' || sender === '🎮') {
        nameSpan.classList.add('live-name-system');
        nameSpan.textContent = sender === '🎮' ? '🎮' : '📢';
    } else {
        nameSpan.classList.add('live-name-user');
        nameSpan.textContent = sender + ':';
    }

    msg.appendChild(nameSpan);
    msg.appendChild(document.createTextNode(' ' + (text || '')));
    box.appendChild(msg);

    while (box.children.length > 15) {
        box.removeChild(box.firstChild);
    }

    box.scrollTop = box.scrollHeight;
}

// ── Tab Switching ──
function switchTab(tab) {
    currentTab = tab;

    // Update tab button styles
    document.getElementById('tab-ludo').classList.toggle('active', tab === 'ludo');
    document.getElementById('tab-chess').classList.toggle('active', tab === 'chess');

    // Show the correct pane
    document.getElementById('pane-ludo').classList.toggle('vis', tab === 'ludo');
    document.getElementById('pane-chess').classList.toggle('vis', tab === 'chess');

    // Show game area
    const gameArea = document.getElementById('game-area');
    gameArea.classList.add('active');

    // On mobile, enter game fullscreen mode
    if (window.innerWidth < 800 && myUsername) {
        enterGameMode();
    }
}

// ── Game Fullscreen Mode (mobile) ──
function enterGameMode() {
    gameMode = true;
    const gameArea = document.getElementById('game-area');
    const chatPanel = document.querySelector('.chat-panel');
    gameArea.classList.add('active');
    if (chatPanel) chatPanel.style.display = 'none';
}

function exitGameMode() {
    gameMode = false;
    const gameArea = document.getElementById('game-area');
    const chatPanel = document.querySelector('.chat-panel');

    if (window.innerWidth < 800) {
        gameArea.classList.remove('active');
        if (chatPanel) chatPanel.style.display = 'flex';

        // Deselect tab
        currentTab = null;
        document.getElementById('tab-ludo').classList.remove('active');
        document.getElementById('tab-chess').classList.remove('active');
    }
}

// ── Handle resize ──
window.addEventListener('resize', () => {
    if (window.innerWidth >= 800) {
        const gameArea = document.getElementById('game-area');
        const chatPanel = document.querySelector('.chat-panel');
        if (currentTab) {
            gameArea.classList.add('active');
        }
        if (chatPanel) chatPanel.style.display = 'flex';
        gameMode = false;
    }
});
