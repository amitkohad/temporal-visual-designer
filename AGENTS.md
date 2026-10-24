# Development instructions

Use the Python backend/Temporal worker and React browser architecture documented in docs/development.md and the feature specifications.

When a task is confirmed implemented and verified, update its checkbox in the same change and link the evidence. Do not leave completed backend tasks unchecked because separate UI or integration tasks remain open. For a partially implemented task, retain its original acceptance, check completed subtasks, and list the remaining unchecked subtasks explicitly. Do not claim production-provider or browser acceptance from fake-port tests.

Use sub-agents for implementation increments. Freeze shared interfaces first, assign disjoint writable paths, and run independent implementation/acceptance work in parallel where dependencies allow. Coordinate shared dependency/environment changes centrally; agents must not run simultaneous package synchronization or editable installs in the shared virtual environment.
