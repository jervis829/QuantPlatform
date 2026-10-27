"""Strict OrderStateMachine prohibiting illegal state transitions (P6)."""

from __future__ import annotations

from quant_platform.core.enums import OrderStatus
from quant_platform.exceptions import OrderStateTransitionError

TERMINAL_ORDER_STATUSES: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
        OrderStatus.EXPIRED_IN_MATCH,
    }
)

LEGAL_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING_SUBMIT: frozenset(
        {
            OrderStatus.NEW,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
            OrderStatus.EXPIRED_IN_MATCH,
        }
    ),
    OrderStatus.NEW: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
            OrderStatus.EXPIRED_IN_MATCH,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
    OrderStatus.EXPIRED_IN_MATCH: frozenset(),
}

STATUS_PROGRESSION_RANK: dict[OrderStatus, int] = {
    OrderStatus.PENDING_SUBMIT: 0,
    OrderStatus.NEW: 1,
    OrderStatus.PARTIALLY_FILLED: 2,
    OrderStatus.FILLED: 3,
    OrderStatus.CANCELED: 3,
    OrderStatus.REJECTED: 3,
    OrderStatus.EXPIRED: 3,
    OrderStatus.EXPIRED_IN_MATCH: 3,
}


class OrderStateMachine:
    """Per-order finite state machine enforcing legal exchange lifecycle transitions."""

    def __init__(self, client_order_id: str, initial_status: OrderStatus = OrderStatus.PENDING_SUBMIT) -> None:
        self.client_order_id = client_order_id
        self._status = initial_status

    @property
    def status(self) -> OrderStatus:
        return self._status

    @property
    def is_terminal(self) -> bool:
        return self._status in TERMINAL_ORDER_STATUSES

    def can_transition_to(self, target: OrderStatus) -> bool:
        return target in LEGAL_TRANSITIONS[self._status]

    def transition_to(self, target: OrderStatus) -> OrderStatus:
        if target not in LEGAL_TRANSITIONS[self._status]:
            raise OrderStateTransitionError(
                client_order_id=self.client_order_id,
                from_status=self._status.value,
                to_status=target.value,
            )
        self._status = target
        return self._status
