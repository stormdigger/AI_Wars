// ═══════════════════════════════════════════
// SCRIBBLE GAME – Fixed version
// Fixes: round flow, word-length scoring, user guess points,
//        AI clue spoilers, AI drawing quality
// ═══════════════════════════════════════════

const WORD_BANK = [
    'apple','banana','car','dog','elephant','fish','guitar','house','jellyfish','kite',
    'lion','moon','octopus','pizza','robot','sun','tree','umbrella','violin','whale','zebra',
    'airplane','butterfly','castle','dinosaur','flower','ghost','helicopter','kangaroo','laptop',
    'mushroom','ninja','owl','penguin','rainbow','spider','tornado','unicorn','volcano','dragon',
    'rocket','camera','diamond','grapes','hammer','igloo','key','lamp','mountain','parachute',
    'skateboard','telescope','star','sword','crown','anchor','balloon','candle','drum','flag',
    'globe','heart','leaf','magnet','pencil','rose','snowflake','tent','cat','bird','frog',
    'snake','turtle','rabbit','bear','bee','eagle','fox','giraffe','horse','koala','monkey',
    'panda','shark','tiger','wolf','boat','bridge','bus','chair','clock','door','glasses',
    'hat','ladder','mirror','piano','scissors','table','train','truck','basketball','football',
    'trophy','fire','lightning','cloud','rain','snow','beach','forest','cave','river',
    'lighthouse','burger','taco','sushi','cake','cookie','donut','popcorn','bicycle','compass',
    'backpack','shoe','watch','bell','cherry','lemon','strawberry','pineapple','carrot','tomato',
    'crab','deer','fan','window','medal','sandwich','coffee','bread','notebook','vase','drum',
    'feather','envelope','jar','kettle','nest','orange','ring','windmill','wave','ufo',
    'corn','pear','cheese','egg','soup','pie','barn','desert','sunrise','sunset',
];

const DRAWER_ORDER = ['user', 'groq', 'router'];

let scribble = {
    active: false,
    endingRound: false,       // ← guard against double sEndRound
    pendingNextRound: null,   // ← store timeout id so we can cancel
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

let sCanvas, sCtx;
let sDrawing = false;
let sColor = '#000000';
let sSize = 4;
let sEraser = false;
let sUndoStack = [];

const SCOLORS = [
    '#000000','#ffffff','#E53935','#FF6D00','#FDD835','#43A047',
    '#1E88E5','#8E24AA','#ff66cc','#6D4C41','#808080','#00cccc'
];

// ── Scoring: word length × 15 base + time bonus ──
function sCalcPoints(wordLength, timeLeft, maxTime) {
    const base = wordLength * 15;
    const timeBonus = Math.round((timeLeft / maxTime) * wordLength * 25);
    return base + timeBonus;
}

// ══════════════════════════════
// CANVAS SETUP
// ══════════════════════════════

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
    sCtx.fillRect(0, 0, w, h);
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
    return {
        x: (e.clientX - rect.left) * (sCanvas.width / rect.width),
        y: (e.clientY - rect.top) * (sCanvas.height / rect.height)
    };
}
function sTouchPos(e) {
    const t = e.touches[0], rect = sCanvas.getBoundingClientRect();
    return {
        x: (t.clientX - rect.left) * (sCanvas.width / rect.width),
        y: (t.clientY - rect.top) * (sCanvas.height / rect.height)
    };
}
function sStartDraw(e) {
    if (!scribble.active || scribble.drawer !== 'user') return;
    sDrawing = true;
    const pos = sGetPos(e);
    sCtx.beginPath(); sCtx.moveTo(pos.x, pos.y);
    sUndoStack.push(sCtx.getImageData(0, 0, sCanvas.width, sCanvas.height));
    if (sUndoStack.length > 20) sUndoStack.shift();
}
function sDraw(e) {
    if (!sDrawing || !scribble.active || scribble.drawer !== 'user') return;
    const pos = sGetPos(e);
    sCtx.strokeStyle = sEraser ? '#ffffff' : sColor;
    sCtx.lineWidth = sEraser ? sSize * 3 : sSize;
    sCtx.lineTo(pos.x, pos.y); sCtx.stroke();
}
function sEndDraw() { sDrawing = false; }
function sTouchStart(e) {
    e.preventDefault();
    if (!scribble.active || scribble.drawer !== 'user') return;
    sDrawing = true;
    const pos = sTouchPos(e);
    sCtx.beginPath(); sCtx.moveTo(pos.x, pos.y);
    sUndoStack.push(sCtx.getImageData(0, 0, sCanvas.width, sCanvas.height));
    if (sUndoStack.length > 20) sUndoStack.shift();
}
function sTouchMove(e) {
    e.preventDefault();
    if (!sDrawing || !scribble.active || scribble.drawer !== 'user') return;
    const pos = sTouchPos(e);
    sCtx.strokeStyle = sEraser ? '#ffffff' : sColor;
    sCtx.lineWidth = sEraser ? sSize * 3 : sSize;
    sCtx.lineTo(pos.x, pos.y); sCtx.stroke();
}
function sSetColor(c) {
    sColor = c; sEraser = false;
    document.querySelectorAll('.scolor-btn').forEach(b => b.classList.toggle('active', b.dataset.color === c));
}
function sSetSize(s) {
    sSize = s;
    document.querySelectorAll('.ssize-btn').forEach(b => b.classList.toggle('active', parseInt(b.dataset.size) === s));
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
        dot.style.cssText = `width:${[4,7,12][i]}px;height:${[4,7,12][i]}px`;
        btn.appendChild(dot); btn.onclick = () => sSetSize(s);
        tb.appendChild(btn);
    });
    tb.appendChild(Object.assign(document.createElement('div'), { className: 'stool-sep' }));
    [['🧽 Eraser', sSetEraser],['↩ Undo', sUndo],['🗑 Clear', sClearCanvas]].forEach(([txt, fn]) => {
        const btn = document.createElement('button');
        btn.className = 'stool-btn'; btn.textContent = txt; btn.onclick = fn;
        tb.appendChild(btn);
    });
}

// ══════════════════════════════
// AI STROKE ENGINE
// ══════════════════════════════

function sScaleX(x) { return (x / 520) * sCanvas.width; }
function sScaleY(y) { return (y / 380) * sCanvas.height; }
function sScaleR(r) { return r * (sCanvas.width / 520 + sCanvas.height / 380) / 2; }

function sExecuteStroke(cmd) {
    if (!sCtx || !cmd || !cmd.t) return;
    sCtx.save();
    sCtx.strokeStyle = cmd.c || '#333333';
    sCtx.lineWidth = cmd.w || 3;
    sCtx.lineCap = 'round';
    sCtx.lineJoin = 'round';
    try {
        switch (cmd.t) {
            case 'l':
                sCtx.beginPath();
                sCtx.moveTo(sScaleX(cmd.x1), sScaleY(cmd.y1));
                sCtx.lineTo(sScaleX(cmd.x2), sScaleY(cmd.y2));
                sCtx.stroke(); break;
            case 'c':
                sCtx.beginPath();
                sCtx.arc(sScaleX(cmd.x), sScaleY(cmd.y), Math.max(1, sScaleR(cmd.r)), 0, Math.PI * 2);
                if (cmd.fill) { sCtx.fillStyle = cmd.fill; sCtx.fill(); }
                sCtx.stroke(); break;
            case 'r': {
                const rx = sScaleX(cmd.x), ry = sScaleY(cmd.y);
                const rw = sScaleX(cmd.x + cmd.w) - rx;
                const rh = sScaleY(cmd.y + cmd.h) - ry;
                if (cmd.fill) { sCtx.fillStyle = cmd.fill; sCtx.fillRect(rx, ry, rw, rh); }
                sCtx.strokeRect(rx, ry, rw, rh); break;
            }
            case 'a':
                sCtx.beginPath();
                sCtx.arc(sScaleX(cmd.x), sScaleY(cmd.y), Math.max(1, sScaleR(cmd.r)), cmd.s || 0, cmd.e || Math.PI * 2);
                if (cmd.fill) { sCtx.fillStyle = cmd.fill; sCtx.fill(); }
                sCtx.stroke(); break;
            case 'b':
                sCtx.beginPath();
                sCtx.moveTo(sScaleX(cmd.x1), sScaleY(cmd.y1));
                sCtx.bezierCurveTo(
                    sScaleX(cmd.cx1), sScaleY(cmd.cy1),
                    sScaleX(cmd.cx2), sScaleY(cmd.cy2),
                    sScaleX(cmd.x2), sScaleY(cmd.y2)
                );
                if (cmd.fill) { sCtx.fillStyle = cmd.fill; sCtx.fill(); }
                sCtx.stroke(); break;
            case 'p':
                if (!cmd.pts || cmd.pts.length < 2) break;
                sCtx.beginPath();
                sCtx.moveTo(sScaleX(cmd.pts[0][0]), sScaleY(cmd.pts[0][1]));
                for (let i = 1; i < cmd.pts.length; i++)
                    sCtx.lineTo(sScaleX(cmd.pts[i][0]), sScaleY(cmd.pts[i][1]));
                if (cmd.close) sCtx.closePath();
                if (cmd.fill) { sCtx.fillStyle = cmd.fill; sCtx.fill(); }
                sCtx.stroke(); break;
        }
    } catch(e) { console.warn('Stroke error:', e, cmd); }
    sCtx.restore();
}

function sPlayAiStrokes(strokes) {
    if (!strokes || strokes.length === 0) return;
    sCtx.fillStyle = '#ffffff';
    sCtx.fillRect(0, 0, sCanvas.width, sCanvas.height);
    let i = 0;
    const isQuickDraw = strokes.length > 50;
    const delay = isQuickDraw
        ? Math.max(40, Math.min(120, 6000 / strokes.length))  // QD: fast fluid animation
        : Math.max(200, Math.min(500, 7000 / strokes.length)); // LLM: deliberate strokes
    clearInterval(scribble.strokeAnimInterval);
    scribble.strokeAnimInterval = setInterval(() => {
        if (i >= strokes.length || !scribble.active) {
            clearInterval(scribble.strokeAnimInterval); return;
        }
        sExecuteStroke(strokes[i++]);
    }, delay);
}

function sGetCompressedSnapshot() {
    if (!sCanvas) return null;
    try {
        const maxW = 300, scale = Math.min(1, maxW / sCanvas.width);
        const tmp = document.createElement('canvas');
        tmp.width = Math.round(sCanvas.width * scale);
        tmp.height = Math.round(sCanvas.height * scale);
        tmp.getContext('2d').drawImage(sCanvas, 0, 0, tmp.width, tmp.height);
        return tmp.toDataURL('image/jpeg', 0.65);
    } catch(e) { return null; }
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
    scribble.endingRound = false;
    scribble.pendingNextRound = null;
    scribbleInitCanvas();
    scribbleBuildToolbar();
    sUpdateScores();
    wsSend({ sender: myUsername, message: `__SCRIBBLE__:${JSON.stringify({ event: 'game_start' })}`, image: null });
    appendGameMsg('🎨 Scribble started! Get ready!');
    sNextRound();
}

function sNextRound() {
    scribble.round++;
    if (scribble.round > scribble.maxRounds) { sGameOver(); return; }

    // Reset ALL state cleanly
    scribble.endingRound = false;
    scribble.pendingNextRound = null;
    scribble.active = false;
    scribble.guessedCorrectly = false;
    scribble.aiGuessedCorrectly = { groq: false, router: false };
    scribble.word = '';
    scribble.clueIndex = 0;
    scribble.clues = [];
    scribble.strokes = [];
    sUndoStack = [];

    sClearAllTimers();

    // Hide overlays
    document.getElementById('scribble-result')?.classList.remove('show');
    document.getElementById('scribble-word-pick')?.classList.remove('show');
    const wd = document.getElementById('scribble-your-word');
    if (wd) wd.style.display = 'none';

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
    // Cancel any pending next-round timeout
    if (scribble.pendingNextRound !== null) {
        clearTimeout(scribble.pendingNextRound);
        scribble.pendingNextRound = null;
    }
}

function sClearCanvasForNewRound() {
    if (!sCtx) return;
    sCtx.fillStyle = '#ffffff';
    sCtx.fillRect(0, 0, sCanvas.width, sCanvas.height);
}

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
    const d = scribble.drawer;
    if (d === 'user') { el.textContent = '✏️ Your turn to draw!'; el.style.color = 'var(--accent)'; }
    else { el.textContent = `🎨 ${d === 'groq' ? 'Groq-AI' : 'Router-AI'} is drawing...`; el.style.color = d === 'groq' ? '#99ffcc' : '#aaccff'; }
}

// ══════════════════════════════
// USER DRAW TURN
// ══════════════════════════════

function sShowWordPick() {
    const pick = document.getElementById('scribble-word-pick');
    const opts = document.getElementById('word-options');
    if (!pick || !opts) return;
    const words = [], used = new Set();
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
    document.getElementById('scribble-result')?.classList.remove('show');

    const wd = document.getElementById('scribble-your-word');
    if (wd) { wd.textContent = `Your word: ${word.toUpperCase()}`; wd.style.display = 'block'; }

    appendGameMsg(`Round ${scribble.round}: You're drawing! (${word.length} letters)`);

    wsSend({
        sender: myUsername,
        message: `__SCRIBBLE__:${JSON.stringify({
            event: 'user_draw_start', round: scribble.round,
            wordLength: word.length, word: word,
            hint: scribble.hintRevealed.join('')
        })}`,
        image: null
    });

    sStartTimer();
    clearInterval(scribble.aiGuessInterval);
    scribble.aiGuessInterval = setInterval(sAiGuess, 6000);
    sScheduleHints();
}

// ══════════════════════════════
// AI DRAW TURN
// ══════════════════════════════

function sStartAiDrawTurn() {
    const drawerName = scribble.drawer === 'groq' ? 'Groq-AI' : 'Router-AI';
    sDrawAiThinking(drawerName);
    appendGameMsg(`Round ${scribble.round}: ${drawerName} is drawing! Guess the word!`);
    wsSend({
        sender: myUsername,
        message: `__SCRIBBLE__:${JSON.stringify({
            event: 'ai_draw_request', drawer: scribble.drawer, round: scribble.round
        })}`,
        image: null
    });
}

function sDrawAiThinking(name) {
    if (!sCtx) return;
    sCtx.fillStyle = '#ffffff';
    sCtx.fillRect(0, 0, sCanvas.width, sCanvas.height);
    sCtx.fillStyle = '#ccccdd';
    sCtx.font = `bold ${Math.round(sCanvas.width * 0.036)}px Inter, sans-serif`;
    sCtx.textAlign = 'center'; sCtx.textBaseline = 'middle';
    sCtx.fillText(`🎨 ${name} is drawing...`, sCanvas.width / 2, sCanvas.height / 2 - 18);
    sCtx.font = `${Math.round(sCanvas.width * 0.022)}px Inter, sans-serif`;
    sCtx.fillStyle = '#aaa';
    sCtx.fillText('Type your guess below when you see the drawing!', sCanvas.width / 2, sCanvas.height / 2 + 18);
}

// ══════════════════════════════
// GUESSING
// ══════════════════════════════

// User guesses when AI draws
function sSubmitGuess() {
    const inp = document.getElementById('scribble-guess-inp');
    if (!inp) return;
    const guess = inp.value.trim();
    if (!guess || !scribble.active || scribble.drawer === 'user' || !scribble.word) return;
    inp.value = '';

    appendMsg(myUsername, `🤔 "${guess}"?`);
    appendLiveMsg(myUsername, `🤔 "${guess}"?`);

    wsSend({
        sender: myUsername,
        message: `__SCRIBBLE__:${JSON.stringify({ event: 'user_guess', guess, round: scribble.round })}`,
        image: null
    });

    if (guess.toLowerCase().trim() === scribble.word.toLowerCase()) {
        if (!scribble.guessedCorrectly) {
            scribble.guessedCorrectly = true;
            const pts = sCalcPoints(scribble.word.length, scribble.timeLeft, scribble.maxTime);
            scribble.scores.you += pts;
            // Drawer AI also gets half points
            const drawerKey = scribble.drawer === 'groq' ? 'groq' : 'router';
            scribble.scores[drawerKey] += Math.ceil(pts * 0.5);
            appendMsg('System', `✅ Correct! "${scribble.word.toUpperCase()}" +${pts} pts!`);
            appendLiveMsg('System', `✅ You got it! +${pts} pts`);
            sUpdateScores();
            sEndRound(true);
        }
    } else {
        appendMsg('System', `❌ Nope, keep trying!`);
    }
}

// AI guesses when user draws — sends compressed snapshot
function sAiGuess() {
    if (!scribble.active || scribble.drawer !== 'user' || !sCanvas) return;
    const img = sGetCompressedSnapshot();
    if (!img) return;
    wsSend({
        sender: myUsername,
        message: `__SCRIBBLE__:${JSON.stringify({
            event: 'canvas_snapshot',
            image: img,
            hint: scribble.hintRevealed.join(''),
            wordLength: scribble.word.length,
            round: scribble.round
        })}`,
        image: null
    });
}

// ══════════════════════════════
// HANDLE MESSAGES FROM BACKEND
// ══════════════════════════════

function handleScribbleMsg(data) {
    try {
        const d = JSON.parse(data.message.replace('__SCRIBBLE__:', ''));

        // AI guesses while user draws
        if (d.event === 'ai_guess') {
            if (!scribble.active || scribble.drawer !== 'user' || !d.guess) return;

            const { guesser, guess } = d;
            appendMsg(guesser, `🤔 "${guess}"?`);
            appendLiveMsg(guesser, `🤔 "${guess}"?`);

            if (scribble.word && guess.toLowerCase().trim() === scribble.word.toLowerCase()) {
                const key = guesser.includes('Groq') ? 'groq' : 'router';
                if (!scribble.aiGuessedCorrectly[key]) {
                    scribble.aiGuessedCorrectly[key] = true;
                    const pts = sCalcPoints(scribble.word.length, scribble.timeLeft, scribble.maxTime);
                    scribble.scores[key] += pts;
                    scribble.scores.you += Math.ceil(pts * 0.5); // drawer bonus
                    appendMsg('System', `✅ ${guesser} guessed it! "${scribble.word.toUpperCase()}" +${pts} pts`);
                    appendLiveMsg('System', `✅ ${guesser} correct! +${pts} pts`);
                    sUpdateScores();
                    // End round only when BOTH AIs got it or one got it and timer low
                    if (scribble.aiGuessedCorrectly.groq && scribble.aiGuessedCorrectly.router) {
                        sEndRound(true);
                    }
                }
            }
        }

        // Backend sends word + strokes + clues for AI's drawing turn
        if (d.event === 'ai_draw_start') {
            if (scribble.endingRound) return; // ignore if already ending

            scribble.word = d.word;             // ← set word so user can guess
            scribble.clues = d.clues || [];
            scribble.strokes = d.strokes || [];
            scribble.clueIndex = 0;
            scribble.active = true;
            scribble.timeLeft = scribble.maxTime;
            scribble.hintRevealed = d.word.split('').map(ch => ch === ' ' ? ' ' : '_');
            sUpdateHint();

            document.getElementById('scribble-result')?.classList.remove('show');

            // Update drawer info with source badge
            const drawerInfoEl = document.getElementById('scribble-drawer-info');
            if (drawerInfoEl) {
                const drawerLabel = d.drawer === 'groq' ? 'Groq-AI' : 'Router-AI';
                const sourceBadge = d.source === 'quickdraw'
                    ? '<span style="font-size:0.7em;opacity:0.6;margin-left:6px;">✏️ human drawing</span>'
                    : '';
                drawerInfoEl.innerHTML = `🎨 ${drawerLabel} is drawing...${sourceBadge}`;
            }

            sStartTimer();
            sScheduleHints();
            if (scribble.strokes.length > 0) sPlayAiStrokes(scribble.strokes);

            // Show clues in chat — first after 14s, then every 15s
            clearInterval(scribble.aiClueInterval);
            const clueDelay = setTimeout(() => {
                sShowNextClue();
                scribble.aiClueInterval = setInterval(sShowNextClue, 15000);
            }, 14000);
            scribble.hintTimers.push(clueDelay);
        }

    } catch(e) { console.warn('handleScribbleMsg error:', e); }
}

// Clues must NEVER contain the actual word
function sShowNextClue() {
    if (!scribble.active || scribble.drawer === 'user') return;
    if (scribble.clueIndex >= scribble.clues.length) return;

    let clue = scribble.clues[scribble.clueIndex++];

    // Safety check — strip the word if it somehow appears in clue
    if (scribble.word) {
        const regex = new RegExp(scribble.word, 'gi');
        clue = clue.replace(regex, '???');
        // If clue is basically just the answer, skip it
        if (clue.toLowerCase().includes(scribble.word.toLowerCase())) return;
    }

    const drawerName = scribble.drawer === 'groq' ? 'Groq-AI' : 'Router-AI';
    appendMsg(drawerName, `💡 ${clue}`);
    appendLiveMsg(drawerName, `💡 ${clue}`);
}

// ══════════════════════════════
// TIMER & HINTS
// ══════════════════════════════

function sStartTimer() {
    clearInterval(scribble.timerInterval);
    sUpdateTimerUI();
    scribble.timerInterval = setInterval(() => {
        if (!scribble.active) { clearInterval(scribble.timerInterval); return; }
        scribble.timeLeft--;
        sUpdateTimerUI();
        if (scribble.timeLeft <= 0) {
            clearInterval(scribble.timerInterval);
            sEndRound(false);
        }
    }, 1000);
}

function sUpdateTimerUI() {
    const fill = document.getElementById('timer-fill');
    const txt = document.getElementById('timer-txt');
    if (fill) { fill.style.width = (scribble.timeLeft / scribble.maxTime * 100) + '%'; fill.classList.toggle('low', scribble.timeLeft <= 15); }
    if (txt) txt.textContent = scribble.timeLeft + 's';
}

function sUpdateHint() {
    const el = document.getElementById('scribble-hint');
    if (el) el.textContent = scribble.hintRevealed.join(' ');
}

function sScheduleHints() {
    scribble.hintTimers.forEach(t => clearTimeout(t));
    scribble.hintTimers = [];
    [20, 35, 50].forEach(sec => {
        const t = setTimeout(() => { if (scribble.active) sRevealLetter(); }, sec * 1000);
        scribble.hintTimers.push(t);
    });
}

function sRevealLetter() {
    const hidden = scribble.hintRevealed.map((ch, i) => ch === '_' ? i : -1).filter(i => i !== -1);
    if (hidden.length <= 1) return;
    const idx = hidden[Math.floor(Math.random() * hidden.length)];
    scribble.hintRevealed[idx] = scribble.word[idx];
    sUpdateHint();
}

function sUpdateScores() {
    ['you','groq','router'].forEach(k => {
        const el = document.getElementById(`ss-${k}-pts`);
        if (el) el.textContent = scribble.scores[k];
    });
}

// ══════════════════════════════
// ROUND END & GAME OVER
// ══════════════════════════════

function sEndRound(guessed) {
    // ← CRITICAL: prevent double-call from timer + AI guess both firing
    if (scribble.endingRound) return;
    scribble.endingRound = true;
    scribble.active = false;

    sClearAllTimers(); // also cancels any existing pendingNextRound

    const wd = document.getElementById('scribble-your-word');
    if (wd) wd.style.display = 'none';

    // Reveal full word
    if (scribble.word) {
        scribble.hintRevealed = scribble.word.split('');
        sUpdateHint();
    }

    const resultEl = document.getElementById('scribble-result');
    const h3 = document.getElementById('scribble-result-title');
    const p = document.getElementById('scribble-result-text');
    if (h3) h3.textContent = guessed ? '🎉 Word Guessed!' : "⏰ Time's Up!";
    if (p) p.textContent = `The word was "${scribble.word.toUpperCase()}"`;
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
        ? `✅ Round ${scribble.round} done! Word: "${scribble.word}"`
        : `⏰ Time's up! Word was "${scribble.word}"`
    );

    sUpdateScores();

    // Schedule next round — save ID so it can be cancelled if needed
    scribble.pendingNextRound = setTimeout(() => {
        scribble.pendingNextRound = null;
        sNextRound();
    }, 3500);
}

function sGameOver() {
    scribble.active = false;
    scribble.endingRound = true;
    sClearAllTimers();

    const { you, groq, router } = scribble.scores;
    let winner = 'You', maxScore = you;
    if (groq > maxScore) { winner = 'Groq-AI'; maxScore = groq; }
    if (router > maxScore) { winner = 'Router-AI'; maxScore = router; }

    const resultEl = document.getElementById('scribble-result');
    const h3 = document.getElementById('scribble-result-title');
    const p = document.getElementById('scribble-result-text');
    if (h3) h3.textContent = '🏆 Game Over!';
    if (p) p.innerHTML = `Winner: <strong>${winner}</strong> with ${maxScore} pts!<br><small>You: ${you} | Groq: ${groq} | Router: ${router}</small>`;
    if (resultEl) resultEl.classList.add('show');

    appendGameMsg(`🏆 Scribble over! ${winner} wins with ${maxScore} pts!`);

    wsSend({
        sender: myUsername,
        message: `__SCRIBBLE__:${JSON.stringify({ event: 'game_over', winner, scores: scribble.scores })}`,
        image: null
    });
}

function scribbleReset() {
    scribble.active = false;
    scribble.endingRound = true;
    sClearAllTimers();
    document.getElementById('scribble-start-screen').style.display = 'flex';
    document.getElementById('scribble-game-area').style.display = 'none';
    document.getElementById('scribble-result')?.classList.remove('show');
    document.getElementById('scribble-word-pick')?.classList.remove('show');
}