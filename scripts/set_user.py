SERVER_URL = "https://localhost:5000/" # FILL
SERVICE_API_KEY = "999aaaaaacddddd"  # FILL
PROJECT_NAME = "resilio_sync"  # FILL

USER_NAME = "test"  # FILL, - use service user name for background process
SITE_NAME = "test-site-name"  # FILL, - use ‘us-cache’ for background process

ACTIVE_SITE = "us-cache"  # FILL,
REMOTE_SITE = "africa-studio"  # FILL


skeleton = {
   "local_setting": {
       "active_site": ACTIVE_SITE,
       "remote_site": REMOTE_SITE
   },
   "local_roots": []
}


from ayon_api import ServerAPI, get_client_version,get_bundles

# Create connection with service API key
api = ServerAPI(
   SERVER_URL,
   token=SERVICE_API_KEY,
   site_id=SITE_NAME,
   client_version=get_client_version()
)

bundles = get_bundles()

# Find the production bundle
production_bundle = next(
    (bundle for bundle in bundles["bundles"] if bundle["isProduction"]),
    None
)

if not production_bundle:
    raise ValueError("No production bundle found, stopping")

sitesync_version = production_bundle["addons"].get("sitesync")

# Run commands as a specific user temporarily
with api.as_username(USER_NAME):
   api.get_info()  # necessary to register site if not present
   response = api.get(f"addons/sitesync/{sitesync_version}/rawOverrides/{PROJECT_NAME}?site_id={SITE_NAME}")


   response.raise_for_status("Cannot get site settings")
   site_settings = response.data
   site_settings.update(skeleton)


   response = api.put(f"addons/sitesync/{sitesync_version}/rawOverrides/{PROJECT_NAME}?site_id={SITE_NAME}", **site_settings,)
   response.raise_for_status("Cannot save site settings")
   print(f"Site settings saved for {USER_NAME} {SITE_NAME}")