SERVER_URL = "https://localhost:5000/" # FILL
SERVICE_API_KEY = "999aaaaaacddddd"  # FILL
PROJECT_NAME = "resilio_sync"  # FILL
ADDON_VERSION =  "1.2.6+dev"
ADDON_VERSION =  "1.2.6+dev.1"


USER_NAME = "test"  # FILL, - use service user name for background process
SITE_NAME = "test-site-name"  # FILL, - use ‘us-cache’ for background process


ACTIVE_SITE = "us-cache"  # FILL,
REMOTE_SITE = "africa-studio"  # FILL


import ayon_api


skeleton = {
   "local_setting": {
       "active_site": ACTIVE_SITE,
       "remote_site": REMOTE_SITE
   },
   "local_roots": []
}


from ayon_api import ServerAPI


# Create connection with service API key
api = ServerAPI(SERVER_URL, token=SERVICE_API_KEY)


# Run commands as a specific user temporarily
with api.as_username(USER_NAME):
   response = api.get(f"addons/sitesync/{ADDON_VERSION}/rawOverrides/{PROJECT_NAME}?site_id={SITE_NAME}")


   response.raise_for_status("Cannot get site settings")
   site_settings = response.data
   site_settings.update(skeleton)


   response = api.put(f"addons/sitesync/{ADDON_VERSION}/rawOverrides/{PROJECT_NAME}?site_id={SITE_NAME}", **site_settings,)
   response.raise_for_status("Cannot save site settings")