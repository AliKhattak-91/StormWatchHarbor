# Stormwatch Harbour

A grid-based harbour simulation where cooperating agents move cargo from
ships to a warehouse while storms threaten the exposed berths. It also
tests how to put an unreliable LLM in charge safely.

## What it does
- **Dockworkers** collect cargo from berths and deliver it to the warehouse.
- **Lookout** warns every agent 5 ticks before a storm.
- **Quartermaster** assigns idle workers to berths.
- **Harbour Master** is an LLM agent that reads a text report and suggests
  an action. A validator checks every reply before it runs, and the system
  falls back to the model-based workers if the reply is invalid or missing.

Two worker policies are compared: a baseline that ignores warnings, and a
model-based policy that remembers warnings and avoids storm windows.

## Files
| File | Purpose |
|---|---|
| `simulation.py` | Harbour environment and the two worker policies |
| `harbour_master.py` | LLM Harbour Master, validator, fallback and a mock LLM |
| `safety_tests.py` | Prompt-injection tests against the validator |

## Run it
Python 3 only, no extra libraries. From inside the folder:

    py simulation.py
    py harbour_master.py
    py safety_tests.py

On Mac or Linux, use `python3` instead of `py`.

All runs use fixed seeds, so results are repeatable.

## Sample results (seeds 11-13)
| Policy | Avg cargo delivered | Avg storm knock-outs |
|---|---|---|
| Baseline | 107.33 | 0.67 |
| Model-based | 108.00 | 0.00 |
| Model-based + Harbour Master | 108.00 | 0.00 |

## Safety design
The LLM can only request `warn_storm()` or `hold_ship(ship_id)`. Anything
else is rejected, and untrusted text in the report (manifests, crew notes,
player messages) can never trigger an action outside that list.

**Known limitation:** the validator checks the form of an action, not its
intent. A valid but unwise action, such as a false `warn_storm()`, would
still be accepted.

## Using a real LLM
The default LLM is a mock. To use a real model, pass the real API call
function to `HarbourMaster` and set `ANTHROPIC_API_KEY` in your environment.