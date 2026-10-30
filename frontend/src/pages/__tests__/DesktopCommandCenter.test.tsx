import React from 'react';
import {afterEach,beforeEach,describe,it,expect,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import DesktopCommandCenter,{closedPnlPoints} from '../DesktopCommandCenter';
import {api} from '../../api/client';
vi.mock('../../api/client',()=>({api:{getExecutionMode:vi.fn(),getTradeDeskKillSwitch:vi.fn(),getPositions:vi.fn(),getPendingApprovals:vi.fn(),getTradeHistory:vi.fn()}}));
beforeEach(()=>{
 vi.clearAllMocks();
 vi.mocked(api.getExecutionMode).mockResolvedValue({mode:'manual',persistence:'confirmed'});
 vi.mocked(api.getTradeDeskKillSwitch).mockResolvedValue({engaged:false});
 vi.mocked(api.getPositions).mockResolvedValue({positions:[]});
 vi.mocked(api.getPendingApprovals).mockResolvedValue({pending:[]});
 vi.mocked(api.getTradeHistory).mockResolvedValue({trades:[]});
 vi.stubGlobal('fetch',vi.fn(async(path:string)=>({ok:true,json:async()=>path.includes('dashboard')?{total_equity:124532.88,day_pnl:-25,total_pnl:225}:path.includes('broker')?{broker:'ibkr',paper_mode:true,status:'connected'}:{}})));
});
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
describe('Desktop performance truth',()=>{
 it('sorts actual closes, retains losses and leaves the input unchanged',()=>{
  const rows=[{status:'closed',exit_date:'2026-10-02',pnl:-80},{status:'closed',exit_date:'2026-10-01',pnl:50}];
  expect(closedPnlPoints(rows).map(p=>p.value)).toEqual([50,-30]);
  expect(rows[0].pnl).toBe(-80);
 });
 it('rejects incomplete or non-finite history instead of inventing a curve',()=>{
  expect(()=>closedPnlPoints([{status:'closed',exit_date:'bad',pnl:1}])).toThrow();
  expect(()=>closedPnlPoints([{status:'closed',exit_date:'2026-10-01',pnl:NaN}])).toThrow();
  expect(()=>closedPnlPoints([{status:'open',exit_date:'2026-10-01',pnl:0}])).toThrow();
  expect(closedPnlPoints([])).toEqual([]);
 });
 it('shows unavailable coverage and risk budget without a false zero meter',async()=>{
  const {container}=render(<DesktopCommandCenter/>);
  expect(await screen.findByText('$124,532.88')).toBeInTheDocument();
  expect(screen.getByText('−$25.00')).toBeInTheDocument();
  expect(container.querySelector('progress')).toBeNull();
  expect(screen.getByText('No closed trades in this range.')).toBeInTheDocument();
  expect(screen.getAllByText('Unverified',{exact:false}).length).toBeGreaterThan(0);
 });
 it('removes a previously successful balance on refresh failure',async()=>{
  render(<DesktopCommandCenter/>);
  await screen.findByText('$124,532.88');
  vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new Error('offline')));
  vi.mocked(api.getTradeHistory).mockRejectedValue(new Error('offline'));
  fireEvent.click(screen.getByRole('button',{name:'Refresh command center'}));
  await waitFor(()=>expect(screen.queryByText('$124,532.88')).not.toBeInTheDocument());
  expect(await screen.findByText('Trade performance unavailable. Refresh to retry.')).toBeInTheDocument();
  expect(screen.queryByText('$0.00')).not.toBeInTheDocument();
 });
});
