import React, { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import IndicatorPicker from "./IndicatorPicker";
import { DEFAULT_INDICATORS, INDICATORS, MAX_INDICATORS, INDICATOR_IDS, normaliseSelection, type IndicatorId } from "./indicators";

afterEach(cleanup);

/** The picker is controlled; this holds the state the way the real hook does. */
function Harness({ initial = [...DEFAULT_INDICATORS], onChange, onReset }: {
  initial?: IndicatorId[]; onChange?: (n: IndicatorId[]) => void; onReset?: () => void;
}) {
  const [sel, setSel] = useState<IndicatorId[]>(initial);
  return (
    <div data-testid="toolbar" style={{ overflow: "hidden" }}>
      <IndicatorPicker
        selected={sel}
        onChange={(n) => { const c = normaliseSelection(n); setSel(c); onChange?.(c); }}
        onReset={() => { setSel([...DEFAULT_INDICATORS]); onReset?.(); }}
        buttonClassName="x"
      />
      <input aria-label="elsewhere" />
    </div>
  );
}

const button = () => screen.getByRole("button", { name: "Indicators" });
const dialog = () => screen.getByRole("dialog", { name: "Chart indicators" });
const box = (name: string) => within(dialog()).getByRole("checkbox", { name }) as HTMLInputElement;
const open = () => fireEvent.click(button());

describe("IndicatorPicker", () => {
  it("is closed until the button is pressed, and says so", () => {
    render(<Harness />);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(button()).toHaveAttribute("aria-expanded", "false");
    expect(button()).toHaveAttribute("aria-haspopup", "dialog");
    open();
    expect(dialog()).toBeInTheDocument();
    expect(button()).toHaveAttribute("aria-expanded", "true");
  });

  it("lists every indicator once, grouped, with the current selection ticked", () => {
    render(<Harness />);
    open();
    const boxes = within(dialog()).getAllByRole("checkbox");
    expect(boxes).toHaveLength(INDICATORS.length);
    expect(within(dialog()).getByRole("group", { name: "On the price chart" })).toBeInTheDocument();
    expect(within(dialog()).getByRole("group", { name: "Panels below" })).toBeInTheDocument();
    for (const d of INDICATORS) expect(box(d.label).checked).toBe(DEFAULT_INDICATORS.includes(d.id));
    expect(within(dialog()).getByText(`${DEFAULT_INDICATORS.length} of ${MAX_INDICATORS}`)).toBeInTheDocument();
  });

  it("describes each indicator to assistive technology, in plain words", () => {
    render(<Harness />);
    open();
    for (const d of INDICATORS) {
      const id = box(d.label).getAttribute("aria-describedby")!;
      expect(document.getElementById(id)).toHaveTextContent(d.description);
    }
  });

  it("switching one on adds it, and off removes it, always reported in registry order", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    open();
    fireEvent.click(box("RSI"));
    expect(onChange).toHaveBeenLastCalledWith([...DEFAULT_INDICATORS, "rsi"]);
    fireEvent.click(box("EMA9"));
    expect(onChange).toHaveBeenLastCalledWith(["ma5", "ma10", "ma20", "ema9", "vwap", "volume", "macd", "rsi"]);
    fireEvent.click(box("MA5"));
    expect(onChange).toHaveBeenLastCalledWith(["ma10", "ma20", "ema9", "vwap", "volume", "macd", "rsi"]);
    expect(box("RSI").checked).toBe(true);
    expect(box("MA5").checked).toBe(false);
  });

  it("at the cap the unticked ones are disabled, the ticked ones are not, and it says why", () => {
    render(<Harness initial={INDICATOR_IDS.slice(0, MAX_INDICATORS) as IndicatorId[]} />);
    open();
    const off = INDICATORS.filter((d) => !INDICATOR_IDS.slice(0, MAX_INDICATORS).includes(d.id));
    expect(off.length).toBeGreaterThan(0);
    for (const d of off) expect(box(d.label)).toBeDisabled();
    for (const id of INDICATOR_IDS.slice(0, MAX_INDICATORS)) expect(box(INDICATORS.find((d) => d.id === id)!.label)).not.toBeDisabled();
    expect(within(dialog()).getByRole("status")).toHaveTextContent(`Most ${MAX_INDICATORS}`);
    fireEvent.click(box("MA5"));                                         // switch one off ...
    expect(box(off[0].label)).not.toBeDisabled();                        // ... and the rest can be added again
    expect(within(dialog()).queryByRole("status")).toBeNull();
  });

  it("can be emptied: candles only is a valid choice", () => {
    const onChange = vi.fn();
    render(<Harness initial={["ma5"]} onChange={onChange} />);
    open();
    fireEvent.click(box("MA5"));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("Reset puts the default back", () => {
    const onReset = vi.fn();
    render(<Harness initial={["rsi"]} onReset={onReset} />);
    open();
    fireEvent.click(within(dialog()).getByRole("button", { name: "Reset to default" }));
    expect(onReset).toHaveBeenCalledTimes(1);
    expect(box("MACD").checked).toBe(true);
    expect(box("RSI").checked).toBe(false);
  });

  it("Done closes it and puts focus back on the button", () => {
    render(<Harness />);
    open();
    fireEvent.click(within(dialog()).getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(button()).toHaveFocus();
  });

  it("Escape closes it and puts focus back on the button", () => {
    render(<Harness />);
    open();
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(button()).toHaveFocus();
  });

  it("a press outside closes it; a press inside, or on the button, does not by itself", () => {
    render(<Harness />);
    open();
    fireEvent.pointerDown(box("RSI"));
    expect(dialog()).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByLabelText("elsewhere"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("pressing the button again closes it", () => {
    render(<Harness />);
    open();
    fireEvent.click(button());
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("a resize closes it, because its position would be stale", () => {
    render(<Harness />);
    open();
    fireEvent(window, new Event("resize"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps Tab inside while open: forward from the last control wraps to the first, and back from the first to the last", () => {
    render(<Harness />);
    open();
    const items = Array.from(dialog().querySelectorAll<HTMLElement>("input:not([disabled]), button:not([disabled])"));
    const first = items[0], last = items[items.length - 1];
    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(first).toHaveFocus();
    first.focus();
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
    expect(last).toHaveFocus();
  });

  it("moves focus into the panel when it opens", () => {
    render(<Harness />);
    open();
    expect(dialog()).toHaveFocus();
  });

  it("renders outside the toolbar, so a toolbar that clips or scrolls cannot cut it off", () => {
    render(<Harness />);
    open();
    expect(screen.getByTestId("toolbar").contains(dialog())).toBe(false);
    expect(dialog().parentElement).toBe(document.body);
  });

  it("says indicators are not signals", () => {
    render(<Harness />);
    open();
    expect(dialog()).toHaveTextContent("not signals");
  });

  it("cleans up its listeners when it closes: Escape does nothing afterwards", () => {
    render(<Harness />);
    open();
    fireEvent.click(within(dialog()).getByRole("button", { name: "Done" }));
    button().blur();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(button()).not.toHaveFocus();
  });
});
