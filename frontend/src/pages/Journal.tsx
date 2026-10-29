import React, { useEffect, useState } from "react";
import { api } from "../api/client";
import { Panel, StatTile, Badge, Button } from "../components/ui";

interface Entry {
  id: string;
  trade_id: string | null;
  strategy: string | null;
  underlying: string | null;
  pre_trade_thesis: string;
  confidence_level: number;
  market_context: string;
  post_trade_notes: string | null;
  followed_rules: boolean | null;
  exit_felt_right: boolean | null;
  tags: string[];
  loss_category: string | null;
  mistake_tags: string[];
  pnl: number | null;
  mfe: number | null;
  mae: number | null;
  signal_score: number | null;
  entry_date: string | null;
  exit_date: string | null;
}

interface EditState {
  entry_id: string;
  followed_rules: boolean | null;
  post_trade_notes: string;
  tags: string;
}

interface TagPerf {
  tag: string;
  trade_count: number;
  win_rate: number;
  avg_pnl: number;
  total_pnl: number;
}

interface MonthlyReviewData {
  month: string;
  total_trades: number;
  win_rate: number;
  total_pnl: number;
  rules_followed_pct: number;
  top_tags: string[];
  top_mistakes: string[];
  mistake_frequency: Record<string, number>;
  recommendation: string;
}

const fmt = (n: number | null | undefined) =>
  n == null ? "—" : (n >= 0 ? "+" : "") + "$" + Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: 0 });

const pct = (n: number | null | undefined) => (n == null ? "—" : `${(n * 100).toFixed(0)}%`);

const holdDays = (entry: string | null, exit: string | null) => {
  if (!entry) return null;
  const from = new Date(entry);
  const to   = exit ? new Date(exit) : new Date();
  return Math.floor((to.getTime() - from.getTime()) / 86400000);
};

const currentMonth = () => new Date().toISOString().slice(0, 7);

function EmptyState({ title, sub }: { title: string; sub: string }) {
  return (
    <div style={{ padding: 24, textAlign: "center", fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
      <div style={{ color: "var(--cyan)", marginBottom: 6 }}>{title}</div>
      {sub}
    </div>
  );
}

function EntryCard({ e, onClick }: { e: Entry; onClick: () => void }) {
  const days  = holdDays(e.entry_date, e.exit_date);
  const isOpen = e.pnl == null;
  const pnlColor = isOpen ? "var(--ink-dim)" : (e.pnl ?? 0) >= 0 ? "var(--green)" : "var(--red)";

  return (
    <div
      className="instrument-card"
      onClick={onClick}
      style={{
        borderLeft: `3px solid ${isOpen ? "var(--cyan)" : (e.pnl ?? 0) >= 0 ? "var(--green)" : "var(--red)"}`,
        padding: "14px 16px",
        cursor: "pointer",
        transition: "background 0.1s",
      }}
      onMouseEnter={ev => (ev.currentTarget.style.background = "var(--bg-3)")}
      onMouseLeave={ev => (ev.currentTarget.style.background = "")}
    >
      {/* Row 1: symbol + P&L */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontFamily: "var(--mono)", fontSize: 15, fontWeight: 700, color: "var(--cyan)" }}>
            {e.underlying || "—"}
          </span>
          <Badge kind="tag" tone={isOpen ? "var(--cyan)" : "var(--green)"}>
            {isOpen ? "OPEN" : "CLOSED"}
          </Badge>
          <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)" }}>
            {e.strategy?.replace(/_/g, " ").toUpperCase() || "—"}
          </span>
          {e.market_context && e.market_context.toLowerCase() !== "unknown" && (
            <span style={{
              fontFamily: "var(--mono)", fontSize: 9, padding: "2px 7px",
              background: "rgba(148,163,184,0.08)", border: "1px solid var(--line-dim)",
              color: "var(--ink-dim)", textTransform: "uppercase",
            }}>
              {e.market_context}
            </span>
          )}
        </div>
        <div style={{ textAlign: "right" }}>
          <span style={{ fontFamily: "var(--mono)", fontSize: 16, fontWeight: 700, color: pnlColor }}>
            {isOpen ? "OPEN" : fmt(e.pnl)}
          </span>
        </div>
      </div>

      {/* Row 2: meta info */}
      <div style={{ display: "flex", gap: 20, marginBottom: 8, flexWrap: "wrap" }}>
        {[
          { label: "ENTRY", val: e.entry_date?.slice(0, 10) || "—" },
          { label: "EXIT",  val: e.exit_date?.slice(0, 10)  || "—" },
          { label: "HOLD",  val: days != null ? `${days}d` : "—" },
          { label: "SCORE", val: e.signal_score != null ? e.signal_score.toFixed(3) : "—" },
          { label: "CONF",  val: e.confidence_level != null ? `${e.confidence_level}/5` : "—" },
          { label: "MFE",   val: e.mfe != null ? fmt(e.mfe) : "—" },
          { label: "MAE",   val: e.mae != null ? fmt(e.mae) : "—" },
        ].map(item => (
          <div key={item.label} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span style={{ fontFamily: "var(--mono)", fontSize: 8, color: "var(--ink-faint)", letterSpacing: "0.08em" }}>
              {item.label}
            </span>
            <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-dim)" }}>
              {item.val}
            </span>
          </div>
        ))}

        {/* Rules followed */}
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontFamily: "var(--mono)", fontSize: 8, color: "var(--ink-faint)", letterSpacing: "0.08em" }}>RULES</span>
          {e.followed_rules == null ? (
            <Badge kind="tag" tone="var(--amber)">ADD</Badge>
          ) : e.followed_rules ? (
            <Badge kind="tag" tone="var(--green)">YES ✓</Badge>
          ) : (
            <Badge kind="tag" tone="var(--red)">NO ✗</Badge>
          )}
        </div>
      </div>

      {/* Row 3: context + notes */}
      {(e.market_context || e.post_trade_notes) && (
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 8 }}>
          {e.post_trade_notes && (
            <div style={{ flex: 2, minWidth: 200 }}>
              <div style={{ fontFamily: "var(--mono)", fontSize: 8, color: "var(--ink-faint)", letterSpacing: "0.08em", marginBottom: 3 }}>NOTES</div>
              <div style={{ fontSize: 11, color: "var(--ink)", lineHeight: 1.5 }}>{e.post_trade_notes}</div>
            </div>
          )}
        </div>
      )}

      {/* Row 4: tags */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
          {(e.tags || []).map((t: string) => (
            <span key={t} style={{
              fontFamily: "var(--mono)", fontSize: 9, padding: "1px 7px",
              background: "rgba(6,182,212,0.08)", border: "1px solid rgba(6,182,212,0.2)",
              color: "var(--cyan)",
            }}>{t}</span>
          ))}
        </div>
        <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink-faint)" }}>
          CLICK TO EDIT
        </span>
      </div>
    </div>
  );
}

export default function Journal() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [tab, setTab]         = useState<"entries" | "performance" | "mistakes" | "review">("entries");
  const [editing, setEditing] = useState<EditState | null>(null);
  const [saving, setSaving]   = useState(false);
  const [impact, setImpact]   = useState<any>(null);
  const [filter, setFilter]   = useState<"all" | "open" | "closed">("all");

  const [tagPerf, setTagPerf]           = useState<TagPerf[] | null>(null);
  const [tagPerfLoading, setTagPerfLoading] = useState(false);
  const [mistakes, setMistakes]         = useState<Record<string, number> | null>(null);
  const [mistakesLoading, setMistakesLoading] = useState(false);
  const [month, setMonth]               = useState(currentMonth());
  const [review, setReview]             = useState<MonthlyReviewData | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    setLoadError(false);
    setEntries([]);
    setImpact(null);
    try {
      const [entryRes, impactRes]: any = await Promise.all([
        api.getJournalEntries(),
        api.getRuleBreachImpact(),
      ]);
      setEntries(entryRes.entries || []);
      setImpact(impactRes.impact || null);
    } catch { setLoadError(true); } finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  useEffect(() => {
    if (tab === "performance" && tagPerf === null && !tagPerfLoading) {
      setTagPerfLoading(true);
      (api as any).getTagPerformance()
        .then((r: any) => setTagPerf(r.tag_performance || []))
        .catch(() => setTagPerf([]))
        .finally(() => setTagPerfLoading(false));
    }
    if (tab === "mistakes" && mistakes === null && !mistakesLoading) {
      setMistakesLoading(true);
      (api as any).getMistakeFrequency()
        .then((r: any) => setMistakes(r.mistakes || {}))
        .catch(() => setMistakes({}))
        .finally(() => setMistakesLoading(false));
    }
  }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (tab !== "review") return;
    setReviewLoading(true);
    (api as any).getMonthlyReview(month)
      .then((r: any) => setReview(r.review || null))
      .catch(() => setReview(null))
      .finally(() => setReviewLoading(false));
  }, [tab, month]);

  const openEdit = (e: Entry) => setEditing({
    entry_id: e.id,
    followed_rules: e.followed_rules,
    post_trade_notes: e.post_trade_notes || "",
    tags: (e.tags || []).join(", "),
  });

  const saveEdit = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      await (api as any).updateJournalEntry(editing.entry_id, {
        followed_rules: editing.followed_rules,
        post_trade_notes: editing.post_trade_notes || null,
        tags: editing.tags.split(",").map((t: string) => t.trim()).filter(Boolean),
      });
      setEditing(null);
      await load();
    } finally { setSaving(false); }
  };

  const filtered = entries.filter(e => {
    if (filter === "open")   return e.pnl == null;
    if (filter === "closed") return e.pnl != null;
    return true;
  });

  const totalPnl  = entries.filter(e => e.pnl != null).reduce((s, e) => s + (e.pnl ?? 0), 0);
  const wins      = entries.filter(e => (e.pnl ?? 0) > 0).length;
  const closed    = entries.filter(e => e.pnl != null).length;
  const open      = entries.filter(e => e.pnl == null).length;
  const winRate   = closed > 0 ? Math.round(wins / closed * 100) : 0;
  const withRules = entries.filter(e => e.followed_rules != null);
  const rulesPct  = withRules.length > 0
    ? Math.round(withRules.filter(e => e.followed_rules).length / withRules.length * 100)
    : null;

  const maxMistake = mistakes ? Math.max(1, ...Object.values(mistakes)) : 1;

  return (
    <div className="workspace-page" style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>

      {/* Toolbar */}
      <div className="workspace-toolbar" style={{
        padding: "8px 16px", borderBottom: "1px solid var(--line-dim)",
        background: "var(--bg-2)", display: "flex", alignItems: "center", gap: 8,
      }}>
        {([
          { key: "entries",     label: "ENTRIES" },
          { key: "performance", label: "PERFORMANCE" },
          { key: "mistakes",    label: "MISTAKES" },
          { key: "review",      label: "MONTHLY REVIEW" },
        ] as const).map(t => (
          <Button key={t.key} active={tab === t.key}
            onClick={() => setTab(t.key)}>{t.label}</Button>
        ))}
        {tab === "entries" && (
          <>
            <div style={{ width: 1, height: 16, background: "var(--line-dim)", margin: "0 4px" }} />
            {(["all","open","closed"] as const).map(f => (
              <Button key={f} active={filter === f}
                style={{ fontSize: 10 }} onClick={() => setFilter(f)}>
                {f.toUpperCase()}
              </Button>
            ))}
          </>
        )}
        <div style={{ flex: 1 }} />
        {tab === "review" && (
          <input
            type="month"
            value={month}
            onChange={ev => setMonth(ev.target.value)}
            style={{
              background: "var(--bg-3)", border: "1px solid var(--line-dim)", color: "var(--ink)",
              fontFamily: "var(--mono)", fontSize: 11, padding: "5px 8px",
            }}
          />
        )}
      </div>

      <div style={{ flex: 1, overflowY: "auto" }}>

        {/* ── Entries tab ── */}
        {tab === "entries" && (
          loading ? (
            <div style={{ padding: 40, textAlign: "center", fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
              LOADING...
            </div>
          ) : loadError ? (
            <div role="alert" className="workspace-read-error">Journal unavailable. Reload to retry; no entry count or performance is confirmed.</div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 16, padding: 16 }}>

              {/* Overview strip */}
              {entries.length > 0 && (
                <div className="instrument-stat-strip workspace-stat-grid" style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)" }}>
                  <StatTile variant="divider" size="default" label="Total P&L"       value={fmt(totalPnl)}                         tone={totalPnl >= 0 ? "var(--green)" : "var(--red)"} />
                  <StatTile variant="divider" size="default" label="Win Rate"        value={closed > 0 ? `${winRate}%` : "—"}       tone={winRate >= 50 ? "var(--green)" : "var(--amber)"} />
                  <StatTile variant="divider" size="default" label="Closed / Open"   value={`${closed} / ${open}`} />
                  <StatTile variant="divider" size="default" label="Rules Followed"  value={rulesPct != null ? `${rulesPct}%` : "—"} tone={rulesPct != null && rulesPct >= 80 ? "var(--green)" : "var(--amber)"} />
                  <StatTile variant="divider" size="default" label="Total Entries"   value={String(entries.length)} />
                </div>
              )}

              {filtered.length === 0 ? (
                <EmptyState title="NO JOURNAL ENTRIES YET" sub="Entries are created automatically when a trade fills." />
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                  {filtered.map(e => (
                    <EntryCard key={e.id} e={e} onClick={() => openEdit(e)} />
                  ))}
                </div>
              )}
            </div>
          )
        )}

        {/* ── Performance tab ── */}
        {tab === "performance" && (
          <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 16 }}>
            <Panel
              padding={0}
              title="Rule Breach Impact"
              action={<span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)" }}>Requires followed_rules set on trades</span>}
            >
              {!impact ? (
                <EmptyState title="NOT ENOUGH DATA" sub="Need 5+ trades with followed_rules data. Click any trade card and mark whether you followed the rules." />
              ) : (
                <>
                  <div className="workspace-grid" style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)" }}>
                    <StatTile variant="divider" size="default" label="Followed — Avg P&L"   value={fmt(impact.followed_avg_pnl)}                    tone="var(--green)" />
                    <StatTile variant="divider" size="default" label="Breached — Avg P&L"   value={fmt(impact.breached_avg_pnl)}                    tone="var(--red)" />
                    <StatTile variant="divider" size="default" label="Followed — Win Rate"  value={`${(impact.followed_win_rate*100).toFixed(1)}%`} tone="var(--green)" />
                    <StatTile variant="divider" size="default" label="Cost of Breach/Trade"  value={fmt(impact.pnl_delta)}                          tone="var(--amber)" />
                  </div>
                  <div style={{ padding: "10px 14px", borderTop: "1px solid var(--line-dim)",
                    fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-dim)" }}>
                    {impact.summary}
                  </div>
                </>
              )}
            </Panel>

            <Panel
              padding={0}
              title="Performance by Tag"
              action={<span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)" }}>Avg / total P&L for each tag you've applied to a closed trade</span>}
            >
              {tagPerfLoading ? (
                <EmptyState title="LOADING..." sub="" />
              ) : !tagPerf || tagPerf.length === 0 ? (
                <EmptyState title="NO TAGGED TRADES YET" sub="Add tags to closed trades (e.g. good-entry, early-exit) to see performance broken out by tag." />
              ) : (
                <div className="workspace-journal-data">
                  {/* header row */}
                  <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr 1fr 1fr", padding: "6px 14px",
                    borderBottom: "1px solid var(--line-dim)", fontFamily: "var(--mono)", fontSize: 8,
                    color: "var(--ink-faint)", letterSpacing: "0.08em" }}>
                    <span>TAG</span><span>TRADES</span><span>WIN RATE</span><span>AVG P&L</span><span>TOTAL P&L</span>
                  </div>
                  {tagPerf.map(tp => (
                    <div key={tp.tag} style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr 1fr 1fr", padding: "8px 14px",
                      borderBottom: "1px solid var(--line-dim)", fontFamily: "var(--mono)", fontSize: 11, alignItems: "center" }}>
                      <span style={{ color: "var(--cyan)" }}>{tp.tag}</span>
                      <span style={{ color: "var(--ink-dim)" }}>{tp.trade_count}</span>
                      <span style={{ color: tp.win_rate >= 0.5 ? "var(--green)" : "var(--amber)" }}>{pct(tp.win_rate)}</span>
                      <span style={{ color: tp.avg_pnl >= 0 ? "var(--green)" : "var(--red)" }}>{fmt(tp.avg_pnl)}</span>
                      <span style={{ color: tp.total_pnl >= 0 ? "var(--green)" : "var(--red)" }}>{fmt(tp.total_pnl)}</span>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </div>
        )}

        {/* ── Mistakes tab ── */}
        {tab === "mistakes" && (
          <div style={{ padding: 16 }}>
            <Panel
              padding={0}
              title="Mistake Frequency"
              action={<span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)" }}>Counted from mistake_tags added during post-trade review</span>}
            >
              {mistakesLoading ? (
                <EmptyState title="LOADING..." sub="" />
              ) : !mistakes || Object.keys(mistakes).length === 0 ? (
                <EmptyState title="NO MISTAKES LOGGED YET" sub="Tag recurring errors (e.g. overrode-exit, sized-too-big, chased-entry) on trade entries to track patterns here." />
              ) : (
                <div style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
                  {Object.entries(mistakes).map(([tag, count]) => (
                    <div key={tag} style={{ display: "flex", alignItems: "center", gap: 12 }}>
                      <span style={{ width: 160, fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink)" }}>{tag}</span>
                      <div style={{ flex: 1, height: 8, background: "var(--bg-3)", position: "relative" }}>
                        <div style={{
                          position: "absolute", left: 0, top: 0, bottom: 0,
                          width: `${(count / maxMistake) * 100}%`,
                          background: "var(--red)", opacity: 0.6,
                        }} />
                      </div>
                      <span style={{ width: 24, textAlign: "right", fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-dim)" }}>{count}</span>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </div>
        )}

        {/* ── Monthly Review tab ── */}
        {tab === "review" && (
          <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 16 }}>
            <Panel padding={0} title={`Monthly Review — ${month}`}>
              {reviewLoading ? (
                <EmptyState title="LOADING..." sub="" />
              ) : !review || review.total_trades === 0 ? (
                <EmptyState title="NO TRADES THIS MONTH" sub="Pick a different month, or check back once trades have closed this month." />
              ) : (
                <>
                  <div className="workspace-grid" style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)" }}>
                    <StatTile variant="divider" size="default" label="Total Trades"     value={String(review.total_trades)} />
                    <StatTile variant="divider" size="default" label="Win Rate"         value={pct(review.win_rate)}                           tone={review.win_rate >= 0.5 ? "var(--green)" : "var(--amber)"} />
                    <StatTile variant="divider" size="default" label="Total P&L"        value={fmt(review.total_pnl)}                          tone={review.total_pnl >= 0 ? "var(--green)" : "var(--red)"} />
                    <StatTile variant="divider" size="default" label="Rules Followed"   value={`${review.rules_followed_pct.toFixed(0)}%`}     tone={review.rules_followed_pct >= 80 ? "var(--green)" : "var(--amber)"} />
                  </div>

                  {(review.top_tags.length > 0 || review.top_mistakes.length > 0) && (
                    <div style={{ display: "flex", gap: 24, padding: "14px 16px", borderTop: "1px solid var(--line-dim)", flexWrap: "wrap" }}>
                      {review.top_tags.length > 0 && (
                        <div>
                          <div className="kicker" style={{ marginBottom: 6 }}>Top Tags</div>
                          <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
                            {review.top_tags.map(t => (
                              <span key={t} style={{ fontFamily: "var(--mono)", fontSize: 9, padding: "1px 7px",
                                background: "rgba(6,182,212,0.08)", border: "1px solid rgba(6,182,212,0.2)", color: "var(--cyan)" }}>{t}</span>
                            ))}
                          </div>
                        </div>
                      )}
                      {review.top_mistakes.length > 0 && (
                        <div>
                          <div className="kicker" style={{ marginBottom: 6 }}>Top Mistakes</div>
                          <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
                            {review.top_mistakes.map(t => (
                              <span key={t} style={{ fontFamily: "var(--mono)", fontSize: 9, padding: "1px 7px",
                                background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", color: "var(--red)" }}>{t}</span>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  <div style={{ padding: "12px 16px", borderTop: "1px solid var(--line-dim)",
                    fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink)", lineHeight: 1.6 }}>
                    {review.recommendation}
                  </div>
                </>
              )}
            </Panel>
          </div>
        )}
      </div>

      {/* Edit modal */}
      {editing && (
        <div style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,0.75)",
          display: "flex", alignItems: "center", justifyContent: "center", zIndex: 200,
        }}>
          <div className="instrument-card" style={{
            maxWidth: 500, width: "100%", padding: 28,
          }}>
            <div className="panel-title" style={{ marginBottom: 20 }}>ADD TRADE NOTES</div>

            <div style={{ marginBottom: 16 }}>
              <div className="kicker" style={{ marginBottom: 8 }}>Did you follow the rules?</div>
              <div style={{ display: "flex", gap: 8 }}>
                {[
                  { val: true,  label: "YES — followed rules",  color: "var(--green)" },
                  { val: false, label: "NO — broke a rule",     color: "var(--red)"   },
                  { val: null,  label: "Skip",                  color: "var(--ink-dim)" },
                ].map(o => (
                  <Button key={String(o.val)}
                    active={editing.followed_rules === o.val}
                    style={editing.followed_rules === o.val ? { borderColor: o.color, color: o.color } : {}}
                    onClick={() => setEditing({ ...editing, followed_rules: o.val })}>
                    {o.label}
                  </Button>
                ))}
              </div>
            </div>

            <div style={{ marginBottom: 16 }}>
              <div className="kicker" style={{ marginBottom: 8 }}>Post-trade notes</div>
              <textarea
                value={editing.post_trade_notes}
                onChange={e => setEditing({ ...editing, post_trade_notes: e.target.value })}
                placeholder="What went right or wrong? What would you do differently?"
                rows={4}
                style={{
                  width: "100%", background: "var(--bg-3)",
                  border: "1px solid var(--line-dim)", color: "var(--ink)",
                  fontFamily: "var(--sans)", fontSize: 13, padding: "8px 10px",
                  outline: "none", resize: "vertical", boxSizing: "border-box",
                }}
              />
            </div>

            <div style={{ marginBottom: 24 }}>
              <div className="kicker" style={{ marginBottom: 8 }}>Tags (comma-separated)</div>
              <input
                value={editing.tags}
                onChange={e => setEditing({ ...editing, tags: e.target.value })}
                placeholder="e.g. good-entry, high-iv, early-exit"
                style={{
                  width: "100%", background: "var(--bg-3)",
                  border: "1px solid var(--line-dim)", color: "var(--ink)",
                  fontFamily: "var(--mono)", fontSize: 12, padding: "7px 10px",
                  outline: "none", boxSizing: "border-box",
                }}
              />
            </div>

            <div style={{ display: "flex", gap: 10 }}>
              <Button onClick={() => setEditing(null)} style={{ flex: 1 }}>CANCEL</Button>
              <Button active onClick={saveEdit} disabled={saving} style={{ flex: 2 }}>
                {saving ? "SAVING..." : "SAVE NOTES"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
