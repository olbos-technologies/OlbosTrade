/**
 * Undo and redo for a value that changes in steps. Pure: every function returns a
 * new history and never mutates the one it was given.
 *
 * A drag changes the value many times a second. Those intermediate states replace
 * the present without being recorded; only the state before the drag is pushed to
 * the past, once, when the drag ends. So one drag is one undo step.
 */

export interface History<T> {
  past: readonly T[];
  present: T;
  future: readonly T[];
}

export const MAX_HISTORY = 100;

export function createHistory<T>(present: T): History<T> {
  return { past: [], present, future: [] };
}

/** Record a new present, remembering the old one. No-op when nothing changed. */
export function commit<T>(h: History<T>, next: T): History<T> {
  if (Object.is(next, h.present)) return h;
  return { past: [...h.past, h.present].slice(-MAX_HISTORY), present: next, future: [] };
}

/** Change the present WITHOUT recording a step (a live preview during a drag). */
export function replacePresent<T>(h: History<T>, next: T): History<T> {
  return Object.is(next, h.present) ? h : { ...h, present: next };
}

/**
 * End of a drag: the present is the result and `base` is what it was before the
 * drag began. Records one step, or none if the drag changed nothing.
 */
export function checkpoint<T>(h: History<T>, base: T): History<T> {
  if (Object.is(base, h.present)) return h;
  return { past: [...h.past, base].slice(-MAX_HISTORY), present: h.present, future: [] };
}

export const canUndo = <T,>(h: History<T>) => h.past.length > 0;
export const canRedo = <T,>(h: History<T>) => h.future.length > 0;

export function undo<T>(h: History<T>): History<T> {
  if (!h.past.length) return h;
  const previous = h.past[h.past.length - 1];
  return { past: h.past.slice(0, -1), present: previous, future: [h.present, ...h.future] };
}

export function redo<T>(h: History<T>): History<T> {
  if (!h.future.length) return h;
  const [next, ...rest] = h.future;
  return { past: [...h.past, h.present].slice(-MAX_HISTORY), present: next, future: rest };
}

/** Start over with a new present and no history (a different symbol was loaded). */
export function reset<T>(present: T): History<T> {
  return createHistory(present);
}
