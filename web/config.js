/* SeRAPHIM deployment configuration — the ONE file you edit to go live.
 *
 * Leave both null for local development: the pages fall back to relative paths
 * (../data/out) and a localhost API, so `python3 -m http.server` keeps working.
 *
 * Set them after following docs/DEPLOY.md. Nothing else needs to change.
 */
window.SERAPHIM = {
  // Public base URL of the snapshot bucket, no trailing slash.
  // R2 dev URL:      "https://pub-<hash>.r2.dev/latest"
  // Custom domain:   "https://data.example.com/latest"
  dataBase: null,

  // Public URL of the SOS Worker, no trailing slash.
  //   "https://seraphim-sos.<your-subdomain>.workers.dev"
  apiBase: null,
};
