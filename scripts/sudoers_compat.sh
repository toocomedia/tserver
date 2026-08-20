#!/usr/bin/env bash
# Sudo/sudo-rs-compatible exact plugin lifecycle command allowlist.

SRV_BUNDLED_PLUGIN_LIFECYCLE_SCRIPTS=(
  "maddy/scripts/install_maddy.sh"
  "maddy/scripts/uninstall_maddy.sh"
  "phpmyadmin/scripts/install_phpmyadmin.sh"
  "phpmyadmin/scripts/uninstall_phpmyadmin.sh"
  "railpack_apps/scripts/install_railpack_apps.sh"
  "railpack_apps/scripts/uninstall_railpack_apps.sh"
  "roundcube_php/scripts/install_roundcube.sh"
  "roundcube_php/scripts/uninstall_roundcube.sh"
  "roundcube_webmail/scripts/install_roundcube.sh"
  "roundcube_webmail/scripts/uninstall_roundcube.sh"
  "rspamd/scripts/install_rspamd.sh"
  "rspamd/scripts/uninstall_rspamd.sh"
  "wireguard/scripts/install_wireguard.sh"
  "wireguard/scripts/uninstall_wireguard.sh"
)

srv_sudoers_plugin_commands() {
  local panel_dir="$1"
  local relative_path absolute_path

  for relative_path in "${SRV_BUNDLED_PLUGIN_LIFECYCLE_SCRIPTS[@]}"; do
    absolute_path="$panel_dir/app/plugins/$relative_path"
    [[ -f "$absolute_path" ]] || continue
    printf ', /bin/bash %s, /usr/bin/bash %s' "$absolute_path" "$absolute_path"
  done
}
