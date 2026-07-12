# todo

- [x] inspect existing cli patterns in `fiebatt_cli/client.py`, `fiebatt_cli/main.py`, and `fiebatt_cli/commands/generate.py`
- [x] add new api endpoint wrappers to `FiebattClient`
- [x] add new command modules for preview, timeline surgery, grading, scoring, remix, and batch flows
- [x] register the new commands in `fiebatt_cli/main.py`
- [x] run a cli sanity check

# review

- added new client wrappers for preview, timeline surgery, grading, scoring, remix, and batch endpoints
- added `preview`, `split`, `trim`, `snapshot`, `revert`, `grade`, `score`, `remix`, `batch-generate`, and `batch-accept` commands
- verified command registration with `python3 -m fiebatt_cli.main --help`
- verified option surfaces with `python3 -m fiebatt_cli.main preview --help` and `python3 -m fiebatt_cli.main score --help`
- verified import/compile sanity with `python3 -m compileall fiebatt_cli`
