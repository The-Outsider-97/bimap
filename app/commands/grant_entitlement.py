"""
responsibility:
result = entitlement_service.consume(
    account_id=order.account_id,
    kind=UsageKind.AUDIT,
    source_id=order.order_id,
    idempotency_key=idempotency_key,
)

order_service.transition(
    order.order_id,
    OrderState.ENTITLED,
    idempotency_key=f"{idempotency_key}:order",
    actor=actor,
    metadata={
        "entitlement_source": result.source,
    },
)


The entitlement store must be idempotent on the audit order_id.

Thus a timeout and retry cannot charge two audits.
""" 

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...domain.orders.models import Order
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Grant Entitlement")
printer = PrettyPrinter()


class GrantEntitlement:
    def __init__(self) -> None:
        pass


__all__ = ["GrantEntitlement"]


if __name__ == "__main__":
    print("\n=== Running BIMAP Grant Entitlement ===\n")
    printer.status("Init", "BIMAP Grant Entitlement initialized", "success")

    print("\n=== Successfully ran the BIMAP Grant Entitlement ===\n")