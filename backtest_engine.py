"""
backtest_engine.py
공통 백테스트 엔진 — 포지션 관리, 주문 시뮬레이션, 수수료/슬리피지 처리
"""
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


# ─────────────────────────────────────────
# 상수
# ─────────────────────────────────────────
COMMISSION_RATE = 0.00015   # 매수·매도 각 0.015%
SLIPPAGE_RATE = 0.0005      # 슬리피지 0.05% (체결 불리하게 적용)
TAX_RATE = 0.0018           # 증권거래세 0.18% (매도 시)


# ─────────────────────────────────────────
# 포지션 단위
# ─────────────────────────────────────────
@dataclass
class Position:
    code: str
    strategy: str           # "BREAKOUT" | "REVERSION"
    entry_price: float
    qty: int
    entry_time: str
    budget: float
    stop_loss: float
    take_profit: float
    trailing_stop_pct: float = 0.0   # BREAKOUT trailing stop (0이면 미사용)
    peak_price: float = 0.0          # trailing stop 기준 최고가


# ─────────────────────────────────────────
# 체결가 계산 (슬리피지 반영)
# ─────────────────────────────────────────
def buy_price(price: float) -> float:
    return round(price * (1 + SLIPPAGE_RATE))


def sell_price(price: float) -> float:
    return round(price * (1 - SLIPPAGE_RATE))


# ─────────────────────────────────────────
# 엔진 클래스
# ─────────────────────────────────────────
class BacktestEngine:
    def __init__(self, initial_capital: float = 10_000_000, max_positions: int = 6):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.max_positions = max_positions
        self.positions: dict[str, Position] = {}   # code → Position
        self.trades: list[dict] = []               # 체결 내역
        self.equity_curve: list[dict] = []         # 자산 곡선

    # ── 잔여 슬롯 ──────────────────────────
    @property
    def available_slots(self) -> int:
        return self.max_positions - len(self.positions)

    # ── 총 자산 ────────────────────────────
    def total_equity(self, current_prices: dict[str, float]) -> float:
        pos_value = sum(
            p.qty * current_prices.get(p.code, p.entry_price)
            for p in self.positions.values()
        )
        return self.cash + pos_value

    # ── 매수 ───────────────────────────────
    def open_position(
        self,
        code: str,
        strategy: str,
        price: float,
        budget: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        entry_time: str,
        trailing_stop_pct: float = 0.0,
    ) -> bool:
        if code in self.positions:
            return False
        if self.available_slots <= 0:
            return False

        exec_price = buy_price(price)
        qty = int(budget // (exec_price * (1 + COMMISSION_RATE)))
        if qty <= 0:
            return False

        cost = exec_price * qty * (1 + COMMISSION_RATE)
        if cost > self.cash:
            return False

        self.cash -= cost
        self.positions[code] = Position(
            code=code,
            strategy=strategy,
            entry_price=exec_price,
            qty=qty,
            entry_time=entry_time,
            budget=budget,
            stop_loss=exec_price * (1 - stop_loss_pct),
            take_profit=exec_price * (1 + take_profit_pct),
            trailing_stop_pct=trailing_stop_pct,
            peak_price=exec_price,
        )

        self.trades.append({
            "time": entry_time,
            "code": code,
            "strategy": strategy,
            "side": "BUY",
            "price": exec_price,
            "qty": qty,
            "amount": cost,
            "pnl": None,
            "pnl_pct": None,
        })
        return True

    # ── 매도 ───────────────────────────────
    def close_position(
        self,
        code: str,
        price: float,
        exit_time: str,
        reason: str = "",
    ) -> Optional[dict]:
        pos = self.positions.pop(code, None)
        if pos is None:
            return None

        exec_price = sell_price(price)
        proceeds = exec_price * pos.qty * (1 - COMMISSION_RATE - TAX_RATE)
        self.cash += proceeds

        pnl = proceeds - (pos.entry_price * pos.qty * (1 + COMMISSION_RATE))
        pnl_pct = pnl / (pos.entry_price * pos.qty) * 100

        trade = {
            "time": exit_time,
            "code": code,
            "strategy": pos.strategy,
            "side": "SELL",
            "price": exec_price,
            "qty": pos.qty,
            "amount": proceeds,
            "pnl": round(pnl),
            "pnl_pct": round(pnl_pct, 2),
            "reason": reason,
            "hold_from": pos.entry_time,
        }
        self.trades.append(trade)
        return trade

    # ── 포지션별 손익 체크 (매 캔들 호출) ──
    def check_exits(
        self,
        current_prices: dict[str, float],
        current_time: str,
    ) -> list[dict]:
        """손절·익절·트레일링스톱 조건 확인 후 청산"""
        closed = []
        for code in list(self.positions.keys()):
            pos = self.positions[code]
            price = current_prices.get(code)
            if price is None:
                continue

            # trailing stop peak 갱신
            if pos.trailing_stop_pct > 0 and price > pos.peak_price:
                pos.peak_price = price
                pos.stop_loss = max(
                    pos.stop_loss,
                    pos.peak_price * (1 - pos.trailing_stop_pct),
                )

            reason = None
            if price <= pos.stop_loss:
                reason = "STOP_LOSS"
            elif price >= pos.take_profit:
                reason = "TAKE_PROFIT"

            if reason:
                trade = self.close_position(code, price, current_time, reason)
                if trade:
                    closed.append(trade)
        return closed

    # ── 자산 곡선 스냅샷 ────────────────────
    def snapshot(self, ts: str, current_prices: dict[str, float]):
        self.equity_curve.append({
            "time": ts,
            "equity": self.total_equity(current_prices),
            "cash": self.cash,
            "positions": len(self.positions),
        })

    # ── 전체 결과 집계 ──────────────────────
    def summary(self) -> dict:
        sell_trades = [t for t in self.trades if t["side"] == "SELL"]
        if not sell_trades:
            return {"total_trades": 0}

        pnls = [t["pnl"] for t in sell_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        total_pnl = sum(pnls)
        win_rate = len(wins) / len(pnls) * 100

        # MDD 계산
        eq = pd.Series([e["equity"] for e in self.equity_curve])
        roll_max = eq.cummax()
        drawdown = (eq - roll_max) / roll_max * 100
        mdd = drawdown.min()

        final_equity = self.equity_curve[-1]["equity"] if self.equity_curve else self.initial_capital
        total_return = (final_equity - self.initial_capital) / self.initial_capital * 100

        return {
            "initial_capital": self.initial_capital,
            "final_equity": round(final_equity),
            "total_return_pct": round(total_return, 2),
            "total_pnl": round(total_pnl),
            "total_trades": len(sell_trades),
            "win_rate_pct": round(win_rate, 1),
            "avg_win": round(sum(wins) / len(wins)) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses)) if losses else 0,
            "profit_factor": round(abs(sum(wins) / sum(losses)), 2) if losses and sum(losses) != 0 else float("inf"),
            "mdd_pct": round(mdd, 2),
        }
