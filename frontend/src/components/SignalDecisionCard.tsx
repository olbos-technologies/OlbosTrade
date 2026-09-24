/**
 * Shared signal presentation for the equity and options scanners.
 *
 * On a phone, the card keeps the actual decision (direction, confidence and
 * freshness) visible while making supporting evidence an explicit disclosure.
 * Desktop retains the expanded research view for rapid comparison.
 */
import React from "react";
import MissionCard, { type MissionCardProps } from "./MissionCard";
import { useIsMobile } from "../hooks/useIsMobile";

interface SignalDecisionCardProps extends MissionCardProps {
  /** Short, evidence-based label for the disclosure affordance. */
  detailsLabel?: string;
}

export default function SignalDecisionCard({
  children,
  detailsLabel = "View plan & evidence",
  className = "",
  ...missionProps
}: SignalDecisionCardProps) {
  const isMobile = useIsMobile();
  const details = <div className="mission-card__details">{children}</div>;

  return (
    <MissionCard {...missionProps} className={`signal-decision-card ${className}`.trim()}>
      {isMobile ? (
        <details className="signal-decision-card__disclosure">
          <summary>{detailsLabel}</summary>
          {details}
        </details>
      ) : details}
    </MissionCard>
  );
}
