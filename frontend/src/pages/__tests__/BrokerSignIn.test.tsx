import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, it, expect, beforeEach } from "vitest";
const { providers, connections, start, disconnect } = vi.hoisted(() => ({
  providers: vi.fn(), connections: vi.fn(), start: vi.fn(), disconnect: vi.fn(),
}));
vi.mock("../../api/client", () => ({ api: {
  brokerOAuthProviders: providers, brokerOAuthConnections: connections,
  startBrokerOAuth: start, disconnectBrokerOAuth: disconnect,
} }));
import BrokerSignIn from "../account/BrokerSignIn";
beforeEach(() => {
  vi.clearAllMocks();
  providers.mockResolvedValue({providers:[
    {id:"alpaca",name:"Alpaca",available:true,note:"Paper read-only"},
    {id:"ibkr",name:"Interactive Brokers",available:false,note:"Coming soon"},
    {id:"schwab",name:"Charles Schwab (thinkorswim)",available:false,note:"Coming soon"},
  ]});
  connections.mockResolvedValue({connections:[]});
});
it("offers broker login and explicitly disables unsupported brokers", async () => {
  render(<BrokerSignIn />);
  expect(await screen.findByRole("button",{name:"Sign in with Alpaca"})).toBeEnabled();
  expect(screen.getAllByRole("button",{name:"Coming soon"}).every(b=>(b as HTMLButtonElement).disabled)).toBe(true);
  expect(screen.getByText(/Automated execution unavailable/)).toBeInTheDocument();
  expect(document.querySelector('input[type="password"]')).toBeNull();
});
it("shows failed reads without claiming there are no accounts", async () => {
  connections.mockRejectedValue(new Error());
  render(<BrokerSignIn />);
  expect(await screen.findByRole("alert")).toHaveTextContent("unavailable");
  expect(screen.queryByText("No accounts connected through broker sign-in.")).toBeNull();
});
it("rejects arbitrary authorization URLs", async () => {
  start.mockResolvedValue({authorization_url:"https://evil.example/steal"});
  render(<BrokerSignIn />);
  fireEvent.click(await screen.findByRole("button",{name:"Sign in with Alpaca"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not start");
});
it("disconnects a specific account and refreshes", async () => {
  connections.mockResolvedValue({connections:[{id:"a",account_last4:"1234",connected_at:"2026-01-01T00:00:00Z"}]});
  vi.spyOn(window,"confirm").mockReturnValue(true);
  render(<BrokerSignIn />);
  fireEvent.click(await screen.findByRole("button",{name:"Disconnect broker sign-in"}));
  await waitFor(()=>expect(disconnect).toHaveBeenCalledWith("a"));
  expect(screen.getByText(/Current account data has not been synced/)).toBeInTheDocument();
});
