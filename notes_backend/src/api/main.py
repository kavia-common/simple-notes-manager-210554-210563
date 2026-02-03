from __future__ import annotations

import json
import os
import threading
import time
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


class NoteBase(BaseModel):
    """Base fields shared by note create/update operations."""

    title: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Short title for the note.",
        examples=["Grocery list"],
    )
    content: str = Field(
        ...,
        min_length=1,
        max_length=20_000,
        description="Full note content/body.",
        examples=["Milk, eggs, bread"],
    )


class NoteCreate(NoteBase):
    """Request model for creating a note."""


class NoteUpdate(BaseModel):
    """Request model for updating a note. Fields are optional for partial updates."""

    title: Optional[str] = Field(
        None,
        min_length=1,
        max_length=200,
        description="Updated title for the note.",
        examples=["Updated grocery list"],
    )
    content: Optional[str] = Field(
        None,
        min_length=1,
        max_length=20_000,
        description="Updated content for the note.",
        examples=["Milk, eggs, bread, coffee"],
    )


class Note(NoteBase):
    """Response model for a note."""

    id: int = Field(..., ge=1, description="Unique note identifier.", examples=[1])
    created_at: float = Field(
        ...,
        description="Unix timestamp (seconds) when the note was created.",
        examples=[1712345678.123],
    )
    updated_at: float = Field(
        ...,
        description="Unix timestamp (seconds) when the note was last updated.",
        examples=[1712345688.456],
    )


class NotesListResponse(BaseModel):
    """Response model for listing notes."""

    notes: List[Note] = Field(..., description="List of notes.")
    total: int = Field(..., ge=0, description="Total number of notes returned.")


class NotesStore:
    """A tiny in-memory notes store with simple JSON-file persistence.

    This is intentionally lightweight (no DB, no env vars), suitable for a demo app.
    Persistence is best-effort: reads/writes to a JSON file in this module's directory.
    """

    def __init__(self, persistence_path: str) -> None:
        self._path = persistence_path
        self._lock = threading.RLock()
        self._notes: Dict[int, Note] = {}
        self._next_id = 1
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        """Load notes from disk if present. Non-fatal on errors."""
        if not os.path.exists(self._path):
            return

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            # If the file is corrupted/unreadable, we avoid crashing the API.
            return

        notes = payload.get("notes", [])
        if not isinstance(notes, list):
            return

        with self._lock:
            self._notes = {}
            max_id = 0
            for item in notes:
                try:
                    note = Note.model_validate(item)
                except Exception:
                    continue
                self._notes[note.id] = note
                max_id = max(max_id, note.id)
            self._next_id = max_id + 1

    def _save_to_disk(self) -> None:
        """Persist notes to disk. Best-effort; errors are swallowed."""
        try:
            tmp_path = f"{self._path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"notes": [n.model_dump() for n in self.list_notes()]},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(tmp_path, self._path)
        except Exception:
            # Persistence failure should not prevent the API from functioning.
            return

    def list_notes(self) -> List[Note]:
        """Return all notes sorted by updated_at desc, then id desc."""
        with self._lock:
            return sorted(
                self._notes.values(),
                key=lambda n: (n.updated_at, n.id),
                reverse=True,
            )

    def get_note(self, note_id: int) -> Note:
        """Get a note by id or raise KeyError."""
        with self._lock:
            note = self._notes.get(note_id)
            if note is None:
                raise KeyError(note_id)
            return note

    def create_note(self, data: NoteCreate) -> Note:
        """Create a new note."""
        now = time.time()
        with self._lock:
            note_id = self._next_id
            self._next_id += 1

            note = Note(
                id=note_id,
                title=data.title,
                content=data.content,
                created_at=now,
                updated_at=now,
            )
            self._notes[note_id] = note
            self._save_to_disk()
            return note

    def update_note(self, note_id: int, data: NoteUpdate) -> Note:
        """Update an existing note (partial update)."""
        with self._lock:
            existing = self._notes.get(note_id)
            if existing is None:
                raise KeyError(note_id)

            updated = existing.model_copy(
                update={
                    "title": data.title if data.title is not None else existing.title,
                    "content": (
                        data.content if data.content is not None else existing.content
                    ),
                    "updated_at": time.time(),
                }
            )
            self._notes[note_id] = updated
            self._save_to_disk()
            return updated

    def delete_note(self, note_id: int) -> None:
        """Delete a note."""
        with self._lock:
            if note_id not in self._notes:
                raise KeyError(note_id)
            del self._notes[note_id]
            self._save_to_disk()


openapi_tags = [
    {"name": "Health", "description": "Service health endpoints."},
    {"name": "Notes", "description": "CRUD operations for notes."},
]

# Store persistence file next to this module so it works out-of-the-box.
_PERSISTENCE_FILE = os.path.join(os.path.dirname(__file__), "notes_data.json")
store = NotesStore(persistence_path=_PERSISTENCE_FILE)

app = FastAPI(
    title="Simple Notes Manager API",
    description=(
        "REST API for a simple notes app. Provides CRUD operations for notes "
        "(title + content) and uses lightweight in-memory storage with best-effort "
        "JSON file persistence."
    ),
    version="1.0.0",
    openapi_tags=openapi_tags,
)

# CORS: allow the React dev server by default.
# If you deploy the frontend elsewhere, add the origin here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get(
    "/",
    tags=["Health"],
    summary="Health check",
    description="Simple health check endpoint used by deployment/monitoring.",
    operation_id="health_check",
)
def health_check() -> dict:
    """Health check endpoint.

    Returns:
        A small JSON payload indicating the API is reachable.
    """
    return {"message": "Healthy"}


# PUBLIC_INTERFACE
@app.post(
    "/notes",
    response_model=Note,
    status_code=status.HTTP_201_CREATED,
    tags=["Notes"],
    summary="Create note",
    description="Create a new note with a title and content.",
    operation_id="create_note",
)
def create_note(payload: NoteCreate) -> Note:
    """Create a note.

    Args:
        payload: Note fields (title, content).

    Returns:
        The newly created note including its generated id and timestamps.
    """
    return store.create_note(payload)


# PUBLIC_INTERFACE
@app.get(
    "/notes",
    response_model=NotesListResponse,
    tags=["Notes"],
    summary="List notes",
    description="List all notes sorted by most recently updated first.",
    operation_id="list_notes",
)
def list_notes() -> NotesListResponse:
    """List notes.

    Returns:
        Notes list response containing notes and total count.
    """
    notes = store.list_notes()
    return NotesListResponse(notes=notes, total=len(notes))


# PUBLIC_INTERFACE
@app.get(
    "/notes/{note_id}",
    response_model=Note,
    tags=["Notes"],
    summary="Get note by id",
    description="Fetch a single note by its id.",
    operation_id="get_note",
)
def get_note(note_id: int = Field(..., ge=1, description="Note id to retrieve.")) -> Note:
    """Get a note by id.

    Args:
        note_id: Note identifier.

    Raises:
        HTTPException: 404 if the note does not exist.

    Returns:
        The requested note.
    """
    try:
        return store.get_note(note_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Note {note_id} not found",
        ) from exc


# PUBLIC_INTERFACE
@app.put(
    "/notes/{note_id}",
    response_model=Note,
    tags=["Notes"],
    summary="Update note",
    description="Update an existing note (partial update supported).",
    operation_id="update_note",
)
def update_note(
    payload: NoteUpdate,
    note_id: int = Field(..., ge=1, description="Note id to update."),
) -> Note:
    """Update a note by id.

    Args:
        payload: Fields to update (title/content). Any omitted field remains unchanged.
        note_id: Note identifier.

    Raises:
        HTTPException: 404 if the note does not exist.
        HTTPException: 400 if no fields were provided to update.

    Returns:
        The updated note.
    """
    if payload.title is None and payload.content is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field (title or content) must be provided",
        )

    try:
        return store.update_note(note_id, payload)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Note {note_id} not found",
        ) from exc


# PUBLIC_INTERFACE
@app.delete(
    "/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Notes"],
    summary="Delete note",
    description="Delete a note by its id.",
    operation_id="delete_note",
)
def delete_note(
    note_id: int = Field(..., ge=1, description="Note id to delete."),
) -> Response:
    """Delete a note by id.

    Args:
        note_id: Note identifier.

    Raises:
        HTTPException: 404 if the note does not exist.

    Returns:
        An empty response with HTTP 204.
    """
    try:
        store.delete_note(note_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Note {note_id} not found",
        ) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)
