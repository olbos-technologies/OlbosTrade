"""
Trading mode API routes.
Allows frontend to read and switch trading modes.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import require_api_key
from app.services.trading_mode import (
    TradingModeType,
    TRADING_MODES,
    trading_mode_manager,
)

router = APIRouter()


class SetModeRequest(BaseModel):
    mode: str
    confirmed: bool = False     # Scalper mode requires explicit confirmation


@router.get("/current")
async def get_current_mode():
    """Get the current active trading mode and all its parameters."""
    return trading_mode_manager.summary()


@router.get("/all")
async def get_all_modes():
    """Get all available modes for the mode selector UI."""
    from app.services.trade_frequency_controller import MODE_RULES

    return {
        mode.value: {
            "display_name":       config.display_name,
            "description":        config.description,
            "dte_range":          f"{config.dte_min}–{config.dte_max} DTE",
            "risk_per_trade":     f"{config.risk_per_trade_pct:.1%}",
            "requires_monitoring": config.requires_monitoring,
            "monitoring_warning":  config.monitoring_warning,
            "ui_color":           config.ui_color,
            "ui_icon":            config.ui_icon,
            "is_active":          trading_mode_manager.current.active_mode.value == mode.value,
            # Frequency controller floor — why 75% signals die under Balanced (0.90).
            "min_confidence":     MODE_RULES[mode].min_confidence,
            "hard_max_per_day":   MODE_RULES[mode].hard_max_per_day,
        }
        for mode, config in TRADING_MODES.items()
    }


@router.post("/set", dependencies=[Depends(require_api_key)])
async def set_trading_mode(body: SetModeRequest):
    """
    Switch to a new trading mode.

    Scalper mode requires confirmed=True to prevent accidental activation.
    Mode change takes effect on next signal cycle — never mid-trade.
    """
    try:
        mode = TradingModeType(body.mode)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode: {body.mode}. "
                   f"Valid modes: {[m.value for m in TradingModeType]}"
        )

    # Scalper mode requires explicit confirmation
    if mode == TradingModeType.SCALPER and not body.confirmed:
        config = TRADING_MODES[mode]
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Scalper mode requires explicit confirmation.",
                "warning": config.monitoring_warning,
                "action": "Resend with confirmed=true to activate.",
            }
        )

    new_state = await trading_mode_manager.set_mode(mode, activated_by="user")
    return {
        "success": True,
        "mode": new_state.active_mode.value,
        "message": f"Trading mode set to {new_state.config.display_name}. "
                   f"Takes effect on next signal cycle.",
        "summary": trading_mode_manager.summary(),
    }


@router.post("/reset-to-balanced", dependencies=[Depends(require_api_key)])
async def reset_to_balanced():
    """Reset to default Balanced mode."""
    await trading_mode_manager.set_mode(TradingModeType.BALANCED, activated_by="user")
    return {"success": True, "mode": "balanced"}
