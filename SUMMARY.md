Files changed:
- unscroll/settings.py

What was fixed:
1. Fixed the middleware for allauth account: changed to "allauth.account.middleware.AccountMiddleware" and placed after "django.contrib.auth.middleware.AuthenticationMiddleware".
2. Fixed the context processors for allauth: changed "allauth.context_processors.account" to "allauth.account.context_processors.account" and "allauth.context_processors.socialaccount" to "allauth.socialaccount.context_processors.socialaccount".

Remaining warnings:
- Unknown until running `python manage.py check`