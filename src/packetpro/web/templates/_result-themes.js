(function () {
  const THEME_KEY = "packetpro.resultTheme";
  const CRT_THEMES = new Set(["amber", "arcade", "c64", "mainframe", "nasa70s", "pdp11", "phosphor", "tripleplanets", "weylandyutani"]);
  const THEME_OPTIONS = [
    {
        "value": "mainframe",
        "label": "1970s mainframe"
    },
    {
        "value": "dotmatrix",
        "label": "80s dot matrix printer"
    },
    {
        "value": "amber",
        "label": "Amber terminal"
    },
    {
        "value": "ansi",
        "label": "ANSI color terminal"
    },
    {
        "value": "blueprint",
        "label": "Blueprint"
    },
    {
        "value": "c64",
        "label": "Commodore 64"
    },
    {
        "value": "dune1984",
        "label": "Dune 1984"
    },
    {
        "value": "computer50s",
        "label": "Early 1950s computer"
    },
    {
        "value": "empire",
        "label": "Galactic Empire"
    },
    {
        "value": "phosphor",
        "label": "Green phosphor CRT"
    },
    {
        "value": "kawaiimail",
        "label": "Kawaii Mail"
    },
    {
        "value": "lsmail",
        "label": "Leisure Suit Mailman"
    },
    {
        "value": "teleprinter",
        "label": "Line printer"
    },
    {
        "value": "logansrun",
        "label": "Logan's Run"
    },
    {
        "value": "macintosh",
        "label": "Macintosh"
    },
    {
        "value": "mailtrek",
        "label": "Mail Trek (LCARS)"
    },
    {
        "value": "mailcraft",
        "label": "MailCraft"
    },
    {
        "value": "modern",
        "label": "Modern display"
    },
    {
        "value": "nasa70s",
        "label": "NASA Mission Control"
    },
    {
        "value": "newsprint",
        "label": "Newsprint"
    },
    {
        "value": "pacmail",
        "label": "PacMail"
    },
    {
        "value": "pdp11",
        "label": "PDP-11 terminal"
    },
    {
        "value": "reddwarf",
        "label": "Red Dwarf"
    },
    {
        "value": "arcade",
        "label": "Retro arcade CRT"
    },
    {
        "value": "solarized",
        "label": "Solarized"
    },
    {
        "value": "tripleplanets",
        "label": "Triple Planets"
    },
    {
        "value": "typewriter",
        "label": "Typewriter"
    },
    {
        "value": "weylandyutani",
        "label": "Weyland-Yutani Corp"
    }
].sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }));
  const THEMES = THEME_OPTIONS.map((item) => item.value);
  const DEFAULT_VARS = {
    "--result-bg": "rgba(26, 35, 50, 0.75)",
    "--result-border": "#2b3a4f",
    "--ocr-bg": "rgba(15, 20, 25, 0.45)",
    "--ocr-text": "#d5deea",
    "--ocr-font": "inherit",
    "--ocr-size": "0.95rem",
    "--ocr-shadow": "none",
    "--title-color": "#e7ecf3",
    "--title-font": "inherit",
    "--mark-bg": "#ffe08a",
    "--mark-fg": "#111",
    "--actions-bg": "rgba(26, 35, 50, 0.75)",
    "--actions-border": "#2b3a4f",
    "--empty-color": "#9aa7b8",
    "--thumb-border": "#2b3a4f",
    "--thumb-bg": "#111"
};
  const THEME_VARS = {
    "mainframe": {
        "--result-bg": "rgba(5, 8, 5, 0.95)",
        "--result-border": "#1a4a1a",
        "--ocr-bg": "#050805",
        "--ocr-text": "#3dff3d",
        "--ocr-font": "'Courier New', Courier, 'Share Tech Mono', ui-monospace, monospace",
        "--ocr-size": "0.8rem",
        "--ocr-shadow": "0 0 2px rgba(61, 255, 61, 0.35)",
        "--title-color": "#7fff7f",
        "--title-font": "'Courier New', Courier, 'Share Tech Mono', ui-monospace, monospace",
        "--mark-bg": "#0a3a0a",
        "--mark-fg": "#b0ffb0",
        "--actions-bg": "rgba(3, 6, 3, 0.92)",
        "--actions-border": "#1a4a1a",
        "--empty-color": "#5cff5c",
        "--thumb-border": "#2a6a2a",
        "--thumb-bg": "#020402"
    },
    "dotmatrix": {
        "--result-bg": "#e8e4dc",
        "--result-border": "#8a8070",
        "--ocr-bg": "#f6f6ec",
        "--ocr-text": "#1a1a1a",
        "--ocr-font": "'Courier New', Courier, 'Liberation Mono', monospace",
        "--ocr-size": "0.8rem",
        "--ocr-shadow": "0.05em 0 0 rgba(0, 0, 0, 0.18)",
        "--title-color": "#111",
        "--title-font": "'Courier New', Courier, monospace",
        "--mark-bg": "#c8c0b0",
        "--mark-fg": "#0a0806",
        "--actions-bg": "#dcd8d0",
        "--actions-border": "#8a8070",
        "--empty-color": "#4a4438",
        "--thumb-border": "#9a9080",
        "--thumb-bg": "#e0dcd4"
    },
    "amber": {
        "--result-bg": "rgba(18, 11, 0, 0.94)",
        "--result-border": "#4a3000",
        "--ocr-bg": "#120b00",
        "--ocr-text": "#ffb000",
        "--ocr-font": "'Share Tech Mono', ui-monospace, monospace",
        "--ocr-size": "0.84rem",
        "--ocr-shadow": "0 0 5px rgba(255, 176, 0, 0.35)",
        "--title-color": "#ffc84d",
        "--title-font": "'Share Tech Mono', ui-monospace, monospace",
        "--mark-bg": "#5c3a00",
        "--mark-fg": "#ffe08a",
        "--actions-bg": "rgba(14, 9, 0, 0.9)",
        "--actions-border": "#4a3000",
        "--empty-color": "#ffc84d",
        "--thumb-border": "#4a3000",
        "--thumb-bg": "#0a0600"
    },
    "ansi": {
        "--result-bg": "#0c0c0c",
        "--result-border": "#3a3a3a",
        "--ocr-bg": "#0a0a0a",
        "--ocr-text": "#c0c0c0",
        "--ocr-font": "'Share Tech Mono', ui-monospace, monospace",
        "--ocr-size": "0.82rem",
        "--ocr-shadow": "none",
        "--title-color": "#29b8db",
        "--title-font": "'Share Tech Mono', ui-monospace, monospace",
        "--mark-bg": "#404000",
        "--mark-fg": "#ffff55",
        "--actions-bg": "#080808",
        "--actions-border": "#3a3a3a",
        "--empty-color": "#9aa7b8",
        "--thumb-border": "#555",
        "--thumb-bg": "#050505"
    },
    "blueprint": {
        "--result-bg": "rgba(8, 38, 68, 0.88)",
        "--result-border": "#4a9fd4",
        "--ocr-bg": "#0c2d4f",
        "--ocr-text": "#d8ecff",
        "--ocr-font": "'Share Tech Mono', ui-monospace, monospace",
        "--ocr-size": "0.86rem",
        "--ocr-shadow": "none",
        "--title-color": "#7ec8ff",
        "--title-font": "'Share Tech Mono', ui-monospace, monospace",
        "--mark-bg": "rgba(126, 200, 255, 0.35)",
        "--mark-fg": "#fff",
        "--actions-bg": "rgba(6, 32, 58, 0.9)",
        "--actions-border": "#4a9fd4",
        "--empty-color": "#b8dcff",
        "--thumb-border": "#4a9fd4",
        "--thumb-bg": "#081e36"
    },
    "c64": {
        "--result-bg": "#352a78",
        "--result-border": "#5858b0",
        "--ocr-bg": "#40318d",
        "--ocr-text": "#7878e8",
        "--ocr-font": "'Courier New', Courier, monospace",
        "--ocr-size": "0.78rem",
        "--ocr-shadow": "0 0 1px rgba(120, 120, 232, 0.35)",
        "--title-color": "#a9a2f0",
        "--title-font": "'Courier New', Courier, monospace",
        "--mark-bg": "#5858b0",
        "--mark-fg": "#e8e4ff",
        "--actions-bg": "#3a2e70",
        "--actions-border": "#5858b0",
        "--empty-color": "#a9a2f0",
        "--thumb-border": "#5858b0",
        "--thumb-bg": "#2a2060"
    },
    "dune1984": {
        "--result-bg": "rgba(14, 10, 5, 0.94)",
        "--result-border": "#6a5030",
        "--ocr-bg": "#120c06",
        "--ocr-text": "#d8c8a0",
        "--ocr-font": "'Share Tech Mono', 'Courier New', Courier, ui-monospace, monospace",
        "--ocr-size": "0.8rem",
        "--ocr-shadow": "0 1px 2px rgba(0, 0, 0, 0.4)",
        "--title-color": "#e8d0a0",
        "--title-font": "'Share Tech Mono', ui-monospace, monospace",
        "--mark-bg": "#6a5030",
        "--mark-fg": "#f0e0c0",
        "--actions-bg": "rgba(10, 7, 4, 0.92)",
        "--actions-border": "#6a5030",
        "--empty-color": "#d8c8a0",
        "--thumb-border": "#6a5030",
        "--thumb-bg": "#0a0704"
    },
    "computer50s": {
        "--result-bg": "#e0d8c8",
        "--result-border": "#a89880",
        "--ocr-bg": "#ece4d4",
        "--ocr-text": "#1a1814",
        "--ocr-font": "'Courier New', Courier, 'Liberation Mono', ui-monospace, monospace",
        "--ocr-size": "0.82rem",
        "--ocr-shadow": "none",
        "--title-color": "#101008",
        "--title-font": "'Courier New', Courier, monospace",
        "--mark-bg": "#d0c4a8",
        "--mark-fg": "#101008",
        "--actions-bg": "#d8d0c0",
        "--actions-border": "#a89880",
        "--empty-color": "#4a4438",
        "--thumb-border": "#a89880",
        "--thumb-bg": "#d4ccc0"
    },
    "empire": {
        "--result-bg": "rgba(6, 6, 10, 0.95)",
        "--result-border": "#3a3a50",
        "--ocr-bg": "#08080c",
        "--ocr-text": "#b8c8e0",
        "--ocr-font": "'Share Tech Mono', 'Courier New', Courier, ui-monospace, monospace",
        "--ocr-size": "0.8rem",
        "--ocr-shadow": "0 0 2px rgba(184, 200, 224, 0.2)",
        "--title-color": "#e8ecf4",
        "--title-font": "'Share Tech Mono', ui-monospace, monospace",
        "--mark-bg": "#c41c1c",
        "--mark-fg": "#fff",
        "--actions-bg": "rgba(4, 4, 8, 0.92)",
        "--actions-border": "#3a3a50",
        "--empty-color": "#b8c8e0",
        "--thumb-border": "#3a3a50",
        "--thumb-bg": "#040408"
    },
    "phosphor": {
        "--result-bg": "rgba(2, 10, 2, 0.94)",
        "--result-border": "#1a4d1a",
        "--ocr-bg": "#020a02",
        "--ocr-text": "#39ff39",
        "--ocr-font": "'Share Tech Mono', ui-monospace, monospace",
        "--ocr-size": "0.84rem",
        "--ocr-shadow": "0 0 6px rgba(57, 255, 57, 0.45)",
        "--title-color": "#5cff5c",
        "--title-font": "'Share Tech Mono', ui-monospace, monospace",
        "--mark-bg": "#1a5c1a",
        "--mark-fg": "#7dff7d",
        "--actions-bg": "rgba(1, 7, 1, 0.9)",
        "--actions-border": "#1a4d1a",
        "--empty-color": "#5cff5c",
        "--thumb-border": "#1a4d1a",
        "--thumb-bg": "#010501"
    },
    "kawaiimail": {
        "--result-bg": "#fff4fa",
        "--result-border": "#f0b8d8",
        "--ocr-bg": "#fff8fc",
        "--ocr-text": "#5a4868",
        "--ocr-font": "'Zen Maru Gothic', 'M PLUS Rounded 1c', sans-serif",
        "--ocr-size": "0.88rem",
        "--ocr-shadow": "none",
        "--title-color": "#d85898",
        "--title-font": "'Zen Maru Gothic', 'M PLUS Rounded 1c', sans-serif",
        "--mark-bg": "#ffc8e0",
        "--mark-fg": "#5a4868",
        "--actions-bg": "#ffeef6",
        "--actions-border": "#f0b8d8",
        "--empty-color": "#8a7088",
        "--thumb-border": "#f0b8d8",
        "--thumb-bg": "#f8e8f0"
    },
    "lsmail": {
        "--result-bg": "#000088",
        "--result-border": "#5555ff",
        "--ocr-bg": "#0000aa",
        "--ocr-text": "#ffffff",
        "--ocr-font": "'Courier New', Courier, monospace",
        "--ocr-size": "0.8rem",
        "--ocr-shadow": "none",
        "--title-color": "#ff55ff",
        "--title-font": "'Courier New', Courier, monospace",
        "--mark-bg": "#ffff55",
        "--mark-fg": "#0000aa",
        "--actions-bg": "#000099",
        "--actions-border": "#5555ff",
        "--empty-color": "#ccccff",
        "--thumb-border": "#5555ff",
        "--thumb-bg": "#000066"
    },
    "teleprinter": {
        "--result-bg": "#ddd7c6",
        "--result-border": "#b8b0a0",
        "--ocr-bg": "#e8e2d0",
        "--ocr-text": "#111",
        "--ocr-font": "'Courier New', Courier, monospace",
        "--ocr-size": "0.82rem",
        "--ocr-shadow": "none",
        "--title-color": "#000",
        "--title-font": "'Courier New', Courier, monospace",
        "--mark-bg": "#ccc4b0",
        "--mark-fg": "#000",
        "--actions-bg": "#d4cec0",
        "--actions-border": "#b8b0a0",
        "--empty-color": "#444",
        "--thumb-border": "#a8a090",
        "--thumb-bg": "#d0cac0"
    },
    "logansrun": {
        "--result-bg": "rgba(12, 18, 26, 0.94)",
        "--result-border": "#3a5060",
        "--ocr-bg": "#101820",
        "--ocr-text": "#c8d8e8",
        "--ocr-font": "'Share Tech Mono', 'Courier New', Courier, ui-monospace, monospace",
        "--ocr-size": "0.8rem",
        "--ocr-shadow": "0 0 2px rgba(200, 216, 232, 0.15)",
        "--title-color": "#e8f0f8",
        "--title-font": "'Share Tech Mono', ui-monospace, monospace",
        "--mark-bg": "#ff8040",
        "--mark-fg": "#101820",
        "--actions-bg": "rgba(8, 14, 20, 0.92)",
        "--actions-border": "#3a5060",
        "--empty-color": "#c8d8e8",
        "--thumb-border": "#3a5060",
        "--thumb-bg": "#080c12"
    },
    "macintosh": {
        "--result-bg": "#f8f8f8",
        "--result-border": "#c0c0c0",
        "--ocr-bg": "#ffffff",
        "--ocr-text": "#000000",
        "--ocr-font": "Geneva, 'Helvetica Neue', Helvetica, Arial, sans-serif",
        "--ocr-size": "0.8rem",
        "--ocr-shadow": "none",
        "--title-color": "#000000",
        "--title-font": "Geneva, 'Helvetica Neue', Helvetica, Arial, sans-serif",
        "--mark-bg": "#ffff00",
        "--mark-fg": "#000",
        "--actions-bg": "#f0f0f0",
        "--actions-border": "#c0c0c0",
        "--empty-color": "#444",
        "--thumb-border": "#a0a0a0",
        "--thumb-bg": "#e8e8e8"
    },
    "mailtrek": {
        "--result-bg": "#000",
        "--result-border": "#ff9900",
        "--ocr-bg": "#000",
        "--ocr-text": "#ffcc99",
        "--ocr-font": "Antonio, 'Arial Narrow', 'Helvetica Neue', sans-serif",
        "--ocr-size": "0.92rem",
        "--ocr-shadow": "none",
        "--title-color": "#ff9900",
        "--title-font": "Antonio, 'Arial Narrow', 'Helvetica Neue', sans-serif",
        "--mark-bg": "#99ccff",
        "--mark-fg": "#000",
        "--actions-bg": "#0a0a0a",
        "--actions-border": "#ff9900",
        "--empty-color": "#9999ff",
        "--thumb-border": "#99ccff",
        "--thumb-bg": "#101010"
    },
    "mailcraft": {
        "--result-bg": "#d0c4a4",
        "--result-border": "#8a9a78",
        "--ocr-bg": "#d6c8a8",
        "--ocr-text": "#3a3428",
        "--ocr-font": "'Segoe UI', system-ui, -apple-system, sans-serif",
        "--ocr-size": "0.94rem",
        "--ocr-shadow": "none",
        "--title-color": "#2f4a38",
        "--title-font": "'Segoe UI', system-ui, sans-serif",
        "--mark-bg": "#8a9a78",
        "--mark-fg": "#fff",
        "--actions-bg": "#c8baa0",
        "--actions-border": "#8a9a78",
        "--empty-color": "#4a4438",
        "--thumb-border": "#8a9a78",
        "--thumb-bg": "#b8aa90"
    },
    "modern": {
        "--result-bg": "rgba(26, 35, 50, 0.82)",
        "--result-border": "#2b3a4f",
        "--ocr-bg": "rgba(15, 20, 25, 0.55)",
        "--ocr-text": "#e7ecf3",
        "--ocr-font": "inherit",
        "--ocr-size": "0.92rem",
        "--ocr-shadow": "none",
        "--title-color": "#e7ecf3",
        "--title-font": "inherit",
        "--mark-bg": "#ffe08a",
        "--mark-fg": "#111",
        "--actions-bg": "rgba(26, 35, 50, 0.82)",
        "--actions-border": "#2b3a4f",
        "--empty-color": "#9aa7b8",
        "--thumb-border": "#2b3a4f",
        "--thumb-bg": "#111"
    },
    "nasa70s": {
        "--result-bg": "rgba(2, 6, 3, 0.95)",
        "--result-border": "#1a4a1a",
        "--ocr-bg": "#020804",
        "--ocr-text": "#38c838",
        "--ocr-font": "'Share Tech Mono', 'Courier New', Courier, ui-monospace, monospace",
        "--ocr-size": "0.78rem",
        "--ocr-shadow": "0 0 2px rgba(56, 200, 56, 0.35)",
        "--title-color": "#68d868",
        "--title-font": "'Share Tech Mono', ui-monospace, monospace",
        "--mark-bg": "#1a4a1a",
        "--mark-fg": "#a8f0a8",
        "--actions-bg": "rgba(1, 5, 2, 0.92)",
        "--actions-border": "#1a4a1a",
        "--empty-color": "#68d868",
        "--thumb-border": "#2a6a2a",
        "--thumb-bg": "#010402"
    },
    "newsprint": {
        "--result-bg": "#f0ede6",
        "--result-border": "#c8c0b4",
        "--ocr-bg": "#f7f4ee",
        "--ocr-text": "#1c1c1c",
        "--ocr-font": "Georgia, 'Times New Roman', serif",
        "--ocr-size": "0.95rem",
        "--ocr-shadow": "none",
        "--title-color": "#111",
        "--title-font": "Georgia, 'Times New Roman', serif",
        "--mark-bg": "#ffe566",
        "--mark-fg": "#111",
        "--actions-bg": "#e8e5de",
        "--actions-border": "#c8c0b4",
        "--empty-color": "#555",
        "--thumb-border": "#b0a898",
        "--thumb-bg": "#e0ddd6"
    },
    "pacmail": {
        "--result-bg": "#000",
        "--result-border": "#2121de",
        "--ocr-bg": "#000",
        "--ocr-text": "#fff",
        "--ocr-font": "'Segoe UI', system-ui, -apple-system, sans-serif",
        "--ocr-size": "1.08rem",
        "--ocr-shadow": "2px 2px 0 #2121de",
        "--title-color": "#ffe100",
        "--title-font": "'Press Start 2P', ui-monospace, monospace",
        "--mark-bg": "#2121de",
        "--mark-fg": "#ffe100",
        "--actions-bg": "#0a0a2a",
        "--actions-border": "#2121de",
        "--empty-color": "#00ffff",
        "--thumb-border": "#2121de",
        "--thumb-bg": "#101030"
    },
    "pdp11": {
        "--result-bg": "rgba(8, 6, 4, 0.95)",
        "--result-border": "#6a5030",
        "--ocr-bg": "#0a0806",
        "--ocr-text": "#e8a030",
        "--ocr-font": "'Courier New', Courier, 'Share Tech Mono', ui-monospace, monospace",
        "--ocr-size": "0.8rem",
        "--ocr-shadow": "0 0 3px rgba(232, 160, 48, 0.28)",
        "--title-color": "#ffcc66",
        "--title-font": "'Courier New', Courier, monospace",
        "--mark-bg": "#6a5030",
        "--mark-fg": "#ffe8b0",
        "--actions-bg": "rgba(6, 5, 3, 0.92)",
        "--actions-border": "#6a5030",
        "--empty-color": "#ffcc66",
        "--thumb-border": "#6a5030",
        "--thumb-bg": "#060504"
    },
    "reddwarf": {
        "--result-bg": "rgba(6, 10, 20, 0.94)",
        "--result-border": "#3a6080",
        "--ocr-bg": "#080c18",
        "--ocr-text": "#a8f0ff",
        "--ocr-font": "'Courier New', Courier, 'Share Tech Mono', ui-monospace, monospace",
        "--ocr-size": "0.82rem",
        "--ocr-shadow": "0 0 3px rgba(168, 240, 255, 0.25)",
        "--title-color": "#f0d878",
        "--title-font": "'Courier New', Courier, monospace",
        "--mark-bg": "#78d8f0",
        "--mark-fg": "#080c18",
        "--actions-bg": "rgba(4, 8, 16, 0.92)",
        "--actions-border": "#3a6080",
        "--empty-color": "#a8f0ff",
        "--thumb-border": "#3a6080",
        "--thumb-bg": "#040810"
    },
    "arcade": {
        "--result-bg": "rgba(8, 5, 24, 0.94)",
        "--result-border": "#2121de",
        "--ocr-bg": "#0c0820",
        "--ocr-text": "#e8f4ff",
        "--ocr-font": "'Courier New', Courier, ui-monospace, monospace",
        "--ocr-size": "0.94rem",
        "--ocr-shadow": "1px 0 0 rgba(255, 0, 128, 0.35), -1px 0 0 rgba(0, 255, 255, 0.35)",
        "--title-color": "#ffea00",
        "--title-font": "'Courier New', Courier, monospace",
        "--mark-bg": "#00e5ff",
        "--mark-fg": "#0c0820",
        "--actions-bg": "rgba(6, 4, 18, 0.92)",
        "--actions-border": "#2121de",
        "--empty-color": "#00e5ff",
        "--thumb-border": "#2121de",
        "--thumb-bg": "#060418"
    },
    "solarized": {
        "--result-bg": "#073642",
        "--result-border": "#2aa198",
        "--ocr-bg": "#002b36",
        "--ocr-text": "#93a1a1",
        "--ocr-font": "inherit",
        "--ocr-size": "0.92rem",
        "--ocr-shadow": "none",
        "--title-color": "#eee8d5",
        "--title-font": "inherit",
        "--mark-bg": "#b58900",
        "--mark-fg": "#002b36",
        "--actions-bg": "#073642",
        "--actions-border": "#2aa198",
        "--empty-color": "#93a1a1",
        "--thumb-border": "#586e75",
        "--thumb-bg": "#001f27"
    },
    "tripleplanets": {
        "--result-bg": "rgba(6, 6, 10, 0.92)",
        "--result-border": "#4a4a60",
        "--ocr-bg": "rgba(14, 14, 20, 0.78)",
        "--ocr-text": "#d8d8e0",
        "--ocr-font": "'Share Tech Mono', ui-monospace, monospace",
        "--ocr-size": "0.76rem",
        "--ocr-shadow": "none",
        "--title-color": "#f0c848",
        "--title-font": "'Share Tech Mono', ui-monospace, monospace",
        "--mark-bg": "#c62828",
        "--mark-fg": "#fff",
        "--actions-bg": "rgba(4, 4, 8, 0.9)",
        "--actions-border": "#4a4a60",
        "--empty-color": "#d8d8e0",
        "--thumb-border": "#4a4a60",
        "--thumb-bg": "#040408"
    },
    "typewriter": {
        "--result-bg": "#ebe3d0",
        "--result-border": "rgba(80, 60, 40, 0.28)",
        "--ocr-bg": "#f3ead8",
        "--ocr-text": "#2a2118",
        "--ocr-font": "'Courier New', Courier, 'Liberation Mono', monospace",
        "--ocr-size": "0.9rem",
        "--ocr-shadow": "none",
        "--title-color": "#1a140e",
        "--title-font": "'Courier New', Courier, monospace",
        "--mark-bg": "#e8d4a8",
        "--mark-fg": "#1a140e",
        "--actions-bg": "#e5dcc8",
        "--actions-border": "rgba(80, 60, 40, 0.28)",
        "--empty-color": "#5a4a38",
        "--thumb-border": "rgba(80, 60, 40, 0.35)",
        "--thumb-bg": "#ddd4c0"
    },
    "weylandyutani": {
        "--result-bg": "rgba(2, 4, 2, 0.95)",
        "--result-border": "#1a3a1a",
        "--ocr-bg": "#020402",
        "--ocr-text": "#4ae24a",
        "--ocr-font": "'Share Tech Mono', 'Courier New', Courier, ui-monospace, monospace",
        "--ocr-size": "0.8rem",
        "--ocr-shadow": "0 0 2px rgba(74, 226, 74, 0.35)",
        "--title-color": "#8fd48f",
        "--title-font": "'Share Tech Mono', ui-monospace, monospace",
        "--mark-bg": "#1a4a1a",
        "--mark-fg": "#b0ffb0",
        "--actions-bg": "rgba(1, 3, 1, 0.92)",
        "--actions-border": "#1a3a1a",
        "--empty-color": "#6a9a6a",
        "--thumb-border": "#1a3a1a",
        "--thumb-bg": "#010201"
    }
};
  const viewport = document.getElementById("results-viewport");
  const select = document.getElementById("result-theme");
  if (!viewport || !select) return;
  function applyThemeVars(theme) {
    const chosen = THEMES.includes(theme) ? theme : "modern";
    const vars = Object.assign({}, DEFAULT_VARS, THEME_VARS[chosen] || THEME_VARS.modern);
    Object.keys(DEFAULT_VARS).forEach((key) => {
      viewport.style.setProperty(key, vars[key]);
    });
    return chosen;
  }
  function applyResultTheme(theme) {
    if (theme === "minecraft") theme = "mailcraft";
    const chosen = applyThemeVars(theme);
    viewport.className = "results-viewport theme-" + chosen;
    viewport.classList.toggle("crt-scanlines", CRT_THEMES.has(chosen));
    if (select.value !== chosen) select.value = chosen;
    try { localStorage.setItem(THEME_KEY, chosen); } catch (_) {}
  }
  if (!select.options.length) {
    THEME_OPTIONS.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.value;
      option.textContent = item.label;
      select.appendChild(option);
    });
  }
  let saved = null;
  try { saved = localStorage.getItem(THEME_KEY); } catch (_) {}
  applyResultTheme(saved || "modern");
  select.addEventListener("change", () => applyResultTheme(select.value));
})();
