"""P12 paper-trading slice: real-quote adapter, versioned intraday matching,
persistent simulated order book on the shared account ledger.

Owner-authorized self-directed simulated orders.  Quote display, simulated
matching and user-reported fills stay separate sources; no system advice is
implied, no broker or exchange order is sent.
"""
