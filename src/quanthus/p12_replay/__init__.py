"""P12 compressed historical-session replay (issue #748).

Engineering verification of the whole daily-advice chain over historical
trading days on CN02: T-evening package -> T+1 confirm or skip -> simulated
fills decided by real 1d bars -> next-day carry-in -> relogin
reconciliation, under restart / duplicate / same-day-rerun / overdue
events.  The replay is fail-closed evidence work, not a strategy-P&L
evaluation: it runs the labelled synthetic replay runner, touches no
8791 ledger/timer/config, and never goes near 8790.
"""
