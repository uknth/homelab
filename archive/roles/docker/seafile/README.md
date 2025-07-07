# Seafile

Issues while running this
- Has a weird restriction when running behind proxy
    - Requires a file to be edited after it is already running
    - seahub_settings.py needs a CSRF property added, but you can't actually mount seahub_settings.py
- Running without reverse proxy also didn't work for whatever reason
- Too much hassle