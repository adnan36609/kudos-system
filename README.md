# Kudos System

Internal-portal feature: colleagues give each other kudos; admins can hide, restore, or delete inappropriate ones.
See [SPECIFICATION.md](SPECIFICATION.md) for requirements and design.

## Run locally
```
pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000 and sign in as `alice`, `bob`, `carol` (users) or `admin`. Demo password for all: `password123` (local development only).

## Test
```
python -m unittest discover -s tests -v
```
