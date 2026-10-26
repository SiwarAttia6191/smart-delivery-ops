# Driver supply feature sanity check

* Business-date range: 2026-10-25 to 2026-10-26
* Feature cells: 20
* Demand cells with at least one available driver: 1/5 (20.0%)
* Demand cells without supply: 4
* Supply-only cells: 15
* Orders represented: 7
* Available driver-hours represented (summed across zone/hour cells): 15.633
* Average drivers per order in demand cells: 0.200

This is a coverage/sanity report, not a causal or forecasting benchmark. Demand uses order pickup zones; availability is integrated over the hour using the last evented driver location. GPS pings and live movement are out of scope.
