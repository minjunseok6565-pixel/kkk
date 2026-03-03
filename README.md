# all

`all` is a basketball simulation and league-management codebase.

## Repository layout

- `app/`, `sim/`, `matchengine_v3/`: game simulation and runtime modules.
- `contracts/`, `trades/`, `salary_matching_brackets.py`: roster and cap-management logic.
- `injury/`, `fatigue/`, `training/`, `readiness/`: player condition systems.
- `news/`, `analytics/`, `scouting/`: reporting and analysis helpers.

## Getting started

1. Use Python 3.10+ in a virtual environment.
2. Install dependencies used by your target subsystem.
3. Run feature-specific scripts/modules from the repository root.

Because this repository has multiple entry points, there is no single command that runs every subsystem.
