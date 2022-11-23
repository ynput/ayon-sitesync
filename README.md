Site Sync Addon
===============

Addon allowing synchronization of published elements between remote and local locations.
Implements couple of different protocols (local drive, GDrive API, Dropbox API etc.)

Server side should allow reporting of status of presence of published elements on 
various sites (eg. studio, specific artist site, GDrive). It should also allow
marking each published file(s) to be synched to specific location eventually.

Client side runs webserver on artist (or studio) machine which does real synching and
provides Tray application `Sync Queue` to follow progress of synchronized files.