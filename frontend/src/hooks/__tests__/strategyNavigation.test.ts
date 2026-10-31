import { describe, it, expect } from 'vitest';
import { NAV_MODEL_LEGACY, NAV_MODEL_V2, groupIdForKey } from '../../utils/navModels';
import { filterNavForDisplay } from '../../utils/navLabels';
describe.each([['legacy',NAV_MODEL_LEGACY],['v2',NAV_MODEL_V2]] as const)('%s strategy navigation',(_,model)=>{
  // SUPERSEDED, deliberately. This asserted five daily Strategies destinations
  // (Alpha Edge, Live Signals, History, Strategy Library, Health) with options
  // signals left in advanced Research. The product owner has since restated the
  // goals: a dense terminal, equity AND options signals, automated execution.
  // Options signals therefore belong ON the rail, and the four read-only
  // surfaces belong behind Advanced. All five keys still exist and are still
  // reachable — see navGoalAlignment.test.ts, which owns the rail's size.
  it('puts both asset classes on the rail and research behind Advanced',()=>{
    const simple=filterNavForDisplay(model,false,'dashboard');
    expect(simple.find(g=>g.id==='strat')?.children?.map(c=>c.key)).toEqual(['equity','options:signals']);
    expect(simple.some(g=>g.id==='lab')).toBe(false);
    for(const key of ['crypto:signals','strat:research','strat:calendar','strat:builder','strat:alerts']) expect(groupIdForKey(key,model)).toBe('lab');
    for(const key of ['options:chain','scan','options:income','options:flow']) expect(groupIdForKey(key,model)).toBe('trade');
    // Demoted, never deleted: each still resolves to the Strategies group.
    for(const key of ['strat:alpha-edge','strat:signal-history','strat:cards','strat:health']) expect(groupIdForKey(key,model)).toBe('strat');
  });
  it('keeps a deep-linked research page visible with Advanced closed',()=>{
    expect(filterNavForDisplay(model,false,'crypto:signals').find(g=>g.id==='lab')?.children?.some(c=>c.key==='crypto:signals')).toBe(true);
  });
});
