<?php

// Database connection — SQLite for lightweight native execution
$db_path = getenv('ROUNDCUBE_DB_PATH') ?: '/opt/srv-panel/data/roundcube_php/db/roundcube.db';
$config['db_dsnw'] = 'sqlite:///' . $db_path . '?mode=0646';

// DES Encryption key for session cookies (must be exactly 24 chars)
$des_file = dirname($db_path) . '/des_key.secret';
if (file_exists($des_file) && ($key = trim(file_get_contents($des_file))) && strlen($key) >= 24) {
    $config['des_key'] = substr($key, 0, 24);
} else {
    $config['des_key'] = 'rcmail-!srv-panel-key24!';
}

// Mail server transport settings — direct localhost connection to Maddy
$maddy_host = '127.0.0.1';
$config['imap_host'] = $maddy_host . ':143';
$config['smtp_host'] = $maddy_host . ':587';
$config['smtp_user'] = '%u';
$config['smtp_pass'] = '%p';

$local_tls = [
    'verify_peer' => false,
    'verify_peer_name' => false,
    'allow_self_signed' => true,
];
$config['imap_conn_options'] = ['ssl' => $local_tls];
$config['smtp_conn_options'] = ['ssl' => $local_tls];

// Load dynamic settings from state.json
$state_file = dirname($db_path, 2) . '/state.json';
if (!file_exists($state_file)) {
    $state_file = '/opt/srv-panel/data/roundcube_php/state.json';
}
$dyn = [];
if (file_exists($state_file) && ($json = @file_get_contents($state_file))) {
    $data = @json_decode($json, true);
    if (is_array($data) && !empty($data['settings']) && is_array($data['settings'])) {
        $dyn = $data['settings'];
    }
}

// Authentication & Session settings
$config['auto_create_user'] = true;
$config['login_lc'] = 2;
$config['login_autocomplete'] = 1;
$config['session_lifetime'] = !empty($dyn['session_lifetime']) ? (int)$dyn['session_lifetime'] : (int)(getenv('ROUNDCUBE_SESSION_LIFETIME') ?: 30);

// UI & Presentation
$config['skin'] = !empty($dyn['skin']) ? $dyn['skin'] : (getenv('ROUNDCUBE_SKIN') ?: 'elastic');
$config['product_name'] = !empty($dyn['product_name']) ? $dyn['product_name'] : (getenv('ROUNDCUBE_PRODUCT_NAME') ?: 'SRV Webmail');
$config['dont_override'] = ['skin'];
$config['use_https'] = true;
$config['request_path'] = '/';
$config['remote_resources'] = false;
$config['max_message_size'] = !empty($dyn['max_message_size']) ? $dyn['max_message_size'] : (getenv('ROUNDCUBE_MAX_MESSAGE_SIZE') ?: '32M');

// Active Roundcube Plugins
if (!empty($dyn['plugins']) && is_array($dyn['plugins'])) {
    $active_plugins = $dyn['plugins'];
    if (!in_array('srvpanel_launch', $active_plugins, true)) {
        $active_plugins[] = 'srvpanel_launch';
    }
    $config['plugins'] = array_values(array_filter($active_plugins));
} else {
    $plugins_str = getenv('ROUNDCUBE_PLUGINS') ?: 'archive,zipdownload,markasjunk,srvpanel_launch';
    $config['plugins'] = array_filter(array_map('trim', explode(',', $plugins_str)));
}
