"""P6 prospective-validation package.

Submodules are intentionally not imported eagerly.  The Platform API image uses only the read-only
``product_view`` module, while replay-worker runtime modules may depend on DWD/P2 packages that are
not part of the lean API image.
"""

__all__: list[str] = []
