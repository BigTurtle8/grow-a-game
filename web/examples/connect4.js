import { start } from "/static/kit.js";

const COLUMNS = 7;
const ROWS = 6;
const CELL = 84;
const BOARD_X = (1280 - COLUMNS * CELL) / 2;
const BOARD_Y = 120;
const COLORS = { 1: "#ff4d4d", 2: "#ffd23d" };
const NAMES = { 1: "Red", 2: "Yellow" };
const DIRECTIONS = [[0, 1], [1, 0], [1, 1], [1, -1]];

start({
  width: 1280,
  height: 720,
  background: "#10131c",

  init(g) {
    g.keep.wins ??= { 1: 0, 2: 0 };
    g.state = {
      grid: Array.from({ length: ROWS }, () => Array(COLUMNS).fill(0)),
      pieces: [],
      turn: 1,
      busy: false,
      winLine: null,
    };
    for (let player = 1; player <= 2; player++) {
      g.setScore(player, g.keep.wins[player], NAMES[player]);
    }
    announceTurn(g);
  },

  onInput(g, event) {
    const state = g.state;
    const isHuman = g.players === 2 || event.player === 1;
    if (!isHuman || event.player !== state.turn || state.busy) return;
    if (event.kind === "select" && event.control === "column") dropPiece(g, event.value);
  },

  update(g, dt) {
    for (const piece of g.state.pieces) {
      if (piece.y >= piece.targetY) continue;
      piece.speed += 3000 * dt;
      piece.y = Math.min(piece.targetY, piece.y + piece.speed * dt);
    }
  },

  draw(g, ctx) {
    const state = g.state;
    for (const piece of state.pieces) {
      g.circle(columnX(piece.column), piece.y, CELL * 0.4, COLORS[piece.player]);
    }

    ctx.beginPath();
    ctx.roundRect(BOARD_X - 14, BOARD_Y - 14, COLUMNS * CELL + 28, ROWS * CELL + 28, 20);
    for (let row = 0; row < ROWS; row++) {
      for (let column = 0; column < COLUMNS; column++) {
        const x = columnX(column);
        const y = rowY(row);
        ctx.moveTo(x + CELL * 0.4, y);
        ctx.arc(x, y, CELL * 0.4, 0, Math.PI * 2);
      }
    }
    ctx.fillStyle = "#2450d6";
    ctx.fill("evenodd");

    for (let column = 0; column < COLUMNS; column++) {
      g.text(column + 1, columnX(column), BOARD_Y + ROWS * CELL + 48, { size: 30, color: "#8a93b8" });
    }
    if (state.winLine) {
      for (const [row, column] of state.winLine) {
        g.circle(columnX(column), rowY(row), CELL * 0.43, null, { stroke: "#ffffff", lineWidth: 6 });
      }
    }
    if (!state.busy && !state.winLine) {
      g.circle(BOARD_X - 70, BOARD_Y + 40, 26, COLORS[state.turn]);
    }
  },
});

function columnX(column) {
  return BOARD_X + column * CELL + CELL / 2;
}

function rowY(row) {
  return BOARD_Y + row * CELL + CELL / 2;
}

function lowestEmptyRow(grid, column) {
  for (let row = ROWS - 1; row >= 0; row--) {
    if (grid[row][column] === 0) return row;
  }
  return -1;
}

function announceTurn(g) {
  const { turn } = g.state;
  const computer = g.players === 1 && turn === 2;
  g.setStatus(computer ? "Computer is thinking…" : `${NAMES[turn]}'s turn`);
  for (let player = 1; player <= g.players; player++) {
    g.tell(player, player === turn ? "Your turn · pick a column" : `Waiting for ${NAMES[turn]}…`);
  }
}

function dropPiece(g, column) {
  const state = g.state;
  const row = lowestEmptyRow(state.grid, column);
  if (row < 0) {
    g.message("That column is full");
    return;
  }
  const player = state.turn;
  state.grid[row][column] = player;
  state.busy = true;
  state.pieces.push({ row, column, player, y: BOARD_Y - CELL, targetY: rowY(row), speed: 0 });
  g.beep(300 + row * 40, 0.08);

  g.after(0.5, () => {
    state.busy = false;
    const line = winningLine(state.grid, row, column);
    if (line) {
      state.winLine = line;
      g.keep.wins[player] += 1;
      g.setScore(player, g.keep.wins[player], NAMES[player]);
      g.gameOver(`${NAMES[player]} wins!`, "Four in a row");
      return;
    }
    if (state.grid[0].every((cell) => cell !== 0)) {
      g.gameOver("Draw!", "The board is full");
      return;
    }
    state.turn = player === 1 ? 2 : 1;
    announceTurn(g);
    if (g.players === 1 && state.turn === 2) {
      state.busy = true;
      g.after(0.7, () => {
        state.busy = false;
        dropPiece(g, chooseComputerColumn(state.grid));
      });
    }
  });
}

function winningLine(grid, row, column) {
  const player = grid[row][column];
  for (const [dRow, dColumn] of DIRECTIONS) {
    const line = [[row, column]];
    for (const sign of [1, -1]) {
      let r = row + dRow * sign;
      let c = column + dColumn * sign;
      while (r >= 0 && r < ROWS && c >= 0 && c < COLUMNS && grid[r][c] === player) {
        line.push([r, c]);
        r += dRow * sign;
        c += dColumn * sign;
      }
    }
    if (line.length >= 4) return line;
  }
  return null;
}

function chooseComputerColumn(grid) {
  const open = [...Array(COLUMNS).keys()].filter((column) => lowestEmptyRow(grid, column) >= 0);
  for (const player of [2, 1]) {
    for (const column of open) {
      const row = lowestEmptyRow(grid, column);
      grid[row][column] = player;
      const wins = winningLine(grid, row, column);
      grid[row][column] = 0;
      if (wins) return column;
    }
  }
  const byCenter = open.sort((a, b) => Math.abs(a - 3) - Math.abs(b - 3) + (Math.random() - 0.5));
  return byCenter[0];
}
