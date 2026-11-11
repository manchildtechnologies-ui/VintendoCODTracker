"""Single source of truth for the tracker release version and update feed."""

APP_ID = 'VintendoCODTracker'
APP_VERSION = '3.2.2'

# GitHub repo that hosts releases, as 'owner/name'. The release workflow
# publishes the built exe plus RELEASES metadata there; the in-app updater
# polls it. Friends never need to touch this.
UPDATE_REPO = 'manchildtechnologies-ui/VintendoCODTracker'

# Asset name produced by the release workflow (see .github/workflows/release.yml).
UPDATE_ASSET_NAME = 'VintendoCODTracker.exe'
