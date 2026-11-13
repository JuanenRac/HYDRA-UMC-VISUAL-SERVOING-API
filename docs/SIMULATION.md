# Simulation bench

The servoing session can run without a camera, a robot or a cell. The bench of the loop (`bench.py`) closes the real session against a simulated tool in Cartesian space: the vision arrives every period and may be missed, the measurement has noise, a command takes time to reach the tool, the tool follows it with a lag, moves less than it is told and acts on a command only while it is valid. The simulated clock makes a run take no real time and give the same numbers every time. The scenario file runs the bench over a grid of faults drawn from a fixed seed and states what must hold in every cell.

## Run it

```
python -m pytest -q tests/test_simulation_bench.py tests/test_contract_and_bench.py
```

## What the scenarios check

| Scenario | What it shows |
|----------|---------------|
| forty cells with random delay, lag, noise, missed vision and a tool that moves less than told | the speed of the tool never passes the limits of the session and nothing is not a number |
| the same cell twice, another seed | the same run, and a different noise |
| a camera that never sees the target | the tool stays where it was and the verdict says it did not settle |
| vision that comes back after a loss | the tool still gets there |
| more noise | the final position is never better on average |
| a tool that moves less than told | never faster than told |
| a sweep of seven gains | each is reported, the two the session refuses say why, and the best is the quickest of the ones that pass |
| a longer delay | never settles sooner |
| the contracts, the session, the image-based servoing and the safety service integration | the tests of the other files the workflow runs |

The workflow named "Simulation bench" runs the bench on every change, so the simulated path has its own visible job.

## What it cannot show

A real robot, a real camera, the dynamics of a real arm and a real network need the cell. The status of the repository therefore stays at "simulated" for the hardware row until a person has run the service on the bench.
