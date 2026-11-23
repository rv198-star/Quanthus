# P12 daily advice runtime: daily-core input, rule port, immutable daily packages.
#
# This package owns the T-evening batch that turns the P10 daily-core product and the
# bound strategy rule version into one immutable daily decision package per market
# date.  It never mutates the CN02 public ledger; consumers bind to the published
# packages through PublishedPackageSource.
