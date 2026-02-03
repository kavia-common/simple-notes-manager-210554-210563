# Notes persistence (backend)

The backend stores notes **in memory** and also performs **best-effort persistence** to a local JSON file:

- File location (runtime-generated): `src/api/notes_data.json`
- The API will keep working even if the file does not exist or cannot be written.
- This is intentionally lightweight for a demo environment (no database, no env vars).

If you want to reset the stored notes during development, delete `src/api/notes_data.json` and restart the backend.
