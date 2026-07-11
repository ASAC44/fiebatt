- [x] inspect existing route, storage, ffmpeg, and timeline patterns
- [x] add preview response schemas
- [x] add preview route with frame, strip, and range endpoints
- [x] extend ffmpeg clip extraction for low-res preview output
- [x] register preview router and run sanity checks

## review

- added preview endpoints that resolve timestamps through `build_timeline()` so generated spans read from variant media instead of the original source.
- range previews stitch low-res, video-only subclips across timeline boundaries to keep mixed original/generated spans accurate.
- `python3 -m py_compile app/api/routes/preview.py app/schemas/preview.py app/services/ffmpeg.py app/main.py` passed.

## scoring task

- [x] inspect existing route, model, storage, timeline, and gemini service patterns
- [x] implement `app/api/routes/scoring.py` with `/score`, `/score/compare`, and `/score/continuity`
- [x] register the scoring router in app startup
- [x] run targeted validation and note results

### scoring review

- added `app/api/routes/scoring.py` with typed request/response models, gemini json scoring helpers, variant comparison ranking, and continuity boundary analysis
- wired the new router into `app/main.py`
- `python3 -m py_compile app/api/routes/scoring.py app/main.py` passed
- full import-time validation was limited here because the shell environment is missing project deps like `sqlalchemy`
