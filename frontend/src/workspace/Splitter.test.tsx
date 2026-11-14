import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import Splitter from "./Splitter";
import { KEY_STEP, KEY_STEP_LARGE, LIMITS } from "./layout";

afterEach(cleanup);

// jsdom has no PointerEvent, so clientX, button and pointerType would be dropped from
// the events fired below. This stand-in carries them, as a browser's event does.
class TestPointerEvent extends MouseEvent {
  pointerId: number; pointerType: string;
  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 0;
    this.pointerType = init.pointerType ?? "mouse";
  }
}
beforeAll(() => { (window as unknown as { PointerEvent: unknown }).PointerEvent = TestPointerEvent; });

function setup(over: Partial<React.ComponentProps<typeof Splitter>> = {}) {
  const onChange = vi.fn(), onReset = vi.fn();
  const { container } = render(<Splitter orientation="vertical" label="Resize chart" which="chartFrac" value={0.7}
    getTotalPx={() => 1000} onChange={onChange} onReset={onReset} {...over} />);
  // The splitter is the only element in its own render, so scope to it.
  return { el: container.querySelector<HTMLElement>('[role="separator"]')!, onChange, onReset };
}

describe("Splitter", () => {
  it("is a focusable separator with a value, range and orientation", () => {
    const { el } = setup();
    expect(el).toHaveAttribute("role", "separator");
    expect(el).toHaveAttribute("aria-label", "Resize chart");
    expect(el).toHaveAttribute("aria-orientation", "vertical");
    expect(el).toHaveAttribute("aria-valuenow", "70");
    expect(el).toHaveAttribute("aria-valuemin", String(Math.round(LIMITS.chartFrac.min * 100)));
    expect(el).toHaveAttribute("aria-valuemax", String(Math.round(LIMITS.chartFrac.max * 100)));
    expect(el).toHaveAttribute("tabindex", "0");
  });
  it("arrow keys step, Shift steps further, and the bar never leaves its limits", () => {
    const { el, onChange } = setup();
    fireEvent.keyDown(el, { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith(expect.closeTo(0.7 + KEY_STEP));
    fireEvent.keyDown(el, { key: "ArrowLeft", shiftKey: true });
    expect(onChange).toHaveBeenLastCalledWith(expect.closeTo(0.7 - KEY_STEP_LARGE));
  });
  it("at the upper limit a further step stays at the limit", () => {
    const edge = setup({ value: LIMITS.chartFrac.max });
    fireEvent.keyDown(edge.el, { key: "ArrowRight" });
    expect(edge.onChange).toHaveBeenLastCalledWith(LIMITS.chartFrac.max);
  });
  it("a horizontal splitter uses Up and Down, and ignores Left and Right", () => {
    const { el: h, onChange } = setup({ orientation: "horizontal", which: "topFrac", value: 0.6, label: "Resize rows" });
    fireEvent.keyDown(h, { key: "ArrowDown" });
    expect(onChange).toHaveBeenLastCalledWith(expect.closeTo(0.6 + KEY_STEP));
    onChange.mockClear();
    fireEvent.keyDown(h, { key: "ArrowRight" });
    expect(onChange).not.toHaveBeenCalled();
  });
  it("Enter, Home and double-click reset; unrelated keys do nothing", () => {
    const { el, onReset, onChange } = setup();
    fireEvent.keyDown(el, { key: "Enter" });
    fireEvent.keyDown(el, { key: "Home" });
    fireEvent.doubleClick(el);
    expect(onReset).toHaveBeenCalledTimes(3);
    fireEvent.keyDown(el, { key: "a" });
    expect(onChange).not.toHaveBeenCalled();
  });
  it("dragging reports the new share from the pixel distance, using the region size at drag start", () => {
    const { el, onChange } = setup();
    fireEvent.pointerDown(el, { clientX: 700, pointerId: 1, button: 0 });
    fireEvent.pointerMove(el, { clientX: 800, pointerId: 1 });
    expect(onChange).toHaveBeenLastCalledWith(expect.closeTo(0.8));
    fireEvent.pointerMove(el, { clientX: 5000, pointerId: 1 });
    expect(onChange).toHaveBeenLastCalledWith(LIMITS.chartFrac.max);
    fireEvent.pointerUp(el, { pointerId: 1 });
    onChange.mockClear();
    fireEvent.pointerMove(el, { clientX: 900, pointerId: 1 });
    expect(onChange).not.toHaveBeenCalled(); // no drag in progress after release
  });
  it("ignores a right-button press", () => {
    const { el, onChange } = setup();
    fireEvent.pointerDown(el, { clientX: 700, pointerId: 1, button: 2, pointerType: "mouse" });
    fireEvent.pointerMove(el, { clientX: 800, pointerId: 1 });
    expect(onChange).not.toHaveBeenCalled();
  });
});
