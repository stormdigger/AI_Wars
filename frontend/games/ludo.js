/**
 * Squad.GG – Ludo Game Engine
 * Exposed as window.LudoGame
 *
 * Features:
 *  - Full Ludo rules (3 players: red, green, blue)
 *  - Multi-human support (any color can be human or AI)
 *  - Special event detection and callbacks
 *  - Viewport-responsive board rendering
 *  - Token capture, safe cells, home stretch, winning
 */

(function (global) {
  "use strict";

  // ── Constants ──────────────────────────────────────────────

  // 52-cell clockwise main track
  const TRACK = [
    [6,1],[6,2],[6,3],[6,4],[6,5],
    [5,6],[4,6],[3,6],[2,6],[1,6],[0,6],[0,7],
    [0,8],[1,8],[2,8],[3,8],[4,8],[5,8],
    [6,9],[6,10],[6,11],[6,12],[6,13],[6,14],[7,14],
    [8,14],[8,13],[8,12],[8,11],[8,10],[8,9],
    [9,8],[10,8],[11,8],[12,8],[13,8],[14,8],[14,7],
    [14,6],[13,6],[12,6],[11,6],[10,6],[9,6],
    [8,5],[8,4],[8,3],[8,2],[8,1],[8,0],[7,0],
  ];

  // Each color starts at this absolute track index
  const START_ABS  = { red: 0, green: 13, blue: 26 };
  // Entry into home stretch at this absolute track index
  const ENTRY_ABS  = { red: 51, green: 12, blue: 25 };
  // Home stretch coordinates (6 cells, index 0–5)
  const STRETCH = {
    red:   [[7,1],[7,2],[7,3],[7,4],[7,5],[7,6]],
    green: [[1,7],[2,7],[3,7],[4,7],[5,7],[6,7]],
    blue:  [[7,13],[7,12],[7,11],[7,10],[7,9],[7,8]],
  };
  // Home yard positions (4 tokens per color)
  const YARD = {
    red:   [[1,1],[2,1],[1,2],[2,2]],
    green: [[1,12],[2,12],[1,13],[2,13]],
    blue:  [[12,12],[13,12],[12,13],[13,13]],
  };
  // Absolute track indices that are safe (no capture)
  const SAFE_ABS = new Set([0, 8, 13, 21, 26, 34, 39, 47]);

  const COLORS   = ["red", "green", "blue"];
  const DICE_ICO = ["⚀","⚁","⚂","⚃","⚄","⚅"];
  const COLOR_CSS = { red: "tok-r", green: "tok-g", blue: "tok-b" };
  const EMOJI     = { red: "🔴", green: "🟢", blue: "🔵" };
  const PLAYER_NAMES = { red: null, green: "Groq-AI", blue: "Router-AI" }; // null = assigned at init

  // ── State ──────────────────────────────────────────────────

  // Token positions:
  //   -1       = in yard (not on board)
  //   0..51    = relative main track position (0 = starting cell)
  //   100..105 = home stretch (index 0..5)
  //   200      = goal (finished)
  let state = {
    pos:       { red: [-1,-1,-1,-1], green: [-1,-1,-1,-1], blue: [-1,-1,-1,-1] },
    turn:      "red",
    dice:      null,
    rolled:    false,
    movable:   [],
    winner:    null,
    started:   false,
    // Which colors are human (rest are AI)
    humans:    { red: false, green: false, blue: false },
    // Player names
    names:     { red: "You", green: "Groq-AI", blue: "Router-AI" },
    consecutiveSixes: 0,
  };

  let aiTimerHandle = null;
  let callbacks = {
    onSpecialEvent: null,  // fn(eventType, detail, summary)
    onMove:         null,  // fn(summary) — for every move
    onGameEnd:      null,  // fn(winner)
    onLog:          null,  // fn(msg)
  };

  // ── Public API ─────────────────────────────────────────────

  const LudoGame = {

    /** Initialize with player config and callbacks. */
    init(config = {}) {
      if (config.names) Object.assign(state.names, config.names);
      if (config.humans) Object.assign(state.humans, config.humans);
      if (config.callbacks) Object.assign(callbacks, config.callbacks);
      state.names.red = config.names?.red || "You";
      this.reset();
    },

    reset() {
      clearTimeout(aiTimerHandle);
      state.pos    = { red: [-1,-1,-1,-1], green: [-1,-1,-1,-1], blue: [-1,-1,-1,-1] };
      state.turn   = "red";
      state.dice   = null;
      state.rolled = false;
      state.movable = [];
      state.winner  = null;
      state.started = true;
      state.consecutiveSixes = 0;

      buildBoard();
      renderTokens();
      updateControls();
      log("Game started! You are 🔴 Red.");
      fireMove("game_reset", "New Ludo game started");
    },

    /** Call when player rolls dice (human's turn). */
    rollDice() {
      if (state.turn !== "red" || state.rolled || state.winner || !state.started) return;
      if (!state.humans.red) return;
      const val = Math.floor(Math.random() * 6) + 1;
      processRoll("red", val);
    },

    /** Call when human clicks a token to move it. */
    moveToken(tokenIdx) {
      if (!state.movable.includes(tokenIdx)) return;
      if (!state.humans[state.turn]) return;
      if (!state.rolled) return;
      doMove(state.turn, tokenIdx, state.dice);
    },

    /** Return current game state (for broadcasting). */
    getState() {
      return {
        pos:     JSON.parse(JSON.stringify(state.pos)),
        turn:    state.turn,
        winner:  state.winner,
        summary: buildSummary(),
      };
    },

    /** Sync state from another player's broadcast. */
    syncState(remoteState) {
      if (!remoteState) return;
      state.pos    = remoteState.pos;
      state.turn   = remoteState.turn;
      state.winner = remoteState.winner;
      renderTokens();
      updateControls();
      if (state.winner) showWinner(state.winner);
    },

    isMyTurn() { return state.turn === "red" && state.humans.red; },
    getNames()  { return { ...state.names }; },
  };

  // ── Board Build ────────────────────────────────────────────

  function buildBoard() {
    const bd = document.getElementById("ludo-board");
    if (!bd) return;
    bd.innerHTML = "";

    for (let r = 0; r < 15; r++) {
      for (let c = 0; c < 15; c++) {
        const el = document.createElement("div");
        el.className = "lc";
        el.id = `lc${r}-${c}`;
        bd.appendChild(el);
      }
    }

    // Home zones
    zone(0,0,5,5, "lhr");
    zone(0,9,5,14,"lhg");
    zone(9,9,14,14,"lhb");
    zone(9,0,14,5, "lhy");

    // Inner circles inside home yards
    [[1,1],[1,2],[2,1],[2,2]].forEach(([r,c]) => cell(r,c)?.classList.add("lcircle"));
    [[1,12],[1,13],[2,12],[2,13]].forEach(([r,c]) => cell(r,c)?.classList.add("lcircle"));
    [[12,12],[12,13],[13,12],[13,13]].forEach(([r,c]) => cell(r,c)?.classList.add("lcircle"));

    // Path cells
    TRACK.forEach(([r,c]) => cell(r,c)?.classList.add("lpath"));

    // Colored rows/columns on the main path
    for (let i = 1; i <= 5; i++) cell(6,i)?.classList.add("lpr");  // red row
    for (let i = 1; i <= 5; i++) cell(i,8)?.classList.add("lpg");  // green col
    for (let i = 9; i <=13; i++) cell(8,i)?.classList.add("lpb");  // blue row

    // Home stretch lanes
    STRETCH.red.forEach(([r,c])   => cell(r,c)?.classList.add("lsr"));
    STRETCH.green.forEach(([r,c]) => cell(r,c)?.classList.add("lsg"));
    STRETCH.blue.forEach(([r,c])  => cell(r,c)?.classList.add("lsb"));

    // Safe stars
    SAFE_ABS.forEach(i => { const [r,c] = TRACK[i]; cell(r,c)?.classList.add("lsafe"); });

    // Center
    cell(7,7)?.classList.add("lcenter");

    // Start cells get colored tint
    cell(6,1)?.classList.add("lpr");
    cell(0,8)?.classList.add("lpg");
    cell(8,13)?.classList.add("lpb");
  }

  function cell(r, c) { return document.getElementById(`lc${r}-${c}`); }
  function zone(r1,c1,r2,c2,cls) {
    for (let r=r1;r<=r2;r++) for (let c=c1;c<=c2;c++) cell(r,c)?.classList.add(cls);
  }

  // ── Token Rendering ────────────────────────────────────────

  function renderTokens() {
    document.querySelectorAll(".tok").forEach(t => t.remove());
    const occupancy = {};

    for (const color of COLORS) {
      for (let ti = 0; ti < 4; ti++) {
        const pos = state.pos[color][ti];
        const coord = pos === -1 ? YARD[color][ti] : getCoord(color, pos);
        if (!coord) continue;

        const [r, c] = coord;
        const key = `${r}-${c}`;
        const idx = occupancy[key] || 0;
        occupancy[key] = idx + 1;

        const cellEl = cell(r, c);
        if (!cellEl) continue;

        const tok = document.createElement("div");
        tok.className = `tok ${COLOR_CSS[color]}`;
        tok.textContent = ti + 1;

        // Stacking offsets for multiple tokens on same cell
        const size = parseFloat(getComputedStyle(document.documentElement)
                     .getPropertyValue("--ludo-cell")) || 30;
        const half = size / 2;
        const offsets = [[-6,-6],[6,-6],[-6,6],[6,6]];
        const [ox,oy] = idx < 4 ? offsets[idx] : [0,0];
        tok.style.left = `${half - size*0.3 + ox}px`;
        tok.style.top  = `${half - size*0.3 + oy}px`;

        // Highlight movable tokens
        if (state.turn === color && state.rolled && state.movable.includes(ti)) {
          tok.classList.add("mov");
        }

        // Click handler for human-controlled color
        if (state.humans[color] && state.turn === color && state.rolled && state.movable.includes(ti)) {
          tok.style.cursor = "pointer";
          tok.addEventListener("click", () => LudoGame.moveToken(ti));
        }

        cellEl.appendChild(tok);
      }
    }
  }

  // ── Move Logic ─────────────────────────────────────────────

  function absIdx(color, rel) { return (START_ABS[color] + rel) % 52; }

  function getCoord(color, pos) {
    if (pos === 200) return [7, 7];
    if (pos >= 100)  return STRETCH[color][pos - 100];
    return TRACK[absIdx(color, pos)];
  }

  function canMove(color, ti, dice) {
    const pos = state.pos[color][ti];
    if (pos === 200) return false;
    if (pos === -1)  return dice === 6;
    if (pos >= 100)  return (pos - 100 + dice) <= 5;

    const abs  = absIdx(color, pos);
    const ea   = ENTRY_ABS[color];
    const toEntry = ea >= abs ? ea - abs : 52 - abs + ea;
    if (dice <= toEntry) return true;
    return (dice - toEntry - 1) <= 5;
  }

  function applyMove(color, ti, dice) {
    let pos = state.pos[color][ti];

    if (pos === -1) {
      state.pos[color][ti] = 0;
      checkCapture(color, ti, "token_enter");
      return;
    }

    if (pos >= 100) {
      const np = pos + dice;
      state.pos[color][ti] = (np - 100 >= 5) ? 200 : np;
      return;
    }

    const abs  = absIdx(color, pos);
    const ea   = ENTRY_ABS[color];
    const toEntry = ea >= abs ? ea - abs : 52 - abs + ea;

    if (dice <= toEntry) {
      state.pos[color][ti] = pos + dice;
      checkCapture(color, ti, "move");
    } else {
      const hIdx = dice - toEntry - 1;
      state.pos[color][ti] = hIdx >= 5 ? 200 : 100 + hIdx;
    }
  }

  function checkCapture(color, ti, baseEvent) {
    const pos = state.pos[color][ti];
    if (pos < 0 || pos >= 100) return;

    const abs = absIdx(color, pos);
    if (SAFE_ABS.has(abs)) return;

    for (const oc of COLORS) {
      if (oc === color) continue;
      for (let t = 0; t < 4; t++) {
        const op = state.pos[oc][t];
        if (op >= 0 && op < 100 && absIdx(oc, op) === abs) {
          // Was the captured token close to winning? That's a comeback
          const captured_progress = op;
          state.pos[oc][t] = -1;
          log(`💥 ${EMOJI[color]} captured ${EMOJI[oc]} token ${t+1}!`);

          const detail = `${state.names[color]} captured ${state.names[oc]}'s token!`;
          fireSpecial("capture", detail);
          if (captured_progress >= 40) fireSpecial("comeback", `${state.names[oc]}'s token was near home and got captured!`);
        }
      }
    }
  }

  function getMovable(color, dice) {
    return [0,1,2,3].filter(i => canMove(color, i, dice));
  }

  function checkWin(color) {
    return state.pos[color].every(p => p === 200);
  }

  function isInHomeStretch(color) {
    return state.pos[color].some(p => p >= 100 && p < 200);
  }

  function allInHomeStretch(color) {
    return state.pos[color].every(p => p === -1 || p === 200 || (p >= 100 && p < 200));
  }

  // ── Game Flow ──────────────────────────────────────────────

  function processRoll(color, val) {
    state.dice   = val;
    state.rolled = true;

    // Animate dice
    const diceEl = document.getElementById("ludo-dice");
    if (diceEl) {
      diceEl.classList.add("rolling");
      diceEl.textContent = DICE_ICO[val - 1];
      setTimeout(() => diceEl.classList.remove("rolling"), 400);
    }

    log(`🎲 ${state.names[color]} rolled ${val}`);

    // Three sixes in a row → forfeit turn
    if (val === 6) {
      state.consecutiveSixes++;
      if (state.consecutiveSixes >= 3) {
        log(`⚠️ Three 6s in a row — turn forfeited!`);
        state.consecutiveSixes = 0;
        nextTurn(false);
        return;
      }
      fireSpecial("dice_six", `${state.names[color]} rolled a SIX!`);
    } else {
      state.consecutiveSixes = 0;
    }

    const movable = getMovable(color, val);
    state.movable = movable;

    if (!movable.length) {
      log(`${state.names[color]}: no moves available`);
      fireMove("no_moves", `${state.names[color]} rolled ${val} but has no moves`);
      setTimeout(() => nextTurn(false), 1200);
      return;
    }

    // Auto-move if only one option
    if (movable.length === 1) {
      setTimeout(() => doMove(color, movable[0], val), 550);
    } else {
      setGameStatus("Click a glowing token!");
      renderTokens();
    }

    updateControls();
  }

  function doMove(color, ti, dice) {
    if (!state.movable.includes(ti)) return;

    const oldPos = state.pos[color][ti];
    applyMove(color, ti, dice);
    const newPos = state.pos[color][ti];

    log(`${EMOJI[color]} ${state.names[color]} moved token ${ti+1}: ${descPos(color, oldPos)} → ${descPos(color, newPos)}`);

    // Detect special events post-move
    if (newPos >= 100 && newPos < 200 && oldPos < 100) {
      fireSpecial("home_stretch", `${state.names[color]}'s token entered the home stretch!`);
    }
    if (allInHomeStretch(color)) {
      fireSpecial("near_win", `${state.names[color]} has all tokens in home stretch!`);
    }

    // Broadcast move
    fireMove(dice === 6 ? "dice_six_move" : "move",
      `${state.names[color]} moved token ${ti+1}`);

    // Check win
    if (checkWin(color)) {
      state.winner = color;
      showWinner(color);
      fireSpecial("win", `${state.names[color]} WINS!`);
      if (callbacks.onGameEnd) callbacks.onGameEnd(color, state.names[color]);
      return;
    }

    state.rolled  = false;
    state.movable = [];
    nextTurn(dice === 6);
  }

  function nextTurn(extraTurn) {
    state.dice   = null;
    const diceEl = document.getElementById("ludo-dice");
    if (diceEl) diceEl.textContent = "🎲";

    if (!extraTurn) {
      const idx  = COLORS.indexOf(state.turn);
      state.turn = COLORS[(idx + 1) % COLORS.length];
      state.consecutiveSixes = 0;
    }

    state.rolled  = false;
    state.movable = [];

    renderTokens();
    updateControls();

    const name = state.names[state.turn];
    setGameStatus(`${name}'s turn`);

    // Trigger AI if it's an AI turn
    if (!state.humans[state.turn] && !state.winner) {
      clearTimeout(aiTimerHandle);
      aiTimerHandle = setTimeout(() => aiTakeTurn(state.turn), 1900);
    }
  }

  // ── AI Logic ──────────────────────────────────────────────

  function aiTakeTurn(color) {
    if (state.turn !== color || state.winner || !state.started) return;

    const val = Math.floor(Math.random() * 6) + 1;
    processRoll(color, val);

    // For AI, auto-select best move after a short delay
    if (state.movable.length > 0) {
      const chosen = aiChoose(color, state.movable, val);
      setTimeout(() => {
        // Temporarily allow any color to move (AI moves aren't human-gated)
        const oldHumans = state.humans[color];
        state.humans[color] = true; // temporarily
        doMove(color, chosen, val);
        state.humans[color] = oldHumans;
      }, 1300);
    }
  }

  function aiChoose(color, movable, dice) {
    let best = movable[0], bestScore = -Infinity;
    for (const ti of movable) {
      const pos = state.pos[color][ti];
      let score = 0;

      if (pos === -1) score = 65; // enter board
      else if (pos >= 100) score = 210 + pos; // advance in home stretch
      else {
        // Check if this move would capture
        const newRel = pos + dice;
        if (newRel < 52) {
          const newAbs = absIdx(color, newRel);
          if (!SAFE_ABS.has(newAbs)) {
            for (const oc of COLORS) {
              if (oc === color) continue;
              for (let t = 0; t < 4; t++) {
                const op = state.pos[oc][t];
                if (op >= 0 && op < 100 && absIdx(oc, op) === newAbs) {
                  score = Math.max(score, 190); // capture is high value
                }
              }
            }
          }
        }
        if (score < 190) score = Math.max(score, pos + dice); // advance
      }

      if (score > bestScore) { bestScore = score; best = ti; }
    }
    return best;
  }

  // ── UI Helpers ─────────────────────────────────────────────

  function updateControls() {
    const diceEl    = document.getElementById("ludo-dice");
    const dotsEl    = document.getElementById("turn-dots");
    const statusEl  = document.getElementById("game-status");

    // Dice: only enabled for human's turn, not yet rolled
    if (diceEl) {
      const canRoll = state.humans[state.turn] && !state.rolled && !state.winner && state.started;
      diceEl.classList.toggle("off", !canRoll);
    }

    // Turn dots
    if (dotsEl) {
      dotsEl.innerHTML = "";
      for (const c of COLORS) {
        const d = document.createElement("div");
        d.className = "t-dot" + (state.turn === c ? " active" : "");
        d.style.background = c === "red" ? "var(--red)" : c === "green" ? "var(--green)" : "var(--blue)";
        d.title = state.names[c];
        dotsEl.appendChild(d);
      }
    }

    if (statusEl && !state.winner) {
      const name = state.names[state.turn];
      statusEl.textContent = state.humans[state.turn]
        ? (state.rolled ? "Pick a token!" : `${name} — Roll the dice!`)
        : `${name} is thinking…`;
    }
  }

  function setGameStatus(msg) {
    const el = document.getElementById("game-status");
    if (el) el.textContent = msg;
  }

  function showWinner(color) {
    const ov = document.getElementById("ludo-winner");
    if (!ov) return;
    document.getElementById("ludo-winner-emoji").textContent = EMOJI[color];
    document.getElementById("ludo-winner-text").textContent  = `${state.names[color]} WINS!`;
    ov.classList.add("show");
    updateControls();
  }

  function log(msg) {
    const el = document.getElementById("game-log");
    if (el) { el.innerHTML += msg + "<br>"; el.scrollTop = el.scrollHeight; }
    if (callbacks.onLog) callbacks.onLog(msg);
  }

  function descPos(color, pos) {
    if (pos === -1)  return "yard";
    if (pos === 200) return "🏠goal";
    if (pos >= 100)  return `hs${pos - 100 + 1}`;
    return `t${pos}`;
  }

  function buildSummary() {
    return COLORS.map(c =>
      `${c}:[${state.pos[c].map((p) => descPos(c, p)).join(",")}]`
    ).join(" ");
  }

  // ── Event Callbacks ────────────────────────────────────────

  function fireSpecial(eventType, detail) {
    if (callbacks.onSpecialEvent) {
      callbacks.onSpecialEvent(eventType, detail, buildSummary());
    }
  }

  function fireMove(eventType, detail) {
    if (callbacks.onMove) {
      callbacks.onMove(eventType, detail, buildSummary(), LudoGame.getState());
    }
  }

  // ── Exports ────────────────────────────────────────────────
  global.LudoGame = LudoGame;

})(window);