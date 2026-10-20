import { describe, expect, it, vi, afterEach } from "vitest";
import { readExperienceLevel, EXPERIENCE_LEVEL_KEY } from "../WelcomeBanner";

describe("experience preference", () => {
  afterEach(() => vi.restoreAllMocks());
  it("defaults to beginner when no saved preference exists", () => {
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue(null);
    expect(readExperienceLevel()).toBe("beginner");
  });
  it.each(["beginner", "intermediate", "pro"] as const)("accepts %s", (level) => {
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue(level);
    expect(readExperienceLevel()).toBe(level);
    expect(localStorage.getItem).toHaveBeenCalledWith(EXPERIENCE_LEVEL_KEY);
  });
  it("ignores unsupported values", () => {
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue("autopilot");
    expect(readExperienceLevel()).toBe("beginner");
  });
  it("handles denied browser storage", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
    expect(readExperienceLevel()).toBe("beginner");
  });
});
