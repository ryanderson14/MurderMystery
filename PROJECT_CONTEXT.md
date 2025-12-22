# Murder Mystery App (Raspberry Pi) — Project Context

## Theme
80s prom murder mystery party with 9 characters.

## Runtime environment
- Raspberry Pi hosts a local web app on LAN
- Python backend (Flask now; later Flask-SocketIO)
- SQLite database
- TV in kiosk mode shows /tv
- Phones use /app
- GM uses /gm

## MVP goals (ship order)
Ship 1 (done): /tv character board from SQLite + /gm seed/reset.
Ship 2 (done): /app login (code), public posts (signed/anonymous), TV live feed.
Ship 3: DMs + suspect scoring with real-time TV updates.
Ship 4: murder events + animation overlay + endgame voting.

## Routes
- /tv (TV dashboard)
- /app (player phone UI)
- /gm (GM controls)

## Non-goals for early ships
No accounts/passwords, no internet dependency, no cloud.

## Current files
- app.py: Flask server + sqlite helpers + seed data
- templates/tv.html, templates/gm.html
- static/style.css