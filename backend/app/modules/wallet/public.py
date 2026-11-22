"""Public interface of the wallet module (ADR-0001)."""

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
    "FreezeSource",
    "Wallet",
    "WalletNotActiveError",
    "WalletNotFoundError",
    "WalletStateConflictError",
    "WalletStatus",
    "WalletView",
    "ensure_active",
    "ensure_wallet",
    "find_for_owner",
    "freeze",
    "get_by_number",
    "get_wallet",
    "is_valid_wallet_number",
    "lock_wallets",
    "mask_wallet_number",
    "unfreeze",
    "view",
]
