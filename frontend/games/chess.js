/**
 * Squad.GG – Chess Game Engine
 * Exposed as window.ChessGame
 *
 * Features:
 *  - Complete chess rules: all piece moves, castling, en passant, promotion
 *  - Check, checkmate, stalemate detection
 *  - Minimax depth-2 with alpha-beta pruning + piece-square tables
 *  - Special event detection (check, capture queen, promotion, castling, etc.)
 *  - Viewport-responsive board rendering
 *  - Undo support (replays from history)
 */

(function (global) {
  "use strict";

  // ── Unicode pieces ──────────────────────────────────────────
  const U = {
    wk:"♔",wq:"♕",wr:"♖",wb:"♗",wn:"♘",wp:"♙",
    bk:"♚",bq:"♛",br:"♜",bb:"♝",bn:"♞",bp:"♟",
  };

  // ── Piece values + piece-square tables ──────────────────────
  const PV = { p:100, n:320, b:330, r:500, q:900, k:20000 };
  const PST = {
    p: [[0,0,0,0,0,0,0,0],[50,50,50,50,50,50,50,50],[10,10,20,30,30,20,10,10],[5,5,10,25,25,10,5,5],[0,0,0,20,20,0,0,0],[5,-5,-10,0,0,-10,-5,5],[5,10,10,-20,-20,10,10,5],[0,0,0,0,0,0,0,0]],
    n: [[-50,-40,-30,-30,-30,-30,-40,-50],[-40,-20,0,0,0,0,-20,-40],[-30,0,10,15,15,10,0,-30],[-30,5,15,20,20,15,5,-30],[-30,0,15,20,20,15,0,-30],[-30,5,10,15,15,10,5,-30],[-40,-20,0,5,5,0,-20,-40],[-50,-40,-30,-30,-30,-30,-40,-50]],
    b: [[-20,-10,-10,-10,-10,-10,-10,-20],[-10,0,0,0,0,0,0,-10],[-10,0,5,10,10,5,0,-10],[-10,5,5,10,10,5,5,-10],[-10,0,10,10,10,10,0,-10],[-10,10,10,10,10,10,10,-10],[-10,5,0,0,0,0,5,-10],[-20,-10,-10,-10,-10,-10,-10,-20]],
    r: [[0,0,0,0,0,0,0,0],[5,10,10,10,10,10,10,5],[-5,0,0,0,0,0,0,-5],[-5,0,0,0,0,0,0,-5],[-5,0,0,0,0,0,0,-5],[-5,0,0,0,0,0,0,-5],[-5,0,0,0,0,0,0,-5],[0,0,0,5,5,0,0,0]],
    q: [[-20,-10,-10,-5,-5,-10,-10,-20],[-10,0,0,0,0,0,0,-10],[-10,0,5,5,5,5,0,-10],[-5,0,5,5,5,5,0,-5],[0,0,5,5,5,5,0,-5],[-10,5,5,5,5,5,0,-10],[-10,0,5,0,0,0,0,-10],[-20,-10,-10,-5,-5,-10,-10,-20]],
    k: [[-30,-40,-40,-50,-50,-40,-40,-30],[-30,-40,-40,-50,-50,-40,-40,-30],[-30,-40,-40,-50,-50,-40,-40,-30],[-30,-40,-40,-50,-50,-40,-40,-30],[-20,-30,-30,-40,-40,-30,-30,-20],[-10,-20,-20,-20,-20,-20,-20,-10],[20,20,0,0,0,0,20,20],[20,30,10,0,0,10,30,20]],
  };

  // ── State ───────────────────────────────────────────────────
  let g = {};  // game state (rebuilt by initState)
  let aiRunning = false;

  let callbacks = {
    onSpecialEvent: null,
    onMove:         null,
    onGameEnd:      null,
    onLog:          null,
  };

  let playerNames = { w: "You", b: "Groq-AI" };
  let humanColors = { w: true, b: false };  // w=human, b=AI by default

  function initState() {
    const board = Array.from({length:8}, () => Array(8).fill(null));
    const back = ["r","n","b","q","k","b","n","r"];
    for (let f = 0; f < 8; f++) {
      board[0][f] = { type: back[f], color: "b" };
      board[1][f] = { type: "p",    color: "b" };
      board[6][f] = { type: "p",    color: "w" };
      board[7][f] = { type: back[f], color: "w" };
    }
    return {
      board,
      turn:     "w",
      sel:      null,   // [r,f]
      legal:    [],     // [[r,f]…]
      hist:     [],     // move history
      castling: { wk:true, wq:true, bk:true, bq:true },
      ep:       null,   // en-passant square [r,f]
      capW:     [],     // piece types captured by white
      capB:     [],     // piece types captured by black
      inCheck:  false,
      mate:     false,
      stale:    false,
      started:  false,
      promo:    null,   // pending promotion {fr,ff,tr,tf,color,san}
    };
  }

  // ── Public API ──────────────────────────────────────────────
  const ChessGame = {

    init(config = {}) {
      if (config.names)  Object.assign(playerNames, config.names);
      if (config.humans) Object.assign(humanColors, config.humans);
      if (config.callbacks) Object.assign(callbacks, config.callbacks);
      document.getElementById("cname-w").textContent = playerNames.w + " (White)";
      this.reset();
    },

    reset() {
      aiRunning = false;
      g = initState();
      g.started = true;
      renderBoard();
      updateSide();
      setStatus("Your turn (White)");
      document.getElementById("c-log").innerHTML = "";
      document.getElementById("cap-w").textContent = "";
      document.getElementById("cap-b").textContent = "";
    },

    /** Called when user clicks a board cell. */
    handleClick(r, f) {
      if (!g.started || g.turn !== "w" || !humanColors.w) return;
      if (g.mate || g.stale || aiRunning || g.promo) return;

      const piece = g.board[r][f];

      if (g.sel) {
        const [sr, sf] = g.sel;
        if (g.legal.some(m => m[0]===r && m[1]===f)) {
          execMove(sr, sf, r, f, "w");
          return;
        }
        g.sel = null; g.legal = [];
        if (piece && piece.color === "w") {
          g.sel = [r,f]; g.legal = getLegal(r, f, "w");
        }
      } else {
        if (piece && piece.color === "w") {
          g.sel = [r,f]; g.legal = getLegal(r, f, "w");
        }
      }
      renderBoard();
    },

    /** Called from promo modal. */
    promote(type) {
      if (!g.promo) return;
      const { fr, ff, tr, tf, color, san } = g.promo;
      g.board[tr][tf] = { type, color };
      g.promo = null;
      document.getElementById("promo-modal").classList.remove("show");
      fireSpecial("promotion", `Pawn promoted to ${type.toUpperCase()}!`);
      finishMove(fr, ff, tr, tf, color, san + "=" + type.toUpperCase());
    },

    undo() {
      if (g.hist.length < 2 || aiRunning) return;
      const moves = g.hist.slice(0, -2);
      ChessGame.reset();
      document.getElementById("c-log").innerHTML = "";
      for (const m of moves) {
        execMoveSilent(m.fr, m.ff, m.tr, m.tf, m.color, m.promoType);
        addLog(m.san, m.color);
      }
      renderBoard(); updateSide(); setStatus("Your turn (White)");
    },

    getState() {
      const last = g.hist[g.hist.length - 1];
      return {
        turn:    g.turn,
        inCheck: g.inCheck,
        mate:    g.mate,
        stale:   g.stale,
        capW:    [...g.capW],
        capB:    [...g.capB],
        lastMove: last ? last.san : "",
        moveCount: g.hist.length,
      };
    },
  };

  // ── Move Execution ──────────────────────────────────────────

  function execMove(fr, ff, tr, tf, color) {
    const piece  = g.board[fr][ff];
    const target = g.board[tr][tf];
    const san    = makeSAN(fr, ff, tr, tf, piece, target, color);

    // Capture
    if (target) {
      color === "w" ? g.capW.push(target.type) : g.capB.push(target.type);
      if (target.type === "q") fireSpecial("queen_capture", `${playerNames[color]} captured the queen!`);
      else if (target.type === "r") fireSpecial("rook_capture", `${playerNames[color]} captured a rook!`);
    }

    // En-passant capture
    if (piece.type === "p" && g.ep && tr === g.ep[0] && tf === g.ep[1]) {
      const cr = color === "w" ? tr + 1 : tr - 1;
      const cp = g.board[cr][tf];
      if (cp) color === "w" ? g.capW.push(cp.type) : g.capB.push(cp.type);
      g.board[cr][tf] = null;
    }

    g.board[tr][tf] = { ...piece };
    g.board[fr][ff] = null;

    // Castling: move rook
    if (piece.type === "k") {
      if (tf - ff === 2) { g.board[tr][tf-1] = {type:"r",color}; g.board[tr][7] = null; fireSpecial("castling","Kingside castle!"); }
      if (ff - tf === 2) { g.board[tr][tf+1] = {type:"r",color}; g.board[tr][0] = null; fireSpecial("castling","Queenside castle!"); }
      g.castling[color+"k"] = false; g.castling[color+"q"] = false;
    }
    if (piece.type === "r") {
      if (ff === 0) g.castling[color+"q"] = false;
      if (ff === 7) g.castling[color+"k"] = false;
    }

    // En-passant square
    g.ep = (piece.type === "p" && Math.abs(tr - fr) === 2) ? [(fr+tr)/2, ff] : null;

    // Promotion
    if (piece.type === "p" && (tr === 0 || tr === 7)) {
      g.promo = { fr, ff, tr, tf, color, san };
      // Show promo modal with correct colored pieces
      const modal    = document.getElementById("promo-modal");
      const piecesEl = document.getElementById("promo-pieces");
      piecesEl.innerHTML = "";
      for (const t of ["q","r","b","n"]) {
        const d = document.createElement("div");
        d.className = "pp";
        d.textContent = U[color + t];
        d.addEventListener("click", () => ChessGame.promote(t));
        piecesEl.appendChild(d);
      }
      modal.classList.add("show");
      renderBoard();
      return;
    }

    finishMove(fr, ff, tr, tf, color, san);
  }

  function finishMove(fr, ff, tr, tf, color, san) {
    g.hist.push({ fr, ff, tr, tf, san, color });
    g.sel = null; g.legal = [];

    const nc     = color === "w" ? "b" : "w";
    g.turn       = nc;
    g.inCheck    = isInCheck(nc);
    const hasLeg = hasAnyLegal(nc);
    g.mate       = g.inCheck && !hasLeg;
    g.stale      = !g.inCheck && !hasLeg;

    const fullSan = san + (g.mate ? "#" : g.inCheck ? "+" : "");
    addLog(fullSan, color);

    if (callbacks.onMove) {
      callbacks.onMove("move", `${playerNames[color]} played ${fullSan}`, ChessGame.getState());
    }

    renderBoard(); updateSide();

    if (g.mate) {
      setStatus(`Checkmate! ${playerNames[color]} wins! 🏆`);
      fireSpecial("checkmate", `${playerNames[color]} wins by checkmate!`);
      if (callbacks.onGameEnd) callbacks.onGameEnd(color, playerNames[color]);
    } else if (g.stale) {
      setStatus("Stalemate — Draw!");
      fireSpecial("stalemate", "The game is a draw by stalemate!");
      if (callbacks.onGameEnd) callbacks.onGameEnd("draw", "Draw");
    } else if (g.inCheck) {
      setStatus(`Check! ${playerNames[nc]}'s king is in check`);
      fireSpecial("check", `${playerNames[color]} put ${playerNames[nc]}'s king in check!`);
      if (!humanColors[nc]) setTimeout(() => aiMove(), 700);
    } else {
      setStatus(humanColors[nc] ? `${playerNames[nc]}'s turn` : "Groq-AI thinking…");
      if (!humanColors[nc]) setTimeout(() => aiMove(), 700);
    }
  }

  function execMoveSilent(fr, ff, tr, tf, color, promoType) {
    const piece  = g.board[fr][ff]; if (!piece) return;
    const target = g.board[tr][tf];
    if (target) color === "w" ? g.capW.push(target.type) : g.capB.push(target.type);
    if (piece.type==="p" && g.ep && tr===g.ep[0] && tf===g.ep[1]) {
      const cr = color==="w"?tr+1:tr-1; const cp=g.board[cr][tf];
      if (cp) color==="w"?g.capW.push(cp.type):g.capB.push(cp.type);
      g.board[cr][tf]=null;
    }
    g.board[tr][tf]={...piece}; g.board[fr][ff]=null;
    if (piece.type==="k"){
      if(tf-ff===2){g.board[tr][tf-1]={type:"r",color};g.board[tr][7]=null;}
      if(ff-tf===2){g.board[tr][tf+1]={type:"r",color};g.board[tr][0]=null;}
      g.castling[color+"k"]=false; g.castling[color+"q"]=false;
    }
    if(piece.type==="r"){ if(ff===0)g.castling[color+"q"]=false; if(ff===7)g.castling[color+"k"]=false; }
    g.ep = (piece.type==="p"&&Math.abs(tr-fr)===2)?[(fr+tr)/2,ff]:null;
    if (piece.type==="p"&&(tr===0||tr===7)) g.board[tr][tf]={type:promoType||"q",color};
    g.hist.push({fr,ff,tr,tf,san:"",color,promoType});
    g.turn = color==="w"?"b":"w";
    g.inCheck = isInCheck(g.turn);
  }

  // ── Move Generation ─────────────────────────────────────────

  function getLegal(r, f, color) {
    return getPseudo(r, f, color).filter(([tr, tf]) => {
      const sb  = g.board.map(row => [...row]);
      const sep = g.ep;
      g.board[tr][tf] = { ...g.board[r][f] };
      g.board[r][f]   = null;
      if (g.board[tr][tf].type==="p"&&sep&&tr===sep[0]&&tf===sep[1]) {
        g.board[color==="w"?tr+1:tr-1][tf] = null;
      }
      const ic = isInCheck(color);
      g.board = sb; g.ep = sep;
      return !ic;
    });
  }

  function getPseudo(r, f, color) {
    const p = g.board[r][f]; if (!p || p.color !== color) return [];
    const mv = [], opp = color==="w"?"b":"w", dir = color==="w"?-1:1;

    const add = (tr, tf) => {
      if (tr<0||tr>7||tf<0||tf>7) return false;
      const t = g.board[tr][tf]; if (t&&t.color===color) return false;
      mv.push([tr,tf]); return !t;
    };
    const slide = (dr, df) => {
      let tr=r+dr, tf=f+df;
      while (tr>=0&&tr<8&&tf>=0&&tf<8) {
        const t=g.board[tr][tf]; if(t){if(t.color!==color)mv.push([tr,tf]);break;}
        mv.push([tr,tf]); tr+=dr; tf+=df;
      }
    };

    switch (p.type) {
      case "p": {
        if (r+dir>=0&&r+dir<=7&&!g.board[r+dir][f]) {
          mv.push([r+dir,f]);
          const sr = color==="w"?6:1;
          if (r===sr&&!g.board[r+2*dir][f]) mv.push([r+2*dir,f]);
        }
        for (const df of [-1,1]) {
          const tr2=r+dir, tf2=f+df;
          if (tr2>=0&&tr2<=7&&tf2>=0&&tf2<=7) {
            const t=g.board[tr2][tf2];
            if (t&&t.color===opp) mv.push([tr2,tf2]);
            if (g.ep&&tr2===g.ep[0]&&tf2===g.ep[1]) mv.push([tr2,tf2]);
          }
        }
        break;
      }
      case "r": slide(1,0);slide(-1,0);slide(0,1);slide(0,-1); break;
      case "b": slide(1,1);slide(1,-1);slide(-1,1);slide(-1,-1); break;
      case "q": slide(1,0);slide(-1,0);slide(0,1);slide(0,-1);slide(1,1);slide(1,-1);slide(-1,1);slide(-1,-1); break;
      case "n": for (const [dr,df] of [[2,1],[2,-1],[-2,1],[-2,-1],[1,2],[1,-2],[-1,2],[-1,-2]]) add(r+dr,f+df); break;
      case "k": {
        for (const [dr,df] of [[1,0],[-1,0],[0,1],[0,-1],[1,1],[1,-1],[-1,1],[-1,-1]]) add(r+dr,f+df);
        const br = color==="w"?7:0;
        if (!g.inCheck&&r===br&&f===4) {
          if (g.castling[color+"k"]&&!g.board[br][5]&&!g.board[br][6]&&!isAttacked(br,5,opp)&&!isAttacked(br,6,opp)) mv.push([br,6]);
          if (g.castling[color+"q"]&&!g.board[br][3]&&!g.board[br][2]&&!g.board[br][1]&&!isAttacked(br,3,opp)&&!isAttacked(br,2,opp)) mv.push([br,2]);
        }
        break;
      }
    }
    return mv;
  }

  function isInCheck(color) {
    let kr=-1, kf=-1;
    for (let r=0;r<8;r++) for (let f=0;f<8;f++) { const p=g.board[r][f]; if(p&&p.type==="k"&&p.color===color){kr=r;kf=f;} }
    if (kr===-1) return false;
    return isAttacked(kr, kf, color==="w"?"b":"w");
  }

  function isAttacked(r, f, byColor) {
    for (let sr=0;sr<8;sr++) for (let sf=0;sf<8;sf++) {
      const p=g.board[sr][sf]; if(!p||p.color!==byColor) continue;
      if (getPseudo(sr,sf,byColor).some(([mr,mf])=>mr===r&&mf===f)) return true;
    }
    return false;
  }

  function hasAnyLegal(color) {
    for (let r=0;r<8;r++) for (let f=0;f<8;f++) {
      const p=g.board[r][f]; if(p&&p.color===color&&getLegal(r,f,color).length>0) return true;
    }
    return false;
  }

  function makeSAN(fr, ff, tr, tf, piece, target, color) {
    const files="abcdefgh", ranks="87654321";
    if (piece.type==="k"&&Math.abs(tf-ff)===2) return tf>ff?"O-O":"O-O-O";
    let s = "";
    if (piece.type!=="p") s = piece.type.toUpperCase();
    else if (target||(g.ep&&tr===g.ep[0]&&tf===g.ep[1])) s = files[ff];
    if (target||(piece.type==="p"&&g.ep&&tr===g.ep[0]&&tf===g.ep[1])) s += "x";
    return s + files[tf] + ranks[tr];
  }

  // ── AI (Minimax depth-2 + alpha-beta) ──────────────────────

  function aiMove() {
    if (g.turn!=="b"||g.mate||g.stale||!g.started) return;
    aiRunning = true;
    setTimeout(() => {
      const move = minimaxRoot();
      aiRunning = false;
      if (move) { g.sel=null; g.legal=[]; execMove(move.fr,move.ff,move.tr,move.tf,"b"); }
    }, 350);
  }

  function evalBoard() {
    let score = 0;
    for (let r=0;r<8;r++) for (let f=0;f<8;f++) {
      const p=g.board[r][f]; if(!p) continue;
      const pr = p.color==="w"?r:7-r;
      score += (p.color==="b"?1:-1) * (PV[p.type] + (PST[p.type]?.[pr]?.[f]||0));
    }
    return score;
  }

  function minimaxRoot() {
    let bestScore=-Infinity, bestMove=null;
    const sb=g.board.map(r=>[...r]), sc=JSON.parse(JSON.stringify(g.castling)), se=g.ep;
    const sChk=g.inCheck;

    for (let fr=0;fr<8;fr++) for (let ff=0;ff<8;ff++) {
      const p=g.board[fr][ff]; if(!p||p.color!=="b") continue;
      g.turn="b"; g.inCheck=sChk;
      const moves=getLegal(fr,ff,"b");
      g.board=sb.map(r=>[...r]); g.castling=JSON.parse(JSON.stringify(sc)); g.ep=se;

      for (const [tr,tf] of moves) {
        const nb=sb.map(r=>[...r]);
        const nc=JSON.parse(JSON.stringify(sc));
        const piece=nb[fr][ff];
        nb[tr][tf]=piece; nb[fr][ff]=null;
        if (piece.type==="p"&&(tr===0||tr===7)) nb[tr][tf]={type:"q",color:"b"};
        if (piece.type==="k"){nc["bk"]=false;nc["bq"]=false;}
        if (piece.type==="r"){if(ff===0)nc["bq"]=false;if(ff===7)nc["bk"]=false;}
        const nep=(piece.type==="p"&&Math.abs(tr-fr)===2)?[(fr+tr)/2,ff]:null;

        const prevBoard=g.board, prevCast=g.castling, prevEp=g.ep, prevTurn=g.turn, prevChk=g.inCheck;
        g.board=nb; g.castling=nc; g.ep=nep; g.turn="w"; g.inCheck=isInCheck("w");
        const score=alphaBeta(1,false,-Infinity,Infinity);
        g.board=prevBoard; g.castling=prevCast; g.ep=prevEp; g.turn=prevTurn; g.inCheck=prevChk;

        if (score>bestScore) { bestScore=score; bestMove={fr,ff,tr,tf}; }
      }
    }
    g.board=sb; g.castling=sc; g.ep=se; g.inCheck=sChk; g.turn="b";
    return bestMove;
  }

  function alphaBeta(depth, maximizing, alpha, beta) {
    if (depth===0) return evalBoard();
    const color=maximizing?"b":"w";
    let best=maximizing?-Infinity:Infinity;
    let moved=false;
    const sb=g.board.map(r=>[...r]), sc=JSON.parse(JSON.stringify(g.castling)), se=g.ep, st=g.turn, sChk=g.inCheck;

    outer:
    for (let fr=0;fr<8;fr++) for (let ff=0;ff<8;ff++) {
      const p=g.board[fr][ff]; if(!p||p.color!==color) continue;
      g.turn=color; g.inCheck=sChk;
      const moves=getLegal(fr,ff,color);
      g.board=sb.map(r=>[...r]); g.castling=JSON.parse(JSON.stringify(sc)); g.ep=se;

      for (const [tr,tf] of moves) {
        moved=true;
        const nb=sb.map(r=>[...r]);
        const nc=JSON.parse(JSON.stringify(sc));
        const piece=nb[fr][ff];
        nb[tr][tf]=piece; nb[fr][ff]=null;
        if (piece.type==="p"&&(tr===0||tr===7)) nb[tr][tf]={type:"q",color};
        if (piece.type==="k"){nc[color+"k"]=false;nc[color+"q"]=false;}
        if (piece.type==="r"){if(ff===0)nc[color+"q"]=false;if(ff===7)nc[color+"k"]=false;}
        const nep=(piece.type==="p"&&Math.abs(tr-fr)===2)?[(fr+tr)/2,ff]:null;

        g.board=nb; g.castling=nc; g.ep=nep; g.turn=color==="w"?"b":"w"; g.inCheck=isInCheck(g.turn);
        const score=alphaBeta(depth-1,!maximizing,alpha,beta);
        g.board=sb.map(r=>[...r]); g.castling=JSON.parse(JSON.stringify(sc)); g.ep=se; g.turn=st; g.inCheck=sChk;

        if (maximizing){best=Math.max(best,score);alpha=Math.max(alpha,best);}
        else{best=Math.min(best,score);beta=Math.min(beta,best);}
        if (beta<=alpha) break outer;
      }
    }
    return moved ? best : (maximizing ? -99999 : 99999);
  }

  // ── Rendering ───────────────────────────────────────────────

  function renderBoard() {
    const bd = document.getElementById("chess-board"); if (!bd) return;
    bd.innerHTML = "";

    for (let r=0;r<8;r++) for (let f=0;f<8;f++) {
      const cell = document.createElement("div");
      cell.className = "cc " + ((r+f)%2===0 ? "lt" : "dk");
      cell.dataset.r=r; cell.dataset.f=f;
      const p=g.board[r][f];
      if (p) cell.textContent = U[p.color+p.type];

      if (g.sel && g.sel[0]===r && g.sel[1]===f) cell.classList.add("sel");
      const isLegal = g.legal.some(m=>m[0]===r&&m[1]===f);
      if (isLegal) {
        if (p && p.color!==g.turn) cell.classList.add("lc2");
        else cell.classList.add("lm");
      }
      if (g.inCheck && p && p.type==="k" && p.color===g.turn) cell.classList.add("chk");

      cell.addEventListener("click", () => ChessGame.handleClick(r, f));
      bd.appendChild(cell);
    }
  }

  function updateSide() {
    const sw=document.getElementById("chess-slot-w");
    const sb=document.getElementById("chess-slot-b");
    if(sw) sw.classList.toggle("active", g.turn==="w"&&!g.mate&&!g.stale);
    if(sb) sb.classList.toggle("active", g.turn==="b"&&!g.mate&&!g.stale);
    const cw=document.getElementById("cap-w");
    const cb=document.getElementById("cap-b");
    if(cw) cw.textContent = g.capW.map(t=>U["b"+t]).join("");
    if(cb) cb.textContent = g.capB.map(t=>U["w"+t]).join("");
  }

  function setStatus(msg) { const el=document.getElementById("c-status"); if(el)el.textContent=msg; }

  function addLog(san, color) {
    const log=document.getElementById("c-log"); if(!log) return;
    const mn=Math.ceil(g.hist.length/2);
    if (color==="w") {
      const row=document.createElement("div"); row.className="move-row";
      row.innerHTML=`<span class="mn">${mn}.</span><span class="mw">${san}</span><span class="mb2" id="clb${mn}"></span>`;
      log.appendChild(row);
    } else {
      const sp=document.getElementById(`clb${mn}`);
      if(sp) sp.textContent=san;
      else {
        const row=document.createElement("div"); row.className="move-row";
        row.innerHTML=`<span class="mn">${mn}.</span><span class="mw">…</span><span class="mb2">${san}</span>`;
        log.appendChild(row);
      }
    }
    log.scrollTop=log.scrollHeight;
  }

  // ── Events ──────────────────────────────────────────────────
  function fireSpecial(eventType, detail) {
    if (callbacks.onSpecialEvent) callbacks.onSpecialEvent(eventType, detail, ChessGame.getState());
  }

  // ── Exports ──────────────────────────────────────────────────
  global.ChessGame = ChessGame;

})(window);