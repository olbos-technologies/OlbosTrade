// C++ twin of simulate() in scripts/vwap_reversion_backtest.py.
//
// Research only: no broker, no orders, no I/O. It exists to make large
// parameter searches fast. The Python version is the REFERENCE; this file must
// produce byte-for-byte the same trades, and tests/test_vwap_reversion_cpp.py
// fails if it does not. If you change a rule, change the Python first, then
// make this match, then let the differential test confirm it.
//
// Every floating-point expression below is written in the same order as its
// Python counterpart, and the build uses -ffp-contract=off, so the results are
// identical rather than merely close. Do not "simplify" an expression here
// without making the same change in Python.

#include <cmath>
#include <cstdint>

namespace {

constexpr int FLATTEN_MIN = 15 * 60 + 55;  // flat by 15:55 ET
constexpr int BAR_MIN = 5;

enum Reason : int32_t { STOP_GAP = 0, STOP = 1, TARGET = 2, FLATTEN = 3, TIME = 4 };

// prm layout (all doubles), kept in step with vwap_reversion_cpp.PARAM_ORDER:
//  0 z_entry  1 stop_pct  2 target_pct  3 time_stop_min  4 trend_ema_slope(1/0)
//  5 atr_lo  6 atr_hi  7 vol_min  8 be_trigger_pct  9 slippage  10 commission
//  11 cost_mult  12 tick  13 allow_long  14 allow_short  15 n_windows
//  16.. window (lo, hi) pairs, up to 4
struct Prm {
    double z_entry, stop_pct, target_pct, time_stop_min, trend, atr_lo, atr_hi, vol_min,
        be_trigger_pct, slippage, commission, cost_mult, tick, allow_long, allow_short;
    int n_windows;
    const double* windows;
};

Prm parse(const double* p) {
    return Prm{p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], p[9], p[10], p[11],
               p[12], p[13], p[14], static_cast<int>(p[15]), p + 16};
}

}  // namespace

extern "C" {

// Returns the number of trades written, or -1 if max_out was too small.
int vwap_simulate(int n_days, const int32_t* day_start, const int32_t* day_end,
                  const double* o, const double* h, const double* l, const double* c,
                  const double* z, const double* atr_ratio, const double* vol_ratio,
                  const double* h_slope, const int32_t* end_min, const int64_t* end_epoch_min,
                  const double* prm_raw, int max_out, int32_t* out_day, int32_t* out_j,
                  int32_t* out_k, int32_t* out_side, double* out_entry, double* out_exit,
                  double* out_net, double* out_r, int32_t* out_reason) {
    const Prm p = parse(prm_raw);
    const double slip = p.slippage * p.cost_mult;
    const double comm = p.commission * p.cost_mult;
    int n_out = 0;

    for (int d = 0; d < n_days; ++d) {
        const int s = day_start[d];
        const int n = day_end[d] - s;

        int i = 0;
        while (i < n - 1) {
            const int gi = s + i;

            // ── signal at the completed bar i (mirror of _signal) ──────────────────
            int side = 0;
            const double zz = z[gi];
            if (std::isfinite(zz) && std::isfinite(atr_ratio[gi]) && std::isfinite(vol_ratio[gi]) &&
                p.atr_lo <= atr_ratio[gi] && atr_ratio[gi] <= p.atr_hi &&
                vol_ratio[gi] >= p.vol_min) {
                bool in_window = false;
                for (int w = 0; w < p.n_windows; ++w) {
                    if (p.windows[2 * w] <= end_min[gi] && end_min[gi] <= p.windows[2 * w + 1]) {
                        in_window = true;
                        break;
                    }
                }
                if (in_window) {
                    if (zz <= -p.z_entry) side = 1;
                    else if (zz >= p.z_entry) side = -1;
                    if (side == 1 && p.allow_long == 0.0) side = 0;
                    if (side == -1 && p.allow_short == 0.0) side = 0;
                    if (side != 0 && p.trend == 1.0) {
                        const double slope = h_slope[gi];
                        if (!std::isfinite(slope) || (side == 1 && slope < 0) ||
                            (side == -1 && slope > 0))
                            side = 0;
                    }
                }
            }

            const int j = i + 1;
            if (side == 0 || end_min[s + j] >= FLATTEN_MIN) {
                ++i;
                continue;
            }

            // ── the trade (mirror of _run_trade) ───────────────────────────────────
            const double entry = o[s + j] + side * slip;
            double stop = entry * (1 - side * p.stop_pct / 100);
            const double target = entry * (1 + side * p.target_pct / 100);
            const double be_level = entry * (1 + side * p.be_trigger_pct / 100);
            const int64_t entry_epoch = end_epoch_min[s + j] - BAR_MIN;
            bool be_done = false;
            bool found = false;
            int k = j;
            double exit_px = 0.0;
            int32_t reason = FLATTEN;

            for (k = j; k < n; ++k) {
                const int g = s + k;
                if (side == 1) {
                    if (o[g] <= stop) { exit_px = o[g] - slip; reason = STOP_GAP; found = true; break; }
                    if (l[g] <= stop) { exit_px = stop - slip; reason = STOP; found = true; break; }
                    if (h[g] >= target + p.tick) { exit_px = target; reason = TARGET; found = true; break; }
                    if (!be_done && h[g] >= be_level) { be_done = true; stop = entry + 2 * comm; }
                } else {
                    if (o[g] >= stop) { exit_px = o[g] + slip; reason = STOP_GAP; found = true; break; }
                    if (h[g] >= stop) { exit_px = stop + slip; reason = STOP; found = true; break; }
                    if (l[g] <= target - p.tick) { exit_px = target; reason = TARGET; found = true; break; }
                    if (!be_done && l[g] <= be_level) { be_done = true; stop = entry - 2 * comm; }
                }
                if (end_min[g] >= FLATTEN_MIN) { exit_px = c[g] - side * slip; reason = FLATTEN; found = true; break; }
                if (static_cast<double>(end_epoch_min[g] - entry_epoch) >= p.time_stop_min) {
                    exit_px = c[g] - side * slip; reason = TIME; found = true; break;
                }
            }
            if (!found) {  // ran off the end of the day without a flatten bar
                k = n - 1;
                exit_px = c[s + n - 1] - side * slip;
                reason = FLATTEN;
            }

            const double per_share = (exit_px - entry) * side - 2 * comm;
            const double net_pct = per_share / entry * 100.0;

            if (n_out >= max_out) return -1;
            out_day[n_out] = d;
            out_j[n_out] = s + j;
            out_k[n_out] = s + k;
            out_side[n_out] = side;
            out_entry[n_out] = entry;
            out_exit[n_out] = exit_px;
            out_net[n_out] = net_pct;
            out_r[n_out] = net_pct / p.stop_pct;
            out_reason[n_out] = reason;
            ++n_out;
            i = k + 1;
        }
    }
    return n_out;
}

}  // extern "C"
