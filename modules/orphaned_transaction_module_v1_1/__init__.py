from __future__ import annotations


def __getattr__(name: str):
    if name == "OrphanedTransactionModule":
        from modules.orphaned_transaction_module_v1_1.module import OrphanedTransactionModule
        return OrphanedTransactionModule
    raise AttributeError(name)
