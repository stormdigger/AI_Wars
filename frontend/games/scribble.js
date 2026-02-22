// ═══════════════════════════════════════════
// SCRIBBLE GAME – Full Turn-Based
// AI draws via animated stroke commands from backend
// AI guesses via vision API on compressed canvas snapshots
// ═══════════════════════════════════════════

const WORD_BANK = [
    'apple', 'banana', 'car', 'dog', 'elephant', 'fish', 'guitar', 'house',
    'jellyfish', 'kite', 'lion', 'moon', 'notebook', 'octopus', 'pizza', 'robot',
    'sun', 'tree', 'umbrella', 'violin', 'whale', 'yacht', 'zebra',
    'airplane', 'butterfly', 'castle', 'dinosaur', 'earth', 'flower', 'ghost', 'helicopter',
    'island', 'juice', 'kangaroo', 'laptop', 'mushroom', 'ninja', 'owl', 'penguin',
    'rainbow', 'spider', 'tornado', 'unicorn', 'volcano', 'waterfall', 'dragon', 'rocket',
    'camera', 'diamond', 'feather', 'grapes', 'hammer', 'igloo', 'jacket', 'key',
    'lamp', 'mountain', 'necklace', 'ocean', 'parachute', 'ring', 'skateboard', 'telescope',
    'vase', 'windmill', 'star', 'sword', 'crown', 'anchor', 'balloon',
    'candle', 'drum', 'envelope', 'flag', 'globe', 'heart', 'iceberg', 'jar',
    'kettle', 'leaf', 'magnet', 'nest', 'orange', 'pencil', 'rose',
    'snowflake', 'tent', 'ufo', 'wave', 'cat', 'bird', 'frog', 'snake',
    'turtle', 'rabbit', 'bear', 'bee', 'crab', 'deer', 'eagle', 'fox',
    'giraffe', 'horse', 'koala', 'monkey', 'panda', 'shark', 'tiger', 'wolf',
    'boat', 'bridge', 'bus', 'chair', 'clock', 'door', 'fan',
    'glasses', 'hat', 'iron', 'ladder', 'mirror', 'piano', 'scissors', 'table',
    'train', 'truck', 'window', 'basketball', 'football', 'baseball', 'tennis',
    'soccer', 'bowling', 'surfing', 'skiing', 'boxing', 'medal',
    'trophy', 'fire', 'lightning', 'cloud', 'rain', 'snow', 'sunrise', 'sunset',
    'beach', 'forest', 'desert', 'cave', 'river', 'lighthouse', 'barn',
    'sandwich', 'burger', 'taco', 'sushi', 'cake', 'cookie', 'donut', 'popcorn',
    'coffee', 'milk', 'bread', 'cheese', 'egg', 'salad', 'soup', 'pie',
    'bicycle', 'motorcycle', 'compass', 'pillow', 'backpack', 'shoe', 'watch', 'bell',
    'cherry', 'lemon', 'pear', 'strawberry', 'pineapple', 'corn', 'carrot', 'tomato'
];

const DRAWER_ORDER = ['user', 'groq', 'router'];

let scribble = {
    active: false,
    round: 0,
    maxRounds: 6,
    drawer: 'user',
    word: '',
    hintRevealed: [],
    timeLeft: 60,
    maxTime: 60,
    scores: { you: 0, groq: 0, router: 0 },
    guessedCorrectly: false,
    aiGuessedCorrectly: { groq: false, router: false },
    timerInterval: null,
    aiGuessInterval: null,
    aiClueInterval: null,
    strokeAnimInterval: null,
    clueIndex: 0,
    clues: [],
    strokes: [],
    hintTimers: [],
};

// Canvas state
let sCanvas, sCtx;
let sDrawing = false;
let sColor = '#000000';
let sSize = 4;
let sEraser = false;
let sUndoStack = [];

const SCOLORS = [
    '#000000', '#ffffff', '#ff0000', '#ff6600', '#ffcc00', '#33cc33',
    '#0066ff', '#9933ff', '#ff66cc', '#8B4513', '#808080', '#00cccc'
];

// ── Init canvas ──
function scribbleInitCanvas() {
    sCanvas = document.getElementById('scribble-canvas');
    if (!sCanvas) return;

    const wrap = sCanvas.parentElement;
    const w = Math.min(wrap.clientWidth || 520, 520);
    const h = Math.round(w * 0.73);
    sCanvas.width = w;
    sCanvas.height = h;
    sCanvas.style.width = w + 'px';
    sCanvas.style.height = h + 'px';

    sCtx = sCanvas.getContext('2d');
    sCtx.fillStyle = '#ffffff';
    sCtx.fillRect(0, 0, sCanvas.width, sCanvas.height);
    sCtx.lineCap = 'round';
    sCtx.lineJoin = 'round';

    sCanvas.addEventListener('mousedown', sStartDraw);
    sCanvas.addEventListener('mousemove', sDraw);
    sCanvas.addEventListener('mouseup', sEndDraw);
    sCanvas.addEventListener('mouseleave', sEndDraw);
    sCanvas.addEventListener('touchstart', sTouchStart, { passive: false });
    sCanvas.addEventListener('touchmove', sTouchMove, { passive: false });
    sCanvas.addEventListener('touchend', sEndDraw);
    sCanvas.addEventListener('touchcancel', sEndDraw);
}

function sGetPos(e) {
    const rect = sCanvas.getBoundingClientRect();
    const scaleX = sCanvas.width / rect.width;
    const scaleY = sCanvas.height / rect.height;
    return { x: (e.clientX - rect.left) * scaleX, y: (e.clientY - rect.top) * scaleY };
}

function sTouchPos(e) {
    const t = e.touches[0];
    const rect = sCanvas.getBoundingClientRect();
    const scaleX = sCanvas.width / rect.width;
    const scaleY = sCanvas.height / rect.height;
    return { x: (t.clientX - rect.left) * scaleX, y: (t.clientY - rect.top) * scaleY };
}

function sStartDraw(e) {
    if (!scribble.active || scribble.drawer !== 'user') return;
    sDrawing = true;
    const pos = sGetPos(e);
    sCtx.beginPath();
    sCtx.moveTo(pos.x, pos.y);
    sUndoStack.push(sCtx.getImageData(0, 0, sCanvas.width, sCanvas.height));
    if (sUndoStack.length > 20) sUndoStack.shift();
}

function sDraw(e) {
    if (!sDrawing || !scribble.active || scribble.drawer !== 'user') return;
    const pos = sGetPos(e);
    sCtx.strokeStyle = sEraser ? '#ffffff' : sColor;
    sCtx.lineWidth = sEraser ? sSize * 3 : sSize;
    sCtx.lineTo(pos.x, pos.y);
    sCtx.stroke();
}

function sEndDraw() { sDrawing = false; }

function sTouchStart(e) {
    e.preventDefault();
    if (!scribble.active || scribble.drawer !== 'user') return;
    sDrawing = true;
    const pos = sTouchPos(e);
    sCtx.beginPath();
    sCtx.moveTo(pos.x, pos.y);
    sUndoStack.push(sCtx.getImageData(0, 0, sCanvas.width, sCanvas.height));
    if (sUndoStack.length > 20) sUndoStack.shift();
}

function sTouchMove(e) {
    e.preventDefault();
    if (!sDrawing || !scribble.active || scribble.drawer !== 'user') return;
    const pos = sTouchPos(e);
    sCtx.strokeStyle = sEraser ? '#ffffff' : sColor;
    sCtx.lineWidth = sEraser ? sSize * 3 : sSize;
    sCtx.lineTo(pos.x, pos.y);
    sCtx.stroke();
}

// ── Drawing tools ──
function sSetColor(color) {
    sColor = color; sEraser = false;
    document.querySelectorAll('.scolor-btn').forEach(b => b.classList.toggle('active', b.dataset.color === color));
}

function sSetSize(size) {
    sSize = size;
    document.querySelectorAll('.ssize-btn').forEach(b => b.classList.toggle('active', parseInt(b.dataset.size) === size));
}

function sSetEraser() {
    sEraser = true;
    document.querySelectorAll('.scolor-btn').forEach(b => b.classList.remove('active'));
}

function sUndo() { if (sUndoStack.length > 0) sCtx.putImageData(sUndoStack.pop(), 0, 0); }

function sClearCanvas() {
    if (!sCtx) return;
    sUndoStack.push(sCtx.getImageData(0, 0, sCanvas.width, sCanvas.height));
    sCtx.fillStyle = '#ffffff';
    sCtx.fillRect(0, 0, sCanvas.width, sCanvas.height);
}

// ── Build toolbar ──
function scribbleBuildToolbar() {
    const tb = document.getElementById('scribble-toolbar');
    if (!tb) return;
    tb.innerHTML = '';

    SCOLORS.forEach(c => {
        const btn = document.createElement('div');
        btn.className = 'scolor-btn' + (c === '#000000' ? ' active' : '');
        btn.style.background = c;
        if (c === '#ffffff') btn.style.border = '2px solid #aaa';
        btn.dataset.color = c;
        btn.onclick = () => sSetColor(c);
        tb.appendChild(btn);
    });

    tb.appendChild(Object.assign(document.createElement('div'), { className: 'stool-sep' }));

    [2, 4, 8].forEach((s, i) => {
        const btn = document.createElement('div');
        btn.className = 'ssize-btn' + (s === 4 ? ' active' : '');
        btn.dataset.size = s;
        const dot = document.createElement('div');
        dot.className = 'ssize-dot';
        dot.style.width = [4, 7, 12][i] + 'px';
        dot.style.height = [4, 7, 12][i] + 'px';
        btn.appendChild(dot);
        btn.onclick = () => sSetSize(s);
        tb.appendChild(btn);
    });

    tb.appendChild(Object.assign(document.createElement('div'), { className: 'stool-sep' }));

    [['🧽 Eraser', sSetEraser], ['↩ Undo', sUndo], ['🗑 Clear', sClearCanvas]].forEach(([txt, fn]) => {
        const btn = document.createElement('button');
        btn.className = 'stool-btn';
        btn.textContent = txt;
        btn.onclick = fn;
        tb.appendChild(btn);
    });
}

// ═══════════════════════════════
// AI STROKE DRAWING ENGINE
// ═══════════════════════════════

function sScaleX(x) {
    // Scale x from backend's 520 to actual canvas width
    return (x / 520) * sCanvas.width;
}

function sScaleY(y) {
    // Scale y from backend's 380 to actual canvas height
    return (y / 380) * sCanvas.height;
}

/**
 * Execute a single drawing command from the AI stroke array.
 * Supports: line (l), circle (c), rect (r), arc (a), bezier (b), polyline (p)
 */
function sExecuteStroke(cmd) {
    if (!sCtx || !cmd || !cmd.t) return;

    sCtx.save();
    sCtx.strokeStyle = cmd.c || '#333333';
    sCtx.lineWidth = cmd.w || 3;
    sCtx.lineCap = 'round';
    sCtx.lineJoin = 'round';

    try {
        switch (cmd.t) {
            case 'l': { // Line
                sCtx.beginPath();
                sCtx.moveTo(sScaleX(cmd.x1), sScaleY(cmd.y1));
                sCtx.lineTo(sScaleX(cmd.x2), sScaleY(cmd.y2));
                sCtx.stroke();
                break;
            }
            case 'c': { // Circle
                sCtx.beginPath();
                const rx = sScaleX(cmd.x);
                const ry = sScaleY(cmd.y);
                // Scale radius by average of x/y scale factors
                const rr = cmd.r * (sCanvas.width / 520 + sCanvas.height / 380) / 2;
                sCtx.arc(rx, ry, Math.max(1, rr), 0, Math.PI * 2);
                if (cmd.fill) { sCtx.fillStyle = cmd.fill; sCtx.fill(); }
                sCtx.stroke();
                break;
            }
            case 'r': { // Rectangle
                const rx = sScaleX(cmd.x), ry = sScaleY(cmd.y);
                const rw = sScaleX(cmd.x + cmd.w) - rx;
                const rh = sScaleY(cmd.y + cmd.h) - ry;
                if (cmd.fill) { sCtx.fillStyle = cmd.fill; sCtx.fillRect(rx, ry, rw, rh); }
                sCtx.strokeRect(rx, ry, rw, rh);
                break;
            }
            case 'a': { // Arc
                sCtx.beginPath();
                const ax = sScaleX(cmd.x), ay = sScaleY(cmd.y);
                const ar = cmd.r * (sCanvas.width / 520 + sCanvas.height / 380) / 2;
                sCtx.arc(ax, ay, Math.max(1, ar), cmd.s || 0, cmd.e || Math.PI * 2);
                sCtx.stroke();
                break;
            }
            case 'b': { // Bezier curve
                sCtx.beginPath();
                sCtx.moveTo(sScaleX(cmd.x1), sScaleY(cmd.y1));
                sCtx.bezierCurveTo(
                    sScaleX(cmd.cx1), sScaleY(cmd.cy1),
                    sScaleX(cmd.cx2), sScaleY(cmd.cy2),
                    sScaleX(cmd.x2), sScaleY(cmd.y2)
                );
                sCtx.stroke();
                break;
            }
            case 'p': { // Polyline (array of points)
                if (!cmd.pts || cmd.pts.length < 2) break;
                sCtx.beginPath();
                sCtx.moveTo(sScaleX(cmd.pts[0][0]), sScaleY(cmd.pts[0][1]));
                for (let i = 1; i < cmd.pts.length; i++) {
                    sCtx.lineTo(sScaleX(cmd.pts[i][0]), sScaleY(cmd.pts[i][1]));
                }
                if (cmd.fill) { sCtx.fillStyle = cmd.fill; sCtx.fill(); }
                sCtx.stroke();
                break;
            }
        }
    } catch (e) {
        console.warn('Stroke exec error:', e, cmd);
    }

    sCtx.restore();
}

/**
 * Animate AI drawing strokes one by one with a delay.
 * Each stroke is drawn with a 280ms interval to look "human-like".
 */
function sPlayAiStrokes(strokes) {
    if (!strokes || strokes.length === 0) return;

    // Clear canvas first
    sCtx.fillStyle = '#ffffff';
    sCtx.fillRect(0, 0, sCanvas.width, sCanvas.height);

    let i = 0;
    const delay = Math.max(200, Math.min(500, 8000 / strokes.length)); // auto-pace

    clearInterval(scribble.strokeAnimInterval);
    scribble.strokeAnimInterval = setInterval(() => {
        if (i >= strokes.length || !scribble.active) {
            clearInterval(scribble.strokeAnimInterval);
            return;
        }
        sExecuteStroke(strokes[i]);
        i++;
    }, delay);
}

// ── Canvas snapshot compression ──
/**
 * Get a compressed version of the canvas (max 300px wide, JPEG 0.65 quality)
 * This is critical — full canvas base64 is too large for WebSocket.
 */
function sGetCompressedSnapshot() {
    if (!sCanvas) return null;

    try {
        const maxW = 300;
        const scale = Math.min(1, maxW / sCanvas.width);
        const tmpW = Math.round(sCanvas.width * scale);
        const tmpH = Math.round(sCanvas.height * scale);

        const tmp = document.createElement('canvas');
        tmp.width = tmpW;
        tmp.height = tmpH;
        const tmpCtx = tmp.getContext('2d');
        tmpCtx.drawImage(sCanvas, 0, 0, tmpW, tmpH);

        return tmp.toDataURL('image/jpeg', 0.65);
    } catch (e) {
        return null;
    }
}

// ══════════════════════════════
// GAME FLOW
// ══════════════════════════════

function startScribbleGame() {
    if (!myUsername) { alert('Join a room first!'); return; }

    document.getElementById('scribble-start-screen').style.display = 'none';
    document.getElementById('scribble-game-area').style.display = 'block';

    scribble.round = 0;
    scribble.scores = { you: 0, groq: 0, router: 0 };

    scribbleInitCanvas();
    scribbleBuildToolbar();
    sUpdateScores();
    sUpdateDrawerDisplay();

    wsSend({
        sender: myUsername,
        message: `__SCRIBBLE__:${JSON.stringify({ event: 'game_start' })}`,
        image: null
    });

    appendGameMsg('🎨 Scribble game started! Get ready to draw & guess!');
    sNextRound();
}

function sNextRound() {
    scribble.round++;
    if (scribble.round > scribble.maxRounds) { sGameOver(); return; }

    sClearAllTimers();

    scribble.active = false;
    scribble.guessedCorrectly = false;
    scribble.aiGuessedCorrectly = { groq: false, router: false };
    scribble.clueIndex = 0;
    scribble.clues = [];
    scribble.strokes = [];
    sUndoStack = [];

    scribble.drawer = DRAWER_ORDER[(scribble.round - 1) % 3];

    const roundEl = document.getElementById('scribble-round');
    if (roundEl) roundEl.textContent = `Round ${scribble.round}/${scribble.maxRounds}`;

    sUpdateDrawerDisplay();
    sClearCanvasForNewRound();

    if (scribble.drawer === 'user') {
        sShowToolbar(true);
        sShowGuessInput(false);
        sShowClueArea(false);
        sShowWordPick();
    } else {
        sShowToolbar(false);
        sShowGuessInput(true);
        sShowClueArea(true);
        sStartAiDrawTurn();
    }
}

function sClearAllTimers() {
    clearInterval(scribble.timerInterval);
    clearInterval(scribble.aiGuessInterval);
    clearInterval(scribble.aiClueInterval);
    clearInterval(scribble.strokeAnimInterval);
    scribble.hintTimers.forEach(t => clearTimeout(t));
    scribble.hintTimers = [];
}

function sClearCanvasForNewRound() {
    if (sCtx) {
        sCtx.fillStyle = '#ffffff';
        sCtx.fillRect(0, 0, sCanvas.width, sCanvas.height);
    }
}

// ── UI toggles ──
function sShowToolbar(show) {
    const tb = document.getElementById('scribble-toolbar');
    if (tb) tb.style.display = show ? 'flex' : 'none';
}

function sShowGuessInput(show) {
    const gi = document.getElementById('scribble-guess-row');
    if (gi) gi.style.display = show ? 'flex' : 'none';
}

function sShowClueArea(show) {
    const ca = document.getElementById('scribble-clue-area');
    if (ca) ca.style.display = show ? 'block' : 'none';
}

function sUpdateDrawerDisplay() {
    const el = document.getElementById('scribble-drawer-info');
    if (!el) return;
    const names = { user: '👤 You', groq: '🟢 Groq-AI', router: '🔵 Router-AI' };
    const d = scribble.drawer;
    if (d === 'user') {
        el.textContent = '✏️ Your turn to draw!';
        el.style.color = 'var(--accent)';
    } else {
        el.textContent = `🎨 ${names[d]} is drawing...`;
        el.style.color = d === 'groq' ? '#99ffcc' : '#aaccff';
    }
}

// ══════════════════════════════
// USER DRAW TURN
// ══════════════════════════════

function sShowWordPick() {
    const pick = document.getElementById('scribble-word-pick');
    const opts = document.getElementById('word-options');
    if (!pick || !opts) return;

    const words = [];
    const used = new Set();
    while (words.length < 3) {
        const w = WORD_BANK[Math.floor(Math.random() * WORD_BANK.length)];
        if (!used.has(w)) { words.push(w); used.add(w); }
    }

    opts.innerHTML = '';
    words.forEach(w => {
        const btn = document.createElement('button');
        btn.className = 'word-opt';
        btn.textContent = w.toUpperCase();
        btn.onclick = () => sUserPickWord(w);
        opts.appendChild(btn);
    });

    pick.classList.add('show');
}

function sUserPickWord(word) {
    scribble.word = word;
    scribble.active = true;
    scribble.timeLeft = scribble.maxTime;

    scribble.hintRevealed = word.split('').map(ch => ch === ' ' ? ' ' : '_');
    sUpdateHint();

    document.getElementById('scribble-word-pick').classList.remove('show');
    const resultEl = document.getElementById('scribble-result');
    if (resultEl) resultEl.classList.remove('show');

    const wordDisplay = document.getElementById('scribble-your-word');
    if (wordDisplay) {
        wordDisplay.textContent = `Your word: ${word.toUpperCase()}`;
        wordDisplay.style.display = 'block';
    }

    appendGameMsg(`Round ${scribble.round}: ${myUsername} is drawing! (${word.length} letters)`);

    wsSend({
        sender: myUsername,
        message: `__SCRIBBLE__:${JSON.stringify({
            event: 'user_draw_start',
            round: scribble.round,
            wordLength: word.length,
            word: word,
            hint: scribble.hintRevealed.join('')
        })}`,
        image: null
    });

    sStartTimer();

    // Send compressed canvas snapshots every 6 seconds for AI to guess
    clearInterval(scribble.aiGuessInterval);
    scribble.aiGuessInterval = setInterval(sAiGuess, 6000);

    sScheduleHints();
}

// ══════════════════════════════
// AI DRAW TURN
// ══════════════════════════════

function sStartAiDrawTurn() {
    const drawerName = scribble.drawer === 'groq' ? 'Groq-AI' : 'Router-AI';

    // Show "waiting" placeholder on canvas
    sDrawAiThinking(drawerName);

    appendGameMsg(`Round ${scribble.round}: ${drawerName} is drawing! Guess what it is!`);

    // Ask backend to generate strokes
    wsSend({
        sender: myUsername,
        message: `__SCRIBBLE__:${JSON.stringify({
            event: 'ai_draw_request',
            drawer: scribble.drawer,
            round: scribble.round
        })}`,
        image: null
    });
}

function sDrawAiThinking(name) {
    if (!sCtx) return;
    sCtx.fillStyle = '#ffffff';
    sCtx.fillRect(0, 0, sCanvas.width, sCanvas.height);

    // Pulsing dots animation (static version)
    sCtx.fillStyle = '#e0e0f0';
    sCtx.font = `bold ${Math.round(sCanvas.width * 0.035)}px Inter, sans-serif`;
    sCtx.textAlign = 'center';
    sCtx.textBaseline = 'middle';
    sCtx.fillText(`🎨 ${name}`, sCanvas.width / 2, sCanvas.height / 2 - 24);

    sCtx.font = `${Math.round(sCanvas.width * 0.022)}px Inter, sans-serif`;
    sCtx.fillStyle = '#888';
    sCtx.fillText('thinking & drawing...', sCanvas.width / 2, sCanvas.height / 2 + 14);
}

// ══════════════════════════════
// USER GUESSING (when AI draws)
// ══════════════════════════════

function sSubmitGuess() {
    const inp = document.getElementById('scribble-guess-inp');
    if (!inp) return;
    const guess = inp.value.trim();
    if (!guess || !scribble.active || scribble.drawer === 'user') return;

    inp.value = '';

    const drawerName = scribble.drawer === 'groq' ? 'Groq-AI' : 'Router-AI';
    appendMsg(myUsername, `🤔 "${guess}"?`);
    appendLiveMsg(myUsername, `🤔 "${guess}"?`);

    wsSend({
        sender: myUsername,
        message: `__SCRIBBLE__:${JSON.stringify({
            event: 'user_guess',
            guess: guess,
            round: scribble.round
        })}`,
        image: null
    });

    if (scribble.word && guess.toLowerCase().trim() === scribble.word.toLowerCase()) {
        if (!scribble.guessedCorrectly) {
            scribble.guessedCorrectly = true;
            const timeBonus = Math.ceil(scribble.timeLeft * 1.5);
            scribble.scores.you += timeBonus;

            const drawerKey = scribble.drawer === 'groq' ? 'groq' : 'router';
            scribble.scores[drawerKey] += Math.ceil(timeBonus * 0.5);

            appendMsg('System', `✅ You guessed it! "${scribble.word}" (+${timeBonus} pts)`);
            appendLiveMsg('System', `✅ Correct! "${scribble.word}"`);
            sUpdateScores();
            sEndRound(true);
        }
    } else {
        appendMsg('System', `❌ Not quite...`);
    }
}

// ══════════════════════════════
// AI GUESSING (when user draws) — sends COMPRESSED canvas
// ══════════════════════════════

function sAiGuess() {
    if (!scribble.active || scribble.drawer !== 'user' || !sCanvas) return;

    // Compress canvas before sending — critical for WebSocket size limits
    const compressedImage = sGetCompressedSnapshot();
    if (!compressedImage) return;

    wsSend({
        sender: myUsername,
        message: `__SCRIBBLE__:${JSON.stringify({
            event: 'canvas_snapshot',
            image: compressedImage,       // compressed JPEG, ~10x smaller
            hint: scribble.hintRevealed.join(''),
            wordLength: scribble.word.length,
            round: scribble.round
        })}`,
        image: null
    });
}

// ══════════════════════════════
// HANDLE SCRIBBLE MESSAGES FROM BACKEND
// ══════════════════════════════

function handleScribbleMsg(data) {
    try {
        const raw = data.message.replace('__SCRIBBLE__:', '');
        const d = JSON.parse(raw);

        // ── AI guess when user draws ──
        if (d.event === 'ai_guess') {
            const guesser = d.guesser;
            const guess = d.guess;

            if (!guess) return;

            appendMsg(guesser, `🤔 "${guess}"?`);
            appendLiveMsg(guesser, `🤔 "${guess}"?`);

            if (scribble.active && scribble.drawer === 'user' && scribble.word &&
                guess.toLowerCase().trim() === scribble.word.toLowerCase()) {
                const key = guesser.includes('Groq') ? 'groq' : 'router';
                if (!scribble.aiGuessedCorrectly[key]) {
                    scribble.aiGuessedCorrectly[key] = true;

                    const timeBonus = Math.ceil(scribble.timeLeft * 1.5);
                    scribble.scores[key] += timeBonus;
                    scribble.scores.you += Math.ceil(timeBonus * 0.5); // drawer bonus

                    appendMsg('System', `✅ ${guesser} guessed it! "${scribble.word}" (+${timeBonus} pts)`);
                    appendLiveMsg('System', `✅ ${guesser} guessed correctly!`);
                    sUpdateScores();

                    if (scribble.aiGuessedCorrectly.groq && scribble.aiGuessedCorrectly.router) {
                        sEndRound(true);
                    }
                }
            }
        }

        // ── AI draw response — backend sends word + strokes + clues ──
        if (d.event === 'ai_draw_start') {
            scribble.word = d.word;
            scribble.clues = d.clues || [];
            scribble.strokes = d.strokes || [];
            scribble.clueIndex = 0;
            scribble.active = true;
            scribble.timeLeft = scribble.maxTime;

            // Setup hint
            scribble.hintRevealed = d.word.split('').map(ch => ch === ' ' ? ' ' : '_');
            sUpdateHint();

            const resultEl = document.getElementById('scribble-result');
            if (resultEl) resultEl.classList.remove('show');

            const drawerName = d.drawer === 'groq' ? 'Groq-AI' : 'Router-AI';
            appendGameMsg(`${drawerName} is now drawing! Type your guess below!`);

            // Start timer
            sStartTimer();
            sScheduleHints();

            // ✨ Animate AI strokes on canvas
            if (scribble.strokes.length > 0) {
                sPlayAiStrokes(scribble.strokes);
            }

            // Show progressive text clues in chat as backup
            clearInterval(scribble.aiClueInterval);
            // First clue after 10s, then every 12s
            setTimeout(() => {
                sShowNextClue();
                scribble.aiClueInterval = setInterval(sShowNextClue, 12000);
            }, 10000);
        }

        // ── Round result ──
        if (d.event === 'round_guessed' || d.event === 'round_timeout') {
            // Already handled locally
        }

    } catch (e) {
        console.warn('handleScribbleMsg error:', e);
    }
}

function sShowNextClue() {
    if (!scribble.active || scribble.drawer === 'user') return;
    if (scribble.clueIndex >= scribble.clues.length) return;

    const clue = scribble.clues[scribble.clueIndex];
    const drawerName = scribble.drawer === 'groq' ? 'Groq-AI' : 'Router-AI';
    appendMsg(drawerName, `💡 Hint: ${clue}`);
    appendLiveMsg(drawerName, `💡 ${clue}`);

    scribble.clueIndex++;
}

// ══════════════════════════════
// TIMER & HINTS
// ══════════════════════════════

function sStartTimer() {
    clearInterval(scribble.timerInterval);
    sUpdateTimerUI();
    scribble.timerInterval = setInterval(() => {
        scribble.timeLeft--;
        sUpdateTimerUI();
        if (scribble.timeLeft <= 0) sEndRound(false);
    }, 1000);
}

function sUpdateTimerUI() {
    const fill = document.getElementById('timer-fill');
    const txt = document.getElementById('timer-txt');
    if (fill) {
        fill.style.width = (scribble.timeLeft / scribble.maxTime * 100) + '%';
        fill.classList.toggle('low', scribble.timeLeft <= 15);
    }
    if (txt) txt.textContent = scribble.timeLeft + 's';
}

function sUpdateHint() {
    const el = document.getElementById('scribble-hint');
    if (el) el.textContent = scribble.hintRevealed.join(' ');
}

function sScheduleHints() {
    scribble.hintTimers.forEach(t => clearTimeout(t));
    scribble.hintTimers = [];
    [20, 35, 50].forEach(t => {
        const timer = setTimeout(() => {
            if (!scribble.active) return;
            sRevealLetter();
        }, t * 1000);
        scribble.hintTimers.push(timer);
    });
}

function sRevealLetter() {
    const hidden = [];
    scribble.hintRevealed.forEach((ch, i) => { if (ch === '_') hidden.push(i); });
    if (hidden.length <= 1) return;
    const idx = hidden[Math.floor(Math.random() * hidden.length)];
    scribble.hintRevealed[idx] = scribble.word[idx];
    sUpdateHint();
}

function sUpdateScores() {
    const els = {
        you: document.getElementById('ss-you-pts'),
        groq: document.getElementById('ss-groq-pts'),
        router: document.getElementById('ss-router-pts')
    };
    if (els.you) els.you.textContent = scribble.scores.you;
    if (els.groq) els.groq.textContent = scribble.scores.groq;
    if (els.router) els.router.textContent = scribble.scores.router;
}

// ══════════════════════════════
// ROUND END & GAME OVER
// ══════════════════════════════

function sEndRound(guessed) {
    scribble.active = false;
    sClearAllTimers();

    const wordDisplay = document.getElementById('scribble-your-word');
    if (wordDisplay) wordDisplay.style.display = 'none';

    scribble.hintRevealed = scribble.word.split('');
    sUpdateHint();

    const resultEl = document.getElementById('scribble-result');
    const resultH3 = document.getElementById('scribble-result-title');
    const resultP = document.getElementById('scribble-result-text');

    if (guessed) {
        if (resultH3) resultH3.textContent = '🎉 Word Guessed!';
        if (resultP) resultP.textContent = `The word was "${scribble.word}"`;
    } else {
        if (resultH3) resultH3.textContent = "⏰ Time's Up!";
        if (resultP) resultP.textContent = `The word was "${scribble.word}"`;
    }

    if (resultEl) resultEl.classList.add('show');

    wsSend({
        sender: myUsername,
        message: `__SCRIBBLE__:${JSON.stringify({
            event: guessed ? 'round_guessed' : 'round_timeout',
            word: scribble.word, round: scribble.round
        })}`,
        image: null
    });

    appendGameMsg(guessed
        ? `Round ${scribble.round} complete! Word: "${scribble.word}"`
        : `Time's up! Word was "${scribble.word}"`
    );

    sUpdateScores();
    setTimeout(sNextRound, 3500);
}

function sGameOver() {
    scribble.active = false;
    sClearAllTimers();

    const { you, groq, router } = scribble.scores;
    let winner = 'You', maxScore = you;
    if (groq > maxScore) { winner = 'Groq-AI'; maxScore = groq; }
    if (router > maxScore) { winner = 'Router-AI'; maxScore = router; }

    const resultEl = document.getElementById('scribble-result');
    const resultH3 = document.getElementById('scribble-result-title');
    const resultP = document.getElementById('scribble-result-text');

    if (resultH3) resultH3.textContent = '🏆 Game Over!';
    if (resultP) resultP.innerHTML = `Winner: <strong>${winner}</strong> with ${maxScore} pts!`;
    if (resultEl) resultEl.classList.add('show');

    appendGameMsg(`🏆 Scribble game over! ${winner} wins with ${maxScore} pts!`);

    wsSend({
        sender: myUsername,
        message: `__SCRIBBLE__:${JSON.stringify({ event: 'game_over', winner, scores: scribble.scores })}`,
        image: null
    });
}

function scribbleReset() {
    scribble.active = false;
    sClearAllTimers();

    document.getElementById('scribble-start-screen').style.display = 'flex';
    document.getElementById('scribble-game-area').style.display = 'none';

    const resultEl = document.getElementById('scribble-result');
    if (resultEl) resultEl.classList.remove('show');
    const pickEl = document.getElementById('scribble-word-pick');
    if (pickEl) pickEl.classList.remove('show');
}