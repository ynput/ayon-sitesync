Site Sync Addon
===============

Deployment:
----------
Content of addon repo must be prepared for proper deployment to the server.
Currently it is a manual process consisting of steps: (requirements: at least Python3.8)
- clone repo to local machine
- run `python create_package.py` - this will produce `package` folder in root of cloned repo
- install `.zip` file from `package` folder via Ayon Server UI (`Studio Settings > Bundles > Install Addons`)

Addon allowing synchronization of published elements between remote and local locations.
Implements couple of different protocols (local drive, GDrive API, Dropbox API etc.)

Server side should allow reporting of status of presence of published elements on 
various sites (eg. studio, specific artist site, GDrive). It should also allow
marking each published file(s) to be synched to specific location eventually.

Client side runs webserver on artist (or studio) machine which does real synching.
