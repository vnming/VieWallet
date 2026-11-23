"""Public interface of the wallet module (ADR-0001)."""

from app.modules.wallet.destinations import (
    BANKS,
    BankDestination,
    DestinationNotFoundError,
    add_destination,
    get_destination,
    list_destinations,
    remove_destination,
    reveal_account_number,
)
from app.modules.wallet.domain import (
    FreezeSource,
    WalletStatus,
    is_valid_wallet_number,
    mask_wallet_number,
)
from app.modules.wallet.models import Wallet
from app.modules.wallet.service import (
    WalletNotActiveError,
    WalletNotFoundError,
    WalletStateConflictError,
    WalletView,
    ensure_active,
    ensure_wallet,
    find_for_owner,
    freeze,
    get_by_number,
    get_wallet,
    lock_wallets,
    unfreeze,
    view,
)

__all__ = [
    "BANKS",
    "BankDestination",
    "DestinationNotFoundError",
    "FreezeSource",
    "Wallet",
    "WalletNotActiveError",
    "WalletNotFoundError",
    "WalletStateConflictError",
    "WalletStatus",
    "WalletView",
    "add_destination",
    "ensure_active",
    "ensure_wallet",
    "find_for_owner",
    "freeze",
    "get_by_number",
    "get_destination",
    "get_wallet",
    "is_valid_wallet_number",
    "list_destinations",
    "lock_wallets",
    "mask_wallet_number",
    "remove_destination",
    "reveal_account_number",
    "unfreeze",
    "view",
]
