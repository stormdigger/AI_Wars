// ═══════════════════════════════════════════
// SCRIBBLE GAME – Draw & AI Guesses
// Canvas drawing + periodic AI vision guessing
// ═══════════════════════════════════════════

// ── Word bank (200+ drawable nouns) ──
const WORD_BANK = [
    'apple', 'banana', 'car', 'dog', 'elephant', 'fish', 'guitar', 'house', 'ice cream',
    'jellyfish', 'kite', 'lion', 'moon', 'notebook', 'octopus', 'pizza', 'queen', 'robot',
    'sun', 'tree', 'umbrella', 'violin', 'whale', 'xylophone', 'yacht', 'zebra',
    'airplane', 'butterfly', 'castle', 'dinosaur', 'earth', 'flower', 'ghost', 'helicopter',
    'island', 'juice', 'kangaroo', 'laptop', 'mushroom', 'ninja', 'owl', 'penguin',
    'rainbow', 'spider', 'tornado', 'unicorn', 'volcano', 'waterfall', 'dragon', 'rocket',
    'camera', 'diamond', 'feather', 'grapes', 'hammer', 'igloo', 'jacket', 'key',
    'lamp', 'mountain', 'necklace', 'ocean', 'parachute', 'ring', 'skateboard', 'telescope',
    'umbrella', 'vase', 'windmill', 'star', 'sword', 'crown', 'anchor', 'balloon',
    'candle', 'drum', 'envelope', 'flag', 'globe', 'heart', 'iceberg', 'jar',
    'kettle', 'leaf', 'magnet', 'nest', 'orange', 'pencil', 'quilt', 'rose',
    'snowflake', 'tent', 'ufo', 'wave', 'cat', 'bird', 'frog', 'snake',
    'turtle', 'rabbit', 'bear', 'bee', 'crab', 'deer', 'eagle', 'fox',
    'giraffe', 'horse', 'koala', 'monkey', 'panda', 'shark', 'tiger', 'wolf',
    'boat', 'bridge', 'bus', 'chair', 'clock', 'door', 'elevator', 'fan',
    'glasses', 'hat', 'iron', 'ladder', 'mirror', 'piano', 'scissors', 'table',
    'train', 'truck', 'window', 'zipper', 'basketball', 'football', 'baseball', 'tennis',
    'soccer', 'bowling', 'surfing', 'skiing', 'boxing', 'chess', 'dice', 'medal',
    'trophy', 'fire', 'lightning', 'cloud', 'rain', 'snow', 'sunrise', 'sunset',
    'beach', 'forest', 'desert', 'cave', 'river', 'waterfall', 'lighthouse', 'barn',
    'church', 'hospital', 'school', 'library', 'store', 'restaurant', 'hotel', 'airport',
    'sandwich', 'burger', 'taco', 'sushi', 'cake', 'cookie', 'donut', 'popcorn',
    'coffee', 'milk', 'bread', 'cheese', 'egg', 'steak', 'salad', 'soup', 'pie'
];

// ── Game state ──
let scribble = {
    active: false,
    round: 0,
    maxRounds: 5,
    word: '',
    hintRevealed: [],
    timeLeft: 60,
    maxTime: 60,
    scores: { you: 0, groq: 0, router: 0 },
    guessedCorrectly: { groq: false, router: false },
    timerInterval: null,
    aiGuessInterval: null,
    drawingHistory: [],
    currentPath: null,
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

    // Set actual pixel dimensions
    const wrap = sCanvas.parentElement;
    const w = wrap.clientWidth || 520;
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

    // Mouse events
    sCanvas.addEventListener('mousedown', sStartDraw);
    sCanvas.addEventListener('mousemove', sDraw);
    sCanvas.addEventListener('mouseup', sEndDraw);
    sCanvas.addEventListener('mouseleave', sEndDraw);

    // Touch events
    sCanvas.addEventListener('touchstart', sTouchStart, { passive: false });
    sCanvas.addEventListener('touchmove', sTouchMove, { passive: false });
    sCanvas.addEventListener('touchend', sEndDraw);
    sCanvas.addEventListener('touchcancel', sEndDraw);
}

function sGetPos(e) {
    const rect = sCanvas.getBoundingClientRect();
    const scaleX = sCanvas.width / rect.width;
    const scaleY = sCanvas.height / rect.height;
    return {
        x: (e.clientX - rect.left) * scaleX,
        y: (e.clientY - rect.top) * scaleY
    };
}

function sTouchPos(e) {
    const t = e.touches[0];
    const rect = sCanvas.getBoundingClientRect();
    const scaleX = sCanvas.width / rect.width;
    const scaleY = sCanvas.height / rect.height;
    return {
        x: (t.clientX - rect.left) * scaleX,
        y: (t.clientY - rect.top) * scaleY
    };
}

function sStartDraw(e) {
    if (!scribble.active) return;
    sDrawing = true;
    const pos = sGetPos(e);
    sCtx.beginPath();
    sCtx.moveTo(pos.x, pos.y);
    // Save snapshot for undo
    sUndoStack.push(sCtx.getImageData(0, 0, sCanvas.width, sCanvas.height));
    if (sUndoStack.length > 20) sUndoStack.shift();
}

function sDraw(e) {
    if (!sDrawing || !scribble.active) return;
    const pos = sGetPos(e);
    sCtx.strokeStyle = sEraser ? '#ffffff' : sColor;
    sCtx.lineWidth = sEraser ? sSize * 3 : sSize;
    sCtx.lineTo(pos.x, pos.y);
    sCtx.stroke();
}

function sEndDraw() {
    sDrawing = false;
}

function sTouchStart(e) {
    e.preventDefault();
    if (!scribble.active) return;
    sDrawing = true;
    const pos = sTouchPos(e);
    sCtx.beginPath();
    sCtx.moveTo(pos.x, pos.y);
    sUndoStack.push(sCtx.getImageData(0, 0, sCanvas.width, sCanvas.height));
    if (sUndoStack.length > 20) sUndoStack.shift();
}

function sTouchMove(e) {
    e.preventDefault();
    if (!sDrawing || !scribble.active) return;
    const pos = sTouchPos(e);
    sCtx.strokeStyle = sEraser ? '#ffffff' : sColor;
    sCtx.lineWidth = sEraser ? sSize * 3 : sSize;
    sCtx.lineTo(pos.x, pos.y);
    sCtx.stroke();
}

// ── Drawing tools ──
function sSetColor(color) {
    sColor = color;
    sEraser = false;
    document.querySelectorAll('.scolor-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.color === color);
    });
}

function sSetSize(size) {
    sSize = size;
    document.querySelectorAll('.ssize-btn').forEach(b => {
        b.classList.toggle('active', parseInt(b.dataset.size) === size);
    });
}

function sSetEraser() {
    sEraser = true;
    document.querySelectorAll('.scolor-btn').forEach(b => b.classList.remove('active'));
}

function sUndo() {
    if (sUndoStack.length > 0) {
        sCtx.putImageData(sUndoStack.pop(), 0, 0);
    }
}

function sClearCanvas() {
    sUndoStack.push(sCtx.getImageData(0, 0, sCanvas.width, sCanvas.height));
    sCtx.fillStyle = '#ffffff';
    sCtx.fillRect(0, 0, sCanvas.width, sCanvas.height);
}

// ── Build toolbar ──
function scribbleBuildToolbar() {
    const tb = document.getElementById('scribble-toolbar');
    if (!tb) return;
    tb.innerHTML = '';

    // Color buttons
    SCOLORS.forEach(c => {
        const btn = document.createElement('div');
        btn.className = 'scolor-btn' + (c === '#000000' ? ' active' : '');
        btn.style.background = c;
        if (c === '#ffffff') btn.style.border = '2px solid #ccc';
        btn.dataset.color = c;
        btn.onclick = () => sSetColor(c);
        tb.appendChild(btn);
    });

    // Separator
    tb.appendChild(Object.assign(document.createElement('div'), { className: 'stool-sep' }));

    // Sizes
    [2, 4, 8].forEach((s, i) => {
        const btn = document.createElement('div');
        btn.className = 'ssize-btn' + (s === 4 ? ' active' : '');
        btn.dataset.size = s;
        const dot = document.createElement('div');
        dot.className = 'ssize-dot';
        const ds = [4, 7, 12][i];
        dot.style.width = ds + 'px';
        dot.style.height = ds + 'px';
        btn.appendChild(dot);
        btn.onclick = () => sSetSize(s);
        tb.appendChild(btn);
    });

    tb.appendChild(Object.assign(document.createElement('div'), { className: 'stool-sep' }));

    // Eraser
    const erBtn = document.createElement('button');
    erBtn.className = 'stool-btn';
    erBtn.textContent = '🧽 Eraser';
    erBtn.onclick = sSetEraser;
    tb.appendChild(erBtn);

    // Undo
    const undoBtn = document.createElement('button');
    undoBtn.className = 'stool-btn';
    undoBtn.textContent = '↩ Undo';
    undoBtn.onclick = sUndo;
    tb.appendChild(undoBtn);

    // Clear
    const clrBtn = document.createElement('button');
    clrBtn.className = 'stool-btn';
    clrBtn.textContent = '🗑 Clear';
    clrBtn.onclick = sClearCanvas;
    tb.appendChild(clrBtn);
}

// ── Game flow ──
function startScribbleGame() {
    if (!myUsername) { alert('Join a room first!'); return; }

    document.getElementById('scribble-start-screen').style.display = 'none';
    document.getElementById('scribble-game-area').style.display = 'block';

    scribble.round = 0;
    scribble.scores = { you: 0, groq: 0, router: 0 };

    scribbleInitCanvas();
    scribbleBuildToolbar();
    sUpdateScores();

    // Start first round
    sNextRound();
}

function sNextRound() {
    scribble.round++;
    if (scribble.round > scribble.maxRounds) {
        sGameOver();
        return;
    }

    // Reset state
    scribble.active = false;
    scribble.guessedCorrectly = { groq: false, router: false };
    sUndoStack = [];

    // Clear canvas
    if (sCtx) {
        sCtx.fillStyle = '#ffffff';
        sCtx.fillRect(0, 0, sCanvas.width, sCanvas.height);
    }

    // Show word choices
    const roundEl = document.getElementById('scribble-round');
    if (roundEl) roundEl.textContent = `Round ${scribble.round}/${scribble.maxRounds}`;

    sShowWordPick();
}

function sShowWordPick() {
    const pick = document.getElementById('scribble-word-pick');
    const opts = document.getElementById('word-options');
    if (!pick || !opts) return;

    // Pick 3 random words
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
        btn.onclick = () => sPickWord(w);
        opts.appendChild(btn);
    });

    pick.classList.add('show');
}

function sPickWord(word) {
    scribble.word = word;
    scribble.active = true;
    scribble.timeLeft = scribble.maxTime;

    // Setup hint
    scribble.hintRevealed = word.split('').map(ch => ch === ' ' ? ' ' : '_');
    sUpdateHint();

    // Hide word picker
    document.getElementById('scribble-word-pick').classList.remove('show');

    // Hide result overlay if visible
    const resultEl = document.getElementById('scribble-result');
    if (resultEl) resultEl.classList.remove('show');

    // Notify in chat
    appendGameMsg(`Round ${scribble.round}: Drawing started! (${word.length} letters)`);

    // Broadcast to room that round started
    wsSend({
        sender: myUsername,
        message: `__SCRIBBLE__:${JSON.stringify({
            event: 'round_start',
            round: scribble.round,
            wordLength: word.length,
            hint: scribble.hintRevealed.join('')
        })}`,
        image: null
    });

    // Start timer
    sStartTimer();

    // Start AI guessing every 5 seconds
    clearInterval(scribble.aiGuessInterval);
    scribble.aiGuessInterval = setInterval(sAiGuess, 5000);

    // Schedule hint reveals
    sScheduleHints();
}

function sStartTimer() {
    clearInterval(scribble.timerInterval);
    sUpdateTimerUI();

    scribble.timerInterval = setInterval(() => {
        scribble.timeLeft--;
        sUpdateTimerUI();

        if (scribble.timeLeft <= 0) {
            sEndRound(false);
        }
    }, 1000);
}

function sUpdateTimerUI() {
    const fill = document.getElementById('timer-fill');
    const txt = document.getElementById('timer-txt');
    if (fill) {
        const pct = (scribble.timeLeft / scribble.maxTime) * 100;
        fill.style.width = pct + '%';
        fill.classList.toggle('low', scribble.timeLeft <= 15);
    }
    if (txt) txt.textContent = scribble.timeLeft + 's';
}

function sUpdateHint() {
    const el = document.getElementById('scribble-hint');
    if (el) el.textContent = scribble.hintRevealed.join(' ');
}

function sScheduleHints() {
    // Reveal letters at 40s, 25s, 10s remaining
    const revealTimes = [20, 35, 50]; // seconds elapsed
    revealTimes.forEach(t => {
        setTimeout(() => {
            if (!scribble.active) return;
            sRevealLetter();
        }, t * 1000);
    });
}

function sRevealLetter() {
    const hidden = [];
    scribble.hintRevealed.forEach((ch, i) => {
        if (ch === '_') hidden.push(i);
    });
    if (hidden.length <= 1) return;
    const idx = hidden[Math.floor(Math.random() * hidden.length)];
    scribble.hintRevealed[idx] = scribble.word[idx];
    sUpdateHint();

    // Broadcast hint update
    wsSend({
        sender: myUsername,
        message: `__SCRIBBLE__:${JSON.stringify({
            event: 'hint',
            hint: scribble.hintRevealed.join('')
        })}`,
        image: null
    });
}

function sUpdateScores() {
    const els = {
        you: document.getElementById('ss-you-pts'),
        groq: document.getElementById('ss-groq-pts'),
        router: document.getElementById('ss-router-pts'),
    };
    if (els.you) els.you.textContent = scribble.scores.you;
    if (els.groq) els.groq.textContent = scribble.scores.groq;
    if (els.router) els.router.textContent = scribble.scores.router;
}

// ── AI Guessing (send canvas to backend for vision) ──
function sAiGuess() {
    if (!scribble.active || !sCanvas) return;

    // Convert canvas to base64
    const dataUrl = sCanvas.toDataURL('image/png', 0.7);

    // Send to backend for vision guessing
    wsSend({
        sender: myUsername,
        message: `__SCRIBBLE__:${JSON.stringify({
            event: 'canvas_snapshot',
            image: dataUrl,
            hint: scribble.hintRevealed.join(''),
            wordLength: scribble.word.length,
            round: scribble.round
        })}`,
        image: null
    });
}

// ── Handle AI guess response from backend ──
function handleScribbleMsg(data) {
    try {
        const d = JSON.parse(data.message.replace('__SCRIBBLE__:', ''));

        if (d.event === 'ai_guess') {
            const guesser = d.guesser;  // 'Groq-AI' or 'Router-AI'
            const guess = d.guess;

            // Show guess in chat
            appendMsg(guesser, `🎨 "${guess}"?`);
            appendLiveMsg(guesser, `🎨 "${guess}"?`);

            // Check if correct
            if (scribble.active && guess.toLowerCase().trim() === scribble.word.toLowerCase()) {
                const key = guesser.includes('Groq') ? 'groq' : 'router';
                if (!scribble.guessedCorrectly[key]) {
                    scribble.guessedCorrectly[key] = true;

                    // Points: faster = more points
                    const timeBonus = Math.ceil(scribble.timeLeft * 1.5);
                    scribble.scores[key] += timeBonus;
                    scribble.scores.you += Math.ceil(timeBonus * 0.5); // Drawer gets half points

                    appendMsg('System', `✅ ${guesser} guessed it! "${scribble.word}" (+${timeBonus} pts)`);
                    appendLiveMsg('System', `✅ ${guesser} guessed correctly!`);

                    sUpdateScores();

                    // If both guessed, end round
                    if (scribble.guessedCorrectly.groq && scribble.guessedCorrectly.router) {
                        sEndRound(true);
                    }
                }
            }
        }

        if (d.event === 'round_start' || d.event === 'hint') {
            // Sync from other clients if needed
        }
    } catch (e) { }
}

function sEndRound(guessed) {
    scribble.active = false;
    clearInterval(scribble.timerInterval);
    clearInterval(scribble.aiGuessInterval);

    // Reveal word
    scribble.hintRevealed = scribble.word.split('');
    sUpdateHint();

    // Show result
    const resultEl = document.getElementById('scribble-result');
    const resultH3 = document.getElementById('scribble-result-title');
    const resultP = document.getElementById('scribble-result-text');

    if (guessed) {
        if (resultH3) resultH3.textContent = '🎉 Word Guessed!';
        if (resultP) resultP.textContent = `The word was "${scribble.word}"`;
    } else {
        if (resultH3) resultH3.textContent = '⏰ Time\'s Up!';
        if (resultP) resultP.textContent = `The word was "${scribble.word}"`;
    }

    if (resultEl) resultEl.classList.add('show');

    // Broadcast round end
    wsSend({
        sender: myUsername,
        message: `__SCRIBBLE__:${JSON.stringify({
            event: guessed ? 'round_guessed' : 'round_timeout',
            word: scribble.word,
            round: scribble.round
        })}`,
        image: null
    });

    appendGameMsg(guessed
        ? `Round ${scribble.round} complete! Word: "${scribble.word}"`
        : `Time's up! Word was "${scribble.word}"`
    );

    sUpdateScores();

    // Next round after delay
    setTimeout(() => {
        sNextRound();
    }, 3000);
}

function sGameOver() {
    scribble.active = false;
    clearInterval(scribble.timerInterval);
    clearInterval(scribble.aiGuessInterval);

    // Determine winner
    const { you, groq, router } = scribble.scores;
    let winner = 'You';
    let maxScore = you;
    if (groq > maxScore) { winner = 'Groq-AI'; maxScore = groq; }
    if (router > maxScore) { winner = 'Router-AI'; maxScore = router; }

    const resultEl = document.getElementById('scribble-result');
    const resultH3 = document.getElementById('scribble-result-title');
    const resultP = document.getElementById('scribble-result-text');

    if (resultH3) resultH3.textContent = '🏆 Game Over!';
    if (resultP) resultP.innerHTML = `Winner: <strong>${winner}</strong> with ${maxScore} points!`;
    if (resultEl) resultEl.classList.add('show');

    appendGameMsg(`🏆 Scribble game over! ${winner} wins with ${maxScore} pts!`);

    wsSend({
        sender: myUsername,
        message: `__SCRIBBLE__:${JSON.stringify({
            event: 'game_over',
            winner: winner,
            scores: scribble.scores
        })}`,
        image: null
    });
}

function scribbleReset() {
    scribble.active = false;
    clearInterval(scribble.timerInterval);
    clearInterval(scribble.aiGuessInterval);

    // Show start screen
    document.getElementById('scribble-start-screen').style.display = 'flex';
    document.getElementById('scribble-game-area').style.display = 'none';

    const resultEl = document.getElementById('scribble-result');
    if (resultEl) resultEl.classList.remove('show');
    const pickEl = document.getElementById('scribble-word-pick');
    if (pickEl) pickEl.classList.remove('show');
}
