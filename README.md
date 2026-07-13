# Unscroll

Unscroll creates a mindful digital postcard from a user's mood, tomorrow's intention, and optional screen-time context.

## Local setup

1. Create and activate a Python 3.12+ virtual environment.
2. Install dependencies with `python -m pip install -r requirements.txt`.
3. Run `python manage.py migrate`.
4. Start the application with `python manage.py runserver`.

For production, copy `.env.example` into your deployment environment, set a long random `DJANGO_SECRET_KEY`, configure your public hosts, and run `python manage.py check --deploy`.
