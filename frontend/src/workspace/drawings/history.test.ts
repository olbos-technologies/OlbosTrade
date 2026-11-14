import { describe, expect, it } from "vitest";
import { MAX_HISTORY, canRedo, canUndo, checkpoint, commit, createHistory, redo, replacePresent, reset, undo } from "./history";

describe("history", () => {
  it("commit, undo and redo walk through the steps", () => {
    let h = createHistory<number[]>([]);
    h = commit(h, [1]); h = commit(h, [1, 2]); h = commit(h, [1, 2, 3]);
    expect(h.present).toEqual([1, 2, 3]);
    h = undo(h); expect(h.present).toEqual([1, 2]);
    h = undo(h); expect(h.present).toEqual([1]);
    h = redo(h); expect(h.present).toEqual([1, 2]);
    h = redo(h); h = redo(h);
    expect(h.present).toEqual([1, 2, 3]);
    expect(canRedo(h)).toBe(false);
    expect(canUndo(h)).toBe(true);
  });
  it("a new commit after an undo discards the redo branch", () => {
    let h = commit(commit(createHistory<number[]>([]), [1]), [1, 2]);
    h = undo(h);
    h = commit(h, [9]);
    expect(h.future).toEqual([]);
    expect(redo(h)).toBe(h);
  });
  it("undo and redo at the ends do nothing", () => {
    const h = createHistory<number[]>([]);
    expect(undo(h)).toBe(h);
    expect(redo(h)).toBe(h);
    expect(canUndo(h)).toBe(false);
  });
  it("committing the same value records nothing", () => {
    const v: number[] = [];
    const h = createHistory(v);
    expect(commit(h, v)).toBe(h);
  });
  it("never mutates the history it was given", () => {
    const h = createHistory<number[]>([]);
    const frozen = Object.freeze({ ...h, past: Object.freeze([...h.past]), future: Object.freeze([...h.future]) });
    expect(() => { commit(frozen, [1]); undo(frozen); redo(frozen); }).not.toThrow();
    expect(frozen.present).toEqual([]);
  });
  it("a drag is ONE step: live replaces, one checkpoint at the end", () => {
    const base = [1];
    let h = commit(createHistory<number[]>([]), base);
    for (let i = 0; i < 50; i++) h = replacePresent(h, [1, i]);
    expect(h.past).toHaveLength(1); // still just the step before the drag
    h = checkpoint(h, base);
    expect(h.past).toHaveLength(2);
    h = undo(h);
    expect(h.present).toBe(base);
  });
  it("a drag that changed nothing records no step", () => {
    const base = [1];
    const h = commit(createHistory<number[]>([]), base);
    expect(checkpoint(h, base)).toBe(h);
  });
  it("is capped, keeping the most recent steps", () => {
    let h = createHistory(0);
    for (let i = 1; i <= MAX_HISTORY + 30; i++) h = commit(h, i);
    expect(h.past).toHaveLength(MAX_HISTORY);
    expect(h.past[h.past.length - 1]).toBe(MAX_HISTORY + 29);
  });
  it("reset clears everything", () => {
    const h = reset([5]);
    expect(h.present).toEqual([5]);
    expect(canUndo(h) || canRedo(h)).toBe(false);
  });
});
