// ═══════════════════════════════════════════
// CHESS ENGINE – Full rules, minimax depth-2, rendering
// ═══════════════════════════════════════════

const UNIC = {
    wk: '♔', wq: '♕', wr: '♖', wb: '♗', wn: '♘', wp: '♙',
    bk: '♚', bq: '♛', br: '♜', bb: '♝', bn: '♞', bp: '♟'
};

let chess = {};
let cAiRunning = false;

function chessInitState() {
    const b = Array.from({ length: 8 }, () => Array(8).fill(null));
    const back = ['r', 'n', 'b', 'q', 'k', 'b', 'n', 'r'];
    for (let f = 0; f < 8; f++) {
        b[0][f] = { type: back[f], color: 'b' };
        b[1][f] = { type: 'p', color: 'b' };
        b[6][f] = { type: 'p', color: 'w' };
        b[7][f] = { type: back[f], color: 'w' };
    }
    return {
        board: b, turn: 'w', sel: null, legal: [], hist: [],
        castling: { wk: true, wq: true, bk: true, bq: true },
        ep: null, capW: [], capB: [], inCheck: false,
        mate: false, stale: false, started: false, promo: null
    };
}

function chessInit() {
    chess = chessInitState();
    chess.started = true;
    cRender();
    cUpdateSide();
    cSetStat('Your turn (White)');
    document.getElementById('clog').innerHTML = '';
    document.getElementById('cap-w').innerHTML = '';
    document.getElementById('cap-b').innerHTML = '';
}

// ── Rendering ──
function cRender() {
    const bd = document.getElementById('chess-board');
    bd.innerHTML = '';
    for (let r = 0; r < 8; r++) {
        for (let f = 0; f < 8; f++) {
            const cell = document.createElement('div');
            cell.className = 'cc ' + ((r + f) % 2 === 0 ? 'lt' : 'dk');
            cell.dataset.r = r;
            cell.dataset.f = f;

            const p = chess.board[r][f];
            if (p) cell.textContent = UNIC[p.color + p.type];

            if (chess.sel && chess.sel[0] === r && chess.sel[1] === f) cell.classList.add('sel');

            const isL = chess.legal.some(m => m[0] === r && m[1] === f);
            if (isL) {
                if (p && p.color !== chess.turn) cell.classList.add('lc2');
                else cell.classList.add('lm');
            }

            if (chess.inCheck && p && p.type === 'k' && p.color === chess.turn) cell.classList.add('chk');

            cell.addEventListener('click', () => cClick(r, f));
            bd.appendChild(cell);
        }
    }
}

function cUpdateSide() {
    document.getElementById('cslot-w').classList.toggle('aturn', chess.turn === 'w' && !chess.mate && !chess.stale);
    document.getElementById('cslot-b').classList.toggle('aturn', chess.turn === 'b' && !chess.mate && !chess.stale);
    document.getElementById('cap-w').textContent = chess.capW.map(p => UNIC['b' + p]).join('');
    document.getElementById('cap-b').textContent = chess.capB.map(p => UNIC['w' + p]).join('');
}

function cSetStat(msg) { document.getElementById('cstatus').textContent = msg; }

function cAddLog(san, color) {
    const log = document.getElementById('clog');
    const mn = Math.ceil(chess.hist.length / 2);
    if (color === 'w') {
        const row = document.createElement('div');
        row.className = 'cmove-row';
        row.innerHTML = `<span class="cmnum">${mn}.</span><span class="cmw">${san}</span><span class="cmb" id="clb${mn}"></span>`;
        log.appendChild(row);
    } else {
        const sp = document.getElementById(`clb${mn}`);
        if (sp) sp.textContent = san;
        else {
            const row = document.createElement('div');
            row.className = 'cmove-row';
            row.innerHTML = `<span class="cmnum">${mn}.</span><span class="cmw">…</span><span class="cmb">${san}</span>`;
            log.appendChild(row);
        }
    }
    log.scrollTop = log.scrollHeight;
}

// ── Click handler ──
function cClick(r, f) {
    if (!chess.started || chess.turn !== 'w' || chess.mate || chess.stale || cAiRunning || chess.promo) return;

    const p = chess.board[r][f];
    if (chess.sel) {
        const [sr, sf] = chess.sel;
        if (chess.legal.some(m => m[0] === r && m[1] === f)) {
            cExec(sr, sf, r, f, 'w');
            return;
        }
        chess.sel = null;
        chess.legal = [];
        if (p && p.color === 'w') {
            chess.sel = [r, f];
            chess.legal = cGetLegal(r, f, 'w');
        }
    } else {
        if (p && p.color === 'w') {
            chess.sel = [r, f];
            chess.legal = cGetLegal(r, f, 'w');
        }
    }
    cRender();
}

// ── Execute move ──
function cExec(fr, ff, tr, tf, color, silent) {
    const piece = chess.board[fr][ff];
    const target = chess.board[tr][tf];
    const san = cSAN(fr, ff, tr, tf, piece, target, color);

    if (target) {
        color === 'w' ? chess.capW.push(target.type) : chess.capB.push(target.type);
    }

    // En passant capture
    if (piece.type === 'p' && chess.ep && tr === chess.ep[0] && tf === chess.ep[1]) {
        const cr = color === 'w' ? tr + 1 : tr - 1;
        const cp = chess.board[cr][tf];
        if (cp) { color === 'w' ? chess.capW.push(cp.type) : chess.capB.push(cp.type); }
        chess.board[cr][tf] = null;
    }

    chess.board[tr][tf] = { ...piece };
    chess.board[fr][ff] = null;

    // Castling — move rook
    if (piece.type === 'k') {
        if (tf - ff === 2) { chess.board[tr][tf - 1] = { type: 'r', color }; chess.board[tr][7] = null; }
        if (ff - tf === 2) { chess.board[tr][tf + 1] = { type: 'r', color }; chess.board[tr][0] = null; }
        chess.castling[color + 'k'] = false;
        chess.castling[color + 'q'] = false;
    }
    if (piece.type === 'r') {
        if (ff === 0) chess.castling[color + 'q'] = false;
        if (ff === 7) chess.castling[color + 'k'] = false;
    }

    chess.ep = null;
    if (piece.type === 'p' && Math.abs(tr - fr) === 2) chess.ep = [(fr + tr) / 2, ff];

    // Promotion
    if (piece.type === 'p' && (tr === 0 || tr === 7)) {
        if (silent) {
            chess.board[tr][tf] = { type: 'q', color };
        } else {
            chess.promo = { fr, ff, tr, tf, color, san };
            const pm = document.getElementById('promo-modal');
            const pp = document.getElementById('promo-pieces');
            pp.innerHTML = '';
            ['q', 'r', 'b', 'n'].forEach(t => {
                const d = document.createElement('div');
                d.className = 'pp';
                d.textContent = UNIC[color + t];
                d.addEventListener('click', () => chessPromote(t));
                pp.appendChild(d);
            });
            pm.classList.add('show');
            cRender();
            return;
        }
    }

    cFinish(fr, ff, tr, tf, color, san, silent, target);
}

function chessPromote(type) {
    if (!chess.promo) return;
    const { fr, ff, tr, tf, color, san } = chess.promo;
    chess.board[tr][tf] = { type, color };
    chess.promo = null;
    document.getElementById('promo-modal').classList.remove('show');
    cFinish(fr, ff, tr, tf, color, san + '=' + type.toUpperCase(), false, null);
}

function cFinish(fr, ff, tr, tf, color, san, silent, target) {
    chess.hist.push({ fr, ff, tr, tf, san, color });
    chess.sel = null;
    chess.legal = [];

    const nc = color === 'w' ? 'b' : 'w';
    chess.turn = nc;
    chess.inCheck = cIsInCheck(nc);

    const hasL = cHasLegal(nc);
    chess.mate = chess.inCheck && !hasL;
    chess.stale = !chess.inCheck && !hasL;

    const fullSan = san + (chess.mate ? '#' : chess.inCheck ? '+' : '');

    if (!silent) {
        cAddLog(fullSan, color);

        // Determine event notability for AI chat
        const playerName = color === 'w' ? myUsername : 'Groq-AI';

        if (chess.mate) {
            cBroadcast(`Checkmate! ${playerName} wins! 🏆`);
        } else if (chess.stale) {
            cBroadcast(`Stalemate — Draw!`);
        } else if (chess.inCheck) {
            cBroadcast(`${playerName} played ${fullSan} — CHECK!`);
        } else if (target && (target.type === 'q' || target.type === 'r')) {
            // Major piece captured — notable
            cBroadcast(`${playerName} captured ${target.type === 'q' ? 'queen' : 'rook'}! ${fullSan}`);
        } else if (san === 'O-O' || san === 'O-O-O') {
            cBroadcast(`${playerName} castled! ${fullSan}`);
        } else if (san.includes('=')) {
            cBroadcast(`${playerName} promoted a pawn! ${fullSan}`);
        } else {
            // Regular move — quiet broadcast
            cBroadcastQuiet(`${playerName} played ${fullSan}`);
        }

        cRender();
        cUpdateSide();

        if (chess.mate) {
            cSetStat(`Checkmate! ${playerName} wins! 🏆`);
        } else if (chess.stale) {
            cSetStat('Stalemate — Draw!');
        } else if (chess.inCheck) {
            cSetStat(`Check! ${nc === 'w' ? myUsername : "Groq-AI"}'s king is in check`);
            if (nc === 'b') setTimeout(() => cAiMove(), 700);
        } else {
            if (nc === 'w') cSetStat('Your turn (White)');
            else { cSetStat('Groq-AI thinking…'); setTimeout(() => cAiMove(), 700); }
        }
    }
}

// ── Move generation ──
function cGetLegal(r, f, color) {
    return cPseudo(r, f, color).filter(([tr, tf]) => {
        const sb = JSON.parse(JSON.stringify(chess.board));
        const sep = chess.ep;
        chess.board[tr][tf] = { ...chess.board[r][f] };
        chess.board[r][f] = null;
        if (chess.board[tr][tf].type === 'p' && sep && tr === sep[0] && tf === sep[1]) {
            const cr = color === 'w' ? tr + 1 : tr - 1;
            chess.board[cr][tf] = null;
        }
        const ic = cIsInCheck(color);
        chess.board = sb;
        chess.ep = sep;
        return !ic;
    });
}

function cPseudo(r, f, color) {
    const p = chess.board[r][f];
    if (!p || p.color !== color) return [];
    const mv = [], opp = color === 'w' ? 'b' : 'w', dir = color === 'w' ? -1 : 1;

    const ai = (tr, tf) => {
        if (tr < 0 || tr > 7 || tf < 0 || tf > 7) return false;
        const t = chess.board[tr][tf];
        if (t && t.color === color) return false;
        mv.push([tr, tf]);
        return !t;
    };

    const sl = (dr, df) => {
        let tr = r + dr, tf = f + df;
        while (tr >= 0 && tr < 8 && tf >= 0 && tf < 8) {
            const t = chess.board[tr][tf];
            if (t) { if (t.color !== color) mv.push([tr, tf]); break; }
            mv.push([tr, tf]);
            tr += dr; tf += df;
        }
    };

    switch (p.type) {
        case 'p': {
            if (r + dir >= 0 && r + dir < 8 && !chess.board[r + dir][f]) {
                mv.push([r + dir, f]);
                const sr = color === 'w' ? 6 : 1;
                if (r === sr && !chess.board[r + 2 * dir][f]) mv.push([r + 2 * dir, f]);
            }
            for (const df of [-1, 1]) {
                const tr2 = r + dir, tf2 = f + df;
                if (tr2 >= 0 && tr2 < 8 && tf2 >= 0 && tf2 < 8) {
                    const t = chess.board[tr2][tf2];
                    if (t && t.color === opp) mv.push([tr2, tf2]);
                    if (chess.ep && tr2 === chess.ep[0] && tf2 === chess.ep[1]) mv.push([tr2, tf2]);
                }
            }
            break;
        }
        case 'r': sl(1, 0); sl(-1, 0); sl(0, 1); sl(0, -1); break;
        case 'b': sl(1, 1); sl(1, -1); sl(-1, 1); sl(-1, -1); break;
        case 'q': sl(1, 0); sl(-1, 0); sl(0, 1); sl(0, -1); sl(1, 1); sl(1, -1); sl(-1, 1); sl(-1, -1); break;
        case 'n':
            [[2, 1], [2, -1], [-2, 1], [-2, -1], [1, 2], [1, -2], [-1, 2], [-1, -2]]
                .forEach(([dr, df]) => ai(r + dr, f + df));
            break;
        case 'k': {
            [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]]
                .forEach(([dr, df]) => ai(r + dr, f + df));
            const br = color === 'w' ? 7 : 0;
            if (!chess.inCheck && r === br && f === 4) {
                if (chess.castling[color + 'k'] && !chess.board[br][5] && !chess.board[br][6] &&
                    !cAtk(br, 5, opp) && !cAtk(br, 6, opp)) mv.push([br, 6]);
                if (chess.castling[color + 'q'] && !chess.board[br][3] && !chess.board[br][2] && !chess.board[br][1] &&
                    !cAtk(br, 3, opp) && !cAtk(br, 2, opp)) mv.push([br, 2]);
            }
            break;
        }
    }
    return mv;
}

function cIsInCheck(color) {
    let kr = -1, kf = -1;
    for (let r = 0; r < 8; r++) {
        for (let f = 0; f < 8; f++) {
            const p = chess.board[r][f];
            if (p && p.type === 'k' && p.color === color) { kr = r; kf = f; }
        }
    }
    if (kr === -1) return false;
    return cAtk(kr, kf, color === 'w' ? 'b' : 'w');
}

function cAtk(r, f, by) {
    for (let sr = 0; sr < 8; sr++) {
        for (let sf = 0; sf < 8; sf++) {
            const p = chess.board[sr][sf];
            if (!p || p.color !== by) continue;
            if (cPseudo(sr, sf, by).some(([mr, mf]) => mr === r && mf === f)) return true;
        }
    }
    return false;
}

function cHasLegal(color) {
    for (let r = 0; r < 8; r++) {
        for (let f = 0; f < 8; f++) {
            const p = chess.board[r][f];
            if (p && p.color === color && cGetLegal(r, f, color).length > 0) return true;
        }
    }
    return false;
}

function cSAN(fr, ff, tr, tf, piece, target, color) {
    const files = 'abcdefgh', ranks = '87654321';
    if (piece.type === 'k' && Math.abs(tf - ff) === 2) return tf > ff ? 'O-O' : 'O-O-O';
    let s = '';
    if (piece.type !== 'p') s = piece.type.toUpperCase();
    else if (target || (chess.ep && tr === chess.ep[0] && tf === chess.ep[1])) s = files[ff];
    if (target || (piece.type === 'p' && chess.ep && tr === chess.ep[0] && tf === chess.ep[1])) s += 'x';
    return s + files[tf] + ranks[tr];
}

// ── AI (minimax depth 2) ──
const PV = { p: 100, n: 320, b: 330, r: 500, q: 900, k: 20000 };
const PST = {
    p: [[0, 0, 0, 0, 0, 0, 0, 0], [50, 50, 50, 50, 50, 50, 50, 50], [10, 10, 20, 30, 30, 20, 10, 10], [5, 5, 10, 25, 25, 10, 5, 5], [0, 0, 0, 20, 20, 0, 0, 0], [5, -5, -10, 0, 0, -10, -5, 5], [5, 10, 10, -20, -20, 10, 10, 5], [0, 0, 0, 0, 0, 0, 0, 0]],
    n: [[-50, -40, -30, -30, -30, -30, -40, -50], [-40, -20, 0, 0, 0, 0, -20, -40], [-30, 0, 10, 15, 15, 10, 0, -30], [-30, 5, 15, 20, 20, 15, 5, -30], [-30, 0, 15, 20, 20, 15, 0, -30], [-30, 5, 10, 15, 15, 10, 5, -30], [-40, -20, 0, 5, 5, 0, -20, -40], [-50, -40, -30, -30, -30, -30, -40, -50]],
    b: [[-20, -10, -10, -10, -10, -10, -10, -20], [-10, 0, 0, 0, 0, 0, 0, -10], [-10, 0, 5, 10, 10, 5, 0, -10], [-10, 5, 5, 10, 10, 5, 5, -10], [-10, 0, 10, 10, 10, 10, 0, -10], [-10, 10, 10, 10, 10, 10, 10, -10], [-10, 5, 0, 0, 0, 0, 5, -10], [-20, -10, -10, -10, -10, -10, -10, -20]],
    r: [[0, 0, 0, 0, 0, 0, 0, 0], [5, 10, 10, 10, 10, 10, 10, 5], [-5, 0, 0, 0, 0, 0, 0, -5], [-5, 0, 0, 0, 0, 0, 0, -5], [-5, 0, 0, 0, 0, 0, 0, -5], [-5, 0, 0, 0, 0, 0, 0, -5], [-5, 0, 0, 0, 0, 0, 0, -5], [0, 0, 0, 5, 5, 0, 0, 0]],
    q: [[-20, -10, -10, -5, -5, -10, -10, -20], [-10, 0, 0, 0, 0, 0, 0, -10], [-10, 0, 5, 5, 5, 5, 0, -10], [-5, 0, 5, 5, 5, 5, 0, -5], [0, 0, 5, 5, 5, 5, 0, -5], [-10, 5, 5, 5, 5, 5, 0, -10], [-10, 0, 5, 0, 0, 0, 0, -10], [-20, -10, -10, -5, -5, -10, -10, -20]],
    k: [[-30, -40, -40, -50, -50, -40, -40, -30], [-30, -40, -40, -50, -50, -40, -40, -30], [-30, -40, -40, -50, -50, -40, -40, -30], [-30, -40, -40, -50, -50, -40, -40, -30], [-20, -30, -30, -40, -40, -30, -30, -20], [-10, -20, -20, -20, -20, -20, -20, -10], [20, 20, 0, 0, 0, 0, 20, 20], [20, 30, 10, 0, 0, 10, 30, 20]]
};

function cEval() {
    let s = 0;
    for (let r = 0; r < 8; r++) {
        for (let f = 0; f < 8; f++) {
            const p = chess.board[r][f];
            if (!p) continue;
            const pr = p.color === 'w' ? r : 7 - r;
            s += (p.color === 'b' ? 1 : -1) * (PV[p.type] + (PST[p.type]?.[pr]?.[f] || 0));
        }
    }
    return s;
}

function cAiMove() {
    if (chess.turn !== 'b' || chess.mate || chess.stale || !chess.started) return;
    cAiRunning = true;
    setTimeout(() => {
        const move = cMinimax();
        cAiRunning = false;
        if (move) {
            chess.sel = null;
            chess.legal = [];
            cExec(move.fr, move.ff, move.tr, move.tf, 'b');
        }
    }, 350);
}

function cMinimax() {
    let bestS = -Infinity, bestM = null;
    const sBoard = JSON.parse(JSON.stringify(chess.board));
    const sCast = JSON.parse(JSON.stringify(chess.castling));
    const sEP = chess.ep, sTurn = chess.turn, sCheck = chess.inCheck;

    for (let fr = 0; fr < 8; fr++) {
        for (let ff = 0; ff < 8; ff++) {
            const p = chess.board[fr][ff];
            if (!p || p.color !== 'b') continue;
            const moves = cGetLegal(fr, ff, 'b');
            for (const [tr, tf] of moves) {
                const nb = JSON.parse(JSON.stringify(chess.board));
                chess.board = nb;
                const piece = chess.board[fr][ff];
                chess.board[tr][tf] = piece;
                chess.board[fr][ff] = null;
                if (piece.type === 'p' && (tr === 0 || tr === 7)) chess.board[tr][tf] = { type: 'q', color: 'b' };
                chess.turn = 'w';
                chess.inCheck = cIsInCheck('w');
                const score = cAB(1, false, -Infinity, Infinity);
                chess.board = sBoard;
                chess.castling = sCast;
                chess.ep = sEP;
                chess.turn = sTurn;
                chess.inCheck = sCheck;
                if (score > bestS) { bestS = score; bestM = { fr, ff, tr, tf }; }
            }
        }
    }
    return bestM;
}

function cAB(depth, maximizing, alpha, beta) {
    if (depth === 0) return cEval();
    const color = maximizing ? 'b' : 'w';
    let best = maximizing ? -Infinity : Infinity;
    let moved = false;

    const sB = JSON.parse(JSON.stringify(chess.board));
    const sC = JSON.parse(JSON.stringify(chess.castling));
    const sE = chess.ep, sTu = chess.turn, sCh = chess.inCheck;

    outer: for (let fr = 0; fr < 8; fr++) {
        for (let ff = 0; ff < 8; ff++) {
            const p = chess.board[fr][ff];
            if (!p || p.color !== color) continue;
            chess.turn = color;
            chess.inCheck = sCh;
            const ms = cGetLegal(fr, ff, color);
            for (const [tr, tf] of ms) {
                moved = true;
                const nb = JSON.parse(JSON.stringify(sB));
                chess.board = nb;
                const piece = chess.board[fr][ff];
                chess.board[tr][tf] = piece;
                chess.board[fr][ff] = null;
                if (piece.type === 'p' && (tr === 0 || tr === 7)) chess.board[tr][tf] = { type: 'q', color };
                chess.turn = color === 'w' ? 'b' : 'w';
                chess.inCheck = cIsInCheck(chess.turn);
                const sc = cAB(depth - 1, !maximizing, alpha, beta);
                chess.board = sB;
                chess.castling = sC;
                chess.ep = sE;
                chess.turn = sTu;
                chess.inCheck = sCh;
                if (maximizing) { best = Math.max(best, sc); alpha = Math.max(alpha, best); }
                else { best = Math.min(best, sc); beta = Math.min(beta, best); }
                if (beta <= alpha) break outer;
            }
        }
    }
    return moved ? best : (maximizing ? -99999 : 99999);
}

// ── Undo ──
function chessUndo() {
    if (chess.hist.length < 2 || cAiRunning) return;
    const moves = chess.hist.slice(0, -2);
    chess = chessInitState();
    chess.started = true;
    document.getElementById('clog').innerHTML = '';
    for (const m of moves) {
        const piece = chess.board[m.fr][m.ff];
        if (!piece) continue;
        const target = chess.board[m.tr][m.tf];
        if (target) { m.color === 'w' ? chess.capW.push(target.type) : chess.capB.push(target.type); }
        chess.board[m.tr][m.tf] = { ...piece };
        chess.board[m.fr][m.ff] = null;
        if (piece.type === 'k') { chess.castling[m.color + 'k'] = false; chess.castling[m.color + 'q'] = false; }
        if (piece.type === 'r') { if (m.ff === 0) chess.castling[m.color + 'q'] = false; if (m.ff === 7) chess.castling[m.color + 'k'] = false; }
        chess.ep = null;
        if (piece.type === 'p' && Math.abs(m.tr - m.fr) === 2) chess.ep = [(m.fr + m.tr) / 2, m.ff];
        if (piece.type === 'p' && (m.tr === 0 || m.tr === 7)) chess.board[m.tr][m.tf] = { type: 'q', color: m.color };
        chess.hist.push(m);
        chess.turn = m.color === 'w' ? 'b' : 'w';
        cAddLog(m.san, m.color);
    }
    chess.inCheck = cIsInCheck(chess.turn);
    cRender();
    cUpdateSide();
    cSetStat('Your turn (White)');
}

// ── Broadcasting ──
// Notable broadcast — triggers AI
function cBroadcast(event) {
    if (!ws || ws.readyState !== 1) return;
    wsSend({
        sender: myUsername,
        message: `__CHESS__:${JSON.stringify({ event, turn: chess.turn })}`,
        image: null
    });
    appendGameMsg(event);
}

// Quiet broadcast — no AI trigger
function cBroadcastQuiet(event) {
    if (!ws || ws.readyState !== 1) return;
    wsSend({
        sender: myUsername,
        message: `__CHESS__:${JSON.stringify({ event: 'move', turn: chess.turn })}`,
        image: null
    });
    appendGameMsg(event);
}

function handleChessSync(data) {
    try {
        const d = JSON.parse(data.message.replace('__CHESS__:', ''));
        appendGameMsg(d.event || 'Chess update');
    } catch (e) { }
}

function chessReset() {
    cAiRunning = false;
    chessInit();
    cBroadcast('New Chess game started!');
}

// ── Build coords on load ──
(function () {
    const cf = document.getElementById('cfiles');
    if (cf) {
        'abcdefgh'.split('').forEach(f => {
            const s = document.createElement('span');
            s.textContent = f;
            cf.appendChild(s);
        });
    }
    const cr = document.getElementById('cranks');
    if (cr) {
        for (let r = 0; r < 8; r++) {
            const s = document.createElement('span');
            s.textContent = 8 - r;
            cr.appendChild(s);
        }
    }
})();
