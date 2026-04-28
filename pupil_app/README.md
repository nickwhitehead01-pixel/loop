# Pupil App (Flutter)

Cross-platform pupil client for the Loop repo.

Targets:
- iPad (iOS)
- Android phones/tablets, including Google Pixel

Backend contract:
- REST against the existing FastAPI backend (`/health`, pupil endpoints)
- WebSocket streaming on `/ws/pupil/{id}/chat`

## Current scaffold

- Connect screen
	- Hub URL field (label only, no placeholder text)
	- Pupil ID field
	- Test Hub connection via `GET /health`
	- Persist settings locally with `shared_preferences`
- Chat screen
	- Connects to pupil chat WebSocket
	- Sends JSON payload with `message`, `conversation_id`, `session_id`
	- Renders streamed token frames until `done=true`
- Discovery contract
	- `HubDiscoveryService` added as a stub for future Bonjour + Android NSD

## Run locally

1. Start backend services in the repo root.
2. From this directory:

```bash
flutter pub get
flutter run
```

## Project structure

```
lib/
	app/                       # app shell + theme
	core/                      # shared models + URI utilities
	features/
		connection/              # hub URL + pupil id setup and persistence
		chat/                    # websocket client + chat UI
		discovery/               # discovery abstraction (stub for now)
```

## Next implementation step

- Add platform discovery adapters behind `HubDiscoveryService`:
	- iOS Bonjour discovery
	- Android NSD discovery
