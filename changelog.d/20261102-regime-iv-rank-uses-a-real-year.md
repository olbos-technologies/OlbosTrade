## Unreleased — The regime no longer calls CRISIS on an ordinary VIX

- On 2026-11-02 the dashboard showed CRISIS and "Entries blocked" with VIX at
  19.4. The only flag was `IV_rank=90.1 >= 85.0`; the absolute VIX trigger is 40.
  The regime job ranked today's VIX within the last **60 sessions**, so after a
  calm stretch any ordinary VIX sat at the top of that narrow range. The
  classifier treats `iv_rank >= 85` as CRISIS on its own, and CRISIS blocks every
  equity and options entry. `IVSurfaceSnapshot.iv_rank` is documented as a 1-year
  rank; the job never supplied one.
- The rank is now computed over the last **252 sessions** of VIX. With fewer than
  126 sessions of VIX available it is not computed at all: the job logs a warning
  and uses the neutral rank 30, exactly what it already assumed when VIX data was
  unavailable, instead of ranking against a short, calm window.
- Unchanged: every CRISIS trigger and threshold (VIX >= 40, IV rank >= 85,
  backwardation with ATM IV >= 35%, vol-of-vol >= 0.08), the regime stabilizer,
  RSI/ADX/returns (still 60 sessions of SPY), and every other entry gate. A VIX
  spike to 40+ still produces CRISIS; tests cover it.
- Effect: the desk will now trade on days when VIX is moderately elevated but the
  rank was an artifact of a short window. It will not trade on days a real
  1-year rank is extreme.
- **Not changed, same flaw:** the options scanner computes its own IV rank from
  60 sessions of VIX (`options_scan_engine._compute_iv_rank`); it feeds skew
  adjustments and the `iv_rank` field on options signals, not the regime gate.
- No schema change, no new setting, no migration. Needs a backend rebuild.
