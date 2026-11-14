import { describe, expect, it, vi } from "vitest";
import { DRAG_CREATE_MIN, DrawingEngine, MIN_SIZE, type EngineDeps } from "./engine";
import { layoutDrawing, type Pt } from "./geometry";
import { MAX_PER_SYMBOL, PALETTE, type Anchor, type Drawing } from "./model";

// 1 second = 1 pixel and price p is at y = 1000 - p; the plot is 800 x 500.
const W = 800, H = 500;
const inPlot = (p: Pt) => p.x >= 0 && p.x <= W && p.y >= 0 && p.y <= H;

function setup() {
  const snaps: boolean[] = [];
  let eng!: DrawingEngine;
  const deps: EngineDeps = {
    toAnchor: (p, snap) => { snaps.push(snap); return inPlot(p) ? { time: p.x, price: 1000 - p.y } : null; },
    toPixel: (a: Anchor) => ({ x: a.time, y: 1000 - a.price }),
    inPlot,
    shapes: () => eng.getState().drawings.map((d) => layoutDrawing(d, (t) => t, (p) => 1000 - p, { w: W, h: H })).filter((s) => s !== null) as never,
  };
  eng = new DrawingEngine(deps);
  const st = () => eng.getState();
  const click = (x: number, y: number) => { const used = eng.pointerDown({ x, y }); eng.pointerUp({ x, y }); return used; };
  const drag = (from: Pt, to: Pt, steps = 6) => {
    const used = eng.pointerDown(from);
    for (let i = 1; i <= steps; i++) eng.pointerMove({ x: from.x + ((to.x - from.x) * i) / steps, y: from.y + ((to.y - from.y) * i) / steps });
    eng.pointerUp(to);
    return used;
  };
  return { eng, st, click, drag, snaps };
}

/** An engine that already holds one trend line from (100,300) to (300,200), selected. */
function withTrend() {
  const t = setup();
  t.eng.setTool("trend");
  t.click(100, 300); t.click(300, 200);
  return t;
}

describe("creating two-point drawings", () => {
  it("click, move, click: the preview follows the pointer, then the drawing is made", () => {
    const { eng, st, click } = setup();
    eng.setTool("trend");
    expect(click(100, 300)).toBe(true); // the press was used, so the chart must not pan
    expect(st().preview).not.toBeNull();
    expect(st().drawings).toHaveLength(0);
    eng.pointerMove({ x: 250, y: 220 });
    expect(st().preview!.points[1]).toEqual({ time: 250, price: 780 });
    expect(click(300, 200)).toBe(true);
    expect(st().drawings).toHaveLength(1);
    expect(st().drawings[0]).toMatchObject({ kind: "trend", points: [{ time: 100, price: 700 }, { time: 300, price: 800 }] });
    expect(st().preview).toBeNull();
  });
  it("press, drag, release also creates it", () => {
    const { st, eng, drag } = setup();
    eng.setTool("rect");
    drag({ x: 100, y: 300 }, { x: 300, y: 200 });
    expect(st().drawings).toHaveLength(1);
    expect(st().drawings[0].kind).toBe("rect");
  });
  it("after finishing, the new drawing is selected and the tool goes back to Select", () => {
    const { st } = withTrend();
    expect(st().tool).toBe("select");
    expect(st().selectedId).toBe(st().drawings[0].id);
  });
  it("a tiny drag on the first press is a plain click, so click-click still works", () => {
    const { eng, st, drag, click } = setup();
    eng.setTool("trend");
    drag({ x: 100, y: 300 }, { x: 100 + DRAG_CREATE_MIN - 2, y: 300 });
    expect(st().drawings).toHaveLength(0);
    expect(st().preview).not.toBeNull(); // still waiting for the second point
    click(300, 200);
    expect(st().drawings).toHaveLength(1);
  });
  it("a second point on top of the first is ignored (it would be invisible)", () => {
    const { eng, st, click } = setup();
    eng.setTool("trend");
    click(100, 300);
    click(100 + MIN_SIZE - 1, 300);
    expect(st().drawings).toHaveLength(0);
    expect(st().preview).not.toBeNull();
  });
  it("works for fib too", () => {
    const { eng, st, click } = setup();
    eng.setTool("fib");
    click(100, 300); click(300, 200);
    expect(st().drawings[0].kind).toBe("fib");
  });
});

describe("creating one-point drawings", () => {
  it("a horizontal and a vertical line are made by one click", () => {
    const { eng, st, click } = setup();
    eng.setTool("hline");
    expect(click(400, 250)).toBe(true);
    expect(st().drawings[0]).toMatchObject({ kind: "hline", points: [{ time: 400, price: 750 }] });
    eng.setTool("vline");
    click(500, 100);
    expect(st().drawings[1]).toMatchObject({ kind: "vline", points: [{ time: 500, price: 900 }] });
  });
});

describe("clicks outside the plot", () => {
  it("are not used, so the chart keeps them", () => {
    const { eng, st, click } = setup();
    eng.setTool("trend");
    expect(click(W + 50, 100)).toBe(false); // over the price scale
    expect(click(100, H + 50)).toBe(false); // over the time axis
    expect(st().preview).toBeNull();
    expect(st().drawings).toHaveLength(0);
  });
});

describe("cancelling", () => {
  it("Escape cancels a half-placed drawing, then leaves the tool, then deselects", () => {
    const { eng, st, click } = setup();
    eng.setTool("trend");
    click(100, 300);
    expect(eng.keyDown({ key: "Escape" })).toBe(true);
    expect(st().preview).toBeNull();
    expect(st().tool).toBe("trend");
    expect(eng.keyDown({ key: "Escape" })).toBe(true);
    expect(st().tool).toBe("select");
    expect(eng.keyDown({ key: "Escape" })).toBe(false); // nothing left to do
  });
  it("Escape deselects a selected drawing", () => {
    const { eng, st } = withTrend();
    expect(st().selectedId).not.toBeNull();
    expect(eng.keyDown({ key: "Escape" })).toBe(true);
    expect(st().selectedId).toBeNull();
  });
  it("choosing another tool drops a half-placed drawing", () => {
    const { eng, st, click } = setup();
    eng.setTool("trend");
    click(100, 300);
    eng.setTool("rect");
    expect(st().preview).toBeNull();
  });
});

describe("selecting", () => {
  it("clicking a drawing selects it and uses the press; clicking empty space deselects and does NOT use it", () => {
    const { eng, st, click } = withTrend();
    eng.keyDown({ key: "Escape" });
    expect(click(200, 250)).toBe(true); // on the line
    expect(st().selectedId).toBe(st().drawings[0].id);
    expect(click(700, 50)).toBe(false); // empty: the chart may pan
    expect(st().selectedId).toBeNull();
  });
  it("hovering marks a drawing without selecting it", () => {
    const { eng, st } = withTrend();
    eng.keyDown({ key: "Escape" });
    eng.pointerMove({ x: 200, y: 250 });
    expect(st().hoverId).toBe(st().drawings[0].id);
    expect(st().selectedId).toBeNull();
    eng.pointerMove({ x: 700, y: 50 });
    expect(st().hoverId).toBeNull();
  });
  it("a subscriber is told on a real change, but not repeatedly for an unchanged hover", () => {
    const { eng } = withTrend();
    const spy = vi.fn();
    eng.subscribe(spy);
    eng.pointerMove({ x: 200, y: 250 });
    eng.pointerMove({ x: 201, y: 250 });
    eng.pointerMove({ x: 202, y: 249 });
    expect(spy).toHaveBeenCalledTimes(1);
  });
});

describe("editing", () => {
  it("dragging an end handle moves just that end", () => {
    const { st, drag } = withTrend();
    drag({ x: 300, y: 200 }, { x: 400, y: 100 });
    expect(st().drawings[0].points).toEqual([{ time: 100, price: 700 }, { time: 400, price: 900 }]);
  });
  it("dragging the body moves the whole drawing by the same amount", () => {
    const { st, drag } = withTrend();
    drag({ x: 200, y: 250 }, { x: 250, y: 300 });
    expect(st().drawings[0].points).toEqual([{ time: 150, price: 650 }, { time: 350, price: 750 }]);
  });
  it("a drag is ONE undo step, however many mouse moves it had", () => {
    const { eng, st, drag } = withTrend();
    drag({ x: 300, y: 200 }, { x: 400, y: 100 }, 40);
    eng.undo();
    expect(st().drawings[0].points[1]).toEqual({ time: 300, price: 800 }); // back to before the drag
    eng.undo();
    expect(st().drawings).toHaveLength(0); // the next undo removes the drawing itself
    eng.redo(); eng.redo();
    expect(st().drawings[0].points[1]).toEqual({ time: 400, price: 900 });
  });
  it("a press and release without moving records no step", () => {
    const { eng, st, click } = withTrend();
    click(200, 250);
    eng.undo();
    expect(st().drawings).toHaveLength(0); // one undo went straight back past the creation: no step for the click
  });
  it("a body drag that would leave the plot holds still instead of corrupting the drawing", () => {
    const { st, drag } = withTrend();
    drag({ x: 200, y: 250 }, { x: 5000, y: 250 });
    expect(st().drawings[0].points.every((p) => Number.isFinite(p.time) && p.time <= W)).toBe(true);
  });
  it("Escape during a drag puts the drawing back", () => {
    const { eng, st } = withTrend();
    eng.pointerDown({ x: 300, y: 200 });
    eng.pointerMove({ x: 400, y: 100 });
    expect(st().drawings[0].points[1].time).toBe(400);
    eng.keyDown({ key: "Escape" });
    expect(st().drawings[0].points[1]).toEqual({ time: 300, price: 800 });
    eng.pointerUp({ x: 400, y: 100 });
    expect(st().canUndo).toBe(true);
    eng.undo();
    expect(st().drawings).toHaveLength(0); // no extra step was recorded for the cancelled drag
  });
  it("only the selected drawing's handle can be grabbed; an unselected end drags the whole line", () => {
    const { eng, st, drag } = withTrend();
    eng.keyDown({ key: "Escape" }); // deselect
    drag({ x: 300, y: 200 }, { x: 350, y: 200 }); // the end of the line, but nothing is selected: this is a body grab
    expect(st().drawings[0].points[0].time).toBe(150); // both ends moved
  });
});

describe("deleting, clearing and undo", () => {
  it("Delete and Backspace remove the selected drawing; with nothing selected they do nothing", () => {
    const { eng, st } = withTrend();
    expect(eng.keyDown({ key: "Delete" })).toBe(true);
    expect(st().drawings).toHaveLength(0);
    expect(eng.keyDown({ key: "Backspace" })).toBe(false);
    expect(eng.deleteSelected()).toBe(false);
  });
  it("clear all is undoable", () => {
    const { eng, st, click } = withTrend();
    eng.setTool("hline"); click(400, 250);
    expect(st().drawings).toHaveLength(2);
    expect(eng.clearAll()).toBe(true);
    expect(st().drawings).toHaveLength(0);
    expect(eng.clearAll()).toBe(false);
    eng.undo();
    expect(st().drawings).toHaveLength(2);
  });
  it("Ctrl/Cmd+Z undoes; Ctrl/Cmd+Shift+Z and Ctrl+Y redo", () => {
    const { eng, st } = withTrend();
    expect(eng.keyDown({ key: "z", ctrlKey: true })).toBe(true);
    expect(st().drawings).toHaveLength(0);
    eng.keyDown({ key: "Z", metaKey: true, shiftKey: true });
    expect(st().drawings).toHaveLength(1);
    eng.keyDown({ key: "z", ctrlKey: true });
    eng.keyDown({ key: "y", ctrlKey: true });
    expect(st().drawings).toHaveLength(1);
  });
  it("plain letters are not taken, so typing elsewhere is never swallowed", () => {
    const { eng } = withTrend();
    for (const key of ["z", "y", "a", "Enter", "ArrowLeft"]) expect(eng.keyDown({ key }), key).toBe(false);
  });
  it("undoing the creation drops the selection that pointed at it", () => {
    const { eng, st } = withTrend();
    eng.undo();
    expect(st().selectedId).toBeNull();
  });
  it("canUndo and canRedo track the history", () => {
    const { eng, st } = setup();
    expect(st().canUndo || st().canRedo).toBe(false);
    eng.setTool("hline"); eng.pointerDown({ x: 10, y: 10 });
    expect(st().canUndo).toBe(true);
    eng.undo();
    expect(st().canRedo).toBe(true);
  });
});

describe("style", () => {
  it("colour and width apply to new drawings and to the selected one, each as an undo step", () => {
    const { eng, st } = withTrend();
    const orange = PALETTE[1].value;
    eng.setColor(orange);
    eng.setWidth(3);
    expect(st().drawings[0]).toMatchObject({ color: orange, width: 3 });
    eng.undo(); eng.undo();
    expect(st().drawings[0]).toMatchObject({ width: 2 });
    eng.setTool("hline");
    eng.pointerDown({ x: 50, y: 50 });
    expect(st().drawings[1].color).toBe(orange); // new drawings use the chosen colour
  });
  it("changing colour with nothing selected only sets the default", () => {
    const { eng, st } = setup();
    eng.setColor(PALETTE[2].value);
    expect(st().color).toBe(PALETTE[2].value);
    expect(st().canUndo).toBe(false);
  });
});

describe("magnet", () => {
  it("is passed to the conversion for new points and handle drags, never for moving a whole drawing", () => {
    const { eng, snaps, click, drag } = setup();
    eng.setMagnet(true);
    eng.setTool("trend");
    click(100, 300); click(300, 200);
    expect(snaps.length).toBeGreaterThan(0);
    expect(snaps.every(Boolean)).toBe(true);
    snaps.length = 0;
    drag({ x: 200, y: 250 }, { x: 250, y: 250 }); // body move
    expect(snaps.every((s) => s === false)).toBe(true);
    snaps.length = 0;
    drag({ x: 350, y: 200 }, { x: 370, y: 200 }); // handle drag: the end moved to x=350 with the body drag above
    expect(snaps.some(Boolean)).toBe(true);
  });
  it("is off by default", () => {
    const { eng, snaps, click } = setup();
    eng.setTool("hline"); click(10, 10);
    expect(snaps.every((s) => s === false)).toBe(true);
  });
});

describe("limits and loading", () => {
  it("stops adding at the per-symbol cap", () => {
    const { eng, st } = setup();
    for (let i = 0; i < MAX_PER_SYMBOL + 5; i++) { eng.setTool("hline"); eng.pointerDown({ x: 10 + (i % 700), y: 10 + (i % 400) }); }
    expect(st().drawings).toHaveLength(MAX_PER_SYMBOL);
  });
  it("load replaces everything and starts a fresh history", () => {
    const { eng, st } = withTrend();
    const saved: Drawing[] = [{ id: "s1", kind: "hline", points: [{ time: 1, price: 2 }], color: "#7daaff", width: 2 }];
    eng.load(saved);
    expect(st().drawings).toEqual(saved);
    expect(st().selectedId).toBeNull();
    expect(st().canUndo).toBe(false);
    expect(eng.busy).toBe(false);
  });
  it("the snapshot is stable between changes and new after one (for useSyncExternalStore)", () => {
    const { eng } = setup();
    expect(eng.getState()).toBe(eng.getState());
    const before = eng.getState();
    eng.setTool("rect");
    expect(eng.getState()).not.toBe(before);
  });
  it("busy reports a half-placed drawing and a drag", () => {
    const { eng, click } = setup();
    expect(eng.busy).toBe(false);
    eng.setTool("trend"); click(100, 300);
    expect(eng.busy).toBe(true);
    eng.cancel();
    expect(eng.busy).toBe(false);
  });
  it("tells a listener when drawings are EDITED, with the symbol, and never on load, hover or preview", () => {
    const { eng, click, drag } = setup();
    const seen: { n: number; key: string }[] = [];
    eng.onCommit((d, key) => seen.push({ n: d.length, key }));
    eng.load([], "AAPL");
    eng.pointerMove({ x: 10, y: 10 });
    expect(seen).toEqual([]); // loading and hovering are not edits
    eng.setTool("trend");
    click(100, 300);
    expect(seen).toEqual([]); // a half-placed drawing is not an edit either
    click(300, 200);
    expect(seen).toEqual([{ n: 1, key: "AAPL" }]);
    drag({ x: 300, y: 200 }, { x: 400, y: 100 }, 30);
    expect(seen).toHaveLength(2); // one notification for the whole drag, not thirty
    click(200, 250); // a click that moves nothing
    expect(seen).toHaveLength(2);
    eng.setColor("#f2b84b");
    eng.undo(); eng.redo();
    eng.deleteSelected(); eng.clearAll();
    expect(seen.length).toBeGreaterThanOrEqual(5);
  });
  it("restyling with nothing selected is not an edit", () => {
    const { eng } = setup();
    const spy = vi.fn();
    eng.onCommit(spy);
    eng.setColor("#f2b84b"); eng.setWidth(3);
    expect(spy).not.toHaveBeenCalled();
  });
  it("remembers which symbol its drawings belong to", () => {
    const { eng } = setup();
    expect(eng.key).toBe("");
    eng.load([], "AAPL");
    expect(eng.key).toBe("AAPL");
    eng.load([], "MSFT");
    expect(eng.key).toBe("MSFT");
  });
  it("leaving the chart clears the hover, but not in the middle of a drag", () => {
    const { eng, st } = withTrend();
    eng.keyDown({ key: "Escape" });
    eng.pointerMove({ x: 200, y: 250 });
    expect(st().hoverId).not.toBeNull();
    eng.pointerLeave();
    expect(st().hoverId).toBeNull();
    eng.pointerDown({ x: 200, y: 250 });
    eng.pointerLeave();
    expect(eng.busy).toBe(true); // the drag carries on
  });
  it("unsubscribing stops notifications", () => {
    const { eng } = setup();
    const spy = vi.fn();
    const off = eng.subscribe(spy);
    off();
    eng.setTool("rect");
    expect(spy).not.toHaveBeenCalled();
  });
});
