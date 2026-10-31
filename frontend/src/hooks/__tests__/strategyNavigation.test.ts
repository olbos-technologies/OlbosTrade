import { describe, it, expect } from 'vitest';
import { NAV_MODEL_LEGACY, NAV_MODEL_V2, groupIdForKey } from '../../utils/navModels';
import { filterNavForDisplay } from '../../utils/navLabels';
describe.each([['legacy',NAV_MODEL_LEGACY],['v2',NAV_MODEL_V2]] as const)('%s strategy navigation',(_,model)=>{
  it('keeps the daily signals loop short in the sidebar',()=>{
    const simple=filterNavForDisplay(model,true,'dashboard');
    expect(simple.find(g=>g.id==='strat')?.label).toBe('Signals');
    expect(simple.find(g=>g.id==='strat')?.children?.map(c=>c.key)).toEqual(['strat:alpha-edge','equity','strat:signal-history']);
    expect(simple.some(g=>g.id==='lab')).toBe(false);
    for(const key of ['crypto:signals','strat:research','strat:calendar','strat:builder','strat:alerts']) expect(groupIdForKey(key,model)).toBe('lab');
    for(const key of ['options:chain','scan','options:income','options:flow']) {
      expect(groupIdForKey(key,model)).toBe(model===NAV_MODEL_V2?'lab':'trade');
    }
  });
  it('keeps a deep-linked research page visible even when Research is sidebar-hidden',()=>{
    expect(filterNavForDisplay(model,true,'crypto:signals').find(g=>g.id==='lab')?.children?.some(c=>c.key==='crypto:signals')).toBe(true);
  });
});
