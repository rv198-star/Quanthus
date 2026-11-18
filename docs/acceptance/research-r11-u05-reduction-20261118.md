# R11 U05 complete-order reduction and residual lot

This bounded synthetic acceptance adds a versioned `ReductionSellProfile` for a complete
200-share sell order against a 500-share lot. The original full-lot `SellProfile` remains
unchanged and continues to reject partial holdings.

The shared accounting `apply_sell_delta` calculation supplies the account and bucket cash
impact. The adapter atomically persists the reduced quantity and proportional remaining cost,
preserving the original lot identity, source, cycle and original cost. A later full exit
consumes only the residual cost. Settlement remains on the existing path and is refused while
the account still holds a position.

The synthetic expected sequence is:

1. Buy 500 at 10 with fee 5: cash 15995, bucket 14995.
2. Reduce 200 at 12 with fee 5: cash 18390, bucket 17390, remaining quantity 300,
   remaining cost 3000.
3. Exit 300 at 9 with fee 5: cash 21085, bucket 20085, quantity 0.
4. Settle: bucket 20000, reserve 1085.
5. Apply old fee 100: cash 20985, reserve 985.

The profile is synthetic engineering data only. It does not authorize market data, capital,
P6, daily execution, production operation, or a change to historical strategy windows.
