# Driver supply feature sanity check

* Business-date range: 2026-10-25 to 2026-10-26
* Feature cells: 5
* Demand cells with at least one available driver: 0/4 (0.0%)
* Demand cells without supply: 4
* Supply-only cells: 1
* Orders represented: 6
* Available driver-hours represented (summed across zone/hour cells): 0.672
* Average drivers per order in demand cells: 0.000

This is a coverage/sanity report, not a causal or forecasting benchmark. Demand uses order pickup zones; availability is integrated over the hour using the last evented driver location. GPS pings and live movement are out of scope.
