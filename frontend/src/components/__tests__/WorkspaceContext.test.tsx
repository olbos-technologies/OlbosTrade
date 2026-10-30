import React from 'react';
import {afterEach,beforeEach,it,expect,vi} from 'vitest';
import {act,cleanup,render,screen} from '@testing-library/react';
import WorkspaceContext from '../WorkspaceContext';
import {api} from '../../api/client';
vi.mock('../../api/client',()=>({api:{getExecutionMode:vi.fn(),getTradeDeskKillSwitch:vi.fn(),getPendingApprovals:vi.fn()}}));
beforeEach(()=>{vi.clearAllMocks();vi.mocked(api.getExecutionMode).mockResolvedValue({mode:'manual',persistence:'confirmed'});vi.mocked(api.getTradeDeskKillSwitch).mockResolvedValue({engaged:false});vi.mocked(api.getPendingApprovals).mockResolvedValue({pending:[]});vi.stubGlobal('fetch',vi.fn(async(path:string)=>({ok:true,json:async()=>path.includes('/broker')?{broker:'ibkr',paper_mode:true,status:'connected'}:path.includes('/guardrails')?{trading_allowed:true}:{available:true,source:'refreshed',unprotected_is_reliable:true,positions_without_stop:[]}})));});
afterEach(()=>{cleanup();vi.useRealTimers();vi.unstubAllGlobals();});
it('states confirmed readings without inventing audit or model status',async()=>{
 render(<WorkspaceContext page="account:brokers"/>);
 expect(await screen.findByText('CONNECTED')).toBeInTheDocument();
 expect(screen.getByText('MANUAL')).toBeInTheDocument();
 expect(screen.queryByText('Audit logging active')).not.toBeInTheDocument();
 expect(screen.getByText(/No exceptions reported by these reads/)).toBeInTheDocument();
});
it('failed polls replace previous confirmed readings with unknowns',async()=>{
 vi.useFakeTimers();
 await act(async()=>{render(<WorkspaceContext page="dashboard"/>);});
 expect(screen.getByText('CONNECTED')).toBeInTheDocument();
 vi.mocked(api.getExecutionMode).mockRejectedValue(new Error('offline'));
 vi.mocked(api.getTradeDeskKillSwitch).mockRejectedValue(new Error('offline'));
 vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new Error('offline')));
 await act(async()=>{await vi.advanceTimersByTimeAsync(15000);});
 expect(screen.queryByText('CONNECTED')).not.toBeInTheDocument();
 expect(screen.queryByText('MANUAL')).not.toBeInTheDocument();
 expect(screen.getByText('Broker unverified →')).toBeInTheDocument();
 expect(screen.getAllByText('UNVERIFIED').length).toBeGreaterThan(2);
});
