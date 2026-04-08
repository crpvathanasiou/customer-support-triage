from app.core.settings import get_settings

settings = get_settings()
print("Loaded settings successfully")
print(settings.app_name)