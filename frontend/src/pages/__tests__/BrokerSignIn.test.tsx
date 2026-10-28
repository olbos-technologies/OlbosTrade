import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, it, expect, beforeEach } from "vitest";
const { providers, connections, start, disconnect, verify } = vi.hoisted(() => ({
  providers: vi.fn(), connections: vi.fn(), start: vi.fn(), disconnect: vi.fn(), verify: vi.fn(),
}));
vi.mock("../../api/client", () => ({ api: {
  brokerOAuthProviders: providers, brokerOAuthConnections: connections,
  startBrokerOAuth: start, disconnectBrokerOAuth: disconnect, verifyBrokerOAuth: verify,
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
  expect(screen.getByText(/Check account verifies access only/)).toBeInTheDocument();
});

it("checks only the selected account and never enables execution", async () => {
  connections.mockResolvedValue({connections:[{id:"a",account_last4:"1111",connected_at:"2026-01-01T00:00:00Z"}, {id:"b",account_last4:"2222",connected_at:"2026-01-01T00:00:00Z"}]});
  verify.mockResolvedValue({id:"b",health:"verified",last_checked_at:"2026-01-01T00:01:00Z",status:"active",execution_enabled:false,positions_reconciled:false});
  render(<BrokerSignIn />);
  const buttons = await screen.findAllByRole("button",{name:"Check account"});
  fireEvent.click(buttons[1]);
  await screen.findByText(/Last account check succeeded/);
  expect(verify).toHaveBeenCalledWith("b");
  expect(screen.getAllByText("Read-only · Trading off")).toHaveLength(2);
});
it("clears a previous successful check when verification fails", async () => {
  connections.mockResolvedValue({connections:[{id:"a",account_last4:"1111",connected_at:"2026-01-01T00:00:00Z",health:"verified",last_checked_at:"2026-01-01T00:01:00Z"}]});
  verify.mockRejectedValue(new Error());
  render(<BrokerSignIn />);
  await screen.findByText(/Last account check succeeded/);
  fireEvent.click(screen.getByRole("button",{name:"Check account"}));
  await screen.findByText(/Account check unavailable/);
  expect(screen.queryByText(/Last account check succeeded/)).toBeNull();
});
it("does not offer verification when reconnection is required", async () => {
  connections.mockResolvedValue({connections:[{id:"a",account_last4:"1111",connected_at:"2026-01-01T00:00:00Z",health:"identity_mismatch",status:"reconnect_required"}]});
  render(<BrokerSignIn />);
  expect(await screen.findByRole("button",{name:"Check account"})).toBeDisabled();
  expect(screen.getByText(/Account mismatch/)).toBeInTheDocument();
  expect(screen.getByRole("button",{name:"Disconnect broker sign-in"})).toBeEnabled();
});

it.each([{id:"other-account"}, {execution_enabled:true}, {positions_reconciled:true}])("rejects a mismatched or authority-bearing verification response: %j", async override => {
  connections.mockResolvedValue({connections:[{id:"a",account_last4:"1111",connected_at:"2026-01-01T00:00:00Z",health:"verified",last_checked_at:"2026-01-01T00:01:00Z"}]});
  verify.mockResolvedValue({id:"a",health:"verified",last_checked_at:"2026-01-01T00:02:00Z",status:"active",execution_enabled:false,positions_reconciled:false,...override});
  render(<BrokerSignIn />);
  fireEvent.click(await screen.findByRole("button",{name:"Check account"}));
  await screen.findByText(/Account check unavailable/);
  expect(screen.queryByText(/Last account check succeeded/)).toBeNull();
});
