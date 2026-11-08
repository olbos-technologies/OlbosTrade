import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Button } from "../ui";

describe("Button selector", () => {
  it("is flat only when asked, and keeps the raised key for actions", () => {
    render(<><Button selector active>Positions</Button><Button>Close</Button><Button danger>Halt</Button></>);
    expect(screen.getByText("Positions").className).toContain("btn-t--selector");
    expect(screen.getByText("Positions").className).toContain("active");
    expect(screen.getByText("Close").className).not.toContain("btn-t--selector");
    expect(screen.getByText("Halt").className).not.toContain("btn-t--selector");
  });
});
