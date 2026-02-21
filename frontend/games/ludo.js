// ═══════════════════════════════════════════
// LUDO ENGINE – Full game logic, rendering, AI
// ═══════════════════════════════════════════

const LT = [ // 52-cell main track [row,col]
    [6, 1], [6, 2], [6, 3], [6, 4], [6, 5],
    [5, 6], [4, 6], [3, 6], [2, 6], [1, 6], [0, 6], [0, 7],
    [0, 8], [1, 8], [2, 8], [3, 8], [4, 8], [5, 8],
    [6, 9], [6, 10], [6, 11], [6, 12], [6, 13], [6, 14], [7, 14],
    [8, 14], [8, 13], [8, 12], [8, 11], [8, 10], [8, 9],
    [9, 8], [10, 8], [11, 8], [12, 8], [13, 8], [14, 8], [14, 7],
    [14, 6], [13, 6], [12, 6], [11, 6], [10, 6], [9, 6],
    [8, 5], [8, 4], [8, 3], [8, 2], [8, 1], [8, 0], [7, 0]
];

const LSTART = { red: 0, green: 13, blue: 26 };
const LENTRY = { red: 51, green: 12, blue: 25 };

const LSTRETCH = {
    red: [[7, 1], [7, 2], [7, 3], [7, 4], [7, 5], [7, 6]],
    green: [[1, 7], [2, 7], [3, 7], [4, 7], [5, 7], [6, 7]],
    blue: [[7, 13], [7, 12], [7, 11], [7, 10], [7, 9], [7, 8]]
};

const LYARD = {
    red: [[1, 1], [2, 1], [1, 2], [2, 2]],
    green: [[1, 12], [2, 12], [1, 13], [2, 13]],
    blue: [[12, 12], [13, 12], [12, 13], [13, 13]]
};

const LSAFE = [0, 8, 13, 21, 26, 34, 39, 47];
const DF = ['⚀', '⚁', '⚂', '⚃', '⚄', '⚅'];
const LCOLORS = ['red', 'green', 'blue'];
const LCOL = { red: 'var(--red)', green: 'var(--green)', blue: 'var(--blue)' };
const LEMOJI = { red: '🔴', green: '🟢', blue: '🔵' };

let ludo = {
    pos: { red: [-1, -1, -1, -1], green: [-1, -1, -1, -1], blue: [-1, -1, -1, -1] },
    turn: 'red', dice: null, rolled: false, movable: [], winner: null, started: false
};
let lAiTimer = null;

// ── Core helpers ──
function lAbsIdx(c, rel) { return (LSTART[c] + rel) % 52; }

function lCoord(c, pos) {
    if (pos === -1) return null;
    if (pos === 200) return [7, 7];
    if (pos >= 100) return LSTRETCH[c][pos - 100];
    return LT[lAbsIdx(c, pos)];
}

function lCanMove(c, ti, dice) {
    const p = ludo.pos[c][ti];
    if (p === 200) return false;
    if (p === -1) return dice === 6;
    if (p >= 100) return (p - 100 + dice) <= 5;
    const abs = lAbsIdx(c, p), ea = LENTRY[c];
    let te = ea >= abs ? ea - abs : 52 - abs + ea;
    if (dice <= te) return true;
    return (dice - te - 1) <= 5;
}

function lDoMove(c, ti, dice) {
    let p = ludo.pos[c][ti];
    if (p === -1) { ludo.pos[c][ti] = 0; lCheckCapture(c, ti); return; }
    if (p >= 100) { const np = p + dice; ludo.pos[c][ti] = (np - 100 >= 5) ? 200 : np; return; }
    const abs = lAbsIdx(c, p), ea = LENTRY[c];
    let te = ea >= abs ? ea - abs : 52 - abs + ea;
    if (dice <= te) { ludo.pos[c][ti] = p + dice; lCheckCapture(c, ti); }
    else { const h = dice - te - 1; ludo.pos[c][ti] = h >= 5 ? 200 : (100 + h); }
}

function lCheckCapture(c, ti) {
    const p = ludo.pos[c][ti];
    if (p < 0 || p >= 100) return;
    const abs = lAbsIdx(c, p);
    if (LSAFE.includes(abs)) return;
    for (const oc of LCOLORS) {
        if (oc === c) continue;
        for (let t = 0; t < 4; t++) {
            const op = ludo.pos[oc][t];
            if (op >= 0 && op < 100 && lAbsIdx(oc, op) === abs) {
                ludo.pos[oc][t] = -1;
                lLog(`💥 ${LEMOJI[c]} captured ${LEMOJI[oc]}!`);
            }
        }
    }
}

function lMovable(c, dice) { return [0, 1, 2, 3].filter(i => lCanMove(c, i, dice)); }
function lWon(c) { return ludo.pos[c].every(p => p === 200); }
function lDescPos(c, p) {
    if (p === -1) return 'yard';
    if (p === 200) return 'goal';
    if (p >= 100) return `hs${p - 100 + 1}`;
    return `t${p}`;
}

// ── Board build ──
function ludoBuildBoard() {
    const bd = document.getElementById('ludo-board');
    bd.innerHTML = '';
    for (let r = 0; r < 15; r++) {
        for (let c = 0; c < 15; c++) {
            const el = document.createElement('div');
            el.className = 'lc';
            el.id = `lc${r}-${c}`;
            bd.appendChild(el);
        }
    }
    lZone(0, 0, 5, 5, 'lhr');
    lZone(0, 9, 5, 14, 'lhg');
    lZone(9, 9, 14, 14, 'lhb');
    lZone(9, 0, 14, 5, 'lhy');

    // Home circles
    [[1, 1], [1, 2], [2, 1], [2, 2], [1, 12], [1, 13], [2, 12], [2, 13],
    [12, 12], [12, 13], [13, 12], [13, 13], [12, 1], [12, 2], [13, 1], [13, 2]]
        .forEach(([r, c]) => lc(r, c).classList.add('lcircle'));

    LT.forEach(([r, c]) => lc(r, c).classList.add('lpath'));

    // Color paths
    for (let i = 1; i <= 5; i++) lc(6, i).classList.add('lpr');
    for (let i = 1; i <= 5; i++) lc(i, 8).classList.add('lpg');
    for (let i = 9; i <= 13; i++) lc(8, i).classList.add('lpb');
    for (let i = 9; i <= 13; i++) lc(i, 6).classList.add('lpy');

    // Home stretches
    LSTRETCH.red.forEach(([r, c]) => lc(r, c).classList.add('lsr'));
    LSTRETCH.green.forEach(([r, c]) => lc(r, c).classList.add('lsg'));
    LSTRETCH.blue.forEach(([r, c]) => lc(r, c).classList.add('lsb'));

    // Safe stars
    LSAFE.forEach(i => { const [r, c] = LT[i]; lc(r, c).classList.add('lsafe'); });

    // Center
    lc(7, 7).classList.add('lcenter');

    // Starting cells
    lc(6, 1).classList.add('lpr');
    lc(0, 8).classList.add('lpg');
    lc(8, 13).classList.add('lpb');
}

function lc(r, c) { return document.getElementById(`lc${r}-${c}`); }

function lZone(r1, c1, r2, c2, cls) {
    for (let r = r1; r <= r2; r++)
        for (let c = c1; c <= c2; c++)
            lc(r, c)?.classList.add(cls);
}

// ── Token rendering ──
function lRenderTokens() {
    document.querySelectorAll('.tok').forEach(t => t.remove());
    const occ = {};

    for (const color of LCOLORS) {
        for (let ti = 0; ti < 4; ti++) {
            const pos = ludo.pos[color][ti];
            const coord = pos === -1 ? LYARD[color][ti] : lCoord(color, pos);
            if (!coord) continue;

            const [r, c] = coord;
            const key = `${r}-${c}`;
            const idx = occ[key] || 0;
            occ[key] = idx + 1;

            const cell = lc(r, c);
            if (!cell) continue;

            const tok = document.createElement('div');
            const cl = { red: 'r', green: 'g', blue: 'b' }[color];
            tok.className = `tok tok-${cl}`;
            tok.textContent = ti + 1;

            // Offset for stacking
            const cellSize = cell.offsetWidth || 24;
            const ofs = Math.max(4, cellSize * 0.2);
            const ox = [-ofs, ofs, -ofs, ofs][idx];
            const oy = [-ofs, -ofs, ofs, ofs][idx];
            const base = Math.max(2, (cellSize - (cellSize * 0.6)) / 2);
            tok.style.left = `${base + ox}px`;
            tok.style.top = `${base + oy}px`;

            if (ludo.turn === color && ludo.rolled && ludo.movable.includes(ti)) {
                tok.classList.add('mov');
            }

            if (color === 'red' && ludo.turn === 'red' && ludo.rolled && ludo.movable.includes(ti)) {
                tok.style.cursor = 'pointer';
                tok.addEventListener('click', () => lPlayerMove(ti));
            }

            cell.appendChild(tok);
        }
    }
}

// ── UI helpers ──
function lUpdateDots() {
    const el = document.getElementById('tdots');
    el.innerHTML = '';
    for (const c of LCOLORS) {
        const d = document.createElement('div');
        d.className = 'tdot' + (ludo.turn === c ? ' act' : '');
        d.style.background = LCOL[c];
        d.title = { red: myUsername || 'You', green: 'Groq-AI', blue: 'Router-AI' }[c];
        el.appendChild(d);
    }
}

function lSetStat(msg) { document.getElementById('lstat').textContent = msg; }

function lLog(msg) {
    const el = document.getElementById('llog');
    el.innerHTML += msg + '<br>';
    el.scrollTop = el.scrollHeight;
}

function lDiceEl() { return document.getElementById('ldice'); }
function lSetDice(on) { lDiceEl().classList.toggle('doff', !on); }

// ── Roll dice ──
function ludoRoll() {
    if (ludo.turn !== 'red' || ludo.rolled || ludo.winner || !ludo.started) return;

    const val = Math.floor(Math.random() * 6) + 1;
    ludo.dice = val;
    ludo.rolled = true;

    const d = lDiceEl();
    d.classList.add('dspin');
    d.textContent = DF[val - 1];
    setTimeout(() => d.classList.remove('dspin'), 400);

    lLog(`🎲 ${myUsername} rolled ${val}`);
    ludo.movable = lMovable('red', val);

    if (!ludo.movable.length) {
        lLog('No moves — turn skipped');
        setTimeout(() => lNextTurn(false), 1200);
    } else if (ludo.movable.length === 1) {
        setTimeout(() => lPlayerMove(ludo.movable[0]), 550);
    } else {
        lSetStat('Click a glowing token!');
        lRenderTokens();
    }

    lSetDice(false);

    // Broadcast — notable events only
    if (val === 6) {
        lBroadcast(`${myUsername} rolled 6! 🔥`);
    } else {
        lBroadcastQuiet(`${myUsername} rolled ${val}`);
    }
}

// ── Player move ──
function lPlayerMove(ti) {
    if (ludo.turn !== 'red' || !ludo.rolled || !ludo.movable.includes(ti)) return;

    const old = ludo.pos.red[ti];
    lDoMove('red', ti, ludo.dice);
    const newPos = ludo.pos.red[ti];

    lLog(`🔴 Token ${ti + 1}: ${lDescPos('red', old)} → ${lDescPos('red', newPos)}`);

    // Determine if notable
    const wasCapture = old !== -1 && newPos !== old; // capture is detected in lDoMove via lCheckCapture
    if (newPos === 200) {
        lBroadcast(`${myUsername}'s token ${ti + 1} reached GOAL! 🎯`);
    } else if (newPos >= 100 && old < 100) {
        lBroadcast(`${myUsername}'s token entered home stretch!`);
    } else {
        lBroadcastQuiet(`${myUsername} moved token ${ti + 1}`);
    }

    if (lWon('red')) { lSetWinner('red'); return; }
    lNextTurn(ludo.dice === 6);
}

// ── Next turn ──
function lNextTurn(extra) {
    ludo.rolled = false;
    ludo.movable = [];
    ludo.dice = null;
    lDiceEl().textContent = '🎲';

    if (!extra) {
        const i = LCOLORS.indexOf(ludo.turn);
        ludo.turn = LCOLORS[(i + 1) % LCOLORS.length];
    }

    lUpdateDots();
    lRenderTokens();

    const nm = { red: myUsername || 'You', green: 'Groq-AI', blue: 'Router-AI' }[ludo.turn];
    lSetStat(`${nm}'s turn`);

    if (ludo.turn !== 'red') {
        lSetDice(false);
        clearTimeout(lAiTimer);
        lAiTimer = setTimeout(() => lAiTurn(ludo.turn), 1900);
    } else {
        lSetDice(true);
        lSetStat('Your turn — roll the dice!');
    }
}

// ── AI turn ──
function lAiTurn(color) {
    if (ludo.turn !== color || ludo.winner) return;

    const val = Math.floor(Math.random() * 6) + 1;
    ludo.dice = val;
    ludo.rolled = true;

    const d = lDiceEl();
    d.textContent = DF[val - 1];
    d.classList.add('dspin');
    setTimeout(() => d.classList.remove('dspin'), 400);

    const nm = { green: 'Groq-AI', blue: 'Router-AI' }[color];
    lLog(`🎲 ${nm} rolled ${val}`);

    const mv = lMovable(color, val);
    ludo.movable = mv;

    setTimeout(() => {
        if (!mv.length) {
            lLog(`${nm}: no moves`);
            lBroadcastQuiet(`${nm} rolled ${val}, no moves`);
            lNextTurn(false);
        } else {
            const ti = lAiChoose(color, mv, val);
            const old = ludo.pos[color][ti];
            lDoMove(color, ti, val);
            const newPos = ludo.pos[color][ti];

            lLog(`${LEMOJI[color]} ${nm} token ${ti + 1}: ${lDescPos(color, old)} → ${lDescPos(color, newPos)}`);

            // Determine notability for AI moves
            if (val === 6) {
                lBroadcast(`${nm} rolled 6! 🔥`);
            } else if (newPos === 200) {
                lBroadcast(`${nm}'s token reached GOAL! 🎯`);
            } else if (newPos >= 100 && old < 100) {
                lBroadcast(`${nm}'s token entered home stretch!`);
            } else {
                lBroadcastQuiet(`${nm} rolled ${val}, moved token ${ti + 1}`);
            }

            if (lWon(color)) { lSetWinner(color); return; }
            lNextTurn(val === 6);
        }
    }, 1350);
}

// ── AI strategy ──
function lAiChoose(color, mv, dice) {
    let best = mv[0], bestS = -Infinity;
    for (const ti of mv) {
        const p = ludo.pos[color][ti];
        let s = 0;
        if (p === -1) s = 65;
        else if (p >= 100) s = 210 + p;
        else {
            let cap = false;
            if (p + dice < 52) {
                const na = lAbsIdx(color, p + dice);
                if (!LSAFE.includes(na)) {
                    for (const oc of LCOLORS) {
                        if (oc === color) continue;
                        for (let t = 0; t < 4; t++) {
                            const op = ludo.pos[oc][t];
                            if (op >= 0 && op < 100 && lAbsIdx(oc, op) === na) cap = true;
                        }
                    }
                }
            }
            s = cap ? 185 : p + dice;
        }
        if (s > bestS) { bestS = s; best = ti; }
    }
    return best;
}

// ── Winner ──
function lSetWinner(color) {
    ludo.winner = color;
    const nm = { red: myUsername || 'You', green: 'Groq-AI', blue: 'Router-AI' }[color];
    document.getElementById('wemoji').textContent = LEMOJI[color];
    document.getElementById('wtext').textContent = `${nm} WINS!`;
    document.getElementById('ludo-winner').classList.add('show');
    lLog(`🏆 ${nm} wins!`);
    lBroadcast(`${nm} WON Ludo! 🏆`);
    lSetDice(false);
}

// ── Broadcasting ──
function lBuildSummary() {
    return LCOLORS.map(c =>
        `${c}:[${ludo.pos[c].map((p, i) => lDescPos(c, p)).join(',')}]`
    ).join(' ');
}

// Notable broadcast — triggers AI chat
function lBroadcast(event) {
    if (!ws || ws.readyState !== 1) return;
    wsSend({
        sender: myUsername,
        message: `__LUDO__:${JSON.stringify({
            event, summary: lBuildSummary(),
            pos: ludo.pos, turn: ludo.turn, winner: ludo.winner
        })}`,
        image: null
    });
    appendGameMsg(event);
}

// Quiet broadcast — syncs state but won't trigger AI chat (no notable keywords)
function lBroadcastQuiet(event) {
    if (!ws || ws.readyState !== 1) return;
    wsSend({
        sender: myUsername,
        message: `__LUDO__:${JSON.stringify({
            event: 'move', summary: lBuildSummary(),
            pos: ludo.pos, turn: ludo.turn, winner: ludo.winner
        })}`,
        image: null
    });
    // Still show in local game log
    appendGameMsg(event);
}

function handleLudoSync(data) {
    try {
        const d = JSON.parse(data.message.replace('__LUDO__:', ''));
        appendGameMsg(d.event || 'Ludo update');
    } catch (e) { }
}

// ── Start / Reset ──
function ludoStart() {
    ludo.started = true;
    ludoBuildBoard();
    lRenderTokens();
    lUpdateDots();
    lSetDice(true);
    lSetStat('Your turn — roll the dice!');
    document.getElementById('llog').textContent = 'Game started! You are 🔴 Red.';
}

function ludoReset() {
    clearTimeout(lAiTimer);
    ludo = {
        pos: { red: [-1, -1, -1, -1], green: [-1, -1, -1, -1], blue: [-1, -1, -1, -1] },
        turn: 'red', dice: null, rolled: false, movable: [], winner: null,
        started: !!myUsername
    };
    document.getElementById('ludo-winner').classList.remove('show');
    lDiceEl().textContent = '🎲';
    document.getElementById('llog').textContent = 'New game started!';
    ludoBuildBoard();
    lRenderTokens();
    lUpdateDots();
    if (ludo.started) {
        lSetDice(true);
        lSetStat('Your turn — roll the dice!');
        lBroadcast('New Ludo match started!');
    } else {
        lSetDice(false);
        lSetStat('Join a room to play');
    }
}

// ── Init board on load ──
ludoBuildBoard();
lRenderTokens();
