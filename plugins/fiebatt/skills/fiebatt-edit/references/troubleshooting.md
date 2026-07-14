# Fiebatt troubleshooting

- **Authentication required:** complete the browser flow and retry `account_status` once.
- **Provider not configured:** open the HTTPS settings URL returned by `account_status`; never collect keys in chat.
- **Provider rejected a key:** replace or validate it in settings, then retry only the failed operation.
- **Upload expired:** request a new upload and restart from `prepare_upload`.
- **Unsupported media:** use MP4, MOV, WebM, or M4V and remain within the returned size and duration limits.
- **Generation duration rejected:** shorten the requested window or choose a provider compatible with that duration.
- **Job failed:** report its provider, model, warnings, and error. Retry once only when the error is transient.
- **Continuity problem:** preview adjacent segments, score continuity, then grade or remix the affected segment.
- **Unexpected timeline:** stop mutating and offer `revert_timeline` using the most recent snapshot.
